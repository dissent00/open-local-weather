"""Deterministic aggregation across multiple ground AQI stations.

Pure code, never the LLM — same "arithmetic is code's job" principle as
verify/. The range and which station is currently worst are simple to get
right in code and easy to get subtly wrong if an LLM is asked to eyeball
three numbers and pick a max each day; pre-computing them means the prompt
states a fact instead of asking for a calculation.

STALENESS: WAQI (and the low-cost sensor networks it aggregates) can and do
serve hours-old readings with no obvious signal on their own site — a
"last known value" quietly captioned "updated Xh ago". Confirmed live: all
three of this project's configured stations were once serving readings 7.2
hours old simultaneously. A reading that old does not represent "right
now," so it is excluded from the range/worst-station computation — the same
"don't treat stale data as live ground truth" rule this project already
applies to METAR. Readings are never hidden for being stale, only excluded
from the range; see the `stations_stale` count and hours_old() for surfacing
that explicitly wherever a reading is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from openlocalweather.models import GroundAQIReading

# Beyond this, a reading no longer represents current conditions. WAQI
# stations commonly update hourly; a few hours' lag is ordinary latency,
# but 3+ hours crosses from "a bit behind" into "not this morning's air".
STALE_THRESHOLD_HOURS = 3.0


def hours_old(reading: GroundAQIReading, now: datetime | None = None) -> float | None:
    """None if the reading has no timestamp at all (unknown freshness —
    never assumed fresh)."""
    if reading.measured_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - reading.measured_at).total_seconds() / 3600


# The precision the age is SHOWN at, everywhere it is shown — ROADMAP item
# 142, finding 5. Named rather than repeated, because the bug was that the
# display rounded and the judgement did not.
STALE_DISPLAY_DECIMALS = 1


def is_stale(reading: GroundAQIReading, now: datetime | None = None) -> bool:
    """True if stale OR of unknown freshness — both are treated the same
    way (excluded from the confident range), just worded differently
    wherever they're surfaced to a reader.

    JUDGED AT DISPLAY PRECISION, and that is the fix rather than an
    approximation — ROADMAP item 142, finding 5. The prompt tells the
    forecaster that stale means MORE THAN three hours, and the payload handed
    it `"hours_old": 3.0, "stale": true`: the age was rounded to a tenth for
    display and compared unrounded, so anything between 3.00 and 3.05 showed
    as exactly the threshold and judged past it. The model was told to trust
    both halves of a contradiction.

    Rounding first makes the two agree BY CONSTRUCTION rather than by
    coincidence of precision — the same move as ROADMAP item 154's other
    instances: one value, one comparison, rather than two values for one
    question. What it costs is that a reading between 3.00 and 3.05 hours old
    is now fresh, which is a distinction the threshold was never chosen finely
    enough to make.
    """
    age = hours_old(reading, now)
    return age is None or round(age, STALE_DISPLAY_DECIMALS) > STALE_THRESHOLD_HOURS


@dataclass
class GroundAQISummary:
    aqi_min: int
    aqi_max: int
    highest_station_name: str
    stations_with_aqi: int
    stations_stale: int
    stations_total: int


def summarize_ground_aqi(
    readings: list[GroundAQIReading], now: datetime | None = None
) -> GroundAQISummary | None:
    """None if no station has a numeric, sufficiently-fresh AQI right now —
    either because every station returned WAQI's "-" no-data sentinel (see
    fetch/waqi.py), every reading is too stale to trust, or the list is
    empty. `now` is injectable for deterministic testing; defaults to the
    real current time.

    Ties for highest resolve to whichever station sorts first, which is an
    arbitrary but stable and harmless choice.
    """
    now = now or datetime.now(timezone.utc)
    stale_count = sum(1 for r in readings if r.aqi is not None and is_stale(r, now))
    fresh_with_aqi = [r for r in readings if r.aqi is not None and not is_stale(r, now)]
    if not fresh_with_aqi:
        return None
    worst = max(fresh_with_aqi, key=lambda r: r.aqi)
    best = min(fresh_with_aqi, key=lambda r: r.aqi)
    return GroundAQISummary(
        aqi_min=best.aqi,
        aqi_max=worst.aqi,
        highest_station_name=worst.name,
        stations_with_aqi=len(fresh_with_aqi),
        stations_stale=stale_count,
        stations_total=len(readings),
    )


@dataclass
class GroundAQILastKnown:
    """The newest real ground reading available, whether or not it is fresh.

    Exists because returning None from summarize_ground_aqi() left the prompt
    with "Not applicable" and the LLM free to improvise. It improvised
    differently on consecutive days: 2026-08-25 gave no station numbers at
    all, 2026-08-26 listed all three and then called them stale. Neither was
    wrong, and that is the problem — the reader got a different contract each
    morning.

    A stale reading is still the last time anyone actually measured the air.
    Said with its age attached, it is more use than silence and cannot be
    mistaken for current.
    """

    station_name: str
    aqi: int
    # ISO 8601, not a datetime object. This value exists to be printed into a
    # prompt and pinned in a cross-language vector, and the two runtimes
    # stringify a timestamp differently — Python's str() gives
    # "2026-08-10 12:00:00+00:00" where Dart's gives "2026-08-10 12:00:00.000Z".
    # Anything needing to compute with the age uses hours_old below.
    measured_at: str
    hours_old: float
    stale: bool
    # How many stations share this timestamp — so the narrative can say three
    # stations reported at that hour rather than implying only one exists.
    stations_reporting: int


def last_known_ground_aqi(
    readings: list[GroundAQIReading], now: datetime | None = None
) -> GroundAQILastKnown | None:
    """The most recent numeric ground reading, with its age.

    Independent of freshness on purpose: this answers "when did anyone last
    actually measure the air, and what did they get", which is a different
    question from summarize_ground_aqi()'s "what is it right now". Callers
    decide which to state; `stale` carries what they need to word it.

    Readings with no timestamp are skipped entirely — "most recent" is a
    claim about time, and one cannot be made about a reading whose time is
    unknown. Ties resolve to the highest AQI, matching the worst-station rule
    already used for the fresh range: when several stations report the same
    hour, the one a reader should act on is the worst of them.
    """
    now = now or datetime.now(timezone.utc)
    dated = [r for r in readings if r.aqi is not None and r.measured_at is not None]
    if not dated:
        return None

    newest = max(r.measured_at for r in dated)
    at_newest = [r for r in dated if r.measured_at == newest]
    worst = max(at_newest, key=lambda r: r.aqi)
    age = hours_old(worst, now)

    return GroundAQILastKnown(
        station_name=worst.name,
        aqi=worst.aqi,
        measured_at=newest.isoformat(),
        hours_old=age,
        stale=is_stale(worst, now),
        stations_reporting=len(at_newest),
    )


# WHAT THE LAST-KNOWN BLOCK SAYS WHEN THERE IS NOTHING TO SAY.
#
# `last_known_ground_aqi` returns None when no reading carries BOTH a numeric
# AQI and a timestamp, and that is THREE different situations. The block
# asserted one of them — "no station has a timestamped reading at all" — and on
# the commonest of the three it is false: measured over the stored record on
# 2026-09-22, 11 of 43 days had no numeric AQI from any station, and on the two
# of those inside the prompt archive every station carried `measured_at` and
# `hours_old` while the block denied it. Rule 1 orders the model to state the
# block as given; rule 5 forbids presenting a measurement as absent. ROADMAP
# item 163.
#
# THE INSTRUCTION RIDES HERE RATHER THAN IN THE SYSTEM PROMPT'S AIR-QUALITY
# RULE, and that is the operator's point: a deployment whose stations are
# reliable never reaches this branch, so a rule sentence would cost it
# characters on every run for a case it never hits. These strings appear only
# on the days they apply.
#
# NOTHING HERE IS LOCAL. A station feeding particulates while the aggregator
# has not computed an index is how WAQI reports, not how Kisumu's sensors
# behave — item 95's rule, checked rather than assumed.
LAST_KNOWN_NO_STATIONS = (
    "Unavailable — no station reported at all. Air quality comes from the "
    "model guidance alone; say so plainly rather than going silent."
)
LAST_KNOWN_NO_NUMERIC_AQI = (
    "Unavailable — the stations are reporting but none of them carried a "
    "numeric AQI. Say that, and take the figure from the model guidance. They "
    "are NOT down and NOT absent: their own readings, with their timestamps "
    "and ages, are in GROUND AQI STATIONS above. Do not convert a PM figure "
    "into an AQI yourself."
)
LAST_KNOWN_NO_TIMESTAMP = (
    "Unavailable — a station reported a numeric AQI but none of those readings "
    "carries a timestamp, so there is no most-recent to name. Quote the value "
    "without claiming when it was taken."
)


def _aqi_value(reading) -> float | None:
    """A reading's AQI, whether it arrives typed or as a mapping.

    BOTH SHAPES ARE REAL at this seam. The pipeline holds `GroundAQIReading`
    objects; the prompt layer is loosely typed and the vector exporter feeds
    it plain dicts. A version of this that only did `reading.aqi` worked in
    production and raised on the vectors' own fixtures.
    """
    if isinstance(reading, dict):
        return reading.get("aqi")

    return getattr(reading, "aqi", None)


def last_known_absence(readings) -> str:
    """Which kind of nothing `last_known_ground_aqi` found.

    Only meaningful when that function returned None; the caller asks for this
    instead of asserting a cause it has not checked.

    THE ORDER OF THE BRANCHES IS THE POINT. "No numeric AQI" is tested before
    "no timestamp" because a reading can lack both, and of the two the missing
    NUMBER is what stops the block having anything to quote — saying the
    timestamps are missing when the values are is the exact error this
    replaces.
    """
    if not readings:
        return LAST_KNOWN_NO_STATIONS

    if not any(_aqi_value(r) is not None for r in readings):
        return LAST_KNOWN_NO_NUMERIC_AQI

    return LAST_KNOWN_NO_TIMESTAMP


def merge_ground_aqi(
    stored: list[GroundAQIReading], fresh: list[GroundAQIReading]
) -> list[GroundAQIReading]:
    """A re-issue's readings, in which a fresher absence never replaces an
    older measurement.

    Confirmed live on 2026-08-22: the morning run captured three stations
    with real values — Ochieng' Avenue among them at 160, Unhealthy for
    Sensitive Groups — and the 11:00Z re-fetch returned the same three
    stations with `aqi: null`. Storing those left the day's entry showing
    three nulls, and the single most actionable number that day survived
    only inside morning_issuance. Nothing had failed: upstream simply had no
    composite AQI at that hour, which is ordinary (see the "-" sentinel in
    fetch/waqi.py).

    A station missing from `fresh` is treated the same as one that came back
    null. fetch_ground_aqi_stations drops a station whose fetch errored, so
    absence IS a failed fetch and cannot be told apart from one.

    The kept reading keeps its ORIGINAL measured_at, which is the point:
    hours_old() and is_stale() then describe it honestly, and the narrative
    can say the last real reading was 160 at midnight and is nine hours old
    — more use than silence, and impossible to mistake for current.

    Stations are matched on station_id, not name: `name` is our display
    label and an operator can change it, while station_id is the identity
    WAQI answers to. Order follows `fresh` (configured station order), with
    stored-only stations appended.
    """
    stored_by_id = {r.station_id: r for r in stored}
    merged = []

    for reading in fresh:
        previous = stored_by_id.pop(reading.station_id, None)
        if reading.aqi is None and previous is not None and previous.aqi is not None:
            merged.append(previous)
            continue

        merged.append(reading)

    merged.extend(stored_by_id.values())
    return merged
