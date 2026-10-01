// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for the code blend's row, per AGENTS.md's rule that
// vectors pin the cases you chose and not the function — upstream item 189
// added the wet voters' median onset and the record-weighted amount rounded
// to one decimal, which is exactly where Python's round-half-even and a port
// have parted company before (962 of 4801 values, 2026-08-28).
//
// Input: a JSON list of cases {predictions, weights, highs, lows, wind_kmh}
// and a JSON list of Python's rows, both written by sweep_code_blend.py
// beside this file. Prints the divergences and exits 1 on any. Last run
// 2026-10-01: 20,000 cases, 0 divergences.
import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List;
  final want = jsonDecode(File(args[1]).readAsStringSync()) as List;
  var bad = 0;
  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    Map<String, double> doubles(Object? raw) => {
          for (final e in ((raw as Map?) ?? const {}).entries)
            e.key as String: (e.value as num).toDouble()
        };
    final got = codeBlendPrediction(
      [for (final p in c['predictions'] as List) ModelPrediction.fromJson((p as Map).cast<String, Object?>())],
      doubles(c['weights']),
      doubles(c['highs']),
      doubles(c['lows']),
      (c['wind_kmh'] as num?)?.toDouble(),
    )?.toJson();
    // Key order is each language's own; the values are the contract.
    if (jsonEncode(_canonical(got)) != jsonEncode(_canonical(want[i]))) {
      bad++;
      if (bad <= 5) {
        stdout.writeln('case $i\n  dart: ${jsonEncode(got)}\n  py  : ${jsonEncode(want[i])}');
      }
    }
  }
  stdout.writeln('swept ${cases.length} cases, $bad divergence(s)');
  if (bad > 0) exitCode = 1;
}

Object? _canonical(Object? value) {
  if (value is Map) {
    return SplayTreeMap<String, Object?>.fromIterable(value.keys.cast<String>(),
        value: (k) => _canonical(value[k]));
  }
  if (value is List) return [for (final v in value) _canonical(v)];
  return value;
}
