// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'models.dart';
import 'scoring.dart' show mean;
import 'verify.dart' show TrackRecordEntry;

/// Correcting a forecast for bias the record has already measured.
///
/// Upstream ROADMAP item 126. This project's founding principle is that
/// arithmetic an LLM can get wrong belongs in code, and the gust is where that
/// was stated and then not applied: the prompt TELLS the forecaster the models
/// under-forecast peak wind — gfs's skill_profile_summary says so in words and
/// the per-model figures sit beside it — and the published gust still came in
/// **12.09 km/h below the observed gust over 32 days**, median 10.15. On
/// 2026-09-13 the page published 28.6 km/h against an observed 55.1.
///
/// Telling a model about a bias is not removing it.
///
/// THE CORRECTION IS THE RECORD'S OWN NUMBER. `avgWindErrorKmh10` is
/// `actual - predicted` over the last rollingWindowShort checks, recomputed
/// every run by the scorer and already stored. Nothing new is measured here.
///
/// MEASURED OUT OF SAMPLE — bias taken from the ten days STRICTLY BEFORE each
/// day and applied to that day's consensus: mean absolute error 11.16 -> 3.80
/// km/h over 24 days, improved on 23 of them. A 5-day window gives 12.26 ->
/// 4.48 and a 20-day one 11.69 -> 3.23.
///
/// PER MODEL RATHER THAN POOLED, by a hair on the numbers (3.70 against 3.80)
/// and by a wide margin on the reasoning: the per-model figure is already
/// computed, already stored and already in the prompt, so code and forecaster
/// correct from ONE number. gfs runs +19.99 against best_match's +4.40.
///
/// NEVER APPLIED TO THE SCORED ROWS. The scorer measures stored predictions
/// against observations to produce the very number used here; correcting the
/// stored prediction would close that loop on itself, the measured error would
/// collapse toward zero, and the bias would return uncorrected while the
/// record claimed it was fixed. So this returns a SEPARATE consensus and never
/// a modified ModelPrediction.

/// The lead time the correction is defined for. Day+0 only, because that is
/// where it has been validated; a longer lead has a different error structure
/// and deserves its own measurement first.
const int gustCalibrationLeadDays = 0;

/// The same ten as rollingWindowShort — the window the correction is averaged
/// over. Fewer checks would apply a mean from a window that is not full.
const int gustCalibrationMinChecks = 10;

/// Per-model km/h to ADD to a forecast gust, keyed by model id.
///
/// ADD, not subtract: `avgWindErrorKmh10` is `actual - predicted`, so a
/// positive value is a model that came in under what happened. Getting the
/// sign wrong here would DOUBLE the bias rather than remove it, in the
/// direction the record already leans — 43 stored notes had exactly this
/// direction backwards before the prompt rule spelled it out.
///
/// A model with too few checks, or none recorded, is simply absent. Absence is
/// absence: zero would be a claim that the model is unbiased.
Map<String, double> gustCorrections(
  Iterable<TrackRecordEntry> entries, {
  int leadDays = gustCalibrationLeadDays,
  int minChecks = gustCalibrationMinChecks,
}) {
  final corrections = <String, double>{};

  for (final entry in entries) {
    if (entry.leadTimeDays != leadDays) {
      continue;
    }

    final error = entry.avgWindErrorKmh10;
    if (error == null) {
      continue;
    }

    if (entry.checksInWindow10 < minChecks) {
      continue;
    }

    corrections[entry.model] = error;
  }

  return corrections;
}

/// The consensus gust with each model's measured bias added back, or null when
/// no model in the list has a correction.
///
/// ONLY CORRECTED MODELS COUNT. A consensus mixing corrected and uncorrected
/// members is neither: it would be pulled toward whichever models happen to
/// lack a record, and it would move whenever one crossed the check threshold.
///
/// NULL, NOT A FALLBACK TO THE RAW CONSENSUS, so the caller decides what an
/// uncalibrated day means rather than being handed a number that silently is
/// not the thing its name says.
double? calibratedGustConsensus(
  List<ModelPrediction> predictions,
  Map<String, double>? corrections,
) {
  if (corrections == null || corrections.isEmpty) {
    return null;
  }

  final adjusted = <double?>[];
  for (final p in predictions) {
    final correction = corrections[p.model];
    if (p.windKmh == null || correction == null) {
      continue;
    }
    adjusted.add(p.windKmh! + correction);
  }

  return mean(adjusted);
}
