// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
//
// Phase 3's acceptance test: one forecast generated end-to-end, entirely
// offline. Every HTTP call — Open-Meteo and the LLM — is mocked, so this
// runs in CI, costs nothing, and cannot flake on a network.
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

const _location = LocationConfig(
  regionName: 'the Lake Victoria Basin',
  primaryPlaceName: 'Kisumu, Kenya',
  timezone: 'Africa/Nairobi',
  lat: -0.0917,
  lon: 34.7680,
  secondaryPoint: SecondaryPoint(enabled: false, name: '', sectionLabel: ''),
);

Map<String, Object?> _hourlyBody(List<String> models) => {
      'hourly': {
        'time': ['2026-08-19T12:00', '2026-08-19T13:00', '2026-08-19T14:00'],
        for (final m in models) ...{
          'precipitation_$m': [0.0, 0.8, 1.2],
          'wind_gusts_10m_$m': [12.0, 28.0, 24.0],
          'temperature_2m_$m': [22.0, 27.5, 26.0],
          'pressure_msl_$m': [1013.0, 1012.2, 1011.6],
        },
      }
    };

Map<String, Object?> _dailyBody(List<String> models) => {
      'daily': {
        'time': [for (var i = 19; i <= 26; i++) '2026-08-$i'],
        for (final m in models) ...{
          'precipitation_sum_$m': List<double>.filled(8, 1.5),
          'wind_gusts_10m_max_$m': List<double>.filled(8, 30.0),
          'temperature_2m_max_$m': List<double>.filled(8, 28.0),
          'temperature_2m_min_$m': List<double>.filled(8, 18.0),
          'pressure_msl_mean_$m': List<double>.filled(8, 1012.0),
        },
      }
    };

final _llmPayload = {
  'yesterday_verification': 'No prior record to verify against.',
  'verification_notes': <Object>[],
  'skill_profile_summaries': <Object>[],
  'today_properties': {
    'rain_expected': 'Yes — showers likely this afternoon',
    'onset_window': '13:00-16:00',
    'temp_high_c': 27.5,
    'temp_low_c': 18.0,
    'temp_high_low': '27.5°C / 81.5°F high, 18.0°C / 64.4°F low',
    'mslp_trend_24h': 'Falling slowly',
    'synoptic_pattern': 'Weak easterly flow over the basin',
    'uv_index_max': '9 (Very High)',
    'air_quality_aqi': '42 (Good)',
  },
  'today_narrative': '## Overview\n\nShowers likely this afternoon.',
  'whatsapp_summary': 'Rain likely this afternoon.',
};

/// A stub provider. The real ones are covered against their own wire formats
/// in llm_providers_test.dart; what matters here is the orchestration.
class _StubProvider implements LlmProvider {
  _StubProvider();

  String? seenSystemPrompt;
  String? seenUserPrompt;

  @override
  String get model => 'stub-model';

  @override
  Future<ForecastResponse> generate({
    required String systemPrompt,
    required String userPrompt,
  }) async {
    seenSystemPrompt = systemPrompt;
    seenUserPrompt = userPrompt;
    return ForecastResponse.fromJson(_llmPayload);
  }
}

void main() {
  late List<String> requestedUrls;

  OpenMeteoClient mockClient({bool failAirQuality = false}) {
    requestedUrls = [];
    return OpenMeteoClient(
      client: MockClient((request) async {
        requestedUrls.add(request.url.toString());
        final path = request.url.path;
        if (path.contains('air-quality')) {
          if (failAirQuality) return http.Response('upstream exploded', 500);
          return http.Response(jsonEncode({'hourly': {'pm2_5': [18.0]}}), 200);
        }
        final isDaily = request.url.queryParameters.containsKey('daily');
        return http.Response(
          jsonEncode(isDaily ? _dailyBody(defaultModels) : _hourlyBody(defaultModels)),
          200,
        );
      }),
    );
  }

  test('generates one forecast end to end', () async {
    final llm = _StubProvider();
    final run = await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );

    // The synthesised forecast came back intact.
    expect(run.response.todayProperties.rainExpected, contains('showers'));
    expect(run.response.todayNarrative, contains('Overview'));

    // Predictions were extracted at every tracked lead time, one per model.
    expect(run.day0Predictions, hasLength(defaultModels.length));
    expect(run.day3Predictions, hasLength(defaultModels.length));
    expect(run.day7Predictions, hasLength(defaultModels.length));

    // And they carry real values, not nulls from a mis-keyed lookup.
    final gfs = run.day0Predictions.firstWhere((p) => p.model == 'gfs_seamless');
    expect(gfs.rain, isTrue);
    expect(gfs.windKmh, 28.0);
    expect(gfs.highC, 27.5);
  });

  test('the model sees the same numbers that will be scored', () async {
    // The property that keeps the narrative and the accuracy record
    // describing one set of numbers rather than two.
    final llm = _StubProvider();
    final run = await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, contains('EXTRACTED PER-MODEL PREDICTIONS'));
    for (final p in run.day0Predictions) {
      expect(llm.seenUserPrompt, contains('"model": "${p.model}"'));
    }
    expect(llm.seenSystemPrompt, contains('You are'));
  });

  test('a first run with no history is a normal run, not an error', () async {
    // Exactly what a new user's first forecast looks like: no verification,
    // no track record, no review. The prompt must say so rather than
    // presenting emptiness as measurement.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, contains('Unavailable — no review computed this run.'));
    expect(
      llm.seenUserPrompt,
      contains('Unavailable — no observed record for yesterday'),
    );
    expect(llm.seenUserPrompt, contains('no ground station reported data today'));
  });

  test('a failed optional fetch costs a section, not the forecast', () async {
    final llm = _StubProvider();
    final run = await generateForecast(
      client: mockClient(failAirQuality: true),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );
    expect(run.response.todayNarrative, isNotEmpty);
    expect(llm.seenUserPrompt, contains('"air_quality": null'));
  });

  test('a failed required fetch aborts rather than forecasting from nothing',
      () async {
    final client = OpenMeteoClient(
      client: MockClient((_) async => http.Response('gone', 503)),
    );
    expect(
      () => generateForecast(
        client: client,
        llm: _StubProvider(),
        location: _location,
        today: DateTime.utc(2026, 8, 19),
        publicWebpageUrl: 'https://example.com/',
      ),
      throwsA(isA<OpenMeteoFetchError>()),
    );
  });

  test('an evening refresh is issued as a refresh', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      morningNarrative: 'Warm and dry through the morning.',
    );
    expect(llm.seenSystemPrompt, contains('REFRESH MODE'));
    expect(llm.seenUserPrompt, contains('MORNING NARRATIVE'));
    expect(llm.seenUserPrompt, contains('Warm and dry through the morning.'));
  });
}
