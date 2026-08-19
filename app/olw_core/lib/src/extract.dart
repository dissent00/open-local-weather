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

/// Reads a per-model series, falling back to the un-suffixed key.
///
/// Open-Meteo names multi-model fields `{variable}_{model}`, but a
/// single-model response uses the bare `{variable}`. Both shapes appear in
/// practice, so both are handled — same as the Python implementation.
List<double?> _series(Map<String, Object?> h, String variable, String model) {
  final withModel = h['${variable}_$model'];
  if (withModel is List && withModel.isNotEmpty) return _numList(withModel);
  return _numList(h[variable]);
}

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
    final wind = _series(hourly, 'windgusts_10m', model);
    final temp = _series(hourly, 'temperature_2m', model);
    final press = _series(hourly, 'pressure_msl', model);

    final hasPrecipData = precip.any((v) => v != null);
    final bool? rain =
        hasPrecipData ? precip.any((v) => (v ?? 0) >= threshold) : null;
    final onset = (rain == true)
        ? getOnsetHour(times, precip, threshold: threshold)
        : null;

    final windVals = wind.whereType<double>().toList();
    final tempVals = temp.whereType<double>().toList();
    final pressVals = press.whereType<double>().toList();

    return ModelPrediction(
      model: model,
      rain: rain,
      onset: onset,
      windKmh: windVals.isEmpty ? null : windVals.reduce((a, b) => a > b ? a : b),
      highC: tempVals.isEmpty ? null : tempVals.reduce((a, b) => a > b ? a : b),
      lowC: tempVals.isEmpty ? null : tempVals.reduce((a, b) => a < b ? a : b),
      mslpTrend:
          pressVals.length >= 2 ? pressVals.last - pressVals.first : null,
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
    final windArr = _series(daily, 'windgusts_10m_max', model);
    final highArr = _series(daily, 'temperature_2m_max', model);
    final lowArr = _series(daily, 'temperature_2m_min', model);
    final pressArr = _series(daily, 'pressure_msl_mean', model);

    final precip = at(precipArr, dayIndex);

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
    );
  }).toList();
}
