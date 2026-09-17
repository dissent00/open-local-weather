"""The 0.25° tile a point is filed under in the shared store — ROADMAP item 156.

Hand-computed, so the vector inherits verified expectations. The cases are
the corners and the wrap, because those are where a floor in two languages
could part company.
"""

import pytest

from openlocalweather.cell import CELL_DEGREES, cell_key


def test_the_tile_is_a_quarter_degree():
    assert CELL_DEGREES == 0.25


def test_the_reference_deployment_files_under_its_south_west_corner():
    # config/location.yaml's primary point. -0.0917 sits in the tile whose
    # south edge is -0.25; 34.768 in the tile whose west edge is 34.75.
    assert cell_key(-0.0917, 34.768) == "s0.25_e34.75"


@pytest.mark.parametrize(
    "lat, lon, expected",
    [
        (0.0, 0.0, "n0.00_e0.00"),
        # A corner belongs to the tile it names.
        (0.25, 34.75, "n0.25_e34.75"),
        # Just south of the equator is the southern tile, not the origin's.
        (-0.0001, 34.75, "s0.25_e34.75"),
        (-0.25, -34.75, "s0.25_w34.75"),
        (-0.2499, -34.7499, "s0.25_w34.75"),
        (51.5074, -0.1278, "n51.50_w0.25"),
    ],
)
def test_corners_and_signs(lat, lon, expected):
    assert cell_key(lat, lon) == expected


def test_the_poles_are_the_last_tiles_not_a_tile_past_them():
    assert cell_key(90.0, 0.0) == "n89.75_e0.00"
    assert cell_key(-90.0, 0.0) == "s90.00_e0.00"


def test_the_antimeridian_wraps_west():
    # 180 is the west edge of the first tile, never the east edge of a 361st.
    assert cell_key(0.0, 180.0) == "n0.00_w180.00"
    assert cell_key(0.0, 180.25) == "n0.00_w179.75"
    assert cell_key(0.0, -180.0) == "n0.00_w180.00"


def test_nan_is_refused_rather_than_filed_somewhere():
    with pytest.raises(ValueError):
        cell_key(float("nan"), 0.0)
