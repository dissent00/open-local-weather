from datetime import date, datetime, timezone

import pytest

from openlocalweather.models import DailyActual, DailyLogEntry, LogEntryMeta, ModelPrediction, ModelPredictionsByLead
from openlocalweather.verify.scoring import mean, rescore_rolling_window, score_prediction


def prediction(**overrides) -> ModelPrediction:
    defaults = dict(model="gfs_seamless", rain=True, onset="14:00", wind_kmh=20.0, high_c=26.0, low_c=18.0, mslp_trend=-1.0)
    defaults.update(overrides)
    return ModelPrediction(**defaults)


def actual(**overrides) -> DailyActual:
    defaults = dict(rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0, onset_hour="14:00")
    defaults.update(overrides)
    return DailyActual(**defaults)


def log_entry(d: date, day0_predictions: list[ModelPrediction] | None = None, **kwargs) -> DailyLogEntry:
    defaults = dict(
        date=d,
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26/18",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="narrative",
        model_predictions=ModelPredictionsByLead(day0=day0_predictions or []),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc), llm_provider="test", llm_model="test", pipeline_version="0"
        ),
    )
    defaults.update(kwargs)
    return DailyLogEntry(**defaults)


# ---------------------------------------------------------------------------
# score_prediction
# ---------------------------------------------------------------------------


def test_score_prediction_none_predicted_returns_none():
    assert score_prediction(None, actual(), 0) is None


def test_score_prediction_none_actual_returns_none():
    assert score_prediction(prediction(), None, 0) is None


def test_score_prediction_rain_correct_when_both_true():
    score = score_prediction(prediction(rain=True), actual(rain=True), 0)
    assert score.rain_correct is True


def test_score_prediction_rain_incorrect_when_mismatched():
    score = score_prediction(prediction(rain=True), actual(rain=False), 0)
    assert score.rain_correct is False


def test_score_prediction_onset_error_only_at_lead_time_zero():
    # Predicted 14:00, actual 15:30 -> 1.5 hours late.
    score = score_prediction(
        prediction(rain=True, onset="14:00"), actual(rain=True, onset_hour="15:30"), lead_time_days=0
    )
    assert score.onset_error_hrs == pytest.approx(1.5)

    # Same data at lead_time_days=3 must NOT produce an onset error — Day+3
    # predictions never carry real onset timing.
    score3 = score_prediction(
        prediction(rain=True, onset="14:00"), actual(rain=True, onset_hour="15:30"), lead_time_days=3
    )
    assert score3.onset_error_hrs is None


def test_score_prediction_onset_error_none_when_actual_did_not_rain():
    score = score_prediction(prediction(rain=True, onset="14:00"), actual(rain=False, onset_hour=None), 0)
    assert score.onset_error_hrs is None


def test_score_prediction_onset_error_none_when_predicted_onset_missing():
    score = score_prediction(prediction(rain=True, onset=None), actual(rain=True, onset_hour="15:00"), 0)
    assert score.onset_error_hrs is None


def test_score_prediction_wind_high_low_mslp_errors_are_actual_minus_predicted():
    score = score_prediction(
        prediction(wind_kmh=20.0, high_c=25.0, low_c=17.0, mslp_trend=-1.0),
        actual(peak_wind_kmh=23.0, high_c=26.5, low_c=16.0, mslp_trend=0.5),
        0,
    )
    assert score.wind_error_kmh == pytest.approx(3.0)
    assert score.high_error_c == pytest.approx(1.5)
    assert score.low_error_c == pytest.approx(-1.0)
    assert score.mslp_error_hpa == pytest.approx(1.5)


def test_score_prediction_missing_fields_null_propagate_without_crashing():
    score = score_prediction(
        prediction(wind_kmh=None, high_c=25.0, low_c=None, mslp_trend=None),
        actual(peak_wind_kmh=20.0, high_c=None, low_c=16.0, mslp_trend=0.5),
        0,
    )
    assert score.wind_error_kmh is None  # predicted side missing
    assert score.high_error_c is None  # actual side missing
    assert score.low_error_c is None  # predicted side missing
    assert score.mslp_error_hpa is None  # predicted side missing
    assert score.rain_correct is True  # rain comparison is unaffected by the other fields


# ---------------------------------------------------------------------------
# mean()
# ---------------------------------------------------------------------------


def test_mean_filters_none_values():
    assert mean([1.0, None, 3.0]) == pytest.approx(2.0)


def test_mean_empty_or_all_none_returns_none():
    assert mean([]) is None
    assert mean([None, None]) is None


# ---------------------------------------------------------------------------
# rescore_rolling_window
# ---------------------------------------------------------------------------


def test_rescore_rolling_window_cold_start_no_data():
    result = rescore_rolling_window(
        "gfs_seamless", 0, window_size=10, yesterday=date(2026, 8, 10),
        log_lookup=lambda d: None, actuals={},
    )
    assert result.checks_found == 0
    assert result.rain_pct is None
    assert result.onset_err is None


def test_rescore_rolling_window_collects_exactly_window_size_most_recent_checks():
    # 15 consecutive days of Day+0 log entries + matching actuals; a window
    # of 10 should stop at exactly 10, using the 10 MOST RECENT days
    # (walking backward from yesterday), not all 15.
    yesterday = date(2026, 8, 15)
    logs: dict[date, DailyLogEntry] = {}
    actuals: dict[date, DailyActual] = {}
    for i in range(15):
        d = yesterday - __import__("datetime").timedelta(days=i)
        # Alternate hit/miss so we can hand-verify the resulting percentage.
        rain = i % 2 == 0
        logs[d] = log_entry(d, day0_predictions=[prediction(model="gfs_seamless", rain=rain)])
        actuals[d] = actual(rain=rain)

    result = rescore_rolling_window(
        "gfs_seamless", 0, window_size=10, yesterday=yesterday,
        log_lookup=lambda d: logs.get(d), actuals=actuals,
    )
    assert result.checks_found == 10
    # All 10 collected days were hit (predicted == actual, by construction)
    # since we always score rain against the SAME i-derived value on both
    # sides above -> 100% rain accuracy in this fixture.
    assert result.rain_pct == pytest.approx(100.0)


def test_rescore_rolling_window_hand_computed_wind_error_average():
    yesterday = date(2026, 8, 5)
    logs: dict[date, DailyLogEntry] = {}
    actuals: dict[date, DailyActual] = {}
    wind_errors = [2.0, -4.0, 6.0]  # hand-computed expected mean = 1.333...
    for i, err in enumerate(wind_errors):
        d = yesterday - __import__("datetime").timedelta(days=i)
        predicted_wind = 20.0
        logs[d] = log_entry(d, day0_predictions=[prediction(model="gfs_seamless", wind_kmh=predicted_wind)])
        actuals[d] = actual(peak_wind_kmh=predicted_wind + err)

    result = rescore_rolling_window(
        "gfs_seamless", 0, window_size=3, yesterday=yesterday,
        log_lookup=lambda d: logs.get(d), actuals=actuals,
    )
    assert result.checks_found == 3
    assert result.wind_err == pytest.approx(sum(wind_errors) / 3)


def test_rescore_rolling_window_skips_gaps_in_the_log():
    # Only every other day has a log entry; the safety bound (window_size+30)
    # must be enough to still find window_size checks by searching further
    # back, not silently stop early.
    yesterday = date(2026, 8, 20)
    logs: dict[date, DailyLogEntry] = {}
    actuals: dict[date, DailyActual] = {}
    for i in range(0, 40, 2):  # every other day has data, 20 days worth
        d = yesterday - __import__("datetime").timedelta(days=i)
        logs[d] = log_entry(d, day0_predictions=[prediction(model="gfs_seamless", rain=True)])
        actuals[d] = actual(rain=True)

    result = rescore_rolling_window(
        "gfs_seamless", 0, window_size=10, yesterday=yesterday,
        log_lookup=lambda d: logs.get(d), actuals=actuals,
    )
    assert result.checks_found == 10


def test_rescore_rolling_window_stops_at_safety_bound_when_data_insufficient():
    # Only 5 days of real data exist anywhere in history; asking for a
    # window of 10 must return whatever was found (5), not hang or crash.
    yesterday = date(2026, 8, 5)
    logs: dict[date, DailyLogEntry] = {}
    actuals: dict[date, DailyActual] = {}
    for i in range(5):
        d = yesterday - __import__("datetime").timedelta(days=i)
        logs[d] = log_entry(d, day0_predictions=[prediction(model="gfs_seamless", rain=True)])
        actuals[d] = actual(rain=True)

    result = rescore_rolling_window(
        "gfs_seamless", 0, window_size=10, yesterday=yesterday,
        log_lookup=lambda d: logs.get(d), actuals=actuals,
    )
    assert result.checks_found == 5


def test_rescore_rolling_window_uses_correct_lead_time_offset():
    # A Day+3 window must look up the row dated (target - 3), not (target).
    yesterday = date(2026, 8, 10)
    target = yesterday  # first iteration's target_date == yesterday
    row_date = target - __import__("datetime").timedelta(days=3)
    logs = {row_date: log_entry(row_date, day0_predictions=[])}
    # Put the Day+3 prediction on the day3 field, not day0.
    logs[row_date].model_predictions.day3 = [prediction(model="gfs_seamless", rain=True)]
    actuals = {target: actual(rain=True)}

    result = rescore_rolling_window(
        "gfs_seamless", 3, window_size=1, yesterday=yesterday,
        log_lookup=lambda d: logs.get(d), actuals=actuals,
    )
    assert result.checks_found == 1
    assert result.rain_pct == pytest.approx(100.0)
