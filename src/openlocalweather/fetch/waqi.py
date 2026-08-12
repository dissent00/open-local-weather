"""Optional ground-truth AQI fetch via waqi.info.

Best-effort only, same rationale as metar.py: WAQI ground sensors go offline
sometimes, and the system prompt already instructs the narrative to fall
back to CAMS model-only air-quality data and say so explicitly when this
returns None. The station ID cannot be validated from code — a wrong ID
silently poisons the "ground truth" comparison, so it must be verified
manually at waqi.info when configuring a new location (see
config/location.example.yaml).
"""

from __future__ import annotations

import requests
from pydantic import ValidationError

from openlocalweather.models import GroundAQI

WAQI_URL_TEMPLATE = "https://api.waqi.info/feed/{station_id}/"
REQUEST_TIMEOUT_S = 15


def _as_int_or_none(value) -> int | None:
    """WAQI uses the literal string "-" for "no value available" — not
    absent, not null, an actual dash in the field. Confirmed live: this
    project's own configured station returned aqi="-" while its individual
    pollutant readings (iaqi.pm25.v etc.) were present and numeric, so the
    station clearly had data, just no *composite* AQI computed at that
    moment. Silently coercing that into None (rather than letting pydantic
    reject the string and crash the whole pipeline run) is exactly the
    "degrade gracefully" contract this fetch module is supposed to honor.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit() and value.strip() != "-":
        return int(value)
    return None


def _as_number_or_none(value) -> float | None:
    """Same "-" sentinel handling as _as_int_or_none, for pm25/pm10 —
    GroundAQI types those as float | str | None specifically so a literal
    "-" doesn't crash the run the way the strictly-int aqi field did, but
    we still don't want a bare dash string leaking into the published
    narrative or the LLM prompt as if it meant something."""
    if isinstance(value, (int, float)):
        return float(value)
    if value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_ground_aqi(station_id: str, token: str) -> GroundAQI | None:
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
        return GroundAQI(
            aqi=_as_int_or_none(data.get("aqi")),
            pm25=_as_number_or_none((iaqi.get("pm25") or {}).get("v")),
            pm10=_as_number_or_none((iaqi.get("pm10") or {}).get("v")),
            station=(data.get("city") or {}).get("name"),
        )
    except ValidationError:
        # Belt-and-suspenders: WAQI's response shape is outside our
        # control, and this function's contract (like metar.py's) is to
        # degrade to None on any failure, never to raise into the pipeline.
        return None
