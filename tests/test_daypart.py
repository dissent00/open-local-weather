"""Where the current moment sits in the day.

The bug that prompted this module: the evening run fires at 18:15 and reads
like an early-afternoon summary. The cause was not wording — the prompt
carried a date and no time, so the model could not tell 06:00 from 18:00.

These tests are mostly about the failure directions. A phase function that
returns something plausible for every input is easy; one that refuses to say
"evening" while the sun is still up is the point.
"""

from datetime import datetime

from openlocalweather.daypart import (
    TODAY,
    TOMORROW,
    TONIGHT,
    classify_phase,
    summarize_daypart,
)

# Kisumu, 2026-08-22 — the real figures from Open-Meteo on the day this was
# written, including the sunset that started the whole investigation.
SUNRISE = datetime(2026, 8, 22, 6, 40)
SUNSET = datetime(2026, 8, 22, 18, 47)
NEXT_SUNRISE = datetime(2026, 8, 23, 6, 40)


def at(hour, minute=0):
    return datetime(2026, 8, 22, hour, minute)


def test_the_1815_run_is_dusk_not_evening_and_not_afternoon():
    """The measured case. Sunset was 18:47, so the run fires 32 minutes before
    it — genuinely not evening yet, and long past 'afternoon' as a reader
    standing outside would judge it."""
    d = summarize_daypart(at(18, 15), SUNRISE, SUNSET, NEXT_SUNRISE)
    assert d.phase == "dusk"
    assert d.minutes_to_sunset == 32
    assert d.statement == "It is 18:15. Sunset is in 32 minutes."


def test_the_horizon_moves_off_today_once_most_of_it_has_happened():
    """By dusk, describing the day that has already happened helps nobody
    decide anything. What is left is tonight and tomorrow."""
    assert summarize_daypart(at(9), SUNRISE, SUNSET).horizon[0] == TODAY
    assert summarize_daypart(at(18, 15), SUNRISE, SUNSET).horizon == (
        TONIGHT,
        TOMORROW,
    )


def test_tonight_is_defined_rather_than_left_to_interpretation():
    """Otherwise one run reads it as "this evening" and the next as "the small
    hours", and a reader comparing two issuances sees a contradiction that is
    really just two definitions."""
    assert "dusk" in TONIGHT and "overnight" in TONIGHT and "dawn" in TONIGHT


def test_phases_follow_the_SUN_not_the_clock():
    """The same clock time is a different part of the day at a different
    latitude or season. This is the reason the module exists rather than a
    lookup table of hours: at 60°N, 18:00 is mid-afternoon in June and long
    after dark in December."""
    # Kisumu in August: 18:00 is nearly sunset.
    assert classify_phase(at(18), SUNRISE, SUNSET) == "dusk"

    # A far northern summer day, same clock time, sun still high.
    north_rise = datetime(2026, 8, 22, 3, 30)
    north_set = datetime(2026, 8, 22, 22, 30)
    assert classify_phase(at(18), north_rise, north_set) == "afternoon"

    # A far northern winter day: the sun is long gone by 18:00.
    winter_rise = datetime(2026, 8, 22, 9, 30)
    winter_set = datetime(2026, 8, 22, 15, 15)
    assert classify_phase(at(18), winter_rise, winter_set) == "evening"


def test_dawn_knows_which_side_of_sunrise_it_is_on():
    """'The sun rises at 06:40' ten minutes after it did is the kind of error
    a reader spots by looking out of a window."""
    before = summarize_daypart(at(6, 20), SUNRISE, SUNSET)
    after = summarize_daypart(at(6, 50), SUNRISE, SUNSET)
    assert before.phase == after.phase == "dawn"
    assert before.statement == "It is 06:20. Sunrise is in 20 minutes."
    assert after.statement == "It is 06:50. The sun rose at 06:40."


def test_after_dark_it_names_TOMORROW_s_sunrise():
    """Night spans midnight. Pointing at this morning's sunrise, seventeen
    hours gone, would be both useless and obviously wrong."""
    d = summarize_daypart(at(23), SUNRISE, SUNSET, NEXT_SUNRISE)
    assert d.phase == "night"
    assert "06:40" in d.statement  # tomorrow's, same clock time here
    d_no_next = summarize_daypart(at(23), SUNRISE, SUNSET)
    assert "06:40" in d_no_next.statement  # degrades, never crashes


def test_daylight_left_is_never_negative():
    """A forecast saying '-3 hours of daylight left' would be absurd, and the
    subtraction that produces it is the obvious way to write this."""
    for hour in (2, 6, 12, 18, 20, 23):
        assert summarize_daypart(at(hour), SUNRISE, SUNSET).daylight_hours_left >= 0
    assert summarize_daypart(at(20), SUNRISE, SUNSET).daylight_hours_left == 0


def test_it_rounds_daylight_DOWN():
    """'Two hours of daylight left' should not be said with 2h05m of it, and
    certainly not with 1h55m."""
    d = summarize_daypart(at(16, 45), SUNRISE, SUNSET)  # 2h02m to sunset
    assert d.daylight_hours_left == 2


def test_the_midnight_sun_is_not_reported_as_dusk():
    """Open-Meteo reports Longyearbyen in June as sunrise 00:00 with sunset
    exactly 24 hours later. The ordinary logic reads that as a normal day with
    a very late dusk, and would tell a reader the light is going while the sun
    sits well above the horizon."""
    rise = datetime(2026, 6, 21, 0, 0)
    set_ = datetime(2026, 6, 22, 0, 0)
    d = summarize_daypart(datetime(2026, 6, 21, 23, 0), rise, set_)
    assert d.phase.startswith("polar_")
    assert d.statement == "It is 23:00. The sun does not set at this time of year."


def test_polar_night_says_the_sun_does_not_rise():
    """Not verified against live data — the forecast API will not reach a
    December date from here — so this pins the intended behaviour rather than
    a measured response shape."""
    rise = datetime(2026, 12, 21, 11, 30)
    set_ = datetime(2026, 12, 21, 12, 30)
    d = summarize_daypart(datetime(2026, 12, 21, 10, 0), rise, set_)
    assert d.phase == "polar_night"
    assert "does not rise" in d.statement


def test_every_phase_has_a_horizon_and_a_statement():
    """A missing key here would be a KeyError in the middle of a live run, and
    the phases are the one thing guaranteed to change with latitude."""
    seen = set()
    for hour in range(24):
        for minute in (0, 30):
            d = summarize_daypart(at(hour, minute), SUNRISE, SUNSET, NEXT_SUNRISE)
            seen.add(d.phase)
            assert d.statement and d.horizon
    assert {"night", "dawn", "morning", "midday", "afternoon", "dusk", "evening"} <= seen
