"""The tile a point is filed under in the shared store — ROADMAP item 156.

A privacy and indexing unit, not a model cell: item 110 measured the four
models serving four different grid cells for one request, so no single
model grid could be "the" cell. A quarter degree is the ECMWF spacing and
about 28 km, coarse enough that a home cannot be read from a week of
submissions and fine enough that a viewer's listing is about one town.

The key names the tile's SOUTH-WEST corner, `s0.25_e34.75` for Kisumu, so
that a key is a place a reader can find on a map and two implementations
have one string to agree on. Shared with Dart through `cell_key.json`; the
app computes it before a forecast leaves the phone, and the store's pull
job computes it for a registered deployment from its configured point.

Arithmetic chosen so both languages cannot differ by a bit: a quarter is a
power of two, so dividing by it, flooring and multiplying back are exact
for any double, and a two-decimal render of an exact quarter is exact too.
"""

from __future__ import annotations

import math

CELL_DEGREES = 0.25

# Rows count from the equator; the last row's south edge is 89.75.
_LAST_ROW = int(90 / CELL_DEGREES) - 1


def cell_key(lat: float, lon: float) -> str:
    """The key of the quarter-degree tile containing (lat, lon).

    A corner belongs to the tile it names. Latitude 90 is filed under the
    last row rather than a tile past the pole; a longitude of 180 or beyond
    wraps west, so 180 is the west edge of the first tile and never the
    east edge of a 361st.
    """
    if math.isnan(lat) or math.isnan(lon):
        raise ValueError("a cell needs a real point")

    if lon >= 180.0 or lon < -180.0:
        lon = ((lon + 180.0) % 360.0) - 180.0

    row = min(math.floor(lat / CELL_DEGREES), _LAST_ROW)
    col = math.floor(lon / CELL_DEGREES)

    return f"{_edge(row, 'n', 's')}_{_edge(col, 'e', 'w')}"


def _edge(index: int, positive: str, negative: str) -> str:
    hemisphere = negative if index < 0 else positive
    return f"{hemisphere}{abs(index) * CELL_DEGREES:.2f}"
