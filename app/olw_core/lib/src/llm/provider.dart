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

/// How hard to try, how long to wait, and how long to allow one request.
///
/// There is no single right answer, because the two callers fail in opposite
/// directions and this package serves both:
///
/// - The alarm-scheduled run has nobody watching. Losing it costs a whole
///   forecast, and the next chance is hours away, so it should sit out a
///   provider's bad minute rather than give up inside it.
/// - Someone standing in the app with a phone in their hand has already
///   decided the forecast matters right now. Making them wait eight minutes
///   to be told it failed is worse than telling them in one that it did,
///   because IN THE APP THE USER IS THE RETRY LOOP: the error screen has a
///   retry button and they can see whether it is worth pressing. The
///   pipeline has no such person, which is exactly why its loop lives in
///   code.
///
/// Before 2026-09-04 both got the batch answer by default, which is how a
/// transient 503 could hold the UI for minutes before saying anything.
class RetryPolicy {
  /// Total requests, first attempt included. Each one is billable and each
  /// one is counted by [postWithRetry]'s beforeAttempt hook.
  final int attempts;

  /// Doubles per attempt: 30s gives 30s, 60s, 120s.
  final Duration baseDelay;

  /// Ceiling on a single request.
  final Duration timeout;

  const RetryPolicy({
    required this.attempts,
    required this.baseDelay,
    required this.timeout,
  });

  /// The longest this policy would ever sleep on its own guess, which is
  /// also the most it will honour from a provider's `Retry-After`. See
  /// [retryAfter].
  Duration get retryAfterMax => baseDelay * (1 << (attempts - 2));

  /// Someone is waiting and can see the result. One quick retry catches the
  /// single blip; past that, reporting beats sleeping.
  ///
  /// Worst case ~2.5 minutes, against ~8.5 for [batch]. The timeout stays
  /// generous because a slow model is slow for everyone — cutting it is how
  /// you turn a working forecast into a failed one to save nothing.
  static const interactive = RetryPolicy(
    attempts: 2,
    baseDelay: Duration(seconds: 3),
    timeout: Duration(seconds: 75),
  );

  /// Nobody is waiting. Matches the Python pipeline's constants deliberately:
  /// same attempt count, same 30/60/120 schedule, same 90s ceiling, so the
  /// two implementations fail the same way and one incident explains both.
  static const batch = RetryPolicy(
    attempts: 4,
    baseDelay: Duration(seconds: 30),
    timeout: Duration(seconds: 90),
  );
}

/// Honors a `Retry-After` header when one is sent, ignoring the HTTP-date
/// form and anything implausibly long.
///
/// A provider asking us to wait ten minutes is better handled by failing and
/// letting the next scheduled attempt pick it up than by blocking — doubly
/// so on a phone, where a long sleep inside a background task will simply be
/// killed by the OS.
///
/// [max] is the policy's own longest sleep, and the rule is: never wait
/// longer on a provider's say-so than we would wait on our own guess. The
/// ceiling used to be a flat 60s, which was fine while the schedule topped
/// out at 20s and wrong the moment it reached 120s — at that point a 90s
/// Retry-After, which is an authoritative instruction, would be discarded in
/// favour of sleeping 120s anyway.
Duration? retryAfter(Map<String, String> headers, Duration max) {
  final raw = headers['retry-after'] ?? headers['Retry-After'];
  if (raw == null) return null;
  final seconds = double.tryParse(raw);
  if (seconds == null || seconds <= 0) return null;
  final asked = Duration(milliseconds: (seconds * 1000).round());
  return asked > max ? null : asked;
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
  /// Attempts, backoff and per-request timeout as one decision. See
  /// [RetryPolicy] for why the app and the scheduled run want different
  /// answers. Tests pass a policy with `baseDelay: Duration.zero` rather
  /// than sleeping through the real one.
  RetryPolicy policy = RetryPolicy.interactive,

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
  for (var attempt = 1; attempt <= policy.attempts; attempt++) {
    Duration delay = policy.baseDelay * (1 << (attempt - 1));
    if (beforeAttempt != null) await beforeAttempt();
    try {
      final resp = await client
          .post(uri, headers: headers, body: jsonEncode(payload))
          .timeout(policy.timeout);
      if (!retryableStatusCodes.contains(resp.statusCode)) return resp;
      lastError = '$label returned HTTP ${resp.statusCode}';
      delay = retryAfter(resp.headers, policy.retryAfterMax) ?? delay;
    } catch (e) {
      lastError = e;
    }
    if (attempt < policy.attempts) await Future<void>.delayed(delay);
  }
  throw LlmResponseError(
      '$label request failed after ${policy.attempts} attempts: $lastError');
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
