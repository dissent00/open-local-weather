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

/// Windows open every six hours, which is the model cycle interval itself —
/// 00/06/12/18z. Named because [nextAlignedWindow] steps by it.
const int windowIntervalHours = 6;

/// When the next aligned window opens, and which cycle it will bring.
class NextAlignedWindow {
  const NextAlignedWindow({
    required this.opensAt,
    required this.initialisedAt,
  });

  /// When the next window opens, UTC.
  final DateTime opensAt;

  /// The cycle that window will carry, UTC.
  final DateTime initialisedAt;
}

/// The forward-looking half of [alignedCycleAt]. Port of
/// `cycle.next_aligned_window`; pinned by `spec/vectors/next_aligned_window.json`.
///
/// SAME INFERENCE, SAME CAVEAT. This reads the same hand-measured table, so it
/// estimates when guidance USUALLY lands and never promises it. Item 50
/// measured ECMWF's availability varying from ~7.1 h to 8h25m — more than the
/// whole hour the windows are rounded to — so anything showing this to a
/// person must say "usually by about X", never "at X". A notice that names an
/// exact time and is wrong twice teaches the reader to ignore every notice.
///
/// STRICTLY AFTER [now], including exactly on a boundary: at 08:00 the 08:00
/// window has just opened, and pointing a reader at it would tell them to wait
/// for guidance they already hold.
NextAlignedWindow nextAlignedWindow(DateTime now) {
  if (!now.isUtc) {
    throw ArgumentError.value(
      now,
      'now',
      'nextAlignedWindow requires a UTC DateTime',
    );
  }

  // Derived from alignedCycleAt rather than restating its table: two encodings
  // of one measurement is exactly how the two drift apart.
  var opensAt = alignedCycleAt(now)
      .windowOpenedAt
      .add(const Duration(hours: windowIntervalHours));

  // A window already open but not yet reached by the clock cannot be "next".
  // Only possible on the branch that spans midnight, where windowOpenedAt
  // belongs to yesterday.
  while (!opensAt.isAfter(now)) {
    opensAt = opensAt.add(const Duration(hours: windowIntervalHours));
  }

  return NextAlignedWindow(
    opensAt: opensAt,
    initialisedAt:
        alignedCycleAt(opensAt.add(const Duration(minutes: 1))).initialisedAt,
  );
}

/// "New model guidance is usually in by about HH:MM local" — or an empty
/// string when the location's offset is unknown.
///
/// Port of `pipeline._next_guidance_sentence`. The two take different
/// arguments and must produce the same sentence: Python has a timezone
/// database and is handed a zone name, this package deliberately has neither
/// (see pubspec.yaml) and is handed the offset the hourly response already
/// carries.
///
/// [nowLocal] is a wall clock at the LOCATION, which is what generateForecast
/// works in — its field values are the place's local time and its isUtc flag
/// means nothing. `nowLocal.toUtc()` would convert using the DEVICE's offset,
/// which is a guess about where the reader is standing relative to the place
/// they asked about, so the instant is reconstructed from the fields and the
/// location's own offset instead. Same reasoning as sunTimes taking the
/// offset rather than deriving one.
///
/// AN EMPTY STRING WHEN THE OFFSET IS UNKNOWN, never a guessed time. A notice
/// that names the wrong hour is worse than one that names none: it is checked
/// against reality by the reader, and it teaches them to ignore the next one.
String nextGuidanceSentence({
  required DateTime nowLocal,
  required int? utcOffsetSeconds,
}) {
  if (utcOffsetSeconds == null) {
    return '';
  }

  final offset = Duration(seconds: utcOffsetSeconds);
  final nowUtc = DateTime.utc(
    nowLocal.year,
    nowLocal.month,
    nowLocal.day,
    nowLocal.hour,
    nowLocal.minute,
    nowLocal.second,
  ).subtract(offset);

  final opensLocal = nextAlignedWindow(nowUtc).opensAt.add(offset);
  final hh = opensLocal.hour.toString().padLeft(2, '0');
  final mm = opensLocal.minute.toString().padLeft(2, '0');
  return 'New model guidance is usually in by about $hh:$mm local; '
      'a forecast made after that would normally have the full window.';
}
