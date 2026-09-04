import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// Where the spend cap meets the code that actually sends requests.
///
/// The mirror of `tests/test_spend_seam.py`. The cap's arithmetic is already
/// vector-locked against Python; what those vectors cannot express is a
/// side effect — how many times a hook fires while a retry loop runs. That
/// gap is where a real bug lived: counting once per generate() while the
/// provider sent up to maxAttempts requests, so a cap of 10 permitted 40.
///
/// These tests are about COUNTING, so they need a policy that actually
/// retries — the interactive default stops at two attempts, which would hide
/// the very undercount described above. Real backoff is dropped to zero
/// because sleeping 30 seconds to prove an increment is not a test.
const instantBatch = RetryPolicy(
  attempts: 4,
  baseDelay: Duration.zero,
  timeout: Duration(seconds: 90),
);
///
/// See `spec/README.md`, "Spend cap invariants", for the shared list these
/// assert on both sides.
void main() {
  /// Fails [failures] times with a retryable status, then succeeds.
  http.Client flaky(int failures, {required List<String> log}) {
    var n = 0;
    return MockClient((request) async {
      n++;
      log.add('request $n');
      if (n <= failures) return http.Response('overloaded', 503);
      return http.Response(
        jsonEncode({
          'candidates': [
            {
              'content': {
                'parts': [
                  {
                    // Mirrors forecast_test.dart's payload: the schema has
                    // required fields a trimmed-down stub does not satisfy,
                    // and a validation error here would look like a hook bug.
                    'text': jsonEncode({
                      'yesterday_verification': 'No prior record.',
                      'verification_notes': <Object>[],
                      'skill_profile_summaries': <Object>[],
                      'today_properties': {
                        'rain_expected': 'Yes — showers likely',
                        'onset_window': '13:00-16:00',
                        'temp_high_c': 27.5,
                        'temp_low_c': 18.0,
        'rain': true,
                        'temp_high_low': '27.5°C / 81.5°F high, 18.0°C / 64.4°F low',
                        'mslp_trend_24h': 'Falling slowly',
                        'synoptic_pattern': 'Weak easterly flow',
                        'uv_index_max': '9 (Very High)',
                        'air_quality_aqi': '42 (Good)',
                      },
                      'today_narrative': '## Overview\n\nShowers likely.',
                      'whatsapp_summary': 'Rain likely.',
                    })
                  }
                ]
              }
            }
          ]
        }),
        200,
        // http.Response encodes a String body as latin-1 unless told
        // otherwise, and this payload has ° and — in it.
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    });
  }

  test('every retry fires the hook, not just the generate() call', () async {
    // The exact shape that was undercounted. Retries fire on 429 and 5xx —
    // when a provider is rate-limiting or struggling — so the undercount was
    // worst precisely when spend was most likely to run away.
    final log = <String>[];
    var hookCalls = 0;

    final provider = GeminiProvider(
      apiKey: 'k',
      model: 'm',
      client: flaky(2, log: log),
      retryPolicy: instantBatch,
      beforeAttempt: () async => hookCalls++,
    );

    await provider.generate(systemPrompt: 'sys', userPrompt: 'user');

    expect(log, hasLength(3), reason: 'three requests really were sent');
    expect(hookCalls, 3,
        reason: 'one hook call per request, not one per forecast');
  });

  test('throwing from the hook aborts the retry loop mid-flight', () async {
    // Without this, running out of budget partway would still let the
    // remaining retries go out: the cap would bound how many forecasts start
    // rather than how many requests are sent.
    final log = <String>[];
    var hookCalls = 0;

    final provider = GeminiProvider(
      apiKey: 'k',
      model: 'm',
      client: flaky(99, log: log),
      retryPolicy: instantBatch,
      beforeAttempt: () async {
        hookCalls++;
        if (hookCalls > 2) throw StateError('budget exhausted');
      },
    );

    await expectLater(
      provider.generate(systemPrompt: 'sys', userPrompt: 'user'),
      throwsA(isA<StateError>()),
    );
    expect(log, hasLength(2),
        reason: 'the third request must never leave the process');
  });

  test('the hook runs BEFORE the request, so a crash cannot lose it', () async {
    // Recording afterwards loses the count exactly when things are going
    // wrong. Over-counting refuses a call that would have been allowed — the
    // safe direction; under-counting hands out free calls after a crash.
    final order = <String>[];
    final provider = GeminiProvider(
      apiKey: 'k',
      model: 'm',
      client: MockClient((_) async {
        order.add('request');
        throw const SocketExceptionLike();
      }),
      retryPolicy: instantBatch,
      beforeAttempt: () async => order.add('hook'),
    );

    await expectLater(
      provider.generate(systemPrompt: 'sys', userPrompt: 'user'),
      throwsA(isA<LlmResponseError>()),
    );
    expect(order.first, 'hook');
    expect(order.where((e) => e == 'hook'), hasLength(instantBatch.attempts),
        reason: 'every attempt that left the process was counted first');
  });

  test('a provider with no hook still works', () async {
    // The core is used by the pipeline as well as the app, and only the app
    // has a cap to enforce. An absent hook must not become a required one.
    final log = <String>[];
    final provider = GeminiProvider(
      apiKey: 'k',
      model: 'm',
      client: flaky(0, log: log),
      retryPolicy: instantBatch,
    );
    await provider.generate(systemPrompt: 'sys', userPrompt: 'user');
    expect(log, hasLength(1));
  });
}

/// Stands in for a transport-level failure without importing dart:io, which
/// would make this test unrunnable on web.
class SocketExceptionLike implements Exception {
  const SocketExceptionLike();
}
