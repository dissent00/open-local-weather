/// Fetch-layer tests using a mocked HTTP client — no network, deterministic,
/// the Dart counterpart to the Python suite's requests-mock tests.
///
/// What these check is the REQUEST shape, not the response parsing: getting
/// `forecast_days` or the per-model `models=` list wrong produces a
/// perfectly valid response for the wrong question, which is the kind of bug
/// that survives all the way into a published forecast.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// Captures the last request URI and returns a canned body.
class _Capture {
  Uri? uri;
  late final http.Client client;

  _Capture({int status = 200, String body = '{"ok": true}'}) {
    client = MockClient((req) async {
      uri = req.url;
      return http.Response(body, status);
    });
  }

  Map<String, String> get params => uri!.queryParameters;
}

void main() {
  group('request shape', () {
    test('hourly today asks for exactly one day and joins the model list', () async {
      final cap = _Capture();
      await OpenMeteoClient(client: cap.client).fetchForecastHourlyToday(
        lat: -0.1,
        lon: 34.75,
        models: ['gfs_seamless', 'ecmwf_ifs025'],
        timezone: 'Africa/Nairobi',
      );

      expect(cap.uri!.origin + cap.uri!.path, equals(forecastUrl));
      expect(cap.params['forecast_days'], '1',
          reason: 'onset timing is only meaningful for today');
      expect(cap.params['models'], 'gfs_seamless,ecmwf_ifs025');
      expect(cap.params['timezone'], 'Africa/Nairobi');
      expect(cap.params['hourly'], contains('precipitation'));
    });

    test('daily extended defaults to 8 days, not 7', () async {
      // Index 0 is today, so 8 days is what makes index 7 genuinely "seven
      // full days out". A default of 7 would silently shift every Day+7
      // prediction by one day.
      final cap = _Capture();
      await OpenMeteoClient(client: cap.client).fetchForecastDailyExtended(
        lat: 1,
        lon: 2,
        models: ['gfs_seamless'],
        timezone: 'UTC',
      );
      expect(cap.params['forecast_days'], '8');
    });

    test('regional pressure sends primary first, then region points, best_match only', () async {
      final cap = _Capture();
      await OpenMeteoClient(client: cap.client).fetchRegionalPressure(
        primaryPoint: const Point(-0.1, 34.75),
        regionPoints: const [Point(0.45, 34.11), Point(-0.53, 34.45)],
        timezone: 'Africa/Nairobi',
      );

      expect(cap.params['latitude'], '-0.1,0.45,-0.53',
          reason: 'primary point must come first so index 0 is the primary');
      expect(cap.params['longitude'], '34.75,34.11,34.45');
      expect(cap.params['models'], 'best_match',
          reason: 'regional sketch, not per-model verification data');
    });

    test('archive range formats dates as YYYY-MM-DD', () async {
      final cap = _Capture();
      await OpenMeteoClient(client: cap.client).fetchArchiveRange(
        lat: 1,
        lon: 2,
        startDate: DateTime.utc(2026, 7, 5),
        endDate: DateTime.utc(2026, 8, 11),
        timezone: 'UTC',
      );
      expect(cap.params['start_date'], '2026-07-05');
      expect(cap.params['end_date'], '2026-08-11');
    });

    test('single day archive collapses to the same start and end date', () async {
      final cap = _Capture();
      await OpenMeteoClient(client: cap.client).fetchArchiveSingleDay(
        lat: 1,
        lon: 2,
        day: DateTime.utc(2026, 8, 11),
        timezone: 'UTC',
      );
      expect(cap.params['start_date'], '2026-08-11');
      expect(cap.params['end_date'], '2026-08-11');
    });

    test('air quality hits the air-quality host, not the forecast host', () async {
      final cap = _Capture();
      await OpenMeteoClient(client: cap.client)
          .fetchAirQuality(lat: 1, lon: 2, timezone: 'UTC');
      expect(cap.uri!.origin + cap.uri!.path, equals(airQualityUrl));
    });
  });

  group('failure handling', () {
    // These must fail LOUDLY. Forward guidance and actuals are not optional
    // inputs; a run without them has nothing to synthesise or score, and
    // degrading gracefully here would mean publishing a forecast built from
    // partial data.
    test('non-200 raises', () async {
      final cap = _Capture(status: 503, body: 'upstream unavailable');
      expect(
        () => OpenMeteoClient(client: cap.client)
            .fetchAirQuality(lat: 1, lon: 2, timezone: 'UTC'),
        throwsA(isA<OpenMeteoFetchError>()),
      );
    });

    test('network failure raises rather than returning empty', () async {
      final client = MockClient((_) async => throw const _Boom());
      expect(
        () => OpenMeteoClient(client: client)
            .fetchAirQuality(lat: 1, lon: 2, timezone: 'UTC'),
        throwsA(isA<OpenMeteoFetchError>()),
      );
    });

    test('successful response is decoded', () async {
      final cap = _Capture(body: jsonEncode({'hourly': {'time': <String>[]}}));
      final got = await OpenMeteoClient(client: cap.client)
          .fetchAirQuality(lat: 1, lon: 2, timezone: 'UTC');
      expect(got.containsKey('hourly'), isTrue);
    });
  });
}

class _Boom implements Exception {
  const _Boom();
}
