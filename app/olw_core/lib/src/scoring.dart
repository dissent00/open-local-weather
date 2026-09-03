// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'dates.dart';
import 'brier.dart';
import 'models.dart';

/// Scores one model's stored prediction against one day's actual.
///
/// Returns `null` when there is nothing to score. Three distinct reasons,
/// all of which must stay distinct:
///  - the prediction is missing
///  - the actual is missing
///  - the prediction's `rain` is `null`, meaning the model had no data at
///    this lead time. Scoring that would invent skill out of a gap.
VerificationScore? scorePrediction(
  ModelPrediction? predicted,
  DailyActual? actual,
  int leadTimeDays,
) {
  if (predicted == null || actual == null) return null;
  if (predicted.rain == null) return null;

  // Scored against observed CONVECTION, not against reanalysis rain alone. A
  // thunderstorm the airport watched pass overhead counts even when the grid
  // cell recorded half a millimetre — see DailyActual.observedConvection.
  final observedRain = actual.observedConvection();
  final rainCorrect = predicted.rain == observedRain;

  double? onsetErrorHrs;
  // Onset error is Day+0 only: Day+3/+7 predictions carry no onset timing to
  // begin with, so a non-null value at those lead times would be fabricated.
  if (leadTimeDays == 0 &&
      observedRain &&
      predicted.onset != null &&
      actual.onsetHour != null) {
    onsetErrorHrs = _hourDiff(predicted.onset!, actual.onsetHour!);
  }

  // Convention: actual − predicted. Positive means the actual came in above
  // what the model called.
  double? diff(double? pred, double? act) =>
      (pred == null || act == null) ? null : act - pred;

  // The percentage-to-probability conversion happens HERE and only here, so
  // it is one visible line rather than a thing every caller must remember.
  // brierScore throws on a value outside [0, 1] precisely to catch a missed
  // conversion, which would otherwise score 6241 and pass silently into every
  // mean it touched.
  final prob = predicted.rainProbabilityPct;
  final rainBrier = prob == null ? null : brierScore(prob / 100, observedRain);

  return VerificationScore(
    rainCorrect: rainCorrect,
    rainBrier: rainBrier,
    onsetErrorHrs: onsetErrorHrs,
    windErrorKmh: diff(predicted.windKmh, actual.peakWindKmh),
    highErrorC: diff(predicted.highC, actual.highC),
    lowErrorC: diff(predicted.lowC, actual.lowC),
    mslpErrorHpa: diff(predicted.mslpTrend, actual.mslpTrend),
  );
}

double _hourDiff(String predictedHhmm, String actualHhmm) {
  int toMinutes(String hhmm) {
    final i = hhmm.indexOf(':');
    if (i < 0) return int.parse(hhmm) * 60;
    return int.parse(hhmm.substring(0, i)) * 60 +
        int.parse(hhmm.substring(i + 1));
  }

  return (toMinutes(actualHhmm) - toMinutes(predictedHhmm)) / 60;
}

/// Arithmetic mean ignoring nulls; `null` when nothing is present.
double? mean(List<double?> values) {
  final present = values.whereType<double>().toList();
  if (present.isEmpty) return null;
  return present.reduce((a, b) => a + b) / present.length;
}

/// Outcome of comparing recent skill against the longer-term baseline.
class RainPctTrend {
  /// "improving" | "declining" | "stable", or `null` when there is not yet
  /// enough history in one of the windows to say anything honest.
  final String? label;
  final double? delta;

  const RainPctTrend(this.label, this.delta);
}

/// Deterministic recent-vs-longer-term skill comparison, computed in code so
/// the LLM is handed a conclusion rather than asked to eyeball three numbers
/// and subtract them itself.
///
/// Returns nulls when either window is too thin — "insufficient data" is
/// itself information, and fabricating a trend from three checks would be
/// worse than saying nothing.
RainPctTrend computeRainPctTrend({
  required double? rolling10RainPct,
  required double? rolling30RainPct,
  required int checksInWindow10,
  required int checksInWindow30,
  required int minChecksShort,
  required int minChecksLong,
  required double thresholdPct,
}) {
  if (rolling10RainPct == null ||
      rolling30RainPct == null ||
      checksInWindow10 < minChecksShort ||
      checksInWindow30 < minChecksLong) {
    return const RainPctTrend(null, null);
  }

  final delta = rolling10RainPct - rolling30RainPct;
  // Boundaries are inclusive: exactly at the threshold counts as a trend.
  final label = delta >= thresholdPct
      ? 'improving'
      : (delta <= -thresholdPct ? 'declining' : 'stable');
  return RainPctTrend(label, delta);
}

/// Every scoreable check for one (model, lead time), newest first.
///
/// Storage-agnostic by design: takes a lookup rather than a log-entry type,
/// because on a phone the history is a database table, not JSON files on
/// disk. Mirrors `collect_scores` in the Python implementation.
List<MapEntry<DateTime, VerificationScore>> collectScores({
  required String model,
  required int leadTimeDays,
  required DateTime yesterday,
  required DateTime earliestTargetDate,
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays) predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
}) {
  final scored = <MapEntry<DateTime, VerificationScore>>[];
  var cursor = yesterday;
  while (!cursor.isBefore(earliestTargetDate)) {
    final rowDate = predictionRowDateForTarget(cursor, leadTimeDays);
    final predictions = predictionsFor(rowDate, leadTimeDays);
    final actual = actualFor(cursor);
    if (predictions != null && actual != null) {
      ModelPrediction? prediction;
      for (final p in predictions) {
        if (p.model == model) {
          prediction = p;
          break;
        }
      }
      final score = scorePrediction(prediction, actual, leadTimeDays);
      if (score != null) scored.add(MapEntry(cursor, score));
    }
    cursor = addDays(cursor, -1);
  }
  return scored;
}
