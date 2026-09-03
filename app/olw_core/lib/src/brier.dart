// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Proper scoring for probabilistic rain calls — upstream ROADMAP item 58.
///
/// Port of `verify/brier.py`; see that module for the full reasoning. The
/// short version: a boolean pays a confident wrong call and an honest hedge
/// identically, so the ledger cannot tell them apart and the system prompt
/// has to ask in English for restraint the scoring does not reward. Brier
/// pays for it arithmetically.
///
/// LOWER IS BETTER, unlike every other figure this project publishes. Each of
/// those is a percentage where higher is better; this is a squared error
/// where zero is perfect. Anything that displays it should say so in words.
library;

/// The squared error of one probabilistic call: `(p - outcome)^2`.
///
/// [probability] is a PROBABILITY in [0, 1], not a percentage. A 70 passed
/// here would score 4761 and pass silently into every mean it touched, so it
/// throws instead — the record stores percentages and converting is the
/// caller's job, precisely so the conversion happens once and visibly.
double brierScore(double probability, bool occurred) {
  if (probability < 0.0 || probability > 1.0) {
    throw ArgumentError.value(
      probability,
      'probability',
      'brierScore expects a probability in [0, 1] — if this came from '
          'rainProbabilityPct, divide by 100 first',
    );
  }

  final outcome = occurred ? 1.0 : 0.0;
  final error = probability - outcome;
  return error * error;
}

/// The mean over the entries that HAVE a score, or null when none do.
///
/// Absent days are skipped rather than counted. Most stored days have no
/// probability — the field started being recorded 2026-09-03 — and treating
/// an absent one as an implicit 0.5 would manufacture a hedge nobody made.
double? meanBrier(List<double?> scores) {
  final present = scores.whereType<double>().toList();
  if (present.isEmpty) {
    return null;
  }

  return present.reduce((a, b) => a + b) / present.length;
}

/// How much better than the reference forecast: `1 - brier/reference`.
///
/// 1.0 is perfect, 0.0 matches the reference, and NEGATIVE is worse than it.
/// Deliberately not clamped: upstream item 57 measured two of five models
/// losing to persistence on the boolean, and a negative skill score states
/// that result in a form that cannot be read as merely "less good".
///
/// Null when either side is missing, and when the reference is a perfect 0.0
/// — a reference that is never wrong leaves nothing to improve on, and the
/// division is undefined rather than infinite.
double? brierSkillScore(double? brier, double? referenceBrier) {
  if (brier == null || referenceBrier == null || referenceBrier == 0.0) {
    return null;
  }

  return 1.0 - (brier / referenceBrier);
}
