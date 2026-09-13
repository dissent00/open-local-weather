"""Hand-computed expectations for the observed block — ROADMAP item 121.

The layer spec/README.md calls "Python is RIGHT": these are worked out by
hand, and the vectors mirror their edge cases so the Dart port inherits
verified expectations rather than generated ones.
"""

import pytest

from openlocalweather.models import ObservedSoFar
from openlocalweather.observed import describe_observed_so_far


def test_every_dimension_reported_in_the_contract_order():
    observed = ObservedSoFar(
        precipitation=True,
        precipitation_onset="13:00",
        thunder=True,
        high_c=27.4,
        low_c=18.1,
        peak_wind_kmh=31.4,
        cloud_oktas=5.5,
    )
    assert describe_observed_so_far(observed, as_of="14:00") == (
        "As of 14:00: rain from 13:00; thunder; high so far 27°C / 81°F; "
        "low so far 18°C / 65°F; peak gust 31 km/h; sky 6/8."
    )


def test_a_negative_is_reported_and_an_absence_is_omitted():
    """The distinction the whole record is built on. The station reported and
    saw no rain, which a reader at 09:00 wants; it measured no wind, which is
    not a calm day and must not read as one."""
    observed = ObservedSoFar(precipitation=False, thunder=False, high_c=22.0)
    assert describe_observed_so_far(observed, as_of="09:00") == (
        "As of 09:00: no rain; no thunder; high so far 22°C / 72°F."
    )


def test_rain_without_a_first_seen_time_still_says_rain():
    observed = ObservedSoFar(precipitation=True, precipitation_onset=None)
    assert describe_observed_so_far(observed, as_of="12:00") == "As of 12:00: rain."


def test_a_silent_station_is_not_a_quiet_day():
    """None in, None out — the caller renders a gap as a gap. Returning a
    sentence saying nothing was seen would be the 2026-08-29 error again."""
    assert describe_observed_so_far(None, as_of="14:00") is None
    assert describe_observed_so_far(ObservedSoFar(), as_of="14:00") is None


def test_without_an_issuance_time_it_says_so_far_today():
    observed = ObservedSoFar(thunder=True)
    assert describe_observed_so_far(observed) == "So far today: thunder."


@pytest.mark.parametrize(
    "celsius,expected",
    [(32.5, "32°C / 90°F"), (33.5, "34°C / 92°F"), (-0.5, "0°C / 31°F")],
)
def test_temperature_ties_go_half_to_even(celsius, expected):
    """The tie cases, because Dart's .round() is half AWAY from zero and would
    publish a different temperature in the app than on the site. Same
    divergence _roundHalfEven and _fmt0 already guard."""
    got = describe_observed_so_far(ObservedSoFar(high_c=celsius))
    assert got == f"So far today: high so far {expected}."


@pytest.mark.parametrize("kmh,expected", [(30.5, 30), (31.5, 32), (0.5, 0)])
def test_gust_ties_go_half_to_even(kmh, expected):
    got = describe_observed_so_far(ObservedSoFar(peak_wind_kmh=kmh))
    assert got == f"So far today: peak gust {expected} km/h."


@pytest.mark.parametrize("oktas,expected", [(5.5, 6), (6.5, 6), (0.5, 0)])
def test_sky_ties_go_half_to_even(oktas, expected):
    got = describe_observed_so_far(ObservedSoFar(cloud_oktas=oktas))
    assert got == f"So far today: sky {expected}/8."
