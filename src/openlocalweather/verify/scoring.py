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

import math

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from openlocalweather.dates import add_days, prediction_row_date_for_target
from openlocalweather.instability import CONVECTIVE_CAPE_THRESHOLD_JKG
from openlocalweather.verify.brier import brier_score, mean_brier
from openlocalweather.defaults import ISSUANCE_WINDOW_HOURS
from openlocalweather.fetch.metar import StationWeather, station_weather_within
from openlocalweather.fetch.open_meteo import bucket_hourly_window
from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    IssuancePredictions,
    ModelPrediction,
    ModelPredictionsByLead,
    VerificationScore,
)

# `date -> DailyLogEntry | None`. Injected rather than reading files directly
# so this module is testable with an in-memory dict and has no filesystem
# dependency of its own — see store/log_store.make_log_lookup for the real
# implementation used in production.
LogLookup = Callable[[date], DailyLogEntry | None]


def resolve_prediction_rows(entry: DailyLogEntry) -> list[IssuancePredictions]:
    """This entry's predictions, one row per issuance, oldest first.

    THE SINGLE PLACE THE FALLBACK LIVES, like `resolve_target_date` beside
    it. An entry written since ROADMAP item 104's contract item 4 carries
    `prediction_rows`; every one written before holds a single
    `model_predictions` set, which the day's FIRST issuance wrote and no
    later run touched — so `meta.generated_at_utc` is exactly the moment
    that made it. Both are read by one pass, so the committed record does
    not have to be rewritten to be readable.

    ROW 0 IS THE DAY'S FIRST ISSUANCE on both shapes, which is what keeps
    scoring unchanged across the migration: `predictions_by_model` takes
    row 0 and gets the same numbers it always did.

    Deletable in one edit, with `model_predictions` itself, once no
    unmarked entry remains in the window any verification pass reads.
    """
    if entry.prediction_rows:
        return entry.prediction_rows

    if entry.model_predictions is None:
        return []

    return [
        IssuancePredictions(
            issued_at=entry.meta.generated_at_utc,
            predictions=entry.model_predictions,
        )
    ]


def verify_closed_windows(
    entry: DailyLogEntry,
    archive_hourly: dict,
    *,
    today: date,
    station_reports: list | None = None,
    timezone_name: str | None = None,
    force: bool = False,
) -> bool:
    """Score every window on this entry whose hours are all in finished days.

    ROADMAP item 104, contract item 2. Returns whether anything changed, so
    the caller writes the entry only when it did.

    PER ROW AND BY THE CALENDAR, which is what makes this different from the
    daily verification beside it. That pass asks "has yesterday been
    observed"; this one asks, of each issuance separately, whether the 24
    hours IT claimed have finished — and a 22:00 issuance and a 06:00 one on
    the same day get different answers. `window_is_scorable` carries why the
    floor is the calendar rather than the 24-hour clock.

    IDEMPOTENT BY STAMP, not by recomputation. A row already carrying
    `window_verified_at` is left exactly as it is: the archive is stable for
    finished days so a rescore would agree, but row 0's stored numbers are
    what the record rests on and nothing here should be able to move them.
    `rebuild-record` is where a deliberate re-derivation belongs.
    """
    changed = False

    for row in entry.prediction_rows:
        if row.window_verified_at is not None and not force:
            continue
        if not row.window_predictions or row.window_opened_local is None:
            continue
        if not window_is_scorable(row.window_opened_local, today=today):
            continue

        observed = bucket_hourly_window(archive_hourly, start=row.window_opened_local)
        if observed is not None and station_reports is not None and timezone_name is not None:
            # THE STATION, OVER THE WINDOW'S OWN HOURS — ROADMAP item 139.
            # The calendar path stamps the airport onto its day before scoring
            # (`_apply_station_observations`); without the same here the two
            # bases scored the same forecast against different evidence, and
            # the first scored window called every model's rain wrong on a day
            # the station had seen rain and the reanalysis held 0.4 mm.
            _stamp_station(observed, station_weather_within(
                station_reports, row.window_opened_local, ISSUANCE_WINDOW_HOURS, timezone_name
            ))
        scores = score_window_row(row, observed)
        if observed is None:
            # The archive could not cover a window whose days HAVE finished —
            # a hole rather than a wait. Left unstamped so a later run tries
            # again, because the alternative is a permanent silent gap.
            continue

        row.window_scores = scores
        row.window_verified_at = datetime.now(timezone.utc)
        changed = True

    return changed


def window_is_scorable(issued_local: datetime, *, today: date, hours: int = ISSUANCE_WINDOW_HOURS) -> bool:
    """Whether every hour this issuance's window covers lies on a day that has
    finished — ROADMAP item 104, contract item 2.

    NOT "HAS THE WINDOW CLOSED", WHICH WOULD BE 24 HOURS. The observation
    comes from `archive-api.open-meteo.com`, and that endpoint SERVES THE
    CURRENT DAY WITH MODEL OUTPUT. Measured 2026-09-14: asked for
    2026-09-13..14 at 08:31 local it returned fifteen stamps in the future
    carrying temperatures, the last of them 23:00 that evening. The project
    already knew this about ERA5's same-day precipitation — item 121's C9
    withholds the dimension for exactly this reason — and it is true of the
    whole series, not only of rain.

    So an observation that reaches into today is partly a forecast, and
    scoring a window against it scores a forecast against a forecast. The
    honest floor is the calendar: a window may be scored once the last day it
    touches has ended. For a morning issuance that is roughly 48 hours after
    it was made, not 24, and the difference is not a delay to optimise away —
    it is the difference between an observation and a model run.

    The existing daily verification never met this because it only ever
    buckets a completed yesterday. The window is the first thing here that can
    straddle midnight.
    """
    opened = issued_local.replace(minute=0, second=0, microsecond=0)
    # The window is half-open, so the last hour it covers is one hour before
    # it closes — a window closing exactly at midnight touches only one day.
    last_hour = opened + timedelta(hours=hours - 1)
    return last_hour.date() < today


def _stamp_station(observed: DailyActual, seen: StationWeather | None) -> None:
    """Mirror of the calendar overlay, for a window. None leaves every flag
    as the archive bucket set it — "not observed", never "nothing happened"."""
    if seen is None:
        return
    observed.thunder = seen.thunder
    observed.precipitation = seen.precipitation
    observed.precipitation_onset = seen.precipitation_onset


def score_window_row(
    row: IssuancePredictions, observed: DailyActual | None
) -> dict[str, VerificationScore]:
    """One issuance's window claim, scored per model against the weather that
    actually fell inside it.

    ROADMAP item 104, contract item 2. `observed` comes from
    `open_meteo.bucket_hourly_window` over the SAME hours the claim covered —
    that agreement is pinned across the two modules, because a score whose
    sides cover different hours is wrong in a way nothing else would report.

    SCORED AT LEAD 0, AND BY THE EXISTING SCORER. The window is a lead-0
    shaped claim: it carries an onset and a rain call about a period that has
    now finished, which is exactly what `score_prediction` handles at lead 0
    and nowhere else. A second scorer would be a second place for the rain
    threshold, the onset convention and the error signs to drift.

    EMPTY IS NOT ZERO. No observation means the archive could not cover the
    window; no window predictions means the run declined to make a 24-hour
    claim at all. Both return {} rather than a set of null-filled scores,
    because a row carrying scores asserts that a comparison happened.
    """
    if observed is None or not row.window_predictions:
        return {}

    scores: dict[str, VerificationScore] = {}
    for predicted in row.window_predictions:
        score = score_prediction(predicted, observed, 0)
        if score is not None:
            scores[predicted.model] = score

    return scores


def scored_predictions(entry: DailyLogEntry) -> ModelPredictionsByLead:
    """The set tomorrow scores: the day's FIRST issuance.

    Named rather than inlined because "which issuance is scored" is the
    question item 104 spent a contract settling, and it should be answerable
    by grepping one identifier. Contract item 4 makes every issuance a row;
    it does not yet make every row scored, and the switch belongs with C2/C3
    where the series is re-derived.
    """
    rows = resolve_prediction_rows(entry)
    return rows[0].predictions if rows else ModelPredictionsByLead()


def resolve_target_date(
    prediction: ModelPrediction, *, row_date: date, lead_time_days: int
) -> date:
    """What date this prediction is about — ROADMAP item 104, C1.

    THE SINGLE PLACE THE FALLBACK LIVES. A prediction written since
    2026-09-12 says what it targets; every one written before is on a row
    whose date plus its lead time IS the target, because a day held exactly
    one issuance. Both are read by one pass so the committed record does not
    have to be rewritten to be readable.

    An explicit target WINS and is not required to agree with the arithmetic.
    That is the point: once a day can hold several issuances, the row date
    stops determining the target, and a prediction that disagrees with
    `row_date + lead` is the normal case rather than a corruption.

    Deletable in one edit once no unmarked rows remain in the window any
    verification pass reads.
    """
    if prediction.target_date is not None:
        return prediction.target_date

    return add_days(row_date, lead_time_days)


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

    # Scored against observed CONVECTION, not against reanalysis rain alone.
    # A thunderstorm the airport watched pass overhead counts even when the
    # grid cell recorded half a millimetre — see DailyActual.observed_convection.
    observed_rain = actual.observed_convection()
    rain_correct = predicted.rain == observed_rain

    # THE INSTABILITY CALL, scored against the storm rather than the rain —
    # see VerificationScore.convective_correct. Day+0 only, because CAPE is
    # hourly and the extended leads come from the daily endpoint; None on
    # either side is a call nobody made or a day nobody observed, and neither
    # is a miss.
    convective_correct = None
    if (
        lead_time_days == 0
        and predicted.peak_cape_jkg is not None
        and actual.thunder is not None
    ):
        convective_correct = (
            predicted.peak_cape_jkg >= CONVECTIVE_CAPE_THRESHOLD_JKG
        ) == actual.thunder

    onset_error_hrs = None
    if lead_time_days == 0 and observed_rain and predicted.onset and actual.onset_hour:
        onset_error_hrs = _hour_diff(predicted.onset, actual.onset_hour)

    def _diff(pred_val: float | None, actual_val: float | None) -> float | None:
        if pred_val is None or actual_val is None:
            return None
        return actual_val - pred_val

    # The percentage-to-probability conversion happens HERE and only here, so
    # it is one visible line rather than a thing every caller must remember.
    # brier_score() raises on a value outside [0, 1] precisely to catch a
    # missed conversion, which would otherwise score 6241 and pass silently
    # into every mean it touched.
    rain_brier = (
        brier_score(predicted.rain_probability_pct / 100, observed_rain)
        if predicted.rain_probability_pct is not None
        else None
    )

    return VerificationScore(
        rain_correct=rain_correct,
        rain_brier=rain_brier,
        onset_error_hrs=onset_error_hrs,
        wind_error_kmh=_diff(predicted.wind_kmh, actual.peak_wind_kmh),
        high_error_c=_diff(predicted.high_c, actual.high_c),
        low_error_c=_diff(predicted.low_c, actual.low_c),
        mslp_error_hpa=_diff(predicted.mslp_trend, actual.mslp_trend),
        # THE REANALYSIS DAY MEAN ON BOTH SIDES, like for like — the station's
        # eighths sit beside it as a cross-check and are deliberately not the
        # basis, exactly as the station's sustained wind sits beside the
        # scored gust. A day mean is a blunt read of a sky that burns off by
        # mid-morning, and it is still enough to separate a model that saw no
        # cloud at all from one that did.
        cloud_error_pct=_diff(predicted.cloud_cover_pct, actual.cloud_cover_pct),
        convective_correct=convective_correct,
        # Item 157. Every lead: the daily endpoint carries a total. Against
        # the reanalysis alone — see VerificationScore.precip_error_mm.
        precip_error_mm=_diff(predicted.precip_mm, actual.precip_mm),
    )


def _hour_diff(predicted_hhmm: str, actual_hhmm: str) -> float:
    def to_minutes(hhmm: str) -> int:
        h, _, m = hhmm.partition(":")
        return int(h) * 60 + (int(m) if m else 0)

    return (to_minutes(actual_hhmm) - to_minutes(predicted_hhmm)) / 60


def predictions_by_model(entry: DailyLogEntry, lead_time_days: int) -> dict[str, ModelPrediction]:
    # Keyed by model, so it must be handed ONE issuance's predictions. Given
    # two issuances' rows flattened together it would silently keep whichever
    # came last and score the wrong call, with nothing to notice — which is
    # why contract item 4's rows are rows rather than a stamped flat list.
    return {p.model: p for p in scored_predictions(entry).for_lead(lead_time_days)}


def mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def sample_sd(values: list[float | None]) -> float | None:
    """The n-1 spread of the present values, or None below two of them.

    ROADMAP item 153: the review's "is it real" gate compares a mean with its
    own standard error, and this is the spread that error is built from.
    WRITTEN AS PLAIN LOOPS ON PURPOSE, not `statistics.stdev`: the Dart port
    does the same additions in the same order, so the two agree to the bit
    rather than to a tolerance, and a gate that compares against the result
    cannot flip on one side only. Swept against the port before shipping.
    """
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return None

    total = 0.0
    for v in present:
        total += v
    centre = total / len(present)

    squares = 0.0
    for v in present:
        squares += (v - centre) * (v - centre)

    # math.sqrt, not `** 0.5`: the power goes through pow() and is not
    # correctly rounded — swept 2026-09-17, 5 of 3077 values differed from
    # the port in the last bit until this was sqrt on both sides.
    return math.sqrt(squares / (len(present) - 1))


@dataclass
class RollingWindowResult:
    checks_found: int
    rain_pct: float | None
    onset_err: float | None
    wind_err: float | None
    high_err: float | None
    low_err: float | None
    mslp_err: float | None
    # Mean Brier over the checks in this window that carried a probability —
    # ROADMAP item 58. LOWER IS BETTER. None until enough days have one; see
    # brier.mean_brier for why absent days are skipped rather than defaulted.
    #
    # brier_checks is reported SEPARATELY from checks_found because the two
    # genuinely differ and will for weeks: a window can hold 30 scored days
    # of which 3 have a probability, and presenting one count for both would
    # imply the Brier figure rests on evidence it does not have.
    #
    # Last in the dataclass because they carry defaults, not because they
    # matter least.
    rain_brier: float | None = None
    brier_checks: int = 0
    # The sky, from 2026-09-10. cloud_checks is separate from checks_found
    # for the same reason brier_checks is, and more sharply: cloud_cover_pct
    # started being stored on 2026-09-09, so for weeks a window will hold
    # thirty scored days and a handful with cloud. One count for both would
    # present the figure as resting on evidence it does not have.
    cloud_err: float | None = None
    cloud_checks: int = 0
    # Item 157. Signed mean of the amount error over the window's checks.
    precip_err: float | None = None


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

    briers = [s.rain_brier for s in scores if s.rain_brier is not None]
    return RollingWindowResult(
        checks_found=len(scores),
        rain_pct=(100 * sum(1 for s in scores if s.rain_correct) / len(scores)) if scores else None,
        rain_brier=mean_brier([s.rain_brier for s in scores]),
        brier_checks=len(briers),
        onset_err=mean([s.onset_error_hrs for s in scores]),
        wind_err=mean([s.wind_error_kmh for s in scores]),
        high_err=mean([s.high_error_c for s in scores]),
        low_err=mean([s.low_error_c for s in scores]),
        mslp_err=mean([s.mslp_error_hpa for s in scores]),
        cloud_err=mean([s.cloud_error_pct for s in scores]),
        cloud_checks=sum(1 for s in scores if s.cloud_error_pct is not None),
        precip_err=mean([s.precip_error_mm for s in scores]),
    )



def collect_scores(
    model: str,
    lead_time_days: int,
    yesterday: date,
    earliest_target_date: date,
    log_lookup: LogLookup,
    actuals: dict[date, DailyActual],
) -> list[tuple[date, VerificationScore]]:
    """Every scoreable check for one (model, lead time), newest first.

    The shared walk behind both rescore_all_time (which only needs counts)
    and the weekly review (which needs the individual scores, to compute
    bias and to see how skill moved over time rather than just its
    average).
    """
    scored: list[tuple[date, VerificationScore]] = []
    cursor = yesterday
    while cursor >= earliest_target_date:
        entry = log_lookup(prediction_row_date_for_target(cursor, lead_time_days))
        actual = actuals.get(cursor)
        if entry is not None and actual is not None:
            score = score_prediction(
                predictions_by_model(entry, lead_time_days).get(model), actual, lead_time_days
            )
            if score is not None:
                scored.append((cursor, score))
        cursor = add_days(cursor, -1)
    return scored


@dataclass
class AllTimeResult:
    checks: int
    correct: int
    pct: float | None
    earliest_target_date: date | None
    latest_target_date: date | None


def rescore_all_time(
    model: str,
    lead_time_days: int,
    yesterday: date,
    earliest_target_date: date,
    log_lookup: LogLookup,
    actuals: dict[date, DailyActual],
) -> AllTimeResult:
    """Re-derives all-time checks/correct for one (model, lead time) by
    walking the ENTIRE stored record, rather than carrying a running total.

    Why this replaced an incremental counter. Open-Meteo revises recent
    observations: a day fetched at 06:07 as "rain, 29.6C high" was served
    hours later as "no rain, 30.5C high" — confirmed live, not theorised.
    An incremental counter bakes the provisional verdict in permanently,
    making all-time the one number in this project that cannot be
    recomputed from the record and therefore has to be trusted rather than
    verified. That cuts directly against the git-as-auditable-database
    premise.

    Re-derivation also makes an entire bug class impossible rather than
    guarded against: `last_verified_target_date` existed solely to stop the
    old counter double-counting when a day ran twice.

    The caller MUST pass an `earliest_target_date` covering the whole
    record and ensure `actuals` spans it — see pipeline.py, where actuals
    retention is tied to the log history for exactly this reason. Deriving
    against a short actuals window would silently shrink all-time rather
    than fail.
    """
    scored = collect_scores(model, lead_time_days, yesterday, earliest_target_date, log_lookup, actuals)
    checks = len(scored)
    correct = sum(1 for _, s in scored if s.rain_correct)
    # collect_scores walks backward, so the first entry is the most recent.
    latest_scored = scored[0][0] if scored else None
    earliest_scored = scored[-1][0] if scored else None

    return AllTimeResult(
        checks=checks,
        correct=correct,
        pct=(100 * correct / checks) if checks else None,
        earliest_target_date=earliest_scored,
        latest_target_date=latest_scored,
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
