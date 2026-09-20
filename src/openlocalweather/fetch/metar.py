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
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from openlocalweather.store import station_reports as station_store

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


# SKY COVER, IN EIGHTHS. ROADMAP items 87 and 65.
#
# The forecast predicts cloud_cover and NOTHING OBSERVED IT. A METAR reports
# the sky directly — a ceilometer and a human observer — and this parser threw
# it away, while `metar.py`'s own header records a day whose argument turned on
# a cumulonimbus group. The evidence that settled one argument was not
# available to the next.
#
# THE UNIT IS THE STANDARD'S. The NWS glossary defines sky condition as
# "octants (eighths) of the sky covered by opaque clouds" and confirms SCT
# directly: "3/8th to 4/8th (sky cover is measured in eighths or oktas)". Its
# published band table — Clear 0/8, Mostly Clear 1-2/8, Partly Cloudy 3-4/8,
# Mostly Cloudy 5-7/8, Cloudy 8/8 — has exactly the boundaries the METAR
# abbreviations use, which is where FEW, BKN and OVC come from below. Only
# SCT is quoted outright; the other three are read off that table's edges and
# that is a derivation, not a citation.
#
# THE TOP OF EACH RANGE, not the middle. A layer group reports the sky covered
# at and below its height, so the greatest group is the total. Taking the top
# errs toward MORE cloud, which is the honest direction for a value that will
# be used to ask whether the sun got through.
SKY_COVER_OKTAS = {
    "SKC": 0, "CLR": 0, "NSC": 0, "NCD": 0,
    "FEW": 2, "SCT": 4, "BKN": 7, "OVC": 8,
}

# Ceiling And Visibility OK: no cloud below 5,000 ft and no CB or TCU. Not
# strictly zero cover — high cirrus is permitted — but it is the standard's
# way of saying the sky is not in the way, and it is what this station files
# on a clear day. Counted as clear, and named here so the choice is visible.
CAVOK_GROUP = re.compile(r"(?:^|\s)CAVOK(?=\s|$)")

# Vertical visibility: the sky is OBSCURED, by fog or heavy precipitation, and
# the observer cannot see it at all. Counted as fully covered rather than as
# no data, because an obscured sky is emphatically not a clear one and a null
# would let a fog day read as unobserved.
OBSCURED_GROUP = re.compile(r"(?:^|\s)VV(?:\d{3}|///)(?=\s|$)")

CLOUD_LAYER_GROUP = re.compile(
    rf"(?:^|\s)({'|'.join(SKY_COVER_OKTAS)})(?:\d{{3}}|///)?(?:CB|TCU)?(?=\s|$)"
)


def report_cloud_oktas(raw_metar: str) -> int | None:
    """Total sky cover for ONE report, 0-8, or None when it says nothing.

    None matters: a report with no sky group at all is not a clear sky, and
    averaging it in as zero would manufacture sunshine.
    """
    body = _observed_body(raw_metar)

    if OBSCURED_GROUP.search(body):
        return SKY_COVER_OKTAS["OVC"]

    layers = [SKY_COVER_OKTAS[m] for m in CLOUD_LAYER_GROUP.findall(body)]
    if layers:
        return max(layers)

    if CAVOK_GROUP.search(body):
        return 0

    return None


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

    # LOCAL "HH:MM" of the LAST report on the day, or None. The reach the
    # same-day snapshot states beside the run's clock — ROADMAP item 151.
    reported_through: str | None = None

    # MEAN sky cover across the day's reports, 0-8, or None when no report
    # said anything about the sky. ROADMAP items 87 and 65.
    #
    # MEAN AND NOT MAX, unlike the two flags above, and the difference is the
    # question each answers. Thunder asks "did it happen at all", so one
    # report is enough. Cloud asks "what kind of day was it", and a single
    # OVC hour in an otherwise clear day did not make it a cloudy day. It is
    # also the directly comparable thing: the models' cloud_cover is a mean
    # over the same 24 hours.
    #
    # Reports with no sky group are left out of the mean rather than counted
    # as zero — that would manufacture sunshine out of silence.
    cloud_oktas: float | None = None


class ArchiveUnavailable(Exception):
    """The ASOS archive gave no usable answer, and this says which kind.

    ROADMAP item 151, step 2. The evening runs of 2026-09-16 and 09-17 both
    recorded "the request succeeded and the response was empty", and the
    fetch could not have known that: it returned one None for a request
    exception, a non-200 status and a 200 with no data rows alike. IEM is
    known to answer some failures as plain text under a 200, so the body's
    first line travels with the reason. The message is what the degradation
    stores, so the next failed run is diagnosed from the record.
    """


# How much of a failed answer to keep. Enough to read an error sentence or
# recognise an HTML page; not enough to store a page.
_FIRST_LINE_CHARS = 120

# The two answers the archive gives when it is busy rather than broken —
# measured 2026-09-18 and 09-20: "HTTP 503 ERROR: server over capacity,
# please try later" and "HTTP 429 Too many requests from your IP address,
# slow down". Both ask for a wait, so both get one, bounded: two more tries
# after 2 s and 6 s. Anything else — a 500, a 404, a timeout at the 90 s
# ceiling — is not retried; a timeout tripled would hold the run for four
# minutes and the sentence it stores already says what happened.
ARCHIVE_RETRY_STATUSES = (503, 429)
ARCHIVE_RETRY_DELAYS_S = (2.0, 6.0)


def _archive_rows(params: dict) -> list[list[str]]:
    """Data rows from one archive request, or ArchiveUnavailable saying why
    there are none. Never an empty list: a padded three-day range with no
    rows is not a quiet station, it is an answer this project cannot use."""
    attempts = 1 + len(ARCHIVE_RETRY_DELAYS_S)
    for attempt in range(attempts):
        try:
            resp = requests.get(METAR_ARCHIVE_URL, params=params, timeout=ARCHIVE_TIMEOUT_S)
        except requests.RequestException as e:
            raise ArchiveUnavailable(f"request failed: {type(e).__name__}: {e}") from e
        if resp.status_code in ARCHIVE_RETRY_STATUSES and attempt < attempts - 1:
            time.sleep(ARCHIVE_RETRY_DELAYS_S[attempt])
            continue
        break

    lines = resp.text.splitlines()
    first = lines[0][:_FIRST_LINE_CHARS] if lines else ""
    if resp.status_code != 200:
        tried = f" after {attempts} attempts" if resp.status_code in ARCHIVE_RETRY_STATUSES else ""
        raise ArchiveUnavailable(f"HTTP {resp.status_code}{tried}; first line: {first!r}")

    rows = [r for r in csv.reader(lines) if len(r) >= 3 and r[0] != "station"]
    if not rows:
        raise ArchiveUnavailable(
            f"HTTP 200 with no data rows; body {len(resp.text)} bytes; first line: {first!r}"
        )
    return rows


def fetch_metar_archive(
    icao: str, start: date, end: date
) -> list[tuple[datetime, str]] | None:
    """Raw (UTC observation time, report text) pairs over a date range.

    None without a station, or when rows arrived but none carried a
    parseable time. When the archive gives no usable answer at all this
    RAISES ArchiveUnavailable with the reason — see that class.
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
    reports: list[tuple[datetime, str]] = []
    for row in _archive_rows(params):
        try:
            observed_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        reports.append((observed_at.replace(tzinfo=timezone.utc), row[2]))

    return reports or None


# One knot in km/h. Spelled out because the conversion happens on the way
# INTO the record, and a wrong constant here would be invisible: every
# stored wind would simply be consistently wrong.
KM_PER_KNOT = 1.852


@dataclass
class StationReadings:
    """Quantities the airport MEASURED on one local calendar day.

    Kept apart from StationWeather, which carries what the station SAW —
    thunder, precipitation — because these are numbers the reanalysis also
    supplies and those are observations only the station can make. Mixing them
    would blur which of the two the record is falling back on.

    STORED, NOT SCORED. Item 45's sequencing: cross-check before replacement.
    Nothing here feeds verification; it accumulates beside the reanalysis so
    that a precedence decision can later be made with numbers instead of an
    assumption. Item 44 already measured temperature agreement at +0.43 °C
    mean, which is the sort of answer that decides whether the precedence
    machinery earns anything for a variable at all.

    NO PRECIPITATION FIELD, and that is the important omission. HKKI files
    p01i as 0.00 on every row of a 45-day sample, including an hour whose own
    report carries -RA — a constant dressed as a measurement. Reading it would
    put a confident "no rain" into the record on days it rained. See ROADMAP
    item 45. Precipitation from this station comes only from the present
    weather groups, via StationWeather.
    """

    high_c: float | None = None
    low_c: float | None = None
    peak_wind_kmh: float | None = None


def _number(raw: str) -> float | None:
    """A reading, or None for the service's absence markers.

    `M` is missing and `T` is trace. Parsed as numbers they become confident
    values, which is exactly how a station that measures nothing ends up
    asserting zero.
    """
    value = raw.strip()
    if value in ("", "M", "T"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def station_readings_by_date(
    rows: Iterable[Sequence[str]], start: date, end: date, timezone_name: str
) -> dict[date, StationReadings]:
    """Bucket structured ASOS rows into LOCAL calendar days.

    Units are converted on the way in — Fahrenheit to Celsius, knots to km/h —
    so a later comparison against the reanalysis is a subtraction rather than
    a conversion someone has to remember. The record's units are the record's
    units everywhere.

    Local, not UTC, matching observed_weather_by_date: a 21:30Z report belongs
    to tomorrow in Nairobi, and the two halves of one station's output must
    not describe different days.

    A day appears only when something usable was read. A day of nothing but
    `M` markers is absent rather than present-and-empty, for the same reason
    a missing flag is None rather than False.
    """
    local_zone = ZoneInfo(timezone_name)
    temps: dict[date, list[float]] = {}
    winds: dict[date, list[float]] = {}

    for row in rows:
        if len(row) < 4 or row[0] == "station":
            continue
        try:
            observed_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        local_date = observed_at.replace(tzinfo=timezone.utc).astimezone(local_zone).date()
        if local_date < start or local_date > end:
            continue

        tmpf = _number(row[2])
        if tmpf is not None:
            temps.setdefault(local_date, []).append(round((tmpf - 32) * 5 / 9, 2))

        sknt = _number(row[3])
        if sknt is not None:
            winds.setdefault(local_date, []).append(round(sknt * KM_PER_KNOT, 2))

    readings: dict[date, StationReadings] = {}
    for day in sorted(set(temps) | set(winds)):
        t, w = temps.get(day, []), winds.get(day, [])
        readings[day] = StationReadings(
            high_c=max(t) if t else None,
            low_c=min(t) if t else None,
            peak_wind_kmh=max(w) if w else None,
        )
    return readings


# The structured columns worth asking for alongside the raw report, and the
# order they arrive in. `p01i` is DELIBERATELY ABSENT: HKKI files it as 0.00
# on every row of a 45-day sample, including an hour whose own report carries
# -RA, so it is a constant dressed as a measurement and reading it would put a
# confident "no rain" into the record on days it rained. `gust` is absent for
# the opposite reason — missing on all 932 rows of that sample, because METAR
# files a gust group only when a gust occurs. See ROADMAP item 45.
ARCHIVE_DATA_COLUMNS = ("metar", "tmpf", "sknt")


# COLLECTED BUT NEVER READ — ROADMAP item 152.
#
# These are the columns the comment above excludes, and the exclusions are
# still correct: `gust` was missing on all 932 rows of a 45-day sample because
# METAR files a gust group only when a gust occurs, and `p01i` is filed as a
# constant 0.00 here including on hours whose own report carries -RA.
#
# THE PROBLEM WAS THAT THE EXCLUSION REMOVED ITS OWN FALSIFIER. A column
# nobody requests produces no series, so nothing could ever notice it starting
# to work. Both decisions were made in August 2026 against one station, and a
# station's reporting practice is not a law of nature — HKKI could file a gust
# group tomorrow and this project would never learn it.
#
# NOT ADDED TO ARCHIVE_DATA_COLUMNS, deliberately. The daily parse is
# POSITIONAL (`r[3:]` in observed_station_data), so widening the read set
# would shift every index and put a gust where a wind speed belongs — the
# exact quantity confusion item 144 was raised on. These are requested by the
# WEEKLY HEALTH CHECK on its own request instead, which costs the daily path
# nothing and puts the answer where assumptions that can silently go stale are
# already checked.
ARCHIVE_WATCHED_COLUMNS = ("gust", "p01i")


def fetch_metar_archive_rows(
    icao: str, start: date, end: date, extra_columns: Sequence[str] = ()
) -> list[Sequence[str]] | None:
    """Raw CSV rows from the ASOS archive: station, valid, then
    ARCHIVE_DATA_COLUMNS, then `extra_columns`. None only without a station;
    an archive that gives no usable answer RAISES ArchiveUnavailable, which
    says whether it was the request, the status or an empty body.

    `extra_columns` APPENDS, and appending is the whole of its safety —
    ROADMAP item 152. The daily parse indexes this response POSITIONALLY, so a
    column inserted anywhere but the end would shift a wind speed into a
    temperature's place. Nothing on the daily path passes it; the weekly
    health check does, to ask whether a column this project excluded has
    started arriving.

    Split out from fetch_metar_archive so one request can serve both the raw
    reports and the structured readings. The request carries a 90-second
    timeout and is the slowest call in the verification pass; asking it twice
    for two views of the same rows would double that for nothing.
    """
    if not icao:
        return None

    params = {
        "station": icao,
        "data": ",".join((*ARCHIVE_DATA_COLUMNS, *extra_columns)),
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
    return _archive_rows(params)


# ONE ARCHIVE REQUEST PER RUN — ROADMAP item 151, 2026-09-20. The 09-20 run's
# three readers (the day overlay, the window scorer, the same-day snapshot)
# asked the archive for the same padded range at 03:01:53.57, 53.92 and
# 54.14Z, and it answered the second with "Too many requests from your IP
# address, slow down". The store made two of the three recoverable, and it
# also makes the burst unnecessary: the run fetches once, up front, into
# the store, and every reader whose range the prefetch covered is served
# from the store without a request. A reader asking outside that range —
# the Monday batch, rebuild-record — still fetches as before.
#
# Per icao: the range fetched this run and the archive's reason when it
# failed, so a failed prefetch is one failure for the run rather than one
# per reader, and each reader still gets the fallback with the reason.
_RUN_FETCH: dict[str, tuple[date, date, str | None]] = {}

# The current-conditions feed's report as an archive-shaped row. The archive
# lags the current UTC day by hours on some days (09-20: no rows for the
# day at 07:14Z while the station had reported at 06:30Z on the other
# feed), so the report the prompt already carries is merged into the store
# too, and the same-day reach can say 06:30 instead of nothing.
_REPORT_PREFIXES = ("METAR ", "SPECI ")
_WIND_GROUP = re.compile(r"\b(?:\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b")
_REPORT_TIME_CHARS = 16  # "2026-09-20T06:30" of "2026-09-20T06:30:00.000Z"
_MISSING = "M"


def current_report_row(icao: str, report: dict) -> list[str] | None:
    """One (station, valid, metar, tmpf, sknt) row from a current-conditions
    JSON report, in the archive's own units and spellings, or None when the
    report carries no readable time or text.

    The leading METAR/SPECI word is dropped because the archive writes the
    text without it, and the store keys a row on (time, text): the archive's
    own row for the same minute, when it arrives, is then the same row. That
    the two texts otherwise match is expected from the samples seen, not yet
    measured on a day both held; a mismatch costs one duplicate row, which
    the cumulative fields tolerate.
    """
    when = str(report.get("reportTime") or "")[:_REPORT_TIME_CHARS]
    text = str(report.get("rawOb") or "").strip()
    if len(when) < _REPORT_TIME_CHARS or not text:
        return None

    for prefix in _REPORT_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    temp = report.get("temp")
    tmpf = f"{float(temp) * 9 / 5 + 32:.2f}" if isinstance(temp, (int, float)) else _MISSING
    wind = _WIND_GROUP.search(text)
    sknt = f"{int(wind.group(1)):.2f}" if wind else _MISSING
    return [icao, when.replace("T", " "), text, tmpf, sknt]


def prefetch_station_rows(
    icao: str,
    start: date,
    end: date,
    data_dir: str | Path,
    *,
    current_report: dict | None = None,
) -> str | None:
    """The run's one archive request for `start..end`, merged into the store,
    with the current-conditions report merged beside it. Returns the
    archive's reason when it failed, else None; the readers that follow read
    the store either way — see _RUN_FETCH."""
    _RUN_FETCH.pop(icao, None)
    reason: str | None = None
    try:
        rows = fetch_metar_archive_rows(icao, start, end)
        if rows:
            station_store.merge_rows(data_dir, icao, rows)
    except ArchiveUnavailable as failure:
        reason = str(failure)

    if current_report is not None:
        row = current_report_row(icao, current_report)
        if row is not None:
            station_store.merge_rows(data_dir, icao, [row])

    _RUN_FETCH[icao] = (start, end, reason)
    return reason


def reset_station_session() -> None:
    """Forget the run's prefetch — the next reader fetches for itself."""
    _RUN_FETCH.clear()


def _station_rows(
    icao: str,
    start: date,
    end: date,
    data_dir: str | Path | None,
    on_fallback: Callable[[str], None] | None = None,
) -> list[Sequence[str]] | None:
    """The archive's rows for `start..end`, merged into and read back from
    the store when there is one.

    THE FALLBACK — ROADMAP item 151, step 3. When the fetch fails and the
    store holds any of the range, the stored rows are returned and
    `on_fallback` is told the archive's reason, so the caller can say beside
    the observation that it came from earlier runs and how far it reaches.
    Built only once the reach existed, because stored rows presented as
    current would have shown the morning as the day. With no store, or
    nothing stored, the failure propagates exactly as before.

    WITHIN A RUN THAT PREFETCHED THE RANGE, no request is made: the store
    holds what the prefetch fetched, and a prefetch that failed is one
    failure for the run, handed to each reader as the same fallback.
    """
    cached = _RUN_FETCH.get(icao)
    if cached is not None and data_dir is not None and cached[0] <= start and end <= cached[1]:
        _, _, reason = cached
        stored = station_store.read_rows(data_dir, icao, start, end)
        if reason is None:
            return stored
        if stored is None:
            raise ArchiveUnavailable(reason)
        if on_fallback is not None:
            on_fallback(reason)
        return stored

    try:
        rows = fetch_metar_archive_rows(icao, start, end)
    except ArchiveUnavailable as failure:
        if data_dir is None:
            raise
        stored = station_store.read_rows(data_dir, icao, start, end)
        if stored is None:
            raise
        if on_fallback is not None:
            on_fallback(str(failure))
        return stored

    if rows is None or data_dir is None:
        return rows

    station_store.merge_rows(data_dir, icao, rows)
    return station_store.read_rows(data_dir, icao, start, end)


def station_reports(
    icao: str,
    start: date,
    end: date,
    data_dir: str | Path | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> list[tuple[datetime, str]] | None:
    """Raw (UTC observation time, report text) pairs over `start..end`
    inclusive, through the store like observed_station_data — the window
    scorer's evidence, and since item 151 recomputable from the record.
    None without a station or when no row carried a parseable time."""
    if not icao:
        return None

    rows = _station_rows(icao, start, end, data_dir, on_fallback)
    if rows is None:
        return None

    reports: list[tuple[datetime, str]] = []
    for row in rows:
        try:
            observed_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        reports.append((observed_at.replace(tzinfo=timezone.utc), row[2]))

    return reports or None


def observed_station_data(
    icao: str,
    start: date,
    end: date,
    timezone_name: str,
    data_dir: str | Path | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> tuple[dict[date, StationWeather] | None, dict[date, StationReadings] | None]:
    """Everything one station has to say about a range, from ONE fetch.

    Returns (what it SAW, what it MEASURED) — see StationWeather and
    StationReadings for why those are separate. Both None when the station
    said nothing at all, so a caller can tell that apart from a station that
    reported and observed a quiet day.

    With `data_dir` the rows go THROUGH THE STORE — ROADMAP item 151: what
    was fetched is merged into data/station/ and what is read back is the
    union of every fetch so far, so a row the archive has since dropped is
    still counted and the record can recompute what was derived from it.
    Every reader in the pipeline passes it; a test walks the callers.
    `on_fallback` hears the archive's reason when the rows came from the
    store because the fetch failed — see `_station_rows`.
    """
    rows = _station_rows(
        icao,
        start - timedelta(days=ARCHIVE_PADDING_DAYS),
        end + timedelta(days=ARCHIVE_PADDING_DAYS),
        data_dir,
        on_fallback,
    )
    if rows is None:
        return None, None

    reports: list[tuple[datetime, str]] = []
    for row in rows:
        try:
            observed_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        reports.append((observed_at.replace(tzinfo=timezone.utc), row[2]))

    weather = _weather_from_reports(reports, start, end, timezone_name)
    # Columns after the raw report: station, valid, metar, tmpf, sknt. The
    # bucketing wants (station, valid, tmpf, sknt), so the report is dropped.
    readings = station_readings_by_date(
        [(r[0], r[1], *r[3:]) for r in rows if len(r) >= 5], start, end, timezone_name
    )
    return weather, (readings or None)


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
    # Keeps its None contract: nothing in the pipeline reads this function,
    # and its tests pin the shape. The reason lives on ArchiveUnavailable
    # for the callers that record it.
    try:
        reports = fetch_metar_archive(
            icao,
            start - timedelta(days=ARCHIVE_PADDING_DAYS),
            end + timedelta(days=ARCHIVE_PADDING_DAYS),
        )
    except ArchiveUnavailable:
        return None
    if reports is None:
        return None

    return _weather_from_reports(reports, start, end, timezone_name)


def station_weather_within(
    reports: list[tuple[datetime, str]] | None,
    start_local: datetime,
    hours: int,
    timezone_name: str,
) -> StationWeather | None:
    """What the airport saw inside ONE window of `hours` from `start_local`
    (a naive local instant) — ROADMAP item 139.

    The calendar day gets the station stamped onto its observation before
    scoring; the +24 h window did not, and the first scored window (2026-09-15
    06:00) scored every model's rain call wrong for exactly that reason: the
    reanalysis held 0.4 mm and the station had seen rain. This is the same
    reduction `_weather_from_reports` makes per day, made per window, from
    the same reports and the same predicates, so a window and a calendar day
    covering identical hours see identical station weather.

    None when there are no reports at all, or none inside the window: "not
    observed", never "nothing happened". Sky cover is left out — the window
    is scored on rain and thunder, and a mean over a window is a different
    figure that nothing reads yet.
    """
    if reports is None:
        return None

    zone = ZoneInfo(timezone_name)
    window_start = start_local.replace(tzinfo=zone)
    window_end = window_start + timedelta(hours=hours)
    seen = False
    thunder = False
    precipitation = False
    onset: str | None = None
    for observed_at, raw_metar in reports:
        local = observed_at.astimezone(zone)
        if not (window_start <= local < window_end):
            continue

        seen = True
        thunder = thunder or report_has_thunder(raw_metar)
        precipitating = report_has_precipitation(raw_metar)
        precipitation = precipitation or precipitating
        if precipitating and onset is None:
            onset = local.strftime("%H:%M")

    if not seen:
        return None
    return StationWeather(thunder=thunder, precipitation=precipitation, precipitation_onset=onset)


def _weather_from_reports(
    reports: list[tuple[datetime, str]], start: date, end: date, timezone_name: str
) -> dict[date, StationWeather] | None:
    """The bucketing half of observed_weather_by_date, split out so
    observed_station_data can reuse it without a second fetch."""
    local_zone = ZoneInfo(timezone_name)
    weather_by_date: dict[date, StationWeather] = {}
    # Accumulated separately because this one is averaged, not latched: the
    # loop below builds each day's flags by OR-ing report into report, and a
    # mean cannot be computed that way.
    oktas_by_date: dict[date, list[int]] = {}
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

        oktas = report_cloud_oktas(raw_metar)
        if oktas is not None:
            oktas_by_date.setdefault(local_date, []).append(oktas)

        # The reach is the LATEST report, not the last one seen: the order
        # is the archive's and is not relied on.
        local_clock = observed_at.astimezone(local_zone).strftime("%H:%M")
        reach = seen.reported_through if seen is not None else None
        if reach is None or local_clock > reach:
            reach = local_clock

        weather_by_date[local_date] = StationWeather(
            thunder=(seen is not None and seen.thunder) or report_has_thunder(raw_metar),
            precipitation=(seen is not None and seen.precipitation) or precipitating,
            precipitation_onset=onset,
            reported_through=reach,
        )

    for local_date, values in oktas_by_date.items():
        if local_date in weather_by_date:
            weather_by_date[local_date] = replace(
                weather_by_date[local_date],
                cloud_oktas=round(sum(values) / len(values), 1),
            )

    return weather_by_date or None
