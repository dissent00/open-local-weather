// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// What a real model has to beat.
///
/// Port of `baselines.py`; see that module for the full reasoning and the
/// measurement that prompted it (ROADMAP item 57). The short version: this
/// project publishes figures like "ECMWF 75% rolling Day+0" and nothing
/// beside them said what they would have to beat, so 75% could not be read as
/// good, bad or noise. Measured 2026-08-31 over the 20 scored Day+0 checks
/// then stored: persistence scored 65% and always-dry 55%, against two of the
/// five real models on 60%.
///
/// THESE ARE YARDSTICKS, NOT GUIDANCE. Scored, stored and published beside
/// the numerical models, and withheld from the forecaster's own context —
/// a forecaster shown "persistence: 65%" would reasonably read it as a sixth
/// opinion about tomorrow, and it is not an opinion about anything.
///
/// NEITHER MAY EVER SEE THE DAY IT IS FORECASTING. Held to the Python
/// behaviour by `spec/vectors/baselines.json` — see `test/vectors_test.dart`.
library;

import 'models.dart';

const String persistenceModelId = 'persistence';
const String climatologyModelId = 'climatology';

/// "Tomorrow will be like the last day we actually saw."
///
/// [lastObserved] is the most recent observation available AT ISSUANCE — for
/// a forecast issued on day D that is D-1, at every lead time, because the
/// lead is a property of the target rather than of what the forecaster could
/// see.
///
/// Null when there is no observation to repeat, and deliberately not a dry
/// call: manufacturing a confident dry prediction out of missing data accrues
/// a flattering fake score, and this is the yardstick, so the distortion
/// would move every comparison rather than one row of it.
///
/// [includeOnset] is false by default because the real models carry no onset
/// at Day+3 or Day+7, and a baseline scored on a field its competitors cannot
/// answer is not measuring the same thing they are.
ModelPrediction? persistencePrediction(
  DailyActual? lastObserved, {
  bool includeOnset = false,
}) {
  if (lastObserved == null) {
    return null;
  }

  return ModelPrediction(
    model: persistenceModelId,
    rain: lastObserved.rain,
    onset: includeOnset ? lastObserved.onsetHour : null,
    precipMm: lastObserved.precipMm,
    windKmh: lastObserved.peakWindKmh,
    highC: lastObserved.highC,
    lowC: lastObserved.lowC,
    mslpTrend: lastObserved.mslpTrend,
  );
}

/// "The usual weather here, so far as this record knows."
///
/// The trailing base rate over every stored observation STRICTLY BEFORE
/// [before] — not a thirty-year normal, which no deployment of this project
/// has. Honest about being thin, and it improves on its own as the record
/// grows.
///
/// A TIE BREAKS DRY. At exactly 50% the call has to go somewhere, and dry is
/// the direction that does not manufacture a rain expectation out of a coin
/// flip.
///
/// Averages skip missing values instead of counting them as zero: a day whose
/// high was never recorded is a day with no high, and averaging it in as 0 °C
/// would drag the baseline down and flatter every model against it.
ModelPrediction? climatologyPrediction(
  Map<DateTime, DailyActual> actuals, {
  required DateTime before,
}) {
  final inScope = [
    for (final e in actuals.entries)
      if (e.key.isBefore(before)) e.value
  ];
  if (inScope.isEmpty) {
    return null;
  }

  final wet = inScope.where((a) => a.rain).length;

  return ModelPrediction(
    model: climatologyModelId,
    // Strictly greater than half: see the tie rule above.
    rain: wet * 2 > inScope.length,
    // THE BASE RATE AS A PROBABILITY — upstream ROADMAP item 58. The boolean
    // above discards exactly what a proper scoring rule needs, and as a
    // probability this becomes the canonical REFERENCE forecast that a Brier
    // skill score is measured against. The two can disagree and that is
    // correct: at a 40% base rate the boolean says dry and this says 40.
    rainProbabilityPct: (100 * wet / inScope.length).round(),
    // No view about WHEN, ever.
    onset: null,
    precipMm: _mean([for (final a in inScope) a.precipMm]),
    windKmh: _mean([for (final a in inScope) a.peakWindKmh]),
    highC: _mean([for (final a in inScope) a.highC]),
    lowC: _mean([for (final a in inScope) a.lowC]),
    mslpTrend: null,
  );
}

/// The mean of the values that exist, or null when none do.
double? _mean(List<double?> values) {
  final present = values.whereType<double>().toList();
  if (present.isEmpty) {
    return null;
  }

  return present.reduce((a, b) => a + b) / present.length;
}
