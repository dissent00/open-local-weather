// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Data classes mirroring the Python `models.py` shapes.
///
/// `fromJson`/`toJson` deliberately use the same field names as the committed
/// `data/log/*.json` entries (snake_case), because that JSON is the contract
/// between implementations — see docs-internal/APP_ARCHITECTURE.md. A port
/// that renamed fields would still pass its own tests while being unable to
/// read anything the pipeline actually wrote.

/// Coerces a JSON number to double, tolerating ints.
///
/// Needed because JSON has one number type but Dart has two: a Python float
/// of `26.0` may arrive as either `26` or `26.0` depending on the encoder,
/// and `as double` would throw on the former.
double? _toDouble(Object? v) => v == null ? null : (v as num).toDouble();

int? _toInt(Object? v) => v == null ? null : (v as num).toInt();

/// One model's prediction for one target date at one lead time.
class ModelPrediction {
  final String model;

  /// `null` means the model had NO DATA at this lead time — which is not the
  /// same as a confident dry forecast, and must never be scored as one.
  /// UKMO's horizon stops around 7.2 days, so it genuinely has no Day+7.
  /// Recording that as "no rain" would accrue fake, flattering accuracy; it
  /// is a bug that shipped once in this project's real history.
  final bool? rain;

  /// "HH:MM", Day+0 only — Day+3/+7 carry no onset timing by design.
  final String? onset;
  final double? windKmh;
  final double? highC;
  final double? lowC;
  final double? mslpTrend;

  /// Total precipitation for the day, millimetres. ADDITIVE and NOT SCORED —
  /// `rain` stays the boolean the accuracy record is built on, because
  /// changing what that means would make every stored day incomparable with
  /// every other. A boolean cannot tell 0.6 mm at 20:00 from 40 mm all day,
  /// and the day-over-day summary was calling both "another wet day".
  final double? precipMm;

  const ModelPrediction({
    required this.model,
    this.rain,
    this.onset,
    this.windKmh,
    this.highC,
    this.lowC,
    this.mslpTrend,
    this.precipMm,
  });

  factory ModelPrediction.fromJson(Map<String, Object?> j) => ModelPrediction(
        model: j['model'] as String,
        rain: j['rain'] as bool?,
        onset: j['onset'] as String?,
        windKmh: _toDouble(j['wind_kmh']),
        highC: _toDouble(j['high_c']),
        lowC: _toDouble(j['low_c']),
        mslpTrend: _toDouble(j['mslp_trend']),
        precipMm: _toDouble(j['precip_mm']),
      );

  Map<String, Object?> toJson() => {
        'model': model,
        'rain': rain,
        'onset': onset,
        'wind_kmh': windKmh,
        'high_c': highC,
        'low_c': lowC,
        'mslp_trend': mslpTrend,
        'precip_mm': precipMm,
      };
}

/// One day's actual observation, bucketed from hourly data.
class DailyActual {
  final bool rain;
  final double? highC;
  final double? lowC;
  final double? peakWindKmh;
  final double? mslpTrend;
  final String? onsetHour;

  /// Total precipitation for the day, millimetres. ADDITIVE and NOT SCORED —
  /// `rain` stays the boolean the accuracy record is built on, because
  /// changing what that means would make every stored day incomparable with
  /// every other. A boolean cannot tell 0.6 mm at 20:00 from 40 mm all day,
  /// and the day-over-day summary was calling both "another wet day".
  final double? precipMm;

  const DailyActual({
    required this.rain,
    this.highC,
    this.lowC,
    this.peakWindKmh,
    this.mslpTrend,
    this.onsetHour,
    this.precipMm,
  });

  factory DailyActual.fromJson(Map<String, Object?> j) => DailyActual(
        rain: j['rain'] as bool,
        highC: _toDouble(j['high_c']),
        lowC: _toDouble(j['low_c']),
        peakWindKmh: _toDouble(j['peak_wind_kmh']),
        mslpTrend: _toDouble(j['mslp_trend']),
        onsetHour: j['onset_hour'] as String?,
        precipMm: _toDouble(j['precip_mm']),
      );

  Map<String, Object?> toJson() => {
        'rain': rain,
        'high_c': highC,
        'low_c': lowC,
        'peak_wind_kmh': peakWindKmh,
        'mslp_trend': mslpTrend,
        'onset_hour': onsetHour,
        'precip_mm': precipMm,
      };
}

/// The result of scoring one [ModelPrediction] against one [DailyActual].
///
/// Every error field is **actual − predicted**. Inverting that convention
/// would flip every bias reading in the track record — a model running warm
/// would be reported as running cold — so it is pinned by a shared vector.
class VerificationScore {
  final bool rainCorrect;

  /// Day+0 only, and only when both predicted and actual saw rain.
  final double? onsetErrorHrs;
  final double? windErrorKmh;
  final double? highErrorC;
  final double? lowErrorC;
  final double? mslpErrorHpa;

  const VerificationScore({
    required this.rainCorrect,
    this.onsetErrorHrs,
    this.windErrorKmh,
    this.highErrorC,
    this.lowErrorC,
    this.mslpErrorHpa,
  });

  Map<String, Object?> toJson() => {
        'rain_correct': rainCorrect,
        'onset_error_hrs': onsetErrorHrs,
        'wind_error_kmh': windErrorKmh,
        'high_error_c': highErrorC,
        'low_error_c': lowErrorC,
        'mslp_error_hpa': mslpErrorHpa,
      };
}

/// One ground station's AQI reading.
class GroundAqiReading {
  final String name;
  final String stationId;

  /// `null` when the station reported no composite index — distinct from the
  /// station being absent, and distinct again from the reading being stale.
  final int? aqi;
  final double? pm25;
  final double? pm10;

  /// When the reading was TAKEN, not when it was fetched. `null` means
  /// unknown freshness, which is treated as stale — never assumed fresh.
  final DateTime? measuredAt;

  const GroundAqiReading({
    required this.name,
    required this.stationId,
    this.aqi,
    this.pm25,
    this.pm10,
    this.measuredAt,
  });

  factory GroundAqiReading.fromJson(Map<String, Object?> j) => GroundAqiReading(
        name: j['name'] as String,
        stationId: j['station_id'] as String,
        aqi: _toInt(j['aqi']),
        pm25: _toDouble(j['pm25']),
        pm10: _toDouble(j['pm10']),
        measuredAt: j['measured_at'] == null
            ? null
            : DateTime.parse(j['measured_at'] as String),
      );

  Map<String, Object?> toJson() => {
        'name': name,
        'station_id': stationId,
        'aqi': aqi,
        'pm25': pm25,
        'pm10': pm10,
        'measured_at': measuredAt?.toIso8601String(),
      };
}

/// Deterministic range/worst-station summary across ground stations.
class GroundAqiSummary {
  final int aqiMin;
  final int aqiMax;
  final String highestStationName;
  final int stationsWithAqi;
  final int stationsStale;
  final int stationsTotal;

  const GroundAqiSummary({
    required this.aqiMin,
    required this.aqiMax,
    required this.highestStationName,
    required this.stationsWithAqi,
    required this.stationsStale,
    required this.stationsTotal,
  });

  Map<String, Object?> toJson() => {
        'aqi_min': aqiMin,
        'aqi_max': aqiMax,
        'highest_station_name': highestStationName,
        'stations_with_aqi': stationsWithAqi,
        'stations_stale': stationsStale,
        'stations_total': stationsTotal,
      };
}
