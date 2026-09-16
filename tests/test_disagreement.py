"""ROADMAP item 104, C2's third trigger."""

import pytest

from openlocalweather.disagreement import (
    DISAGREEMENT_HIGH_EXCEEDED,
    DISAGREEMENT_ONSET_ALREADY_PASSED,
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


# --- The onset that has already happened (ROADMAP item 138) ----------------


def test_an_onset_already_observed_contradicts_a_later_called_one():
    """THE OPERATOR'S CASE, 2026-09-16: "we forecast dry morning,
    thunderstorms starting at 1800 ... sensor data showing rain already
    started at 1400. I don't want us to call that dry until 1800 again."

    Nothing caught this. `RAIN_WHILE_DRY` needs `standing.rain is False`, and
    here the call says rain IS coming — it is right about the day and wrong
    about the hour. Onset is scored at Day+0, so the run is graded on the
    wrong number while the page contradicts itself.
    """
    found = observation_disagreements(
        StandingCall(rain=True, onset_hour="18:00"),
        ObservedSoFar(precipitation=True, precipitation_onset="14:00"),
    )

    assert DISAGREEMENT_ONSET_ALREADY_PASSED in found


def test_an_onset_inside_the_forecast_resolution_is_not_a_contradiction():
    """`onset_hour` is a point taken from a multi-hour WINDOW, so a
    difference smaller than that window is agreement, not disagreement.
    Firing here would re-forecast on the instrument."""
    found = observation_disagreements(
        StandingCall(rain=True, onset_hour="18:00"),
        ObservedSoFar(precipitation=True, precipitation_onset="17:30"),
    )

    assert DISAGREEMENT_ONSET_ALREADY_PASSED not in found


def test_rain_observed_later_than_called_is_not_a_contradiction():
    """THE ASYMMETRY THIS MODULE IS BUILT ON. A forecast can be proved too
    LOW by an observation and never too high: rain that arrived late has
    still arrived, and a call whose hour has passed with no rain only means
    the day is not over."""
    found = observation_disagreements(
        StandingCall(rain=True, onset_hour="14:00"),
        ObservedSoFar(precipitation=True, precipitation_onset="18:00"),
    )

    assert DISAGREEMENT_ONSET_ALREADY_PASSED not in found


def test_no_called_onset_or_no_observed_onset_says_nothing():
    """Absence is absence. A station that saw rain without catching the clock
    still reports rain, and that is not evidence about timing."""
    for standing, observed in (
        (StandingCall(rain=True), ObservedSoFar(precipitation=True, precipitation_onset="14:00")),
        (StandingCall(rain=True, onset_hour="18:00"), ObservedSoFar(precipitation=True)),
    ):
        assert DISAGREEMENT_ONSET_ALREADY_PASSED not in observation_disagreements(standing, observed)
