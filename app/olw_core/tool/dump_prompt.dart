// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
// Dev helper: prints the built system prompt for diffing against the vector.
import 'dart:convert';
import 'dart:io';
import 'package:olw_core/olw_core.dart';

void main(List<String> args) {
  final vectors = jsonDecode(File('../../spec/vectors/llm_system_prompt.json').readAsStringSync())
      as Map<String, Object?>;
  final out = <String>[];
  for (final c in (vectors['cases'] as List).cast<Map<String, Object?>>()) {
    final i = c['input'] as Map<String, Object?>;
    final loc = i['location'] as Map<String, Object?>;
    final sec = loc['secondary_point'] as Map<String, Object?>;
    out.add(buildSystemPrompt(
      LocationConfig(
        regionName: loc['region_name'] as String,
        primaryPlaceName: loc['primary_place_name'] as String,
        timezone: 'UTC', lat: 0, lon: 0,
        secondaryPoint: SecondaryPoint(
          enabled: sec['enabled'] as bool,
          name: sec['name'] as String,
          sectionLabel: sec['section_label'] as String,
        ),
      ),
      historicalLookbackDaysArg: i['historical_lookback_days'] as int,
      rollingWindowShortArg: i['rolling_window_short'] as int,
      rollingWindowLongArg: i['rolling_window_long'] as int,
      isRefresh: i['is_refresh'] as bool,
    ));
  }
  print(jsonEncode(out));
}
