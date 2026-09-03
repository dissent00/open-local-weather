"""ROADMAP item 57 — what a real model has to beat.

Hand-computed expectations. These two are the yardsticks the published
accuracy figures are measured against, so a bug here would not look like a
bug: it would look like the models being better or worse than they are.
"""

from datetime import date

import pytest

from openlocalweather.baselines import (
    CLIMATOLOGY_MODEL_ID,
    PERSISTENCE_MODEL_ID,
    climatology_prediction,
    persistence_prediction,
)
from openlocalweather.models import DailyActual


def _a(rain: bool, high=28.0, low=18.0, wind=20.0, onset=None) -> DailyActual:
    return DailyActual(
        rain=rain, high_c=high, low_c=low, peak_wind_kmh=wind, onset_hour=onset
    )


# --- persistence ----------------------------------------------------------


def test_persistence_repeats_the_last_observation():
    p = persistence_prediction(_a(True, high=30.0, low=19.0, wind=25.0))
    assert p is not None
    assert p.model == PERSISTENCE_MODEL_ID
    assert p.rain is True
    assert p.high_c == 30.0
    assert p.low_c == 19.0
    assert p.wind_kmh == 25.0


def test_persistence_has_nothing_to_say_without_an_observation():
    """None, not a dry call. A baseline that invents a forecast out of missing
    data would accrue the same flattering fake score ModelPrediction.rain's
    docstring warns about — and this one is the yardstick, so it would move
    every comparison on the page."""
    assert persistence_prediction(None) is None


def test_persistence_carries_onset_only_at_day_zero():
    """The real models have no onset at Day+3 or Day+7 — extract_day_n_
    predictions_from_daily cannot produce one. A baseline that had one would
    be scored on a field its competitors are not."""
    with_onset = persistence_prediction(_a(True, onset="15:00"), include_onset=True)
    assert with_onset.onset == "15:00"

    without = persistence_prediction(_a(True, onset="15:00"), include_onset=False)
    assert without.onset is None


# --- climatology ----------------------------------------------------------


def test_climatology_calls_rain_when_the_record_is_mostly_wet():
    actuals = {
        date(2026, 8, 1): _a(True),
        date(2026, 8, 2): _a(True),
        date(2026, 8, 3): _a(False),
    }
    p = climatology_prediction(actuals, before=date(2026, 8, 4))
    assert p is not None
    assert p.model == CLIMATOLOGY_MODEL_ID
    assert p.rain is True


def test_climatology_calls_dry_when_the_record_is_mostly_dry():
    actuals = {
        date(2026, 8, 1): _a(False),
        date(2026, 8, 2): _a(False),
        date(2026, 8, 3): _a(True),
    }
    assert climatology_prediction(actuals, before=date(2026, 8, 4)).rain is False


def test_an_exact_half_is_dry():
    """A tie has to break somewhere and the choice must be recorded rather
    than discovered. Dry, because that is the direction that does NOT
    manufacture a rain warning out of a coin flip."""
    actuals = {date(2026, 8, 1): _a(True), date(2026, 8, 2): _a(False)}
    assert climatology_prediction(actuals, before=date(2026, 8, 3)).rain is False


def test_climatology_never_sees_the_day_it_is_forecasting_or_later():
    """The whole record would be worthless if a baseline could read the answer.
    `before` is exclusive."""
    actuals = {
        date(2026, 8, 1): _a(False),
        date(2026, 8, 2): _a(True),
        date(2026, 8, 3): _a(True),
    }
    # Only 08-01 is in scope, so dry.
    assert climatology_prediction(actuals, before=date(2026, 8, 2)).rain is False


def test_climatology_averages_the_numbers_it_has():
    actuals = {
        date(2026, 8, 1): _a(False, high=20.0, low=10.0, wind=10.0),
        date(2026, 8, 2): _a(False, high=30.0, low=20.0, wind=20.0),
    }
    p = climatology_prediction(actuals, before=date(2026, 8, 3))
    assert p.high_c == 25.0
    assert p.low_c == 15.0
    assert p.wind_kmh == 15.0


def test_climatology_ignores_missing_numbers_rather_than_treating_them_as_zero():
    actuals = {
        date(2026, 8, 1): DailyActual(rain=False, high_c=None, low_c=12.0),
        date(2026, 8, 2): DailyActual(rain=False, high_c=30.0, low_c=14.0),
    }
    p = climatology_prediction(actuals, before=date(2026, 8, 3))
    assert p.high_c == 30.0, "one missing high must not average in as 0"
    assert p.low_c == 13.0


def test_climatology_has_nothing_to_say_on_an_empty_record():
    assert climatology_prediction({}, before=date(2026, 8, 3)) is None


def test_climatology_never_carries_an_onset():
    """It has no view about WHEN. Scoring it on onset would compare it against
    models on a field it cannot answer."""
    actuals = {date(2026, 8, 1): _a(True, onset="15:00")}
    assert climatology_prediction(actuals, before=date(2026, 8, 2)).onset is None


# --- the ledger they live in ----------------------------------------------


def test_the_ids_match_the_ledger_s_own_list():
    """defaults.py keeps its own copy so it imports nothing. This is the
    assertion that stops the two drifting."""
    from openlocalweather.defaults import BASELINE_MODEL_IDS

    assert set(BASELINE_MODEL_IDS) == {PERSISTENCE_MODEL_ID, CLIMATOLOGY_MODEL_ID}


def test_baselines_are_scored():
    from openlocalweather.defaults import scored_models

    assert PERSISTENCE_MODEL_ID in scored_models()
    assert CLIMATOLOGY_MODEL_ID in scored_models()


def test_baselines_are_hidden_from_the_forecaster():
    """Scored and published, never in the forecaster's context — the same
    standing rule as the blend, for an adjacent reason. A yardstick handed to
    the forecaster reads as a sixth opinion, and persistence's only input is
    an observation the forecaster already holds."""
    from openlocalweather.defaults import models_visible_to_the_forecaster

    visible = models_visible_to_the_forecaster("kenya_met")
    assert PERSISTENCE_MODEL_ID not in visible
    assert CLIMATOLOGY_MODEL_ID not in visible
    assert "kenya_met" in visible, "a real peer model must still be visible"
    assert "ecmwf_ifs025" in visible


# ---------------------------------------------------------------------------
# ROADMAP item 58 — climatology as the Brier reference forecast
# ---------------------------------------------------------------------------


def test_climatology_emits_the_base_rate_as_a_probability():
    """Its boolean call throws away exactly the information a proper scoring
    rule needs. As a probability it becomes the canonical reference forecast —
    "the usual chance of rain here" — which is what a Brier skill score is
    measured against."""
    actuals = {
        date(2026, 8, 1): _a(True),
        date(2026, 8, 2): _a(True),
        date(2026, 8, 3): _a(False),
        date(2026, 8, 4): _a(False),
    }
    p = climatology_prediction(actuals, before=date(2026, 8, 5))
    assert p.rain_probability_pct == 50


def test_the_probability_and_the_boolean_can_disagree_and_that_is_correct():
    """At a 40% base rate the boolean says dry and the probability says 40 —
    two honest answers to two different questions. Forcing them to agree
    would throw away the distinction the item exists to capture."""
    actuals = {
        date(2026, 8, 1): _a(True),
        date(2026, 8, 2): _a(True),
        date(2026, 8, 3): _a(False),
        date(2026, 8, 4): _a(False),
        date(2026, 8, 5): _a(False),
    }
    p = climatology_prediction(actuals, before=date(2026, 8, 6))
    assert p.rain is False
    assert p.rain_probability_pct == 40


def test_persistence_has_no_probability_and_should_not_pretend_to():
    """It repeats an observation. An observation is certain about the day it
    describes and says nothing about the chance of another one, so inventing
    100 or 0 here would claim a confidence it never expressed — and would
    score terribly under Brier for a reason that is an artefact."""
    assert persistence_prediction(_a(True)).rain_probability_pct is None
