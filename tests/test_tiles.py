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


# ---------------------------------------------------------------------------
# The sky at each anchor — ROADMAP item 159 step 2
# ---------------------------------------------------------------------------


def day_block(covers: list[float]) -> dict:
    """A WHOLE LOCAL DAY, 00:00 to 23:00, which is what `primary_hourly` is.

    Not a forward window. The two carry the same variables under the same
    names, and reading an anchor out of the forward one resolves 03:00 to
    tomorrow — the misreading of 2026-09-21, which the pipeline test
    `test_the_wind_shift_is_given_the_whole_day_not_the_forward_window` now
    pins against.
    """
    from openlocalweather.defaults import MODELS

    hours = {"time": [f"2026-09-21T{h:02d}:00" for h in range(24)]}
    for model in MODELS:
        hours[f"cloud_cover_{model}"] = list(covers)
    return {"hourly": hours}


# Clear overnight, building through the morning, overcast under the afternoon
# convection and clearing after dark — the shape 2026-09-21 actually had, and
# the one whose 45% daily mean describes none of it. The values at 03:00,
# 12:00 and 18:00 are what the anchors read.
CLEAR_TO_OVERCAST = (
    [5] * 6                       # 00-05  clear
    + [10, 20, 30, 40, 55, 60]    # 06-11  building
    + [70, 80, 95, 100, 100, 100] # 12-17  thickening to overcast
    + [100, 80, 60, 40, 20, 10]   # 18-23  overcast at the evening anchor, then clearing
)


def test_the_day_shape_is_three_anchors_in_time_order():
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import cloud_anchors

    assert cloud_anchors(day_block(CLEAR_TO_OVERCAST), MODELS, issued_hour=0) == [
        {"when": "early", "cover": "Clear"},
        {"when": "midday", "cover": "Mostly cloudy"},
        {"when": "evening", "cover": "Overcast"},
    ]


def test_an_evening_run_with_every_anchor_behind_it_says_nothing():
    """ROADMAP item 118, the same rule the wind shift follows: a tile whose
    every hour has gone describes a day the reader has finished."""
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import cloud_anchors

    assert cloud_anchors(day_block(CLEAR_TO_OVERCAST), MODELS, issued_hour=18) == []


def test_a_morning_run_keeps_the_anchor_that_has_passed():
    """The test is "is any of it still ahead", NOT "drop what has passed" —
    item 118's wording. A reader at breakfast still wants to know the day
    started clear."""
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import cloud_anchors

    said = cloud_anchors(day_block(CLEAR_TO_OVERCAST), MODELS, issued_hour=6)
    assert [a["when"] for a in said] == ["early", "midday", "evening"]


def test_an_hour_the_block_does_not_hold_is_absent():
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import cloud_anchors

    block = day_block(CLEAR_TO_OVERCAST)
    # Only 03:00 and 12:00 survive.
    keep = {3, 12}
    times = block["hourly"]["time"]
    idx = [i for i, t in enumerate(times) if int(t[11:13]) in keep]
    block["hourly"]["time"] = [times[i] for i in idx]
    for key in list(block["hourly"]):
        if key != "time":
            block["hourly"][key] = [block["hourly"][key][i] for i in idx]

    assert [a["when"] for a in cloud_anchors(block, MODELS, issued_hour=0)] == [
        "early",
        "midday",
    ]


def test_no_cloud_series_at_all_is_empty_not_clear():
    """An absent sky is not a clear one — the same rule every other absent
    input in this project follows."""
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import cloud_anchors

    bare = {"hourly": {"time": [f"2026-09-21T{h:02d}:00" for h in range(24)]}}
    assert cloud_anchors(bare, MODELS, issued_hour=0) == []


def test_the_words_are_the_standards_own_categories():
    """NWS sky condition in eighths: clear at 0, few at 1-2, scattered at 3-4,
    broken at 5-7, overcast at 8. The boundaries are the midpoints converted,
    the same source CLOUD_CHANGE_BANDS_PCT draws its one-okta floor from."""
    from openlocalweather.tiles import sky_word

    assert sky_word(0.0) == "Clear"
    assert sky_word(6.2) == "Clear"
    assert sky_word(6.25) == "Mostly clear"
    assert sky_word(31.24) == "Mostly clear"
    assert sky_word(31.25) == "Partly cloudy"
    assert sky_word(56.25) == "Mostly cloudy"
    assert sky_word(93.75) == "Overcast"
    assert sky_word(100.0) == "Overcast"
    assert sky_word(None) is None


# ---------------------------------------------------------------------------
# The wind at each anchor — ROADMAP item 159 step 3
# ---------------------------------------------------------------------------


def wind_block(speeds, gusts, bearings) -> dict:
    """A WHOLE LOCAL DAY, like `day_block` and for the same reason."""
    from openlocalweather.defaults import MODELS

    hours = {"time": [f"2026-09-21T{h:02d}:00" for h in range(24)]}
    for model in MODELS:
        hours[f"wind_speed_10m_{model}"] = list(speeds)
        hours[f"wind_gusts_10m_{model}"] = list(gusts)
        if bearings is not None:
            hours[f"wind_direction_10m_{model}"] = list(bearings)
    return {"hourly": hours}


LIGHT_THEN_FRESH = ([9.0] * 12 + [22.0] * 12, [15.0] * 12 + [52.0] * 12)
NE_THEN_SW = [30.0] * 12 + [225.0] * 12


def test_the_wind_anchors_are_values_in_time_order():
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import wind_anchors

    said = wind_anchors(
        wind_block(*LIGHT_THEN_FRESH, NE_THEN_SW), MODELS, issued_hour=0
    )
    assert [a["when"] for a in said] == ["early", "midday", "evening"]
    assert said[0] == {
        "when": "early",
        "direction": "NNE",
        "sustained_kmh": 9.0,
        "gust_kmh": 15.0,
    }
    assert said[1]["direction"] == "SW"
    assert said[1]["gust_kmh"] == 52.0


def test_speeds_stay_in_kilometres_whatever_the_reader_wants():
    """The unit lives in the tile's header and the value converts at render,
    which is what makes the setting a one-label change rather than a rebuild
    of every string."""
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import wind_anchors

    said = wind_anchors(
        wind_block(*LIGHT_THEN_FRESH, NE_THEN_SW), MODELS, issued_hour=0
    )
    assert said[1]["sustained_kmh"] == 22.0, "a speed was converted at the source"


def test_no_agreed_bearing_drops_the_letters_not_the_anchor():
    """A bearing cannot be averaged, so this is consensus_direction's gated
    answer and nothing else. Measured over the prompt archive, a single agreed
    bearing existed on 3 of 18 runs, so this is the common case."""
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import wind_anchors

    # THE GATE IS ACROSS MODELS, not across hours — written wrong the first
    # time, with every model given the same bearing, which agrees by
    # construction. Here each model points a different way at the same hour,
    # which is what a scattered forecast actually looks like.
    block = wind_block(*LIGHT_THEN_FRESH, None)
    for offset, model in enumerate(MODELS):
        block["hourly"][f"wind_direction_10m_{model}"] = [
            float(offset * 72) % 360
        ] * 24

    said = wind_anchors(block, MODELS, issued_hour=0)
    assert said, "the anchors were dropped along with the bearing"
    assert all("direction" not in a for a in said)
    assert said[0]["sustained_kmh"] == 9.0


def test_an_hour_with_no_wind_at_all_is_absent():
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import wind_anchors

    bare = {"hourly": {"time": [f"2026-09-21T{h:02d}:00" for h in range(24)]}}
    assert wind_anchors(bare, MODELS, issued_hour=0) == []


def test_an_evening_run_with_every_anchor_behind_it_says_nothing_about_wind():
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import wind_anchors

    assert (
        wind_anchors(wind_block(*LIGHT_THEN_FRESH, NE_THEN_SW), MODELS, issued_hour=18)
        == []
    )


def test_the_wind_and_the_sky_describe_the_same_moments():
    """Two tiles side by side must name the same three hours, or a reader
    comparing them is comparing different times of day."""
    from openlocalweather.defaults import MODELS
    from openlocalweather.tiles import cloud_anchors, wind_anchors

    block = wind_block(*LIGHT_THEN_FRESH, NE_THEN_SW)
    block["hourly"].update(day_block(CLEAR_TO_OVERCAST)["hourly"])
    sky = cloud_anchors(block, MODELS, issued_hour=0)
    wind = wind_anchors(block, MODELS, issued_hour=0)
    assert [a["when"] for a in sky] == [a["when"] for a in wind]
