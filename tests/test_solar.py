"""Sunrise and sunset, computed rather than fetched.

WHAT THESE ASSERT, AND WHAT THEY CANNOT.

Every expected value below was checked against a source outside this project
before it was written down — Open-Meteo's own daily endpoint on 2026-08-28,
and for Kisumu on 2026-08-19 the Kenya Met Department bulletin committed at
`tests/fixtures/kmd_daily_2026-08-19.json`, which prints the city's sunrise
and sunset in Part III. Two independent sources agreeing to the minute is what
makes these hand-verified rather than a recording of whatever the code did.

They cannot assert that the algorithm is right everywhere. That was measured
separately, by sweeping 108 consecutive days at each of eleven locations
against Open-Meteo; the result and its limits are in `solar`'s docstring. The
cases here are the ones a port would get wrong: the polar conventions, a local
date that is not the UTC date, and a daylight-saving changeover.
"""

from datetime import date, datetime

from openlocalweather.dates import utc_offset_seconds
from openlocalweather.solar import sun_times

# Kisumu, as configured. Three hours ahead of UTC and no daylight saving.
KISUMU_LAT, KISUMU_LON, KISUMU_OFFSET = -0.0917, 34.7680, 3 * 3600


def test_it_agrees_with_the_met_department_bulletin():
    """The strongest check available: an independent published source, for
    this project's own location, committed to this repository.

    Part III of the KMD bulletin for 2026-08-19 gives Kisumu sunrise 06:41 and
    sunset 18:47. Open-Meteo returned the same pair for that date.
    """
    got = sun_times(KISUMU_LAT, KISUMU_LON, date(2026, 8, 19), KISUMU_OFFSET)

    assert got.sunrise == datetime(2026, 8, 19, 6, 41)
    assert got.sunset == datetime(2026, 8, 19, 18, 47)


def test_it_agrees_with_what_used_to_be_fetched():
    """2026-08-22 is the date the daypart module and its vectors are built on,
    where the fetched figures were 06:40 and 18:47. Computing them has to give
    the same answer or this was a change and not a replacement."""
    got = sun_times(KISUMU_LAT, KISUMU_LON, date(2026, 8, 22), KISUMU_OFFSET)

    assert (got.sunrise.strftime("%H:%M"), got.sunset.strftime("%H:%M")) == ("06:40", "18:47")


def test_minutes_are_truncated_not_rounded():
    """Kisumu on 2026-08-28 is 06:38:53. Open-Meteo and the KMD bulletin both
    truncate, so 06:38 is the answer that agrees with the sources a reader can
    check; rounding would publish 06:39 beside a bulletin saying 06:38."""
    got = sun_times(KISUMU_LAT, KISUMU_LON, date(2026, 8, 28), KISUMU_OFFSET)

    assert got.sunrise == datetime(2026, 8, 28, 6, 38)
    assert got.sunset == datetime(2026, 8, 28, 18, 45)


def test_a_long_northern_summer_day():
    """London at the solstice: 04:43 to 21:21, sixteen and a half hours. A
    port that dropped the equation of time or the obliquity correction still
    lands near the middle of the day and fails here at its ends."""
    day = date(2026, 6, 21)
    got = sun_times(51.5072, -0.1276, day, utc_offset_seconds("Europe/London", day))

    assert got.sunrise == datetime(2026, 6, 21, 4, 43)
    assert got.sunset == datetime(2026, 6, 21, 21, 21)


def test_the_southern_hemisphere_runs_the_other_way():
    """Sydney in late August is coming out of winter, not into it — an
    eleven-hour day where London's is fourteen. A declination handled with the
    wrong sign passes every equatorial case and fails this one."""
    day = date(2026, 8, 28)
    got = sun_times(-33.87, 151.21, day, utc_offset_seconds("Australia/Sydney", day))

    assert got.sunrise == datetime(2026, 8, 28, 6, 19)
    assert got.sunset == datetime(2026, 8, 28, 17, 34)


def test_the_midnight_sun_is_a_day_that_starts_and_ends_at_midnight():
    """Open-Meteo's convention, matched deliberately: local midnight to local
    midnight, a span of 24 hours. `daypart.classify_phase` reads that span to
    decide the polar phases, so returning nothing here — the obvious
    alternative — would silently disable them."""
    day = date(2026, 6, 21)
    got = sun_times(78.2232, 15.6469, day, utc_offset_seconds("Arctic/Longyearbyen", day))

    assert got.sunrise == datetime(2026, 6, 21, 0, 0)
    assert got.sunset == datetime(2026, 6, 22, 0, 0)


def test_polar_night_is_a_day_of_zero_length():
    """Also Open-Meteo's, and verified against the live API on 2026-08-28:
    both times are local midnight and `daylight_duration` is 0.

    A comment in the pipeline claimed for months that polar night came back as
    nulls. It does not, and nothing depended on the claim being true, which is
    how it survived."""
    day = date(2025, 12, 21)
    got = sun_times(78.2232, 15.6469, day, utc_offset_seconds("Arctic/Longyearbyen", day))

    assert got.sunrise == datetime(2025, 12, 21, 0, 0)
    assert got.sunset == datetime(2025, 12, 21, 0, 0)


def test_a_timezone_that_disagrees_with_its_longitude():
    """Kiritimati keeps UTC+14 at 157 degrees WEST, so its local noon and its
    solar noon fall on different UTC dates.

    This is where "the solar transit on the same UTC day" gives an answer a
    day out — which is what Open-Meteo does here, labelling this event
    2026-08-27. Picking the transit nearest local noon is what makes the
    answer the local date's own.
    """
    day = date(2026, 8, 28)
    got = sun_times(1.87, -157.40, day, utc_offset_seconds("Pacific/Kiritimati", day))

    assert got.sunrise == datetime(2026, 8, 28, 6, 26)
    assert got.sunset == datetime(2026, 8, 28, 18, 35)


def test_the_day_the_clocks_go_back():
    """London on 2025-10-26, when BST ended at 02:00. Sunrise is 06:43 GMT.

    Open-Meteo answered 07:43 — it applies one UTC offset to a whole response
    and used BST's. Measured 2026-08-28, and not only at the boundary: asked
    for 2025-12-15 alone it still returned +3600 and a sunrise of 08:59
    against a published 07:59. Taking the offset per date is what removes it.
    """
    day = date(2025, 10, 26)
    assert utc_offset_seconds("Europe/London", day) == 0

    got = sun_times(51.5072, -0.1276, day, 0)
    assert got.sunrise == datetime(2025, 10, 26, 6, 43)
    assert got.sunset == datetime(2025, 10, 26, 16, 44)


def test_the_offset_is_taken_at_noon_not_midnight():
    """A date's midnight can fall inside a daylight-saving gap, where the wall
    clock does not exist and the offset is ambiguous. Noon never does.

    Lord Howe Island is the sharpest case: it shifts by THIRTY minutes, at
    02:00 on 2025-10-05. Taking the offset there at midnight would be taking
    it before the change on the day it happens.
    """
    assert utc_offset_seconds("Australia/Lord_Howe", date(2025, 10, 4)) == 10 * 3600 + 1800
    assert utc_offset_seconds("Australia/Lord_Howe", date(2025, 10, 5)) == 11 * 3600
