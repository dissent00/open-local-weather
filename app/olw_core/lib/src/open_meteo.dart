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

class OpenMeteoClient {
  final http.Client _client;

  /// [client] is injectable so tests can run against canned responses with no
  /// network — the Dart equivalent of the Python suite's requests-mock use.
  OpenMeteoClient({http.Client? client}) : _client = client ?? http.Client();

  void close() => _client.close();

  Future<Map<String, Object?>> _get(
    String url,
    Map<String, String> params,
  ) async {
    final uri = Uri.parse(url).replace(queryParameters: params);
    http.Response resp;
    try {
      resp = await _client.get(uri).timeout(requestTimeout);
    } catch (e) {
      throw OpenMeteoFetchError('Request to $url failed: $e');
    }
    if (resp.statusCode != 200) {
      final body = resp.body.length > 500 ? resp.body.substring(0, 500) : resp.body;
      throw OpenMeteoFetchError('$url returned HTTP ${resp.statusCode}: $body');
    }
    return jsonDecode(resp.body) as Map<String, Object?>;
  }

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
  }) {
    final points = [primaryPoint, ...regionPoints];
    return _get(forecastUrl, {
      'latitude': points.map((p) => '${p.lat}').join(','),
      'longitude': points.map((p) => '${p.lon}').join(','),
      'daily': regionalDailyVars,
      'forecast_days': '$days',
      'timezone': timezone,
      'models': 'best_match',
    });
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
