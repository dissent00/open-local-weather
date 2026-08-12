"""Deterministic prediction scoring and stateless rolling-window rescoring.

This is the credibility of the whole project: it is the ONLY place accuracy
numbers get computed, and it is pure Python with no I/O or LLM involvement,
mirroring the "all arithmetic in code, never the LLM" principle from the
original Apps Script pipeline (a deliberate fix after finding that asking
Gemini to compute rolling stats risked silent, hard-to-notice drift).

Ports scorePrediction() and rescoreRollingWindow() from
KisumuForecastPipeline_v2.gs field-for-field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from openlocalweather.dates import add_days, prediction_row_date_for_target
from openlocalweather.models import DailyActual, DailyLogEntry, ModelPrediction, VerificationScore

# `date -> DailyLogEntry | None`. Injected rather than reading files directly
# so this module is testable with an in-memory dict and has no filesystem
# dependency of its own — see store/log_store.make_log_lookup for the real
# implementation used in production.
LogLookup = Callable[[date], DailyLogEntry | None]


def score_prediction(
    predicted: ModelPrediction | None,
    actual: DailyActual | None,
    lead_time_days: int,
) -> VerificationScore | None:
    """Scores one model's stored prediction against one day's actual.

    Returns None if either side is missing (nothing to score). Individual
    error fields are None where that comparison isn't meaningful or the data
    isn't available — e.g. onset error only ever applies at lead_time_days
    == 0, since Day+3/Day+7 predictions never carry onset timing to begin
    with (daily-resolution aggregates only, by design, to control API cost).
    """
    if predicted is None or actual is None:
        return None
    # rain=None means the model had no data at this lead time (see
    # ModelPrediction.rain). There is nothing to score — counting it would
    # invent skill data out of a gap.
    if predicted.rain is None:
        return None

    rain_correct = predicted.rain == actual.rain

    onset_error_hrs = None
    if lead_time_days == 0 and actual.rain and predicted.onset and actual.onset_hour:
        onset_error_hrs = _hour_diff(predicted.onset, actual.onset_hour)

    def _diff(pred_val: float | None, actual_val: float | None) -> float | None:
        if pred_val is None or actual_val is None:
            return None
        return actual_val - pred_val

    return VerificationScore(
        rain_correct=rain_correct,
        onset_error_hrs=onset_error_hrs,
        wind_error_kmh=_diff(predicted.wind_kmh, actual.peak_wind_kmh),
        high_error_c=_diff(predicted.high_c, actual.high_c),
        low_error_c=_diff(predicted.low_c, actual.low_c),
        mslp_error_hpa=_diff(predicted.mslp_trend, actual.mslp_trend),
    )


def _hour_diff(predicted_hhmm: str, actual_hhmm: str) -> float:
    def to_minutes(hhmm: str) -> int:
        h, _, m = hhmm.partition(":")
        return int(h) * 60 + (int(m) if m else 0)

    return (to_minutes(actual_hhmm) - to_minutes(predicted_hhmm)) / 60


def predictions_by_model(entry: DailyLogEntry, lead_time_days: int) -> dict[str, ModelPrediction]:
    return {p.model: p for p in entry.model_predictions.for_lead(lead_time_days)}


def mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


@dataclass
class RollingWindowResult:
    checks_found: int
    rain_pct: float | None
    onset_err: float | None
    wind_err: float | None
    high_err: float | None
    low_err: float | None
    mslp_err: float | None


def rescore_rolling_window(
    model: str,
    lead_time_days: int,
    window_size: int,
    yesterday: date,
    log_lookup: LogLookup,
    actuals: dict[date, DailyActual],
) -> RollingWindowResult:
    """Re-derives rolling stats for one (model, lead time) by walking
    backward from `yesterday`, collecting up to `window_size` scoreable
    checks. Stateless by design — always recomputed from the raw stored
    predictions plus freshly fetched actuals, so there is no running total
    that can silently drift out of sync. Ports rescoreRollingWindow(),
    including its safety bound against gaps in the log.
    """
    scores: list[VerificationScore] = []
    cursor = yesterday
    days_searched = 0
    max_search = window_size + 30  # safety bound in case of gaps in the log

    while len(scores) < window_size and days_searched < max_search:
        target_date = cursor
        row_date = prediction_row_date_for_target(target_date, lead_time_days)
        entry = log_lookup(row_date)
        actual = actuals.get(target_date)
        if entry is not None and actual is not None:
            predictions = predictions_by_model(entry, lead_time_days)
            score = score_prediction(predictions.get(model), actual, lead_time_days)
            if score is not None:
                scores.append(score)
        cursor = add_days(cursor, -1)
        days_searched += 1

    return RollingWindowResult(
        checks_found=len(scores),
        rain_pct=(100 * sum(1 for s in scores if s.rain_correct) / len(scores)) if scores else None,
        onset_err=mean([s.onset_error_hrs for s in scores]),
        wind_err=mean([s.wind_error_kmh for s in scores]),
        high_err=mean([s.high_error_c for s in scores]),
        low_err=mean([s.low_error_c for s in scores]),
        mslp_err=mean([s.mslp_error_hpa for s in scores]),
    )


def compute_rain_pct_trend(
    rolling_10_rain_pct: float | None,
    rolling_30_rain_pct: float | None,
    checks_in_window_10: int,
    checks_in_window_30: int,
    min_checks_short: int,
    min_checks_long: int,
    threshold_pct: float,
) -> tuple[str | None, float | None]:
    """Deterministically compares the recent (rolling_10) rain-call rate
    against the longer-term (rolling_30) one, so the LLM is handed a
    ready-made "is recent skill diverging from the longer-term baseline"
    signal instead of having to notice that itself by comparing raw
    numbers. See defaults.py's TREND_* constants for the sample-size and
    threshold rationale.

    Returns (label, delta) where label is one of "improving" /
    "declining" / "stable", or (None, None) if either window doesn't yet
    have enough checks to make the comparison meaningful — "insufficient
    data" is itself information (this project never fabricates a trend
    off too little history), and the prompt is expected to say so rather
    than guess.
    """
    if (
        rolling_10_rain_pct is None
        or rolling_30_rain_pct is None
        or checks_in_window_10 < min_checks_short
        or checks_in_window_30 < min_checks_long
    ):
        return None, None

    delta = rolling_10_rain_pct - rolling_30_rain_pct
    if delta >= threshold_pct:
        label = "improving"
    elif delta <= -threshold_pct:
        label = "declining"
    else:
        label = "stable"
    return label, delta
