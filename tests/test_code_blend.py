"""ROADMAP item 173, stage 1 — a forecast from the record alone."""

from datetime import date, datetime, timedelta

from openlocalweather.backfill import backfill_entry_code_blend
from openlocalweather.code_blend import (
    blend_inputs,
    code_blend_prediction,
    code_blend_predictions,
    rain_weights,
    temperature_corrections,
    windows_as_of,
)
from openlocalweather.defaults import BLEND_MODEL_ID, CODE_BLEND_MODEL_ID
from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)
from openlocalweather.verify.scoring import RollingWindowResult, scored_predictions


def window(rain_pct=80.0, checks=30, high_err=None, low_err=None) -> RollingWindowResult:
    return RollingWindowResult(
        checks_found=checks,
        rain_pct=rain_pct,
        onset_err=None,
        wind_err=None,
        high_err=high_err,
        low_err=low_err,
        mslp_err=None,
    )


def pred(model, rain, high=None, low=None) -> ModelPrediction:
    return ModelPrediction(model=model, rain=rain, high_c=high, low_c=low)


def test_the_weight_is_the_hit_rate_above_a_coin_flip():
    weights = rain_weights({"ecmwf_ifs025": window(82.0), "gfs_seamless": window(67.0)})

    assert weights == {"ecmwf_ifs025": 32.0, "gfs_seamless": 17.0}


def test_a_coin_flip_or_worse_does_not_vote():
    """Absent, not zero, so the keys say who voted."""
    assert rain_weights({"gfs_seamless": window(50.0), "ukmo_seamless": window(40.0)}) == {}


def test_a_short_record_does_not_vote():
    assert rain_weights({"kenya_met": window(90.0, checks=9)}) == {}
    assert rain_weights({"kenya_met": window(None, checks=0)}) == {}


def test_the_weighted_majority_decides_rain():
    """30 wet against 20 dry: wet, and the share is the probability."""
    blend = code_blend_prediction(
        [pred("ecmwf_ifs025", True), pred("gfs_seamless", False)],
        {"ecmwf_ifs025": 30.0, "gfs_seamless": 20.0},
    )

    assert blend.model == CODE_BLEND_MODEL_ID
    assert blend.rain is True
    assert blend.rain_probability_pct == 60


def test_a_tie_breaks_dry():
    blend = code_blend_prediction(
        [pred("ecmwf_ifs025", True), pred("gfs_seamless", False), pred("icon_seamless", False)],
        {"ecmwf_ifs025": 30.0, "gfs_seamless": 20.0, "icon_seamless": 10.0},
    )

    assert blend.rain is False
    assert blend.rain_probability_pct == 50


def test_no_vote_is_no_call():
    """None rather than a guessed boolean, which would be scored as
    confidently as a real one."""
    assert code_blend_prediction([pred("gfs_seamless", True)], {}) is None
    assert code_blend_prediction([pred("gfs_seamless", None)], {"gfs_seamless": 20.0}) is None


def test_a_model_without_a_weight_cannot_move_the_call():
    """The LLM's own row sits among the stored predictions; it must not vote."""
    blend = code_blend_prediction(
        [pred("gfs_seamless", False), pred(BLEND_MODEL_ID, True)],
        {"gfs_seamless": 20.0},
    )

    assert blend.rain is False
    assert blend.rain_probability_pct == 0


def test_a_model_that_came_in_cold_is_corrected_upward():
    """THE SIGN. Errors are `actual - predicted`, so +1.3 on the high is a
    model that ran cold and the correction is ADDED. Asserted as values."""
    highs, lows = temperature_corrections({"ecmwf_ifs025": window(high_err=1.3, low_err=-0.5, checks=10)})

    blend = code_blend_prediction(
        [pred("ecmwf_ifs025", False, high=30.0, low=19.0)],
        {"ecmwf_ifs025": 30.0},
        highs,
        lows,
    )

    assert blend.high_c == 31.3
    assert blend.low_c == 18.5


def test_only_corrected_models_enter_a_temperature():
    highs, _ = temperature_corrections(
        {"ecmwf_ifs025": window(high_err=1.0, checks=10), "gfs_seamless": window(high_err=-1.0, checks=9)}
    )
    assert highs == {"ecmwf_ifs025": 1.0}

    blend = code_blend_prediction(
        [pred("ecmwf_ifs025", False, high=30.0), pred("gfs_seamless", False, high=40.0)],
        {"ecmwf_ifs025": 30.0, "gfs_seamless": 20.0},
        highs,
    )

    assert blend.high_c == 31.0


def test_no_correction_is_no_temperature():
    blend = code_blend_prediction([pred("ecmwf_ifs025", False, high=30.0)], {"ecmwf_ifs025": 30.0})

    assert blend.high_c is None
    assert blend.low_c is None


def test_inputs_are_what_the_forecaster_sees_minus_best_match():
    inputs = blend_inputs("kenya_met")

    assert "best_match" not in inputs
    assert BLEND_MODEL_ID not in inputs
    assert "persistence" not in inputs
    assert "climatology" not in inputs
    assert {"ecmwf_ifs025", "gfs_seamless", "kenya_met"} <= set(inputs)


def _entry(d: date, rain: bool) -> DailyLogEntry:
    return DailyLogEntry(
        date=d,
        rain_expected="-",
        temp_high_c=28.0,
        temp_low_c=18.0,
        temp_high_low_display="-",
        mslp_trend_24h="-",
        synoptic_pattern="-",
        narrative_markdown="-",
        model_predictions=ModelPredictionsByLead(
            day0=[ModelPrediction(model="gfs_seamless", rain=rain, high_c=27.0)],
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime(d.year, d.month, d.day, 3, 1),
            llm_provider="gemini",
            llm_model="gemini-3.6-flash",
            pipeline_version="0.1.0",
        ),
    )


def test_the_record_as_of_an_issuance_cannot_see_its_day():
    """NO PEEKING, on a POPULATED record. A guard asserted against an empty
    window passes whatever the code does — the blend's own rule leaked for
    weeks behind three such tests — so the window is shown full first."""
    issued = date(2026, 9, 20)
    days = [issued + timedelta(days=i) for i in range(-12, 4)]
    log = {d: _entry(d, rain=True) for d in days}
    actuals = {d: DailyActual(rain=True, high_c=28.0, low_c=18.0) for d in days}

    before = windows_as_of(["gfs_seamless"], 0, 30, issued, log.get, actuals)["gfs_seamless"]
    assert before.checks_found == 12
    assert before.rain_pct == 100.0

    # Every forecast from the issuance on turns wrong, and so does the day.
    for d in days:
        if d >= issued:
            log[d] = _entry(d, rain=False)
            actuals[d] = DailyActual(rain=False, high_c=40.0, low_c=10.0)

    after = windows_as_of(["gfs_seamless"], 0, 30, issued, log.get, actuals)["gfs_seamless"]
    assert after == before


def _record(issued: date, days: int = 12):
    """`days` verified days before `issued`, every model right every time."""
    dates = [issued + timedelta(days=i) for i in range(-days, 1)]
    log = {d: _entry(d, rain=True) for d in dates}
    actuals = {d: DailyActual(rain=True, high_c=28.0, low_c=18.0) for d in dates[:-1]}
    return log, actuals


def test_the_composer_calls_day0_with_temperatures_and_declines_without_a_record():
    issued = date(2026, 9, 20)
    log, actuals = _record(issued)

    blend = code_blend_predictions(scored_predictions(log[issued]), issued, log.get, actuals, ["gfs_seamless"])

    assert [p.model for p in blend.day0] == [CODE_BLEND_MODEL_ID]
    assert blend.day0[0].rain is True
    # 27.0 forecast, 28.0 observed on every day: +1.0 added back.
    assert blend.day0[0].high_c == 28.0
    # No stored Day+3 or Day+7 prediction to blend.
    assert blend.day3 == []
    assert blend.day7 == []


def test_the_backfill_adds_to_row_0_once():
    issued = date(2026, 9, 20)
    log, actuals = _record(issued)

    filled = backfill_entry_code_blend(log[issued], log.get, actuals, ["gfs_seamless"])

    assert filled is not None
    assert CODE_BLEND_MODEL_ID in {p.model for p in scored_predictions(filled).day0}
    # The models' own rows are untouched.
    assert [p for p in scored_predictions(filled).day0 if p.model != CODE_BLEND_MODEL_ID] == list(
        scored_predictions(log[issued]).day0
    )
    # Idempotent: a second pass has nothing to add.
    assert backfill_entry_code_blend(filled, log.get, actuals, ["gfs_seamless"]) is None


def test_the_backfill_leaves_a_day_with_no_record_alone():
    issued = date(2026, 9, 20)
    log, actuals = _record(issued, days=9)

    assert backfill_entry_code_blend(log[issued], log.get, actuals, ["gfs_seamless"]) is None
