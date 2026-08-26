"""METAR: the airport's own eyes. Two endpoints, two different jobs.

CURRENT CONDITIONS (`fetch_metar`, aviationweather.gov) is a nice-to-have
cross-check handed raw to the LLM. Best-effort only: an unconfigured or
wrong ICAO code, a station with no current report, or an API hiccup all
return None rather than raising, and the system prompt instructs the
narrative not to treat stale or absent METAR as live ground truth.

OBSERVATIONS (`observed_thunder_by_date`, Iowa State's archive) is not a
garnish. It is scored.

WHY A SECOND SOURCE EXISTS. This project scores every model against
Open-Meteo's archive (ERA5-family reanalysis), and on 2026-08-24 that
archive recorded 0.5 mm and no thunderstorm code for a day when this very
station reported TS from 13:30Z to 14:30Z, with cumulonimbus and a
32°C -> 25°C outflow drop. Isolated tropical convection at ~25 km grid
spacing gets smoothed into nothing. The consequence was not cosmetic: GFS,
ECMWF and Kenya Met were all scored WRONG for predicting rain that day, and
the day-over-day summary told readers it had been "dry again". Measured
across the whole stored record, 5 of 42 days were misfiled the same way.

WHY THE ARCHIVE AND NOT aviationweather.gov. That endpoint serves a rolling
window (`hours`, verified to work up to 48; the `date` parameter returns
HTTP 400). A 48-hour window is enough for a run that scores yesterday and
only ever runs on time — but a missed run would drop the observation
permanently, and it cannot rebuild history at all. The archive is
idempotent and re-fetchable, so a gap self-heals on the next run and the
whole record can be rescored from scratch. That matches how
verify/scoring.py already works: rolling stats are always recomputed from
raw stored predictions plus freshly fetched actuals, never accumulated.

Both remain optional. No configured ICAO, or a station that reported
nothing, yields None — which is NOT False. See DailyActual.thunder.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"
METAR_ARCHIVE_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
REQUEST_TIMEOUT_S = 15
ARCHIVE_TIMEOUT_S = 90

# Routine reports plus SPECIs. A storm that begins and ends between two
# routine hourly reports leaves its only trace in a SPECI, which is issued
# precisely because conditions changed sharply.
ARCHIVE_REPORT_TYPES = ("3", "4")

# A local calendar day always overhangs its UTC date at one end or the other,
# depending on the sign of the offset. One day of padding on each side covers
# every real timezone.
ARCHIVE_PADDING_DAYS = 1

# Everything from here on is a FORECAST or a remark, not an observation.
# "TEMPO TSRA" is the aerodrome forecasting thunderstorms it has not seen,
# and reading it as an observation would manufacture storms that never
# happened — the exact opposite of the bug this module exists to fix.
NON_OBSERVED_SECTIONS = ("RMK", "TEMPO", "BECMG", "NOSIG", "FM")

# Thunder in every form METAR writes it: optional intensity, optional VC
# (vicinity - near enough that a city-wide forecast should count it) or RE
# (recent - the storm ended since the last report, which is still a storm
# that happened), and any precipitation it arrived with.
THUNDER_GROUP = re.compile(
    r"(?:^|\s)[+-]?(?:VC|RE)?TS(?:RA|GR|GS|SN|PL|DZ)?(?=\s|$)"
)


def fetch_metar(icao: str) -> list[dict] | None:
    """Returns the parsed METAR report list, or None if unavailable for any
    reason (blank ICAO, network failure, non-200, empty response)."""
    if not icao:
        return None
    try:
        resp = requests.get(
            METAR_URL, params={"ids": icao, "format": "json"}, timeout=REQUEST_TIMEOUT_S
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data or None


def _observed_body(raw_metar: str) -> str:
    """The part of a report that describes what was actually seen.

    Truncates at the first forecast or remarks section, so a trend group's
    predicted weather is never mistaken for an observation.
    """
    body = raw_metar
    for marker in NON_OBSERVED_SECTIONS:
        index = body.find(f" {marker}")
        if index != -1:
            body = body[:index]

    return body


def report_has_thunder(raw_metar: str) -> bool:
    """True if this single report observed thunder at or beside the station."""
    return THUNDER_GROUP.search(_observed_body(raw_metar)) is not None


def fetch_metar_archive(
    icao: str, start: date, end: date
) -> list[tuple[datetime, str]] | None:
    """Raw (UTC observation time, report text) pairs over a date range.

    Returns None — never an empty list — when there is nothing to read, so
    the caller can tell "the station said nothing" apart from "the station
    said it was quiet".
    """
    if not icao:
        return None

    params = {
        "station": icao,
        "data": "metar",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year, "month2": end.month, "day2": end.day,
        "tz": "UTC",
        "format": "onlycomma",
        "latlon": "no",
        "missing": "M",
        "trace": "T",
        "direct": "no",
        "report_type": list(ARCHIVE_REPORT_TYPES),
    }
    try:
        resp = requests.get(METAR_ARCHIVE_URL, params=params, timeout=ARCHIVE_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    rows = list(csv.reader(resp.text.splitlines()))
    reports: list[tuple[datetime, str]] = []
    for row in rows:
        if len(row) < 3 or row[0] == "station":
            continue
        try:
            observed_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        reports.append((observed_at.replace(tzinfo=timezone.utc), row[2]))

    return reports or None


def observed_thunder_by_date(
    icao: str, start: date, end: date, timezone_name: str
) -> dict[date, bool] | None:
    """Whether thunder was observed on each LOCAL calendar day in the range.

    A date is absent when the station filed no report for it; the value is
    False only when it reported and saw no thunder. That distinction is the
    whole point — see DailyActual.thunder, where absent becomes None and is
    scored as "no observation" rather than as a quiet day.

    Local, not UTC: the forecast, the log entry and the accuracy record are
    all keyed on the reader's calendar day, and a 21:30Z storm belongs to
    tomorrow in Nairobi.
    """
    reports = fetch_metar_archive(
        icao,
        start - timedelta(days=ARCHIVE_PADDING_DAYS),
        end + timedelta(days=ARCHIVE_PADDING_DAYS),
    )
    if reports is None:
        return None

    local_zone = ZoneInfo(timezone_name)
    thunder_by_date: dict[date, bool] = {}
    for observed_at, raw_metar in reports:
        local_date = observed_at.astimezone(local_zone).date()
        if local_date < start or local_date > end:
            continue

        seen_before = thunder_by_date.get(local_date, False)
        thunder_by_date[local_date] = seen_before or report_has_thunder(raw_metar)

    return thunder_by_date or None
