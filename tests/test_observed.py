"""Hand-computed expectations for the observed block — ROADMAP item 121.

The layer spec/README.md calls "Python is RIGHT": these are worked out by
hand, and the vectors mirror their edge cases so the Dart port inherits
verified expectations rather than generated ones.
"""

import pytest

from openlocalweather.models import ObservedSoFar
from openlocalweather.observed import describe_observed_so_far, observed_baseline


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


# ---------------------------------------------------------------------------
# Contract item 8, stage 2 — what today's own observations may baseline.
# ---------------------------------------------------------------------------


def test_the_baseline_carries_temperature():
    """The one pair of dimensions where the station and the models measure the
    same quantity. Station minus reanalysis over 40 days is +0.49 C on the
    high and -0.05 C on the low, against a 1.0 C band."""
    baseline = observed_baseline(ObservedSoFar(high_c=31.8, low_c=19.4))

    assert baseline is not None
    assert (baseline.high_c, baseline.low_c) == (31.8, 19.4)
    assert baseline.provenance == {"high_c": "metar_station", "low_c": "metar_station"}


def test_the_baseline_refuses_the_station_gust():
    """A DIFFERENT QUANTITY, not a noisier version of the same one.

    `fetch/metar.py` reads `sknt` — the max SUSTAINED wind — because METAR
    files a gust group only when a gust occurs, absent on all 932 rows of a
    45-day sample. The forecast side is `windgusts_10m_max`. Pairing them
    reads the gust factor as weather, which is the error item 126 records a
    whole withdrawn plan over.
    """
    baseline = observed_baseline(ObservedSoFar(high_c=31.8, peak_wind_kmh=24.0))

    assert baseline.peak_wind_kmh is None


def test_the_baseline_refuses_the_station_sky_and_the_rain_amount():
    """Cloud is a different STATISTIC — a point mean in eighths against a mean
    over hourly grid values — and item 123 owns that choice with four paired
    days. A METAR reports that rain fell, never how much, so there is no
    amount to band a day by and the rain half stays silent."""
    baseline = observed_baseline(
        ObservedSoFar(high_c=31.8, cloud_oktas=6.0, precipitation=True)
    )

    assert baseline.cloud_cover_pct is None
    assert baseline.precip_mm is None


def test_nothing_measured_is_not_a_quiet_day():
    """A station that did not report is not a station reporting agreement —
    the error class that cost a published forecast on 2026-08-29."""
    assert observed_baseline(None) is None
    assert observed_baseline(ObservedSoFar(precipitation=True)) is None


# --- the overnight-low footnote — ROADMAP item 143, part 3 -------------------


def test_the_footnote_names_the_station_and_both_numbers():
    """A fact about ONE station, not a claim about the basin. The operator's
    wording: "the airport reported 20 against the forecast of 18.2. It's not
    saying nowhere in the area hit 18.2, just that the airport didn't." """
    from openlocalweather.models import LowDivergence
    from openlocalweather.observed import describe_low_divergence

    div = LowDivergence(
        forecast_c=18.2, observed_c=20.0, delta_c=1.8,
        margin_c=1.0, notable=True, decisive=False,
    )
    got = describe_low_divergence(div, "Kisumu International Airport")

    assert got is not None
    assert "Kisumu International Airport" in got
    # Both units, via the ONE formatter — never a second rounding site.
    assert "20.0°C" in got and "18.2°C" in got, (
        "the tenth is the content — 18.2 rounded to 18 makes the gap 2, not 1.8"
    )
    assert "°F" in got, "the project renders every temperature in both units"


def test_a_divergence_nobody_needs_produces_no_footnote():
    """The default is silence. A gap inside its band is stored and not said."""
    from openlocalweather.models import LowDivergence
    from openlocalweather.observed import describe_low_divergence

    quiet = LowDivergence(
        forecast_c=18.2, observed_c=19.0, delta_c=0.8,
        margin_c=3.0, notable=False, decisive=False,
    )
    assert describe_low_divergence(quiet, "Kisumu International Airport") is None
    assert describe_low_divergence(None, "Kisumu International Airport") is None
