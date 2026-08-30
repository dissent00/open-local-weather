"""METAR: the airport's own eyes. Two endpoints, two different jobs.

CURRENT CONDITIONS (`fetch_metar`, aviationweather.gov) is a nice-to-have
cross-check handed raw to the LLM. Best-effort only: an unconfigured or
wrong ICAO code, a station with no current report, or an API hiccup all
return None rather than raising, and the system prompt instructs the
narrative not to treat stale or absent METAR as live ground truth.

OBSERVATIONS (`observed_weather_by_date`, Iowa State's archive) is not a
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

WHY IT READS RAIN AND NOT ONLY THUNDER. The thunder-only version above
closed the 2026-08-24 gap and left the neighbouring one open. On 2026-08-29
this station reported `-RA` at 16:00Z and `RERA` at 17:00Z and 18:00Z, with
cumulonimbus and a 32°C -> 22°C outflow drop, and NO `TS` group anywhere —
the reader outside heard no thunder either. The reanalysis recorded 0.0 mm.
So `rain` was False and `thunder` was False, the day scored DRY, and every
model that called it dry banked a win for a day it rained. The groups that
prove it were already in the reports this module downloads.

Measured on the 45 days then stored: the station observed
precipitation on 9 of them, and 2 were days that BOTH the reanalysis and the
thunder check had called dry — 2026-07-21 (reanalysis 0.9 mm) and 2026-08-29
(0.0 mm). Correcting those two dropped every model's all-time Day+0 rain
accuracy by about five points, and Kenya Met's by ten. Rain without thunder is
therefore RARER here than thunder, not commoner — the guess that it would be
commoner was written before the rebuild was run, and the rebuild disagreed.
It is worth catching anyway: those two days were invisible by construction,
and an accuracy record that flatters itself by five points is the failure
this project exists to avoid. See ROADMAP item 53.

Both remain optional. No configured ICAO, or a station that reported
nothing, yields None — which is NOT False. See DailyActual.thunder.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
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

# Precipitation in every form METAR writes it as an OBSERVATION, built from
# the WMO present-weather grammar: optional RE (recent — it fell since the
# last report, which is still rain that fell), optional intensity, optional
# descriptor, then one or more precipitation types.
#
# VC IS DELIBERATELY ABSENT HERE, unlike THUNDER_GROUP. `VCTS` counts because
# thunder is heard across a city and a city-wide forecast should own it.
# `VCSH` is a shower seen 8 km away that did not reach the runway, and it
# carries no precipitation type at all — so it cannot match this pattern, and
# should not. A scoring change should flip the days it can prove, not the
# days it can guess at.
#
# Every alternative must be a WHOLE whitespace-delimited token: the leading
# (?:^|\s) and trailing lookahead are what stop `FEW024CB`, `SCT028` and
# `BKN080` being read as weather. Verified against the 2026-08-29 reports in
# test_observed_weather_ignores_precipitation_lookalikes.
PRECIPITATION_TYPES = "DZ|RA|SN|SG|PL|GR|GS|IC|UP"
PRECIPITATION_DESCRIPTORS = "MI|BC|PR|DR|BL|SH|TS|FZ"
PRECIPITATION_GROUP = re.compile(
    rf"(?:^|\s)(?:RE)?[+-]?(?:{PRECIPITATION_DESCRIPTORS})?"
    rf"(?:{PRECIPITATION_TYPES})+(?=\s|$)"
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


def report_has_precipitation(raw_metar: str) -> bool:
    """True if this single report observed precipitation AT the station."""
    return PRECIPITATION_GROUP.search(_observed_body(raw_metar)) is not None


@dataclass(frozen=True)
class StationWeather:
    """What the airport actually saw on one local calendar day.

    Two flags rather than one, because they answer different questions and
    fail differently. Thunder is what the reader remembers; precipitation is
    what the accuracy record was getting wrong. A dry thunderstorm sets the
    first alone, drizzle from stratus sets the second alone, and `TSRA` sets
    both.
    """

    thunder: bool
    precipitation: bool

    # LOCAL "HH:MM" of the FIRST report that observed precipitation, or None
    # when none did. The day-over-day description falls back to this when the
    # reanalysis recorded no onset because it recorded no rain at all — see
    # DailyActual.observed_onset.
    #
    # First rather than last: it is an onset. A `RE`-prefixed group is the
    # only trace of rain that ended between two routine reports, so the time
    # taken from one is a few minutes LATE rather than early; that is the
    # honest direction to err, and the phrase it feeds resolves to a part of
    # the day rather than a clock reading anyway.
    precipitation_onset: str | None = None


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


def observed_weather_by_date(
    icao: str, start: date, end: date, timezone_name: str
) -> dict[date, StationWeather] | None:
    """What the station observed on each LOCAL calendar day in the range.

    A date is absent when the station filed no report for it; a flag is False
    only when it reported and saw none of that thing. That distinction is the
    whole point — see DailyActual.thunder, where absent becomes None and is
    scored as "no observation" rather than as a quiet day.

    ONE FETCH, BOTH FLAGS. The archive request carries a 90-second timeout and
    is the slowest call in the verification pass; asking it the same question
    twice to get two booleans out of the same reports would double that for
    nothing.

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
    weather_by_date: dict[date, StationWeather] = {}
    for observed_at, raw_metar in reports:
        local_date = observed_at.astimezone(local_zone).date()
        if local_date < start or local_date > end:
            continue

        # Any report in the day sets a flag for the whole day, so a storm that
        # left its only trace in one SPECI is not averaged away by the calm
        # hours either side of it.
        seen = weather_by_date.get(local_date)
        precipitating = report_has_precipitation(raw_metar)

        # Reports arrive in time order, so the first one that precipitates is
        # the onset and every later one leaves it alone.
        onset = seen.precipitation_onset if seen is not None else None
        if precipitating and onset is None:
            onset = observed_at.astimezone(local_zone).strftime("%H:%M")

        weather_by_date[local_date] = StationWeather(
            thunder=(seen is not None and seen.thunder) or report_has_thunder(raw_metar),
            precipitation=(seen is not None and seen.precipitation) or precipitating,
            precipitation_onset=onset,
        )

    return weather_by_date or None
