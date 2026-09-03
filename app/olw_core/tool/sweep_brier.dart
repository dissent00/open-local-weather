// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for upstream ROADMAP item 58, per AGENTS.md's rule
// that vectors pin the cases you chose and not the function.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args.first).readAsStringSync()) as List;
  var bad = 0;
  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final got = brierScore((c['p'] as num).toDouble(), c['occurred'] as bool);
    final want = (c['brier'] as num).toDouble();
    if ((got - want).abs() > 1e-12) {
      bad++;
      stdout.writeln('case $i brier: $got != $want');
    }
    final gotSkill = brierSkillScore(
        (c['b'] as num).toDouble(), (c['ref'] as num).toDouble());
    final wantSkill = c['skill'] == null ? null : (c['skill'] as num).toDouble();
    if (gotSkill == null || wantSkill == null) {
      if (gotSkill != wantSkill) {
        bad++;
        stdout.writeln('case $i skill null mismatch: $gotSkill != $wantSkill');
      }
    } else if ((gotSkill - wantSkill).abs() > 1e-9) {
      bad++;
      stdout.writeln('case $i skill: $gotSkill != $wantSkill');
    }
  }
  stdout.writeln('swept ${cases.length} cases, $bad mismatch(es)');
  if (bad > 0) exitCode = 1;
}
