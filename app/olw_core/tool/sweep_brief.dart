// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for the brief, per AGENTS.md's rule that vectors pin
// the cases you chose and not the function — the brief formats every number
// the page shows, at every precision the pipeline produces, and that is
// where a port parts company with the original (962 of 4801 swept values
// on 2026-08-28).
//
// Input: a JSON list of cases {points, inputs} and a JSON list of Python's
// answers {basin, full, mini}, both written by sweep_brief.py beside this
// file. Prints the divergences and exits 1 on any. Last run 2026-10-09:
// 3,000 cases, the basin reduction and both tiers over every section, 0
// divergences.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List;
  final want = jsonDecode(File(args[1]).readAsStringSync()) as List;
  var bad = 0;
  void check(int i, String what, Object? got, Object? expected) {
    if (jsonEncode(got) == jsonEncode(expected)) return;
    bad++;
    if (bad <= 6) {
      stdout.writeln('case $i, $what\n  dart: ${jsonEncode(got)}\n  py  : ${jsonEncode(expected)}');
    }
  }

  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final w = (want[i] as Map).cast<String, Object?>();
    check(i, 'basin', reduceBasinPressure(c['points'] as List?), w['basin']);
    final inputs = BriefInputs.fromJson((c['inputs'] as Map).cast<String, Object?>());
    check(i, 'full', renderBrief(inputs, tier: tierFull, sections: briefSections), w['full']);
    check(i, 'mini', renderBrief(inputs, tier: tierMini, sections: briefSections), w['mini']);
  }
  stdout.writeln('swept ${cases.length} cases, $bad divergence(s)');
  if (bad > 0) exitCode = 1;
}
