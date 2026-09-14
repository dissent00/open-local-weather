from datetime import date, datetime, timezone

import pytest

from openlocalweather.models import DailyActual, DailyLogEntry, LogEntryMeta, ModelPrediction, ModelPredictionsByLead
from openlocalweather.verify.scoring import scored_predictions
from openlocalweather.verify.scoring import (
    compute_rain_pct_trend,
    mean,
    rescore_rolling_window,
    resolve_target_date,
    score_prediction,
)


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


def test_score_prediction_returns_none_when_rain_is_unknown():
    """A model with no data at this lead time must produce no check at all,
    rather than a spurious hit/miss that inflates its track record."""
    assert score_prediction(prediction(rain=None), actual(rain=True), 0) is None
    assert score_prediction(prediction(rain=None), actual(rain=False), 0) is None


def test_rolling_window_skips_unknown_rain_predictions():
    import datetime as _dt
    yesterday = date(2026, 8, 20)
    logs, actuals = {}, {}
    for i in range(5):
        d = yesterday - _dt.timedelta(days=i)
        # Model never has data — should yield zero scoreable checks, not 5.
        logs[d] = log_entry(d, day0_predictions=[prediction(model="ukmo_seamless", rain=None)])
        actuals[d] = actual(rain=False)

    result = rescore_rolling_window(
        "ukmo_seamless", 0, window_size=10, yesterday=yesterday,
        log_lookup=lambda d: logs.get(d), actuals=actuals,
    )
    assert result.checks_found == 0
    assert result.rain_pct is None


# --- compute_rain_pct_trend ---

TREND_ARGS = dict(min_checks_short=5, min_checks_long=10, threshold_pct=15.0)


def test_trend_none_when_either_window_lacks_data():
    assert compute_rain_pct_trend(None, 80.0, 10, 20, **TREND_ARGS) == (None, None)
    assert compute_rain_pct_trend(80.0, None, 10, 20, **TREND_ARGS) == (None, None)


def test_trend_none_when_short_window_below_min_checks():
    # 4 checks found, min_checks_short=5 — not enough for a meaningful trend,
    # even though both percentages are present.
    assert compute_rain_pct_trend(80.0, 60.0, 4, 20, **TREND_ARGS) == (None, None)


def test_trend_none_when_long_window_below_min_checks():
    # 9 checks found, min_checks_long=10.
    assert compute_rain_pct_trend(80.0, 60.0, 10, 9, **TREND_ARGS) == (None, None)


def test_trend_improving_when_recent_exceeds_threshold_above_longterm():
    label, delta = compute_rain_pct_trend(90.0, 60.0, 10, 20, **TREND_ARGS)
    assert label == "improving"
    assert delta == pytest.approx(30.0)


def test_trend_declining_when_recent_exceeds_threshold_below_longterm():
    label, delta = compute_rain_pct_trend(40.0, 70.0, 10, 20, **TREND_ARGS)
    assert label == "declining"
    assert delta == pytest.approx(-30.0)


def test_trend_stable_when_gap_is_within_threshold():
    label, delta = compute_rain_pct_trend(65.0, 60.0, 10, 20, **TREND_ARGS)
    assert label == "stable"
    assert delta == pytest.approx(5.0)


def test_trend_exactly_at_threshold_counts_as_improving_or_declining():
    # >= threshold_pct, not > — boundary is inclusive per the implementation.
    label_up, delta_up = compute_rain_pct_trend(75.0, 60.0, 10, 20, **TREND_ARGS)
    assert label_up == "improving"
    assert delta_up == pytest.approx(15.0)

    label_down, delta_down = compute_rain_pct_trend(45.0, 60.0, 10, 20, **TREND_ARGS)
    assert label_down == "declining"
    assert delta_down == pytest.approx(-15.0)


# ---------------------------------------------------------------------------
# Observed thunder as convection (see DailyActual.observed_convection)
# ---------------------------------------------------------------------------


def test_rain_call_is_correct_on_an_observed_thunder_day():
    # 2026-08-24, the case that prompted this: 0.5 mm in the reanalysis, TS at
    # the airport. GFS, ECMWF and Kenya Met all called rain and were scored
    # wrong for it.
    score = score_prediction(prediction(rain=True), actual(rain=False, thunder=True), 0)
    assert score.rain_correct is True


def test_dry_call_is_wrong_on_an_observed_thunder_day():
    # The other half of the same correction, and the uncomfortable half: ICON
    # and UKMO called that day dry and were credited for it.
    score = score_prediction(prediction(rain=False), actual(rain=False, thunder=True), 0)
    assert score.rain_correct is False


def test_thunder_none_scores_exactly_as_before():
    # A deployment with no METAR station must be unaffected.
    assert score_prediction(prediction(rain=False), actual(rain=False, thunder=None), 0).rain_correct is True
    assert score_prediction(prediction(rain=True), actual(rain=False, thunder=None), 0).rain_correct is False


def test_thunder_false_is_evidence_not_absence():
    # The station reported and saw nothing: a dry call is genuinely right.
    score = score_prediction(prediction(rain=False), actual(rain=False, thunder=False), 0)
    assert score.rain_correct is True


def test_rain_and_thunder_together_still_convective():
    score = score_prediction(prediction(rain=True), actual(rain=True, thunder=True), 0)
    assert score.rain_correct is True


def test_onset_error_scored_on_a_thunder_day_that_had_rain_timing():
    # Onset only exists when the reanalysis saw measurable rain, but the day
    # is now convective, so the timing comparison must still happen.
    score = score_prediction(
        prediction(rain=True, onset="14:00"),
        actual(rain=False, thunder=True, onset_hour="15:30"),
        0,
    )
    assert score.onset_error_hrs == 1.5


def test_cloud_is_scored_so_the_record_can_learn_who_reads_the_sky():
    """Raised by the operator 2026-09-10, asking that the forecast come to
    "trust the better models for cloud coverage" the way it already weighs
    the models on rain and temperature — "if 3 good models say AM clouds and
    2 that aren't so good at that metric say none, we can say partly cloudy".

    THAT IS BLOCKED ON THERE BEING A CLOUD RECORD AT ALL. cloud_cover_pct has
    been stored on both sides since 2026-09-09 — forecast on ModelPrediction,
    reanalysis on DailyActual — and nothing scored one against the other, so
    no model could earn or lose standing on the sky however wrong it was.

    Measured that morning, and the reason it matters here: the five models
    split 3-2 on whether there was dawn cloud at all, and the two that said
    none were the two the operator's own eyes contradicted. A mean over that
    split lands on a number no model holds.
    """
    s = score_prediction(prediction(cloud_cover_pct=50.0), actual(cloud_cover_pct=42.0), 0)
    # OBSERVED MINUS FORECAST, the same sign convention as every other error
    # field here: negative means the model came in OVER what happened.
    assert s.cloud_error_pct == -8.0

    over = score_prediction(prediction(cloud_cover_pct=0.0), actual(cloud_cover_pct=42.0), 0)
    assert over.cloud_error_pct == 42.0, "a model that saw no cloud on a cloudy day scores worst"

    # Absent on either side is not zero error. Most stored days predate the
    # field entirely, and scoring those as perfect would hand every model a
    # skill it never demonstrated.
    assert score_prediction(prediction(cloud_cover_pct=None), actual(cloud_cover_pct=42.0), 0).cloud_error_pct is None
    assert score_prediction(prediction(cloud_cover_pct=50.0), actual(cloud_cover_pct=None), 0).cloud_error_pct is None


def test_the_rolling_window_carries_the_sky_too():
    """A per-day error nothing aggregates is a number nobody can act on. The
    window is what the track record and the prompt read, so this is the step
    that turns "who was right about the sky" from a stored field into a
    fact the forecaster can weigh.

    cloud_checks is reported separately from checks_found for the same reason
    brier_checks is: the field started on 2026-09-09, so for weeks a window
    will hold many scored days and few with cloud, and one count for both
    would imply evidence the figure does not have.
    """
    from datetime import timedelta

    yesterday = date(2026, 8, 15)
    # Three scoreable days: two carry a cloud forecast, the third predates
    # the field. Errors of -8.0 and +32.0, so a mean of +12.0 over the two —
    # and the same +12.0 whether the third is skipped or the window is
    # simply shorter, which is why cloud_checks is asserted as well.
    clouds = [50.0, 10.0, None]
    logs, actuals_by_date = {}, {}
    for i, cloud in enumerate(clouds):
        d = yesterday - timedelta(days=i)
        logs[d] = log_entry(d, [prediction(cloud_cover_pct=cloud)])
        actuals_by_date[d] = actual(cloud_cover_pct=42.0)

    result = rescore_rolling_window(
        "gfs_seamless", 0, window_size=10, yesterday=yesterday,
        log_lookup=logs.get, actuals=actuals_by_date,
    )
    assert result.checks_found == 3, "all three days scored on rain and temperature"
    assert result.cloud_checks == 2, "only two of them said anything about the sky"
    assert result.cloud_err == 12.0, (
        "the day with no cloud forecast must be skipped, not counted as zero"
    )


def test_the_convective_call_is_scored_against_whether_it_thundered():
    """ROADMAP item 35, and the gap left when its data half shipped. Peak
    CAPE reaches the forecaster every run, showing the full per-model spread
    — on 2026-09-10, GFS at 390 J/kg against UKMO at 3910 — and NOTHING
    checked any of them against whether a storm arrived. "Which model reads
    instability here" was unanswerable, which is the same gap cloud had
    until it was scored.

    SCORED AGAINST THUNDER, NOT RAIN, and that is the whole design decision.
    CAPE predicts thunderstorms; a day of steady frontal rain with no
    lightning is not a hit for a model that called high instability, and
    scoring it against `observed_convection()` would credit exactly that. It
    is also why this is a separate column from `rain_correct` rather than a
    second input to it.

    A BOOLEAN AGAINST A BOOLEAN, because there is no observed CAPE to
    subtract from. The reanalysis has a CAPE field, but agreeing with a
    reanalysis is not skill at anticipating storms, and the station's thunder
    flag is the only record of what actually happened overhead.
    """
    stormy = actual(thunder=True)
    calm = actual(thunder=False)

    assert score_prediction(prediction(peak_cape_jkg=1860.0), stormy, 0).convective_correct is True
    assert score_prediction(prediction(peak_cape_jkg=390.0), stormy, 0).convective_correct is False
    assert score_prediction(prediction(peak_cape_jkg=390.0), calm, 0).convective_correct is True
    assert score_prediction(prediction(peak_cape_jkg=1860.0), calm, 0).convective_correct is False


def test_a_convective_call_needs_both_a_cape_figure_and_an_observation():
    """Three-valued, like `thunder` itself. A model with no CAPE series made
    no call, and a day with no station report settled nothing — neither is a
    miss, and scoring either as one would invent skill data out of a gap.
    """
    # No CAPE forecast: the model said nothing about instability.
    assert score_prediction(prediction(peak_cape_jkg=None), actual(thunder=True), 0).convective_correct is None

    # No thunder observation: `thunder` is None when no station reported, and
    # that is not the same as a quiet day — see DailyActual.thunder.
    assert score_prediction(prediction(peak_cape_jkg=1860.0), actual(thunder=None), 0).convective_correct is None

    # Beyond Day+0 there is no CAPE at all: the extended leads come from the
    # daily endpoint. Same rule as onset.
    assert score_prediction(prediction(peak_cape_jkg=1860.0), actual(thunder=True), 3).convective_correct is None


# --- ROADMAP item 104, C1: a prediction says what it targets ----------------


def test_a_prediction_records_the_date_it_targets():
    """C1. Lead time is derived from the target, not the other way round.

    Today the target is implicit — a prediction sits on the issuance's row and
    the lead says how far forward it points, so `target = row + lead`. That
    works only while a day holds exactly one issuance, which is the assumption
    item 104 removes.
    """
    p = ModelPrediction(model="gfs_seamless", rain=True, target_date=date(2026, 9, 13))

    assert p.target_date == date(2026, 9, 13)


def test_a_prediction_written_before_the_field_existed_still_loads():
    """Three-valued, like every other field added to this record.

    None means "this row predates the field", NOT "it targets nothing". Every
    one of the entries committed before 2026-09-12 loads this way, and the
    verification pass has to keep scoring them from the row arithmetic.
    """
    p = ModelPrediction.model_validate({"model": "gfs_seamless", "rain": True})

    assert p.target_date is None


def test_the_target_is_what_the_row_arithmetic_says_when_absent():
    """The bridge that lets the old record and the new one be read by one
    pass. `resolve_target_date` is the single place the fallback lives, so a
    later change can delete it in one edit once no unmarked rows remain."""
    old = ModelPrediction(model="gfs_seamless", rain=True)
    new = ModelPrediction(model="gfs_seamless", rain=True, target_date=date(2026, 9, 20))

    # Row dated the 10th, lead 3 -> targets the 13th.
    assert resolve_target_date(old, row_date=date(2026, 9, 10), lead_time_days=3) == date(2026, 9, 13)
    # An explicit target wins, and is NOT required to agree with the arithmetic.
    assert resolve_target_date(new, row_date=date(2026, 9, 10), lead_time_days=3) == date(2026, 9, 20)


# --- ROADMAP item 104, contract item 2: scoring the issuance window --------


def _window_row(issued, models_cloud=None):
    from openlocalweather.models import IssuancePredictions, ModelPrediction
    return IssuancePredictions(
        issued_at=issued,
        window_predictions=[
            ModelPrediction(model="gfs_seamless", rain=True, onset="20:00", high_c=30.0,
                            low_c=18.0, wind_kmh=20.0, cloud_cover_pct=80.0),
            ModelPrediction(model="ecmwf_ifs025", rain=False, high_c=27.0,
                            low_c=19.0, wind_kmh=15.0, cloud_cover_pct=40.0),
        ],
    )


def test_a_window_is_scored_per_model_against_its_own_observation():
    """Contract item 2. The window's claim is a lead-0-shaped one — it carries
    an onset and a rain call about a period that has now finished — so it is
    scored by `score_prediction` at lead 0 rather than by a second scorer."""
    from datetime import datetime, timezone
    from openlocalweather.verify.scoring import score_window_row

    row = _window_row(datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc))
    observed = actual(rain=True, onset_hour="19:00", high_c=28.0, low_c=17.5,
                      peak_wind_kmh=26.0, cloud_cover_pct=60.0)

    scores = score_window_row(row, observed)

    assert set(scores) == {"gfs_seamless", "ecmwf_ifs025"}
    assert scores["gfs_seamless"].rain_correct is True
    assert scores["ecmwf_ifs025"].rain_correct is False
    # actual - predicted, the convention every other error here follows.
    assert scores["gfs_seamless"].high_error_c == pytest.approx(-2.0)
    assert scores["gfs_seamless"].cloud_error_pct == pytest.approx(-20.0)
    assert scores["gfs_seamless"].onset_error_hrs == pytest.approx(-1.0)


def test_a_window_with_no_observation_scores_nothing():
    """An absent observation is not a wrong forecast. The window bucketer
    returns None when the archive cannot cover the period, and that has to
    stay an absence all the way through rather than becoming a zero error."""
    from datetime import datetime, timezone
    from openlocalweather.verify.scoring import score_window_row

    row = _window_row(datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc))

    assert score_window_row(row, None) == {}


def test_a_row_that_made_no_window_claim_scores_nothing():
    """A run whose two-day fetch failed stored an empty window rather than a
    short one — there is nothing to score and that is not a miss."""
    from datetime import datetime, timezone
    from openlocalweather.models import IssuancePredictions
    from openlocalweather.verify.scoring import score_window_row

    row = IssuancePredictions(issued_at=datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc))

    assert score_window_row(row, actual(rain=True)) == {}


def test_a_window_is_not_scorable_until_every_hour_it_covers_is_a_finished_day():
    """MEASURED 2026-09-14, and this guard is the whole reason the lag is not
    24 hours. `archive-api.open-meteo.com` serves the CURRENT day, and what it
    serves is model output: asked for 2026-09-13..14 at 08:31 local it
    returned fifteen stamps in the future carrying temperatures, up to
    23:00 that evening.

    So an "observation" that reaches into today is partly a forecast, and
    scoring a window against it scores a forecast against a forecast. A window
    is scorable only once every hour it covers lies on a day that has ended.
    """
    from datetime import date, datetime
    from openlocalweather.verify.scoring import window_is_scorable

    # Issued 06:50 on the 11th, so the window runs 06:00 on the 11th to
    # 05:00 on the 12th and touches the 12th.
    issued = datetime(2026, 8, 11, 6, 50)

    assert window_is_scorable(issued, today=date(2026, 8, 12)) is False, (
        "the 12th is still running — its hours would be model output"
    )
    assert window_is_scorable(issued, today=date(2026, 8, 13)) is True

    # A window that closes exactly at midnight touches only one day.
    midnight = datetime(2026, 8, 11, 0, 0)
    assert window_is_scorable(midnight, today=date(2026, 8, 12)) is True


def _archive_two_days(start_day=11):
    """48 hours of 'observations', unique temperature per hour."""
    times, temp = [], []
    for i in range(48):
        times.append(f"2026-08-{start_day + i // 24:02d}T{i % 24:02d}:00")
        temp.append(10.0 + i)
    return {"hourly": {
        "time": times, "temperature_2m": temp, "precipitation": [0.0] * 48,
        "cloud_cover": [50.0] * 48, "wind_gusts_10m": [20.0] * 48,
        "pressure_msl": [1010.0] * 48,
    }}


def test_the_walker_scores_a_finished_window_and_stamps_it():
    from datetime import date, datetime, timezone
    from openlocalweather.models import DailyLogEntry, IssuancePredictions, ModelPrediction, LogEntryMeta
    from openlocalweather.verify.scoring import verify_closed_windows

    row = IssuancePredictions(
        issued_at=datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc),
        window_opened_local=datetime(2026, 8, 11, 6, 0),
        window_predictions=[ModelPrediction(model="gfs_seamless", rain=False, high_c=40.0)],
    )
    entry = log_entry(date(2026, 8, 11))
    entry.prediction_rows = [row]

    changed = verify_closed_windows(entry, _archive_two_days(), today=date(2026, 8, 13))

    assert changed is True
    assert row.window_verified_at is not None, "a scored row says when"
    # 06:00 on the 11th (temp 16) .. 05:00 on the 12th (temp 39).
    assert row.window_scores["gfs_seamless"].high_error_c == pytest.approx(39.0 - 40.0)


def test_the_walker_leaves_a_window_alone_until_its_days_have_finished():
    from datetime import date, datetime, timezone
    from openlocalweather.models import IssuancePredictions, ModelPrediction
    from openlocalweather.verify.scoring import verify_closed_windows

    row = IssuancePredictions(
        issued_at=datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc),
        window_opened_local=datetime(2026, 8, 11, 6, 0),
        window_predictions=[ModelPrediction(model="gfs_seamless", rain=False, high_c=40.0)],
    )
    entry = log_entry(date(2026, 8, 11))
    entry.prediction_rows = [row]

    # The 12th is still running, so its hours would be model output.
    changed = verify_closed_windows(entry, _archive_two_days(), today=date(2026, 8, 12))

    assert changed is False
    assert row.window_verified_at is None
    assert row.window_scores == {}


def test_the_walker_does_not_rescore_a_row_it_already_stamped():
    from datetime import date, datetime, timezone
    from openlocalweather.models import IssuancePredictions, ModelPrediction
    from openlocalweather.verify.scoring import verify_closed_windows

    stamped = datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc)
    row = IssuancePredictions(
        issued_at=datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc),
        window_opened_local=datetime(2026, 8, 11, 6, 0),
        window_predictions=[ModelPrediction(model="gfs_seamless", rain=False, high_c=40.0)],
        window_verified_at=stamped,
    )
    entry = log_entry(date(2026, 8, 11))
    entry.prediction_rows = [row]

    assert verify_closed_windows(entry, _archive_two_days(), today=date(2026, 8, 14)) is False
    assert row.window_verified_at == stamped, "row 0 of a scored window is not rewritten"
