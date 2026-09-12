// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'baselines.dart';
import 'brier.dart';
import 'config.dart';
import 'dates.dart';
import 'models.dart';
import 'rounding.dart';
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
    required this.meanCloudErrorPct,
    required this.cloudChecks,
    required this.stormDays,
    required this.stormsCalled,
    required this.earliest,
    required this.latest,
    this.meanRainBrier,
    this.brierChecks = 0,
    this.rainBrierSkill,
    this.brierSkillChecks = 0,
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

  /// The sky, from 2026-09-10, aggregated the same day it was first scored:
  /// a per-day error nothing rolls up is a number no forecaster can weigh.
  final double? meanCloudErrorPct;

  /// How many of [checks] said anything about the sky. Separate for the same
  /// reason [brierChecks] is, and more sharply: cloudCoverPct started on
  /// 2026-09-09 while rain and temperature have months of rows.
  final int cloudChecks;

  /// The instability call — upstream item 35. [stormDays] counts the days the
  /// station observed thunder AND this model had a CAPE figure to be judged
  /// on; [stormsCalled] how many of those it saw coming.
  ///
  /// Counted rather than rated, because the useful claim is "missed 9 of 12"
  /// and a rate cannot be turned back into that. A hit rate over all days is
  /// also hostage to the base rate: in a stormy fortnight a model that always
  /// calls instability scores well.
  final int stormDays;
  final int stormsCalled;

  final DateTime? earliest;
  final DateTime? latest;

  /// Mean Brier over the checks in this cell that carried a probability —
  /// upstream ROADMAP item 58. LOWER IS BETTER, alone among the figures on
  /// this row: every other one is a percentage or a signed error, and this is
  /// a squared error where zero is perfect. Anything rendering it has to say
  /// so in words.
  final double? meanRainBrier;

  /// Counted separately from [checks] because the two genuinely differ and
  /// will for weeks: probabilities began being recorded 2026-09-03, so a cell
  /// can hold thirty scored days of which three carry one. Showing [checks]
  /// beside the Brier would claim evidence that does not exist.
  final int brierChecks;

  /// `1 - brier/climatologyBrier`: 1.0 is perfect, 0.0 is exactly
  /// climatology, NEGATIVE is worse than simply knowing the usual chance of
  /// rain here.
  ///
  /// Null when this cell has no Brier, when climatology is not among the
  /// models being reviewed, or when the reference is a perfect 0.0. Raw Brier
  /// is not interpretable without it — 0.2 is good or bad entirely depending
  /// on the base rate.
  final double? rainBrierSkill;

  /// How many days [rainBrierSkill] actually rests on: those where this model
  /// AND climatology both stated a probability. Smaller than [brierChecks],
  /// which is itself smaller than [checks]. Three counts on one row looks like
  /// over-reporting until they diverge, and on the real record at 2026-09-06
  /// they were 26, 5 and 2.
  final int brierSkillChecks;
}

/// The Brier skill score over the days a model and the reference SHARE, with
/// the size of that intersection.
///
/// Both means are taken over the same dates, so the ratio compares two
/// forecasts of the same weather rather than two samples of different
/// weather. That is the whole content of a skill score; computed over
/// unpaired days it is a plausible-looking number that answers nothing — and
/// on the real record the two sides differed 5 days to 2.
(double?, int) _pairedSkill(
  List<MapEntry<DateTime, VerificationScore>> scored,
  Map<String, double> referenceByDate,
) {
  final own = <double?>[];
  final reference = <double?>[];
  for (final e in scored) {
    final ref = referenceByDate[formatDate(e.key)];
    if (e.value.rainBrier == null || ref == null) continue;
    own.add(e.value.rainBrier);
    reference.add(ref);
  }
  if (own.isEmpty) return (null, 0);

  return (brierSkillScore(meanBrier(own), meanBrier(reference)), own.length);
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
    // Scored for every model at this lead BEFORE any cell is built, because
    // the Brier skill score is the one figure on a row that is not a property
    // of that row: it needs climatology's Brier at the same lead, and
    // climatology is just another model in this loop.
    final scoredByModel = {
      for (final model in models)
        model: collectScores(
          model: model,
          leadTimeDays: k,
          yesterday: yesterday,
          earliestTargetDate: earliest,
          predictionsFor: predictionsFor,
          actualFor: actualFor,
        ),
    };
    final briers = {
      for (final entry in scoredByModel.entries)
        entry.key: meanBrier([for (final e in entry.value) e.value.rainBrier]),
    };
    // The reference's Brier PER DAY, not as one mean. A skill score is a ratio
    // of two means and only means anything if both are taken over the SAME
    // days — see _pairedSkill. Keyed by formatted date because DateTime does
    // not compare by value as a Map key.
    //
    // Empty when climatology is not among the models being reviewed, which is
    // the prompt path.
    final referenceByDate = <String, double>{
      for (final e in scoredByModel[climatologyModelId] ?? const [])
        if (e.value.rainBrier != null) formatDate(e.key): e.value.rainBrier!,
    };

    for (final model in models) {
      final scored = scoredByModel[model]!;
      final (skill, skillChecks) = _pairedSkill(scored, referenceByDate);
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
        meanCloudErrorPct: mean([for (final e in scored) e.value.cloudErrorPct]),
        cloudChecks: scored.where((e) => e.value.cloudErrorPct != null).length,
        stormDays: scored
            .where((e) =>
                e.value.convectiveCorrect != null &&
                actualFor(e.key)?.thunder == true)
            .length,
        stormsCalled: scored
            .where((e) =>
                e.value.convectiveCorrect == true &&
                actualFor(e.key)?.thunder == true)
            .length,
        earliest: scored.isEmpty ? null : scored.last.key,
        latest: scored.isEmpty ? null : scored.first.key,
        meanRainBrier: briers[model],
        brierChecks:
            scored.where((e) => e.value.rainBrier != null).length,
        rainBrierSkill: skill,
        brierSkillChecks: skillChecks,
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
  final rounded = roundLikePython(v, 1) + 0.0;
  return '${rounded >= 0 ? '+' : ''}${rounded.toStringAsFixed(1)}';
}

/// The weakest confidence among [cells] — a claim is only as strong as the
/// thinnest evidence behind it.
String _lowestConfidence(List<SkillCell> cells) {
  var lowest = cells.first.confidence;
  for (final c in cells) {
    if (_confidenceRank(c.confidence) < _confidenceRank(lowest)) {
      lowest = c.confidence;
    }
  }
  return lowest;
}

int _fewestChecks(List<SkillCell> cells) {
  var fewest = cells.first.checks;
  for (final c in cells) {
    if (c.checks < fewest) fewest = c.checks;
  }
  return fewest;
}

List<Finding> _deriveFindings(List<SkillCell> cells, List<int> leadTimesDays) {
  final findings = <Finding>[];

  for (final k in leadTimesDays) {
    final everyCellAtLead = cells.where((c) => c.leadTimeDays == k).toList();

    // The yardsticks are scored in the same ledger and must NOT be ranked in
    // it. "climatology is the strongest rain caller here" compares guidance
    // against a yardstick as though they were peers, and "persistence
    // under-forecasts peak wind" is a statement about yesterday's weather
    // rather than about a forecast system. Both were reachable here until
    // 2026-09-06, when the first vector case carrying a baseline model found
    // that this port had neither the exclusion nor the finding below.
    //
    // _describeSufficiency deliberately does NOT make this split — coverage
    // is a statement about the record, and the baselines are in it.
    final atLead =
        everyCellAtLead.where((c) => !baselineModelIds.contains(c.model)).toList();
    final baselineCells =
        everyCellAtLead.where((c) => baselineModelIds.contains(c.model)).toList();

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
      // EACH FIELD CARRIES ITS OWN SAMPLE SIZE. Every row here used to be as
      // old as the cell, so `c.checks` described them all. Cloud broke that
      // on 2026-09-10 by arriving months late, and a mean over three days
      // was about to be published "across 30 checks".
      final candidates = <(double?, double, String, String, int)>[
        (c.meanHighErrorC, reviewTempBiasThresholdC, 'daytime highs', '°C', c.checks),
        (c.meanLowErrorC, reviewTempBiasThresholdC, 'overnight lows', '°C', c.checks),
        (c.meanWindErrorKmh, reviewWindBiasThresholdKmh, 'peak wind', ' km/h', c.checks),
        (c.meanCloudErrorPct, reviewCloudBiasThresholdPct, 'cloud cover', ' points',
            c.cloudChecks),
      ];
      for (final (value, threshold, label, unit, n) in candidates) {
        if (value == null || value.abs() < threshold) continue;
        // The floor applies to the FIELD's evidence, not the row's.
        if (n < reviewMinChecksForComparison) continue;
        // Errors are actual - predicted, so a positive mean means the model
        // came in UNDER what actually happened.
        final direction = value > 0 ? 'under-forecasts' : 'over-forecasts';
        findings.add(Finding(
          kind: 'bias',
          claim: 'At Day+$k, ${c.model} systematically $direction $label here.',
          evidence: 'Mean error ${_fmtSigned(value)}$unit across $n checks.',
          // Derived from THIS field's count, so a three-day sky cannot
          // inherit a thirty-day row's "established".
          confidence: confidenceFor(n),
          checks: n,
        ));
      }
    }

    // --- Storms nobody saw coming ------------------------------------------
    //
    // ITEM 35'S ORIGINAL INCIDENT, made checkable. On 2026-08-22 the evening
    // forecast said "no severe weather hazards are expected" while it was
    // thundering: GFS saw essentially no instability, ICON and ECMWF saw
    // 700-1200 J/kg all evening, and the narrative resolved that silently
    // toward the quiet answer.
    //
    // A SEPARATE KIND FROM 'bias', because it is not a signed error and it is
    // not symmetric. A false alarm costs a reader an umbrella; a missed storm
    // costs them the thing this forecast exists to warn about, and averaging
    // the two into one hit rate hides it.
    for (final c in atLead) {
      if (c.stormDays < reviewMinStormDays) continue;
      final missed = c.stormDays - c.stormsCalled;
      if (missed / c.stormDays < reviewStormMissThreshold) continue;
      findings.add(Finding(
        kind: 'convective',
        claim: "At Day+$k, ${c.model} does not see this location's "
            'thunderstorms coming.',
        evidence: 'Its CAPE stayed below the convective threshold on $missed '
            'of ${c.stormDays} days the station observed thunder.',
        // From the STORM days, not the cell's total: a model judged on twelve
        // storms has twelve days of evidence about storms.
        confidence: confidenceFor(c.stormDays),
        checks: c.stormDays,
      ));
    }

    // --- Does being best mean anything? -----------------------------------
    // Upstream ROADMAP item 57. A ranking says which model is best of those
    // present; it cannot say whether being best is worth having. On this
    // project's own record at Day+0, repeating yesterday's weather beat two of
    // the five numerical models and the project's own blend, so "ECMWF is the
    // strongest rain caller here" was a claim a reader had no way to weigh.
    //
    // Gated on the SAME noise floor as the ranking, and for the same reason:
    // clearing a yardstick by three points at n=20 is scatter.
    final eligibleBaselines = baselineCells
        .where((c) => c.checks >= reviewMinChecksForComparison && c.rainPct != null)
        .toList();
    if (eligible.isNotEmpty && eligibleBaselines.isNotEmpty) {
      var bar = eligibleBaselines.first;
      for (final c in eligibleBaselines) {
        if (c.rainPct! > bar.rainPct!) bar = c;
      }
      // Sorted best-first, with ties broken by position in `eligible`.
      // Python sorts this list with `sorted`, which is STABLE, and Dart's
      // List.sort is not — so two models on the same percentage would be
      // named in either order here and in a fixed order there. The claim
      // string joins these names, so that is a different published sentence,
      // not an internal detail.
      final clearing = eligible
          .where((c) => c.rainPct! - bar.rainPct! >= reviewComparisonMinGapPct)
          .toList();
      final orderInEligible = {
        for (var n = 0; n < eligible.length; n++) eligible[n].model: n,
      };
      clearing.sort((a, b) {
        final byPct = b.rainPct!.compareTo(a.rainPct!);
        if (byPct != 0) return byPct;
        return orderInEligible[a.model]!.compareTo(orderInEligible[b.model]!);
      });

      final barEvidence =
          '${bar.model} ${bar.correct}/${bar.checks} (${_fmtPct(bar.rainPct!)}%), '
          'the best of ${eligibleBaselines.length} trivial baseline(s); a model '
          'has to clear it by more than the '
          '${_fmtPct(reviewComparisonMinGapPct)}-point noise floor to count.';

      if (clearing.isNotEmpty) {
        findings.add(Finding(
          kind: 'baseline',
          claim: 'At Day+$k, ${clearing.map((c) => c.model).join(', ')} '
              'beat${clearing.length == 1 ? 's' : ''} the best trivial baseline.',
          evidence: '$barEvidence Clearing it: '
              '${clearing.map((c) => '${c.model} ${_fmtPct(c.rainPct!)}%').join(', ')}.',
          confidence: _lowestConfidence([...clearing, bar]),
          checks: _fewestChecks([...clearing, bar]),
        ));
      } else {
        // The finding this item exists for. Deliberately phrased as what the
        // models FAILED to do rather than as praise for the baseline: nobody
        // should come away thinking persistence is a forecast worth using,
        // only that the guidance did not earn its place here at this lead.
        var bestModel = eligible.first;
        for (final c in eligible) {
          if (c.rainPct! > bestModel.rainPct!) bestModel = c;
        }
        findings.add(Finding(
          kind: 'baseline',
          claim: 'At Day+$k, no model here beats ${bar.model} by more than '
              'noise — the guidance is not yet earning its place at this '
              'lead time.',
          evidence: '$barEvidence Best model: ${bestModel.model} '
              '${_fmtPct(bestModel.rainPct!)}%.',
          confidence: _lowestConfidence([...eligible, bar]),
          checks: _fewestChecks([...eligible, bar]),
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
    // The WEAKEST SCORED model sets the confidence, not the best-covered one —
    // not every model reaches every lead time, and reporting the maximum as
    // "per model" would overstate coverage for exactly the models that have
    // least.
    //
    // Models with NO checks are excluded from setting that number and named
    // separately instead, because never-scored and scored-less are different
    // claims. When the local met service was first added, one newcomer at
    // zero turned an honest "8 checks per model" into "0 check(s) per model —
    // not enough to say anything" with eight days of scored forecasts sitting
    // right there. That fix landed in Python and was never ported here, and
    // no vector case had a zero-check model, so nothing caught it — see
    // review.py for the original.
    final scored = atLead.where((c) => c.checks > 0).map((c) => c.checks).toList();
    var checks = scored.isEmpty ? 0 : scored.first;
    for (final n in scored) {
      if (n < checks) checks = n;
    }
    var richest = atLead.first.checks;
    for (final c in atLead) {
      if (c.checks > richest) richest = c.checks;
    }
    final behind = (atLead
        .where((c) => c.checks > 0 && c.checks < richest)
        .map((c) => c.model)
        .toList()
      ..sort());
    final unscored =
        (atLead.where((c) => c.checks == 0).map((c) => c.model).toList()..sort());

    // WHETHER MODELS CAN BE RANKED IS THE RANKING GATE'S QUESTION, NOT THIS
    // ONE'S — ROADMAP item 85. `checks` is the weakest scored model's
    // coverage; the gate excludes anything under the comparison floor and
    // compares what is left, so one thin model lowers this figure without
    // touching the evidence behind a ranking between two well-covered ones.
    // The count is unchanged and the CONCLUSION defers to the same
    // eligibility rule the gate uses.
    final comparable =
        atLead.where((c) => c.checks >= reviewMinChecksForComparison).toList();

    // "per model" asserts a figure EVERY model has. `checks` is the weakest
    // scored model's coverage, so when coverage is uneven that claim is false
    // — and the uneven-coverage clause below states a different per-model
    // number in the same breath. Measured on the live 2026-09-11 prompt:
    // "22 check(s) per model" beside "the 31 check(s) the other models have".
    // When coverage IS even, "per model" is exactly true, so it stays.
    final scope =
        behind.isNotEmpty ? 'for the least-covered model' : 'per model';
    final conf = confidenceFor(checks);
    if (conf == 'insufficient') {
      final need = reviewConfidenceBands.first.$1 - checks;
      if (comparable.length >= 2) {
        parts.add('Day+$k: $checks check(s) $scope — not enough to say '
            'anything there, though '
            '${comparable.length} models have enough checks to compare. '
            'Any ranking below rests on those, not on this number.');
      } else {
        parts.add('Day+$k: $checks check(s) $scope — not enough to say anything; '
            'roughly $need more day(s) before even a provisional read.');
      }
    } else if (conf == 'provisional') {
      if (comparable.length >= 2) {
        parts.add('Day+$k: $checks check(s) $scope — directional only, '
            'though ${comparable.length} '
            'models have enough checks to compare. Any ranking below '
            'rests on those, not on this number.');
      } else {
        parts.add('Day+$k: $checks check(s) $scope — directional only, '
            'not yet enough to rank models against each other.');
      }
    } else if (conf == 'usable') {
      parts.add('Day+$k: $checks check(s) $scope — enough to compare models, '
          'though differences smaller than about 15 points remain noise.');
    } else {
      parts.add('Day+$k: $checks check(s) $scope — a settled picture.');
    }
    if (behind.isNotEmpty) {
      parts.add('(Coverage at Day+$k is uneven: ${behind.join(', ')} '
          '${behind.length == 1 ? 'has' : 'have'} fewer than the '
          '$richest check(s) the other models have, so any comparison '
          'at this lead time is not like-for-like.)');
    }
    if (unscored.isNotEmpty) {
      parts.add('(${unscored.join(', ')} '
          '${unscored.length == 1 ? 'has' : 'have'} '
          'no verified checks at Day+$k yet and '
          '${unscored.length == 1 ? 'is' : 'are'} '
          'not included in the figure above.)');
    }
  }
  return parts.join(' ');
}
