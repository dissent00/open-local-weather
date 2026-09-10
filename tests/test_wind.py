import pytest

from openlocalweather.wind import (
    compass_point,
    describe_wind_shift,
    consensus_direction,
    vector_mean,
)


def test_a_north_wind_and_a_south_wind_have_no_mean_direction():
    """Raised by the operator 2026-09-10: "What I don't want is a north wind
    in model A averaged with a south wind in model B to become east or west
    or some nonsense."

    THAT IS WHAT AN ARITHMETIC MEAN DOES to compass bearings, and it is why
    this module exists. Direction is circular: 0 and 180 average to 90 on a
    number line, which is a real bearing pointing at right angles to both
    inputs and supported by neither.

    The vector mean cannot produce it. Two opposing winds sum to a resultant
    of length zero, and a zero-length resultant HAS no direction — which is
    the honest answer and the signal to say nothing.
    """
    deg, r = vector_mean([0.0, 180.0])
    assert r == pytest.approx(0.0, abs=1e-9), "opposing winds must not agree"
    assert consensus_direction([0.0, 180.0]) is None

    # And the arithmetic mean, for contrast — the bug this prevents.
    assert sum([0.0, 180.0]) / 2 == 90.0


def test_the_wrap_around_case_an_arithmetic_mean_also_fails():
    """350 and 10 degrees are twenty degrees apart, both nearly north. An
    arithmetic mean puts them at 180 — due SOUTH, the opposite of both."""
    deg, r = vector_mean([350.0, 10.0])
    assert deg == pytest.approx(0.0, abs=1e-9)
    assert r > 0.98, "twenty degrees apart is near-perfect agreement"

    # Three, because two bearings are not a consensus however well they
    # agree — see WIND_DIRECTION_MIN_MODELS and the single-model case below.
    assert consensus_direction([350.0, 10.0, 2.0]) == "N"

    # And the arithmetic mean of the pair, for contrast: due SOUTH of both.
    assert sum([350.0, 10.0]) / 2 == 180.0


def test_one_outlier_does_not_drag_the_consensus_off_the_field():
    """The real case, from 2026-09-06: four models southwesterly and one at
    11 degrees. The arithmetic mean lands on SSW (192), a bearing no model
    held. The vector mean stays with the field and the agreement figure
    reports that the outlier is there.
    """
    dirs = [11.0, 254.0, 264.0, 207.0, 225.0]
    deg, r = vector_mean(dirs)
    assert 230 < deg < 265, f"the consensus must stay with the four, not split the difference: {deg}"
    assert 0.5 < r < 0.8, "and must report that agreement is only moderate"

    naive = sum(dirs) / len(dirs)
    assert 185 < naive < 200, "the arithmetic mean lands where nobody is"


def test_the_gate_withholds_a_direction_the_models_do_not_share():
    """Measured 2026-09-10 across seven days of archived guidance: model
    agreement swings from R 0.95 at midday, when the lake breeze is driven,
    to 0.48 at 19:00 when it collapses. Naming a cardinal at 19:00 states a
    bearing the models do not share."""
    assert consensus_direction([225.0, 230.0, 220.0, 235.0]) == "SW"
    assert consensus_direction([22.0, 135.0, 275.0, 215.0]) is None


def test_a_direction_needs_something_to_average():
    assert vector_mean([]) is None
    assert consensus_direction([]) is None
    assert consensus_direction([180.0]) is None, "one model is not a consensus"


@pytest.mark.parametrize("deg,point", [
    (0, "N"), (11, "N"), (12, "NNE"), (45, "NE"), (180, "S"),
    (225, "SW"), (247.5, "WSW"), (350, "N"), (359.9, "N"),
])
def test_compass_points_are_the_16_point_rose(deg, point):
    assert compass_point(deg) == point


# ---------------------------------------------------------------------------
# describe_wind_shift — the day has a shape, and here it is the same shape
# every day.
# ---------------------------------------------------------------------------


def _hourly(per_hour: dict[int, list[float]], models: list[str]) -> dict:
    """Multi-model hourly in Open-Meteo's shape, from {hour: [dir per model]}."""
    hours = sorted(per_hour)
    out: dict[str, list] = {"time": [f"2026-09-10T{h:02d}:00" for h in hours]}
    for i, m in enumerate(models):
        out[f"wind_direction_10m_{m}"] = [per_hour[h][i] for h in hours]
    return {"hourly": out}


MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless"]


def test_the_lake_breeze_is_described_as_a_shift():
    """Measured 2026-09-10 across seven days of archived guidance, and the
    across-DAY agreement is the striking figure: 0.99 at 06:00, 0.98 at
    09:00 and 12:00. It is the same day every day — northeasterly overnight,
    round through the east in mid-morning, southwest once the lake breeze is
    driven, west as it decays.

    THE SHIFT IS BETTER SUPPORTED THAN ANY SINGLE BEARING. Asked for one
    direction for the day, the models disagree; asked which way it turns,
    they agree. So the shape is what gets reported.
    """
    shift = describe_wind_shift(
        _hourly({
            3: [30.0, 35.0, 25.0, 40.0],      # NE overnight, tight
            12: [225.0, 220.0, 230.0, 218.0],  # SW by midday, tight
            18: [270.0, 265.0, 275.0, 268.0],  # W into the evening, tight
        }, MODELS),
        MODELS,
    )
    assert shift == (
        "north-northeasterly overnight, turning southwest by midday "
        "and west into the evening"
    ), shift
    assert shift == shift.lower(), "a clause, not a sentence — the prompt capitalises it"


def test_an_hour_the_models_split_on_is_left_out_not_guessed():
    """The operator's rule, 2026-09-10: "Low confidence/low-agreement can be
    left out." The evening is where the models actually part company here —
    agreement 0.48 at 19:00 — so the phrase must be able to stop early."""
    shift = describe_wind_shift(
        _hourly({
            3: [30.0, 35.0, 25.0, 40.0],        # tight
            12: [225.0, 220.0, 230.0, 218.0],    # tight
            18: [10.0, 200.0, 100.0, 280.0],     # scattered: no bearing exists
        }, MODELS),
        MODELS,
    )
    assert shift == "north-northeasterly overnight, turning southwest by midday", shift
    assert "evening" not in shift, "the scattered hour was named anyway"


def test_a_day_with_no_shift_says_so_rather_than_inventing_one():
    """A steady bearing all day is a real and useful answer — the same
    reasoning describe_extended_trend uses for a steady spell."""
    steady = describe_wind_shift(
        _hourly({
            3: [225.0, 220.0, 230.0, 218.0],
            12: [223.0, 228.0, 222.0, 226.0],
            18: [220.0, 224.0, 219.0, 227.0],
        }, MODELS),
        MODELS,
    )
    assert steady is not None
    assert "southwesterly" in steady
    assert "then" not in steady, "nothing turned, so nothing should be reported as turning"


def test_no_shift_is_claimed_when_the_models_never_agree():
    assert describe_wind_shift(
        _hourly({
            3: [10.0, 200.0, 100.0, 280.0],
            12: [15.0, 190.0, 95.0, 300.0],
            18: [20.0, 210.0, 110.0, 290.0],
        }, MODELS),
        MODELS,
    ) is None


def test_an_empty_input_is_not_a_calm_day():
    assert describe_wind_shift({}, MODELS) is None
    assert describe_wind_shift({"hourly": None}, MODELS) is None
    assert describe_wind_shift(_hourly({3: [None] * 4, 12: [None] * 4}, MODELS), MODELS) is None
