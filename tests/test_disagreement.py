"""ROADMAP item 104, C2's third trigger."""

import pytest

from openlocalweather.disagreement import (
    DISAGREEMENT_HIGH_EXCEEDED,
    DISAGREEMENT_RAIN_WHILE_DRY,
    ObservedSoFar,
    StandingCall,
    observation_disagreements,
)


def test_rain_observed_while_the_call_said_dry():
    """The clearest contradiction available, and the one that matters most:
    `rain` is the scored boolean."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=True, high_c=None),
    )

    assert out == [DISAGREEMENT_RAIN_WHILE_DRY]


def test_no_rain_YET_does_not_contradict_a_rain_call():
    """THE ASYMMETRY, and it governs every test in this module.

    A mid-day observation can only ever prove a forecast too LOW, never too
    high — rain that has fallen has fallen, and a day that has not rained yet
    may still. Treating "no rain so far" as a contradiction would re-forecast
    every dry morning of every wet day.
    """
    out = observation_disagreements(
        StandingCall(rain=True, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=None),
    )

    assert out == []


def test_the_observed_high_has_already_passed_the_forecast_high():
    """A maximum only rises, so an observation above the call settles it."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=33.0),
    )

    assert out == [DISAGREEMENT_HIGH_EXCEEDED]


def test_a_high_below_the_call_is_not_a_contradiction():
    """Same asymmetry: the day is not over."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=24.0),
    )

    assert out == []


def test_the_margin_must_clear_the_station_offset():
    """Sized against two MEASURED quantities rather than picked.

    The station reads +0.43 C against the reanalysis on average (item 44),
    and the Day+0 high error is a few tenths. A margin at or below either
    would fire on the instrument rather than on the weather.
    """
    from openlocalweather.disagreement import TEMP_CONTRADICTION_MARGIN_C

    assert TEMP_CONTRADICTION_MARGIN_C > 0.43

    just_under = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=30.0 + TEMP_CONTRADICTION_MARGIN_C - 0.1),
    )
    assert just_under == []


def test_absent_observations_contradict_nothing():
    """A station that reported nothing is not a station reporting agreement —
    the same rule every absent input in this project follows."""
    assert observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=None, high_c=None),
    ) == []


def test_absent_standing_call_contradicts_nothing():
    """Nothing to disagree WITH. A first issuance has no standing call, and
    that is C2's first trigger rather than this one."""
    assert observation_disagreements(
        StandingCall(rain=None, temp_high_c=None),
        ObservedSoFar(precipitation=True, high_c=99.0),
    ) == []


def test_both_can_fire_and_the_order_is_stable():
    """Stored and compared across two languages, so the order is part of the
    contract rather than an implementation detail."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=True, high_c=35.0),
    )

    assert out == [DISAGREEMENT_RAIN_WHILE_DRY, DISAGREEMENT_HIGH_EXCEEDED]
