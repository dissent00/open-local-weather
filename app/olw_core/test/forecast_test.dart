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
        // Sunrise/sunset: Kisumu's real figures for the fixture date. Sunset
        // at 18:47 is the number that started the time-awareness work — the
        // "evening" run fires 32 minutes BEFORE it.
        if (request.url.queryParameters['daily'] ==
            'sunrise,sunset,daylight_duration') {
          return http.Response(
            jsonEncode({
              'utc_offset_seconds': 10800,
              'daily': {
                'time': ['2026-08-19', '2026-08-20'],
                'sunrise': ['2026-08-19T06:40', '2026-08-20T06:40'],
                'sunset': ['2026-08-19T18:47', '2026-08-20T18:46'],
              },
            }),
            200,
            headers: {'content-type': 'application/json; charset=utf-8'},
          );
        }
        // The synoptic ring is a multi-coordinate request: an ARRAY.
        if (request.url.queryParameters['daily'] == 'pressure_msl_mean') {
          return http.Response(
            jsonEncode([
              for (var i = 0; i < 9; i++)
                {
                  'latitude': -0.1 + i,
                  'longitude': 34.8 + i,
                  'daily': {
                    'time': ['2026-08-19', '2026-08-20', '2026-08-21'],
                    'pressure_msl_mean': [1016.0 - i, 1015.0 - i, 1014.0 - i],
                  },
                }
            ]),
            200,
          );
        }
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

  test('the synoptic ring reaches the prompt as derived labels', () async {
    // Not raw arrays: the prompt tells the model to use the labels and
    // statements AS GIVEN, precisely so it does not work out which quadrant
    // is lowest by eye — the arithmetic-by-eye mistake the day-over-day
    // comparison already had to be rescued from.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );
    expect(llm.seenUserPrompt, contains('synoptic_scale_pressure'));
    expect(llm.seenUserPrompt, contains('gradient_strength'));
    expect(llm.seenUserPrompt, contains('locates a direction, not a centre or a front'));
  });

  test('the convective flag reaches the prompt, decided in code', () async {
    // The Overview is a tight slot and nothing competed for it: a real
    // forecast opened "similar warmth, calmer winds, and dry again" on a day
    // whose afternoon CAPE reached 2600 J/kg on two models.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      forwardHourly: const {
        'hourly': {
          'time': ['2026-08-19T12:00', '2026-08-19T15:00', '2026-08-19T18:00'],
          'cape_gfs_seamless': [50.0, 2400.0, 1900.0],
        }
      },
    );
    expect(llm.seenUserPrompt, contains('CONVECTIVE INSTABILITY'));
    expect(llm.seenUserPrompt, contains('"convective": true'));
    expect(llm.seenUserPrompt, contains('"peak_hour": "15:00"'));
  });

  test('a quiet afternoon does not raise the convective flag', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      forwardHourly: const {
        'hourly': {
          'time': ['2026-08-19T12:00', '2026-08-19T15:00'],
          'cape_gfs_seamless': [50.0, 120.0],
        }
      },
    );
    expect(llm.seenUserPrompt, contains('"convective": false'));
  });

  test('an absent cape series reads as a gap, not a calm afternoon', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      forwardHourly: const {
        'hourly': {
          'time': ['2026-08-19T12:00'],
          'precipitation_gfs_seamless': [0.0],
        }
      },
    );
    expect(llm.seenUserPrompt, contains('no model supplied a CAPE series'));
  });

  test('the last known ground reading reaches the prompt with its age', () async {
    final llm = _StubProvider();
    final readings = [
      GroundAqiReading(
        name: 'Kisumu Airport',
        stationId: 'A1',
        aqi: 63,
        measuredAt: DateTime.utc(2026, 8, 19, 0, 0),
      ),
      GroundAqiReading(
        name: 'Dunga Beach',
        stationId: 'A2',
        aqi: 49,
        measuredAt: DateTime.utc(2026, 8, 19, 0, 0),
      ),
    ];
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      groundAqiLastKnown:
          lastKnownGroundAqi(readings, DateTime.utc(2026, 8, 19, 9, 0))?.toJson(),
    );
    expect(llm.seenUserPrompt, contains('GROUND AQI LAST KNOWN'));
    // Worst station at the newest timestamp, flagged stale, with its age.
    expect(llm.seenUserPrompt, contains('"station_name": "Kisumu Airport"'));
    expect(llm.seenUserPrompt, contains('"stale": true'));
    expect(llm.seenUserPrompt, contains('"stations_reporting": 2'));
  });

  test('a later issuance is told it is one, and shown every earlier narrative', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      earlierToday: const [
        {'time': '06:07', 'narrative': 'Warm and dry through the morning.'},
        {'time': '13:02', 'narrative': 'Cloud building over the lake.'},
      ],
    );
    expect(llm.seenSystemPrompt, contains('LATER ISSUANCE'));
    expect(llm.seenUserPrompt, contains('EARLIER TODAY'));
    // A list, not one narrative: the number of runs a day is the operator's
    // choice, and the third needs to know about the second.
    expect(llm.seenUserPrompt, contains('Issued 06:07'));
    expect(llm.seenUserPrompt, contains('Issued 13:02'));
    expect(llm.seenUserPrompt, contains('Warm and dry through the morning.'));
  });

  test('the run derives its own issuance, and says so in the prompt', () async {
    // Guards against the trap this suite already fell into once: the new
    // fetches are best-effort, so an unmocked endpoint degrades silently and
    // every assertion below would be checking the DEGRADED path while looking
    // like it checks the real one.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      nowLocal: DateTime(2026, 8, 19, 18, 15),
    );

    expect(llm.seenUserPrompt, contains('ISSUED:'));
    expect(llm.seenUserPrompt, isNot(contains('Time of day unavailable')),
        reason: 'the sun fetch must actually be mocked, not silently failing');
    expect(llm.seenUserPrompt, contains('Sunset is in 32 minutes'));
    expect(llm.seenUserPrompt, contains('WHAT MATTERS NOW: tonight'));
    expect(llm.seenUserPrompt, contains('HOURS AHEAD'));
  });
}
