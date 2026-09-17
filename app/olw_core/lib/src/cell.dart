// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The tile a point is filed under in the shared store — ROADMAP item 156.
///
/// A privacy and indexing unit, not a model cell: item 110 measured the four
/// models serving four different grid cells for one request. A quarter
/// degree is about 28 km, coarse enough that a home cannot be read from a
/// week of submissions and fine enough that a listing is about one town.
///
/// The app computes this before a forecast leaves the phone; the store's
/// pull job computes it for a registered deployment. Both must file one
/// point under one key, which is what `cell_key.json` pins. The arithmetic
/// is exact on purpose: a quarter is a power of two, so dividing, flooring
/// and multiplying back cannot differ by a bit between the two languages,
/// and a two-decimal render of an exact quarter is exact.
library;

import 'dart:math' as math;

const double cellDegrees = 0.25;

// Rows count from the equator; the last row's south edge is 89.75, which
// is 90 / cellDegrees - 1 rows up.
const int _lastRow = 359;

/// The key of the quarter-degree tile containing ([lat], [lon]), named by
/// its south-west corner: `s0.25_e34.75` for Kisumu.
///
/// A corner belongs to the tile it names. Latitude 90 is filed under the
/// last row rather than a tile past the pole; a longitude of 180 or beyond
/// wraps west, so 180 is the west edge of the first tile and never the
/// east edge of a 361st.
String cellKey(double lat, double lon) {
  if (lat.isNaN || lon.isNaN) {
    throw ArgumentError('a cell needs a real point');
  }

  var longitude = lon;
  if (longitude >= 180.0 || longitude < -180.0) {
    longitude = ((longitude + 180.0) % 360.0) - 180.0;
  }

  final row = math.min((lat / cellDegrees).floor(), _lastRow);
  final col = (longitude / cellDegrees).floor();

  return '${_edge(row, 'n', 's')}_${_edge(col, 'e', 'w')}';
}

String _edge(int index, String positive, String negative) {
  final hemisphere = index < 0 ? negative : positive;
  return '$hemisphere${(index.abs() * cellDegrees).toStringAsFixed(2)}';
}
