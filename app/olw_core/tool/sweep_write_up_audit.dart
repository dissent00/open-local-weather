// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for the write-up gate, per AGENTS.md's rule that
// vectors pin the cases you chose and not the function — the gate's text
// handling (Python's splitlines() and strip()) is where a port parts company
// with the original on separators and whitespace a vector never carries.
//
// Input: a JSON list of cases {markdown, headings} and a JSON list of
// Python's defect lists, both written by sweep_write_up_audit.py beside this
// file. Prints the divergences and exits 1 on any. Last run 2026-10-09:
// 3,000 cases (332 passing, every defect kind present), 0 divergences.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List;
  final want = jsonDecode(File(args[1]).readAsStringSync()) as List;
  var bad = 0;
  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final got = auditWriteUp(c['markdown'] as String, List<String>.from(c['headings'] as List));
    if (jsonEncode(got) != jsonEncode(want[i])) {
      bad++;
      if (bad <= 5) {
        stdout.writeln('case $i\n  dart: ${jsonEncode(got)}\n  py  : ${jsonEncode(want[i])}\n  text: ${jsonEncode(c['markdown'])}');
      }
    }
  }
  stdout.writeln('swept ${cases.length} cases, $bad divergence(s)');
  if (bad > 0) exitCode = 1;
}
