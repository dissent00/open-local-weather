// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'dates.dart';
import 'extract.dart' show rainThresholdMm, getOnsetHour;
import 'models.dart';

/// Open-Meteo fetch layer: multi-model forecast, archive (actuals) and air
/// quality.
///
/// Mirrors the Python `fetch/open_meteo.py`, including its deliberate
/// decision NOT to hide this behind a swappable interface: the whole
/// multi-model design depends on Open-Meteo's per-model field-naming
/// convention (`precipitation_gfs_seamless`, ...), so swapping providers
/// would mean rewriting this module and the extraction logic together.
///
/// Failures raise [OpenMeteoFetchError] rather than degrading, for the same
/// reason as the Python version: forward guidance and yesterday's actuals
/// are not optional. A run without them has nothing to synthesise or score
/// and should fail loudly rather than invent a forecast from partial data.

const String forecastUrl = 'https://api.open-meteo.com/v1/forecast';
const String archiveUrl = 'https://archive-api.open-meteo.com/v1/archive';
const String airQualityUrl =
    'https://air-quality-api.open-meteo.com/v1/air-quality';

const Duration requestTimeout = Duration(seconds: 30);

const String hourlyForecastVars =
    'temperature_2m,precipitation_probability,precipitation,cloud_cover,'
    'windspeed_10m,windgusts_10m,winddirection_10m,cape,pressure_msl,uv_index';
const String dailyForecastVars =
    'temperature_2m_max,temperature_2m_min,precipitation_sum,'
    'precipitation_probability_max,windspeed_10m_max,windgusts_10m_max,'
    'pressure_msl_mean,uv_index_max';
const String regionalDailyVars =
    'precipitation_sum,windspeed_10m_max,pressure_msl_mean';
const String archiveHourlyVars =
    'temperature_2m,precipitation,windspeed_10m,windgusts_10m,cloud_cover,pressure_msl';
const String airQualityHourlyVars = 'pm10,pm2_5,european_aqi,us_aqi';

/// Retry budget for weather fetches. Small: the data is free and the failure
/// mode is a missing forecast, so a few quick attempts are worth far more than
/// they cost. Mirrors MAX_ATTEMPTS in the Python implementation.
const int fetchMaxAttempts = 3;
const Duration fetchRetryBaseDelay = Duration(milliseconds: 1500);

class OpenMeteoFetchError implements Exception {
  final String message;
  OpenMeteoFetchError(this.message);
  @override
  String toString() => 'OpenMeteoFetchError: $message';
}

/// A geographic point. Kept minimal — this package has no config concept.
class Point {
  final double lat;
  final double lon;
  const Point(this.lat, this.lon);
}


/// The synoptic ring: a coarse pressure field around the primary point.
///
/// DELIBERATELY separate from the near-field region points. Those span
/// roughly 125 x 55 km and feed local convection reasoning; synoptic
/// features have wavelengths of 1,000-4,000 km, so a 125 km box fits
/// entirely inside one system's gradient. Conflating the two scales would
/// degrade both.
///
/// 12 degrees is about 1,300 km, so the ring spans ~2,600 km — enough to see
/// a centre and its movement without pretending to resolve a front.
const double synopticRingOffsetDeg = 12.0;
const List<(double, double, String)> synopticRing = [
  (0.0, 0.0, 'centre'),
  (1.0, 0.0, 'N'), (1.0, 1.0, 'NE'), (0.0, 1.0, 'E'), (-1.0, 1.0, 'SE'),
  (-1.0, 0.0, 'S'), (-1.0, -1.0, 'SW'), (0.0, -1.0, 'W'), (1.0, -1.0, 'NW'),
];

/// (lat, lon, label) for the ring. Latitudes clamp so a high-latitude fork
/// cannot request an impossible coordinate; longitudes wrap. Location-agnostic
/// by construction.
List<(double, double, String)> synopticRingPoints(
  double lat,
  double lon, {
  double offsetDeg = synopticRingOffsetDeg,
}) {
  double round4(double v) => (v * 10000).round() / 10000;
  return [
    for (final (dlat, dlon, label) in synopticRing)
      (
        round4((lat + dlat * offsetDeg).clamp(-90.0, 90.0)),
        round4((lon + dlon * offsetDeg + 180.0) % 360.0 - 180.0),
        label,
      )
  ];
}

class OpenMeteoClient {
  final http.Client _client;
  final Duration _retryBaseDelay;

  /// [client] is injectable so tests can run against canned responses with no
  /// network — the Dart equivalent of the Python suite's requests-mock use.
  OpenMeteoClient({http.Client? client, Duration? retryBaseDelay})
      : _client = client ?? http.Client(),
        _retryBaseDelay = retryBaseDelay ?? fetchRetryBaseDelay;

  /// Injectable so tests do not actually wait. Real backoff is correct
  /// behaviour in production and pure cost in a suite — and a slow suite gets
  /// run less, which is how regressions reach production.

  void close() => _client.close();

  Future<Map<String, Object?>> _get(
    String url,
    Map<String, String> params,
  ) async =>
      (await _fetch(url, params)) as Map<String, Object?>;

  /// One request, retried on transient failure.
  ///
  /// Transient failures here used to abort the whole run on the first blip,
  /// which was backwards: the LLM providers retry (see llm/provider.dart) and
  /// those calls cost money, while this one is FREE and its failure is more
  /// expensive — the LLM is never reached, so nothing is produced at all.
  ///
  /// Observed in practice: a run succeeded, and an identical one 30 seconds
  /// later could not reach the API, with the service healthy either side.
  Future<Object?> _fetch(String url, Map<String, String> params) async {
    final uri = Uri.parse(url).replace(queryParameters: params);
    Object? lastError;

    for (var attempt = 1; attempt <= fetchMaxAttempts; attempt++) {
      http.Response? resp;
      try {
        resp = await _client.get(uri).timeout(requestTimeout);
      } catch (e) {
        lastError = OpenMeteoFetchError('Request to $url failed: $e');
      }

      if (resp != null) {
        if (resp.statusCode == 200) return jsonDecode(resp.body);
        final body =
            resp.body.length > 500 ? resp.body.substring(0, 500) : resp.body;
        // A 4xx other than 429 means the REQUEST is wrong — a misspelled
        // variable, an impossible coordinate. Retrying repeats the mistake
        // more slowly and hides it behind a longer wait.
        if (resp.statusCode >= 400 &&
            resp.statusCode < 500 &&
            resp.statusCode != 429) {
          throw OpenMeteoFetchError(
              '$url returned HTTP ${resp.statusCode}: $body');
        }
        lastError =
            OpenMeteoFetchError('$url returned HTTP ${resp.statusCode}: $body');
      }

      if (attempt < fetchMaxAttempts) {
        await Future<void>.delayed(_retryBaseDelay * attempt);
      }
    }
    throw lastError!;
  }

  /// Like [_get], but without asserting the response is an object.
  ///
  /// Open-Meteo returns a JSON **array** when the request carries multiple
  /// comma-separated coordinates — one block per point. Casting that to a Map
  /// throws, so every multi-point endpoint must decode through here.
  Future<Object?> _getRaw(String url, Map<String, String> params) =>
      _fetch(url, params);

  /// Today's hourly multi-model guidance. `forecast_days=1` because onset
  /// timing is only meaningful for today.
  Future<Map<String, Object?>> fetchForecastHourlyToday({
    required double lat,
    required double lon,
    required List<String> models,
    required String timezone,
  }) =>
      _get(forecastUrl, {
        'latitude': '$lat',
        'longitude': '$lon',
        'hourly': hourlyForecastVars,
        'forecast_days': '1',
        'timezone': timezone,
        'models': models.join(','),
      });

  /// Daily multi-model summary. Default 8 days, NOT 7 — index 0 is today, so
  /// index 7 is what genuinely represents seven full days out.
  Future<Map<String, Object?>> fetchForecastDailyExtended({
    required double lat,
    required double lon,
    required List<String> models,
    required String timezone,
    int days = 8,
  }) =>
      _get(forecastUrl, {
        'latitude': '$lat',
        'longitude': '$lon',
        'daily': dailyForecastVars,
        'forecast_days': '$days',
        'timezone': timezone,
        'models': models.join(','),
      });

  /// Multi-point regional pressure sketch for the Synoptic Overview.
  ///
  /// Always `best_match`, never the full model set: this is a
  /// regional-pattern sketch, not per-model verification data.
  Future<Map<String, Object?>> fetchRegionalPressure({
    required Point primaryPoint,
    required List<Point> regionPoints,
    required String timezone,
    int days = 7,
  }) async {
    final points = [primaryPoint, ...regionPoints];
    // Multi-coordinate, so the response is an ARRAY of per-point blocks. It
    // is normalised to {"blocks": [...]} rather than cast to a Map, which
    // would throw against the real API — this went unnoticed because the
    // test mocked a single-object response.
    final raw = await _getRaw(forecastUrl, {
      'latitude': points.map((p) => '${p.lat}').join(','),
      'longitude': points.map((p) => '${p.lon}').join(','),
      'daily': regionalDailyVars,
      'forecast_days': '$days',
      'timezone': timezone,
      'models': 'best_match',
    });
    if (raw is Map<String, Object?>) return raw;
    return {'blocks': raw};
  }

  Future<Map<String, Object?>> fetchAirQuality({
    required double lat,
    required double lon,
    required String timezone,
    int days = 1,
  }) =>
      _get(airQualityUrl, {
        'latitude': '$lat',
        'longitude': '$lon',
        'hourly': airQualityHourlyVars,
        'forecast_days': '$days',
        'timezone': timezone,
      });

  /// Actual/reanalysis hourly data for a date range in ONE call.
  ///
  /// Open-Meteo retains this indefinitely, which is what lets rolling stats
  /// be re-derived statelessly rather than needing fragile incremental
  /// storage — and, in the app, what lets a device that missed days backfill
  /// the observations it slept through. Note that predictions cannot be
  /// backfilled this way: a day the app never ran stored no prediction, so
  /// that day can never be scored.
  Future<Map<String, Object?>> fetchArchiveRange({
    required double lat,
    required double lon,
    required DateTime startDate,
    required DateTime endDate,
    required String timezone,
  }) =>
      _get(archiveUrl, {
        'latitude': '$lat',
        'longitude': '$lon',
        'start_date': formatDate(startDate),
        'end_date': formatDate(endDate),
        'hourly': archiveHourlyVars,
        'timezone': timezone,
      });

  Future<Map<String, Object?>> fetchArchiveSingleDay({
    required double lat,
    required double lon,
    required DateTime day,
    required String timezone,
  }) =>
      fetchArchiveRange(
        lat: lat,
        lon: lon,
        startDate: day,
        endDate: day,
        timezone: timezone,
      );


  /// Coarse MSLP field for the Synoptic Overview.
  ///
  /// One request for all nine points (Open-Meteo accepts comma-separated
  /// coordinates), `best_match` only — a large-scale pattern sketch, never
  /// scored. Measured 2026-08-19: HTTP 200 in 1.16 s for 3,133 bytes.
  Future<Map<String, Object?>> fetchSynopticPressure({
    required double lat,
    required double lon,
    required String timezone,
    int days = 3,
  }) async {
    final points = synopticRingPoints(lat, lon);
    final raw = await _getRaw(forecastUrl, {
      'latitude': points.map((p) => p.$1.toString()).join(','),
      'longitude': points.map((p) => p.$2.toString()).join(','),
      'daily': 'pressure_msl_mean',
      'forecast_days': '$days',
      'timezone': timezone,
      'models': 'best_match',
    });
    // Multi-coordinate requests return a LIST of blocks, one per point.
    final blocks = raw is List ? raw : [raw];
    return {
      'points': [
        for (var i = 0; i < points.length && i < blocks.length; i++)
          {
            'label': points[i].$3,
            'lat': (blocks[i] as Map)['latitude'],
            'lon': (blocks[i] as Map)['longitude'],
            'mslp_hpa':
                ((blocks[i] as Map)['daily'] as Map?)?['pressure_msl_mean'] ?? const <Object?>[],
          }
      ]
    };
  }
}

/// Splits a flat multi-day hourly archive response into one [DailyActual]
/// per calendar date — the definition of "what actually happened" that every
/// verification score is measured against.
///
/// Wind fallback matters and is subtle: if the `windgusts_10m` ARRAY is
/// present at all it is used for every hour, *even hours where that
/// individual value is null*. `windspeed_10m` applies only when the gust
/// array is absent entirely. Substituting windspeed for null gust hours
/// would quietly mix two different measurements into one series.
Map<DateTime, DailyActual> bucketHourlyByDate(
  Map<String, Object?> hourlyJson, {
  double threshold = rainThresholdMm,
}) {
  final hourly = hourlyJson['hourly'];
  if (hourly is! Map<String, Object?> || hourly.isEmpty) return {};

  List<double?> nums(Object? v) => v is List
      ? v.map((e) => e == null ? null : (e as num).toDouble()).toList()
      : const [];

  final times = (hourly['time'] as List?)?.cast<String>() ?? const <String>[];
  final tempArr = nums(hourly['temperature_2m']);
  final precipArr = nums(hourly['precipitation']);
  // Presence of the ARRAY decides, not presence of values within it.
  final gusts = hourly['windgusts_10m'];
  final windArr = (gusts is List && gusts.isNotEmpty)
      ? nums(gusts)
      : nums(hourly['windspeed_10m']);
  final pressureArr = nums(hourly['pressure_msl']);

  double? at(List<double?> a, int i) => i < a.length ? a[i] : null;

  final byDate = <String, _DayBucket>{};
  for (var i = 0; i < times.length; i++) {
    final dStr = times[i].split('T').first;
    final b = byDate.putIfAbsent(dStr, _DayBucket.new);
    b.temps.add(at(tempArr, i));
    b.precip.add(at(precipArr, i));
    b.wind.add(at(windArr, i));
    b.pressure.add(at(pressureArr, i));
    b.times.add(times[i]);
  }

  final result = <DateTime, DailyActual>{};
  byDate.forEach((dStr, day) {
    final temps = day.temps.whereType<double>().toList();
    final wind = day.wind.whereType<double>().toList();
    final pressure = day.pressure.whereType<double>().toList();
    result[parseDate(dStr)] = DailyActual(
      rain: day.precip.any((v) => (v ?? 0) >= threshold),
      highC: temps.isEmpty ? null : temps.reduce((a, b) => a > b ? a : b),
      lowC: temps.isEmpty ? null : temps.reduce((a, b) => a < b ? a : b),
      peakWindKmh: wind.isEmpty ? null : wind.reduce((a, b) => a > b ? a : b),
      mslpTrend:
          pressure.length >= 2 ? pressure.last - pressure.first : null,
      onsetHour: getOnsetHour(day.times, day.precip, threshold: threshold),
    );
  });
  return result;
}

class _DayBucket {
  final List<double?> temps = [];
  final List<double?> precip = [];
  final List<double?> wind = [];
  final List<double?> pressure = [];
  final List<String> times = [];

}
