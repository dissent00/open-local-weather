// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'config.dart';
import 'dates.dart';
import 'models.dart';

/// Notice when a data source quietly stops supplying something.
///
/// Port of the Python `coverage.py`. The companion to tolerant parsing, and
/// the half that was missing: `ecmwf_ifs025` supplied no Day+0 wind for the
/// entire life of the reference deployment and nothing raised, because every
/// layer behaved correctly. An all-null array arrived, extraction recorded
/// null, scoring declined to score a null, and the rolling stats excluded it.
/// Absence propagated cleanly as absence, exactly as designed — which is
/// precisely why it stayed invisible.
///
/// Tolerance keeps the system RUNNING through an upstream change. This makes
/// the change VISIBLE. Different properties, and the second is what turns a
/// months-long silent gap into a one-day one.
///
/// It matters more in an app than on a server: an upstream change is a git
/// push there and a store release here, so knowing quickly is worth more,
/// and the app can degrade honestly in the meantime by naming the gap rather
/// than showing a quietly narrower model set.
///
/// THREE KINDS, because the obvious two are not enough:
///  - `regression`  — present before, absent now. An upstream rename.
///  - `peer_gap`    — never present here, but peers at this lead time DO
///                    supply it. The ECMWF case had no before-and-after
///                    transition to detect; what was visible from day one is
///                    that four other models reported wind and it did not.
///  - `never_published` — absent for every model. A property of the data,
///                    with nothing to investigate.

/// Fields worth watching.
///
/// `onset` is deliberately absent: it is populated only when rain is
/// forecast, so its absence is a legitimate forecast outcome rather than a
/// data gap, and at Day+3/Day+7 it carries no data by design. Watching it
/// would fire on every dry spell.
const List<String> watchedVariables = [
  'rain',
  'wind_kmh',
  'high_c',
  'low_c',
  'mslp_trend',
];

class CoverageFinding {
  const CoverageFinding({
    required this.kind,
    required this.model,
    required this.leadTimeDays,
    required this.variable,
    required this.lastSeen,
    required this.absentRuns,
    required this.checkedRuns,
    this.peersWithValue = const [],
  });

  final String kind; // regression | peer_gap | never_published
  final String model;
  final int leadTimeDays;
  final String variable;
  final DateTime? lastSeen;
  final int absentRuns;
  final int checkedRuns;
  final List<String> peersWithValue;

  String get message {
    final where = '$model Day+$leadTimeDays $variable';
    switch (kind) {
      case 'peer_gap':
        return '$where: never supplied in $checkedRuns run(s), while '
            '${peersWithValue.length} other model(s) do supply it '
            '(${peersWithValue.join(', ')}). Either this model genuinely does not '
            'publish it, or it is being requested under a name that returns '
            'nothing — the shape of the ECMWF wind gap. Worth checking once, then '
            'recording the answer.';
      case 'never_published':
        return '$where: not supplied by any model in $checkedRuns run(s) — a '
            'property of the data, not a fault.';
      default:
        return '$where: absent for the last $absentRuns run(s), last seen '
            '${lastSeen == null ? "never" : formatDate(lastSeen!)}. This is the '
            'signature of an upstream rename or a retired model — the value is '
            'being recorded as unknown, so nothing is wrong with the forecast, '
            'but a variable that used to be scored no longer is.';
    }
  }
}

Object? _valueOf(ModelPrediction p, String variable) {
  switch (variable) {
    case 'rain':
      return p.rain;
    case 'wind_kmh':
      return p.windKmh;
    case 'high_c':
      return p.highC;
    case 'low_c':
      return p.lowC;
    case 'mslp_trend':
      return p.mslpTrend;
    default:
      return null;
  }
}

/// Walks the stored record backwards, newest first.
///
/// Reads predictions as they were STORED rather than re-fetching, so a
/// finding always reflects what actually went into the record — which is
/// what got scored, or didn't.
List<CoverageFinding> detectCoverage({
  required List<ModelPrediction>? Function(DateTime rowDate, int leadTimeDays) predictionsFor,
  required DateTime today,
  required List<String> models,
  required List<int> leadTimesDays,
  int windowDays = coverageWindowDays,
  int absentRunsThreshold = coverageAbsentRuns,
}) {
  final byLead = <int, List<Map<String, ModelPrediction>>>{
    for (final k in leadTimesDays) k: []
  };
  var cursor = addDays(today, -1);
  final earliest = addDays(today, -windowDays);
  while (!cursor.isBefore(earliest)) {
    for (final k in leadTimesDays) {
      final preds = predictionsFor(cursor, k);
      if (preds != null && preds.isNotEmpty) {
        byLead[k]!.add({for (final p in preds) p.model: p});
      }
    }
    cursor = addDays(cursor, -1);
  }

  final findings = <CoverageFinding>[];
  for (final k in leadTimesDays) {
    final runs = byLead[k]!;
    if (runs.isEmpty) continue;
    for (final model in models) {
      // Runs in which this model appeared at all. A model absent entirely is
      // a different problem — a config change, or one added partway through
      // — and is not what this watches.
      final modelRuns = [for (final r in runs) if (r.containsKey(model)) r[model]!];
      if (modelRuns.isEmpty) continue;

      for (final variable in watchedVariables) {
        final anyPresent = modelRuns.any((p) => _valueOf(p, variable) != null);
        if (!anyPresent) {
          // Do any OTHER models supply this at this lead time? If so the gap
          // belongs to this model, not to the variable — the distinction
          // that makes the ECMWF case detectable at all.
          final peers = <String>{};
          for (final r in runs) {
            r.forEach((m, p) {
              if (m != model && _valueOf(p, variable) != null) peers.add(m);
            });
          }
          final sortedPeers = peers.toList()..sort();
          findings.add(CoverageFinding(
            kind: sortedPeers.isNotEmpty ? 'peer_gap' : 'never_published',
            model: model,
            leadTimeDays: k,
            variable: variable,
            lastSeen: null,
            absentRuns: modelRuns.length,
            checkedRuns: modelRuns.length,
            peersWithValue: sortedPeers,
          ));
          continue;
        }
        // Consecutive absences from the newest run backwards.
        var absent = 0;
        for (final p in modelRuns) {
          if (_valueOf(p, variable) != null) break;
          absent++;
        }
        if (absent >= absentRunsThreshold) {
          DateTime? lastSeen;
          var idx = 0;
          for (final r in runs) {
            final p = r[model];
            if (p != null && _valueOf(p, variable) != null) {
              lastSeen = addDays(today, -1 - idx);
              break;
            }
            idx++;
          }
          findings.add(CoverageFinding(
            kind: 'regression',
            model: model,
            leadTimeDays: k,
            variable: variable,
            lastSeen: lastSeen,
            absentRuns: absent,
            checkedRuns: modelRuns.length,
          ));
        }
      }
    }
  }
  return findings;
}

/// Findings a human should look at: something changed, or one model is alone
/// in not supplying something its peers do. `never_published` is excluded —
/// nothing supplies it, so there is nothing to chase, and reporting all three
/// at equal volume is how monitoring stops being read.
List<CoverageFinding> actionable(List<CoverageFinding> findings) =>
    [for (final f in findings) if (f.kind != 'never_published') f];
