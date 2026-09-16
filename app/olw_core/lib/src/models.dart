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
/// What the station has actually reported TODAY, so far.
///
/// Every field is three-valued and absence means absence: a station that
/// reported nothing is not a station reporting agreement.
///
/// WITHIN a populated record the distinction sharpens, and it is worth
/// stating because the two look alike in JSON. `thunder: false` means the
/// station reported and saw none, which is information. `thunder: null` means
/// nothing was measured, which is not.
///
/// SIX DIMENSIONS, WHICH ARE UPSTREAM ITEM 104'S C9 TABLE — high and low,
/// peak wind, sky, thunder, and rain with its onset. It carried two until
/// 2026-09-13 because it existed only to feed the contradiction check; item
/// 121 reports these to a reader directly, in code, so the set is now the one
/// C9 specified rather than the one that check happened to need.
///
/// PRECIPITATION AMOUNT IS ABSENT ON PURPOSE and is the one dimension C9
/// withholds: a METAR reports that rain fell, never how much, and ERA5's
/// same-day archive is model output rather than observation.
class ObservedSoFar {
  const ObservedSoFar({
    this.precipitation,
    this.precipitationOnset,
    this.thunder,
    this.highC,
    this.lowC,
    this.peakWindKmh,
    this.cloudOktas,
  });

  final bool? precipitation;

  /// Local "HH:MM" of the first report that saw precipitation.
  final String? precipitationOnset;
  final bool? thunder;
  final double? highC;
  final double? lowC;
  final double? peakWindKmh;

  /// Mean cover in eighths across the day's reports so far.
  final double? cloudOktas;
}

/// How far an observation must sit from the forecast before a reader is TOLD
/// about it — upstream item 145.
///
/// THE REPORTING HALF AND ONLY THE REPORTING HALF. Nothing here may change
/// what a run SPENDS: [lowDivergence] decides `decisive` from its own fixed
/// constant and never from these, and a swept test drives every field across
/// its range asserting `observationDisagreements` does not move.
///
/// WHY. `llmShouldReason` treats any member of `observationDisagreements` as
/// grounds to buy a judgment call AND a narrative. The two used to be one
/// number, so tightening what you wanted to be told about also bought calls,
/// silently, against a fixed cap. This project is for people who will not be
/// buying an API key.
///
/// DEFAULTS ARE THE SHIPPED VALUES, so an app that configures nothing behaves
/// as it did.
class DeviationBands {
  const DeviationBands({this.lowC = 3.0, this.lowFreezingC = 1.0});

  /// At or below [nearFreezingC] the freezing band applies instead. Two
  /// widths because one number cannot be right: two degrees is nothing at
  /// 20 C and is ice or no ice at 2 C.
  final double lowC;
  final double lowFreezingC;
}

/// What the station's overnight low says about the called one — upstream
/// item 143.
///
/// STORED WHETHER OR NOT IT PRINTS, which is what makes the other parts
/// answerable later: whether the station runs warmer than the forecast or the
/// forecast low is the problem is answered by the ordinary days, not the loud
/// ones. [notable] is a judgement ABOUT this, not a condition for keeping it.
class LowDivergence {
  const LowDivergence({
    required this.forecastC,
    required this.observedC,
    required this.deltaC,
    required this.marginC,
    required this.notable,
    required this.decisive,
  });

  final double forecastC;
  final double observedC;

  /// Observed minus forecast. POSITIVE means the station came in WARMER than
  /// the call, which is the founding case and the direction that needs a
  /// settled night behind it.
  final double deltaC;

  /// The band this gap was judged against, carried so a stored row can be
  /// re-read after the margin is retuned without guessing which one applied.
  final double marginC;

  /// Worth a reader's attention: the gap exceeded its band.
  final bool notable;

  /// Worth an LLM call: [notable] AND cold enough to change what someone
  /// does. Separate from [notable] because membership in the disagreement
  /// list is a spending decision.
  final bool decisive;
}

const String disagreementRainWhileDry = 'rain_observed_while_dry_called';
const String disagreementHighExceeded = 'high_already_exceeded';
const String disagreementLowDiverges = 'observed_low_diverges';
const String disagreementOnsetAlreadyPassed = 'onset_already_passed';

/// How far above the standing high an observation must sit before it counts.
///
/// SIZED AGAINST TWO MEASURED QUANTITIES, not picked for roundness. The
/// station reads +0.43 C against the reanalysis on average, and the blend's
/// Day+0 high error runs a few tenths. A margin at or below either would fire
/// on the instrument rather than on the weather, and every spurious firing
/// spends an LLM call. Conservative and not yet measured — revisit against
/// the record, not against a convenient sample.
const double tempContradictionMarginC = 2.0;

/// How much earlier the observed onset must be before it counts — upstream
/// ROADMAP item 138. Sized to the forecast's own resolution: `onsetHour` is a
/// point taken from a multi-hour window, so a difference smaller than that
/// window is agreement. Unmeasured, and conservative on purpose.
const int onsetContradictionMarginMin = 60;

String formatTempHighLow(double highC, double lowC) =>
    '${formatTempC(highC)} high, ${formatTempC(lowC)} low';

/// One temperature, in both units — the half of [formatTempHighLow] that is
/// about a single number.
///
/// EXTRACTED RATHER THAN COPIED, 2026-09-13, mirroring `format_temp_c` in
/// models.py. Upstream item 121 needs the same rendering for observed
/// temperatures, and a second spelling would be a second rounding site —
/// which is this project's most-bitten cross-language divergence.
///
/// The rounding reasoning belongs to [formatTempHighLow] above and is not
/// repeated.
/// [decimals] IS A PRECISION, NOT A STYLE — upstream item 143, and it exists
/// for exactly one caller. Whole degrees are right for everything a reader
/// plans a day around and remain the default. They are wrong for the
/// overnight-low footnote, whose entire content is a GAP: at 0 decimals "20
/// against a forecast of 18.2" prints as "20 against 18", and near freezing
/// -0.4 and 0.6 both print as 0, erasing the ice/no-ice distinction that is
/// the only reason that footnote is allowed to exist.
///
/// THE TWO PRECISIONS ROUND TIES DIFFERENTLY, ON PURPOSE, AND THIS IS THE
/// PART TO READ BEFORE CHANGING ANYTHING. At 0 decimals the tie goes to EVEN,
/// because that is what Python's `round()` does and [_roundHalfEven] exists to
/// match it. At more than 0 it goes AWAY FROM ZERO, because that is what
/// `toStringAsFixed` does natively here and Python's side quantizes with
/// ROUND_HALF_UP to match. Each precision is pinned to whichever rule both
/// languages can express with a primitive; the alternative was hand-rolling
/// exact decimal arithmetic in Dart to chase Python's `format`, which is more
/// code in the place this project has been bitten most.
///
/// DO NOT "SIMPLIFY" THIS BY SCALING. Multiplying by 10^d, rounding, and
/// dividing back was tried on 2026-09-16 and diverged from Python on 569 of
/// 13,202 swept values: the multiplication lands a value like -59.85 exactly
/// on a representable half that it was not on before, and the rounding then
/// answers a different question. The committed pair was swept over the same
/// 13,202 values at 1 and 2 input decimals with zero divergences.
String formatTempC(double celsius, {int decimals = 0}) => decimals == 0
    ? '${_roundHalfEven(celsius)}°C / ${_roundHalfEven(celsius * 9 / 5 + 32)}°F'
    : '${_noNegativeZero(celsius.toStringAsFixed(decimals))}°C / '
        '${_noNegativeZero((celsius * 9 / 5 + 32).toStringAsFixed(decimals))}°F';

/// "-0.0" is a real output of both languages here — -17.8 C is -0.04 F — and
/// it is not wrong so much as unreadable in a sentence a person is meant to
/// act on. Normalised identically on both sides rather than left to differ.
String _noNegativeZero(String fixed) =>
    double.parse(fixed) == 0 ? fixed.replaceFirst('-', '') : fixed;

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

  /// WHAT THIS PREDICTION IS ABOUT — upstream ROADMAP item 104, C1.
  ///
  /// The target was implicit until 2026-09-12: a prediction sat on the
  /// issuance's row and the lead time said how far forward it pointed, so
  /// `target = rowDate + lead`. That arithmetic is correct and stays correct,
  /// but only while a day holds exactly ONE issuance — the assumption item
  /// 104 removes.
  ///
  /// Three-valued: `null` means the row predates the field, NOT that it
  /// targets nothing. Every entry committed before 2026-09-12 loads that way.
  ///
  /// A DATE, not a DateTime. It names a calendar day at the location, and a
  /// timestamp here would invite a timezone to creep into a value that has
  /// none — the same reason `DailyLogEntry.date` is a date.
  final DateTime? targetDate;

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

  /// Day MEAN cloud cover, 0-100. Fetched in the forecast vars since before
  /// this field existed and discarded at extraction — the third place cloud
  /// was paid for and thrown away. A MEAN where wind is a max, because the
  /// question is what kind of day it was, and because the observed side is a
  /// mean too so the two compare like for like.
  final double? cloudCoverPct;

  /// The day's HIGHEST hourly CAPE, J/kg — upstream items 35 and 87.
  ///
  /// A PEAK where cloud is a mean, and deliberately: an afternoon that
  /// touches 2000 J/kg for one hour is convective, and a mean against a calm
  /// morning hides the one hour that matters. Same quantity
  /// [summarizeInstability] shows the forecaster.
  ///
  /// Whole-day, where summarizeInstability trims to the hours ahead: every
  /// other Day+0 field is taken over the whole day, and a morning run's
  /// record has to be comparable with an evening one's.
  ///
  /// Day+0 only — CAPE is hourly and the extended leads come from the daily
  /// endpoint, exactly like `onset`. Null beyond Day+0 means "not fetched",
  /// never "stable".
  final double? peakCapeJkg;

  /// The compass bearing, degrees, AT THIS MODEL'S OWN PEAK-GUST HOUR —
  /// upstream item 59. Paired with [windKmh], which is that same model's own
  /// day-maximum gust: a speed from one hour beside a bearing from another
  /// describes a wind that never blew.
  ///
  /// NOT SCORED, and never averaged by a caller. Bearings are circular and an
  /// arithmetic mean of them is meaningless — see wind.dart's vectorMean,
  /// the only thing allowed to combine these. Null, never 0.0, when absent:
  /// due north is a confident bearing and no data is not.
  final double? windDirectionDeg;

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
    this.targetDate,
    this.rain,
    this.onset,
    this.windKmh,
    this.highC,
    this.lowC,
    this.mslpTrend,
    this.cloudCoverPct,
    this.peakCapeJkg,
    this.windDirectionDeg,
    this.precipMm,
    this.rainProbabilityPct,
  });

  factory ModelPrediction.fromJson(Map<String, Object?> j) => ModelPrediction(
        model: j['model'] as String,
        targetDate: j['target_date'] == null
            ? null
            : DateTime.parse(j['target_date'] as String),
        rain: j['rain'] as bool?,
        onset: j['onset'] as String?,
        windKmh: _toDouble(j['wind_kmh']),
        highC: _toDouble(j['high_c']),
        lowC: _toDouble(j['low_c']),
        mslpTrend: _toDouble(j['mslp_trend']),
        precipMm: _toDouble(j['precip_mm']),
        rainProbabilityPct: (j['rain_probability_pct'] as num?)?.toInt(),
        cloudCoverPct: _toDouble(j['cloud_cover_pct']),
        peakCapeJkg: _toDouble(j['peak_cape_jkg']),
        windDirectionDeg: _toDouble(j['wind_direction_deg']),
      );

  Map<String, Object?> toJson() => {
        'model': model,
        // ISO date, no time component — matches Python's `date` serialization
        // exactly, which the shared vectors compare against.
        'target_date': targetDate == null
            ? null
            : targetDate!.toIso8601String().substring(0, 10),
        'rain': rain,
        'onset': onset,
        'wind_kmh': windKmh,
        'high_c': highC,
        'low_c': lowC,
        'mslp_trend': mslpTrend,
        'cloud_cover_pct': cloudCoverPct,
        'peak_cape_jkg': peakCapeJkg,
        'wind_direction_deg': windDirectionDeg,
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

  /// TWO CLOUD OBSERVATIONS, IN DIFFERENT UNITS, AND NEITHER IS THE OTHER.
  /// [cloudCoverPct] is the reanalysis daily MEAN, 0-100, and had been fetched
  /// and discarded on every archive call. [stationCloudOktas] is the airport's
  /// daily mean in EIGHTHS, 0-8, from the METAR sky groups.
  ///
  /// NOT MERGED, unlike highC and stationHighC: those are one quantity in one
  /// unit from two sources, so a ladder can choose. Percent and eighths are
  /// not, and converting needs the NWS band table on both sides.
  final double? cloudCoverPct;
  final double? stationCloudOktas;

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
  /// Carried here so the app's own record can answer the same question, and
  /// THE APP DOES WRITE IT. `bucketHourlyByDate` stamps `rain` plus one key
  /// per field that came back non-null, so a device with no station of its own
  /// still attributes every reanalysis value it stores. Verified by running
  /// that function on 2026-09-12: eight keys, all `era5_archive`.
  ///
  /// This paragraph said the opposite until then — "the app does not yet WRITE
  /// it" — and had said it since the commit that ALSO taught
  /// `bucketHourlyByDate` to stamp (2026-09-03). It was load-bearing: a first
  /// design pass at ROADMAP item 74, the app screen that displays this, was
  /// built around showing no provenance at all because of it. Corrected from
  /// the app side, which is where it was caught.
  ///
  /// A field that came back null is left UNSTAMPED rather than stamped with an
  /// absence, so "no key" already means "nothing observed this" and a consumer
  /// needs no separate signal for it.
  final Map<String, String>? provenance;

  /// Did anything DETECT lightning on this local day — ROADMAP item 65.
  ///
  /// THREE-VALUED, like [thunder] and [precipitation] before it: null means
  /// nothing was asked, false means something looked and detected none. Every
  /// stored day is null today and stays null until a detection source exists,
  /// which is the honest state rather than a gap to fill with false.
  ///
  /// DELIBERATELY NOT IN [observedConvection]. That method is an OR, so every
  /// term added to it can only create wet days and can only move the rain
  /// rate. Lightning is a DIFFERENT QUESTION: "did it storm" and "did it
  /// rain" have different answers and the ledger has had one column for both.
  /// Scored separately, a model that predicted thunder and got a dry storm is
  /// right about thunder and wrong about rain — more information than either
  /// verdict alone, and nobody has to rule on whether a dry thunderstorm is
  /// "a wet day". A test asserts the omission, because it reads as an obvious
  /// completion to anyone who finds the field and not the reasoning.
  final bool? lightning;

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
    this.cloudCoverPct,
    this.stationCloudOktas,
    this.lightning,
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
        lightning: j['lightning'] as bool?,
        precipitation: j['precipitation'] as bool?,
        precipitationOnset: j['precipitation_onset'] as String?,
        stationHighC: _toDouble(j['station_high_c']),
        stationLowC: _toDouble(j['station_low_c']),
        stationPeakWindKmh: _toDouble(j['station_peak_wind_kmh']),
        cloudCoverPct: _toDouble(j['cloud_cover_pct']),
        stationCloudOktas: _toDouble(j['station_cloud_oktas']),
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
        'lightning': lightning,
        'precipitation': precipitation,
        'precipitation_onset': precipitationOnset,
        'station_high_c': stationHighC,
        'station_low_c': stationLowC,
        'station_peak_wind_kmh': stationPeakWindKmh,
        'cloud_cover_pct': cloudCoverPct,
        'station_cloud_oktas': stationCloudOktas,
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

  /// The squared error of the model's own probability — upstream ROADMAP item
  /// 58, and LOWER IS BETTER unlike every other figure here. Null when the
  /// model supplied no probability, which is most stored days; never a
  /// default of 0.5, which would invent a hedge nobody made. Scored against
  /// the same observedConvection() truth as [rainCorrect], because two
  /// columns scored against two truths would not be comparable.
  final double? rainBrier;

  /// Day+0 only, and only when both predicted and actual saw rain.
  final double? onsetErrorHrs;
  final double? windErrorKmh;
  final double? highErrorC;
  final double? lowErrorC;
  final double? mslpErrorHpa;

  /// actual - predicted, in percentage points of sky covered.
  ///
  /// Added 2026-09-10 so the sky can earn a track record. cloudCoverPct had
  /// been stored on both sides for a day and scored against nothing, so no
  /// model could gain or lose standing on it however wrong it was. Null on
  /// most stored days, and that is the honest value: zero would be a claim
  /// of perfect skill on a day the field did not exist.
  final double? cloudErrorPct;

  /// Did this model's INSTABILITY call match whether it actually thundered?
  /// Upstream item 35. Three-valued: a model with no CAPE series made no
  /// call, and a day with no station report settled nothing.
  ///
  /// AGAINST THUNDER, NOT RAIN. CAPE predicts thunderstorms; a day of steady
  /// frontal rain with no lightning is not a hit for a model that called
  /// high instability, which is why this is its own column rather than a
  /// second input to `rainCorrect`.
  final bool? convectiveCorrect;

  const VerificationScore({
    required this.rainCorrect,
    this.rainBrier,
    this.onsetErrorHrs,
    this.windErrorKmh,
    this.highErrorC,
    this.lowErrorC,
    this.mslpErrorHpa,
    this.cloudErrorPct,
    this.convectiveCorrect,
  });

  Map<String, Object?> toJson() => {
        'rain_correct': rainCorrect,
        'rain_brier': rainBrier,
        'onset_error_hrs': onsetErrorHrs,
        'wind_error_kmh': windErrorKmh,
        'high_error_c': highErrorC,
        'low_error_c': lowErrorC,
        'mslp_error_hpa': mslpErrorHpa,
        'cloud_error_pct': cloudErrorPct,
        'convective_correct': convectiveCorrect,
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

/// C2's three triggers, as the record stores them — upstream ROADMAP item
/// 104 stage 2b, acted on since item 121 by [llmShouldReason].
///
/// THREE-VALUED THROUGHOUT, AND THE MIDDLE VALUE IS THE POINT. `null` means
/// there was no BASIS for the comparison, which is different from a
/// comparison that came back negative. A port that collapsed the two would
/// decide spending on the strength of not having looked — the error class
/// that cost a published forecast upstream on 2026-08-29.
class InformationMoved {
  /// The day's first run is itself a trigger, so the other two have nothing
  /// to compare against on it.
  final bool firstIssuanceOfDay;

  /// Whether a new guidance cycle has landed since the previous issuance.
  /// `null` on a first run, and on a re-issue of an entry written before this
  /// was recorded — both mean "no basis", never false.
  final bool? guidanceIsNewer;

  /// What the station has already seen that contradicts the standing call.
  /// Empty means nothing seen contradicts it; `null` means nothing was looked
  /// at, which happens when the station did not report or the lookup failed.
  final List<String>? observationDisagreements;

  const InformationMoved({
    required this.firstIssuanceOfDay,
    this.guidanceIsNewer,
    this.observationDisagreements,
  });
}
