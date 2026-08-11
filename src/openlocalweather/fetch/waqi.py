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

from openlocalweather.models import GroundAQI

WAQI_URL_TEMPLATE = "https://api.waqi.info/feed/{station_id}/"
REQUEST_TIMEOUT_S = 15


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
    return GroundAQI(
        aqi=data.get("aqi"),
        pm25=(iaqi.get("pm25") or {}).get("v"),
        pm10=(iaqi.get("pm10") or {}).get("v"),
        station=(data.get("city") or {}).get("name"),
    )
