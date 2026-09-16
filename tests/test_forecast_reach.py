"""ROADMAP item 150: how far each source forecast, observed per run and
never declared."""

from datetime import date, timedelta

from openlocalweather.defaults import MODELS
from openlocalweather.models import (
    DEGRADATION_DAILY_GUIDANCE_UNREADABLE,
    DEGRADATION_EXTENDED_OUTLOOK,
    DEGRADATION_METAR,
    RunDegradation,
)
from openlocalweather.models import TrackRecordEntry
from openlocalweather.pipeline import _track_record_payload, observe_forecast_reach
from openlocalweather.verify.pipeline import derive_forecast_horizons

from tests.test_pipeline import log_entry, prediction


def _daily(reach_by_model: dict[str, int]) -> dict:
    arrays = {}
    for model, reach in reach_by_model.items():
        arrays[f"precipitation_sum_{model}"] = [0.0] * (reach + 1) + [None] * (7 - reach)
    return {"daily": {"time": [f"2026-09-{16 + i}" for i in range(8)], **arrays}}


def _degradation(code: str) -> RunDegradation:
    return RunDegradation(code=code, summary="x", detail="y")


def test_a_clean_run_records_each_model_that_had_a_value():
    daily = _daily({"gfs_seamless": 7, "ukmo_seamless": 5})
    reach = observe_forecast_reach(
        daily, [], met_service_present=False, met_service_day3_present=False,
        met_service_model_id="kenya_met",
    )
    assert reach == {"gfs_seamless": 7, "ukmo_seamless": 5}
    assert "ecmwf_ifs025" not in reach, "no value at any lead is absence, not zero"


def test_a_failed_or_unreadable_seven_day_fetch_records_nothing():
    daily = _daily({m: 7 for m in MODELS})
    for code in (DEGRADATION_EXTENDED_OUTLOOK, DEGRADATION_DAILY_GUIDANCE_UNREADABLE):
        assert observe_forecast_reach(
            daily, [_degradation(code)], met_service_present=False,
            met_service_day3_present=False, met_service_model_id="kenya_met",
        ) is None
    assert observe_forecast_reach(
        {}, [], met_service_present=False, met_service_day3_present=False,
        met_service_model_id="kenya_met",
    ) is None


def test_an_unrelated_degradation_does_not_void_the_observation():
    daily = _daily({"gfs_seamless": 7})
    reach = observe_forecast_reach(
        daily, [_degradation(DEGRADATION_METAR)], met_service_present=False,
        met_service_day3_present=False, met_service_model_id="kenya_met",
    )
    assert reach == {"gfs_seamless": 7}


def test_the_met_service_is_the_reach_of_what_was_extracted():
    daily = _daily({"gfs_seamless": 7})
    kw = dict(met_service_model_id="kenya_met")
    assert observe_forecast_reach(daily, [], met_service_present=True, met_service_day3_present=True, **kw)["kenya_met"] == 3
    assert observe_forecast_reach(daily, [], met_service_present=True, met_service_day3_present=False, **kw)["kenya_met"] == 0
    assert "kenya_met" not in observe_forecast_reach(daily, [], met_service_present=False, met_service_day3_present=False, **kw)


def test_the_horizon_is_the_maximum_over_clean_runs_and_a_short_day_cannot_retract_it():
    # ukmo read Day+6 on 2026-09-15 and Day+5 on 2026-09-16 (measured in
    # item 150); the record must say 6. A degraded day stores None and
    # contributes nothing; an entry from before the field shipped likewise.
    #
    # ORDER MATTERS FOR THE GUARD: the SHORTER reading is the LATER day.
    # The first version of this test had them the other way round, and a
    # mutation that kept the latest reading instead of the maximum passed
    # it — the guard had never been seen to fail.
    today = date(2026, 9, 17)
    logs = {}
    newest_first = [None, {"ukmo_seamless": 5}, {"ukmo_seamless": 6, "gfs_seamless": 7}, {}]
    for offset, reach in enumerate(newest_first):
        d = today - timedelta(days=offset + 1)
        entry = log_entry(d, day0=[prediction(model="gfs_seamless", rain=True)])
        entry.forecast_reach = reach
        logs[d] = entry

    horizons = derive_forecast_horizons(lambda d: logs.get(d), min(logs), today)
    assert horizons == {"ukmo_seamless": 6, "gfs_seamless": 7}


def test_the_stored_horizon_does_not_reach_the_prompt_before_step_3():
    # Found by the CLI drive: the row's new field went into MODEL TRACK
    # RECORD as 36 nulls through model_dump. Step 3 decides the wording.
    entry = TrackRecordEntry(model="gfs_seamless", lead_time_days=7, forecast_horizon_days=7)
    (row,) = _track_record_payload([entry], ["gfs_seamless"])
    assert "forecast_horizon_days" not in row
    assert row["model"] == "gfs_seamless"


# ---------------------------------------------------------------------------
# Step 3: the label, and the review's two kinds of zero
# ---------------------------------------------------------------------------

from openlocalweather.pipeline import _merge_skill_summaries, beyond_reach_summary, forecast_horizons_of  # noqa: E402
from openlocalweather.review import build_weekly_review  # noqa: E402
from openlocalweather.models import DailyActual, ModelPredictionsByLead  # noqa: E402
from tests.test_pipeline import actual  # noqa: E402


def _row(model, lead, horizon=None, summary=None):
    return TrackRecordEntry(
        model=model, lead_time_days=lead, forecast_horizon_days=horizon, skill_profile_summary=summary
    )


def test_a_row_beyond_its_reach_is_labelled_by_code_and_the_model_text_is_ignored():
    stale = "Insufficient data yet to characterize performance at Day+7."
    beyond = _row("icon_seamless", 7, horizon=6, summary=stale)
    within = _row("gfs_seamless", 7, horizon=7, summary=stale)
    unknown = _row("kenya_met", 7, horizon=None, summary=stale)
    _merge_skill_summaries(
        [beyond, within, unknown],
        {
            ("icon_seamless", 7): "Runs warm at Day+7.",   # never had a result; must not land
            ("gfs_seamless", 7): "Runs warm at Day+7.",
        },
    )
    assert beyond.skill_profile_summary == beyond_reach_summary(6)
    assert "Day+6" in beyond.skill_profile_summary and "yet" not in beyond.skill_profile_summary
    assert within.skill_profile_summary == "Runs warm at Day+7."
    assert unknown.skill_profile_summary == stale, "no horizon, no claim — the old text stands"


def test_the_label_persists_when_the_model_returns_nothing_for_the_pair():
    row = _row("ukmo_seamless", 7, horizon=5, summary="Insufficient data yet ...")
    _merge_skill_summaries([row], {})
    assert row.skill_profile_summary == beyond_reach_summary(5)


def test_horizons_are_read_off_the_rows():
    rows = [_row("icon_seamless", 0, 6), _row("icon_seamless", 7, 6), _row("gfs_seamless", 0, None)]
    assert forecast_horizons_of(rows) == {"icon_seamless": 6}


def _review_with_short_model(horizons):
    today = date(2026, 9, 17)
    logs, actuals = {}, {}
    for back in range(1, 13):
        d = today - timedelta(days=back)
        entry = log_entry(d, day0=[
            prediction(model="alpha", rain=True),
            prediction(model="beta", rain=True),
            prediction(model="short", rain=True),
        ])
        entry.model_predictions = ModelPredictionsByLead(
            day0=entry.model_predictions.day0,
            day3=[prediction(model="alpha", rain=True), prediction(model="beta", rain=True)],
        )
        logs[d] = entry
        actuals[d] = actual(rain=True)
    return build_weekly_review(
        log_lookup=lambda d: logs.get(d),
        actuals=actuals,
        all_log_dates=sorted(logs),
        today=today,
        models=["alpha", "beta", "short"],
        lead_times_days=[0, 3],
        forecast_horizons=horizons,
    )


def test_the_review_names_a_model_beyond_its_reach_instead_of_promising_data():
    with_horizon = _review_with_short_model({"short": 0})
    assert "short does not forecast at Day+3" in with_horizon.data_sufficiency
    assert "short has no verified checks at Day+3 yet" not in with_horizon.data_sufficiency

    without = _review_with_short_model(None)
    assert "short has no verified checks at Day+3 yet" in without.data_sufficiency
    assert "does not forecast" not in without.data_sufficiency


def test_a_lead_nobody_reaches_is_not_called_unverified():
    # alpha and beta reach Day+3 and are scored there, so no gap finding;
    # `short` beyond its reach must not create one either way.
    review = _review_with_short_model({"short": 0})
    assert not [f for f in review.findings if f.kind == "gap"]
