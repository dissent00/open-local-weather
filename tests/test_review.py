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


def review_of(logs, actuals):
    return build_weekly_review(
        log_lookup=lambda d: logs.get(d),
        actuals=actuals,
        all_log_dates=sorted(logs),
        today=TODAY,
        models=MODELS,
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
