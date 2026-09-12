// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00

/// Does what we can SEE contradict what we already said?
///
/// Upstream ROADMAP item 104, C2's third trigger. A judgment call is made
/// when the information moved, and "information" is not only a new model
/// cycle: observations are truth where models are opinion, and local sensors
/// update on their own schedule.
///
/// WHY THIS IS ARITHMETIC. Judging whether an observation contradicts a
/// forecast is itself judgment, and judgment is the expensive call being
/// decided on. A test that needed the model to run would be circular.
///
/// THE ASYMMETRY THAT GOVERNS EVERY TEST HERE. A mid-day observation can only
/// ever prove a forecast too LOW, never too high: rain that has fallen has
/// fallen, but a day that has not rained YET may still; and a maximum only
/// rises, so an observed high above the call settles it while one below means
/// the day is not over. Treating the symmetric case as a contradiction would
/// re-forecast every dry morning of every wet day.
///
/// WHAT IS DELIBERATELY ABSENT. There is no wind test, though the station
/// reports wind and `todayProperties` carries `peakWindKmh`. They describe
/// DIFFERENT PLACES — that field is the wind at the secondary point and the
/// station sits at the primary — so comparing them would report a
/// disagreement between two places as a disagreement with reality.
library;

/// What the forecast already committed to, from the standing issuance.
///
/// [rain] is the SCORED boolean, taken from the blend's own Day+0 row rather
/// than from the prose — the prose may hedge and the record does not.
class StandingCall {
  const StandingCall({this.rain, this.tempHighC});

  final bool? rain;
  final double? tempHighC;
}

/// What the station has actually reported TODAY, so far.
///
/// Both fields are three-valued and absence means absence: a station that
/// reported nothing is not a station reporting agreement.
class ObservedSoFar {
  const ObservedSoFar({this.precipitation, this.highC});

  final bool? precipitation;
  final double? highC;
}

const String disagreementRainWhileDry = 'rain_observed_while_dry_called';
const String disagreementHighExceeded = 'high_already_exceeded';

/// How far above the standing high an observation must sit before it counts.
///
/// SIZED AGAINST TWO MEASURED QUANTITIES, not picked for roundness. The
/// station reads +0.43 C against the reanalysis on average, and the blend's
/// Day+0 high error runs a few tenths. A margin at or below either would fire
/// on the instrument rather than on the weather, and every spurious firing
/// spends an LLM call. Conservative and not yet measured — revisit against
/// the record, not against a convenient sample.
const double tempContradictionMarginC = 2.0;

/// Codes for every way the observation settles against the standing call.
///
/// Empty means "nothing seen contradicts what we said", which is NOT the same
/// as "the forecast is right" — most of the day is usually still ahead.
///
/// Order is stable because the result is compared across two languages, which
/// makes it part of the contract rather than an implementation detail.
List<String> observationDisagreements(
  StandingCall standing,
  ObservedSoFar observed, {
  double tempMarginC = tempContradictionMarginC,
}) {
  final found = <String>[];

  if (standing.rain == false && observed.precipitation == true) {
    found.add(disagreementRainWhileDry);
  }

  final high = standing.tempHighC;
  final seen = observed.highC;
  if (high != null && seen != null && seen >= high + tempMarginC) {
    found.add(disagreementHighExceeded);
  }

  return found;
}
