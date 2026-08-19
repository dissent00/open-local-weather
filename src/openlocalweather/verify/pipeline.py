"""The core of the accuracy loop: run_deterministic_verification_and_scoring().

For each tracked lead time k:
  - Verify yesterday's k-lead prediction (the row dated yesterday-k) against
    yesterday's actual. The result is returned for the caller to write back
    (see `newly_verified`) — this module has no file I/O of its own.
  - Recompute rolling 10/30-check stats per model at that lead time,
    statelessly (see verify.scoring.rescore_rolling_window).
  - Re-derive all-time checks/correct by walking the WHOLE stored record.
    Formerly an incremental counter — the one number here that could not be
    recomputed from the record. Open-Meteo revises recent observations, so
    incrementing baked provisional verdicts in permanently; deriving
    self-heals exactly as the rolling windows do.

Ports runDeterministicVerificationAndScoring() from
KisumuForecastPipeline_v2.gs. Note names differ slightly for Python
readability but the logic and field semantics are a direct port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from datetime import date, datetime, timezone

from openlocalweather.dates import prediction_row_date_for_target
from openlocalweather.defaults import (
    LEAD_TIMES_DAYS,
    MODELS,
    ROLLING_WINDOW_LONG,
    ROLLING_WINDOW_SHORT,
    TREND_MIN_CHECKS_LONG,
    TREND_MIN_CHECKS_SHORT,
    TREND_THRESHOLD_PCT,
)
from openlocalweather.models import DailyActual, TrackRecord, TrackRecordEntry, VerificationScore
from openlocalweather.verify.scoring import (
    LogLookup,
    compute_rain_pct_trend,
    predictions_by_model,
    rescore_all_time,
    rescore_rolling_window,
    score_prediction,
)


@dataclass
class LeadTimeResult:
    lead_time_days: int
    # The date whose k-lead prediction was scored today, or None if no log
    # row/actual was available to score against (a gap in the log, or a
    # cold-start day with no history yet).
    target_date_verified: date | None
    per_model_scores: dict[str, VerificationScore] = field(default_factory=dict)


@dataclass
class VerificationRunResult:
    lead_time_results: list[LeadTimeResult]
    updated_track_record: TrackRecord
    # (row_date, lead_time_days) pairs whose log entry should have
    # verification.dayK.verified set True — applied by the caller via
    # store/log_store, since this module only reads logs, never writes them.
    newly_verified: list[tuple[date, int]] = field(default_factory=list)


def run_deterministic_verification_and_scoring(
    log_lookup: LogLookup,
    prior_track_record: TrackRecord,
    actuals_primary: dict[date, DailyActual],
    today: date,
    yesterday: date,
    models: list[str] = MODELS,
    lead_times_days: list[int] = LEAD_TIMES_DAYS,
    window_short: int = ROLLING_WINDOW_SHORT,
    window_long: int = ROLLING_WINDOW_LONG,
    earliest_log_date: date | None = None,
) -> VerificationRunResult:
    entries_by_key: dict[tuple[str, int], TrackRecordEntry] = {
        (e.model, e.lead_time_days): e.model_copy(deep=True) for e in prior_track_record.entries
    }

    # How far back the all-time walk reaches. Defaults to the oldest actual
    # we hold when the caller doesn't say, which is correct but only as
    # complete as the actuals cache — pipeline.py ties that retention to the
    # log history precisely so this covers everything.
    all_time_start = earliest_log_date or (min(actuals_primary) if actuals_primary else yesterday)

    yesterday_actual = actuals_primary.get(yesterday)
    lead_time_results: list[LeadTimeResult] = []
    newly_verified: list[tuple[date, int]] = []

    for k in lead_times_days:
        target_row_date = prediction_row_date_for_target(yesterday, k)
        entry = log_lookup(target_row_date)

        per_model_scores: dict[str, VerificationScore] = {}
        if entry is not None and yesterday_actual is not None:
            predictions = predictions_by_model(entry, k)
            for model in models:
                score = score_prediction(predictions.get(model), yesterday_actual, k)
                if score is not None:
                    per_model_scores[model] = score

        if per_model_scores:
            newly_verified.append((target_row_date, k))

        lead_time_results.append(
            LeadTimeResult(
                lead_time_days=k,
                target_date_verified=target_row_date if per_model_scores else None,
                per_model_scores=per_model_scores,
            )
        )

        for model in models:
            short = rescore_rolling_window(model, k, window_short, yesterday, log_lookup, actuals_primary)
            long = rescore_rolling_window(model, k, window_long, yesterday, log_lookup, actuals_primary)

            key = (model, k)
            track_entry = entries_by_key.get(key)
            if track_entry is None:
                track_entry = TrackRecordEntry(model=model, lead_time_days=k)

            # All-time is RE-DERIVED from the whole record, not carried
            # forward. Open-Meteo revises recent observations (confirmed
            # live: a day served as "rain, 29.6C" at 06:07 came back as "no
            # rain, 30.5C" hours later), and an incremental counter bakes
            # the provisional verdict in permanently. Re-derivation
            # self-heals exactly as the rolling windows already do, and
            # makes double-counting structurally impossible rather than
            # something a guard has to catch.
            new_check = per_model_scores.get(model)
            all_time = rescore_all_time(
                model, k, yesterday, all_time_start, log_lookup, actuals_primary
            )
            new_all_time_checks = all_time.checks
            new_all_time_correct = all_time.correct
            all_time_pct = all_time.pct

            # Safety rail. A derivation covering FEWER checks than last time
            # means actuals are missing for dates we hold predictions for —
            # a data problem to surface, never a smaller number to quietly
            # publish. Keep the previous figures and say so loudly.
            if new_all_time_checks < track_entry.all_time_checks:
                print(
                    f"WARNING: all-time re-derivation for {model} lead+{k} found "
                    f"{new_all_time_checks} checks but {track_entry.all_time_checks} were "
                    "recorded previously — actuals are probably missing for dates that "
                    "have predictions. Keeping the previous figures; investigate the "
                    "actuals cache rather than trusting the smaller number.",
                    file=sys.stderr,
                )
                new_all_time_checks = track_entry.all_time_checks
                new_all_time_correct = track_entry.all_time_correct
                all_time_pct = track_entry.all_time_rain_pct
            else:
                track_entry.all_time_earliest_target_date = all_time.earliest_target_date

            track_entry.rolling_10_rain_pct = short.rain_pct
            track_entry.rolling_30_rain_pct = long.rain_pct
            track_entry.rain_pct_trend, track_entry.rain_pct_trend_delta = compute_rain_pct_trend(
                rolling_10_rain_pct=short.rain_pct,
                rolling_30_rain_pct=long.rain_pct,
                checks_in_window_10=short.checks_found,
                checks_in_window_30=long.checks_found,
                min_checks_short=TREND_MIN_CHECKS_SHORT,
                min_checks_long=TREND_MIN_CHECKS_LONG,
                threshold_pct=TREND_THRESHOLD_PCT,
            )
            track_entry.all_time_checks = new_all_time_checks
            track_entry.all_time_correct = new_all_time_correct
            track_entry.all_time_rain_pct = all_time_pct
            # Onset error only meaningful at Day+0 — no onset data at +3/+7.
            track_entry.avg_onset_error_hrs_10 = short.onset_err if k == 0 else None
            track_entry.avg_wind_error_kmh_10 = short.wind_err
            track_entry.avg_temp_high_error_c_10 = short.high_err
            track_entry.avg_temp_low_error_c_10 = short.low_err
            track_entry.avg_mslp_trend_error_hpa_10 = short.mslp_err
            track_entry.checks_in_window_10 = short.checks_found
            track_entry.last_updated = today
            # No longer load-bearing for correctness (re-derivation cannot
            # double-count), but still a useful record of when this pair
            # last produced a fresh check.
            if new_check is not None:
                track_entry.last_verified_target_date = yesterday
            # skill_profile_summary / notes are intentionally left untouched
            # here — they're LLM-written qualitative text, filled in on a
            # later pass once the LLM has this run's numbers as context (see
            # pipeline.py). This function only ever writes numeric fields.

            entries_by_key[key] = track_entry

    model_order = {m: i for i, m in enumerate(models)}
    ordered_entries = sorted(
        entries_by_key.values(),
        key=lambda e: (model_order.get(e.model, len(models)), e.lead_time_days),
    )

    updated_track_record = TrackRecord(
        generated_at_utc=datetime.now(timezone.utc),
        entries=ordered_entries,
    )

    return VerificationRunResult(
        lead_time_results=lead_time_results,
        updated_track_record=updated_track_record,
        newly_verified=newly_verified,
    )
