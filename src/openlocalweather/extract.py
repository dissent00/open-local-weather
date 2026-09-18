"""Deterministic per-model prediction extraction from Open-Meteo responses —
code, not the LLM, same principle as verify/.

Ports extractDay0PredictionsFromHourly()/extractDayNPredictionsFromDaily()
from KisumuForecastPipeline_v2.gs, but returns a list[ModelPrediction]
instead of the original's pipe-delimited string — see models.py's docstring
for why that round-trip was dropped rather than preserved.
"""

from __future__ import annotations

from datetime import datetime

from openlocalweather.daypart import forward_hours
from openlocalweather.defaults import ISSUANCE_WINDOW_HOURS, RAIN_THRESHOLD_MM
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
        # The SUSTAINED wind, its own series and its own field — item 146.
        # Never a fallback for the gust and never filled from it.
        sustained = pick_series(
            h, f"wind_speed_10m_{model}", f"windspeed_10m_{model}", "wind_speed_10m", "windspeed_10m"
        )
        temp = pick_series(h, f"temperature_2m_{model}", "temperature_2m")
        press = pick_series(h, f"pressure_msl_{model}", "pressure_msl")
        # No daily maximum exists at hourly resolution, so Day+0's is the
        # highest hour — the same quantity precipitation_probability_max
        # serves at Day+3/+7, derived here rather than served. See
        # ModelPrediction.rain_probability_pct for why it is recorded at all
        # before anything scores it.
        prob = pick_series(
            h, f"precipitation_probability_{model}", "precipitation_probability"
        )
        cloud = pick_series(h, f"cloud_cover_{model}", "cloud_cover")
        cape = pick_series(h, f"cape_{model}", "cape")
        bearing = pick_series(h, f"wind_direction_10m_{model}", "wind_direction_10m")

        # An entirely absent/all-null precip series means no data for this
        # model, which is not the same as a confident dry forecast — see
        # ModelPrediction.rain. (A present series that simply never crosses
        # the threshold IS a real "no rain" call.)
        has_precip_data = any(v is not None for v in precip)
        # THE DAILY TOTAL, NOT THE WETTEST HOUR — ROADMAP item 97, changed
        # 2026-09-09.
        #
        # This asked whether ANY HOUR crossed the threshold while
        # extract_day_n_predictions_from_daily asked whether the DAY'S TOTAL
        # did, so the same 2.0 mm day was `rain: false` at Day+0 and
        # `rain: true` at Day+3 — and that boolean is what every Brier score,
        # rain percentage and ranking finding is built on. Six of 85 stored
        # Day+0 predictions carried the difference.
        #
        # The total wins because it is the only rule that CAN apply at every
        # lead: the extended forecast comes from a daily endpoint and has no
        # hours to take a peak over. It is also what the prompt already tells
        # the forecaster — "whether measurable rain falls at the location
        # during the day".
        rain = (
            sum(v for v in precip if v is not None) >= threshold
            if has_precip_data
            else None
        )
        # ONSET IS DELIBERATELY UNCHANGED and still asks about an HOUR. A day
        # whose rain never concentrated into one has no onset to report, so
        # this now legitimately returns rain=True with onset=None — which the
        # scorer already handles, since it scores onset only when both sides
        # have one.
        onset = get_onset_hour(times, precip, threshold) if rain else None

        wind_vals = [v for v in wind if v is not None]
        sustained_vals = [v for v in sustained if v is not None]
        # THE HOUR OF THIS MODEL'S OWN PEAK, so the bearing belongs to the
        # gust being reported. max() over (value, index) would break ties by
        # index; enumerate-and-max on the value alone keeps the first peak,
        # which is what get_onset_hour does for the same reason.
        peak_i = max(
            (i for i, v in enumerate(wind) if v is not None),
            key=lambda i: wind[i],
            default=None,
        )
        bearing_at_peak = (
            bearing[peak_i]
            if peak_i is not None and peak_i < len(bearing) and bearing[peak_i] is not None
            else None
        )
        temp_vals = [v for v in temp if v is not None]
        press_vals = [v for v in press if v is not None]
        prob_vals = [v for v in prob if v is not None]
        cloud_vals = [v for v in cloud if v is not None]
        cape_vals = [v for v in cape if v is not None]

        predictions.append(
            ModelPrediction(
                model=model,
                rain=rain,
                onset=onset,
                # Absent, not 0 — an all-null series is no data, and 0% is a
                # confident claim that it will not rain.
                rain_probability_pct=int(max(prob_vals)) if prob_vals else None,
                cloud_cover_pct=round(sum(cloud_vals) / len(cloud_vals), 1) if cloud_vals else None,
                # THE PEAK, not a mean — see ModelPrediction.peak_cape_jkg.
                # An all-null series is None rather than 0.0, because zero
                # CAPE is a confident claim of stable air and no data is not.
                peak_cape_jkg=max(cape_vals) if cape_vals else None,
                wind_kmh=max(wind_vals) if wind_vals else None,
                sustained_wind_kmh=max(sustained_vals) if sustained_vals else None,
                wind_direction_deg=bearing_at_peak,
                high_c=max(temp_vals) if temp_vals else None,
                low_c=min(temp_vals) if temp_vals else None,
                mslp_trend=(press_vals[-1] - press_vals[0]) if len(press_vals) >= 2 else None,
                # Day total. Additive and never scored — `rain` above stays
                # the boolean the record is built on. See ModelPrediction.
                precip_mm=(
                    round(sum(v for v in precip if v is not None), 2)
                    if has_precip_data
                    else None
                ),
            )
        )
    return predictions


def forecast_horizon_days(daily_multi_model: dict, model: str) -> int | None:
    """The furthest day index at which this model's precipitation sum is a
    value — how far the source forecast on THIS fetch. ROADMAP item 150.

    DERIVED, NOT DECLARED. A declared horizon goes stale silently the day a
    provider extends a model; what the response carried is a fact already
    in hand. Measured 2026-09-16: gfs, ecmwf and best_match reached Day+7,
    icon Day+6, ukmo Day+5 — and ukmo had read Day+6 the day before, which
    is why the stored figure is the maximum over clean runs, not this one.

    PRECIPITATION SETS THE REACH, operator's choice, because it is the
    variable `extract_day_n_predictions_from_daily` itself uses to say "this
    model's horizon doesn't reach this lead" and where the scored rain call
    comes from. A model whose temperature outruns its precipitation still
    cannot be scored on rain there. None means the model had no value at
    any lead, which is "not observed", never zero.

    Reads the series the way the extractor does (`pick_series`, suffixed
    then unsuffixed key), so the two cannot disagree about which array is
    the model's.
    """
    if not daily_multi_model or not daily_multi_model.get("daily"):
        return None
    d = daily_multi_model["daily"]
    precip_arr = pick_series(d, f"precipitation_sum_{model}", "precipitation_sum")
    reached = [i for i, v in enumerate(precip_arr) if v is not None]
    return max(reached) if reached else None


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
        # pick_series, NOT an `or` chain — ROADMAP item 88, divergence 7.
        # Open-Meteo returns a correctly-named array full of nulls when a
        # model does not supply a variable under that alias, and a list of
        # Nones is TRUTHY, so `or` latches onto it and never reaches the
        # fallback. That is how ECMWF's Day+0 wind went unscored for the whole
        # life of this deployment. pick_series was written for exactly this,
        # the Day+0 path above has used it since, and this path never got it —
        # while the Dart port has been correct all along.
        precip_arr = pick_series(d, f"precipitation_sum_{model}", "precipitation_sum")
        wind_arr = pick_series(d, f"windgusts_10m_max_{model}", "windgusts_10m_max")
        sustained_arr = pick_series(
            d, f"windspeed_10m_max_{model}", f"wind_speed_10m_max_{model}",
            "windspeed_10m_max", "wind_speed_10m_max",
        )
        high_arr = pick_series(d, f"temperature_2m_max_{model}", "temperature_2m_max")
        low_arr = pick_series(d, f"temperature_2m_min_{model}", "temperature_2m_min")
        press_arr = pick_series(d, f"pressure_msl_mean_{model}", "pressure_msl_mean")
        # Fetched on every daily request since before this project scored
        # anything, and read by nothing until item 58. Recorded now because a
        # calibration check needs history and history only accrues forwards.
        prob_arr = pick_series(
            d,
            f"precipitation_probability_max_{model}",
            "precipitation_probability_max",
        )
        # The day's CAPE maximum — ROADMAP item 158 step 2. Absent from every
        # daily response before 2026-09-18, so None on every archived row.
        cape_arr = pick_series(d, f"cape_max_{model}", "cape_max")

        precip = precip_arr[day_index] if day_index < len(precip_arr) else None
        cape = cape_arr[day_index] if day_index < len(cape_arr) else None
        prob = prob_arr[day_index] if day_index < len(prob_arr) else None
        wind = wind_arr[day_index] if day_index < len(wind_arr) else None
        sustained = sustained_arr[day_index] if day_index < len(sustained_arr) else None
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
                # None, never 0 — see ModelPrediction.rain_probability_pct.
                rain_probability_pct=None if prob is None else int(prob),
                wind_kmh=wind,
                sustained_wind_kmh=sustained,
                high_c=high,
                low_c=low,
                mslp_trend=mslp_trend,
                # The daily endpoint already gives a total, so this IS the
                # same quantity the Day+0 path sums by hand.
                precip_mm=precip,
                peak_cape_jkg=cape,
            )
        )
    return predictions


def extract_window_predictions(
    hourly_multi_model: dict,
    models: list[str],
    *,
    issued_local: datetime,
    hours: int = ISSUANCE_WINDOW_HOURS,
    threshold: float = RAIN_THRESHOLD_MM,
) -> list[ModelPrediction]:
    """Each model's claim about the next `hours` from the issuance.

    ROADMAP item 104, contract item 2. Day+0 is the only broken lead: at 06:00
    a quarter of the calendar day is already spent and at 22:00 ninety percent
    of it is, so a calendar-day claim is ALREADY part hindcast and a late
    issuance only makes that visible. A window measured from the issuance
    makes every issuance make the same KIND of claim, which is what
    lead-from-initialization means and what operational verification does.

    TWO EXISTING FUNCTIONS COMPOSED, NOT A THIRD IMPLEMENTATION. The slicing
    is `daypart.forward_hours` and the arithmetic is
    `extract_day0_predictions_from_hourly`, unchanged and untouched. That is
    the point rather than a convenience: the window differs from Day+0 in
    WHICH HOURS GO IN and in nothing else, so the two cannot drift in their
    rounding, their gust-series fallback or their onset rule — and
    `test_the_window_uses_the_same_arithmetic_as_day_zero` pins it by handing
    both the same hours and demanding identical output.

    A SHORT WINDOW IS NOT A CLAIM, and returning one would be the exact
    failure `forward_hours` was written to avoid: "scoring a partial day
    against a full day's observation would quietly reward a model for the
    hours it was not asked about". A run whose two-day fetch failed holds only
    today, which at 18:00 is six hours; it returns [] so the caller records an
    absence rather than publishing six hours dressed as twenty-four.
    """
    window = forward_hours(hourly_multi_model, issued_local, hours_ahead=hours)
    if not window or not window.get("hourly"):
        return []

    times = window["hourly"].get("time") or []
    if len(times) < hours:
        return []

    return extract_day0_predictions_from_hourly(window, models, threshold=threshold)
