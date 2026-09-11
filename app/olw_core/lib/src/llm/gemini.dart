// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'package:http/http.dart' as http;

import 'provider.dart';
import 'schema.dart';

/// Gemini via `generateContent`, mirroring Python's `llm/gemini.py`.
///
/// Structured output uses `responseMimeType` + `responseSchema` — Gemini's
/// own dialect, not standard JSON Schema. The reasoning-effort control is
/// `generationConfig.thinkingConfig.thinkingLevel`: nested and camelCase.
/// That was established empirically against the live API, because the
/// documented snake_case form is the Python SDK's, and the REST endpoint
/// rejects it.
/// The only stop reason that means "I finished the answer". Everything else —
/// MAX_TOKENS, RECITATION, SAFETY, OTHER — means the text is not what was
/// asked for, however well it parses. Mirrors `FINISH_REASON_COMPLETE` in the
/// Python provider.
const String finishReasonComplete = 'STOP';

class GeminiProvider implements LlmProvider {
  static const String urlTemplate =
      'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent';
  static const Set<String> validThinkingLevels = {
    'minimal',
    'low',
    'medium',
    'high'
  };

  final String apiKey;
  @override
  final String model;

  /// null leaves Gemini's own default. The pipeline uses "high"; a phone may
  /// well prefer lower for latency and cost.
  final String? thinkingLevel;
  final http.Client _client;

  /// Attempts, backoff and timeout as one decision. Defaults to the
  /// interactive answer because this package's caller is an app; the
  /// alarm-scheduled run passes [RetryPolicy.batch] explicitly.
  final RetryPolicy retryPolicy;

  /// Called before every request this provider sends, retries included.
  /// Used by the app to count spend against the user's own cap.
  final Future<void> Function()? beforeAttempt;

  /// Optional. A provider that never reports one leaves the record's
  /// fields null, which reads as "did not say" rather than as zero.
  final OnResponse? onResponse;

  GeminiProvider({
    required this.apiKey,
    required this.model,
    this.thinkingLevel,
    http.Client? client,
    this.retryPolicy = RetryPolicy.interactive,
    this.beforeAttempt,
    this.onResponse,
  })  : _client = client ?? http.Client() {
    if (apiKey.isEmpty) throw ArgumentError('GeminiProvider requires an api_key.');
    if (model.isEmpty) throw ArgumentError('GeminiProvider requires a model id.');
    if (thinkingLevel != null && !validThinkingLevels.contains(thinkingLevel)) {
      throw ArgumentError('thinkingLevel must be one of $validThinkingLevels');
    }
  }

  Uri get endpoint =>
      Uri.parse(urlTemplate.replaceFirst('{model}', model))
          .replace(queryParameters: {'key': apiKey});

  @override
  Future<T> generate<T>({
    required String systemPrompt,
    required String userPrompt,
    required ResponseShape<T> shape,
  }) async {
    final generationConfig = <String, Object?>{
      'responseMimeType': 'application/json',
      'responseSchema': shape.geminiSchema(),
    };
    if (thinkingLevel != null) {
      generationConfig['thinkingConfig'] = {'thinkingLevel': thinkingLevel};
    }

    final payload = {
      'system_instruction': {
        'parts': [
          {'text': systemPrompt}
        ]
      },
      'contents': [
        {
          'role': 'user',
          'parts': [
            {'text': userPrompt}
          ]
        }
      ],
      'generationConfig': generationConfig,
    };

    final resp = await postWithRetry(
      client: _client,
      uri: endpoint,
      headers: const {'Content-Type': 'application/json'},
      payload: payload,
      label: 'Gemini',
      policy: retryPolicy,
      beforeAttempt: beforeAttempt,
    );

    final body = decodeJsonBody(resp, 'Gemini');
    if (resp.statusCode != 200 || body.containsKey('error')) {
      final err = body['error'];
      final msg = err is Map ? err['message'] : null;
      throw LlmResponseError('Gemini error (HTTP ${resp.statusCode}): ${msg ?? resp.body}');
    }

    final candidates = (body['candidates'] as List?) ?? const [];
    if (candidates.isEmpty) throw LlmResponseError('Gemini returned no candidates.');

    // WHY THE STOP REASON IS CHECKED BEFORE THE CONTENT IS READ.
    //
    // On 2026-09-10 the pipeline's evening run published a UV index of 15,930
    // characters: a plausible opening, then one word repeated some nine
    // hundred times, then kilobytes of unrelated recited text. It parsed, it
    // validated, it was stored and rendered, and every test stayed green,
    // because nothing asked why the model stopped talking. A candidate that
    // hit the token ceiling mid-loop is identical, to code that only reads
    // parts[0].text, to one that finished its sentence.
    //
    // Permissive about absence, strict about a wrong value: refusing a
    // response for a missing field would turn a provider change into a total
    // outage. Mirrors the Python provider.
    final finishReason = (candidates.first as Map)['finishReason'];
    if (finishReason != null && finishReason != finishReasonComplete) {
      throw LlmResponseError(
        'Gemini stopped for $finishReason, not $finishReasonComplete \u2014 '
        'the response is incomplete or is not the answer that was asked for.',
      );
    }

    final String text;
    try {
      text = ((candidates.first as Map)['content'] as Map)['parts'][0]['text'] as String;
    } catch (e) {
      throw LlmResponseError('Gemini response did not contain the expected payload: $e');
    }

    final parsed = parseResponse(text, 'Gemini', shape);

    // AFTER parsing, not before: a response that fails the schema produced no
    // forecast, and the error already carries the reason.
    final usage = (body['usageMetadata'] as Map?) ?? const {};
    onResponse?.call(LlmResponseMeta(
      finishReason: finishReason as String?,
      inputTokens: usage['promptTokenCount'] as int?,
      outputTokens: usage['candidatesTokenCount'] as int?,
    ));

    return parsed;
  }
}
