"""Deterministic aggregation across multiple ground AQI stations.

Pure code, never the LLM — same "arithmetic is code's job" principle as
verify/. The range and which station is currently worst are simple to get
right in code and easy to get subtly wrong if an LLM is asked to eyeball
three numbers and pick a max each day; pre-computing them means the prompt
states a fact instead of asking for a calculation.
"""

from __future__ import annotations

from dataclasses import dataclass

from openlocalweather.models import GroundAQIReading


@dataclass
class GroundAQISummary:
    aqi_min: int
    aqi_max: int
    highest_station_name: str
    stations_with_aqi: int
    stations_total: int


def summarize_ground_aqi(readings: list[GroundAQIReading]) -> GroundAQISummary | None:
    """None if no station has a numeric AQI right now (e.g. every station
    returned WAQI's "-" no-data sentinel — see fetch/waqi.py) or the list
    is empty. Ties for highest resolve to whichever station sorts first,
    which is an arbitrary but stable and harmless choice."""
    with_aqi = [r for r in readings if r.aqi is not None]
    if not with_aqi:
        return None
    worst = max(with_aqi, key=lambda r: r.aqi)
    best = min(with_aqi, key=lambda r: r.aqi)
    return GroundAQISummary(
        aqi_min=best.aqi,
        aqi_max=worst.aqi,
        highest_station_name=worst.name,
        stations_with_aqi=len(with_aqi),
        stations_total=len(readings),
    )
