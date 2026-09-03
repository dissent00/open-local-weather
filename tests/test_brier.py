"""ROADMAP item 58 — scoring the probability, not the guess.

A boolean pays a confident wrong call and an honest hedge exactly the same,
which is why rule 7 has to ask for restraint in English: the ledger does not
reward it. A proper scoring rule pays for it arithmetically.

LOWER IS BETTER HERE, and that inversion is the thing most likely to be
displayed backwards. Every accuracy figure this project publishes is a
percentage where higher is better; Brier is a squared error where 0 is
perfect. The tests below pin the direction as well as the value.
"""

import pytest

from openlocalweather.verify.brier import (
    brier_score,
    brier_skill_score,
    mean_brier,
)


# --- the score itself ------------------------------------------------------


def test_a_confident_correct_call_scores_zero():
    assert brier_score(1.0, True) == 0.0
    assert brier_score(0.0, False) == 0.0


def test_a_confident_wrong_call_scores_the_worst_possible():
    assert brier_score(1.0, False) == 1.0
    assert brier_score(0.0, True) == 1.0


def test_a_coin_flip_scores_the_same_either_way():
    """The point of the whole exercise. An honest 50% is paid 0.25 whatever
    happens, while a confident guess is paid 0 or 1 — so over many days,
    claiming certainty you do not have costs more than admitting doubt."""
    assert brier_score(0.5, True) == 0.25
    assert brier_score(0.5, False) == 0.25


def test_hedging_beats_being_confidently_wrong():
    """Stated as an inequality because this is the incentive the item exists
    to create, and an implementation that got the sign backwards would still
    pass a test that only checked magnitudes."""
    assert brier_score(0.7, False) < brier_score(1.0, False)
    assert brier_score(0.3, True) < brier_score(0.0, True)


def test_percentages_are_rejected_rather_than_silently_misread():
    """70 is not a probability. Taken as one it scores 4761 — a number that
    would sail through any aggregation and poison every mean it touched."""
    with pytest.raises(ValueError):
        brier_score(70, True)
    with pytest.raises(ValueError):
        brier_score(-0.1, True)


# --- aggregation -----------------------------------------------------------


def test_the_mean_skips_days_with_no_probability():
    """Most stored days have none — the field started being recorded on
    2026-09-03. A missing probability is not a 0.5 guess and must not be
    averaged in as one."""
    assert mean_brier([0.0, None, 1.0]) == 0.5
    assert mean_brier([None, None]) is None
    assert mean_brier([]) is None


# --- the skill score, which is the interpretable one -----------------------


def test_skill_is_zero_when_a_forecast_only_matches_climatology():
    """Item 57's lesson applied here: a Brier of 0.2 is good or bad depending
    entirely on the base rate, so the raw number is not a claim. The skill
    score says how much better than the trivial reference it is."""
    assert brier_skill_score(0.2, 0.2) == 0.0


def test_skill_is_one_for_a_perfect_forecast():
    assert brier_skill_score(0.0, 0.25) == 1.0


def test_skill_goes_NEGATIVE_when_a_forecast_is_worse_than_climatology():
    """The finding this makes possible, and the reason it is not clamped. Item
    57 measured two of five models losing to persistence on a boolean; a
    negative skill score is the same result stated in a way that cannot be
    read as merely 'less good'."""
    # approx, not exact: 1 - 0.4/0.25 lands on -0.6000000000000001 in
    # binary floating point. The sign and magnitude are the claim.
    assert brier_skill_score(0.4, 0.25) == pytest.approx(-0.6)


def test_skill_against_a_perfect_reference_is_undefined_not_infinite():
    """A reference that is never wrong leaves nothing to improve on, and the
    division would blow up. None, not a number."""
    assert brier_skill_score(0.1, 0.0) is None
    assert brier_skill_score(0.1, None) is None
    assert brier_skill_score(None, 0.25) is None


# ---------------------------------------------------------------------------
# Wired into the scoring path
# ---------------------------------------------------------------------------

from datetime import date

from openlocalweather.models import DailyActual, ModelPrediction
from openlocalweather.verify.scoring import score_prediction


def test_a_prediction_with_a_probability_is_brier_scored():
    """80% and it rained: (0.8 - 1)**2 = 0.04."""
    s = score_prediction(
        ModelPrediction(model="gfs_seamless", rain=True, rain_probability_pct=80),
        DailyActual(rain=True),
        0,
    )
    assert s.rain_brier == pytest.approx(0.04)


def test_the_percentage_is_converted_exactly_once():
    """The record stores percentages and brier_score takes probabilities. If
    the conversion were missed, 80 would score 6241 and pass silently into
    every mean it touched."""
    s = score_prediction(
        ModelPrediction(model="gfs_seamless", rain=False, rain_probability_pct=0),
        DailyActual(rain=False),
        0,
    )
    assert s.rain_brier == 0.0


def test_a_prediction_with_no_probability_scores_none_not_a_guess():
    """21 of the 24 stored days have no probability. None keeps them out of
    the mean; a default of 0.5 would invent a hedge nobody made."""
    s = score_prediction(
        ModelPrediction(model="gfs_seamless", rain=True),
        DailyActual(rain=True),
        0,
    )
    assert s.rain_correct is True
    assert s.rain_brier is None


def test_brier_is_scored_against_the_same_truth_as_the_boolean():
    """observed_convection(), not reanalysis rain alone. Two columns scored
    against two different truths would be incomparable, and the boolean's
    truth is the one the whole record is built on."""
    s = score_prediction(
        ModelPrediction(model="gfs_seamless", rain=False, rain_probability_pct=10),
        # Reanalysis dry, but the station saw rain — a wet day.
        DailyActual(rain=False, precipitation=True),
        0,
    )
    assert s.rain_correct is False
    assert s.rain_brier == pytest.approx(0.81)


def test_the_rolling_window_reports_brier_checks_separately(tmp_path):
    """A window can hold 30 scored days of which 3 carry a probability, and
    that is the situation for weeks to come. One count for both would imply
    the Brier figure rests on evidence it does not have."""
    from openlocalweather.verify.scoring import RollingWindowResult

    r = RollingWindowResult(
        checks_found=30, rain_pct=70.0, onset_err=None, wind_err=None,
        high_err=None, low_err=None, mslp_err=None,
        rain_brier=0.2, brier_checks=3,
    )
    assert r.checks_found == 30
    assert r.brier_checks == 3
