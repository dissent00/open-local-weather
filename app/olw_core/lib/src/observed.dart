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

  final clock = (asOf != null && asOf.isNotEmpty) ? 'As of $asOf' : 'So far today';
  // The reach, always, when the station gave one — upstream item 151: the
  // clauses are cumulative and stay true, but "so far" has to say how far.
  final reach = observed.reportedThrough;
  final opening = (reach != null && reach.isNotEmpty) ? '$clock, reports through $reach' : clock;
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

/// The overnight-low footnote, or null when there is nothing to footnote —
/// upstream item 143.
///
/// A FACT ABOUT ONE STATION, NOT A CLAIM ABOUT THE BASIN. The sentence names
/// the place and both numbers and asserts nothing about anywhere else: a
/// measuring station can be warmer than the country around it and the
/// forecast can be wrong, and this cannot tell which.
///
/// SILENCE IS THE DEFAULT. Only a [LowDivergence.notable] gap gets a
/// sentence, because a footnote that appears every day stops being read on
/// the day it matters.
///
/// WHY CODE WRITES IT RATHER THAN THE MODEL. OBSERVED SO FAR TODAY and THE
/// FORECASTER'S CALL both arrive locked verbatim and can disagree about the
/// same quantity, so the instruction set REQUIRED publishing two lows for one
/// day. A pre-computed sentence is the way out: there is exactly one
/// sanctioned form of words that mentions both, and the model's only choice
/// is whether to use it.
String? describeLowDivergence(LowDivergence? divergence, String stationName) {
  if (divergence == null || !divergence.notable) return null;

  return '$stationName recorded an overnight low of '
      '${formatTempC(divergence.observedC, decimals: 1)} against a forecast of '
      '${formatTempC(divergence.forecastC, decimals: 1)}. That is this one '
      'station, not the wider area: it does not say nowhere reached the '
      'forecast low.';
}

/// The footnotes for a notable high and a notable onset — port of Python's
/// `describe_notable_disagreements`, upstream ROADMAP item 145. One sentence
/// per code in the reporting list's order; nothing for the low, which has
/// [describeLowDivergence], or for rain, which OBSERVED SO FAR already
/// carries. The same shape as the low's: the place, both numbers, and a
/// claim about nothing wider. A code missing its numbers gets no sentence.
List<String> describeNotableDisagreements(
  List<String>? codes,
  StandingCall standing,
  ObservedSoFar observed,
  String stationName,
) {
  if (codes == null || codes.isEmpty) return const [];

  final notes = <String>[];
  for (final code in codes) {
    final calledHigh = standing.tempHighC;
    final seenHigh = observed.highC;
    if (code == disagreementHighExceeded && calledHigh != null && seenHigh != null) {
      notes.add('$stationName has already recorded '
          '${formatTempC(seenHigh, decimals: 1)} today against a forecast '
          'high of ${formatTempC(calledHigh, decimals: 1)}. That is this '
          'one station, not the wider area: it does not say everywhere has '
          'passed the forecast high.');
    }
    final calledOnset = standing.onsetHour;
    final seenOnset = observed.precipitationOnset;
    if (code == disagreementOnsetAlreadyPassed &&
        calledOnset != null &&
        calledOnset.isNotEmpty &&
        seenOnset != null &&
        seenOnset.isNotEmpty) {
      notes.add('$stationName saw rain from $seenOnset, against '
          'a forecast onset of $calledOnset. That is this one station, '
          'not the wider area: it does not say rain has started everywhere.');
    }
  }
  return notes;
}
