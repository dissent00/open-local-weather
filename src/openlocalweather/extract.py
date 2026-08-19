"""Deterministic per-model prediction extraction from Open-Meteo responses —
code, not the LLM, same principle as verify/.

Ports extractDay0PredictionsFromHourly()/extractDayNPredictionsFromDaily()
from KisumuForecastPipeline_v2.gs, but returns a list[ModelPrediction]
instead of the original's pipe-delimited string — see models.py's docstring
for why that round-trip was dropped rather than preserved.
"""

from __future__ import annotations

from openlocalweather.defaults import RAIN_THRESHOLD_MM
from openlocalweather.fetch.open_meteo import get_onset_hour, pick_series
from openlocalweather.models import ModelPrediction


def extract_day0_predictions_from_hourly(
    hourly_multi_model: dict, models: list[str], threshold: float = RAIN_THRESHOLD_MM
) -> list[ModelPrediction]:
    """Pulls each model's Day+0 prediction from hourly data — onset comes
    from the actual hour-by-hour precip series, which only exists at hourly
    resolution (today only, by design)."""
    if not hourly_multi_model or not hourly_multi_model.get("hourly"):
        return []
    h = hourly_multi_model["hourly"]
    times = h.get("time") or []

    predictions = []
    for model in models:
        precip = pick_series(h, f"precipitation_{model}", "precipitation")
        # Both gust spellings, newest first — see pick_series on why the
        # all-null case must be skipped rather than merely fallen back from.
        wind = pick_series(
            h, f"wind_gusts_10m_{model}", f"windgusts_10m_{model}", "wind_gusts_10m", "windgusts_10m"
        )
        temp = pick_series(h, f"temperature_2m_{model}", "temperature_2m")
        press = pick_series(h, f"pressure_msl_{model}", "pressure_msl")

        # An entirely absent/all-null precip series means no data for this
        # model, which is not the same as a confident dry forecast — see
        # ModelPrediction.rain. (A present series that simply never crosses
        # the threshold IS a real "no rain" call.)
        has_precip_data = any(v is not None for v in precip)
        rain = any((v or 0) >= threshold for v in precip) if has_precip_data else None
        onset = get_onset_hour(times, precip, threshold) if rain else None

        wind_vals = [v for v in wind if v is not None]
        temp_vals = [v for v in temp if v is not None]
        press_vals = [v for v in press if v is not None]

        predictions.append(
            ModelPrediction(
                model=model,
                rain=rain,
                onset=onset,
                wind_kmh=max(wind_vals) if wind_vals else None,
                high_c=max(temp_vals) if temp_vals else None,
                low_c=min(temp_vals) if temp_vals else None,
                mslp_trend=(press_vals[-1] - press_vals[0]) if len(press_vals) >= 2 else None,
            )
        )
    return predictions


def extract_day_n_predictions_from_daily(
    daily_multi_model: dict, day_index: int, models: list[str], threshold: float = RAIN_THRESHOLD_MM
) -> list[ModelPrediction]:
    """Pulls each model's Day+N prediction from DAILY data — no onset
    available at this resolution by design (only daily aggregates are
    fetched at Day+3/Day+7, to control API cost)."""
    if not daily_multi_model or not daily_multi_model.get("daily"):
        return []
    d = daily_multi_model["daily"]

    predictions = []
    for model in models:
        precip_arr = d.get(f"precipitation_sum_{model}") or d.get("precipitation_sum") or []
        wind_arr = d.get(f"windgusts_10m_max_{model}") or d.get("windgusts_10m_max") or []
        high_arr = d.get(f"temperature_2m_max_{model}") or d.get("temperature_2m_max") or []
        low_arr = d.get(f"temperature_2m_min_{model}") or d.get("temperature_2m_min") or []
        press_arr = d.get(f"pressure_msl_mean_{model}") or d.get("pressure_msl_mean") or []

        precip = precip_arr[day_index] if day_index < len(precip_arr) else None
        wind = wind_arr[day_index] if day_index < len(wind_arr) else None
        high = high_arr[day_index] if day_index < len(high_arr) else None
        low = low_arr[day_index] if day_index < len(low_arr) else None

        # No precipitation value means this model's forecast horizon doesn't
        # reach this lead time (UKMO stops around 7.2 days, so it has no
        # Day+7 at all). Record that as "unknown", never as "no rain" — see
        # ModelPrediction.rain.

        mslp_trend = None
        if day_index > 0 and day_index < len(press_arr):
            prev = press_arr[day_index - 1]
            curr = press_arr[day_index]
            if prev is not None and curr is not None:
                mslp_trend = curr - prev

        predictions.append(
            ModelPrediction(
                model=model,
                rain=None if precip is None else precip >= threshold,
                onset=None,
                wind_kmh=wind,
                high_c=high,
                low_c=low,
                mslp_trend=mslp_trend,
            )
        )
    return predictions
