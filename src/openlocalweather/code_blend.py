"""A forecast from the record alone — ROADMAP item 173, stage 1.

WHAT IT ANSWERS. The LLM's own call (`olw_blend`) is scored beside every
model, and on 2026-09-24 it trailed ICON and ECMWF on the headline rain call
(item 182, "Is the LLM adding value?"). The learning this project does lives
in the RECORD — the code that scores every model at every lead. This is that
record turned into a forecast by arithmetic, so the LLM's call has something
fair to be measured against: the same inputs, the same scores, no reasoning.

THE SAME INPUTS THE LLM SEES, minus `best_match`. Its rain probability is
ECMWF's (defaults.KNOWN_DUPLICATES) and it is itself a blend, so a vote that
counted it would count ECMWF twice.

THE SAME NUMBERS THE LLM SEES. Rain votes are weighted by the rolling-30 hit
rate and temperatures are corrected by the rolling-10 signed error — the
figures MODEL TRACK RECORD hands the forecaster. Chosen with the operator on
2026-09-26 on principle. A scratch run had tried three weighting schemes on
29 days first; picking the one that scored best there would have fitted
noise, so none was picked on its score.

NEVER THE DAY IT FORECASTS. `windows_as_of` reads only targets strictly
before the issuance, for the reason baselines.py gives: a blend that could
peek would not look broken, it would make the LLM look worse than it is.

IN THE RECORD FROM STAGE 2 (2026-09-26): every issuance stores it beside the
LLM's own call, and `backfill-code-blend` put it into row 0 of the days
before. Scored and published like any model, and withheld from the
forecaster — defaults.models_visible_to_the_forecaster says why.

A BACKFILLED DAY IS WHAT TODAY'S RECORD SAYS, not what that morning's did.
The stored record has been re-derived since some of those mornings: item 97
(84f0a09) flipped stored Day+0 rain calls on 09-09, and on 09-04..09-23, 91
of 300 rolling-30 figures rebuilt here differ from what the forecaster read,
mostly by one check. From 09-24 they match. See ROADMAP item 173.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from openlocalweather.defaults import (
    BEST_MATCH_MODEL_ID,
    CODE_BLEND_MODEL_ID,
    LEAD_TIMES_DAYS,
    ROLLING_WINDOW_LONG,
    ROLLING_WINDOW_SHORT,
    TREND_MIN_CHECKS_LONG,
    models_visible_to_the_forecaster,
)
from openlocalweather.dates import add_days
from openlocalweather.models import DailyActual, ModelPrediction, ModelPredictionsByLead
from openlocalweather.verify.scoring import (
    LogLookup,
    RollingWindowResult,
    mean,
    rescore_rolling_window,
)

# How many checks a rolling-30 hit rate needs before it carries a vote. THE
# EXISTING TEN, not a second threshold: TREND_MIN_CHECKS_LONG is where this
# project already decided a 30-check window short on data is not to be trusted.
RAIN_WEIGHT_MIN_CHECKS = TREND_MIN_CHECKS_LONG

# A yes/no call right half the time is a coin flip and gets no vote.
COIN_FLIP_PCT = 50.0

# The full window, for calibration.GUST_CALIBRATION_MIN_CHECKS's reason: fewer
# would apply a mean from a window that is not full.
TEMPERATURE_CORRECTION_MIN_CHECKS = ROLLING_WINDOW_SHORT


def blend_inputs(local_bulletin_model_id: str = "") -> list[str]:
    """The models whose record and predictions the blend reads."""
    return [
        m
        for m in models_visible_to_the_forecaster(local_bulletin_model_id)
        if m != BEST_MATCH_MODEL_ID
    ]


def windows_as_of(
    models: list[str],
    lead_time_days: int,
    window_size: int,
    issued: date,
    log_lookup: LogLookup,
    actuals: Mapping[date, DailyActual],
) -> dict[str, RollingWindowResult]:
    """Each model's rolling window as the record stood when `issued` ran.

    Walks back from the day BEFORE the issuance, so the day being forecast
    and anything after it cannot enter. Every row a walk reads is dated on or
    before its target, so later entries cannot enter either.
    """
    yesterday = add_days(issued, -1)

    return {
        m: rescore_rolling_window(m, lead_time_days, window_size, yesterday, log_lookup, dict(actuals))
        for m in models
    }


def rain_weights(
    windows: Mapping[str, RollingWindowResult],
    *,
    min_checks: int = RAIN_WEIGHT_MIN_CHECKS,
) -> dict[str, float]:
    """Each model's vote: its hit rate above a coin flip, in points.

    A model with too few checks, or no better than a coin flip, is absent
    rather than zero-weighted, so "who voted" can be read off the keys.
    """
    weights: dict[str, float] = {}

    for model, window in windows.items():
        if window.rain_pct is None or window.checks_found < min_checks:
            continue

        weight = window.rain_pct - COIN_FLIP_PCT
        if weight <= 0:
            continue

        weights[model] = weight

    return weights


def temperature_corrections(
    windows: Mapping[str, RollingWindowResult],
    *,
    min_checks: int = TEMPERATURE_CORRECTION_MIN_CHECKS,
) -> tuple[dict[str, float], dict[str, float]]:
    """Per-model °C to ADD to a high and to a low, keyed by model id.

    ADD: the errors are `actual - predicted`, so a positive one is a model
    that came in cold — calibration.gust_corrections states the same sign for
    the gust. A model nothing has measured is absent, never zero.
    """
    highs: dict[str, float] = {}
    lows: dict[str, float] = {}

    for model, window in windows.items():
        if window.checks_found < min_checks:
            continue

        if window.high_err is not None:
            highs[model] = window.high_err

        if window.low_err is not None:
            lows[model] = window.low_err

    return highs, lows


def code_blend_prediction(
    predictions: list[ModelPrediction],
    weights: Mapping[str, float],
    high_corrections: Mapping[str, float] | None = None,
    low_corrections: Mapping[str, float] | None = None,
) -> ModelPrediction | None:
    """The record-weighted call, or None when no model carries a vote.

    Only models with a weight vote and only corrected models enter a
    temperature, so a prediction outside the inputs — the LLM's own row among
    them — cannot move the result.

    THE PROBABILITY IS THE WEIGHTED SHARE OF WET VOTES. It exists at every
    lead for every model, which served probabilities do not (UKMO and the met
    service have none), and it keeps the boolean and the number consistent:
    rain exactly when the share is over half. A tie breaks dry, for the
    reason climatology_prediction gives.
    """
    voters = [p for p in predictions if p.model in weights and p.rain is not None]
    total = sum(weights[p.model] for p in voters)
    if total <= 0:
        return None

    wet = sum(weights[p.model] for p in voters if p.rain)

    return ModelPrediction(
        model=CODE_BLEND_MODEL_ID,
        # Compared as wet * 2 > total rather than wet / total > 0.5, so the tie
        # is decided by the arithmetic climatology uses and not by a division.
        rain=wet * 2 > total,
        rain_probability_pct=round(100 * wet / total),
        high_c=_corrected_mean(predictions, high_corrections, "high_c"),
        low_c=_corrected_mean(predictions, low_corrections, "low_c"),
        # No view on these. A field the blend did not compute must not enter
        # the record as a value it never gave.
        onset=None,
        precip_mm=None,
        wind_kmh=None,
        mslp_trend=None,
    )


def _corrected_mean(
    predictions: list[ModelPrediction],
    corrections: Mapping[str, float] | None,
    field: str,
) -> float | None:
    """The mean of the corrected models only — calibrated_gust_consensus's
    rule: a mean mixing corrected and uncorrected members is neither."""
    if not corrections:
        return None

    return mean(
        [
            getattr(p, field) + corrections[p.model]
            for p in predictions
            if getattr(p, field) is not None and p.model in corrections
        ]
    )


def code_blend_predictions(
    predictions: ModelPredictionsByLead,
    issued: date,
    log_lookup: LogLookup,
    actuals: Mapping[date, DailyActual],
    inputs: list[str],
) -> ModelPredictionsByLead:
    """The code blend for one issuance, at every lead it can call.

    THE ONE PLACE the pieces are put together, so the live run, the backfill
    and the backtest cannot build it three ways. A lead where it declines is
    an empty list, never a guessed row. Temperatures at Day+0 only: that is
    where the LLM's call commits to them.
    """
    blends: dict[int, list[ModelPrediction]] = {}

    for lead in LEAD_TIMES_DAYS:
        long = windows_as_of(inputs, lead, ROLLING_WINDOW_LONG, issued, log_lookup, actuals)
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}
        if lead == 0:
            short = windows_as_of(inputs, 0, ROLLING_WINDOW_SHORT, issued, log_lookup, actuals)
            highs, lows = temperature_corrections(short)

        blend = code_blend_prediction(predictions.for_lead(lead), rain_weights(long), highs, lows)
        blends[lead] = [blend] if blend is not None else []

    return ModelPredictionsByLead(day0=blends[0], day3=blends[3], day7=blends[7])
