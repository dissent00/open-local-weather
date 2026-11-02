"""Both units wherever code writes a figure a reader reads — the
operator, 2026-11-02, on readers' feedback: °C (°F), km/h (kt), mm (in)."""

from openlocalweather import floor, outlook
from openlocalweather.models import ObservedSoFar
from openlocalweather.observed import describe_observed_so_far
from openlocalweather.outlook import ExtendedDay


def test_the_outlooks_highs_carry_fahrenheit():
    spread = ExtendedDay(lead_time_days=3, date="2026-11-05", day_name="Thursday", high_c=30.0, high_min_c=29.0, high_max_c=33.0)
    steady = ExtendedDay(lead_time_days=3, date="2026-11-05", day_name="Thursday", high_c=29.4, high_min_c=29.0, high_max_c=30.0)
    assert outlook._highs(spread) == "highs 29 to 33°C (84 to 91°F)"
    assert outlook._highs(steady) == "highs around 29°C (85°F)"


def test_the_floors_highs_carry_fahrenheit():
    assert floor._highs([29.0, 33.0]) == "highs 29 to 33°C (84 to 91°F)"
    assert floor._highs([29.0, 29.8]) == "highs around 29°C (85°F)"


def test_the_observed_line_carries_knots_and_fahrenheit():
    line = describe_observed_so_far(
        ObservedSoFar(precipitation=False, thunder=False, high_c=21.0, low_c=20.0, peak_wind_kmh=7.41, peak_gust_kmh=37.0,
                      reported_through="05:00"),
        as_of="06:01",
    )
    assert "high so far 21°C (70°F)" in line
    assert "peak sustained 7 km/h (4 kt)" in line and "peak gust 37 km/h (20 kt)" in line
