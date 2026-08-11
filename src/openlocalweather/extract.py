"""Deterministic per-model prediction extraction from Open-Meteo responses —
code, not the LLM, same principle as verify/.

Ports extractDay0PredictionsFromHourly()/extractDayNPredictionsFromDaily()
from KisumuForecastPipeline_v2.gs, but returns a list[ModelPrediction]
instead of the original's pipe-delimited string — see models.py's docstring
for why that round-trip was dropped rather than preserved.
"""

from __future__ import annotations

from openlocalweather.defaults import RAIN_THRESHOLD_MM
from openlocalweather.fetch.open_meteo import get_onset_hour
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
        precip = h.get(f"precipitation_{model}") or h.get("precipitation") or []
        wind = h.get(f"windgusts_10m_{model}") or h.get("windgusts_10m") or []
        temp = h.get(f"temperature_2m_{model}") or h.get("temperature_2m") or []
        press = h.get(f"pressure_msl_{model}") or h.get("pressure_msl") or []

        rain = any((v or 0) >= threshold for v in precip)
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

        mslp_trend = None
        if day_index > 0 and day_index < len(press_arr):
            prev = press_arr[day_index - 1]
            curr = press_arr[day_index]
            if prev is not None and curr is not None:
                mslp_trend = curr - prev

        predictions.append(
            ModelPrediction(
                model=model,
                rain=(precip or 0) >= threshold,
                onset=None,
                wind_kmh=wind,
                high_c=high,
                low_c=low,
                mslp_trend=mslp_trend,
            )
        )
    return predictions
