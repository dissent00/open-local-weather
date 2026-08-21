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

  /// Base backoff between retries; tests pass Duration.zero.
  final Duration retryDelay;

  /// Called before every request this provider sends, retries included.
  /// Used by the app to count spend against the user's own cap.
  final Future<void> Function()? beforeAttempt;

  GeminiProvider({
    required this.apiKey,
    required this.model,
    this.thinkingLevel,
    http.Client? client,
    this.retryDelay = retryBaseDelay,
    this.beforeAttempt,
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
  Future<ForecastResponse> generate({
    required String systemPrompt,
    required String userPrompt,
  }) async {
    final generationConfig = <String, Object?>{
      'responseMimeType': 'application/json',
      'responseSchema': geminiForecastSchema(),
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
      baseDelay: retryDelay,
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

    final String text;
    try {
      text = ((candidates.first as Map)['content'] as Map)['parts'][0]['text'] as String;
    } catch (e) {
      throw LlmResponseError('Gemini response did not contain the expected payload: $e');
    }

    return parseForecast(text, 'Gemini');
  }
}
