from datetime import date, datetime, timedelta, timezone

import pytest

from openlocalweather import pipeline
from openlocalweather.config import load_location_config
from openlocalweather.fetch.metar import StationWeather
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


def test_rain_pct_trend_is_computed_and_populated_on_track_record_entry():
    """Genuinely divergent recent-vs-longer-term skill (hand-constructed,
    not just "some data exists") should come out the other end as a
    populated rain_pct_trend/rain_pct_trend_delta, not left for the LLM to
    notice by comparing rolling_10_rain_pct against rolling_30_rain_pct
    itself."""
    today = date(2026, 8, 21)
    yesterday = date(2026, 8, 20)
    logs, actuals = {}, {}
    # 10 consecutive days ending yesterday, lead_time=0 (so the prediction
    # row date equals the target date — no offset to account for). Model
    # always predicts rain; the most recent 5 days actually did rain
    # (5/5 correct), the older 5 days did not (0/5 correct) — recent skill
    # is genuinely, unambiguously better than the longer-term average.
    for i in range(10):
        d = yesterday - timedelta(days=i)
        logs[d] = log_entry(d, day0=[prediction(model="gfs_seamless", rain=True)])
        actuals[d] = actual(rain=(i < 5))

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=empty_track_record(),
        actuals_primary=actuals,
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=[0],
        window_short=5,
        window_long=10,
    )

    entry = next(
        e for e in result.updated_track_record.entries if e.model == "gfs_seamless" and e.lead_time_days == 0
    )
    assert entry.rolling_10_rain_pct == pytest.approx(100.0)
    assert entry.rolling_30_rain_pct == pytest.approx(50.0)
    assert entry.rain_pct_trend == "improving"
    assert entry.rain_pct_trend_delta == pytest.approx(50.0)


def test_rain_pct_trend_is_none_when_history_is_too_thin():
    """A single verified check is nowhere near TREND_MIN_CHECKS_SHORT/LONG
    — the trend must stay None rather than fabricate a label off one data
    point."""
    today = date(2026, 8, 21)
    yesterday = date(2026, 8, 20)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}
    actuals = {yesterday: actual(rain=True)}

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=empty_track_record(),
        actuals_primary=actuals,
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=[0],
    )

    entry = next(
        e for e in result.updated_track_record.entries if e.model == "gfs_seamless" and e.lead_time_days == 0
    )
    assert entry.rain_pct_trend is None
    assert entry.rain_pct_trend_delta is None


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


def test_all_time_is_derived_from_the_whole_record_not_carried_forward():
    """All-time used to be an incremental counter — the one number here that
    could not be recomputed from the record. Open-Meteo revises recent
    observations, so an incremental count bakes provisional verdicts in
    permanently. It is now re-derived by walking every stored prediction."""
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    # Three consecutive scoreable days: two correct calls, one miss.
    logs, actuals = {}, {}
    for offset, (predicted_rain, actual_rain) in enumerate(
        [(True, True), (True, False), (False, False)]
    ):
        d = yesterday - timedelta(days=offset)
        logs[d] = log_entry(d, day0=[prediction(model="gfs_seamless", rain=predicted_rain)])
        actuals[d] = actual(rain=actual_rain)

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=empty_track_record(),
        actuals_primary=actuals,
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=[0],
        earliest_log_date=min(logs),
    )

    entry = result.updated_track_record.get("gfs_seamless", 0)
    assert entry.all_time_checks == 3
    assert entry.all_time_correct == 2
    assert entry.all_time_rain_pct == pytest.approx(100 * 2 / 3)
    # Coverage is recorded so a thin history can't masquerade as a long one.
    assert entry.all_time_earliest_target_date == min(logs)


def test_all_time_is_idempotent_across_repeated_runs():
    """Stronger than the old "doesn't double-count" guarantee: running the
    same day any number of times produces the identical figure, because
    nothing is accumulated. The last_verified_target_date guard existed
    solely to paper over the incremental version of this."""
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}
    actuals = {yesterday: actual(rain=True)}

    counts = []
    track = empty_track_record()
    for _ in range(3):
        result = run_deterministic_verification_and_scoring(
            log_lookup=lambda d: logs.get(d),
            prior_track_record=track,
            actuals_primary=actuals,
            today=today,
            yesterday=yesterday,
            models=MODELS,
            lead_times_days=[0],
            earliest_log_date=yesterday,
        )
        track = result.updated_track_record
        counts.append(track.get("gfs_seamless", 0).all_time_checks)

    assert counts == [1, 1, 1]


def test_all_time_self_heals_when_an_observation_is_revised():
    """The bug this whole change exists for. A day scored "correct" against
    provisional data must flip to incorrect once the observation is revised
    — an incremental counter would have kept the stale verdict forever."""
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}

    def run(actuals, prior):
        return run_deterministic_verification_and_scoring(
            log_lookup=lambda d: logs.get(d),
            prior_track_record=prior,
            actuals_primary=actuals,
            today=today,
            yesterday=yesterday,
            models=MODELS,
            lead_times_days=[0],
            earliest_log_date=yesterday,
        ).updated_track_record

    # Provisional: it rained, so the "rain" call scores correct.
    provisional = run({yesterday: actual(rain=True)}, empty_track_record())
    assert provisional.get("gfs_seamless", 0).all_time_correct == 1

    # Revised: it did not rain after all. Same check count, now zero correct.
    revised = run({yesterday: actual(rain=False)}, provisional)
    assert revised.get("gfs_seamless", 0).all_time_checks == 1
    assert revised.get("gfs_seamless", 0).all_time_correct == 0
    assert revised.get("gfs_seamless", 0).all_time_rain_pct == 0.0


def test_all_time_never_silently_shrinks_when_actuals_are_missing(capsys):
    """A derivation covering fewer checks than before means actuals are
    missing for dates we hold predictions for — a data problem to surface,
    not a smaller headline number to quietly publish."""
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    logs = {yesterday: log_entry(yesterday, day0=[prediction(model="gfs_seamless", rain=True)])}

    prior = empty_track_record()
    for e in prior.entries:
        if e.model == "gfs_seamless" and e.lead_time_days == 0:
            e.all_time_checks = 41
            e.all_time_correct = 30
            e.all_time_rain_pct = 100 * 30 / 41

    result = run_deterministic_verification_and_scoring(
        log_lookup=lambda d: logs.get(d),
        prior_track_record=prior,
        actuals_primary={yesterday: actual(rain=True)},  # only ONE day of actuals
        today=today,
        yesterday=yesterday,
        models=MODELS,
        lead_times_days=[0],
        earliest_log_date=yesterday,
    )

    entry = result.updated_track_record.get("gfs_seamless", 0)
    assert entry.all_time_checks == 41, "must not shrink to the derivable count"
    assert entry.all_time_correct == 30
    assert "WARNING" in capsys.readouterr().err


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

# ---------------------------------------------------------------------------
# Observed thunder stamped onto the actuals (see fetch/metar.py)
# ---------------------------------------------------------------------------

AUG_24 = date(2026, 8, 24)
AUG_25 = date(2026, 8, 25)


def thunder_location(icao: str = "HKKI"):
    return load_location_config("config/location.example.yaml").model_copy(
        update={"metar_station_icao": icao, "timezone": "Africa/Nairobi"}
    )


def two_days() -> dict[date, DailyActual]:
    return {
        AUG_24: DailyActual(rain=False, precip_mm=0.5),
        AUG_25: DailyActual(rain=False, precip_mm=0.4),
    }


def stub_thunder(monkeypatch, result):
    monkeypatch.setattr(
        pipeline.metar_fetch, "observed_station_data", lambda *a, **k: (result, None)
    )


def test_apply_observed_thunder_stamps_each_day(monkeypatch):
    stub_thunder(monkeypatch, {
        AUG_24: StationWeather(thunder=True, precipitation=False),
        AUG_25: StationWeather(thunder=False, precipitation=False),
    })
    actuals = two_days()
    pipeline._apply_station_observations(actuals, thunder_location())
    assert actuals[AUG_24].thunder is True
    assert actuals[AUG_25].thunder is False


def test_apply_observed_thunder_leaves_none_when_archive_unavailable(monkeypatch):
    stub_thunder(monkeypatch, None)
    actuals = two_days()
    pipeline._apply_station_observations(actuals, thunder_location())
    assert all(a.thunder is None for a in actuals.values())


def test_apply_observed_thunder_leaves_unreported_days_alone(monkeypatch):
    # A day the station filed nothing for stays None, not False.
    stub_thunder(monkeypatch, {AUG_24: StationWeather(thunder=True, precipitation=False)})
    actuals = two_days()
    pipeline._apply_station_observations(actuals, thunder_location())
    assert actuals[AUG_25].thunder is None


def test_apply_observed_thunder_asks_only_for_the_bucketed_range(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz: (seen.update(icao=icao, start=start, end=end, tz=tz) or None, None)
    )
    pipeline._apply_station_observations(two_days(), thunder_location())
    assert seen == {"icao": "HKKI", "start": AUG_24, "end": AUG_25, "tz": "Africa/Nairobi"}


def test_apply_observed_thunder_empty_actuals_is_a_no_op(monkeypatch):
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda *a, **k: (pytest.fail("must not fetch for an empty range"), None)
    )
    pipeline._apply_station_observations({}, thunder_location())


def test_apply_station_observations_stamps_the_precipitation_onset(monkeypatch):
    """The onset has to be STORED, not just parsed.

    Item 53.1a added precipitation_onset to StationWeather and to
    DailyActual, and the two places that stamp observations onto the record
    kept copying only the two booleans — so the field was written null on
    every day and the day-over-day description went on saying "dry".

    Caught by rebuilding the real record and reading the diff, not by the
    unit tests, which set the field directly and never exercised the copy.
    """
    stub_thunder(monkeypatch, {
        AUG_24: StationWeather(
            thunder=False, precipitation=True, precipitation_onset="19:00"
        ),
    })
    actuals = two_days()
    pipeline._apply_station_observations(actuals, thunder_location())

    assert actuals[AUG_24].precipitation_onset == "19:00"
    assert actuals[AUG_24].observed_onset() == "19:00"
