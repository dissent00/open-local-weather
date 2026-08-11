from datetime import date, datetime, timedelta, timezone

import pytest

from openlocalweather.models import DailyActual, DailyLogEntry, LogEntryMeta, ModelPrediction, ModelPredictionsByLead, TrackRecord, TrackRecordEntry
from openlocalweather.verify.pipeline import run_deterministic_verification_and_scoring


def prediction(**overrides) -> ModelPrediction:
    defaults = dict(model="gfs_seamless", rain=True, onset="14:00", wind_kmh=20.0, high_c=26.0, low_c=18.0, mslp_trend=-1.0)
    defaults.update(overrides)
    return ModelPrediction(**defaults)


def actual(**overrides) -> DailyActual:
    defaults = dict(rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0, onset_hour="14:00")
    defaults.update(overrides)
    return DailyActual(**defaults)


def log_entry(d: date, day0=None, day3=None, day7=None) -> DailyLogEntry:
    return DailyLogEntry(
        date=d,
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26/18",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="narrative",
        model_predictions=ModelPredictionsByLead(day0=day0 or [], day3=day3 or [], day7=day7 or []),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc), llm_provider="test", llm_model="test", pipeline_version="0"
        ),
    )


MODELS = ["gfs_seamless", "ecmwf_ifs025"]
LEAD_TIMES = [0, 3]


def empty_track_record() -> TrackRecord:
    return TrackRecord(
        generated_at_utc=datetime.now(timezone.utc),
        entries=[TrackRecordEntry(model=m, lead_time_days=k) for m in MODELS for k in LEAD_TIMES],
    )


def test_marks_newly_verified_when_target_row_and_actual_both_exist():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}
    actuals = {yesterday: actual(rain=True)}

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=empty_track_record(),
        actuals_primary=actuals,
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    assert (yesterday, 0) in result.newly_verified
    day0_result = next(r for r in result.lead_time_results if r.lead_time_days == 0)
    assert day0_result.target_date_verified == yesterday
    assert day0_result.per_model_scores["gfs_seamless"].rain_correct is True


def test_no_verification_when_no_actual_for_yesterday():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless")])}

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=empty_track_record(),
        actuals_primary={},  # no actuals at all
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    assert result.newly_verified == []
    for r in result.lead_time_results:
        assert r.per_model_scores == {}
        assert r.target_date_verified is None


def test_no_verification_when_no_row_at_target_date():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: None,  # empty log entirely
        prior_track_record=empty_track_record(),
        actuals_primary={yesterday: actual(rain=True)},
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    assert result.newly_verified == []


def test_all_time_checks_increment_by_at_most_one_per_run():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}
    actuals = {yesterday: actual(rain=True)}

    prior = empty_track_record()
    for e in prior.entries:
        if e.model == "gfs_seamless" and e.lead_time_days == 0:
            e.all_time_checks = 41
            e.all_time_correct = 30

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=prior,
        actuals_primary=actuals,
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    entry = result.updated_track_record.get("gfs_seamless", 0)
    assert entry.all_time_checks == 42  # +1, not recomputed from scratch
    assert entry.all_time_correct == 31
    assert entry.all_time_rain_pct == pytest.approx(100 * 31 / 42)


def test_all_time_checks_unchanged_when_nothing_new_scored():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)

    prior = empty_track_record()
    for e in prior.entries:
        if e.model == "gfs_seamless" and e.lead_time_days == 0:
            e.all_time_checks = 41
            e.all_time_correct = 30

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: None,
        prior_track_record=prior,
        actuals_primary={},
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    entry = result.updated_track_record.get("gfs_seamless", 0)
    assert entry.all_time_checks == 41
    assert entry.all_time_correct == 30


def test_skill_profile_summary_and_notes_preserved_from_prior_run():
    # This function only ever writes numeric fields — the qualitative text
    # is LLM-written on a later pass and must survive untouched here.
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)

    prior = empty_track_record()
    for e in prior.entries:
        if e.model == "gfs_seamless" and e.lead_time_days == 0:
            e.skill_profile_summary = "Strong on precip timing."
            e.notes = "some note"

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: None,
        prior_track_record=prior,
        actuals_primary={},
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    entry = result.updated_track_record.get("gfs_seamless", 0)
    assert entry.skill_profile_summary == "Strong on precip timing."
    assert entry.notes == "some note"


def test_onset_error_only_populated_at_day_zero():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    row_date_day3 = yesterday - timedelta(days=3)
    logs = {
        row_date_day3: log_entry(
            row_date_day3, day3=[prediction(model="gfs_seamless", rain=True, onset="14:00")]
        ),
    }
    actuals = {yesterday: actual(rain=True, onset_hour="16:00")}

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=empty_track_record(),
        actuals_primary=actuals,
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )

    entry3 = result.updated_track_record.get("gfs_seamless", 3)
    assert entry3.avg_onset_error_hrs_10 is None  # never populated at lead time != 0


def test_result_covers_all_requested_models_and_lead_times():
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: None,
        prior_track_record=empty_track_record(),
        actuals_primary={},
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=LEAD_TIMES,
    )
    keys = {(e.model, e.lead_time_days) for e in result.updated_track_record.entries}
    assert keys == {(m, k) for m in MODELS for k in LEAD_TIMES}


# ---------------------------------------------------------------------------
# All-time counter idempotency (regression)
# ---------------------------------------------------------------------------


def test_all_time_counters_do_not_double_count_on_repeat_same_day_runs():
    """All-time checks/correct are the one piece of carried-forward (not
    re-derived) state, so a double-count is permanent — it never washes out
    the way rolling windows do. Repeated runs against the SAME yesterday
    (manual workflow_dispatch, a retry, or a second scheduled run later the
    same day) must not inflate them."""
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}
    actuals = {yesterday: actual(rain=True)}

    track_record = empty_track_record()
    for _ in range(3):
        result = run_deterministic_verification_and_scoring(
            log_lookup=lambda d: logs.get(d),
            prior_track_record=track_record,
            actuals_primary=actuals,
            today=today,
            yesterday=yesterday,
            models=MODELS,
            lead_times_days=LEAD_TIMES,
        )
        track_record = result.updated_track_record  # as read-modify-write of the file does

    entry = track_record.get("gfs_seamless", 0)
    assert entry.all_time_checks == 1
    assert entry.all_time_correct == 1
    assert entry.last_verified_target_date == yesterday


def test_all_time_counters_still_advance_on_a_genuinely_new_day():
    """The guard must not freeze the counters — a new target date still counts."""
    day1_today, day1_yesterday = date(2026, 8, 11), date(2026, 8, 10)
    day2_today, day2_yesterday = date(2026, 8, 12), date(2026, 8, 11)

    logs = {
        day1_yesterday: log_entry(day1_yesterday, day0=[prediction(model="gfs_seamless", rain=True)]),
        day2_yesterday: log_entry(day2_yesterday, day0=[prediction(model="gfs_seamless", rain=True)]),
    }
    actuals = {day1_yesterday: actual(rain=True), day2_yesterday: actual(rain=True)}

    track_record = empty_track_record()
    for today, yesterday in [(day1_today, day1_yesterday), (day2_today, day2_yesterday)]:
        result = run_deterministic_verification_and_scoring(
            log_lookup=lambda d: logs.get(d),
            prior_track_record=track_record,
            actuals_primary=actuals,
            today=today,
            yesterday=yesterday,
            models=MODELS,
            lead_times_days=LEAD_TIMES,
        )
        track_record = result.updated_track_record

    entry = track_record.get("gfs_seamless", 0)
    assert entry.all_time_checks == 2
    assert entry.all_time_correct == 2
    assert entry.last_verified_target_date == day2_yesterday
