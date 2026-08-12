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
