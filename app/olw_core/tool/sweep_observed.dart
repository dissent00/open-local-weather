// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Cross-language sweep for upstream ROADMAP item 121, per AGENTS.md's rule
// that vectors pin the cases you chose and not the function. Three of the six
// dimensions round, and rounding is the divergence this project has been
// bitten by most — item 88 found ten, and a Dart port once passed every
// vector case while disagreeing with Python on 962 of 4801 swept values.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final cases = jsonDecode(File(args[0]).readAsStringSync()) as List;
  final want = jsonDecode(File(args[1]).readAsStringSync()) as List;
  var bad = 0;
  for (var i = 0; i < cases.length; i++) {
    final c = (cases[i] as Map).cast<String, Object?>();
    final got = describeObservedSoFar(
      ObservedSoFar(
        precipitation: c['precipitation'] as bool?,
        precipitationOnset: c['precipitation_onset'] as String?,
        thunder: c['thunder'] as bool?,
        highC: (c['high_c'] as num?)?.toDouble(),
        lowC: (c['low_c'] as num?)?.toDouble(),
        peakWindKmh: (c['peak_wind_kmh'] as num?)?.toDouble(),
        cloudOktas: (c['cloud_oktas'] as num?)?.toDouble(),
      ),
      asOf: c['as_of'] as String?,
    );
    if (got != want[i]) {
      bad++;
      if (bad <= 10) {
        stdout.writeln('case $i ${jsonEncode(c)}');
        stdout.writeln('  dart: ${jsonEncode(got)}');
        stdout.writeln('  py  : ${jsonEncode(want[i])}');
      }
    }
  }
  stdout.writeln('${cases.length} cases, $bad divergences');
  exit(bad == 0 ? 0 : 1);
}
