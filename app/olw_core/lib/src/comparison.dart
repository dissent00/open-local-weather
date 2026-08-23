// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'models.dart';
import 'scoring.dart' show mean;

/// Deterministic day-over-day comparison for the narrative's Overview.
///
/// In code rather than the LLM because a live run got it wrong: asked to
/// compare 29.6°C against 29.5°C it wrote "about 1°C cooler" — a ten-fold
/// overstatement of the single sentence most readers actually act on.
///
/// Today's PUBLISHED high is the LLM's own blended call and doesn't exist at
/// prompt-building time, so the comparison is made against the MODEL
/// CONSENSUS, and what's handed over is a categorical BAND rather than a raw
/// delta. The band is stable across the small gap between consensus and the
/// final blend; a number would not be.

/// Felt-change bands: (upperBoundExclusive, label). Calibrated to human
/// perception, not instrument precision — roughly a degree is inside
/// day-to-day noise and shouldn't be announced as a change at all.
const List<(double, String)> tempChangeBandsC = [
  (1.5, 'about the same'),
  (3.0, 'slightly'),
  (6.0, 'noticeably'),
  (99.0, 'much'),
];

/// Gust change below this isn't worth remarking on.
const double windChangeThresholdKmh = 8.0;

class DayOverDayComparison {
  final double? yesterdayHighC;
  final double? yesterdayLowC;
  final bool? yesterdayRain;
  final double? yesterdayPeakWindKmh;
  final bool? todayRainExpected;
  final double? todayConsensusHighC;
  final double? todayConsensusLowC;
  final double? todayConsensusPeakWindKmh;
  final double? highDeltaC;
  final double? lowDeltaC;
  final double? windDeltaKmh;
  final String? highLabel;
  final String? windLabel;
  final String? rainContrast;

  const DayOverDayComparison({
    this.yesterdayHighC,
    this.yesterdayLowC,
    this.yesterdayRain,
    this.yesterdayPeakWindKmh,
    this.todayRainExpected,
    this.todayConsensusHighC,
    this.todayConsensusLowC,
    this.todayConsensusPeakWindKmh,
    this.highDeltaC,
    this.lowDeltaC,
    this.windDeltaKmh,
    this.highLabel,
    this.windLabel,
    this.rainContrast,
  });

  Map<String, Object?> toJson() => {
        'yesterday_high_c': yesterdayHighC,
        'yesterday_low_c': yesterdayLowC,
        'yesterday_rain': yesterdayRain,
        'yesterday_peak_wind_kmh': yesterdayPeakWindKmh,
        'today_rain_expected': todayRainExpected,
        'today_consensus_high_c': todayConsensusHighC,
        'today_consensus_low_c': todayConsensusLowC,
        'today_consensus_peak_wind_kmh': todayConsensusPeakWindKmh,
        'high_delta_c': highDeltaC,
        'low_delta_c': lowDeltaC,
        'wind_delta_kmh': windDeltaKmh,
        'high_label': highLabel,
        'wind_label': windLabel,
        'rain_contrast': rainContrast,
      };
}

double? _round1(double? v) =>
    v == null ? null : (v * 10).roundToDouble() / 10;

String? _bandLabel(double? delta, String warmer, String cooler) {
  if (delta == null) return null;
  final magnitude = delta.abs();
  for (final (threshold, word) in tempChangeBandsC) {
    if (magnitude < threshold) {
      return word == 'about the same'
          ? word
          : '$word ${delta > 0 ? warmer : cooler}';
    }
  }
  final (_, word) = tempChangeBandsC.last;
  return '$word ${delta > 0 ? warmer : cooler}';
}

/// What separates a wet day from a dry one with a shower in it.
///
/// `rainThresholdMm` (0.5) answers a different question — "did measurable
/// rain fall in any hour" — which is what per-model skill is scored on and
/// must not change. It is a poor description of a DAY: half a millimetre at
/// 20:00 and forty millimetres from dawn were both "rain", so the summary
/// called both "another wet day".
const List<(double, String)> dayRainBandsMm = [
  (1.0, 'dry'),
  (5.0, 'largely dry'),
  (15.0, 'showery'),
];
const String wetDayLabel = 'wet';

/// When rain arriving stops being a feature OF the day and becomes a feature
/// AT THE END of it.
const int eveningOnsetHour = 16;
const int afternoonOnsetHour = 12;

String? _onsetPhrase(String? onset) {
  if (onset == null || onset.isEmpty) return null;
  final hour = int.tryParse(onset.split(':').first);
  if (hour == null) return null;
  if (hour >= eveningOnsetHour) return 'evening';
  if (hour >= afternoonOnsetHour) return 'afternoon';
  return 'from the morning';
}

/// One phrase for the rain character of a day: how much, and when.
///
/// Null when there is no amount to reason from, so the caller omits the
/// comparison rather than guessing.
String? describeDayRain(double? precipMm, String? onset) {
  if (precipMm == null) return null;

  var band = wetDayLabel;
  for (final (threshold, word) in dayRainBandsMm) {
    if (precipMm < threshold) {
      band = word;
      break;
    }
  }
  if (band == 'dry') return 'dry';

  final when = _onsetPhrase(onset);

  // Timing only qualifies the wetter bands. "Largely dry from the morning"
  // reads as though the DRYNESS started in the morning.
  if (band == 'largely dry') {
    return when == 'evening' ? 'dry until evening showers' : 'largely dry';
  }
  if (when == 'evening') {
    return 'dry until ${band == wetDayLabel ? 'heavy evening rain' : 'evening showers'}';
  }
  if (when == 'afternoon') return '$band from the afternoon';
  // Morning onset, or none recorded: the band alone is the whole story.
  return band;
}

/// The median onset among models that expect rain, as "HH:MM".
///
/// Median rather than mean: one model calling dawn while three call evening
/// should not average into mid-afternoon — a shape of day none forecast.
String? consensusOnset(List<ModelPrediction> predictions) {
  final hours = <int>[];
  for (final p in predictions) {
    if (p.onset == null || p.onset!.isEmpty) continue;
    final h = int.tryParse(p.onset!.split(':').first);
    if (h != null) hours.add(h);
  }
  if (hours.isEmpty) return null;
  hours.sort();
  return '${hours[hours.length ~/ 2].toString().padLeft(2, '0')}:00';
}

/// Null when there is no observed record for yesterday — a gap must read as
/// a gap, not as a day with unremarkable weather.
DayOverDayComparison? computeDayOverDay(
  DailyActual? yesterdayActual,
  List<ModelPrediction> todayDay0Predictions,
) {
  if (yesterdayActual == null) return null;

  final consensusHigh = mean([for (final p in todayDay0Predictions) p.highC]);
  final consensusLow = mean([for (final p in todayDay0Predictions) p.lowC]);
  final consensusWind = mean([for (final p in todayDay0Predictions) p.windKmh]);

  double? delta(double? today, double? yesterday) =>
      (today == null || yesterday == null) ? null : _round1(today - yesterday);

  final highDelta = delta(consensusHigh, yesterdayActual.highC);
  final lowDelta = delta(consensusLow, yesterdayActual.lowC);
  final windDelta = delta(consensusWind, yesterdayActual.peakWindKmh);

  String? windLabel;
  if (windDelta != null) {
    windLabel = windDelta.abs() < windChangeThresholdKmh
        ? 'similar winds'
        : (windDelta > 0 ? 'windier' : 'calmer');
  }

  final votes = [for (final p in todayDay0Predictions) if (p.rain != null) p.rain!];
  final bool? todayRain =
      votes.isEmpty ? null : votes.where((v) => v).length > votes.length / 2;

  // Both days described by AMOUNT and TIMING, then compared — rather than by
  // whether any hour crossed 0.5 mm, which called a clear day with evening
  // storms "another wet day". todayRain above is still computed and still
  // stored, because it is what the accuracy record scores; it is simply no
  // longer what the reader is handed.
  final todayPrecip = mean([for (final p in todayDay0Predictions) p.precipMm]);
  final todayCharacter =
      describeDayRain(todayPrecip, consensusOnset(todayDay0Predictions));
  final yesterdayCharacter =
      describeDayRain(yesterdayActual.precipMm, yesterdayActual.onsetHour);

  String? rainContrast;
  if (todayCharacter != null && yesterdayCharacter != null) {
    // Reaches the reader almost verbatim — the prompt says to use this AS
    // GIVEN — so the wording is a user-facing decision, not an internal
    // label. Keep the two implementations in step; the shared vectors
    // enforce it.
    rainContrast = todayCharacter == yesterdayCharacter
        ? '$todayCharacter again, like yesterday'
        : '$todayCharacter today; yesterday was $yesterdayCharacter';
  }

  return DayOverDayComparison(
    yesterdayHighC: yesterdayActual.highC,
    yesterdayLowC: yesterdayActual.lowC,
    yesterdayRain: yesterdayActual.rain,
    yesterdayPeakWindKmh: yesterdayActual.peakWindKmh,
    todayRainExpected: todayRain,
    todayConsensusHighC: _round1(consensusHigh),
    todayConsensusLowC: _round1(consensusLow),
    todayConsensusPeakWindKmh: _round1(consensusWind),
    highDeltaC: highDelta,
    lowDeltaC: lowDelta,
    windDeltaKmh: windDelta,
    highLabel: _bandLabel(highDelta, 'warmer', 'cooler'),
    windLabel: windLabel,
    rainContrast: rainContrast,
  );
}
