// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The served call, built from code — upstream ROADMAP item 189.
///
/// Port of `code_call.py`; the reasoning lives there. The numbers a reader is
/// shown are the code blend's where it calls, the inputs' equal-weight
/// consensus where the record is too thin for it to, and never a model's
/// answer. Held to the Python by `spec/vectors/code_call.json` and the four
/// files beside it, because the app must serve the same call from the same
/// guidance.
///
/// THE SHAPE IS THE JUDGMENT CALL'S, so the app's store, the pipeline's entry
/// and the write-up read one shape whoever decided the numbers.
library;

import 'comparison.dart' show consensusOnset, dayRainBand, dryDayLabel, wetDayLabel;
import 'config.dart';
import 'llm/schema.dart';
import 'models.dart';
import 'rounding.dart';
import 'scoring.dart' show mean;
import 'synoptic.dart';
import 'verify.dart' show LeadTimeResult;

/// What decided the served numbers — stored as `call_source`.
const String callSourceCodeBlend = 'code_blend';
const String callSourceConsensus = 'consensus';

/// The tile's own ceiling, measured by the judgment prompt.
const int rainLabelMaxChars = 48;

/// The amount bands that read as isolated rather than as showers (under 5 mm).
const List<String> _isolatedBands = [dryDayLabel, 'largely dry'];

/// A composed phrase's time-of-day word, for a label — see `_LABEL_WORDS`.
const List<(String, String)> _labelWords = [
  ('morning', 'Morning'),
  ('afternoon', 'Afternoon'),
  ('evening', 'Evening'),
  ('night', 'Overnight'),
];

/// No model carried a high or a low. Mirrors `NoTemperatureToServe`.
class NoTemperatureToServe implements Exception {
  const NoTemperatureToServe(this.message);
  final String message;
  @override
  String toString() => 'NoTemperatureToServe: $message';
}

class ServedCall {
  const ServedCall({required this.judgment, required this.source});
  final JudgmentResponse judgment;
  final String source;
}

/// The time-of-day word a label carries, from a composed phrase.
String? labelWord(String? phrase) {
  if (phrase == null || phrase.isEmpty) return null;
  final lowered = phrase.toLowerCase();
  for (final (needle, word) in _labelWords) {
    if (lowered.contains(needle)) return word;
  }
  return null;
}

/// `rain_expected`: the call and roughly when, as a tile label. Mirrors
/// `rain_label`, including its vocabulary.
String rainLabel({
  required bool rain,
  required double? precipMm,
  required bool convective,
  required String? when,
}) {
  if (!rain) {
    if (!convective) return 'Dry / No Rain';
    return when != null ? 'Dry / $when Thunder Possible' : 'Dry / Thunder Possible';
  }

  final band = dayRainBand(precipMm);
  final amount = _isolatedBands.contains(band) ? 'Isolated ' : '';
  final kind = convective
      ? 'Showers & Thunderstorms'
      : (band == wetDayLabel ? 'Rain' : 'Showers');

  if (when != null) return '$amount$when $kind';
  if (amount.isNotEmpty) return '$amount$kind';
  return '$kind Likely';
}

int? _hourOf(String hhmm) => int.tryParse(hhmm.split(':').first);

/// `onset_window`: the spread of the wet models' onsets still ahead. Mirrors
/// `onset_window_label`: sorted, de-duplicated, "From HH:MM" when one.
String? onsetWindowLabel(List<String> onsets, {required int? issuedHour}) {
  final ahead = <String>{
    for (final o in onsets)
      if (o.isNotEmpty && (issuedHour == null || _hourOf(o) == null || _hourOf(o)! >= issuedHour)) o,
  }.toList()
    ..sort();
  if (ahead.isEmpty) return null;
  if (ahead.length == 1) return 'From ${ahead.first}';
  return '${ahead.first} – ${ahead.last}';
}

/// The day's peak US AQI from the CAMS hourly block, rounded like Python's
/// round(): half to even. Mirrors `cams_peak_aqi`.
int? camsPeakAqi(Map<String, Object?>? airQuality) {
  final hourly = (airQuality?['hourly'] as Map?)?.cast<String, Object?>();
  final values = [
    for (final v in (hourly?['us_aqi'] as List?) ?? const [])
      if (v != null) (v as num).toDouble(),
  ];
  if (values.isEmpty) return null;
  return roundLikePython(values.reduce((a, b) => a > b ? a : b), 0).toInt();
}

double? _round1(double? value) => value == null ? null : roundLikePython(value, 1);

/// Equal-weight majority and its share, with the blend's tie rule: dry.
(bool, int)? _consensusRain(List<ModelPrediction> models) {
  final votes = [for (final p in models) if (p.rain != null) p];
  if (votes.isEmpty) return null;
  final wet = votes.where((p) => p.rain!).length;
  return (wet * 2 > votes.length, roundLikePython(100 * wet / votes.length, 0).toInt());
}

/// The call the reader is shown, in the judgment call's shape. Mirrors
/// `served_call`; `codeBlend` is keyed by lead as `codeBlendPredictions`
/// returns it.
ServedCall servedCall({
  required List<ModelPrediction> day0Models,
  required List<ModelPrediction> day3Models,
  required List<ModelPrediction> day7Models,
  required Map<int, List<ModelPrediction>> codeBlend,
  required List<ModelPrediction> secondaryDay0,
  required double? calibratedGustKmh,
  required SynopticSnapshot? synoptic,
  required Map<String, Object?>? airQuality,
  required bool convective,
  required String? thunderWhen,
  required String? Function(String? hhmm)? onsetWordFor,
  required int? issuedHour,
  required List<String> inputs,
}) {
  final voters = [for (final p in day0Models) if (inputs.contains(p.model)) p];
  ModelPrediction? rowAt(int lead) {
    for (final p in codeBlend[lead] ?? const <ModelPrediction>[]) {
      if (p.model == codeBlendModelId) return p;
    }
    return null;
  }

  final blend = rowAt(0);
  final source = blend != null ? callSourceCodeBlend : callSourceConsensus;

  bool rain;
  int? probability;
  String? onset;
  double? precipMm;
  if (blend != null) {
    rain = blend.rain!;
    probability = blend.rainProbabilityPct;
    onset = blend.onset;
    precipMm = blend.precipMm;
  } else {
    final called = _consensusRain(voters);
    rain = called?.$1 ?? false;
    probability = called?.$2;
    onset = rain ? consensusOnset([for (final p in voters) if (p.rain == true) p]) : null;
    precipMm = _round1(mean([for (final p in voters) p.precipMm]));
  }

  var high = _round1(blend?.highC);
  high ??= _round1(mean([for (final p in voters) p.highC]));
  var low = _round1(blend?.lowC);
  low ??= _round1(mean([for (final p in voters) p.lowC]));
  if (high == null || low == null) {
    throw const NoTemperatureToServe('no model carried a high and a low for today');
  }

  final double? primaryGust;
  if (blend != null && blend.windKmh != null) {
    primaryGust = _round1(blend.windKmh);
  } else if (calibratedGustKmh != null) {
    primaryGust = _round1(calibratedGustKmh);
  } else {
    primaryGust = _round1(mean([for (final p in voters) p.windKmh]));
  }
  final secondaryGust = _round1(mean([for (final p in secondaryDay0) p.windKmh]));

  final wetOnsets = [
    for (final p in voters)
      if (p.rain == true && p.onset != null && p.onset!.isNotEmpty) p.onset!,
  ];
  var when = labelWord(
      (rain && onset != null && onset.isNotEmpty && onsetWordFor != null) ? onsetWordFor(onset) : null);
  if (!rain && convective) {
    when = labelWord(thunderWhen);
  }

  final trend = mean([for (final p in voters) p.mslpTrend]);

  final today = TodayProperties(
    rainExpected: rainLabel(rain: rain, precipMm: precipMm, convective: convective, when: when),
    onsetWindow: rain ? onsetWindowLabel(wetOnsets, issuedHour: issuedHour) : null,
    peakWindPrimaryKmh: primaryGust,
    peakWindSecondaryKmh: secondaryGust,
    tempHighC: high,
    tempLowC: low,
    rain: rain,
    onsetHour: onset,
    precipMm: precipMm,
    rainProbabilityPct: probability,
    mslpTrend24h: trend == null ? null : '${_signed1(trend)} hPa',
    synopticPattern: describePattern(synoptic),
    airQualityAqi: camsPeakAqi(airQuality),
  );

  final extended = <ExtendedDayProperties>[];
  for (final (lead, models) in [(3, day3Models), (7, day7Models)]) {
    final row = rowAt(lead);
    if (row != null && row.rain != null) {
      extended.add(ExtendedDayProperties(
          leadTimeDays: lead, rain: row.rain!, rainProbabilityPct: row.rainProbabilityPct));
      continue;
    }
    final called = _consensusRain([for (final p in models) if (inputs.contains(p.model)) p]);
    if (called == null) continue;
    extended.add(ExtendedDayProperties(leadTimeDays: lead, rain: called.$1, rainProbabilityPct: called.$2));
  }

  return ServedCall(
    judgment: JudgmentResponse(todayProperties: today, extendedProperties: extended),
    source: source,
  );
}

/// Python's `f"{x:+.1f}"`: a sign always, one decimal, half-to-even on the
/// decimal expansion.
String _signed1(double value) {
  final rounded = roundLikePython(value, 1);
  final text = rounded.abs().toStringAsFixed(1);
  // -0.0 prints as "-0.0" in Python's format too.
  final negative = rounded < 0 || (rounded == 0 && value < 0 && 1 / rounded < 0);
  return (negative ? '-' : '+') + text;
}

/// `yesterday_verification`, from the table the run just scored. Mirrors
/// `verification_summary`.
String verificationSummary(List<LeadTimeResult> leadTimeResults,
    {required List<String> visibleModels}) {
  final sentences = <String>[];
  for (final result in leadTimeResults) {
    final target = result.targetDateVerified;
    if (target == null) continue;
    final right = [
      for (final m in visibleModels)
        if (result.perModelScores.containsKey(m) && result.perModelScores[m]!.rainCorrect) m,
    ];
    final wrong = [
      for (final m in visibleModels)
        if (result.perModelScores.containsKey(m) && !result.perModelScores[m]!.rainCorrect) m,
    ];
    if (right.isEmpty && wrong.isEmpty) continue;
    final parts = <String>[];
    if (right.isNotEmpty) parts.add('${_join(right)} called the rain right');
    if (wrong.isNotEmpty) {
      parts.add(right.isNotEmpty ? '${_join(wrong)} did not' : '${_join(wrong)} missed the rain call');
    }
    final iso = target.toIso8601String().substring(0, 10);
    sentences.add('Day+${result.leadTimeDays} for $iso: ${parts.join('; ')}.');
  }
  return sentences.isEmpty ? 'No verification was possible this run.' : sentences.join(' ');
}

String _join(List<String> names) {
  if (names.length <= 1) return names.join();
  return '${names.sublist(0, names.length - 1).join(', ')} and ${names.last}';
}
