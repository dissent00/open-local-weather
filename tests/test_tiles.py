"""The tile comparison — ROADMAP item 159, step 1.

What this replaces returned "nothing worth saying" ZERO times across the 16
comparisons in the prompt archive, so on a quiet day it filled with "about the
same" and "winds and cloud little changed". Silence is the answer these tests
care about most.
"""

import pytest

from openlocalweather.tiles import (
    MIN_PAIRS_FOR_NOTABLE,
    comparison_modifiers,
    notable_moves,
)

# The reference station's own gates, measured over its 40 day-pairs.
GATES = {"temp": 2.2, "wind": 11.1, "cloud": 34.5}


def test_a_dimension_that_did_not_move_says_nothing():
    assert comparison_modifiers({"temp": 1.0, "wind": 4.0, "cloud": 9.0}, GATES) == {}


def test_only_the_dimension_that_moved_speaks():
    said = comparison_modifiers({"temp": 0.4, "wind": 18.4, "cloud": 3.0}, GATES)
    assert said == {"wind": "much windier"}


def test_temperature_says_degrees_rather_than_an_adjective():
    """The bands call 2.2 C "slightly" because they are built for a climate
    where six degrees is ordinary. Here 2.2 is the top decile, so "slightly
    cooler" on a day the gate just called unusual undercuts itself."""
    assert comparison_modifiers({"temp": -2.6}, GATES) == {"temp": "3° cooler"}
    assert comparison_modifiers({"temp": 2.6}, GATES) == {"temp": "3° warmer"}


def test_the_word_comes_from_the_standards_band_not_the_gate():
    """The gate is local; the vocabulary is not. Cloud's boundaries are one
    and three oktas, the NWS resolution — see CLOUD_CHANGE_BANDS_PCT."""
    assert comparison_modifiers({"cloud": 40.0}, GATES) == {"cloud": "much cloudier"}
    assert comparison_modifiers({"cloud": -40.0}, GATES) == {"cloud": "much clearer"}
    # Past the gate but inside the one-okta-to-three band: the bare word.
    assert comparison_modifiers({"cloud": 35.0}, GATES) == {"cloud": "cloudier"}


def test_exactly_at_the_gate_speaks():
    assert comparison_modifiers({"wind": 11.1}, GATES) == {"wind": "windier"}
    assert comparison_modifiers({"wind": 11.0}, GATES) == {}


def test_a_dimension_with_no_gate_never_speaks():
    """A deployment whose record is too short says nothing rather than
    inventing a threshold — the same answer every absent input gets here."""
    assert comparison_modifiers({"temp": 9.0, "cloud": 80.0}, {}) == {}
    assert comparison_modifiers({"temp": 9.0, "cloud": 80.0}, {"cloud": 34.5}) == {
        "cloud": "much cloudier"
    }


def test_an_unmeasured_dimension_is_absent_not_zero():
    assert comparison_modifiers({"temp": None, "wind": 18.0}, GATES) == {
        "wind": "much windier"
    }


def test_rain_is_not_a_dimension_here():
    """Deliberately out: a near-zero day against another near-zero day flips
    wetter/drier on a rounding difference, and rain is the dimension where the
    absolute matters more than the change. The operator left it open."""
    assert comparison_modifiers({"rain": 40.0}, {**GATES, "rain": 6.0}) == {}


# ---------------------------------------------------------------------------
# The gates themselves
# ---------------------------------------------------------------------------


def day(high=None, gust=None, cloud=None):
    return {"high_c": high, "peak_wind_kmh": gust, "cloud_cover_pct": cloud}


def test_too_short_a_record_produces_no_gate_at_all():
    """ITEM 100 APPLIED TO ITSELF. At 30 pairs the decile has three
    observations above it; below that it is noise wearing a number."""
    history = [day(high=20.0 + i % 3) for i in range(MIN_PAIRS_FOR_NOTABLE)]
    assert notable_moves(history) == {}, "a gate was built from too few pairs"

    history.append(day(high=21.0))
    assert "temp" in notable_moves(history)


def test_each_dimension_gets_its_own_gate_from_its_own_pairs():
    """A day missing one reading must not cost the others their history."""
    history = []
    for i in range(40):
        history.append(day(high=20.0 + (i % 5), gust=None, cloud=10.0 * (i % 6)))
    gates = notable_moves(history)
    assert "temp" in gates and "cloud" in gates
    assert "wind" not in gates, "a gate appeared for a dimension with no readings"


def test_the_gate_is_the_ninetieth_percentile_of_the_moves():
    """Not the maximum, and not a mean.

    Written wrong the first time and corrected by running it: with thirty
    moves of 1 and a single move of 10, the decile is 1, because 90% of 31
    values sits below the outlier. One unusual day does not move the bar —
    which is the property that makes this survive a freak afternoon.
    """
    history = [day(high=float(i)) for i in range(0, 31)]  # 30 moves of 1.0
    history.append(day(high=41.0))  # one move of 10.0
    assert notable_moves(history)["temp"] == pytest.approx(1.0)

    # Four large moves in 34 do reach it.
    history = [day(high=float(i)) for i in range(0, 31)]
    for extra in (41.0, 51.0, 61.0, 71.0):
        history.append(day(high=extra))
    assert notable_moves(history)["temp"] == pytest.approx(10.0)
