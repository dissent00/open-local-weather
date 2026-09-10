"""Weekly review: does it say useful things when the evidence supports them,
and stay quiet when it doesn't?

Both halves matter equally. A review that never speaks is useless; a review
that speaks off five days is actively harmful, because its findings are
designed to feed back into the daily prompt and would harden into received
wisdom.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)
from openlocalweather.defaults import REVIEW_MIN_CHECKS_FOR_COMPARISON
from openlocalweather.review import build_weekly_review, confidence_for

MODELS = ["good_model", "poor_model"]
TODAY = date(2026, 8, 20)


def entry(d: date, day0: list[ModelPrediction]) -> DailyLogEntry:
    return DailyLogEntry(
        date=d,
        rain_expected="x",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26/18",
        mslp_trend_24h="",
        synoptic_pattern="",
        narrative_markdown="n",
        model_predictions=ModelPredictionsByLead(day0=day0),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="t", llm_model="t", pipeline_version="0",
        ),
    )


def build_history(days: int, good_hits: int, poor_hits: int, high_bias: float = 0.0):
    """`days` consecutive scoreable days at Day+0. good_model gets the rain
    call right `good_hits` times, poor_model `poor_hits` times."""
    logs, actuals = {}, {}
    for i in range(days):
        d = TODAY - timedelta(days=i + 1)
        actual_rain = True
        actuals[d] = DailyActual(
            rain=actual_rain, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
        )
        logs[d] = entry(d, [
            ModelPrediction(
                model="good_model", rain=(i < good_hits), high_c=26.0 - high_bias, low_c=18.0
            ),
            ModelPrediction(model="poor_model", rain=(i < poor_hits), high_c=26.0, low_c=18.0),
        ])
    return logs, actuals


def review_of(logs, actuals, models=None):
    return build_weekly_review(
        log_lookup=lambda d: logs.get(d),
        actuals=actuals,
        all_log_dates=sorted(logs),
        today=TODAY,
        models=models or MODELS,
        lead_times_days=[0],
    )


# ---------------------------------------------------------------------------
# Confidence bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("checks,expected", [
    (0, "insufficient"), (4, "insufficient"),
    (5, "provisional"), (9, "provisional"),
    (10, "usable"), (29, "usable"),
    (30, "established"), (500, "established"),
])
def test_confidence_bands(checks, expected):
    assert confidence_for(checks) == expected


# ---------------------------------------------------------------------------
# Staying quiet
# ---------------------------------------------------------------------------


def test_says_nothing_on_a_thin_record_even_with_a_huge_apparent_gap():
    """The dangerous case. Eight days can easily show a 75-point spread that
    is pure chance; announcing it would plant a false belief the daily prompt
    then reasons from."""
    logs, actuals = build_history(days=8, good_hits=8, poor_hits=2)
    r = review_of(logs, actuals)

    assert r.findings == [], "must not rank models on 8 checks"
    cell = next(c for c in r.cells if c.model == "good_model")
    assert cell.rain_pct == 100.0, "the figure is still computed..."
    assert cell.confidence == "provisional", "...it just isn't trusted"


def test_sufficiency_statement_is_per_lead_time_and_always_present():
    """Day+0 gains a check daily; Day+7 cannot produce one for a week. A
    single blanket confidence would badly overstate the extended outlook."""
    logs, actuals = build_history(days=8, good_hits=6, poor_hits=4)
    r = build_weekly_review(
        log_lookup=lambda d: logs.get(d), actuals=actuals,
        all_log_dates=sorted(logs), today=TODAY,
        models=MODELS, lead_times_days=[0, 7],
    )
    assert "Day+0" in r.data_sufficiency
    assert "Day+7" in r.data_sufficiency
    assert "not enough to say anything" in r.data_sufficiency, "Day+7 has no checks here"


# ---------------------------------------------------------------------------
# Speaking up
# ---------------------------------------------------------------------------


def test_ranks_models_once_the_evidence_supports_it():
    logs, actuals = build_history(days=30, good_hits=27, poor_hits=9)
    r = review_of(logs, actuals)

    ranking = [f for f in r.findings if f.kind == "ranking"]
    assert len(ranking) == 1
    assert "good_model" in ranking[0].claim
    assert "poor_model" in ranking[0].claim
    assert ranking[0].confidence == "established"
    # The evidence travels with the claim, so it can be weighed not trusted.
    assert "27/30" in ranking[0].evidence and "9/30" in ranking[0].evidence


def test_declines_to_rank_when_models_are_genuinely_close():
    """Not the same as having no data. With plenty of checks and a narrow
    spread, the honest finding is that nothing separates them."""
    logs, actuals = build_history(days=30, good_hits=20, poor_hits=18)
    r = review_of(logs, actuals)

    ranking = [f for f in r.findings if f.kind == "ranking"]
    assert len(ranking) == 1
    assert "no model is meaningfully better" in ranking[0].claim
    assert "noise floor" in ranking[0].evidence


def test_detects_systematic_temperature_bias():
    # good_model forecasts highs 2C below what actually happens.
    logs, actuals = build_history(days=30, good_hits=15, poor_hits=15, high_bias=2.0)
    r = review_of(logs, actuals)

    bias = [f for f in r.findings if f.kind == "bias" and "good_model" in f.claim]
    assert bias, "a consistent 2C error across 30 checks should be reported"
    assert "under-forecasts" in bias[0].claim, "actual came in above forecast"
    assert "daytime highs" in bias[0].claim
    assert bias[0].checks == 30


def test_bias_below_the_threshold_is_not_reported():
    """Half a degree is scatter, not a finding."""
    logs, actuals = build_history(days=30, good_hits=15, poor_hits=15, high_bias=0.4)
    r = review_of(logs, actuals)
    assert [f for f in r.findings if f.kind == "bias"] == []


def test_names_a_lead_time_that_has_never_been_verified():
    logs, actuals = build_history(days=30, good_hits=20, poor_hits=18)
    r = build_weekly_review(
        log_lookup=lambda d: logs.get(d), actuals=actuals,
        all_log_dates=sorted(logs), today=TODAY,
        models=MODELS, lead_times_days=[0, 7],
    )
    gaps = [f for f in r.findings if f.kind == "gap"]
    assert len(gaps) == 1
    assert "Day+7 has never been verified" in gaps[0].claim


def test_every_finding_carries_evidence_and_confidence():
    """The property that makes findings safe to feed into the daily prompt."""
    logs, actuals = build_history(days=30, good_hits=27, poor_hits=9, high_bias=2.0)
    r = review_of(logs, actuals)
    assert r.findings
    for f in r.findings:
        assert f.claim and f.evidence, f"finding without evidence: {f}"
        assert f.confidence in {"insufficient", "provisional", "usable", "established"}
        assert f.checks >= 0


def test_uneven_model_coverage_is_reported_not_averaged_away():
    """Not every model reaches every lead time — UKMO's horizon ends around
    7.2 days and ICON's around 7.5 — so at Day+7 some models genuinely have
    fewer checks. Taking the best-covered model's count and calling it "per
    model" would overstate coverage for precisely the models that have least.
    This shows up on real Kisumu data in the very first week.
    """
    logs, actuals = build_history(days=12, good_hits=8, poor_hits=6)
    # poor_model stops reaching this lead time half way through the record.
    for i, d in enumerate(sorted(logs, reverse=True)):
        if i >= 6:
            preds = logs[d].model_predictions.day0
            logs[d].model_predictions.day0 = [p for p in preds if p.model != "poor_model"]

    r = review_of(logs, actuals)
    counts = {c.model: c.checks for c in r.cells}
    assert counts == {"good_model": 12, "poor_model": 6}

    assert "12 check(s) per model" not in r.data_sufficiency, "must not claim the richest count"
    assert "6 check(s) per model" in r.data_sufficiency, "the weakest model sets confidence"
    assert "Coverage at Day+0 is uneven" in r.data_sufficiency
    assert "poor_model has fewer" in r.data_sufficiency
    assert "not like-for-like" in r.data_sufficiency


def test_a_newly_added_model_does_not_erase_the_existing_record():
    """Regression. When the local met service was first scored it had zero
    checks, and because the weakest model set the headline, Day+0 reported
    "0 check(s) per model — not enough to say anything" while eight days of
    scored forecasts for five other models sat right there.

    Never scored and scored-less are different claims, and only the second
    should move the confidence figure."""
    logs, actuals = build_history(days=8, good_hits=6, poor_hits=5)
    r = build_weekly_review(
        log_lookup=lambda d: logs.get(d), actuals=actuals,
        all_log_dates=sorted(logs), today=TODAY,
        models=[*MODELS, "brand_new_model"], lead_times_days=[0],
    )
    assert "8 check(s) per model" in r.data_sufficiency
    assert "0 check(s) per model" not in r.data_sufficiency
    # The newcomer is named rather than quietly folded in.
    assert "brand_new_model" in r.data_sufficiency
    assert "no verified checks at Day+0 yet" in r.data_sufficiency
    assert "not included in the figure above" in r.data_sufficiency


# ---------------------------------------------------------------------------
# ROADMAP item 57 — the gate that actually matters
#
# A ranking says which model is best of those present. It cannot say whether
# being best is worth anything, and on this project's own record at Day+0 it
# often is not: repeating yesterday's weather beat two of the five numerical
# models. Until the review says so, "ECMWF is the strongest rain caller here"
# is a claim a reader cannot weigh.
# ---------------------------------------------------------------------------

from openlocalweather.baselines import CLIMATOLOGY_MODEL_ID, PERSISTENCE_MODEL_ID


def build_history_with_baseline(days: int, good_hits: int, poor_hits: int, base_hits: int):
    """As build_history, plus a persistence row hitting `base_hits` times."""
    logs, actuals = {}, {}
    for i in range(days):
        d = TODAY - timedelta(days=i + 1)
        actuals[d] = DailyActual(
            rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
        )
        logs[d] = entry(d, [
            ModelPrediction(model="good_model", rain=(i < good_hits), high_c=26.0, low_c=18.0),
            ModelPrediction(model="poor_model", rain=(i < poor_hits), high_c=26.0, low_c=18.0),
            ModelPrediction(
                model=PERSISTENCE_MODEL_ID, rain=(i < base_hits), high_c=26.0, low_c=18.0
            ),
        ])
    return logs, actuals


def _review_with_baseline(logs, actuals):
    return build_weekly_review(
        log_lookup=lambda d: logs.get(d),
        actuals=actuals,
        all_log_dates=sorted(logs),
        today=TODAY,
        models=[*MODELS, PERSISTENCE_MODEL_ID],
        lead_times_days=[0],
    )


def test_a_baseline_is_never_ranked_as_though_it_were_a_model():
    """The nonsense this prevents: "good_model is the strongest rain caller
    and persistence the weakest" compares guidance against a yardstick as
    though they were peers. On the real record climatology sits at the bottom
    of Day+0, so this would have started happening the moment the backfill
    landed."""
    logs, actuals = build_history_with_baseline(30, good_hits=27, poor_hits=9, base_hits=5)
    r = _review_with_baseline(logs, actuals)

    ranking = [f for f in r.findings if f.kind == "ranking"]
    assert len(ranking) == 1
    assert PERSISTENCE_MODEL_ID not in ranking[0].claim
    assert PERSISTENCE_MODEL_ID not in ranking[0].evidence


def test_a_baseline_is_not_reported_as_having_a_bias():
    """"persistence systematically under-forecasts peak wind" is a statement
    about yesterday's weather, not about a forecast system."""
    logs, actuals = build_history_with_baseline(30, good_hits=27, poor_hits=9, base_hits=5)
    r = _review_with_baseline(logs, actuals)

    assert not [f for f in r.findings if f.kind == "bias" and PERSISTENCE_MODEL_ID in f.claim]


def test_it_says_when_the_models_clear_the_bar():
    logs, actuals = build_history_with_baseline(30, good_hits=27, poor_hits=24, base_hits=5)
    r = _review_with_baseline(logs, actuals)

    baseline = [f for f in r.findings if f.kind == "baseline"]
    assert len(baseline) == 1
    assert "good_model" in baseline[0].claim
    assert "persistence" in baseline[0].evidence


def test_it_says_when_no_model_clears_the_bar():
    """The finding this whole item exists for. Every model at or below the
    trivial rule, and the ranking alone would still announce a winner."""
    logs, actuals = build_history_with_baseline(30, good_hits=15, poor_hits=12, base_hits=27)
    r = _review_with_baseline(logs, actuals)

    baseline = [f for f in r.findings if f.kind == "baseline"]
    assert len(baseline) == 1
    assert "no model" in baseline[0].claim.lower()
    assert "persistence" in baseline[0].claim or "persistence" in baseline[0].evidence


def test_beating_the_bar_by_less_than_the_noise_floor_does_not_count():
    """Same gate as the ranking: at n=30 a few points is scatter, not skill."""
    logs, actuals = build_history_with_baseline(30, good_hits=22, poor_hits=21, base_hits=20)
    r = _review_with_baseline(logs, actuals)

    baseline = [f for f in r.findings if f.kind == "baseline"]
    assert len(baseline) == 1
    assert "no model" in baseline[0].claim.lower()


def test_no_baseline_in_the_record_means_no_finding_rather_than_a_pass():
    """Absence of a yardstick is not evidence that the bar was cleared — the
    same rule the rest of this project follows about missing data."""
    logs, actuals = build_history(days=30, good_hits=27, poor_hits=9)
    r = review_of(logs, actuals)

    assert not [f for f in r.findings if f.kind == "baseline"]


# ---------------------------------------------------------------------------
# Brier on the skill table — ROADMAP item 58
# ---------------------------------------------------------------------------


def brier_history(days: int, model_pct: int | None, climatology_pct: int, wet: bool = True):
    """`days` scoreable days at Day+0 where every day has the same outcome and
    each model states the same probability every day.

    Fixing the probability makes the expected mean Brier a hand-checkable
    square rather than something only the code under test can produce.
    """
    logs, actuals = {}, {}
    for i in range(days):
        d = TODAY - timedelta(days=i + 1)
        actuals[d] = DailyActual(
            rain=wet, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
        )
        logs[d] = entry(d, [
            ModelPrediction(
                model="good_model",
                rain=wet,
                rain_probability_pct=model_pct,
                high_c=26.0,
                low_c=18.0,
            ),
            ModelPrediction(
                model="climatology",
                rain=wet,
                rain_probability_pct=climatology_pct,
                high_c=26.0,
                low_c=18.0,
            ),
        ])
    return logs, actuals


def brier_review_of(logs, actuals):
    return build_weekly_review(
        log_lookup=lambda d: logs.get(d),
        actuals=actuals,
        all_log_dates=sorted(logs),
        today=TODAY,
        models=["good_model", "climatology"],
        lead_times_days=[0],
    )


def cell_for(review, model: str):
    return next(c for c in review.cells if c.model == model)


def test_mean_brier_is_the_mean_of_the_daily_squared_errors():
    # 90% every day on a day that rains: (0.9 - 1)^2 == 0.01, ten times over.
    logs, actuals = brier_history(days=10, model_pct=90, climatology_pct=50)
    cell = cell_for(brier_review_of(logs, actuals), "good_model")

    assert cell.mean_rain_brier == pytest.approx(0.01)
    assert cell.brier_checks == 10


def test_brier_skill_is_measured_against_climatology():
    # Model at 0.01, climatology at 0.25 -> 1 - 0.01/0.25 == 0.96.
    logs, actuals = brier_history(days=10, model_pct=90, climatology_pct=50)
    r = brier_review_of(logs, actuals)

    assert cell_for(r, "good_model").rain_brier_skill == pytest.approx(0.96)
    # The reference measured against itself is exactly 0.0 by construction,
    # which is the honest reading: climatology is precisely as good as
    # climatology. It is not missing data and must not be rendered as such.
    assert cell_for(r, "climatology").rain_brier_skill == pytest.approx(0.0)


def test_brier_skill_goes_negative_when_the_model_loses_to_climatology():
    """Not clamped at zero. Item 57 measured two of five models losing to
    persistence on the boolean, and a negative number states that in a form
    that cannot be misread as merely 'less good'."""
    logs, actuals = brier_history(days=10, model_pct=10, climatology_pct=50)
    cell = cell_for(brier_review_of(logs, actuals), "good_model")

    # 0.81 against 0.25 -> 1 - 3.24 == -2.24.
    assert cell.rain_brier_skill == pytest.approx(-2.24)


def test_brier_checks_is_counted_separately_from_checks():
    """The display trap this field exists to prevent: a cell can be scored on
    every day of the record and carry a probability on none of them, and one
    count shown for both would claim evidence that does not exist."""
    logs, actuals = brier_history(days=10, model_pct=None, climatology_pct=50)
    cell = cell_for(brier_review_of(logs, actuals), "good_model")

    assert cell.checks == 10
    assert cell.brier_checks == 0
    assert cell.mean_rain_brier is None
    assert cell.rain_brier_skill is None


def test_no_reference_means_no_skill_score_rather_than_a_wrong_one():
    """The prompt path reviews only the forecaster's own models, so
    climatology is absent from `models` there and there is no reference to
    measure against. Every other Brier figure must still be present."""
    logs, actuals = brier_history(days=10, model_pct=90, climatology_pct=50)
    r = build_weekly_review(
        log_lookup=lambda d: logs.get(d),
        actuals=actuals,
        all_log_dates=sorted(logs),
        today=TODAY,
        models=["good_model"],
        lead_times_days=[0],
    )
    cell = cell_for(r, "good_model")

    assert cell.mean_rain_brier == pytest.approx(0.01)
    assert cell.brier_checks == 10
    assert cell.rain_brier_skill is None


def test_brier_skill_is_paired_on_the_days_both_forecasts_spoke():
    """The reference and the model rarely cover the same days.

    Here the model states a probability on all 10 days and climatology on the
    5 most recent. An unpaired ratio would divide the model's 10-day mean by
    the reference's 5-day mean and call the result skill. The paired one
    compares both over the 5 shared days, which is the only comparison a
    skill score can honestly make.
    """
    logs, actuals = {}, {}
    for i in range(10):
        d = TODAY - timedelta(days=i + 1)
        # The older half of the record is WET, the recent half DRY, so an
        # unpaired mean differs from a paired one by more than rounding.
        wet = i >= 5
        actuals[d] = DailyActual(
            rain=wet, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
        )
        logs[d] = entry(d, [
            ModelPrediction(model="good_model", rain=wet, rain_probability_pct=90,
                            high_c=26.0, low_c=18.0),
            ModelPrediction(model="climatology", rain=wet,
                            rain_probability_pct=50 if i < 5 else None,
                            high_c=26.0, low_c=18.0),
        ])

    cell = cell_for(brier_review_of(logs, actuals), "good_model")

    # The model's own Brier still spans all ten days: five at (0.9-0)^2 = 0.81
    # and five at (0.9-1)^2 = 0.01, mean 0.41.
    assert cell.mean_rain_brier == pytest.approx(0.41)
    assert cell.brier_checks == 10

    # The skill score spans only the five shared days, all of them dry, where
    # the model scored 0.81 and climatology 0.25: 1 - 0.81/0.25 == -2.24.
    # Unpaired it would have been 1 - 0.41/0.25 == -0.64, a materially
    # different claim about the same forecasts.
    assert cell.brier_skill_checks == 5
    assert cell.rain_brier_skill == pytest.approx(-2.24)


def test_sufficiency_does_not_deny_a_ranking_the_review_itself_publishes():
    """ROADMAP item 85. The two counts are both right and mean different
    things; the CONCLUSION drawn from one of them is what contradicts.

    `_describe_sufficiency` takes the minimum over every SCORED model, which
    is deliberate — the weakest model's coverage is the honest headline, and
    the comment above it records the regression that established that. The
    ranking gate takes the minimum of the TWO MODELS IT COMPARED, having
    already excluded anything under REVIEW_MIN_CHECKS_FOR_COMPARISON.

    So a thin third model drags the sufficiency band down to "provisional",
    whose wording asserts the record is "not yet enough to rank models
    against each other" — while a gated ranking for that same lead sits in
    the same payload. Measured on the real 2026-09-08 prompt: Day+3 carried
    "9 check(s) per model — directional only, not yet enough to rank" beside
    a ranking finding marked usable on 25 checks.

    The forecaster is told a present finding is authoritative AND that
    data_sufficiency must be reflected in the Confidence Notes. It cannot
    honour both, so it picks — which is the judgement this design exists to
    take away from it.
    """
    logs, actuals = build_history(days=14, good_hits=13, poor_hits=4)
    # A third model, scored but thin: present for only the last few days, so
    # it lands below the comparison floor without ever being unscored.
    for i, d in enumerate(sorted(logs, reverse=True)):
        if i < 5:
            logs[d].model_predictions.day0 = [
                *logs[d].model_predictions.day0,
                ModelPrediction(model="thin_model", rain=True, high_c=26.0, low_c=18.0),
            ]

    r = review_of(logs, actuals, models=[*MODELS, "thin_model"])
    counts = {c.model: c.checks for c in r.cells if c.lead_time_days == 0}
    assert counts["thin_model"] < REVIEW_MIN_CHECKS_FOR_COMPARISON, counts
    assert counts["good_model"] >= REVIEW_MIN_CHECKS_FOR_COMPARISON, counts

    ranked = [
        f for f in r.findings
        if f.kind == "ranking" and "At Day+0" in f.claim
        and "is the strongest rain caller" in f.claim
    ]
    assert ranked, "the setup must produce a ranking, or this proves nothing"

    assert "not yet enough to rank models against each other" not in r.data_sufficiency, (
        f"the review publishes {ranked[0].claim!r} and simultaneously tells the "
        f"forecaster the record cannot rank models at this lead"
    )


def build_cloud_history(days: int, cloud_bias: float):
    """`days` scoreable Day+0 rows where good_model reads the sky and
    poor_model forecasts `cloud_bias` points less cloud than there was."""
    logs, actuals = {}, {}
    for i in range(days):
        d = TODAY - timedelta(days=i + 1)
        actuals[d] = DailyActual(
            rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0,
            cloud_cover_pct=60.0,
        )
        logs[d] = entry(d, [
            ModelPrediction(model="good_model", rain=True, high_c=26.0, low_c=18.0, cloud_cover_pct=60.0),
            ModelPrediction(
                model="poor_model", rain=True, high_c=26.0, low_c=18.0,
                cloud_cover_pct=60.0 - cloud_bias,
            ),
        ])
    return logs, actuals


def test_a_model_that_cannot_read_the_sky_is_named():
    """The point of scoring cloud, and what the operator asked for on
    2026-09-10: "as the data history improves it should begin to trust the
    better models for cloud coverage... if 3 good models say AM clouds and 2
    that aren't so good at that metric say none, we can say partly cloudy".

    That judgement needs a NAMED, EVIDENCED finding the forecaster reads —
    the same surface that already says which model runs warm. Measured that
    morning: two of the five said there was no dawn cloud at all on a
    morning that started partly cloudy, and nothing in the record could say
    so, because until now cloud was stored and never scored.
    """
    logs, actuals = build_cloud_history(days=30, cloud_bias=35.0)
    r = review_of(logs, actuals)

    bias = [f for f in r.findings if f.kind == "bias" and "poor_model" in f.claim]
    assert bias, "a consistent 35-point cloud error across 30 checks should be reported"
    assert "cloud cover" in bias[0].claim
    # Errors are observed minus forecast, so a model forecasting LESS cloud
    # than there was comes in under — the same convention as every other row.
    assert "under-forecasts" in bias[0].claim
    assert bias[0].checks == 30

    # And the model that read it right is not accused of anything.
    assert not [f for f in r.findings if f.kind == "bias" and "good_model" in f.claim]


def test_a_few_points_of_cloud_is_scatter_not_a_finding():
    """Cloud is the noisiest field in the record — on 2026-09-10 the five
    models spanned 0 to 98 percent on the same morning. A threshold that
    fired on small differences would fill the review with findings about
    nothing."""
    logs, actuals = build_cloud_history(days=30, cloud_bias=5.0)
    r = review_of(logs, actuals)
    assert [f for f in r.findings if f.kind == "bias"] == []


def test_a_cloud_finding_cannot_borrow_the_rain_record_s_sample_size():
    """cloud_cover_pct started being stored on 2026-09-09, and every other
    field has months of rows. So a model can hold 30 scored checks of which
    3 say anything about the sky, and the bias loop reads `c.checks` — which
    would put "across 30 checks" under a mean taken over 3.

    THE SAME LESSON brier_checks ALREADY LEARNED, one field later: a count
    presented for the whole row overstates the evidence behind any column
    that is thinner than the row. Here it would overstate it tenfold, in the
    one sentence a forecaster would act on.
    """
    logs, actuals = build_cloud_history(days=30, cloud_bias=35.0)
    # Strip the sky from all but the three most recent days. Rain and
    # temperature still score on all 30, exactly as in the real record.
    for i, d in enumerate(sorted(logs, reverse=True)):
        if i < 3:
            continue
        actuals[d] = DailyActual(
            rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
        )
        for p in logs[d].model_predictions.day0:
            p.cloud_cover_pct = None

    r = review_of(logs, actuals)
    cloud = [f for f in r.findings if f.kind == "bias" and "cloud cover" in f.claim]

    assert not cloud, (
        "three days of sky is below the comparison floor and must not be "
        "reported at all, let alone as thirty checks"
    )
