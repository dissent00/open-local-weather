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
/// The headline temperature line, in both units.
///
/// Computed here rather than asked of the model. It used to be a string the
/// LLM wrote, and it drifted in both of the ways an LLM-written number does.
///
/// It drifted in VALUE: on 2026-08-27 a blended high of 33.5 °C was published
/// as "34°C / 93°F". 33.5 °C is 92.3 °F — the model rounded to 34 first and
/// converted that. The day's comparison label, computed in code, said the day
/// was about the same as yesterday's observed 32.3 °C, and a reader looking at
/// 90 °F yesterday and 93 °F today reasonably disagreed.
///
/// And it drifted in FORM: the day before, the same field came out as
/// "32°C / 90°F (High) | 18°C / 64°F (Low)". Two consecutive days, two
/// formats, because nothing had ever fixed one.
///
/// Each unit is rounded from the true Celsius value rather than one from the
/// other, so both are the closest whole number to what was actually forecast.
/// A consequence worth keeping rather than "fixing": 33.5 °C gives
/// "34°C / 92°F", and 34 °C converts to 93.2 °F. The pair does not round-trip,
/// because rounding twice is what caused this.
String formatTempHighLow(double highC, double lowC) {
  String both(double celsius) =>
      '${_roundHalfEven(celsius)}°C / ${_roundHalfEven(celsius * 9 / 5 + 32)}°F';

  return '${both(highC)} high, ${both(lowC)} low';
}

/// Matches Python's `round()`, which is half-to-EVEN.
///
/// NOT Dart's `.round()`, which is half away from zero: 32.5 would come out as
/// 33 here and 32 there, publishing a different temperature on the site than
/// in the app. Same divergence as `_fmt0` in synoptic.dart guards against.
int _roundHalfEven(double v) {
  final floor = v.floor();
  final frac = v - floor;
  if (frac > 0.5) {
    return floor + 1;
  }
  if (frac < 0.5) {
    return floor;
  }

  return floor.isEven ? floor : floor + 1;
}

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

  /// The model's own chance-of-rain, percent — upstream ROADMAP item 58,
  /// storage half.
  ///
  /// RECORDED BUT NOT YET SCORED, and stored ahead of anything reading it on
  /// purpose. `rain` is a boolean, so a model that said "60% chance" and one
  /// that said "certainly" score identically whichever way the day goes, and
  /// the ledger cannot tell a confidently wrong forecast from an honestly
  /// uncertain one. Fixing that needs a proper scoring rule, and a proper
  /// scoring rule needs history: it cannot be computed backwards over days
  /// whose probabilities were fetched and thrown away, which is what has
  /// happened on every run until now. So the clock starts here.
  ///
  /// `null` means the model gave no probability, NEVER zero — zero is a
  /// confident claim that it will not rain, the same distinction `rain`
  /// keeps for the same reason.
  final int? rainProbabilityPct;

  const ModelPrediction({
    required this.model,
    this.rain,
    this.onset,
    this.windKmh,
    this.highC,
    this.lowC,
    this.mslpTrend,
    this.precipMm,
    this.rainProbabilityPct,
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
        rainProbabilityPct: (j['rain_probability_pct'] as num?)?.toInt(),
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
        'rain_probability_pct': rainProbabilityPct,
      };
}

/// One day's actual observation, bucketed from hourly data.
/// Source identifiers for [DailyActual.provenance] — upstream ROADMAP item
/// 45, trap 2. Must match `models.py`'s SOURCE_* constants: they are written
/// into every stored day, and a rename on one side would make the two records
/// incomparable.
const String sourceReanalysis = 'era5_archive';
const String sourceStation = 'metar_station';

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

  /// Did the airport observe thunder on this local day?
  ///
  /// THREE-VALUED, AND THE THIRD VALUE MATTERS. Null means no observation was
  /// available — no ICAO configured, the archive unreachable, or the station
  /// filed nothing that day — and must never read as "no thunder". False
  /// means the station reported and saw none, which is real evidence a dry
  /// call can be scored against.
  ///
  /// Not a decoration on `rain`: it changes what a rain forecast is scored
  /// against, via [observedConvection].
  final bool? thunder;

  /// Did the airport observe PRECIPITATION on this local day?
  ///
  /// THREE-VALUED for the same reason [thunder] is, and read the same way:
  /// null is "no observation", never "it stayed dry".
  ///
  /// Separate from [thunder] because the two fail separately. On 2026-08-29
  /// the station reported `-RA` and `RERA` under cumulonimbus with no `TS`
  /// group at all, the reanalysis recorded 0.0 mm, and the day scored DRY —
  /// crediting every model that had called it dry for a day it rained.
  /// Thunder alone could not catch that.
  ///
  /// Measured over the 45 days then stored: precipitation observed on 9, of
  /// which 2 had been scored dry by both the reanalysis and the thunder check
  /// (2026-07-21, 2026-08-29). Every model's all-time Day+0 rain accuracy
  /// fell about five points once they were counted. See ROADMAP item 53.
  final bool? precipitation;

  /// LOCAL "HH:MM" the airport first observed precipitation, or null.
  ///
  /// Kept SEPARATE from [onsetHour] rather than filling it in, because
  /// [onsetHour] is SCORED — scoring.dart measures onset error against it —
  /// and quietly swapping a reanalysis quantity for a station one would
  /// change what every stored onset error means. This field only ever feeds
  /// the day-over-day description, via [observedOnset].
  final String? precipitationOnset;

  /// What the STATION measured, stored beside the reanalysis values and not
  /// scored against anything — upstream ROADMAP item 45's sequencing, which
  /// is cross-check before replacement.
  ///
  /// Carried here so the shared record shape stays identical across the two
  /// languages; this app writes none of them, having no station of its own.
  /// There is deliberately no station precipitation field: the reference
  /// deployment's station files 0.00 inches on every row including hours its
  /// own report says -RA, so an amount from it is a constant dressed as a
  /// measurement.
  final double? stationHighC;
  final double? stationLowC;
  final double? stationPeakWindKmh;

  /// Which source supplied which value, for THIS day — upstream ROADMAP item
  /// 45, trap 2. Keys are field names, values are source ids.
  ///
  /// THREE-VALUED, like [thunder] before it. `null` means the day predates
  /// provenance recording and was never asked; an empty map would claim we
  /// looked and found no sources, which is never true of a stored day.
  ///
  /// WHY IT MATTERS. The station is truth for most days and down for a few,
  /// and those few are scored against a coarser instrument. Acceptable only
  /// if visible: without this, a dip in the accuracy record cannot be told
  /// apart from the models getting worse. Item 53.1 moved every model about
  /// five points in a day purely by adding a source.
  ///
  /// Carried here so the app's own record can answer the same question. The
  /// app does not yet WRITE it — it has no station of its own to attribute —
  /// but a value read back from storage must survive the round trip rather
  /// than being silently dropped.
  final Map<String, String>? provenance;

  const DailyActual({
    required this.rain,
    this.highC,
    this.lowC,
    this.peakWindKmh,
    this.mslpTrend,
    this.onsetHour,
    this.precipMm,
    this.thunder,
    this.precipitation,
    this.precipitationOnset,
    this.stationHighC,
    this.stationLowC,
    this.stationPeakWindKmh,
    this.provenance,
  });

  /// The onset a day's CHARACTER should be described from.
  ///
  /// The reanalysis onset when there is one, the station's when there is
  /// not. A day the reanalysis recorded as 0.0 mm has no onset by
  /// construction, so a shower it missed entirely had no time to be
  /// described at — which is how 2026-08-29 reached readers as "dry" after
  /// item 53.1 had already scored it as a wet day.
  ///
  /// NOT what onset error is scored against; see [precipitationOnset].
  String? observedOnset() => onsetHour ?? precipitationOnset;

  /// What a rain forecast is actually scored against.
  ///
  /// Reanalysis precipitation OR anything the airport actually saw fall or
  /// heard. A day with a thunderstorm over the city and 0.5 mm in a 25 km
  /// grid cell is a day the convective models called correctly, and scoring
  /// it as dry punishes exactly the models most worth trusting over a lake
  /// basin whose storms global models already under-resolve.
  ///
  /// THE NAME IS NARROWER THAN THE BEHAVIOUR, and deliberately kept: drizzle
  /// from stratus is not convection, but it is still rain the reader stood
  /// in, and still what a dry call should be scored against.
  ///
  /// Both observations being null leaves this as plain `rain`, so a
  /// deployment with no METAR station scores exactly as it did before.
  bool observedConvection() =>
      rain || thunder == true || precipitation == true;

  factory DailyActual.fromJson(Map<String, Object?> j) => DailyActual(
        rain: j['rain'] as bool,
        highC: _toDouble(j['high_c']),
        lowC: _toDouble(j['low_c']),
        peakWindKmh: _toDouble(j['peak_wind_kmh']),
        mslpTrend: _toDouble(j['mslp_trend']),
        onsetHour: j['onset_hour'] as String?,
        precipMm: _toDouble(j['precip_mm']),
        thunder: j['thunder'] as bool?,
        precipitation: j['precipitation'] as bool?,
        precipitationOnset: j['precipitation_onset'] as String?,
        stationHighC: _toDouble(j['station_high_c']),
        stationLowC: _toDouble(j['station_low_c']),
        stationPeakWindKmh: _toDouble(j['station_peak_wind_kmh']),
        provenance: (j['provenance'] as Map?)?.map(
            (k, v) => MapEntry(k as String, v as String)),
      );

  Map<String, Object?> toJson() => {
        'rain': rain,
        'high_c': highC,
        'low_c': lowC,
        'peak_wind_kmh': peakWindKmh,
        'mslp_trend': mslpTrend,
        'onset_hour': onsetHour,
        'precip_mm': precipMm,
        'thunder': thunder,
        'precipitation': precipitation,
        'precipitation_onset': precipitationOnset,
        'station_high_c': stationHighC,
        'station_low_c': stationLowC,
        'station_peak_wind_kmh': stationPeakWindKmh,
        'provenance': provenance,
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
/// The newest real ground reading available, whether or not it is fresh.
///
/// Exists because a null summary left the prompt with "Not applicable" and
/// the LLM free to improvise, which it did differently on consecutive days.
/// A stale reading is still the last time anyone actually measured the air;
/// said with its age attached it is more use than silence, and cannot be
/// mistaken for current.
class GroundAqiLastKnown {
  final String stationName;
  final int aqi;

  /// ISO 8601, not a DateTime. This value is printed into a prompt and pinned
  /// in a cross-language vector, and the two runtimes stringify a timestamp
  /// differently — Dart's toIso8601String() gives "...T05:30:00.000Z" where
  /// Python's isoformat() gives "...T05:30:00+00:00". Anything computing with
  /// the age uses [hoursOld] instead.
  final String measuredAt;
  final double hoursOld;
  final bool stale;

  /// How many stations share this timestamp — so the narrative can say three
  /// stations reported at that hour rather than implying only one exists.
  final int stationsReporting;

  const GroundAqiLastKnown({
    required this.stationName,
    required this.aqi,
    required this.measuredAt,
    required this.hoursOld,
    required this.stale,
    required this.stationsReporting,
  });

  Map<String, Object?> toJson() => {
        'station_name': stationName,
        'aqi': aqi,
        'measured_at': measuredAt,
        'hours_old': hoursOld,
        'stale': stale,
        'stations_reporting': stationsReporting,
      };
}

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
