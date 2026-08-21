// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'config.dart';
import 'dates.dart';
import 'models.dart';
import 'scoring.dart';

/// Re-deriving every accuracy figure from the raw record.
///
/// Port of Python's `verify/scoring.py` rolling/all-time halves and
/// `verify/pipeline.py`. This is the credibility of the whole project: it is
/// what turns "this app is accurate" from a claim into a measurement, and an
/// app that generated forecasts without it could never show the accuracy
/// record that distinguishes it.
///
/// THE PROPERTY THAT MATTERS: nothing here is carried forward. Every figure —
/// rolling windows and all-time alike — is recomputed from the stored
/// predictions plus freshly fetched observations, so there is no running
/// total that can silently drift.
///
/// All-time was an incremental counter once. It was replaced after Open-Meteo
/// was observed revising recent observations: a day served as "rain, 29.6C"
/// at 06:07 came back as "no rain, 30.5C" hours later. An incremental counter
/// bakes the provisional verdict in permanently, making all-time the one
/// number that has to be trusted rather than verified — which cuts directly
/// against the auditability the project rests on. Re-derivation also makes
/// double-counting structurally impossible rather than something a guard has
/// to catch.

/// Rolling stats for one (model, lead time) over a bounded window.
class RollingWindowResult {
  const RollingWindowResult({
    required this.checksFound,
    required this.rainPct,
    required this.onsetErr,
    required this.windErr,
    required this.highErr,
    required this.lowErr,
    required this.mslpErr,
  });

  /// How many of the window actually had data. Load-bearing for cold-start
  /// honesty: 6/10 checks is a very different claim from 10/10.
  final int checksFound;

  final double? rainPct;
  final double? onsetErr;
  final double? windErr;
  final double? highErr;
  final double? lowErr;
  final double? mslpErr;
}

/// Walks backward from [yesterday] collecting up to [windowSize] scoreable
/// checks.
///
/// The search is bounded beyond the window size so a run of missing days
/// cannot turn this into an unbounded walk over the whole record.
RollingWindowResult rescoreRollingWindow({
  required String model,
  required int leadTimeDays,
  required int windowSize,
  required DateTime yesterday,
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays)
      predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
}) {
  final scores = <VerificationScore>[];
  var cursor = yesterday;
  var daysSearched = 0;
  final maxSearch = windowSize + 30;

  while (scores.length < windowSize && daysSearched < maxSearch) {
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
      if (score != null) scores.add(score);
    }
    cursor = addDays(cursor, -1);
    daysSearched++;
  }

  return RollingWindowResult(
    checksFound: scores.length,
    rainPct: scores.isEmpty
        ? null
        : 100 * scores.where((s) => s.rainCorrect).length / scores.length,
    onsetErr: mean([for (final s in scores) s.onsetErrorHrs]),
    windErr: mean([for (final s in scores) s.windErrorKmh]),
    highErr: mean([for (final s in scores) s.highErrorC]),
    lowErr: mean([for (final s in scores) s.lowErrorC]),
    mslpErr: mean([for (final s in scores) s.mslpErrorHpa]),
  );
}

/// All-time counts for one (model, lead time), re-derived over the whole
/// stored record rather than carried forward. See the file header for why.
class AllTimeResult {
  const AllTimeResult({
    required this.checks,
    required this.correct,
    required this.pct,
    required this.earliestTargetDate,
    required this.latestTargetDate,
  });

  final int checks;
  final int correct;
  final double? pct;
  final DateTime? earliestTargetDate;
  final DateTime? latestTargetDate;
}

/// Walks the ENTIRE record.
///
/// The caller must pass an [earliestTargetDate] covering the whole record and
/// supply observations spanning it. Deriving against a short window would
/// silently SHRINK all-time rather than fail — which is why the caller pairs
/// this with the safety rail in [runVerification].
AllTimeResult rescoreAllTime({
  required String model,
  required int leadTimeDays,
  required DateTime yesterday,
  required DateTime earliestTargetDate,
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays)
      predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
}) {
  final scored = collectScores(
    model: model,
    leadTimeDays: leadTimeDays,
    yesterday: yesterday,
    earliestTargetDate: earliestTargetDate,
    predictionsFor: predictionsFor,
    actualFor: actualFor,
  );
  final checks = scored.length;
  final correct = scored.where((e) => e.value.rainCorrect).length;
  return AllTimeResult(
    checks: checks,
    correct: correct,
    pct: checks == 0 ? null : 100 * correct / checks,
    // collectScores walks backward, so the first entry is the most recent.
    earliestTargetDate: scored.isEmpty ? null : scored.last.key,
    latestTargetDate: scored.isEmpty ? null : scored.first.key,
  );
}

/// One (model, lead time) pair's accuracy record.
class TrackRecordEntry {
  TrackRecordEntry({
    required this.model,
    required this.leadTimeDays,
    this.rolling10RainPct,
    this.rolling30RainPct,
    this.rainPctTrend,
    this.rainPctTrendDelta,
    this.allTimeChecks = 0,
    this.allTimeCorrect = 0,
    this.allTimeRainPct,
    this.allTimeEarliestTargetDate,
    this.avgOnsetErrorHrs10,
    this.avgWindErrorKmh10,
    this.avgTempHighErrorC10,
    this.avgTempLowErrorC10,
    this.avgMslpTrendErrorHpa10,
    this.checksInWindow10 = 0,
    this.lastUpdated,
  });

  final String model;
  final int leadTimeDays;
  double? rolling10RainPct;
  double? rolling30RainPct;
  String? rainPctTrend;
  double? rainPctTrendDelta;
  int allTimeChecks;
  int allTimeCorrect;
  double? allTimeRainPct;
  DateTime? allTimeEarliestTargetDate;
  double? avgOnsetErrorHrs10;
  double? avgWindErrorKmh10;
  double? avgTempHighErrorC10;
  double? avgTempLowErrorC10;
  double? avgMslpTrendErrorHpa10;
  int checksInWindow10;
  DateTime? lastUpdated;
}

/// What one lead time produced when scored against yesterday.
class LeadTimeResult {
  const LeadTimeResult({
    required this.leadTimeDays,
    required this.targetDateVerified,
    required this.perModelScores,
  });

  final int leadTimeDays;

  /// Null when there was no stored prediction or no observation to score
  /// against — a gap in the record, or a cold-start day with no history.
  final DateTime? targetDateVerified;
  final Map<String, VerificationScore> perModelScores;
}

class VerificationRunResult {
  const VerificationRunResult({
    required this.leadTimeResults,
    required this.trackRecord,
    required this.newlyVerified,
    required this.warnings,
  });

  final List<LeadTimeResult> leadTimeResults;
  final List<TrackRecordEntry> trackRecord;

  /// (rowDate, leadTimeDays) pairs whose stored entry should be marked
  /// verified. Applied by the caller, since this function only reads.
  final List<MapEntry<DateTime, int>> newlyVerified;

  /// Problems worth surfacing rather than swallowing — currently the
  /// all-time shrink rail. Returned rather than printed so each surface
  /// decides how to show them.
  final List<String> warnings;
}

/// The full verification pass. Pure: reads, computes, returns.
VerificationRunResult runVerification({
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays)
      predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
  required List<TrackRecordEntry> priorTrackRecord,
  required DateTime today,
  required DateTime yesterday,
  required DateTime earliestRecordDate,
  List<String> models = defaultModels,
  List<int> leadTimesDays = leadTimesDays_,
  int windowShort = rollingWindowShort,
  int windowLong = rollingWindowLong,
}) {
  final byKey = <String, TrackRecordEntry>{
    for (final e in priorTrackRecord) '${e.model}|${e.leadTimeDays}': e,
  };

  final yesterdayActual = actualFor(yesterday);
  final leadTimeResults = <LeadTimeResult>[];
  final newlyVerified = <MapEntry<DateTime, int>>[];
  final warnings = <String>[];

  for (final k in leadTimesDays) {
    final targetRowDate = predictionRowDateForTarget(yesterday, k);
    final entry = predictionsFor(targetRowDate, k);

    final perModelScores = <String, VerificationScore>{};
    if (entry != null && yesterdayActual != null) {
      for (final model in models) {
        ModelPrediction? prediction;
        for (final p in entry) {
          if (p.model == model) {
            prediction = p;
            break;
          }
        }
        final score = scorePrediction(prediction, yesterdayActual, k);
        if (score != null) perModelScores[model] = score;
      }
    }

    if (perModelScores.isNotEmpty) {
      newlyVerified.add(MapEntry(targetRowDate, k));
    }

    leadTimeResults.add(LeadTimeResult(
      leadTimeDays: k,
      targetDateVerified: perModelScores.isEmpty ? null : targetRowDate,
      perModelScores: perModelScores,
    ));

    for (final model in models) {
      final short = rescoreRollingWindow(
        model: model,
        leadTimeDays: k,
        windowSize: windowShort,
        yesterday: yesterday,
        predictionsFor: predictionsFor,
        actualFor: actualFor,
      );
      final long = rescoreRollingWindow(
        model: model,
        leadTimeDays: k,
        windowSize: windowLong,
        yesterday: yesterday,
        predictionsFor: predictionsFor,
        actualFor: actualFor,
      );

      final key = '$model|$k';
      final track = byKey[key] ??
          TrackRecordEntry(model: model, leadTimeDays: k);

      final allTime = rescoreAllTime(
        model: model,
        leadTimeDays: k,
        yesterday: yesterday,
        earliestTargetDate: earliestRecordDate,
        predictionsFor: predictionsFor,
        actualFor: actualFor,
      );

      var newChecks = allTime.checks;
      var newCorrect = allTime.correct;
      var newPct = allTime.pct;

      // Safety rail. A derivation covering FEWER checks than last time means
      // observations are missing for dates we hold predictions for — a data
      // problem to surface, never a smaller number to quietly publish.
      if (newChecks < track.allTimeChecks) {
        warnings.add(
          'All-time re-derivation for $model lead+$k found $newChecks checks '
          'but ${track.allTimeChecks} were recorded previously — observations '
          'are probably missing for dates that have predictions. Keeping the '
          'previous figures; investigate the stored observations rather than '
          'trusting the smaller number.',
        );
        newChecks = track.allTimeChecks;
        newCorrect = track.allTimeCorrect;
        newPct = track.allTimeRainPct;
      } else {
        track.allTimeEarliestTargetDate = allTime.earliestTargetDate;
      }

      track.rolling10RainPct = short.rainPct;
      track.rolling30RainPct = long.rainPct;
      final trend = computeRainPctTrend(
        rolling10RainPct: short.rainPct,
        rolling30RainPct: long.rainPct,
        checksInWindow10: short.checksFound,
        checksInWindow30: long.checksFound,
        minChecksShort: trendMinChecksShort,
        minChecksLong: trendMinChecksLong,
        thresholdPct: trendThresholdPct,
      );
      track.rainPctTrend = trend.label;
      track.rainPctTrendDelta = trend.delta;
      track.allTimeChecks = newChecks;
      track.allTimeCorrect = newCorrect;
      track.allTimeRainPct = newPct;
      // Onset error is meaningful only at Day+0 — there is no onset data at
      // the extended lead times by design.
      track.avgOnsetErrorHrs10 = k == 0 ? short.onsetErr : null;
      track.avgWindErrorKmh10 = short.windErr;
      track.avgTempHighErrorC10 = short.highErr;
      track.avgTempLowErrorC10 = short.lowErr;
      track.avgMslpTrendErrorHpa10 = short.mslpErr;
      track.checksInWindow10 = short.checksFound;
      track.lastUpdated = today;

      byKey[key] = track;
    }
  }

  // Stable ordering: model order as configured, then lead time.
  final ordered = byKey.values.toList()
    ..sort((a, b) {
      final ai = models.indexOf(a.model);
      final bi = models.indexOf(b.model);
      final am = ai < 0 ? models.length : ai;
      final bm = bi < 0 ? models.length : bi;
      if (am != bm) return am.compareTo(bm);
      return a.leadTimeDays.compareTo(b.leadTimeDays);
    });

  return VerificationRunResult(
    leadTimeResults: leadTimeResults,
    trackRecord: ordered,
    newlyVerified: newlyVerified,
    warnings: warnings,
  );
}
