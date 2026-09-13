// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for the narrative claim checker, per AGENTS.md's rule
// that vectors pin the cases you chose and not the function.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List;
  final want = jsonDecode(File(args[1]).readAsStringSync()) as List;
  var bad = 0;
  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final got = falseWeekdayClaims(
        c['text'] as String, DateTime.parse(c['today'] as String));
    if (jsonEncode(got) != jsonEncode(want[i])) {
      bad++;
      if (bad <= 5) {
        stdout.writeln('${c['today']} ${c['text']}\n  dart: ${jsonEncode(got)}\n  py  : ${jsonEncode(want[i])}');
      }
    }
  }
  stdout.writeln('swept ${cases.length} cases, $bad divergence(s)');
  if (bad > 0) exitCode = 1;
}
