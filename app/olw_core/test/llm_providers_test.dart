// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Provider tests against a mocked HTTP client — the Dart counterparts of
/// tests/test_gemini_provider.py, test_anthropic_provider.py and
/// test_openai_provider.py.
///
/// The emphasis is on REQUEST shape and on every failure path throwing
/// rather than degrading. A provider that quietly returns a partial forecast
/// is worse than one that fails, because the partial one gets published.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

const validPayload = {
  'yesterday_verification': 'Rain call was accurate.',
  'verification_notes': [
    {'lead_time_days': 0, 'note': 'Spot on.'}
  ],
  'skill_profile_summaries': [
    {'model': 'gfs_seamless', 'lead_time_days': 0, 'summary': 'Reliable.'}
  ],
  'today_properties': {
    'rain_expected': 'Likely',
    'temp_high_c': 26.0,
    'temp_low_c': 18.0,
        'rain': true,
    'temp_high_low': '26°C / 79°F',
  },
  'today_narrative': '## Overview\nRain expected.',
  'whatsapp_summary': null,
};

class _Cap {
  Uri? uri;
  Map<String, String> headers = {};
  Map<String, Object?> body = {};
  late final http.Client client;

  _Cap(String responseBody, {int status = 200, Map<String, String>? respHeaders}) {
    client = MockClient((req) async {
      uri = req.url;
      headers = req.headers;
      body = jsonDecode(req.body) as Map<String, Object?>;
      return http.Response(responseBody, status,
          headers: respHeaders ?? const {}, request: req);
    });
  }
}

String geminiEnvelope(Object payload) => jsonEncode({
      'candidates': [
        {
          'content': {
            'parts': [
              {'text': jsonEncode(payload)}
            ]
          }
        }
      ]
    });

String anthropicEnvelope(Object input, {String stop = 'tool_use'}) => jsonEncode({
      'type': 'message',
      'stop_reason': stop,
      'content': [
        {'type': 'tool_use', 'name': 'structured_response', 'input': input}
      ],
    });

String openAiEnvelope(Object payload) => jsonEncode({
      'choices': [
        {
          'message': {'content': jsonEncode(payload)},
          'finish_reason': 'stop'
        }
      ]
    });

void main() {
  group('GeminiProvider', () {
    test('sends the Gemini schema dialect and returns a validated forecast', () async {
      final cap = _Cap(geminiEnvelope(validPayload));
      final got = await GeminiProvider(
        apiKey: 'k',
        model: 'gemini-test',
        client: cap.client,
      ).generate(systemPrompt: 'SYS', userPrompt: 'USR');

      expect(got.todayProperties.tempHighC, 26.0);
      expect(cap.uri!.queryParameters['key'], 'k');
      final gc = cap.body['generationConfig'] as Map;
      expect(gc['responseMimeType'], 'application/json');
      expect((gc['responseSchema'] as Map)['type'], 'OBJECT',
          reason: 'Gemini wants its own uppercase dialect');
      // System prompt is its own top-level field for Gemini.
      expect(((cap.body['system_instruction'] as Map)['parts'] as List).first,
          {'text': 'SYS'});
    });

    test('thinkingLevel is nested camelCase, and omitted when unset', () async {
      var cap = _Cap(geminiEnvelope(validPayload));
      await GeminiProvider(apiKey: 'k', model: 'm', client: cap.client)
          .generate(systemPrompt: 's', userPrompt: 'u');
      expect((cap.body['generationConfig'] as Map).containsKey('thinkingConfig'), isFalse);

      cap = _Cap(geminiEnvelope(validPayload));
      await GeminiProvider(
              apiKey: 'k', model: 'm', thinkingLevel: 'high', client: cap.client)
          .generate(systemPrompt: 's', userPrompt: 'u');
      expect((cap.body['generationConfig'] as Map)['thinkingConfig'],
          {'thinkingLevel': 'high'});
    });

    test('rejects an invalid thinking level', () {
      expect(() => GeminiProvider(apiKey: 'k', model: 'm', thinkingLevel: 'turbo'),
          throwsArgumentError);
    });

    test('no candidates throws', () {
      final cap = _Cap(jsonEncode({'candidates': []}));
      expect(
        () => GeminiProvider(apiKey: 'k', model: 'm', client: cap.client)
            .generate(systemPrompt: 's', userPrompt: 'u'),
        throwsA(isA<LlmResponseError>()),
      );
    });
  });

  group('AnthropicProvider', () {
    test('uses x-api-key, a top-level system field, and forced tool use', () async {
      final cap = _Cap(anthropicEnvelope(validPayload));
      final got = await AnthropicProvider(
        apiKey: 'ant',
        model: 'claude-test',
        client: cap.client,
      ).generate(systemPrompt: 'SYS', userPrompt: 'USR');

      expect(got.todayProperties.rainExpected, 'Likely');
      expect(cap.headers['x-api-key'], 'ant');
      expect(cap.headers['anthropic-version'], isNotNull);
      expect(cap.headers.containsKey('Authorization'), isFalse);
      expect(cap.body['system'], 'SYS');
      expect(cap.body['messages'], [
        {'role': 'user', 'content': 'USR'}
      ]);
      expect(cap.body['tool_choice'],
          {'type': 'tool', 'name': 'structured_response'});
      expect(cap.body['max_tokens'], isNotNull,
          reason: 'max_tokens is required by the API');
    });

    test('truncation names max_tokens explicitly', () {
      final cap = _Cap(jsonEncode({
        'type': 'message',
        'stop_reason': 'max_tokens',
        'content': [
          {'type': 'text', 'text': 'partial...'}
        ]
      }));
      expect(
        () => AnthropicProvider(apiKey: 'k', model: 'm', client: cap.client)
            .generate(systemPrompt: 's', userPrompt: 'u'),
        throwsA(isA<LlmResponseError>().having(
            (e) => e.message, 'message', contains('truncated at max_tokens'))),
      );
    });

    test('prose-only reply throws rather than returning nothing usable', () {
      final cap = _Cap(jsonEncode({
        'type': 'message',
        'stop_reason': 'end_turn',
        'content': [
          {'type': 'text', 'text': 'I think it will rain.'}
        ]
      }));
      expect(
        () => AnthropicProvider(apiKey: 'k', model: 'm', client: cap.client)
            .generate(systemPrompt: 's', userPrompt: 'u'),
        throwsA(isA<LlmResponseError>()),
      );
    });
  });

  group('OpenAiCompatProvider', () {
    test('sends strict json_schema and a Bearer token', () async {
      final cap = _Cap(openAiEnvelope(validPayload));
      final got = await OpenAiCompatProvider(
        apiKey: 'sk-x',
        model: 'gpt-test',
        baseUrl: 'https://api.example.test/v1',
        client: cap.client,
      ).generate(systemPrompt: 'SYS', userPrompt: 'USR');

      expect(got.todayNarrative, contains('Overview'));
      expect(cap.uri.toString(), 'https://api.example.test/v1/chat/completions');
      expect(cap.headers['Authorization'], 'Bearer sk-x');
      final rf = cap.body['response_format'] as Map;
      expect(rf['type'], 'json_schema');
      expect((rf['json_schema'] as Map)['strict'], isTrue);
      expect(((rf['json_schema'] as Map)['schema'] as Map)['additionalProperties'],
          isFalse);
    });

    test('keyless local runtime sends no Authorization header', () async {
      final cap = _Cap(openAiEnvelope(validPayload));
      await OpenAiCompatProvider(
        apiKey: '',
        model: 'llama',
        baseUrl: 'http://localhost:11434/v1',
        client: cap.client,
      ).generate(systemPrompt: 's', userPrompt: 'u');
      expect(cap.headers.containsKey('Authorization'), isFalse);
    });

    test('json_object mode injects the schema into the prompt instead', () async {
      final cap = _Cap(openAiEnvelope(validPayload));
      await OpenAiCompatProvider(
        apiKey: 'k',
        model: 'm',
        baseUrl: 'https://x.test/v1',
        jsonMode: 'json_object',
        client: cap.client,
      ).generate(systemPrompt: 'SYS', userPrompt: 'u');

      expect(cap.body['response_format'], {'type': 'json_object'});
      final system = (cap.body['messages'] as List).first['content'] as String;
      expect(system, startsWith('SYS'));
      expect(system, contains('today_narrative'));
    });

    test('strips a markdown code fence some models add anyway', () async {
      final fenced = '```json\n${jsonEncode(validPayload)}\n```';
      final cap = _Cap(jsonEncode({
        'choices': [
          {
            'message': {'content': fenced},
            'finish_reason': 'stop'
          }
        ]
      }));
      final got = await OpenAiCompatProvider(
        apiKey: 'k',
        model: 'm',
        baseUrl: 'https://x.test/v1',
        jsonMode: 'json_object',
        client: cap.client,
      ).generate(systemPrompt: 's', userPrompt: 'u');
      expect(got.todayProperties.rainExpected, 'Likely');
    });

    test('schema validation failure throws rather than yielding a partial', () {
      final cap = _Cap(openAiEnvelope({'yesterday_verification': 'only this'}));
      expect(
        () => OpenAiCompatProvider(
          apiKey: 'k',
          model: 'm',
          baseUrl: 'https://x.test/v1',
          client: cap.client,
        ).generate(systemPrompt: 's', userPrompt: 'u'),
        throwsA(isA<LlmResponseError>()),
      );
    });
  });

  group('shared retry behaviour', () {
    test('retries a transient status then succeeds', () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        if (calls == 1) return http.Response('{}', 503);
        return http.Response(openAiEnvelope(validPayload), 200);
      });
      final got = await OpenAiCompatProvider(
        apiKey: 'k',
        model: 'm',
        baseUrl: 'https://x.test/v1',
        client: client,
        retryDelay: Duration.zero,
      ).generate(systemPrompt: 's', userPrompt: 'u');
      expect(got.todayProperties.tempLowC, 18.0);
      expect(calls, 2);
    });

    test('does not retry a non-retryable status', () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        return http.Response(jsonEncode({'error': {'message': 'unknown model'}}), 400);
      });
      await expectLater(
        OpenAiCompatProvider(
          apiKey: 'k',
          model: 'm',
          baseUrl: 'https://x.test/v1',
          client: client,
          retryDelay: Duration.zero,
        ).generate(systemPrompt: 's', userPrompt: 'u'),
        throwsA(isA<LlmResponseError>()),
      );
      expect(calls, 1, reason: 'retrying a bad request only burns quota');
    });
  });

  group('retryAfter helper', () {
    test('honors a plausible value', () {
      expect(retryAfter({'retry-after': '3'}), const Duration(seconds: 3));
    });
    test('ignores an implausibly long wait', () {
      // Better to fail and let the next scheduled attempt pick it up than to
      // block — especially inside a mobile background task the OS will kill.
      expect(retryAfter({'retry-after': '600'}), isNull);
    });
    test('ignores the HTTP-date form', () {
      expect(retryAfter({'retry-after': 'Wed, 21 Oct 2026 07:28:00 GMT'}), isNull);
    });
  });

  group('TodayProperties serialisation', () {
    test('round-trips through the pipeline wire keys', () {
      // Snake_case keys, not Dart names: these are the committed JSON shape,
      // so a client inventing its own would produce records the server could
      // not import.
      const original = TodayProperties(
        rain: false,
        rainExpected: 'Yes — showers likely',
        onsetWindow: '13:00-16:00',
        peakWindKmh: 28.4,
        tempHighC: 27.5,
        tempLowC: 18.0,
        mslpTrend24h: 'Falling slowly',
        synopticPattern: 'Weak easterly flow',
        uvIndexMax: '9 (Very High)',
        airQualityAqi: '42 (Good)',
      );
      final json = original.toJson();
      expect(json['rain_expected'], original.rainExpected);
      expect(json['temp_high_c'], 27.5);
      expect(json['peak_wind_kmh'], 28.4);

      final back = TodayProperties.fromJson(json);
      expect(back.rainExpected, original.rainExpected);
      expect(back.onsetWindow, original.onsetWindow);
      expect(back.tempLowC, original.tempLowC);
      expect(back.airQualityAqi, original.airQualityAqi);
    });

    test('nullable fields survive being absent', () {
      // A model that omits optional fields must not produce a record that
      // fails to read back.
      const sparse = TodayProperties(
        rain: false,
        rainExpected: 'No',
        tempHighC: 26.0,
        tempLowC: 18.0,
      );
      final back = TodayProperties.fromJson(sparse.toJson());
      expect(back.onsetWindow, isNull);
      expect(back.peakWindKmh, isNull);
      expect(back.synopticPattern, isNull);
    });
  });
}
