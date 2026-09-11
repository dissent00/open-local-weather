// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'prompt.dart';
import 'provider.dart';
import 'schema.dart';

/// The forecast as two calls, returning the merged RESPONSE.
///
/// Named for what it returns, because `forecast.dart` already exports a
/// public `generateForecast` that returns a whole [ForecastRun] — and a
/// collision there resolves to the outer function, which is a silent
/// infinite recursion rather than an error at the call site.
///
/// The forecast as two calls — upstream ROADMAP item 59 step 3.
///
/// ONE DEFINITION OF THE ORDER, mirroring Python's `llm/forecast_call.py`.
/// The renderer is handed the judgment's answer, so the judgment has to have
/// happened first. That is the whole shape of the split: one call decides,
/// the other describes what was decided.
///
/// `onCall` fires after each call with its name, for a caller that needs the
/// provider's per-call report before the next call overwrites it. Called
/// AFTER the call returns, so a thrown call fires nothing.
Future<ForecastResponse> generateForecastResponse({
  required LlmProvider provider,
  required String judgmentPrompt,
  required String narrativePrompt,
  required String userPrompt,
  void Function(String name)? onCall,
}) async {
  final judgment = await provider.generate(
    systemPrompt: judgmentPrompt,
    userPrompt: userPrompt,
    shape: judgmentShape,
  );
  onCall?.call('judgment');

  final narrative = await provider.generate(
    systemPrompt: narrativePrompt,
    userPrompt: buildNarrativeUserPrompt(userPrompt, judgment.toJson()),
    shape: narrativeShape,
  );
  onCall?.call('narrative');

  return mergeForecastResponse(judgment, narrative);
}
