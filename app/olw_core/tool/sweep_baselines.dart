// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// One-off cross-language sweep for ROADMAP item 57, per AGENTS.md's rule that
// vectors pin the cases you chose and not the function. Reads the Python
// side's inputs and outputs from a JSON file and reports any disagreement.
import 'dart:convert';
import 'dart:io';

import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args.first).readAsStringSync()) as List;
  var mismatches = 0;

  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final raw = (c['actuals'] as Map).cast<String, Object?>();
    final actuals = {
      for (final e in raw.entries)
        DateTime.parse(e.key):
            DailyActual.fromJson((e.value as Map).cast<String, Object?>())
    };

    final clim = climatologyPrediction(actuals,
        before: DateTime.parse(c['before'] as String));
    final pers = persistencePrediction(
      actuals[DateTime.parse(c['last'] as String)],
      includeOnset: c['include_onset'] as bool,
    );

    for (final pair in [
      ['climatology', clim?.toJson(), c['clim']],
      ['persistence', pers?.toJson(), c['pers']],
    ]) {
      final label = pair[0] as String;
      final got = pair[1] as Map<String, Object?>?;
      final want = pair[2] == null
          ? null
          : (pair[2] as Map).cast<String, Object?>();

      if (got == null || want == null) {
        if (got != want) {
          mismatches++;
          stdout.writeln('case $i $label: null mismatch got=$got want=$want');
        }
        continue;
      }
      for (final k in want.keys) {
        final a = got[k];
        final b = want[k];
        if (a is num && b is num) {
          if ((a - b).abs() > 1e-12) {
            mismatches++;
            stdout.writeln('case $i $label.$k: $a != $b');
          }
        } else if (a != b) {
          mismatches++;
          stdout.writeln('case $i $label.$k: $a != $b');
        }
      }
    }
  }

  stdout.writeln('swept ${cases.length} cases, $mismatches mismatch(es)');
  if (mismatches > 0) exitCode = 1;
}
