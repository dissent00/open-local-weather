// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'provider.dart';
import 'schema.dart';

/// One implementation covering every service speaking the OpenAI
/// `/chat/completions` API: OpenAI, OpenRouter, Groq, Cerebras, Together,
/// vLLM, LM Studio and Ollama.
///
/// Point it at whichever endpoint the user has a key for. On a phone this is
/// the provider that gives real choice without shipping one adapter per
/// vendor.
class OpenAiCompatProvider implements LlmProvider {
  static const Set<String> validJsonModes = {'json_schema', 'json_object'};

  final String apiKey;
  @override
  final String model;
  final String baseUrl;

  /// `json_schema` (default) actually constrains generation to the shape.
  /// `json_object` only guarantees valid JSON, so the schema is injected
  /// into the prompt instead — the fallback that makes local runtimes usable.
  final String jsonMode;
  final http.Client _client;

  /// Base backoff between retries; tests pass Duration.zero.
  final Duration retryDelay;

  OpenAiCompatProvider({
    required this.apiKey,
    required this.model,
    required this.baseUrl,
    this.jsonMode = 'json_schema',
    http.Client? client,
    this.retryDelay = retryBaseDelay,
  }) : _client = client ?? http.Client() {
    // apiKey may legitimately be empty: Ollama and LM Studio need no key,
    // and refusing to construct without one would block the free local path.
    if (model.isEmpty) throw ArgumentError('OpenAiCompatProvider requires a model id.');
    if (baseUrl.isEmpty) throw ArgumentError('OpenAiCompatProvider requires a base_url.');
    if (!validJsonModes.contains(jsonMode)) {
      throw ArgumentError('jsonMode must be one of $validJsonModes');
    }
  }

  Uri get endpoint =>
      Uri.parse('${baseUrl.replaceAll(RegExp(r"/+$"), "")}/chat/completions');

  @override
  Future<ForecastResponse> generate({
    required String systemPrompt,
    required String userPrompt,
  }) async {
    final schema = strictForecastSchema();
    var system = systemPrompt;
    final Map<String, Object?> responseFormat;

    if (jsonMode == 'json_schema') {
      responseFormat = {
        'type': 'json_schema',
        'json_schema': {
          'name': forecastSchemaName,
          'strict': true,
          'schema': schema,
        },
      };
    } else {
      responseFormat = {'type': 'json_object'};
      system = '$systemPrompt\n\nReturn ONLY a JSON object conforming exactly '
          'to this JSON Schema. Do not wrap it in markdown fences or add '
          'commentary:\n${const JsonEncoder.withIndent('  ').convert(schema)}';
    }

    final headers = {'Content-Type': 'application/json'};
    if (apiKey.isNotEmpty) headers['Authorization'] = 'Bearer $apiKey';

    final resp = await postWithRetry(
      client: _client,
      uri: endpoint,
      headers: headers,
      payload: {
        'model': model,
        'messages': [
          {'role': 'system', 'content': system},
          {'role': 'user', 'content': userPrompt},
        ],
        'response_format': responseFormat,
      },
      label: 'LLM',
      baseDelay: retryDelay,
    );

    final body = decodeJsonBody(resp, 'LLM');
    if (resp.statusCode != 200 || body.containsKey('error')) {
      final err = body['error'];
      final msg = err is Map ? err['message'] : null;
      throw LlmResponseError('LLM error (HTTP ${resp.statusCode}): ${msg ?? resp.body}');
    }

    final choices = (body['choices'] as List?) ?? const [];
    if (choices.isEmpty) throw LlmResponseError('LLM returned no choices.');

    final message = (choices.first as Map)['message'];
    final text = message is Map ? message['content'] : null;
    if (text == null) {
      throw LlmResponseError(
          'LLM returned empty content (finish_reason='
          '${(choices.first as Map)['finish_reason']}).');
    }

    return parseForecast(text as String, 'LLM');
  }
}
