// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00

/// What the station has already seen today, composed for a reader.
///
/// Upstream ROADMAP item 121, ported from `observed.py`. An observation is a
/// measured fact, and this project's first principle is that facts are
/// composed in code and never asked of the model. Until this existed the only
/// way "the station has recorded 27 °C and rain since 13:00" reached anybody
/// was by paying for an LLM call to restate it — the one call nobody should
/// have to make, and the reason a reader refreshing often had to choose
/// between cost and currency.
///
/// THREE-VALUED THROUGHOUT. `thunder: false` means the station reported and
/// saw none, which a reader wants at 14:00; `thunder: null` means nothing was
/// measured, which is omitted rather than rendered as a negative.
///
/// THE CLAUSE ORDER IS PART OF THE CONTRACT, pinned by
/// `spec/vectors/observed_so_far.json` and compared exactly against Python.
library;

import 'disagreement.dart';
import 'models.dart';
import 'rounding.dart';

/// One finished sentence for what has been measured today, or null when
/// nothing has.
///
/// Null rather than a cheerful "nothing to report": a station that did not
/// answer has not reported a quiet day, and the caller renders a gap as a gap.
///
/// [asOf] is the issuance's local "HH:MM". A reader has to be told how current
/// this is, because "no rain so far" means something very different at 09:00
/// and at 21:00.
String? describeObservedSoFar(ObservedSoFar? observed, {String? asOf}) {
  if (observed == null) return null;

  final said = <String>[
    for (final clause in <String?>[
      _rain(observed),
      _thunder(observed),
      _temperature('high so far', observed.highC),
      _temperature('low so far', observed.lowC),
      _gust(observed.peakWindKmh),
      _sky(observed.cloudOktas),
    ])
      if (clause != null) clause,
  ];
  if (said.isEmpty) return null;

  final opening = (asOf != null && asOf.isNotEmpty) ? 'As of $asOf' : 'So far today';
  return '$opening: ${said.join('; ')}.';
}

String? _rain(ObservedSoFar observed) {
  final fell = observed.precipitation;
  if (fell == null) return null;
  if (!fell) return 'no rain';

  // The onset is the more useful half and is reported when the station caught
  // it. A station that saw rain without a first-seen time still says so;
  // dropping the clause for want of the clock would lose the fact.
  final onset = observed.precipitationOnset;
  if (onset != null && onset.isNotEmpty) return 'rain from $onset';

  return 'rain';
}

String? _thunder(ObservedSoFar observed) {
  final heard = observed.thunder;
  if (heard == null) return null;

  return heard ? 'thunder' : 'no thunder';
}

String? _temperature(String label, double? celsius) {
  if (celsius == null) return null;

  // formatTempC, never a local round: one rounding site per quantity is the
  // rule this project arrived at the expensive way — upstream item 88.
  return '$label ${formatTempC(celsius)}';
}

String? _gust(double? kmh) {
  if (kmh == null) return null;

  // roundLikePython at zero places, which is half-to-EVEN and verified
  // against Python on every tie including -0.5. Dart's own .round() goes
  // half away from zero and would publish 33 where Python publishes 32.
  return 'peak gust ${roundLikePython(kmh, 0).toInt()} km/h';
}

String? _sky(double? oktas) {
  if (oktas == null) return null;

  // Eighths, which is how a METAR reports cover and how the mean of a day's
  // reports stays comparable with a single one.
  return 'sky ${roundLikePython(oktas, 0).toInt()}/8';
}
