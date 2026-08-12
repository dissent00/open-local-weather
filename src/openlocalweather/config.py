"""Location config loading and validation.

Ports the LOCATION block from the Apps Script pipeline into a validated
Pydantic model, loaded from YAML. Unlike the Apps Script version — where a
malformed or missing field just produces `undefined` somewhere downstream and
fails silently or confusingly — this fails fast at startup with a clear
error, since YAML edited by hand across many forks is exactly where typos
happen.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Point(BaseModel):
    lat: float
    lon: float


class RegionPoint(Point):
    name: str


class SecondaryPoint(Point):
    enabled: bool = False
    name: str = ""
    section_label: str = ""
    lat: float = 0.0
    lon: float = 0.0


class WaqiStation(BaseModel):
    """One ground-truth AQI monitoring station. `name` is OUR configured
    display name — used everywhere a station is named (site, email, the
    LLM prompt) — not WAQI's own `city.name`, which can be verbose or
    inconsistent between stations. Verify the station_id manually at
    waqi.info; it cannot be validated from code, and a wrong ID silently
    poisons the "ground truth" comparison (see fetch/waqi.py)."""

    name: str
    station_id: str


class LocationConfig(BaseModel):
    region_name: str
    primary_place_name: str
    timezone: str
    primary_point: Point
    secondary_point: SecondaryPoint = Field(default_factory=SecondaryPoint)
    region_points: list[RegionPoint] = Field(default_factory=list)
    metar_station_icao: str = ""
    waqi_stations: list[WaqiStation] = Field(default_factory=list)
    local_bulletin_url: str = ""
    local_bulletin_source_name: str = ""


def load_location_config(path: str | Path) -> LocationConfig:
    """Load and validate a location.yaml file.

    Raises FileNotFoundError if the path doesn't exist, and
    pydantic.ValidationError (with a field-level message) if the YAML is
    missing required fields or has the wrong shape — both are meant to be
    loud, readable failures rather than something that silently degrades a
    forecast run.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Location config not found at {path}. "
            "Copy config/location.example.yaml to config/location.yaml and fill it in."
        )
    raw = yaml.safe_load(path.read_text())
    if not raw or "location" not in raw:
        raise ValueError(f"{path} must have a top-level 'location:' key.")
    return LocationConfig.model_validate(raw["location"])
