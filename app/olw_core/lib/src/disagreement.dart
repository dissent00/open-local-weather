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
/// WHAT IS DELIBERATELY ABSENT. There is still no wind test, and upstream
/// item 144 CHANGED THE REASON without changing the answer. It used to be
/// PLACE: one wind field, the secondary point's, against a station at the
/// primary. The split gave the ashore wind its own field, so that objection
/// is gone. What remains is QUANTITY — the station side is `sknt`, the max
/// SUSTAINED wind, because METAR files a gust group only when a gust occurs
/// and none appeared on any of 932 rows in a 45-day sample, while the
/// forecast side is a GUST. Pairing them would read the gust factor as
/// weather.
library;

import 'models.dart';

/// What the forecast already committed to, from the standing issuance.
///
/// [rain] is the SCORED boolean, taken from the blend's own Day+0 row rather
/// than from the prose — the prose may hedge and the record does not.
class StandingCall {
  const StandingCall({
    this.rain,
    this.tempHighC,
    this.onsetHour,
    this.tempLowC,
  });

  final bool? rain;
  final double? tempHighC;

  /// The called overnight minimum — upstream item 143.
  final double? tempLowC;

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
  bool? lowIsSettled,
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

  // ONLY THE DECISIVE ONES REACH THIS LIST — upstream item 143.
  //
  // Membership here is a SPENDING decision: `llmShouldReason` treats any code
  // as grounds to buy a judgment call and a narrative. An ordinary divergence
  // is a footnote and footnotes do not re-forecast a day, so only the
  // near-freezing case — where it is the difference between ice and no ice —
  // is a contradiction. The gap itself is measured and stored regardless; see
  // [lowDivergence].
  final divergence =
      lowDivergence(standing, observed, lowIsSettled: lowIsSettled);
  if (divergence != null && divergence.decisive) {
    found.add(disagreementLowDiverges);
  }

  return found;
}

/// How far the station's overnight low must sit from the called one before it
/// is worth a reader's attention — upstream item 143.
///
/// TWO WIDTHS, BECAUSE ONE NUMBER CANNOT BE RIGHT. Two degrees is nothing at
/// 20 C and decisive at 2 C, where it is the difference between ice and no
/// ice. STEPPED, NOT INTERPOLATED: a smooth taper would imply the shape of
/// the relationship is understood, and it is not.
///
/// A DEFAULT, NOT AN ANSWER — upstream item 145 and this repo's item 20. What
/// counts as "a lot" depends on who is reading, so this becomes a tunable the
/// reader can change rather than a number to keep re-guessing. The reporting
/// band is safe to expose; a threshold reaching [observationDisagreements]
/// decides SPENDING and is not.
const double lowDivergenceMarginC = 3.0;
const double lowDivergenceFreezingMarginC = 1.0;

/// At or below this, the tight margin applies and the divergence is treated
/// as decision-grade. 4 C rather than 0 because ground frost forms while the
/// air is still above freezing.
const double nearFreezingC = 4.0;

/// The gap between the station's overnight low and the standing call, or null
/// when there is no settled comparison to make — upstream item 143.
///
/// THE ASYMMETRY, POINTED THE OTHER WAY. This library's header records that a
/// maximum only rises, so an observed high ABOVE the call settles it. A
/// minimum only FALLS, so the mirror holds: a station already BELOW the
/// called low has proved the call too high at any hour, while one sitting
/// ABOVE it has proved nothing until the night is over.
///
/// THREE-VALUED, and null is not false. Null [lowIsSettled] means the sun
/// times were unavailable, so the caller does not KNOW whether the night is
/// over, and unknown resolves to silence for the warmer case.
LowDivergence? lowDivergence(
  StandingCall standing,
  ObservedSoFar observed, {
  required bool? lowIsSettled,
  double marginC = lowDivergenceMarginC,
  double freezingMarginC = lowDivergenceFreezingMarginC,
}) {
  final called = standing.tempLowC;
  final seen = observed.lowC;
  if (called == null || seen == null) return null;

  final delta = seen - called;

  // Colder than called is settled on its own. Warmer needs the night behind
  // it, and `== true` rather than truthiness because null must not pass.
  if (delta > 0 && lowIsSettled != true) return null;

  final nearFreezing = (called < seen ? called : seen) <= nearFreezingC;
  final band = nearFreezing ? freezingMarginC : marginC;
  final notable = delta.abs() >= band;

  return LowDivergence(
    forecastC: called,
    observedC: seen,
    deltaC: delta,
    marginC: band,
    notable: notable,
    decisive: notable && nearFreezing,
  );
}
