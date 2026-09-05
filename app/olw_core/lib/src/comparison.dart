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
  // 1.0 °C is 1.8 °F. It was 1.5 (2.7 °F), so two days could differ by nearly
  // three Fahrenheit degrees and still be called the same — and on 2026-08-27
  // one did, with the page showing 90 °F yesterday and 92 °F today.
  (1.0, 'about the same'),
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
  /// Surfaced beside the derived label for the same reason todayRainExpected
  /// is: a raw observation is harder for the LLM to misread than a phrase
  /// alone. Null means no station observation, not "no thunder".
  final bool? yesterdayThunder;
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
    this.yesterdayThunder,
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
        'yesterday_thunder': yesterdayThunder,
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
const String dryDayLabel = 'dry';

const List<(double, String)> dayRainBandsMm = [
  (1.0, dryDayLabel),
  (5.0, 'largely dry'),
  (15.0, 'showery'),
];
const String wetDayLabel = 'wet';

/// The amount band alone — "dry", "largely dry", "showery", "wet".
///
/// Separated from [describeDayRain] because the two answer different
/// questions. The PHRASE also carries timing and thunder, and only ever
/// carries them for a day that has already happened.
String? dayRainBand(double? precipMm) {
  if (precipMm == null) return null;

  for (final (threshold, word) in dayRainBandsMm) {
    if (precipMm < threshold) return word;
  }

  return wetDayLabel;
}

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
String? describeDayRain(double? precipMm, String? onset, [bool? thunder]) {
  if (precipMm == null) return null;

  final band = dayRainBand(precipMm)!;
  final when = _onsetPhrase(onset);

  // Thunder outranks the amount. A storm that passes over the city and drops
  // half a millimetre is what the reader remembers about the day, and calling
  // that day "dry" to their face is how this project loses their trust — they
  // were standing outside in it. Measured case: 2026-08-24, told to readers
  // the next morning as "dry again".
  if (thunder == true) {
    if (band == 'dry') return 'dry but thundery';
    if (when == 'evening') return 'dry until evening thunderstorms';
    if (when == 'afternoon') return '$band with afternoon thunderstorms';
    return '$band with thunderstorms';
  }

  if (band == 'dry') {
    // The band edge was a cliff. 0.9 mm falling entirely at 17:00 read "dry";
    // 1.1 mm at 17:00 read "dry until evening showers". A fifth of a
    // millimetre should not redescribe the day, so timing qualifies the dry
    // band too — an onset exists only when some hour actually crossed the
    // rain threshold, which is a shower whatever the daily total.
    if (when == 'evening') return 'dry apart from a brief evening shower';
    if (when == 'afternoon') return 'dry apart from a brief afternoon shower';
    if (when == 'from the morning') return 'dry apart from an early shower';

    return 'dry';
  }

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
  // Today has no thunder observation — it has not happened yet. Today's
  // convective risk is a forecast, and belongs to the hazard sections.
  final todayCharacter =
      describeDayRain(todayPrecip, consensusOnset(todayDay0Predictions), null);
  final yesterdayCharacter = describeDayRain(
      yesterdayActual.precipMm,
      // observedOnset(), not onsetHour: a shower the reanalysis missed
      // entirely leaves onsetHour null, and the dry band's shower phrases are
      // reached by TIMING. Without this the description says "dry" for a day
      // the record scores as wet — the same contradiction, one layer down,
      // that item 42 was raised to fix.
      yesterdayActual.observedOnset(),
      yesterdayActual.thunder);

  String? rainContrast;
  if (todayCharacter != null && yesterdayCharacter != null) {
    // Reaches the reader almost verbatim — the prompt says to use this AS
    // GIVEN — so the wording is a user-facing decision, not an internal
    // label. Keep the two implementations in step; the shared vectors
    // enforce it.
    // NO RAIN NEWS IS NOT A SENTENCE. Two dry days running is the commonest
    // case here, and it was producing an Overview clause every single day
    // about weather that had not changed.
    //
    // SILENCE RATHER THAN "dry again", and the difference is not stylistic.
    // On 2026-08-29 the reanalysis recorded 0.0 mm, the airport reported -RA
    // at 19:00, and the forecast called the day dry to someone who had stood
    // in it — item 53.1a. "dry again" makes that same false claim; saying
    // nothing makes no claim at all, and the shower is still in the
    // verification notes and the detailed discussion where a reader looks it
    // up. "again" is then free for whatever genuinely recurs, which on a
    // pair of dry days is usually the instability rather than the rain.
    final todayBand = dayRainBand(todayPrecip);
    final bothDry = todayBand == dryDayLabel &&
        dayRainBand(yesterdayActual.precipMm) == dryDayLabel;

    if (bothDry &&
        todayCharacter == todayBand &&
        yesterdayActual.thunder != true) {
      // Null is already the prompt's "omit the comparison" signal.
      rainContrast = null;
    } else if (todayCharacter == yesterdayCharacter) {
      // The sentence this lands in already opens with a day-over-day
      // comparison, so appending ", like yesterday" said it twice.
      rainContrast = '$todayCharacter again';
    } else {
      rainContrast = '$todayCharacter today; yesterday was $yesterdayCharacter';
    }
  }

  return DayOverDayComparison(
    yesterdayHighC: yesterdayActual.highC,
    yesterdayLowC: yesterdayActual.lowC,
    yesterdayRain: yesterdayActual.rain,
    yesterdayThunder: yesterdayActual.thunder,
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


/// How far the three-day high has to move before "warming" is honest.
///
/// 2.0 C across the span, not a per-day drift. Item 23 is the measurement
/// behind the size: a live run asked to compare 29.6 C against 29.5 C called
/// it "about 1 C cooler", a ten-fold overstatement in the one sentence most
/// readers act on. The threshold has to clear ordinary day-to-day noise
/// outright — a false "warming trend" has a reader planning around a change
/// that is not there.
const double extendedTrendThresholdC = 2.0;

/// One finished phrase for the next three days, or null when the data is too
/// thin to say anything. ROADMAP item 61.
///
/// A FINISHED PHRASE, not a label, for the same reason [describeDayRain]
/// ships "dry again": the prompt uses it verbatim, so anything left for the
/// model to word is something the model can word wrong. "Wednesday through
/// Friday show a consistent trend" is what a flag produces — bureaucratic,
/// longer than the thing it replaces, and it says less than "much the same
/// through Friday".
///
/// A STEADY SPELL IS SAID, NOT SKIPPED. The absence of change is the planning
/// answer for someone choosing when to do a job, and a reader told nothing
/// has to go and check.
String? describeExtendedTrend(
  double? todayHighC,
  List<double?> dayHighsC,
  List<double?> dayPrecipMm,
  String lastDayName,
) {
  final highs = [
    for (final h in dayHighsC)
      if (h != null) h
  ];
  if (todayHighC == null || highs.isEmpty) return null;

  // The END of the span against today, not the mean. A reader planning three
  // days out wants to know where it ends up, and a warm-cool-warm sequence
  // averages into a steadiness none of the three days has.
  final delta = highs.last - todayHighC;

  final String trend;
  if (delta >= extendedTrendThresholdC) {
    trend = 'warming through $lastDayName';
  } else if (delta <= -extendedTrendThresholdC) {
    trend = 'cooling through $lastDayName';
  } else {
    trend = 'much the same through $lastDayName';
  }

  // Rain is reported only when it ARRIVES. A dry spell continuing is already
  // carried by "much the same", and a second clause saying so is the
  // enumeration item 48 was raised to stop.
  final anyWet = dayPrecipMm.any((p) => p != null && dayRainBand(p) != dryDayLabel);

  return anyWet ? '$trend, with rain becoming more likely' : trend;
}

