"""Optional ground-truth AQI fetch via waqi.info, across 1-N stations.

Best-effort only, same rationale as metar.py: WAQI ground sensors go offline
sometimes, and the system prompt already instructs the narrative to fall
back to CAMS model-only air-quality data and say so explicitly when no
station returns data. Station IDs cannot be validated from code — a wrong
one silently poisons the "ground truth" comparison, so each must be
verified manually at waqi.info when configuring a location (see
config/location.example.yaml and config.WaqiStation).

Each station is fetched and sanitized independently, so one station being
offline never takes the others down with it — same "one bad element
shouldn't abort the batch" pattern used throughout this module and its
callers.
"""

from __future__ import annotations

from datetime import datetime

import requests
from pydantic import ValidationError

from openlocalweather.config import WaqiStation
from openlocalweather.models import GroundAQIReading

WAQI_URL_TEMPLATE = "https://api.waqi.info/feed/{station_id}/"
REQUEST_TIMEOUT_S = 15


def _as_int_or_none(value) -> int | None:
    """WAQI uses the literal string "-" for "no value available" — not
    absent, not null, an actual dash in the field. Confirmed live: a real
    station returned aqi="-" while its individual pollutant readings
    (iaqi.pm25.v etc.) were present and numeric, so the station clearly had
    data, just no *composite* AQI computed at that moment. Silently
    coercing that into None (rather than letting pydantic reject the string
    and crash the whole pipeline run) is exactly the "degrade gracefully"
    contract this fetch module is supposed to honor.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit() and value.strip() != "-":
        return int(value)
    return None


def _as_number_or_none(value) -> float | None:
    """Same "-" sentinel handling as _as_int_or_none, for pm25/pm10."""
    if isinstance(value, (int, float)):
        return float(value)
    if value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_measured_at(data: dict) -> datetime | None:
    """WAQI reports when the reading was actually taken in data.time.iso,
    e.g. "2026-08-11T22:00:00Z". Missing or unparseable -> None, treated as
    unknown freshness by aqi.summarize_ground_aqi() (excluded from the
    "confidently fresh" range, not assumed fresh)."""
    iso = (data.get("time") or {}).get("iso")
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_ground_aqi_reading(name: str, station_id: str, token: str) -> GroundAQIReading | None:
    """Fetches and sanitizes one station's current reading. Returns None on
    any failure — missing config, network error, non-200, malformed JSON,
    a WAQI status other than "ok", or a response shape pydantic rejects
    even after sanitization — never raises into the caller.
    """
    if not station_id or not token:
        return None
    try:
        resp = requests.get(
            WAQI_URL_TEMPLATE.format(station_id=station_id),
            params={"token": token},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if payload.get("status") != "ok":
        return None

    data = payload.get("data") or {}
    iaqi = data.get("iaqi") or {}
    try:
        return GroundAQIReading(
            name=name,
            station_id=station_id,
            aqi=_as_int_or_none(data.get("aqi")),
            pm25=_as_number_or_none((iaqi.get("pm25") or {}).get("v")),
            pm10=_as_number_or_none((iaqi.get("pm10") or {}).get("v")),
            measured_at=_parse_measured_at(data),
        )
    except ValidationError:
        # Belt-and-suspenders: WAQI's response shape is outside our
        # control, and this function's contract is to degrade to None on
        # any failure, never to raise into the pipeline.
        return None


def fetch_ground_aqi_stations(stations: list[WaqiStation], token: str) -> list[GroundAQIReading]:
    """Fetches every configured station independently; stations that fail
    are simply absent from the result, not a reason to drop the others."""
    readings = []
    for station in stations:
        reading = fetch_ground_aqi_reading(station.name, station.station_id, token)
        if reading is not None:
            readings.append(reading)
    return readings
