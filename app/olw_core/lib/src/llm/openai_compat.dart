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

  /// The models an OpenRouter-style gateway should try, in order, inside ONE
  /// request — upstream item 81. Empty for every other endpoint this class
  /// covers. See the payload below for why it is conditional.
  final List<String> fallbackModels;

  /// Restricts the gateway to upstreams supporting every parameter in the
  /// request, which for this project means the JSON schema.
  final bool requireParameters;

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

  OpenAiCompatProvider({
    required this.apiKey,
    required this.model,
    required this.baseUrl,
    this.jsonMode = 'json_schema',
    this.fallbackModels = const [],
    this.requireParameters = false,
    http.Client? client,
    this.retryPolicy = RetryPolicy.interactive,
    this.beforeAttempt,
    this.onResponse,
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
  Future<T> generate<T>({
    required String systemPrompt,
    required String userPrompt,
    required ResponseShape<T> shape,
  }) async {
    final schema = shape.strictSchema();
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
        // OPENROUTER'S OWN FALLBACK, inside one request — upstream item 81.
        // `models` is tried in order until one succeeds, so the gateway walks
        // its own list and the chain above only carries the hop BETWEEN
        // vendors. Sent only when a deployment configures a list: these are
        // OpenRouter extensions and OpenAI itself rejects unknown top-level
        // fields, while this class also covers Groq, Together, vLLM and
        // Ollama. The primary comes first, because `model` above is the one
        // the record names.
        if (fallbackModels.isNotEmpty) 'models': [model, ...fallbackModels],
        // Without this OpenRouter may route to an upstream that treats
        // `response_format` as a hint — their docs say enforcement varies —
        // and the call is paid for and then fails validation, which reads as
        // the MODEL being unable to follow the schema rather than the route
        // being wrong.
        if (requireParameters) 'provider': {'require_parameters': true},
      },
      label: 'LLM',
      policy: retryPolicy,
      beforeAttempt: beforeAttempt,
    );

    final body = decodeJsonBody(resp, 'LLM');
    if (resp.statusCode != 200 || body.containsKey('error')) {
      final err = body['error'];
      final msg = err is Map ? err['message'] : null;
      // WHICH KIND OF FAILURE, so a chain above can tell a vendor that is down
      // from a request that was wrong — upstream item 81.
      final detail = 'LLM error (HTTP ${resp.statusCode}): ${msg ?? resp.body}';
      throw unavailableStatusCodes.contains(resp.statusCode)
          ? LlmUnavailableError(detail)
          : LlmResponseError(detail);
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

    final parsed = parseResponse(text as String, 'LLM', shape);

    final usage = (body['usage'] as Map?) ?? const {};
    onResponse?.call(LlmResponseMeta(
      finishReason: (choices.first as Map)['finish_reason'] as String?,
      inputTokens: usage['prompt_tokens'] as int?,
      outputTokens: usage['completion_tokens'] as int?,
    ));

    return parsed;
  }
}
