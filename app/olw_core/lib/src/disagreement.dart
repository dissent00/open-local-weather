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

import 'models.dart';

/// What the forecast already committed to, from the standing issuance.
///
/// [rain] is the SCORED boolean, taken from the blend's own Day+0 row rather
/// than from the prose — the prose may hedge and the record does not.
class StandingCall {
  const StandingCall({this.rain, this.tempHighC, this.onsetHour});

  final bool? rain;
  final double? tempHighC;

  /// "HH:MM", the hour the standing call put the rain's arrival at. Separate
  /// from [rain] because a call can be right about the DAY and wrong about
  /// the HOUR — upstream item 138.
  final String? onsetHour;
}

/// "HH:MM" as minutes past midnight, or null if it is not that.
///
/// PARSED RATHER THAN COMPARED AS TEXT: '9:00' sorts after '18:00', and one
/// unpadded hour would invert the test silently, in the direction that
/// suppresses a real contradiction.
int? _minutes(String? hhmm) {
  if (hhmm == null || hhmm.isEmpty) return null;
  final parts = hhmm.split(':');
  if (parts.length != 2) return null;
  final hours = int.tryParse(parts[0]);
  final minutes = int.tryParse(parts[1]);
  if (hours == null || minutes == null) return null;
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
  return hours * 60 + minutes;
}

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
  int onsetMarginMin = onsetContradictionMarginMin,
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

  // THE CALL IS RIGHT ABOUT THE DAY AND WRONG ABOUT THE HOUR — item 138.
  // rainWhileDry cannot see this: it needs rain == false, and here the
  // forecast agreed rain was coming and put it too late. One-directional
  // like every other test here — rain that has not arrived by the called
  // hour proves nothing, because the day is not over.
  final called = _minutes(standing.onsetHour);
  final seenAt = _minutes(observed.precipitationOnset);
  if (called != null && seenAt != null && seenAt <= called - onsetMarginMin) {
    found.add(disagreementOnsetAlreadyPassed);
  }

  return found;
}
