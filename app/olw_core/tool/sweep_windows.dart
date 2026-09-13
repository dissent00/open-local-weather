// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for upstream ROADMAP item 104, per AGENTS.md's rule
// that vectors pin the cases you chose and not the function. Reads the cases
// and Python's answers written by spec's sweep helper; reports every window
// list the two languages disagree on.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List;
  final want = jsonDecode(File(args[1]).readAsStringSync()) as List;
  var bad = 0;
  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final got = forecastWindows(
      DateTime.parse(c['now'] as String),
      DateTime.parse(c['sunrise'] as String),
      DateTime.parse(c['sunset'] as String),
      (c['horizon'] as List).cast<String>(),
      DateTime.parse(c['next_sunrise'] as String),
    ).map((w) => w.toJson()).toList();
    if (jsonEncode(got) != jsonEncode(want[i])) {
      bad++;
      stdout.writeln('case $i (${c['now']}, sunrise ${c['sunrise']}):');
      stdout.writeln('  dart: ${jsonEncode(got)}');
      stdout.writeln('  py  : ${jsonEncode(want[i])}');
    }
  }
  stdout.writeln('swept ${cases.length} cases, $bad divergence(s)');
  if (bad > 0) exitCode = 1;
}
