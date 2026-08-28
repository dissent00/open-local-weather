// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Which model RUN (initialisation cycle) is likely behind the guidance in
/// hand right now — an INFERENCE, not an observation.
///
/// Port of `cycle.py`; see that module's docstring for the full reasoning:
/// why this exists (nothing in an Open-Meteo response says which cycle
/// produced it), where the table came from (docs-internal/ROADMAP.md's
/// measured aligned-window table), why an estimate is worth having anyway,
/// and the 00:27 UTC incident it exists to make visible. Note in particular
/// what the answer MEANS: outside the ~2 hours a window stays clean, this is
/// the cycle the SLOWEST model is still on, so it reads as a floor on the
/// age of the guidance rather than a description of the whole blend. Held to the
/// Python behaviour by the shared vector in `spec/vectors/aligned_cycle.json`
/// — see `test/vectors_test.dart`. Not yet wired into anything on this side
/// either.
library;

import 'dates.dart';

/// The model run this project infers is aligned across all five fetched
/// models at a given moment.
class AlignedCycle {
  const AlignedCycle({
    required this.initialisedAt,
    required this.windowOpenedAt,
    required this.ageHours,
  });

  /// The model run's own initialisation time, UTC.
  final DateTime initialisedAt;

  /// When this project measured that cycle as aligned and available, UTC.
  final DateTime windowOpenedAt;

  /// now - initialisedAt, in hours.
  final double ageHours;
}

DateTime _at(DateTime day, int hour) =>
    DateTime.utc(day.year, day.month, day.day, hour);

/// The cycle this project infers is aligned across all five models at
/// [now], per the measured table in `cycle.py`'s docstring
/// (docs-internal/ROADMAP.md, search "Aligned windows open at").
///
/// [now] must be UTC (`now.isUtc`) — rejected rather than silently misread.
/// Python's precondition is "timezone-aware UTC"; Dart's `DateTime` has no
/// separate naive/aware state, only an `isUtc` flag, so the equivalent
/// check here is that flag. A local [now] would misclassify the row by
/// bucketing on the wrong hour without raising anywhere near the mistake.
AlignedCycle alignedCycleAt(DateTime now) {
  if (!now.isUtc) {
    throw ArgumentError.value(
      now,
      'now',
      'alignedCycleAt requires a UTC DateTime',
    );
  }

  final today = DateTime.utc(now.year, now.month, now.day);
  final yesterday = addDays(today, -1);

  // docs-internal/ROADMAP.md's measured aligned-window table: the UTC hour
  // a window opens and which cycle — whose calendar day — is aligned from
  // that hour. Checked in descending hour order; each 6-hour window is
  // exactly wide enough to reach the next one, except 12z's, which spans
  // midnight and so is written here as two date-relative branches.
  final DateTime windowOpenedAt;
  final DateTime initialisedAt;
  if (now.hour >= 20) {
    windowOpenedAt = _at(today, 20);
    initialisedAt = _at(today, 12);
  } else if (now.hour >= 14) {
    windowOpenedAt = _at(today, 14);
    initialisedAt = _at(today, 6);
  } else if (now.hour >= 8) {
    windowOpenedAt = _at(today, 8);
    initialisedAt = _at(today, 0);
  } else if (now.hour >= 2) {
    windowOpenedAt = _at(today, 2);
    initialisedAt = _at(yesterday, 18);
  } else {
    windowOpenedAt = _at(yesterday, 20);
    initialisedAt = _at(yesterday, 12);
  }

  // Microseconds, not seconds, dividing into a Duration in hours: matches
  // hoursOld() in aqi.dart, which uses the same pattern to avoid the loss
  // of precision an inSeconds-based division would carry.
  final ageHours = now.difference(initialisedAt).inMicroseconds /
      Duration.microsecondsPerHour;

  return AlignedCycle(
    initialisedAt: initialisedAt,
    windowOpenedAt: windowOpenedAt,
    ageHours: ageHours,
  );
}

/// One decimal place, half-to-even, computed the same way in both languages
/// — deliberately NOT a language's own rounding.
///
/// See `round_hours_to_tenths` in cycle.py for the measured finding: a
/// scale-and-round-half-even here disagreed with Python's `round(x, 1)` on
/// 962 of 4801 values swept at 0.05 steps, because Python rounds the DECIMAL
/// expansion of a binary float. Both sides now do identical IEEE-754
/// arithmetic, which is exactly specified and so bit-identical across
/// languages; the same sweep gives 0 divergences. Vector-locked. Do not
/// "simplify" either side back to `.round()` or `round()`.
double roundHoursToTenths(double hours) {
  final scaled = hours * 10;
  final floor = scaled.floor();
  final frac = scaled - floor;

  final int tenths;
  if (frac > 0.5) {
    tenths = floor + 1;
  } else if (frac < 0.5) {
    tenths = floor;
  } else {
    tenths = floor.isEven ? floor : floor + 1;
  }

  return tenths / 10;
}
