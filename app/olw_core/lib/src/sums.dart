// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Floating-point summation that matches Python's `sum()` bit for bit.
///
/// WHY THIS EXISTS. Every averaged figure this project publishes — rain
/// percentages, mean errors, Brier scores — is computed in both languages and
/// pinned by vectors that compare EXACTLY. A plain left-to-right accumulation
/// (`values.reduce((a, b) => a + b)`) does not agree with Python here:
/// CPython 3.12 changed `sum()` to use Neumaier compensated summation, so the
/// two drift by an ULP whenever the addends have mixed magnitudes.
///
/// Measured 2026-09-06: five values of 0.81 followed by five of
/// 0.009999999999999995 average to 0.41000000000000003 in Python and 0.41
/// under `reduce`. The existing vectors had not caught it because their
/// values happened to sum exactly.
///
/// Dart converges to Python rather than the other way round, deliberately.
/// Python's is the more accurate algorithm AND the one that produced every
/// figure already committed to the record and published on the site; making
/// Python match Dart would have been the cheaper edit and would have moved
/// the last bit of numbers that are already public.
library;

/// Neumaier summation: the running compensation `c` collects the low-order
/// bits lost in each addition, and is applied once at the end.
///
/// This is CPython's algorithm for `sum()` over floats, transcribed. The
/// branch is the load-bearing part — it decides which operand's low bits were
/// the ones lost, and swapping it silently degrades to ordinary summation on
/// exactly the inputs compensation exists for.
double compensatedSum(Iterable<double> values) {
  var sum = 0.0;
  var compensation = 0.0;

  for (final x in values) {
    final t = sum + x;
    if (sum.abs() >= x.abs()) {
      compensation += (sum - t) + x;
    } else {
      compensation += (x - t) + sum;
    }
    sum = t;
  }

  return sum + compensation;
}
