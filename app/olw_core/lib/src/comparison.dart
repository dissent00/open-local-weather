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

  String? rainContrast;
  if (votes.isNotEmpty) {
    final y = yesterdayActual.rain;
    if (y && !todayRain!) {
      rainContrast =
          'drier than yesterday — yesterday saw rain, today is not expected to';
    } else if (!y && todayRain!) {
      rainContrast =
          'wetter than yesterday — yesterday was dry, rain is expected today';
    } else if (y && todayRain!) {
      rainContrast = 'rain expected again today, as it rained yesterday too';
    } else {
      rainContrast = 'dry again today, as it was dry yesterday';
    }
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
