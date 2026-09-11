// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'prompt.dart';
import 'provider.dart';
import 'schema.dart';

/// What the reader is shown where the forecast would have been.
///
/// NOT EMPTY, and not an apology. An empty narrative renders as a missing
/// section — the page keeps its stat tiles and simply looks short, which
/// reads as a forecast that had nothing to say rather than one whose
/// write-up failed. Mirrors Python's NARRATIVE_UNAVAILABLE_MARKDOWN.
const String narrativeUnavailableMarkdown = '''## Overview

The forecast below could not be written up this issuance: the model call that turns the day's figures into prose did not complete. The figures themselves were produced normally and are shown as usual — they are the same numbers this forecast is scored on.

There is no discussion, no extended outlook and no hazard section for this issuance. Check a later one.''';

const String narrativeUnavailableVerification =
    'Not written this issuance — the write-up call did not complete.';

/// The merged forecast, and whether half of it had to be invented.
class ForecastCall {
  const ForecastCall({required this.response, this.narrativeError});

  final ForecastResponse response;

  /// The provider's own message when the rendering call failed, so the
  /// caller records a degradation rather than inferring one from the
  /// placeholder prose. Null on a clean run.
  final String? narrativeError;
}

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
Future<ForecastCall> generateForecastResponse({
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

  // THE SECOND CALL MAY FAIL WITHOUT COSTING THE FIRST. By here the scored
  // call exists and has been paid for, and it is the half the record
  // verifies against observations. Narrow on purpose: only the provider's
  // own failure is caught, so a bug in the merge still throws.
  NarrativeResponse narrative;
  String? narrativeError;
  try {
    narrative = await provider.generate(
      systemPrompt: narrativePrompt,
      userPrompt: buildNarrativeUserPrompt(userPrompt, judgment.toJson()),
      shape: narrativeShape,
    );
    onCall?.call('narrative');
  } on LlmResponseError catch (e) {
    narrative = const NarrativeResponse(
      yesterdayVerification: narrativeUnavailableVerification,
      verificationNotes: [],
      skillProfileSummaries: [],
      todayNarrative: narrativeUnavailableMarkdown,
    );
    narrativeError = e.toString();
  }

  return ForecastCall(
    response: mergeForecastResponse(judgment, narrative),
    narrativeError: narrativeError,
  );
}
