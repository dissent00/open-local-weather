// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Whether the hours ahead are convective enough to belong in the Overview.
///
/// WHY THIS IS IN CODE. The Overview is the one section most readers finish,
/// and it is a tight slot: one or two sentences opening with the day-over-day
/// comparison. Nothing competes for that space unless something decides it
/// may, and left to its own judgement the LLM put instability in Today's
/// Forecast and Severe Weather and left the Overview to the temperature.
///
/// Live case, 2026-08-26: afternoon CAPE peaked between 1100 and 2600 J/kg
/// across models, with UKMO and ICON both above 2000. The narrative discussed
/// it twice further down. The Overview said "similar warmth, calmer winds,
/// and dry again" — which a reader who stopped there would have taken as a
/// quiet day.
///
/// THE THRESHOLD IS DELIBERATELY THE LOW ONE. 1000 J/kg is where the prompt's
/// own guidance already says thunderstorms become supported. Over the Lake
/// Victoria basin the models under-forecast storms systematically, so a
/// higher bar would mean staying quiet about exactly the days worth warning
/// on.
///
/// MAX ACROSS MODELS, NEVER THE MEAN. One model at 2600 and three near zero
/// is a disagreement, and averaging it away is the failure this project
/// exists to avoid.
library;

import 'extract.dart';

/// Above this, a model is forecasting an atmosphere that supports
/// thunderstorms, and the Overview has to say so.
const double convectiveCapeThresholdJkg = 1000.0;

/// Upstream item 158 step 2: how many models must cross the threshold on a
/// coming day before its thunderstorms are "likely" rather than "possible".
///
/// AGREEMENT, NOT MAGNITUDE, and the record chose it — the measurement is
/// beside `CONVECTIVE_LIKELY_MODELS` in instability.py: the maximum CAPE over
/// the models separated nothing (0.42 thunder against a 0.43 base rate),
/// while three of four crossing 1000 J/kg verified rain-or-thunder on 0.82
/// and 0.88 of days at Day+0 and Day+1 against 0.40-0.80 for one or two, on
/// samples of two to eight.
const int convectiveLikelyModels = 3;
const String thunderPossible = 'possible';
const String thunderLikely = 'likely';

/// The thunder word for one coming day from the models' CAPE maxima: null
/// when no model crosses, [thunderPossible] for one or two,
/// [thunderLikely] at [convectiveLikelyModels] or more. A null value is a
/// model with no CAPE at this lead and is not counted either way. The caller
/// decides which models are in the list; the forecast keeps `best_match`
/// out because the tier was measured without it.
String? convectiveTier(
  List<double?> peakCapesJkg, {
  double threshold = convectiveCapeThresholdJkg,
}) {
  final crossing =
      peakCapesJkg.where((c) => c != null && c >= threshold).length;
  if (crossing == 0) return null;

  return crossing >= convectiveLikelyModels ? thunderLikely : thunderPossible;
}

/// Pre-computed convective outlook for the hours still ahead.
class InstabilityOutlook {
  final double peakCapeJkg;
  final String peakModel;
  final String? peakHour;
  final bool convective;
  final List<String> modelsAboveThreshold;
  final Map<String, double> peakCapeByModel;

  /// WHEN, not only how much — upstream item 158, step 1. The first hour
  /// any model crosses the threshold and the hour of the overall peak, as
  /// the local ISO times the hours carry: the window runs past midnight and
  /// a bare "HH:MM" cannot say which day it is on. [convectiveTiming] turns
  /// them into the Overview's words.
  final String? onsetAt;
  final String? peakAt;

  const InstabilityOutlook({
    required this.peakCapeJkg,
    required this.peakModel,
    required this.peakHour,
    required this.convective,
    required this.modelsAboveThreshold,
    required this.peakCapeByModel,
    this.onsetAt,
    this.peakAt,
  });

  Map<String, Object?> toJson() => {
        'peak_cape_jkg': peakCapeJkg,
        'peak_model': peakModel,
        'peak_hour': peakHour,
        'convective': convective,
        'models_above_threshold': modelsAboveThreshold,
        'peak_cape_by_model': peakCapeByModel,
        'onset_at': onsetAt,
        'peak_at': peakAt,
      };
}

/// Peak CAPE per model over the supplied hours, and whether any model crosses
/// into thunderstorm-supporting territory.
///
/// Returns `null` when no model supplied a usable CAPE series, so an absent
/// forecast reads as a gap rather than as a calm afternoon. Expects the window
/// to have been trimmed to the hours ahead already — a peak that happened this
/// morning is not a reason to warn anyone about this evening.
InstabilityOutlook? summarizeInstability(
  Map<String, Object?> hourlyMultiModel,
  List<String> models, [
  double threshold = convectiveCapeThresholdJkg,
]) {
  final hourly = hourlyMultiModel['hourly'];
  if (hourly is! Map<String, Object?> || hourly.isEmpty) return null;

  final times = (hourly['time'] as List?)?.cast<String>() ?? const <String>[];
  if (times.isEmpty) return null;

  final peakCapeByModel = <String, double>{};
  final peakHourByModel = <String, String>{};
  for (final model in models) {
    // Unsuffixed fallback covers a single-model fetch, where Open-Meteo omits
    // the suffix. pickSeries, not a plain lookup, because a named-but-null
    // series is the documented way this data goes silently missing.
    final series = pickSeries(hourly, ['cape_$model', 'cape']);
    double? best;
    String? bestTime;
    for (var i = 0; i < series.length && i < times.length; i++) {
      final value = series[i];
      if (value == null) continue;
      if (best == null || value > best) {
        best = value;
        bestTime = times[i];
      }
    }
    if (best == null) continue;

    peakCapeByModel[model] = best;
    peakHourByModel[model] = bestTime!;
  }

  if (peakCapeByModel.isEmpty) return null;

  var peakModel = peakCapeByModel.keys.first;
  for (final entry in peakCapeByModel.entries) {
    if (entry.value > peakCapeByModel[peakModel]!) peakModel = entry.key;
  }
  final peakTime = peakHourByModel[peakModel]!;

  // The first hour ANY model crosses, in time order: the earliest warning
  // the guidance supports, which is the one a reader plans around.
  String? onsetAt;
  for (var i = 0; i < times.length && onsetAt == null; i++) {
    for (final model in peakCapeByModel.keys) {
      final series = pickSeries(hourly, ['cape_$model', 'cape']);
      if (i < series.length && series[i] != null && series[i]! >= threshold) {
        onsetAt = times[i];
        break;
      }
    }
  }

  final above = peakCapeByModel.entries
      .where((e) => e.value >= threshold)
      .map((e) => e.key)
      .toList()
    ..sort();

  return InstabilityOutlook(
    peakCapeJkg: peakCapeByModel[peakModel]!,
    peakModel: peakModel,
    // The clock time alone; the date is today by construction and a bare
    // "HH:MM" is what every other timing field here carries.
    peakHour: peakTime.contains('T')
        ? peakTime.split('T').last.substring(0, 5)
        : null,
    convective: peakCapeByModel[peakModel]! >= threshold,
    modelsAboveThreshold: above,
    peakCapeByModel: peakCapeByModel,
    onsetAt: onsetAt,
    peakAt: peakTime,
  );
}
