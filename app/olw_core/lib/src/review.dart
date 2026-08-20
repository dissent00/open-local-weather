// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'config.dart';
import 'dates.dart';
import 'models.dart';
import 'scoring.dart';

/// Weekly review: what the daily loop structurally cannot see.
///
/// Port of the Python `review.py`, and required to be a port rather than a
/// reimplementation. "Accuracy demonstrably improving over time" is this
/// project's strongest claim, and an app whose accuracy screen disagreed
/// with the server's would destroy exactly the credibility the feature
/// exists to build. `spec/vectors/weekly_review.json` holds the two
/// implementations to identical output.
///
/// The gates are the sensitive part. An implementation that ranked models
/// one check earlier than the other would publish a claim the other
/// withholds — which is worse than either behaviour alone, because a user
/// comparing the two would have no way to tell which was right.

/// How much weight a figure derived from `checks` checks can carry.
String confidenceFor(int checks) {
  for (final band in reviewConfidenceBands) {
    if (checks < band.$1) return band.$2;
  }
  return reviewConfidenceBands.last.$2;
}

int _confidenceRank(String label) {
  for (var i = 0; i < reviewConfidenceBands.length; i++) {
    if (reviewConfidenceBands[i].$2 == label) return i;
  }
  return 0;
}

/// One (model, lead time) pair's skill across the whole record.
class SkillCell {
  const SkillCell({
    required this.model,
    required this.leadTimeDays,
    required this.checks,
    required this.correct,
    required this.rainPct,
    required this.confidence,
    required this.meanHighErrorC,
    required this.meanLowErrorC,
    required this.meanWindErrorKmh,
    required this.meanOnsetErrorHrs,
    required this.meanMslpErrorHpa,
    required this.earliest,
    required this.latest,
  });

  final String model;
  final int leadTimeDays;
  final int checks;
  final int correct;
  final double? rainPct;
  final String confidence;
  final double? meanHighErrorC;
  final double? meanLowErrorC;
  final double? meanWindErrorKmh;
  final double? meanOnsetErrorHrs;
  final double? meanMslpErrorHpa;
  final DateTime? earliest;
  final DateTime? latest;
}

/// A single reviewed observation.
///
/// `evidence` and `confidence` are not decoration — they travel with the
/// claim into the prompt and onto the screen, so a reader can weigh it
/// rather than take it on trust.
class Finding {
  const Finding({
    required this.kind,
    required this.claim,
    required this.evidence,
    required this.confidence,
    required this.checks,
  });

  final String kind; // ranking | bias | gap
  final String claim;
  final String evidence;
  final String confidence;
  final int checks;
}

class WeeklyReview {
  const WeeklyReview({
    required this.periodStart,
    required this.periodEnd,
    required this.daysWithPredictions,
    required this.daysVerified,
    required this.cells,
    required this.findings,
    required this.dataSufficiency,
  });

  final DateTime periodStart;
  final DateTime periodEnd;
  final int daysWithPredictions;
  final int daysVerified;
  final List<SkillCell> cells;
  final List<Finding> findings;

  /// How much the review as a whole can be trusted. Always present,
  /// including — especially — when the answer is "not much yet".
  final String dataSufficiency;
}

/// Computes the full review deterministically. No LLM, no I/O.
WeeklyReview buildWeeklyReview({
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays) predictionsFor,
  required DailyActual? Function(DateTime targetDate) actualFor,
  required List<DateTime> allLogDates,
  required DateTime today,
  List<String> models = defaultModels,
  List<int> leadTimesDays = leadTimesDays_,
}) {
  final yesterday = addDays(today, -1);
  final sortedDates = [...allLogDates]..sort();
  final earliest = sortedDates.isEmpty ? yesterday : sortedDates.first;

  final cells = <SkillCell>[];
  for (final k in leadTimesDays) {
    for (final model in models) {
      final scored = collectScores(
        model: model,
        leadTimeDays: k,
        yesterday: yesterday,
        earliestTargetDate: earliest,
        predictionsFor: predictionsFor,
        actualFor: actualFor,
      );
      final checks = scored.length;
      final correct = scored.where((e) => e.value.rainCorrect).length;
      cells.add(SkillCell(
        model: model,
        leadTimeDays: k,
        checks: checks,
        correct: correct,
        rainPct: checks > 0 ? 100 * correct / checks : null,
        confidence: confidenceFor(checks),
        meanHighErrorC: mean([for (final e in scored) e.value.highErrorC]),
        meanLowErrorC: mean([for (final e in scored) e.value.lowErrorC]),
        meanWindErrorKmh: mean([for (final e in scored) e.value.windErrorKmh]),
        meanOnsetErrorHrs: mean([for (final e in scored) e.value.onsetErrorHrs]),
        meanMslpErrorHpa: mean([for (final e in scored) e.value.mslpErrorHpa]),
        earliest: scored.isEmpty ? null : scored.last.key,
        latest: scored.isEmpty ? null : scored.first.key,
      ));
    }
  }

  var daysVerified = 0;
  for (var d = earliest; !d.isAfter(yesterday); d = addDays(d, 1)) {
    if (actualFor(d) != null) daysVerified++;
  }

  final findings = _deriveFindings(cells, leadTimesDays);
  final review = WeeklyReview(
    periodStart: earliest,
    periodEnd: yesterday,
    daysWithPredictions: allLogDates.length,
    daysVerified: daysVerified,
    cells: cells,
    findings: findings,
    dataSufficiency: _describeSufficiency(
      allLogDates.length, earliest, yesterday, daysVerified, cells, leadTimesDays,
    ),
  );
  return review;
}

String _fmtPct(double v) => v.toStringAsFixed(0);

String _fmtSigned(double v) {
  final rounded = (v * 10).round() / 10 + 0.0;
  return '${rounded >= 0 ? '+' : ''}${rounded.toStringAsFixed(1)}';
}

List<Finding> _deriveFindings(List<SkillCell> cells, List<int> leadTimesDays) {
  final findings = <Finding>[];

  for (final k in leadTimesDays) {
    final atLead = cells.where((c) => c.leadTimeDays == k).toList();

    // --- Comparative ranking, heavily gated -------------------------------
    // Two independent gates. Both models need enough checks to be worth
    // comparing at all, AND the gap has to clear the noise floor: at n=10 a
    // binary hit rate carries ~15 points of binomial scatter, so a 10-point
    // "lead" is not evidence of anything.
    final eligible = atLead
        .where((c) => c.checks >= reviewMinChecksForComparison && c.rainPct != null)
        .toList();
    if (eligible.length >= 2) {
      var best = eligible.first;
      var worst = eligible.first;
      for (final c in eligible) {
        if (c.rainPct! > best.rainPct!) best = c;
        if (c.rainPct! < worst.rainPct!) worst = c;
      }
      final gap = best.rainPct! - worst.rainPct!;
      if (gap >= reviewComparisonMinGapPct) {
        findings.add(Finding(
          kind: 'ranking',
          claim: 'At Day+$k, ${best.model} is the strongest rain caller here '
              'and ${worst.model} the weakest.',
          evidence:
              '${best.model} ${best.correct}/${best.checks} (${_fmtPct(best.rainPct!)}%) '
              'vs ${worst.model} ${worst.correct}/${worst.checks} (${_fmtPct(worst.rainPct!)}%); '
              'a ${_fmtPct(gap)}-point gap, above the '
              '${_fmtPct(reviewComparisonMinGapPct)}-point noise floor.',
          confidence: _confidenceRank(best.confidence) <= _confidenceRank(worst.confidence)
              ? best.confidence
              : worst.confidence,
          checks: best.checks < worst.checks ? best.checks : worst.checks,
        ));
      } else {
        var minChecks = eligible.first.checks;
        var minConfidence = eligible.first.confidence;
        for (final c in eligible) {
          if (c.checks < minChecks) minChecks = c.checks;
          if (_confidenceRank(c.confidence) < _confidenceRank(minConfidence)) {
            minConfidence = c.confidence;
          }
        }
        findings.add(Finding(
          kind: 'ranking',
          claim: 'At Day+$k, no model is meaningfully better than the others here yet.',
          evidence: 'Best-to-worst spread is only ${_fmtPct(gap)} points across '
              '${eligible.length} models with enough checks to compare, '
              'within the ${_fmtPct(reviewComparisonMinGapPct)}-point noise floor.',
          confidence: minConfidence,
          checks: minChecks,
        ));
      }
    }

    // --- Systematic bias ---------------------------------------------------
    for (final c in atLead) {
      if (c.checks < reviewMinChecksForComparison) continue;
      final candidates = <(double?, double, String, String)>[
        (c.meanHighErrorC, reviewTempBiasThresholdC, 'daytime highs', '°C'),
        (c.meanLowErrorC, reviewTempBiasThresholdC, 'overnight lows', '°C'),
        (c.meanWindErrorKmh, reviewWindBiasThresholdKmh, 'peak wind', ' km/h'),
      ];
      for (final (value, threshold, label, unit) in candidates) {
        if (value == null || value.abs() < threshold) continue;
        // Errors are actual - predicted, so a positive mean means the model
        // came in UNDER what actually happened.
        final direction = value > 0 ? 'under-forecasts' : 'over-forecasts';
        findings.add(Finding(
          kind: 'bias',
          claim: 'At Day+$k, ${c.model} systematically $direction $label here.',
          evidence: 'Mean error ${_fmtSigned(value)}$unit across ${c.checks} checks.',
          confidence: c.confidence,
          checks: c.checks,
        ));
      }
    }

    // --- Gaps worth naming --------------------------------------------------
    final unscored = atLead.where((c) => c.checks == 0).length;
    if (atLead.isNotEmpty && unscored == atLead.length) {
      findings.add(Finding(
        kind: 'gap',
        claim: 'Day+$k has never been verified here.',
        evidence: 'No stored prediction at this lead time has yet had an '
            'observation to score against.',
        confidence: 'insufficient',
        checks: 0,
      ));
    }
  }

  return findings;
}

String _describeSufficiency(
  int daysWithPredictions,
  DateTime periodStart,
  DateTime periodEnd,
  int daysVerified,
  List<SkillCell> cells,
  List<int> leadTimesDays,
) {
  final parts = <String>[
    'Reviewed $daysWithPredictions day(s) of stored forecasts '
        '(${formatDate(periodStart)} to ${formatDate(periodEnd)}), of which '
        '$daysVerified have observations to score against.'
  ];
  for (final k in leadTimesDays) {
    final atLead = cells.where((c) => c.leadTimeDays == k).toList();
    if (atLead.isEmpty) continue;
    // The WEAKEST model sets the confidence, not the best-covered one — not
    // every model reaches every lead time, and reporting the maximum as "per
    // model" would overstate coverage for exactly the models that have least.
    var checks = atLead.first.checks;
    var richest = atLead.first.checks;
    for (final c in atLead) {
      if (c.checks < checks) checks = c.checks;
      if (c.checks > richest) richest = c.checks;
    }
    final behind = (atLead.where((c) => c.checks < richest).map((c) => c.model).toList()..sort());
    final conf = confidenceFor(checks);
    if (conf == 'insufficient') {
      final need = reviewConfidenceBands.first.$1 - checks;
      parts.add('Day+$k: $checks check(s) per model — not enough to say anything; '
          'roughly $need more day(s) before even a provisional read.');
    } else if (conf == 'provisional') {
      parts.add('Day+$k: $checks check(s) per model — directional only, '
          'not yet enough to rank models against each other.');
    } else if (conf == 'usable') {
      parts.add('Day+$k: $checks check(s) per model — enough to compare models, '
          'though differences smaller than about 15 points remain noise.');
    } else {
      parts.add('Day+$k: $checks check(s) per model — a settled picture.');
    }
    if (behind.isNotEmpty) {
      parts.add('(Coverage at Day+$k is uneven: ${behind.join(', ')} '
          '${behind.length == 1 ? 'has' : 'have'} fewer than the '
          '$richest check(s) the other models have, so any comparison '
          'at this lead time is not like-for-like.)');
    }
  }
  return parts.join(' ');
}
