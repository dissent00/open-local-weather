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
      // The location's offset from UTC, which every Open-Meteo response with a
      // `timezone=` carries. Not decoration: the issuance is derived from it
      // now that the sun is computed rather than fetched, and a body without
      // it degrades to daypartWithoutSun — silently, which is how a mock
      // stops testing the path it looks like it is testing.
      'utc_offset_seconds': 10800,
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
        'rain': true,
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

  /// A client whose day-0 hourly response carries CAPE, and whose FORWARD
  /// response can be made to fail. The two differ only by `forecast_days`,
  /// which is the whole point: the live failure hit one and not the other.
  OpenMeteoClient _capeClient({required bool forwardFails}) {
    Map<String, Object?> withCape() {
      final body = Map<String, Object?>.from(_hourlyBody(defaultModels));
      final hourly = Map<String, Object?>.from(body['hourly'] as Map);
      hourly['cape_ukmo_seamless'] = [40.0, 1830.0, 90.0];
      body['hourly'] = hourly;
      return body;
    }

    return OpenMeteoClient(
      client: MockClient((request) async {
        if (request.url.queryParameters['daily'] == 'pressure_msl_mean') {
          return http.Response(jsonEncode([]), 200);
        }
        if (request.url.path.contains('air-quality')) {
          return http.Response(jsonEncode({'hourly': {'pm2_5': [18.0]}}), 200);
        }
        if (request.url.queryParameters.containsKey('daily')) {
          return http.Response(jsonEncode(_dailyBody(defaultModels)), 200);
        }
        if (forwardFails && request.url.queryParameters['forecast_days'] == '2') {
          return http.Response('read timed out', 504);
        }
        return http.Response(jsonEncode(withCape()), 200);
      }),
    );
  }

  OpenMeteoClient mockClient({bool failAirQuality = false}) {
    requestedUrls = [];
    return OpenMeteoClient(
      client: MockClient((request) async {
        requestedUrls.add(request.url.toString());
        final path = request.url.path;
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
    expect(run.day0Predictions, hasLength(defaultModels.length + 1),
        reason: 'every model, plus our own blended call');
    expect(run.day3Predictions, hasLength(defaultModels.length));
    expect(run.day7Predictions, hasLength(defaultModels.length));

    // And they carry real values, not nulls from a mis-keyed lookup.
    final gfs = run.day0Predictions.firstWhere((p) => p.model == 'gfs_seamless');
    expect(gfs.rain, isTrue);

    // ...and our own blended call sits alongside them, scored as a peer of
    // the guidance that fed it. Built from today_properties' structured
    // fields, so what is verified is what the forecaster committed to.
    final blend = run.day0Predictions.singleWhere((p) => p.model == blendModelId);
    expect(blend.highC, run.response.todayProperties.tempHighC);
    expect(blend.rain, run.response.todayProperties.rain);
    expect(blend.windKmh, isNull,
        reason: 'absent, never zero — peakWindKmh is the secondary point');

    // Day+0 only: today_properties is a call about today, and an extended
    // row for it would be an unscoreable placeholder in the record.
    expect(run.day3Predictions.map((p) => p.model), isNot(contains(blendModelId)));
    expect(run.day7Predictions.map((p) => p.model), isNot(contains(blendModelId)));
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
    for (final p in run.day0Predictions.where((p) => p.model != blendModelId)) {
      expect(llm.seenUserPrompt, contains('"model": "${p.model}"'));
    }

    // The blend is scored and stored, and never shown to the forecaster.
    // Here it cannot be shown — it does not exist until the reply arrives —
    // but the assertion is worth pinning on this side too, because the
    // withholding is a standing rule rather than an accident of ordering.
    expect(llm.seenUserPrompt, isNot(contains(blendModelId)));
    expect(llm.seenSystemPrompt, isNot(contains(blendModelId)));
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
    // No stations are configured here, so the ground AQI blocks are absent
    // rather than reported unavailable — see the dedicated test below.
    expect(llm.seenUserPrompt, isNot(contains('GROUND AQI')));
  });

  test('a deployment with no ground stations is never told about them', () async {
    // The app polls none until someone configures them. Rendering the blocks
    // as "Unavailable — no ground station reported data today" would report a
    // failed fetch for stations that never existed, every single run.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, isNot(contains('GROUND AQI')));
    expect(llm.seenSystemPrompt, isNot(contains('GROUND AQI')));
    expect(llm.seenSystemPrompt, isNot(contains('cross-reference ground sensor data')));
    // It still has to be told where air quality comes from.
    expect(llm.seenSystemPrompt, contains('model (CAMS) data alone'));
  });

  test('no met service configured is a state, not a missing bulletin', () async {
    // 'LOCAL BULLETIN ():' with nothing under it is a fetch that failed, and
    // the prompt went on to demand the service be named EVERY TIME. The
    // absence is stated once instead — the model knows real met services for
    // a real place, so silence would leave it free to attribute a forecast to
    // one it never consulted.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, isNot(contains('LOCAL BULLETIN')));
    expect(llm.seenSystemPrompt, isNot(contains('NAME THE LOCAL MET SERVICE')));
    expect(llm.seenSystemPrompt, isNot(contains('LOCAL MET SERVICE AS A MODEL')));
    expect(llm.seenSystemPrompt, contains('No national met service is configured'));
  });

  test('a named met service is carried and must be named', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      localBulletinSourceName: 'Kenya Meteorological Department (KMD)',
      localBulletinText: 'Sunny intervals, light rains over a few places.',
    );

    expect(
      llm.seenUserPrompt,
      contains('LOCAL BULLETIN (Kenya Meteorological Department (KMD)):'),
    );
    expect(llm.seenUserPrompt, contains('Sunny intervals'));
    expect(llm.seenSystemPrompt, contains('NAME THE LOCAL MET SERVICE EVERY TIME'));
  });

  test('configuring stations brings the blocks and the guidance back', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      groundStationsConfigured: true,
      groundAqiReadings: const [
        {'name': 'Kisumu Airport', 'aqi': 46, 'stale': false},
      ],
    );

    expect(llm.seenUserPrompt, contains('GROUND AQI STATIONS'));
    expect(llm.seenUserPrompt, contains('"aqi": 46'));
    expect(
      llm.seenSystemPrompt,
      contains('Ground AQI stations may occasionally be offline'),
    );
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

  test('a failed forward fetch falls back to the day-0 cape', () async {
    // The 2026-08-29 and 08-30 runs, in miniature. forecast_days=2 read-timed
    // out three runs running while forecast_days=1 — same host, same
    // endpoint, same variable list including cape — succeeded in every one,
    // and the convective outlook was published "unavailable" with the data
    // sitting in memory. A reader was rained on that evening. ROADMAP 53.2.
    final llm = _StubProvider();
    await generateForecast(
      client: _capeClient(forwardFails: true),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      nowLocal: DateTime.utc(2026, 8, 19, 12),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, isNot(contains('no model supplied a CAPE series')));
    expect(llm.seenUserPrompt, contains('"convective": true'));
    expect(llm.seenUserPrompt, contains('"peak_cape_jkg": 1830.0'));
  });

  test('the fallback window says it is only the rest of today', () async {
    // forecast_days=1 stops at 23:00 local. Left unsaid, a series that simply
    // ends reads as a forecast of a quiet night.
    final llm = _StubProvider();
    await generateForecast(
      client: _capeClient(forwardFails: true),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      nowLocal: DateTime.utc(2026, 8, 19, 12),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, contains('REST OF TODAY ONLY'));
    expect(llm.seenUserPrompt, contains('ENDS AT 23:00 local'));
  });

  test('a working forward window is not labelled as narrowed', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: _capeClient(forwardFails: false),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      nowLocal: DateTime.utc(2026, 8, 19, 12),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(llm.seenUserPrompt, isNot(contains('REST OF TODAY ONLY')));
  });

  // ROADMAP item 53.4. The server half records a degraded run in the
  // committed entry and shows it on the page; this side computed the same
  // narrowing and told only the prompt, so an app forecast built on the
  // fallback was indistinguishable from a complete one everywhere the reader
  // or the stored record could see it.
  test('a narrowed run reports the degradation to its caller', () async {
    final run = await generateForecast(
      client: _capeClient(forwardFails: true),
      llm: _StubProvider(),
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      nowLocal: DateTime.utc(2026, 8, 19, 12),
      publicWebpageUrl: 'https://example.com/',
    );

    expect(run.degradations.map((d) => d.code), ['hours_ahead_narrowed']);

    // Plain up top, jargon at the end. The summary must be usable by someone
    // deciding whether to go outside; "forward hourly window" is not.
    final d = run.degradations.single;
    expect(d.summary, contains("Part of tonight's data did not arrive"));
    expect(d.summary, isNot(contains('forward hourly')));
    expect(d.detail, contains('forward hourly window'));

    // And the detail says when waiting would help, in the LOCATION's local
    // time. The fixture is Africa/Nairobi (UTC+3), so the windows that open
    // at 02/08/14/20 UTC land at 05/11/17/23 local.
    expect(d.detail, contains('usually in by about'));
    expect(d.detail, anyOf(contains('05:00'), contains('11:00'),
        contains('17:00'), contains('23:00')));
  });

  test('a complete run reports no degradations', () async {
    final run = await generateForecast(
      client: _capeClient(forwardFails: false),
      llm: _StubProvider(),
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      nowLocal: DateTime.utc(2026, 8, 19, 12),
      publicWebpageUrl: 'https://example.com/',
    );

    // Empty, not null. The distinction the Python side had to learn the hard
    // way: a run that looked and found nothing is not the same answer as a
    // run that was never asked, and this one looked.
    expect(run.degradations, isEmpty);
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
      groundStationsConfigured: true,
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

  test('generateForecast computes the derived guidance recency floor', () async {
    // The app has no metadata fetch (see forecast.dart), so this is always
    // the DERIVED floor from cycle.dart, never OBSERVED, and there is no
    // stored previous issuance here to diff against — see forecast.dart's
    // comment on why newer_than_previous_issuance is always null on this
    // side. now.hour=15 falls in cycle.dart's ">=14" window, aligning to
    // 06:00Z the same day — a plain 9.0-hour age, no rounding tie involved.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      nowLocal: DateTime.utc(2026, 8, 19, 15),
    );

    expect(llm.seenUserPrompt, contains('GUIDANCE RECENCY'));
    expect(llm.seenUserPrompt, contains('"models_last_aligned_at": "2026-08-19T06:00:00+00:00"'));
    expect(llm.seenUserPrompt, contains('"hours_old": 9.0'));
    expect(llm.seenUserPrompt, contains('"source": "derived"'));
    expect(llm.seenUserPrompt, contains('"newer_than_previous_issuance": null'));
  });

  test('guidance recency hours_old rounds half-to-even, matching Python', () async {
    // now=2026-08-11T06:15Z falls in cycle.dart's ">=2" window, aligning to
    // 2026-08-10T18:00Z — age_hours is exactly 12.25, a genuine tie at one
    // decimal place. Measured: Python's round(12.25, 1) is 12.2, not the
    // 12.3 a naive scale-and-round-away-from-zero would give — see
    // _roundHoursOld in forecast.dart.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 11),
      publicWebpageUrl: 'https://example.com/',
      nowLocal: DateTime.utc(2026, 8, 11, 6, 15),
    );

    expect(llm.seenUserPrompt, contains('"hours_old": 12.2'));
    expect(llm.seenUserPrompt, isNot(contains('"hours_old": 12.3')));
  });

  test('the GUIDANCE RECENCY block falls back to Unavailable, ported verbatim', () {
    // buildUserPrompt keeps this fallback for parity with the Python port
    // even though generateForecast itself never triggers it — the app always
    // has a derived floor to state. Mirrors the vector's cold-start case.
    final prompt = buildUserPrompt(
      today: DateTime.utc(2026, 8, 19),
      yesterday: DateTime.utc(2026, 8, 18),
      publicWebpageUrl: 'https://example.com/',
      verificationContext: const <Object>[],
      trackRecordContext: const <Object>[],
      historicalLogs: const <Object>[],
      groundAqiReadings: const <Object>[],
      groundAqiSummary: null,
      yesterdayActual: null,
      todayWeatherData: const <String, Object?>{},
      localBulletinSourceName: '',
      localBulletinText: '',
      guidanceRecency: null,
    );

    expect(
      prompt,
      contains('Unavailable — this run could not establish which model '
          'cycle its guidance came from.'),
    );
  });
}
