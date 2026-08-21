// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'package:http/http.dart' as http;

import 'provider.dart';
import 'schema.dart';

/// Claude via the Anthropic Messages API.
///
/// Differs from the others in every way that matters, which is why it is its
/// own class rather than a variant: `x-api-key` instead of Bearer, a
/// mandatory version header, the system prompt as a TOP-LEVEL field rather
/// than a message role, required `max_tokens`, and structured output via
/// FORCED TOOL USE rather than a response_format.
///
/// That last one is the sturdiest of the three paths: the `tool_use` block
/// carries an already-parsed object, so there is no JSON-in-a-string step
/// and no markdown fence to strip.
class AnthropicProvider implements LlmProvider {
  static const String defaultBaseUrl = 'https://api.anthropic.com';
  static const String anthropicVersion = '2023-06-01';
  static const String toolName = 'structured_response';

  /// Generous but bounded. The real production forecast measured ~2,350
  /// output tokens, and multiple audience voices will grow that — a low
  /// ceiling truncates mid-narrative, which surfaces as stop_reason
  /// "max_tokens" and is reported explicitly rather than silently returning
  /// half a forecast.
  static const int defaultMaxTokens = 8192;

  final String apiKey;
  @override
  final String model;
  final String baseUrl;
  final int maxTokens;
  final http.Client _client;

  /// Base backoff between retries; tests pass Duration.zero.
  final Duration retryDelay;

  /// Called before every request this provider sends, retries included.
  /// Used by the app to count spend against the user's own cap.
  final Future<void> Function()? beforeAttempt;

  AnthropicProvider({
    required this.apiKey,
    required this.model,
    this.baseUrl = defaultBaseUrl,
    this.maxTokens = defaultMaxTokens,
    http.Client? client,
    this.retryDelay = retryBaseDelay,
    this.beforeAttempt,
  }) : _client = client ?? http.Client() {
    if (apiKey.isEmpty) throw ArgumentError('AnthropicProvider requires an api_key.');
    if (model.isEmpty) throw ArgumentError('AnthropicProvider requires a model id.');
  }

  Uri get endpoint =>
      Uri.parse('${baseUrl.replaceAll(RegExp(r"/+$"), "")}/v1/messages');

  @override
  Future<ForecastResponse> generate({
    required String systemPrompt,
    required String userPrompt,
  }) async {
    final payload = {
      'model': model,
      'max_tokens': maxTokens,
      'system': systemPrompt,
      'messages': [
        {'role': 'user', 'content': userPrompt}
      ],
      'tools': [
        {
          'name': toolName,
          'description':
              'Return the complete forecast object. This is the only way to '
                  'answer; do not reply with prose.',
          'input_schema': strictForecastSchema(),
        }
      ],
      // Forces the tool call. Without this a model may answer in prose and
      // produce no structured output at all.
      'tool_choice': {'type': 'tool', 'name': toolName},
    };

    final resp = await postWithRetry(
      client: _client,
      uri: endpoint,
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': anthropicVersion,
        'content-type': 'application/json',
      },
      payload: payload,
      label: 'Anthropic',
      baseDelay: retryDelay,
      beforeAttempt: beforeAttempt,
    );

    final body = decodeJsonBody(resp, 'Anthropic');
    if (resp.statusCode != 200 || body['type'] == 'error') {
      final err = body['error'];
      final msg = err is Map ? err['message'] : null;
      throw LlmResponseError('Anthropic error (HTTP ${resp.statusCode}): ${msg ?? resp.body}');
    }

    Map<String, Object?>? toolInput;
    for (final block in (body['content'] as List?) ?? const []) {
      if (block is Map && block['type'] == 'tool_use') {
        toolInput = (block['input'] as Map).cast<String, Object?>();
        break;
      }
    }

    if (toolInput == null) {
      final stop = body['stop_reason'];
      if (stop == 'max_tokens') {
        throw LlmResponseError(
            'Anthropic response was truncated at max_tokens=$maxTokens before '
            'completing the structured response — raise maxTokens.');
      }
      throw LlmResponseError('Anthropic returned no tool_use block (stop_reason=$stop).');
    }

    try {
      return ForecastResponse.fromJson(toolInput);
    } catch (e) {
      throw LlmResponseError('Anthropic response failed schema validation: $e');
    }
  }
}
