// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// A forecast from the record alone — upstream ROADMAP item 173.
///
/// Port of `code_blend.py`; the reasoning lives there. The record turned into
/// a forecast by arithmetic, so the LLM's call has something fair to be
/// measured against: the same inputs, the same scores, no reasoning.
///
/// Rain votes are weighted by the rolling-30 hit rate above a coin flip, and
/// Day+0 temperatures corrected by the rolling-10 signed error — the figures
/// MODEL TRACK RECORD hands the forecaster. [windowsAsOf] reads only targets
/// strictly before the issuance: a blend that could peek would not look
/// broken, it would make the LLM look worse than it is.
///
/// Held to the Python by `spec/vectors/code_blend.json`, whose three .5 cases
/// exist because Dart's `.round()` goes half away from zero and Python's
/// `round()` half to even.
library;

import 'config.dart';
import 'dates.dart';
import 'models.dart';
import 'rounding.dart';
import 'scoring.dart' show mean;
import 'sums.dart';
import 'verify.dart';

/// How many checks a rolling-30 hit rate needs before it carries a vote —
/// the existing [trendMinChecksLong], not a second threshold.
const int rainWeightMinChecks = trendMinChecksLong;

/// A yes/no call right half the time is a coin flip and gets no vote.
const double coinFlipPct = 50.0;

/// The full window, for [gustCalibrationMinChecks]'s reason: fewer would
/// apply a mean from a window that is not full.
const int temperatureCorrectionMinChecks = rollingWindowShort;

/// The models whose record and predictions the blend reads: what the
/// forecaster sees, minus `best_match`, whose rain probability is ECMWF's.
List<String> blendInputs({String localBulletinModelId = ''}) => [
      for (final m
          in modelsVisibleToTheForecaster(localBulletinModelId: localBulletinModelId))
        if (m != bestMatchModelId) m,
    ];

/// Each model's rolling window as the record stood when [issued] ran.
///
/// Walks back from the day BEFORE the issuance, so the day being forecast
/// and anything after it cannot enter.
Map<String, RollingWindowResult> windowsAsOf({
  required List<String> models,
  required int leadTimeDays,
  required int windowSize,
  required DateTime issued,
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays)
      predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
}) {
  final yesterday = addDays(issued, -1);

  return {
    for (final m in models)
      m: rescoreRollingWindow(
        model: m,
        leadTimeDays: leadTimeDays,
        windowSize: windowSize,
        yesterday: yesterday,
        predictionsFor: predictionsFor,
        actualFor: actualFor,
      ),
  };
}

/// Each model's vote: its hit rate above a coin flip, in points. A model
/// with too few checks, or no better than a coin flip, is ABSENT rather
/// than zero-weighted, so "who voted" can be read off the keys.
Map<String, double> rainWeights(
  Map<String, RollingWindowResult> windows, {
  int minChecks = rainWeightMinChecks,
}) {
  final weights = <String, double>{};

  for (final entry in windows.entries) {
    final rainPct = entry.value.rainPct;
    if (rainPct == null || entry.value.checksFound < minChecks) {
      continue;
    }

    final weight = rainPct - coinFlipPct;
    if (weight <= 0) {
      continue;
    }

    weights[entry.key] = weight;
  }

  return weights;
}

/// Per-model °C to ADD to a high and to a low. ADD: the errors are
/// actual minus predicted, so a positive one is a model that came in cold.
/// A model nothing has measured is absent, never zero.
(Map<String, double>, Map<String, double>) temperatureCorrections(
  Map<String, RollingWindowResult> windows, {
  int minChecks = temperatureCorrectionMinChecks,
}) {
  final highs = <String, double>{};
  final lows = <String, double>{};

  for (final entry in windows.entries) {
    if (entry.value.checksFound < minChecks) {
      continue;
    }

    final high = entry.value.highErr;
    if (high != null) {
      highs[entry.key] = high;
    }

    final low = entry.value.lowErr;
    if (low != null) {
      lows[entry.key] = low;
    }
  }

  return (highs, lows);
}

/// The record-weighted call, or null when no model carries a vote.
///
/// Only models with a weight vote and only corrected models enter a
/// temperature, so the LLM's own row among the predictions cannot move it.
/// The probability is the weighted share of wet votes, rounded as Python
/// rounds; a tie breaks dry.
ModelPrediction? codeBlendPrediction(
  List<ModelPrediction> predictions,
  Map<String, double> weights, [
  Map<String, double>? highCorrections,
  Map<String, double>? lowCorrections,
]) {
  final voters = [
    for (final p in predictions)
      if (weights.containsKey(p.model) && p.rain != null) p,
  ];
  // compensatedSum, not `+=`: Python's sum() is Neumaier's — see sums.dart.
  final total = compensatedSum([for (final p in voters) weights[p.model]!]);
  if (total <= 0) {
    return null;
  }

  final wet = compensatedSum([
    for (final p in voters)
      if (p.rain!) weights[p.model]!,
  ]);

  return ModelPrediction(
    model: codeBlendModelId,
    // wet * 2 > total, as Python compares it, so the tie is decided by the
    // same arithmetic and not by a division.
    rain: wet * 2 > total,
    rainProbabilityPct: roundLikePython(100 * wet / total, 0).toInt(),
    highC: _correctedMean(predictions, highCorrections, (p) => p.highC),
    lowC: _correctedMean(predictions, lowCorrections, (p) => p.lowC),
  );
}

/// The code blend for one issuance, keyed by lead: a one-element list where
/// it calls, empty where it declines. Temperatures at Day+0 only.
Map<int, List<ModelPrediction>> codeBlendPredictions({
  required List<ModelPrediction> Function(int leadTimeDays) issuedPredictions,
  required DateTime issued,
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays)
      predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
  required List<String> inputs,
}) {
  final blends = <int, List<ModelPrediction>>{};

  for (final lead in leadTimesDays) {
    final long = windowsAsOf(
      models: inputs,
      leadTimeDays: lead,
      windowSize: rollingWindowLong,
      issued: issued,
      predictionsFor: predictionsFor,
      actualFor: actualFor,
    );
    var highs = <String, double>{};
    var lows = <String, double>{};
    if (lead == 0) {
      final short = windowsAsOf(
        models: inputs,
        leadTimeDays: 0,
        windowSize: rollingWindowShort,
        issued: issued,
        predictionsFor: predictionsFor,
        actualFor: actualFor,
      );
      (highs, lows) = temperatureCorrections(short);
    }

    final blend = codeBlendPrediction(
        issuedPredictions(lead), rainWeights(long), highs, lows);
    blends[lead] = [if (blend != null) blend];
  }

  return blends;
}

/// The mean of the corrected models only — calibratedGustConsensus's rule.
double? _correctedMean(
  List<ModelPrediction> predictions,
  Map<String, double>? corrections,
  double? Function(ModelPrediction) field,
) {
  if (corrections == null || corrections.isEmpty) {
    return null;
  }

  final adjusted = <double?>[];
  for (final p in predictions) {
    final value = field(p);
    final correction = corrections[p.model];
    if (value == null || correction == null) {
      continue;
    }
    adjusted.add(value + correction);
  }

  return mean(adjusted);
}
