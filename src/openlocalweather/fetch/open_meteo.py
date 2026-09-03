"""Open-Meteo fetch functions: multi-model forecast, archive (actuals), and
air quality.

This is the one data source NOT behind a swappable interface, deliberately —
the multi-model synthesis design depends on Open-Meteo's consistent
per-model field-naming convention (`precipitation_gfs_seamless`,
`precipitation_ecmwf_ifs025`, ...) across its forecast/archive/air-quality
APIs. Adding a model Open-Meteo already serves is a one-line change to
defaults.MODELS; swapping the provider entirely would mean rewriting this
module and extract.py's field-parsing assumptions together — a real
undertaking, not a config change.

Failures here raise OpenMeteoFetchError rather than failing soft, unlike
metar.py/waqi.py/the bulletin fetchers: today's forward guidance and
yesterday's actuals are not optional inputs the pipeline can degrade
gracefully without — a failed fetch here means the run has nothing usable to
synthesize or score, and should abort loudly rather than produce a forecast
from partial/garbage data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import sys
import time

import requests

from openlocalweather.dates import format_date, parse_date
from openlocalweather.defaults import RAIN_THRESHOLD_MM
from openlocalweather.models import SOURCE_REANALYSIS, DailyActual

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

REQUEST_TIMEOUT_S = 30

HOURLY_FORECAST_VARS = (
    "temperature_2m,precipitation_probability,precipitation,cloud_cover,"
    # NOTE the underscore in "wind_gusts_10m". Open-Meteo accepts the legacy
    # "windgusts_10m" alias for most models, but under that alias
    # ecmwf_ifs025 returns an all-null series while every other model returns
    # real data — so it fails silently and only for one model. That cost this
    # deployment every Day+0 ECMWF wind score from the beginning; the gap was
    # invisible until the review aggregated wind per model and ECMWF alone had
    # no value. Verified 2026-08-19 against the live API: the current
    # "wind_gusts_10m" name returns real ECMWF data, and identical values to
    # the alias for all other models. Don't "tidy" this back to the alias.
    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,cape,pressure_msl,uv_index"
)
DAILY_FORECAST_VARS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,windspeed_10m_max,windgusts_10m_max,"
    "pressure_msl_mean,uv_index_max"
)
REGIONAL_DAILY_VARS = "precipitation_sum,windspeed_10m_max,pressure_msl_mean"
ARCHIVE_HOURLY_VARS = (
    "temperature_2m,precipitation,windspeed_10m,windgusts_10m,cloud_cover,pressure_msl"
)
AIR_QUALITY_HOURLY_VARS = "pm10,pm2_5,european_aqi,us_aqi"


class OpenMeteoFetchError(RuntimeError):
    """A core Open-Meteo fetch failed (network error or non-200 response)."""


# Transient failures here used to abort the whole run on the first blip.
#
# That was backwards: the LLM providers retry (see llm/gemini.py, which
# justifies it as "cheap"), and those calls cost money. This one is FREE, and
# a failure is more expensive — the LLM is never reached, so the run produces
# no forecast at all. On the server that means a missed issuance for the day,
# which is the reliability problem the multiple cron slots exist to fight; in
# the app it means a user tapped generate and got nothing.
#
# Observed in practice: a run succeeded, and an identical one 30 seconds later
# failed to reach the API, with the service demonstrably healthy either side.
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 1.5

# ONE CONNECTION FOR THE WHOLE RUN, rather than a fresh TCP+TLS handshake per
# call. A run makes seven /v1/forecast requests plus air quality inside about
# half a minute, and every one of them used to open its own connection.
#
# Added 2026-09-03 for ROADMAP item 53, whose read timeouts have no
# established cause. Connection churn is a plausible mechanism and this is the
# cheap half of testing it; if it is not the cause, a saved handshake per
# request is still worth having. It is NOT a claimed fix — see item 53's
# confound before treating it as one.
_SESSION = requests.Session()

# How many requests this run has made, and to what. Item 53 cost a full
# investigation because the logs said only that something timed out: not which
# request, not its position in the sequence, not how long the run had been
# going. Every one of those turned out to matter.
_request_count = 0


def reset_request_counter() -> None:
    """Zeroes the position counter so numbers are per-run, not per-process."""
    global _request_count
    _request_count = 0


def requests_made() -> int:
    return _request_count


def _describe(url: str, params: dict[str, Any]) -> str:
    """Enough to identify a request in a log without dumping a whole query.

    `forecast_days` is named explicitly because it is the parameter that
    distinguished the failing call from its successful twin for two weeks of
    item 53, and a log omitting it would send the next reader back to the
    source to work out which call this was.
    """
    days = params.get("forecast_days")
    tail = f" forecast_days={days}" if days is not None else ""
    return f"{url.rsplit('/', 1)[-1]}{tail}"


def _get(url: str, params: dict[str, Any]) -> dict:
    global _request_count
    _request_count += 1
    position = _request_count
    started = time.monotonic()

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as e:
            print(
                f"request #{position} ({_describe(url, params)}) failed on "
                f"attempt {attempt}/{MAX_ATTEMPTS}, {time.monotonic() - started:.1f}s "
                f"into this request: {e}",
                file=sys.stderr,
            )
            last_error = OpenMeteoFetchError(f"Request to {url} failed: {e}")
        else:
            if resp.status_code == 200:
                payload = resp.json()
                # The server's own clock, carried on every response we already
                # make. Free, and the only time reference here that does not
                # depend on this machine being right — see daypart.reconcile_now.
                server_date = resp.headers.get("Date")
                if isinstance(payload, dict) and server_date:
                    payload["_server_date"] = server_date
                return payload
            # 4xx other than 429 means the REQUEST is wrong — a bad variable
            # name, an impossible coordinate. Retrying just repeats the
            # mistake more slowly, and hides it behind a longer wait.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise OpenMeteoFetchError(
                    f"{url} returned HTTP {resp.status_code}: {resp.text[:500]}"
                )
            last_error = OpenMeteoFetchError(
                f"{url} returned HTTP {resp.status_code}: {resp.text[:500]}"
            )
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BASE_DELAY_S * attempt)
    raise last_error  # type: ignore[misc]


def fetch_forecast_hourly_today(lat: float, lon: float, models: list[str], timezone: str) -> dict:
    """Today's hourly multi-model guidance for the primary/secondary point.
    forecast_days=1 — onset timing is only meaningful for "today"."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": HOURLY_FORECAST_VARS,
        "forecast_days": 1,
        "timezone": timezone,
        "models": ",".join(models),
    }
    return _get(FORECAST_URL, params)


def fetch_forecast_hourly_forward(
    lat: float, lon: float, models: list[str], timezone: str
) -> dict:
    """Hourly multi-model guidance covering today AND tomorrow.

    A SEPARATE call rather than widening fetch_forecast_hourly_today, and
    deliberately so. `extract_day0_predictions_from_hourly` consumes whatever
    series it is handed with no date slicing, so widening that fetch to two
    days would silently score 48 hours as "today" — quietly corrupting the
    accuracy record, which is the one number in this project that must not
    drift. Keeping the scored fetch untouched means no change here can reach
    it.

    This exists because a run issued in the evening was being asked to talk
    about tonight while holding only 00:00-23:00 of today: at 18:15 that is
    six hours of forecast and no overnight data at all.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": HOURLY_FORECAST_VARS,
        "forecast_days": 2,
        "timezone": timezone,
        "models": ",".join(models),
    }
    return _get(FORECAST_URL, params)


def fetch_forecast_daily_extended(
    lat: float, lon: float, models: list[str], timezone: str, days: int = 8
) -> dict:
    """Daily multi-model summary out to `days` days. Default 8 (not 7!) so
    index 7 genuinely represents 7 full days out — index 0 is today."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": DAILY_FORECAST_VARS,
        "forecast_days": days,
        "timezone": timezone,
        "models": ",".join(models),
    }
    return _get(FORECAST_URL, params)


# Offsets, in degrees, for the synoptic-scale pressure ring. Deliberately
# NOT the near-field `region_points` from location.yaml: those span roughly
# 125 x 55 km around Kisumu and feed local gradient/convection reasoning,
# while synoptic features — highs, lows, the ITCZ, tropical systems — have
# wavelengths of 1,000-4,000 km. A 125 km box fits entirely inside one
# system's gradient, which is why the Synoptic Overview had no synoptic data
# to describe. Conflating the two scales would degrade both.
#
# 12 degrees is about 1,300 km, so the ring spans ~2,600 km — enough to see
# a centre and its movement without pretending to resolve a front.
SYNOPTIC_RING_OFFSET_DEG = 12.0
SYNOPTIC_RING: list[tuple[float, float, str]] = [
    (0.0, 0.0, "centre"),
    (1.0, 0.0, "N"), (1.0, 1.0, "NE"), (0.0, 1.0, "E"), (-1.0, 1.0, "SE"),
    (-1.0, 0.0, "S"), (-1.0, -1.0, "SW"), (0.0, -1.0, "W"), (1.0, -1.0, "NW"),
]


def synoptic_ring_points(lat: float, lon: float, offset_deg: float = SYNOPTIC_RING_OFFSET_DEG):
    """(lat, lon, label) for the ring around a primary point.

    Latitudes are clamped to the valid range so a high-latitude fork doesn't
    request an impossible coordinate; longitudes wrap. Location-agnostic by
    construction — a fork gets this with no extra configuration.
    """
    points = []
    for dlat, dlon, label in SYNOPTIC_RING:
        ring_lat = max(-90.0, min(90.0, lat + dlat * offset_deg))
        ring_lon = (lon + dlon * offset_deg + 180.0) % 360.0 - 180.0
        points.append((round(ring_lat, 4), round(ring_lon, 4), label))
    return points


def fetch_synoptic_pressure(lat: float, lon: float, timezone: str, days: int = 3) -> dict:
    """Coarse MSLP field for the Synoptic Overview.

    One request for all nine points (Open-Meteo accepts comma-separated
    coordinates), `best_match` only — this is a large-scale pattern sketch,
    not per-model verification data, and is never scored. Measured
    2026-08-19: HTTP 200 in 1.16 s for 3,133 bytes.
    """
    points = synoptic_ring_points(lat, lon)
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "daily": "pressure_msl_mean",
        "forecast_days": days,
        "timezone": timezone,
        "models": "best_match",
    }
    raw = _get(FORECAST_URL, params)
    blocks = raw if isinstance(raw, list) else [raw]
    return {
        "points": [
            {
                "label": label,
                "lat": block.get("latitude"),
                "lon": block.get("longitude"),
                "mslp_hpa": (block.get("daily") or {}).get("pressure_msl_mean") or [],
            }
            for (_, _, label), block in zip(points, blocks)
        ]
    }


def fetch_regional_pressure(
    primary_point: tuple[float, float],
    region_points: list[tuple[float, float]],
    timezone: str,
    days: int = 7,
) -> dict:
    """Multi-point daily pressure/precip/wind snapshot across the wider
    region, used for the Synoptic Overview narrative section. Always uses
    Open-Meteo's blended `best_match` model, not the full multi-model set —
    this is a regional-pattern sketch, not per-model verification data."""
    points = [primary_point, *region_points]
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "daily": REGIONAL_DAILY_VARS,
        "forecast_days": days,
        "timezone": timezone,
        "models": "best_match",
    }
    return _get(FORECAST_URL, params)


def fetch_air_quality(lat: float, lon: float, timezone: str, days: int = 1) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": AIR_QUALITY_HOURLY_VARS,
        "forecast_days": days,
        "timezone": timezone,
    }
    return _get(AIR_QUALITY_URL, params)


def fetch_archive_range(lat: float, lon: float, start_date: date, end_date: date, timezone: str) -> dict:
    """Actual/reanalysis hourly data for an arbitrary date range in ONE
    call. Open-Meteo's archive retains this indefinitely, which is what lets
    rolling stats be re-derived statelessly (see verify/) rather than
    needing fragile incremental storage."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": format_date(start_date),
        "end_date": format_date(end_date),
        "hourly": ARCHIVE_HOURLY_VARS,
        "timezone": timezone,
    }
    return _get(ARCHIVE_URL, params)


def fetch_archive_single_day(lat: float, lon: float, day: date, timezone: str) -> dict:
    """The cheap daily-upsert path — see store/actuals_cache.py."""
    return fetch_archive_range(lat, lon, day, day, timezone)


# ---------------------------------------------------------------------------
# Bucketing actuals: hourly archive response -> {date: DailyActual}
# ---------------------------------------------------------------------------


def get_onset_hour(
    times: list[str], precip: list[float | None], threshold: float = RAIN_THRESHOLD_MM
) -> str | None:
    """First hour (HH:MM) whose precipitation crossed `threshold`, or None
    if it never did. `times` entries look like "2026-08-11T14:00"."""
    for t, p in zip(times, precip):
        if (p or 0) >= threshold:
            return t.split("T")[1] if "T" in t else None
    return None


def pick_series(hourly: dict, *candidate_keys: str) -> list:
    """First candidate key holding at least one non-null value.

    Not just `hourly.get(a) or hourly.get(b)`. Open-Meteo returns a
    correctly-named array full of nulls when a model doesn't supply a
    variable under that alias, and a list of Nones is truthy — so a plain
    `or` chain latches onto the empty series and never tries the fallback.
    That is exactly how ECMWF's Day+0 wind went unscored for the whole life
    of this deployment without one error being raised anywhere.
    """
    for key in candidate_keys:
        series = hourly.get(key)
        if series and any(v is not None for v in series):
            return series
    return []


def bucket_hourly_by_date(hourly_json: dict, threshold: float = RAIN_THRESHOLD_MM) -> dict[date, DailyActual]:
    """Splits a flat multi-day hourly archive response into one DailyActual
    per calendar date. This is what makes the batch-fetch-and-rescore
    approach work — "what actually happened on date X" for any X in the
    fetched range becomes a single dict lookup.

    Wind gust fallback matches the original pipeline exactly: if the
    windgusts_10m ARRAY is present in the response at all, it's used for
    every hour (even hours where that specific value is null); only if the
    whole array is absent does the response fall back to windspeed_10m.
    """
    if not hourly_json or not hourly_json.get("hourly"):
        return {}
    h = hourly_json["hourly"]
    times = h.get("time") or []
    temp_arr = h.get("temperature_2m") or []
    precip_arr = h.get("precipitation") or []
    wind_arr = pick_series(h, "wind_gusts_10m", "windgusts_10m", "wind_speed_10m", "windspeed_10m")
    pressure_arr = h.get("pressure_msl") or []

    by_date: dict[str, dict[str, list]] = {}
    for i, t in enumerate(times):
        d_str = t.split("T")[0]
        bucket = by_date.setdefault(
            d_str, {"temps": [], "precip": [], "wind": [], "pressure": [], "times": []}
        )
        bucket["temps"].append(temp_arr[i] if i < len(temp_arr) else None)
        bucket["precip"].append(precip_arr[i] if i < len(precip_arr) else None)
        bucket["wind"].append(wind_arr[i] if i < len(wind_arr) else None)
        bucket["pressure"].append(pressure_arr[i] if i < len(pressure_arr) else None)
        bucket["times"].append(t)

    result: dict[date, DailyActual] = {}
    for d_str, day in by_date.items():
        temps = [v for v in day["temps"] if v is not None]
        wind = [v for v in day["wind"] if v is not None]
        pressure = [v for v in day["pressure"] if v is not None]
        precip_mm = (
            round(sum(v for v in day["precip"] if v is not None), 2)
            if any(v is not None for v in day["precip"])
            else None
        )
        onset_hour = get_onset_hour(day["times"], day["precip"], threshold)
        high_c = max(temps) if temps else None
        low_c = min(temps) if temps else None
        peak_wind = max(wind) if wind else None
        mslp_trend = (pressure[-1] - pressure[0]) if len(pressure) >= 2 else None

        # ROADMAP item 45, trap 2. A key per field this source actually
        # supplied — a field that came back None is left unstamped, because a
        # stamp asserts that an observation was made and stamping an absence
        # would record one that never happened.
        provenance = {"rain": SOURCE_REANALYSIS}
        for field, value in (
            ("high_c", high_c),
            ("low_c", low_c),
            ("peak_wind_kmh", peak_wind),
            ("mslp_trend", mslp_trend),
            ("onset_hour", onset_hour),
            ("precip_mm", precip_mm),
        ):
            if value is not None:
                provenance[field] = SOURCE_REANALYSIS

        result[parse_date(d_str)] = DailyActual(
            rain=any((v or 0) >= threshold for v in day["precip"]),
            high_c=high_c,
            low_c=low_c,
            peak_wind_kmh=peak_wind,
            mslp_trend=mslp_trend,
            onset_hour=onset_hour,
            provenance=provenance,
            # Summed over hours that reported a value. An all-null day gives
            # None rather than 0.0 — "no data" and "no rain" are different
            # answers and the summary must not conflate them.
            precip_mm=precip_mm,
        )
    return result
