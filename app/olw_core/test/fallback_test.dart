// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The fallback chain — upstream ROADMAP item 81, the Dart counterpart of
/// tests/test_fallback.py.
///
/// What these pin is the one judgement the chain makes: whether the failure in
/// front of it is a vendor that is down or a model that answered badly.
/// Getting it wrong either way is worse than having no chain. Falling back on
/// a schema failure pays twice to be told the same thing by a second model;
/// not falling back on a 503 leaves the feature doing nothing on exactly the
/// days it was built for.
library;

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// The production attempt count with the sleeping taken out — the same shape
/// llm_providers_test.dart uses, and for the same reason: waiting three real
/// seconds to prove a retry happened proves nothing.
final instantPolicy = RetryPolicy(
  attempts: RetryPolicy.interactive.attempts,
  baseDelay: Duration.zero,
  timeout: RetryPolicy.interactive.timeout,
);

class _Stub implements LlmProvider {
  @override
  final String model;
  final Object? throws;
  final NarrativeResponse? answer;
  int calls = 0;

  _Stub(this.model, {this.throws, this.answer});

  @override
  Future<T> generate<T>({
    required String systemPrompt,
    required String userPrompt,
    required ResponseShape<T> shape,
  }) async {
    calls++;
    if (throws != null) throw throws!;
    return answer as T;
  }
}

final _written = NarrativeResponse(
  yesterdayVerification: 'Rain call was accurate.',
  skillProfileSummaries: const [],
  todayNarrative: '## Overview\nWritten.',
);

void main() {
  test('an unavailable vendor falls through to the next', () async {
    final down = _Stub('gemini-3.6-flash', throws: LlmUnavailableError('HTTP 503'));
    final up = _Stub('nvidia/nemotron', answer: _written);
    final messages = <String>[];

    final got = await FallbackProvider([down, up], onFallback: messages.add)
        .generate(systemPrompt: 's', userPrompt: 'u', shape: narrativeShape);

    expect(got.todayNarrative, contains('Written'));
    expect(down.calls, 1);
    expect(up.calls, 1);
    expect(messages.single, contains('unavailable'));
  });

  test('a bad answer does not fall through', () async {
    // The expensive mistake: a schema failure is our prompt, our schema, or
    // this model's inability to follow them, and the next model is handed
    // exactly the same two.
    final bad = _Stub('gemini-3.6-flash',
        throws: LlmResponseError('response failed schema validation'));
    final spare = _Stub('nvidia/nemotron', answer: _written);

    await expectLater(
      FallbackProvider([bad, spare])
          .generate(systemPrompt: 's', userPrompt: 'u', shape: narrativeShape),
      throwsA(isA<LlmResponseError>()),
    );
    expect(spare.calls, 0, reason: 'a schema failure paid for a second opinion');
  });

  test('the last failure is what reaches the caller, with its type', () async {
    // Its TYPE is what the caller branches on: the judgment call aborts and
    // the narrative call degrades, so wrapping it would change what happens
    // above.
    final first = _Stub('a', throws: LlmUnavailableError('HTTP 503'));
    final second = _Stub('b', throws: LlmUnavailableError('HTTP 500'));

    await expectLater(
      FallbackProvider([first, second])
          .generate(systemPrompt: 's', userPrompt: 'u', shape: narrativeShape),
      throwsA(isA<LlmUnavailableError>()
          .having((e) => e.message, 'message', contains('HTTP 500'))),
    );
  });

  test('the chain reports its first choice as the model', () {
    final chain = FallbackProvider([_Stub('first'), _Stub('second')]);
    expect(chain.model, 'first');
  });

  test('a chain needs a provider', () {
    expect(() => FallbackProvider([]), throwsArgumentError);
  });

  test('a real provider whose retries are spent reports itself unavailable',
      () async {
    // THE MUTATION THAT SURVIVED. Every test above hands the chain a stub that
    // throws LlmUnavailableError, so all of them pass while the code that
    // actually PRODUCES one throws something else — checked 2026-09-21 by
    // changing postWithRetry's exit to LlmResponseError and watching all 203
    // tests stay green. The stubs pin the chain's branching; this pins the
    // thing the branch is supposed to catch, driven through a real provider
    // and a real HTTP client that only ever answers 503.
    var requests = 0;
    final provider = OpenAiCompatProvider(
      apiKey: 'k',
      model: 'nvidia/nemotron-3-super-120b-a12b:free',
      baseUrl: 'https://openrouter.ai/api/v1',
      retryPolicy: instantPolicy,
      client: MockClient((req) async {
        requests++;
        return http.Response('{"error":{"message":"over capacity"}}', 503);
      }),
    );
    final spare = _Stub('spare', answer: _written);

    final got = await FallbackProvider([provider, spare])
        .generate(systemPrompt: 's', userPrompt: 'u', shape: narrativeShape);

    expect(got.todayNarrative, contains('Written'),
        reason: 'a spent retry budget did not fall through');
    expect(requests, instantPolicy.attempts);
  });

  test('a real provider given a bad request does NOT report itself unavailable',
      () async {
    // The other half. A 400 is the key, the model id or the schema being
    // wrong, and every later entry is handed the same one.
    final provider = OpenAiCompatProvider(
      apiKey: 'k',
      model: 'nvidia/nemotron-3-super-120b-a12b:free',
      baseUrl: 'https://openrouter.ai/api/v1',
      retryPolicy: instantPolicy,
      client: MockClient((req) async =>
          http.Response('{"error":{"message":"unknown model"}}', 400)),
    );
    final spare = _Stub('spare', answer: _written);

    await expectLater(
      FallbackProvider([provider, spare])
          .generate(systemPrompt: 's', userPrompt: 'u', shape: narrativeShape),
      throwsA(isA<LlmResponseError>()),
    );
    expect(spare.calls, 0, reason: 'a bad request bought a second opinion');
  });

  test('an unavailable error is still a response error', () {
    // The subclass promise: every existing `on LlmResponseError` keeps
    // catching both, so nothing above the providers changes.
    expect(LlmUnavailableError('x'), isA<LlmResponseError>());
  });
}
