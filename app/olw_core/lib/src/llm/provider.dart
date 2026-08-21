// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'schema.dart';

/// The seam that keeps forecast logic independent of which LLM writes the
/// narrative — the Dart counterpart of Python's `llm/provider.py` Protocol.
///
/// Implementations must THROW on failure (network error, no usable response,
/// schema validation failure) rather than returning null or a partial
/// object. A failed generate() is a reason to abort the run, not to publish
/// a degraded forecast.
abstract interface class LlmProvider {
  /// Model id, surfaced for cost/latency display and health checks.
  String get model;

  Future<ForecastResponse> generate({
    required String systemPrompt,
    required String userPrompt,
  });
}

/// Raised when a provider call fails outright or returns something that
/// doesn't validate.
class LlmResponseError implements Exception {
  final String message;
  LlmResponseError(this.message);
  @override
  String toString() => 'LlmResponseError: $message';
}

/// Transient statuses worth retrying, shared by all three providers.
///
/// 529 is Anthropic's `overloaded_error`. It is included for everyone
/// because it costs nothing and this project has already lost a whole run to
/// exactly this class of failure before retries existed.
const Set<int> retryableStatusCodes = {408, 409, 429, 500, 502, 503, 504, 529};
const int maxAttempts = 4;
const Duration retryBaseDelay = Duration(seconds: 5); // 5s, 10s, 20s

/// Honors a `Retry-After` header when one is sent, ignoring the HTTP-date
/// form and anything implausibly long.
///
/// A provider asking us to wait ten minutes is better handled by failing and
/// letting the next scheduled attempt pick it up than by blocking — doubly
/// so on a phone, where a long sleep inside a background task will simply be
/// killed by the OS.
Duration? retryAfter(Map<String, String> headers) {
  final raw = headers['retry-after'] ?? headers['Retry-After'];
  if (raw == null) return null;
  final seconds = double.tryParse(raw);
  if (seconds == null || seconds <= 0 || seconds > 60) return null;
  return Duration(milliseconds: (seconds * 1000).round());
}

/// Strips a ```json fence some models add despite being told not to.
String stripCodeFence(String text) {
  final s = text.trim();
  if (!s.startsWith('```')) return s;
  final lines = s.split('\n');
  if (lines.isNotEmpty && lines.first.startsWith('```')) lines.removeAt(0);
  if (lines.isNotEmpty && lines.last.trim() == '```') lines.removeLast();
  return lines.join('\n').trim();
}

/// POSTs JSON with bounded exponential backoff on transient failures.
///
/// Deliberately SHARED by all three providers here, unlike the Python side
/// where each owns its own copy. That difference is intentional rather than
/// sloppy: Python's Gemini client predated the others and they diverged
/// (only some honor Retry-After), whereas all three Dart providers were
/// written together and genuinely behave identically. If one later needs
/// different semantics, split it out then rather than pre-emptively.
///
/// Non-retryable statuses return immediately for the caller to raise on —
/// a bad key or unknown model fails the same way every time, and retrying
/// only burns quota and, on a phone, battery.
Future<http.Response> postWithRetry({
  required http.Client client,
  required Uri uri,
  required Map<String, String> headers,
  required Object payload,
  required String label,
  Duration timeout = const Duration(seconds: 120),
  /// Injectable so tests don't actually sleep through the backoff. Real
  /// callers leave the default; a test passes Duration.zero.
  Duration baseDelay = retryBaseDelay,

  /// Called immediately before EACH request, retries included.
  ///
  /// This exists so a spend cap can count the thing that actually costs
  /// money. Wrapping generate() instead undercounts by up to maxAttempts,
  /// because one generate() can send that many requests — and the undercount
  /// is worst exactly when it matters, since retries fire on 429 and 5xx,
  /// i.e. when a provider is already rate-limiting or struggling.
  ///
  /// Throwing from it aborts the retry loop, which is the right answer to
  /// "the budget is gone": the error propagates to the caller unchanged.
  Future<void> Function()? beforeAttempt,
}) async {
  Object? lastError;
  for (var attempt = 1; attempt <= maxAttempts; attempt++) {
    Duration delay = baseDelay * (1 << (attempt - 1));
    if (beforeAttempt != null) await beforeAttempt();
    try {
      final resp = await client
          .post(uri, headers: headers, body: jsonEncode(payload))
          .timeout(timeout);
      if (!retryableStatusCodes.contains(resp.statusCode)) return resp;
      lastError = '$label returned HTTP ${resp.statusCode}';
      delay = retryAfter(resp.headers) ?? delay;
    } catch (e) {
      lastError = e;
    }
    if (attempt < maxAttempts) await Future<void>.delayed(delay);
  }
  throw LlmResponseError('$label request failed after $maxAttempts attempts: $lastError');
}

Map<String, Object?> decodeJsonBody(http.Response resp, String label) {
  try {
    return jsonDecode(resp.body) as Map<String, Object?>;
  } catch (_) {
    final snippet =
        resp.body.length > 500 ? resp.body.substring(0, 500) : resp.body;
    throw LlmResponseError(
        '$label returned a non-JSON response (HTTP ${resp.statusCode}): $snippet');
  }
}

/// Parses and validates a raw JSON string into a [ForecastResponse].
///
/// Validation is the real safety net: structured-output modes constrain
/// generation but do not guarantee it, so every provider's output is checked
/// against the expected shape before anything downstream sees it.
ForecastResponse parseForecast(String text, String label) {
  final Object? data;
  try {
    data = jsonDecode(stripCodeFence(text));
  } catch (_) {
    final snippet = text.length > 500 ? text.substring(0, 500) : text;
    throw LlmResponseError('$label response was not valid JSON: $snippet');
  }
  try {
    return ForecastResponse.fromJson(data as Map<String, Object?>);
  } catch (e) {
    throw LlmResponseError('$label response failed schema validation: $e');
  }
}
