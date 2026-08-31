// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'models.dart';

/// Default rain threshold in mm, matching Python's `RAIN_THRESHOLD_MM`.
/// Used consistently for both "did it rain" scoring and onset detection.
const double rainThresholdMm = 0.5;

/// First hour (HH:MM) whose precipitation crossed [threshold], or `null` if
/// it never did. `times` entries look like "2026-08-11T14:00".
String? getOnsetHour(
  List<String> times,
  List<double?> precip, {
  double threshold = rainThresholdMm,
}) {
  final n = times.length < precip.length ? times.length : precip.length;
  for (var i = 0; i < n; i++) {
    if ((precip[i] ?? 0) >= threshold) {
      final t = times[i];
      final idx = t.indexOf('T');
      return idx >= 0 ? t.substring(idx + 1) : null;
    }
  }
  return null;
}

List<double?> _numList(Object? v) {
  if (v is! List) return const [];
  return v.map((e) => e == null ? null : (e as num).toDouble()).toList();
}

/// First candidate key holding at least one non-null value.
///
/// The "at least one non-null" part is load-bearing, not defensive. Open-Meteo
/// returns a correctly-NAMED array full of nulls when a model does not publish
/// a variable under a given alias, and a non-empty list of nulls passes an
/// `isNotEmpty` check — so a plain presence test latches onto the empty series
/// and never tries the working key. In Python that cost the live deployment
/// every Day+0 ECMWF wind score for months, with no error raised anywhere.
/// Mirrors `pick_series` in the Python implementation.
List<double?> pickSeries(Map<String, Object?> h, List<String> candidateKeys) {
  for (final key in candidateKeys) {
    final value = h[key];
    if (value is List && value.any((e) => e != null)) return _numList(value);
  }
  return const [];
}

/// Reads a per-model series, falling back to the un-suffixed key.
///
/// Open-Meteo names multi-model fields `{variable}_{model}`, but a
/// single-model response uses the bare `{variable}`. Both shapes appear in
/// practice, so both are handled — same as the Python implementation.
List<double?> _series(Map<String, Object?> h, String variable, String model) =>
    pickSeries(h, ['${variable}_$model', variable]);

/// Wind gusts, newest spelling first.
///
/// Open-Meteo accepts the legacy `windgusts_10m` alias for most models, but
/// under it `ecmwf_ifs025` returns an all-null series while every other model
/// returns real data. The current `wind_gusts_10m` spelling returns real ECMWF
/// data and identical values elsewhere (verified 2026-08-19 against the live
/// API). Both are tried so a stored response in either shape still reads.
List<double?> _windSeries(Map<String, Object?> h, String model, String legacy, String current) =>
    pickSeries(h, ['${current}_$model', '${legacy}_$model', current, legacy]);

/// Pulls each model's Day+0 prediction from hourly data. Onset comes from the
/// actual hour-by-hour series, which only exists at hourly resolution.
///
/// The critical distinction: an entirely absent or all-null precipitation
/// series means NO DATA (`rain: null`), while a present series that simply
/// never crosses the threshold is a genuine dry call (`rain: false`).
List<ModelPrediction> extractDay0PredictionsFromHourly(
  Map<String, Object?> hourlyMultiModel,
  List<String> models, {
  double threshold = rainThresholdMm,
}) {
  final hourly = hourlyMultiModel['hourly'];
  if (hourly is! Map<String, Object?> || hourly.isEmpty) return const [];

  final times = (hourly['time'] as List?)?.cast<String>() ?? const <String>[];

  return models.map((model) {
    final precip = _series(hourly, 'precipitation', model);
    final wind = _windSeries(hourly, model, 'windgusts_10m', 'wind_gusts_10m');
    final temp = _series(hourly, 'temperature_2m', model);
    final press = _series(hourly, 'pressure_msl', model);
    // No daily maximum exists at hourly resolution, so Day+0's is the highest
    // hour — the same quantity precipitation_probability_max serves at
    // Day+3/+7, derived here rather than served. See
    // ModelPrediction.rainProbabilityPct for why it is recorded before
    // anything scores it.
    final prob = _series(hourly, 'precipitation_probability', model);

    final hasPrecipData = precip.any((v) => v != null);
    final bool? rain =
        hasPrecipData ? precip.any((v) => (v ?? 0) >= threshold) : null;
    final onset = (rain == true)
        ? getOnsetHour(times, precip, threshold: threshold)
        : null;

    final windVals = wind.whereType<double>().toList();
    final tempVals = temp.whereType<double>().toList();
    final pressVals = press.whereType<double>().toList();
    final probVals = prob.whereType<double>().toList();

    return ModelPrediction(
      model: model,
      rain: rain,
      onset: onset,
      windKmh: windVals.isEmpty ? null : windVals.reduce((a, b) => a > b ? a : b),
      highC: tempVals.isEmpty ? null : tempVals.reduce((a, b) => a > b ? a : b),
      lowC: tempVals.isEmpty ? null : tempVals.reduce((a, b) => a < b ? a : b),
      mslpTrend:
          pressVals.length >= 2 ? pressVals.last - pressVals.first : null,
      // Absent, not 0 — an all-null series is no data, and 0% is a confident
      // claim that it will not rain.
      rainProbabilityPct: probVals.isEmpty
          ? null
          : probVals.reduce((a, b) => a > b ? a : b).toInt(),
      // Summed over hours that reported a value. An all-null day gives
      // null rather than 0.0 — "no data" and "no rain" are different
      // answers and the summary must not conflate them.
      precipMm: hasPrecipData
          ? (((precip.where((v) => v != null).fold<double>(0, (a, v) => a + v!)) * 100)
                  .roundToDouble() /
              100)
          : null,
    );
  }).toList();
}

/// Pulls each model's Day+N prediction from DAILY aggregates. No onset is
/// available at this resolution by design.
///
/// An index past a model's array means its forecast horizon does not reach
/// that far (UKMO stops around 7.2 days), which is recorded as unknown
/// (`rain: null`) — never as "no rain".
List<ModelPrediction> extractDayNPredictionsFromDaily(
  Map<String, Object?> dailyMultiModel,
  int dayIndex,
  List<String> models, {
  double threshold = rainThresholdMm,
}) {
  final daily = dailyMultiModel['daily'];
  if (daily is! Map<String, Object?> || daily.isEmpty) return const [];

  double? at(List<double?> arr, int i) =>
      i >= 0 && i < arr.length ? arr[i] : null;

  return models.map((model) {
    final precipArr = _series(daily, 'precipitation_sum', model);
    final windArr =
        _windSeries(daily, model, 'windgusts_10m_max', 'wind_gusts_10m_max');
    final highArr = _series(daily, 'temperature_2m_max', model);
    final lowArr = _series(daily, 'temperature_2m_min', model);
    final pressArr = _series(daily, 'pressure_msl_mean', model);
    // Fetched on every daily request since before this project scored
    // anything, and read by nothing until item 58.
    final probArr = _series(daily, 'precipitation_probability_max', model);

    final precip = at(precipArr, dayIndex);
    final prob = at(probArr, dayIndex);

    double? mslpTrend;
    if (dayIndex > 0 && dayIndex < pressArr.length) {
      final prev = pressArr[dayIndex - 1];
      final curr = pressArr[dayIndex];
      if (prev != null && curr != null) mslpTrend = curr - prev;
    }

    return ModelPrediction(
      model: model,
      rain: precip == null ? null : precip >= threshold,
      onset: null,
      windKmh: at(windArr, dayIndex),
      highC: at(highArr, dayIndex),
      lowC: at(lowArr, dayIndex),
      mslpTrend: mslpTrend,
      // The daily endpoint already gives a total, so this IS the same
      // quantity the Day+0 path sums by hand.
      precipMm: precip,
      // None, never 0 — see ModelPrediction.rainProbabilityPct.
      rainProbabilityPct: prob?.toInt(),
    );
  }).toList();
}
