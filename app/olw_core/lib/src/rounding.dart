// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Decimal rounding that matches Python's `round(v, places)` exactly.
///
/// WHY THIS EXISTS. Sibling of `sums.dart`, and for the same reason: figures
/// are computed in both languages and pinned by vectors that compare EXACTLY,
/// so Dart converges to Python rather than the other way round. Python is
/// what produced every number already in the record and on the site.
///
/// The obvious Dart spelling, `(v * scale).roundToDouble() / scale`, is wrong
/// twice over, and the multiply causes both faults.
///
/// 1. `roundToDouble` rounds half AWAY FROM ZERO. Python rounds half to EVEN.
///    Long known here — `_roundHalfEven` in models.dart and `_fmt0` in
///    synoptic.dart both guard it, and `temp_high_low.json` exists for it.
///
/// 2. **Scaling INVENTS ties that the value does not have.** −11.95 is stored
///    as −11.94999999999999928, which Python correctly rounds to −11.9. But
///    `−11.95 * 10` lands exactly on −119.5, and the tie it manufactured then
///    rounds away to −12.0. This one none of the existing guards cover, and it
///    is why applying half-to-even to the SCALED value does not fix anything:
///    measured at 484 disagreements against the original's 600.
///
/// Measured 2026-09-08 on the day-over-day comparison, which was using the
/// broken form: 600 of 32,001 swept values disagreed, and roughly 3.6% of
/// realistic days hit one, because a MEAN divides by two to six models and
/// lands on x.x5 constantly. Two crossed a band edge and changed the words a
/// reader sees — "much cooler" against "dramatically cooler".
///
/// `toStringAsFixed` does the decimal rounding from the double's TRUE value
/// rather than a scaled copy, which fixes fault 2 outright. It differs from
/// Python only on a genuinely exact tie, which is handled separately below.
library;

/// The only values that are EXACT ties at [places] decimals.
///
/// A tie is (2m+1)/(2·10^n). For that to be representable as a double the
/// numerator must carry the whole 5^n, leaving j/2^(n+1) with j odd. So the
/// ties are odd QUARTERS at one place, odd EIGHTHS at two, odd sixteenths at
/// three — and the test scales by 2^(n+1), which is exact, being a power of
/// two. That is what lets this detect a tie without creating one.
///
/// THE SCALE IS PER-PRECISION AND MUST NOT BE COPIED ACROSS. Reusing one
/// place's `* 4` at two places was measured making things WORSE — 544
/// disagreements against a broken original's 378 — because 27.25 is not a tie
/// at two decimals and the test claimed it was.
bool _isExactTie(double v, int places) {
  final scale = (1 << (places + 1)).toDouble();
  final scaled = v * scale;

  return scaled == scaled.roundToDouble() && scaled.abs() % 2 == 1;
}

/// Matches Python's `round(v, places)`. Use this, never `roundToDouble`.
double roundLikePython(double v, int places) {
  if (!_isExactTie(v, places)) {
    return double.parse(v.toStringAsFixed(places));
  }

  // An exact tie: v * 10^places is exactly some integer plus a half, so the
  // floor is that integer and half-to-even picks it or its successor.
  final scale = _pow10(places);
  final below = (v * scale).floorToDouble();

  return (below % 2 == 0 ? below : below + 1) / scale;
}

double _pow10(int places) {
  var scale = 1.0;
  for (var i = 0; i < places; i++) {
    scale *= 10;
  }

  return scale;
}
