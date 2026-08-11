"""Optional METAR fetch via aviationweather.gov (covers most of the world,
not just the US).

Best-effort only: an unconfigured or wrong ICAO code, a station with no
current report, or an API hiccup all just return None rather than raising —
METAR is a nice-to-have cross-check, never a required input. The system
prompt (see llm/prompt.py) explicitly instructs the narrative to say so and
not treat stale/absent METAR as live ground truth, with the archive/
reanalysis data as the primary "actuals" source instead.
"""

from __future__ import annotations

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"
REQUEST_TIMEOUT_S = 15


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
