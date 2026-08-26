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


def is_stale(reading: GroundAQIReading, now: datetime | None = None) -> bool:
    """True if stale OR of unknown freshness — both are treated the same
    way (excluded from the confident range), just worded differently
    wherever they're surfaced to a reader."""
    age = hours_old(reading, now)
    return age is None or age > STALE_THRESHOLD_HOURS


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
