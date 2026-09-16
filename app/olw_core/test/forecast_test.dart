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
  _StubProvider([this._payload]);

  final Map<String, Object?>? _payload;

  String? seenSystemPrompt;
  String? seenUserPrompt;

  /// Every (system, user) pair this stub was sent, in order. A forecast is
  /// two calls since upstream ROADMAP item 59 step 3, and the fields above
  /// keep naming the LAST of them so existing assertions still read the
  /// narrative call — the one that writes what a reader sees.
  final List<(String, String)> calls = [];

  /// Which half of the split to fail, for the degraded-write-up path.
  bool failJudgment = false;
  bool failNarrative = false;

  @override
  String get model => 'stub-model';

  @override
  Future<T> generate<T>({
    required String systemPrompt,
    required String userPrompt,
    required ResponseShape<T> shape,
  }) async {
    seenSystemPrompt = systemPrompt;
    seenUserPrompt = userPrompt;
    calls.add((systemPrompt, userPrompt));
    if (failJudgment && shape.name == 'judgment') {
      throw LlmResponseError('request failed after 4 attempts');
    }
    if (failNarrative && shape.name == 'narrative') {
      throw LlmResponseError('request failed after 4 attempts');
    }
    // The shape that was asked for. The canned payload is a whole forecast,
    // a superset of both halves, so each call gets exactly the fields its
    // own schema declares — which is what a real provider does.
    return shape.fromJson(_payload ?? _llmPayload);
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


  /// A client whose hourly series spans the wind-shift anchors (03:00, 12:00,
  /// 18:00) and carries a direction at each.
  ///
  /// The shared `_hourlyBody` holds three hours — 12:00 to 14:00 — and no
  /// direction at all, so `describeWindShift` could only ever return null
  /// against it: one anchor, and nothing to read. That is exactly why the app
  /// never publishing a wind shift went unnoticed, so the fixture is part of
  /// the defect and not merely absent.
  ///
  /// Bearings turn NE -> SW -> S through the day, the Lake Victoria pattern
  /// upstream item 59 measured, and every model agrees so the consensus gate
  /// opens.
  OpenMeteoClient _windClient() {
    Map<String, Object?> body() {
      const hours = [0, 3, 6, 9, 12, 15, 18, 21];
      const bearings = [30.0, 40.0, 60.0, 120.0, 210.0, 200.0, 180.0, 20.0];
      return {
        'utc_offset_seconds': 10800,
        'hourly': {
          'time': [
            for (final h in hours)
              '2026-08-19T${h.toString().padLeft(2, '0')}:00'
          ],
          for (final m in defaultModels) ...{
            'precipitation_$m': List<double>.filled(hours.length, 0.2),
            'wind_gusts_10m_$m': List<double>.filled(hours.length, 20.0),
            'temperature_2m_$m': List<double>.filled(hours.length, 24.0),
            'pressure_msl_$m': List<double>.filled(hours.length, 1012.0),
            'wind_direction_10m_$m': bearings,
          },
        }
      };
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
        return http.Response(jsonEncode(body()), 200);
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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

    // today_properties is a call about TODAY, so it never produces an
    // extended row — an unscoreable placeholder in the record. A blend row at
    // Day+3 comes from extended_properties instead, and this payload commits
    // nothing there, so declining to call is the correct answer and the
    // record carries no row. See the extended_properties test below.
    expect(run.day3Predictions.map((p) => p.model), isNot(contains(blendModelId)));
    expect(run.day7Predictions.map((p) => p.model), isNot(contains(blendModelId)));
    expect(gfs.windKmh, 28.0);
    expect(gfs.highC, 27.5);
  });

  // ROADMAP items 59 and 72. Dart had no mirror of
  // `_extended_blend_predictions`, so the app recorded no forecaster call at
  // Day+3 or Day+7 — the leads where reconciling disagreeing models is worth
  // the most, which is the whole argument for asking at all.
  //
  // The builder is vector-locked. This covers the WIRING, which a vector
  // cannot reach: with the wiring removed and the builder intact, all 158
  // tests stayed green.
  test('the forecaster joins Day+3 and Day+7 when it commits to them', () async {
    final payload = Map<String, Object?>.from(_llmPayload)
      ..['extended_properties'] = [
        {'lead_time_days': 3, 'rain': true, 'rain_probability_pct': 70},
        {'lead_time_days': 7, 'rain': false, 'rain_probability_pct': 0},
      ];
    final run = await generateForecast(
      client: mockClient(),
      llm: _StubProvider(payload),
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
    );

    final d3 = run.day3Predictions.singleWhere((p) => p.model == blendModelId);
    expect(d3.rain, isTrue);
    expect(d3.rainProbabilityPct, 70);
    // Rain only: a field the forecaster was not asked to commit to must not
    // enter the record as a value it never gave.
    expect(d3.highC, isNull);
    expect(d3.precipMm, isNull);

    // A committed zero is a call, not a refusal. `0` is falsy in both
    // languages, and the record cannot tell the two apart afterwards.
    final d7 = run.day7Predictions.singleWhere((p) => p.model == blendModelId);
    expect(d7.rain, isFalse);
    expect(d7.rainProbabilityPct, 0);
  });

  // ROADMAP item 61's app half. The clause was shipped upstream on
  // 2026-09-05 and vector-locked on both sides, and this side still handed
  // the prompt nothing — so an app-generated forecast silently omitted it and
  // no test noticed, because every test that existed checked the FUNCTION
  // rather than the wiring.
  test('the Overview is given something to say about the next three days',
      () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
    );

    expect(llm.seenUserPrompt, contains('NEXT THREE DAYS'));
    expect(
      llm.seenUserPrompt,
      isNot(contains('Unavailable — omit the extended clause.')),
      reason: 'daily guidance is present, so a trend must have been computed',
    );
    // The day name is the end of the span, not today: 2026-08-19 is a
    // Wednesday, so Day+3 is the Saturday. A name computed from the device
    // clock or off by one lands on a different word and this is where that
    // shows.
    expect(llm.seenUserPrompt, contains('through Saturday'));

    // WIND HAS TO REACH IT TOO. The clause takes wind at these leads and the
    // app is a separate caller from the pipeline — exactly the shape of gap
    // item 88 exists for, where a fix lands in Python and Dart silently keeps
    // the old behaviour because no vector covers the wiring.
    //
    // "temperatures much the same" is what the clause says when it was given
    // no wind, so seeing it here means the app dropped the argument.
    expect(
      llm.seenUserPrompt,
      isNot(contains('temperatures much the same')),
      reason: 'wind was not passed to describeExtendedTrend by the app path',
    );
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
        // Not what these tests exercise; passed explicitly because the
        // parameter is required, which is upstream item 104's rule — an
        // unwired block must fail to compile rather than read as absence.
        gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
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

  test('a run whose verification is already written is told so, and shown every earlier narrative', () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      earlierToday: const [
        {'time': '06:07', 'narrative': 'Warm and dry through the morning.'},
        {'time': '13:02', 'narrative': 'Cloud building over the lake.'},
      ],
    );
    expect(llm.seenSystemPrompt, contains('VERIFICATION IS ALREADY WRITTEN'));
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      nowLocal: DateTime(2026, 8, 19, 18, 15),
    );

    expect(llm.seenUserPrompt, contains('ISSUED:'));
    expect(llm.seenUserPrompt, isNot(contains('Time of day unavailable')),
        reason: 'the sun fetch must actually be mocked, not silently failing');
    expect(llm.seenUserPrompt, contains('Sunset is in 32 minutes'));
    expect(llm.seenUserPrompt, contains('WHAT MATTERS NOW: tonight'));
    expect(llm.seenUserPrompt, contains('HOURS AHEAD'));
  });

  test('every pre-computed block the prompt locks actually reaches it', () async {
    // THE TEST THAT WAS MISSING THREE TIMES — upstream ROADMAP item 104.
    //
    // These blocks are optional arguments defaulting to null, and the prompt
    // renders null as "Unavailable". So a block nobody wired reads exactly
    // like a block with nothing to say, and no assertion anywhere noticed: the
    // server's evening refresh omitted three of them for weeks, and this app
    // omitted wind direction and wind shift from every forecast it has ever
    // issued, while describeWindShift sat here ported, exported and
    // vector-tested with no caller.
    //
    // Asserting the ABSENCE of the fallback text is the point. Asserting the
    // header is present proves nothing — the header is there either way.
    final llm = _StubProvider();
    await generateForecast(
      client: _windClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      // Midday, so the wind clause has anchors still ahead of it and item
      // 118 does not legitimately withhold it. An evening hour would make
      // this test pass for the wrong reason.
      nowLocal: DateTime(2026, 8, 19, 11, 0),
    );

    expect(llm.seenUserPrompt, contains('FORECAST WINDOWS'));
    expect(llm.seenUserPrompt,
        isNot(contains('the periods could not be placed on the clock')),
        reason: 'the windows block was not wired into this path');

    expect(llm.seenUserPrompt, contains('WIND SHIFT'));
    expect(llm.seenUserPrompt,
        isNot(contains('omit any claim about the wind turning')),
        reason: 'the wind shift was not wired into this path');

    expect(llm.seenUserPrompt, contains('NEXT THREE DAYS'));
    expect(llm.seenUserPrompt, isNot(contains('omit the extended clause')),
        reason: 'the extended trend was not wired into this path');

    expect(llm.seenUserPrompt, contains('CALENDAR'));
    expect(llm.seenUserPrompt, isNot(contains('name no weekday and no date')),
        reason: 'the calendar was not wired into this path');
    // The pairing itself, not just the block: 2026-08-19 is a Wednesday, and
    // a port a day out would render the block and still be wrong.
    expect(llm.seenUserPrompt, contains('"day_name": "Wednesday"'));
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      nowLocal: DateTime.utc(2026, 8, 19, 15),
    );

    expect(llm.seenUserPrompt, contains('GUIDANCE RECENCY'));
    expect(llm.seenUserPrompt, contains('"models_last_aligned_at": "2026-08-19T06:00:00+00:00"'));
    expect(llm.seenUserPrompt, contains('"hours_old": 9.0'));
    expect(llm.seenUserPrompt, contains('"source": "derived"'));
    expect(llm.seenUserPrompt, contains('"newer_than_previous_issuance": null'));
  });

  test('a forecast is a judgment call and then a rendering call', () async {
    // Upstream ROADMAP item 59 step 3. The order is not a detail: the
    // renderer is HANDED the judgment's answer, so a run that called them the
    // other way round would be rendering a call that had not been made.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      nowLocal: DateTime(2026, 8, 19, 18, 15),
    );

    expect(llm.calls, hasLength(2), reason: 'two calls, not one');

    final (judgmentSystem, judgmentUser) = llm.calls[0];
    final (narrativeSystem, narrativeUser) = llm.calls[1];

    // Each call gets its OWN instructions, and neither gets the other's.
    expect(judgmentSystem, contains('today_properties FIELDS, ALL OF THEM'));
    expect(judgmentSystem, isNot(contains('STEP 2:')));
    expect(narrativeSystem, contains('STEP 2:'));
    expect(narrativeSystem, isNot(contains('today_properties FIELDS, ALL OF THEM')));

    // The renderer is handed the call, on top of everything the judgment saw.
    expect(narrativeUser, contains(judgmentUser),
        reason: 'the renderer still needs the raw data for the Discussion');
    expect(narrativeUser, contains("THE FORECASTER'S CALL"));
    expect(narrativeUser, contains('"temp_high_c": 27.5'),
        reason: "the judgment call's own number, handed to the renderer");
    expect(judgmentUser, isNot(contains("THE FORECASTER'S CALL")),
        reason: 'the judgment call cannot be shown its own answer');
  });

  test('a failed write-up still keeps the scored call', () async {
    // Upstream ROADMAP item 59 step 3, and the cost the split introduced. The
    // judgment call decides the numbers the record SCORES; the rendering call
    // only writes them up. Losing the second used to lose the first too.
    final llm = _StubProvider()..failNarrative = true;

    final run = await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      nowLocal: DateTime(2026, 8, 19, 18, 15),
    );

    // The scored call survived, and so did the row the record verifies.
    expect(run.response.todayProperties.tempHighC, 27.5);
    expect(run.day0Predictions.where((p) => p.model == 'olw_blend'), hasLength(1));

    // And the run says it is degraded rather than normal.
    expect(run.degradations.map((d) => d.code), contains(degradationNarrative));

    // The prose says what happened rather than pretending to be a forecast.
    expect(run.response.todayNarrative.toLowerCase(),
        contains('could not be written'));
  });

  test('a failed judgment call still aborts the whole run', () async {
    // One-sided on purpose: prose around numbers that were never decided is
    // not a degraded forecast, it is an invented one.
    final llm = _StubProvider()..failJudgment = true;

    expect(
      () => generateForecast(
        client: mockClient(),
        llm: llm,
        location: _location,
        today: DateTime.utc(2026, 8, 19),
        publicWebpageUrl: 'https://example.com/',
        // Not what these tests exercise; passed explicitly because the
        // parameter is required, which is upstream item 104's rule — an
        // unwired block must fail to compile rather than read as absence.
        gustBias: null,
        nowLocal: DateTime(2026, 8, 19, 18, 15),
      ),
      throwsA(isA<LlmResponseError>()),
    );
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
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      nowLocal: DateTime.utc(2026, 8, 11, 6, 15),
    );

    expect(llm.seenUserPrompt, contains('"hours_old": 12.2'));
    expect(llm.seenUserPrompt, isNot(contains('"hours_old": 12.3')));
  });

  test('a station reading supplied by the caller reaches the prompt', () async {
    // Upstream ROADMAP item 121. This library has no station fetch and the
    // standalone app has no station source, so the block would be untestable
    // on this side if generateForecast baked the absence in. It takes the
    // record instead, which is how a harness mirrors what OLW composes — the
    // values below are the ones the Python side's own live check produced.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
      observedSoFar: const ObservedSoFar(
        precipitation: true,
        precipitationOnset: '13:00',
        thunder: true,
        highC: 27.4,
        lowC: 18.1,
        peakWindKmh: 31.4,
        cloudOktas: 5.5,
      ),
    );

    expect(
      llm.seenUserPrompt,
      contains('rain from 13:00; thunder; high so far 27°C / 81°F; '
          'low so far 18°C / 65°F; peak gust 31 km/h; sky 6/8.'),
    );
    // Stamped with THIS issuance's clock, not the device's — the block and
    // the ISSUED line have to agree about when "so far" ended.
    expect(llm.seenUserPrompt, contains('OBSERVED SO FAR TODAY'));
    expect(llm.seenUserPrompt, isNot(contains('the station reported nothing measurable')));
  });

  test('no station reading prints the gap rather than a quiet day', () async {
    // The distinction the whole record is built on, at the prompt boundary: a
    // deployment with no station has not observed a calm day.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      // Not what these tests exercise; passed explicitly because the
      // parameter is required, which is upstream item 104's rule — an
      // unwired block must fail to compile rather than read as absence.
      gustBias: null,
    );

    expect(llm.seenUserPrompt, contains('the station reported nothing measurable today'));
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

  test('the gust bias reaches the prompt applied to THIS run\'s models',
      () async {
    // Upstream item 126. The map, not a finished number, because the number
    // can only be computed once today's models have been read — and on a
    // day's first run there is no earlier extraction to base one on, which is
    // exactly when a scheduled forecast goes out.
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      gustBias: const {
        'ecmwf_ifs025': 12.96,
        'gfs_seamless': 16.47,
        'icon_seamless': 12.76,
        'ukmo_seamless': 13.05,
        'best_match': 2.90,
      },
    );

    expect(llm.seenUserPrompt, contains('CALIBRATED PEAK GUST'));
    expect(llm.seenUserPrompt, isNot(contains('Unavailable - too few verified')));
  });

  test('no measured bias renders the gap rather than a quiet correction',
      () async {
    final llm = _StubProvider();
    await generateForecast(
      client: mockClient(),
      llm: llm,
      location: _location,
      today: DateTime.utc(2026, 8, 19),
      publicWebpageUrl: 'https://example.com/',
      gustBias: null,
    );

    expect(llm.seenUserPrompt, contains('Unavailable - too few verified'));
  });
}
