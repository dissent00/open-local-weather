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
  // THE TOP BAND NEEDS A CEILING TOO — item 83. It was 6.0 -> 99.0, so a
  // 6 degree change and a 25 degree frontal passage produced the same three
  // words. Nothing at this deployment has ever cleared 7 C, so the site hid
  // the bug rather than the code being right.
  (12.0, 'much'),
  (99.0, 'dramatically'),
];

/// Gust change bands, read the same way as [tempChangeBandsC]: the first
/// entry is the whole label, the rest are modifiers on "windier"/"calmer",
/// and an empty modifier means the bare word.
///
/// Below 8 km/h is not worth remarking on. Above it, everything used to be
/// one word — the same missing ceiling, one field over. Absolute rather than
/// proportional, matching the temperature bands.
const List<(double, String)> windChangeBandsKmh = [
  (8.0, 'similar winds'),
  (18.0, ''),
  (35.0, 'much'),
  (99.0, 'dramatically'),
];

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
  /// The three labels above, composed into finished sentences — item 83.
  /// This is what the PROMPT is given; the labels themselves stay in the
  /// record because that is what is stored and scored.
  final String? overviewComparison;

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
    this.overviewComparison,
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
        'overview_comparison': overviewComparison,
      };
}

/// Matches Python's `round(v, 1)`. NOT `(v * 10).roundToDouble() / 10`.
///
/// Two separate divergences, and the multiply causes both. `roundToDouble`
/// rounds half AWAY FROM ZERO where Python rounds half to EVEN — the same gap
/// `_roundHalfEven` in models.dart and `_fmt0` in synoptic.dart already guard.
/// Worse, multiplying by 10 INVENTS ties that the value does not have: -11.95
/// is stored as -11.94999999999999928, which Python rounds to -11.9, but
/// -11.95 * 10 lands exactly on -119.5 and rounds away to -12.0.
///
/// Measured 2026-09-08 by sweeping 32,001 values against Python: the old form
/// disagreed on 600 of them. Two crossed a band edge and changed the words a
/// reader sees — -11.95 read "much cooler" in Python and "dramatically cooler"
/// here, and -17.95 read "calmer" against "much calmer".
///
/// So: `toStringAsFixed` does the decimal rounding, because it works from the
/// double's true value rather than a scaled copy. It only differs from Python
/// on an EXACT tie, and a value that is exactly x.x5 must be an odd quarter —
/// k/20 is representable only when 5 divides k. Multiplying by 4 to test that
/// is exact, being a power of two, so this detects the tie without creating
/// one. Swept again after the change: 0 disagreements in 32,001.
double? _round1(double? v) {
  if (v == null) return null;

  final quarters = v * 4;
  if (quarters == quarters.roundToDouble() && quarters.abs() % 2 == 1) {
    final scaled = v * 10;
    final below = scaled.floorToDouble();
    return (below % 2 == 0 ? below : below + 1) / 10;
  }

  return double.parse(v.toStringAsFixed(1));
}

/// THE FIRST BAND IS THE WHOLE LABEL — "about the same", "similar winds" —
/// because a change too small to remark on has no direction worth naming.
/// Every band above it is a MODIFIER on [up] or [down], and an empty modifier
/// means the bare word.
String? _bandLabel(
  double? delta,
  List<(double, String)> bands,
  String up,
  String down,
) {
  if (delta == null) return null;

  final magnitude = delta.abs();
  final (noChangeThreshold, noChangeLabel) = bands.first;
  if (magnitude < noChangeThreshold) return noChangeLabel;

  final direction = delta > 0 ? up : down;
  for (final (threshold, modifier) in bands.skip(1)) {
    if (magnitude < threshold) return '$modifier $direction'.trim();
  }

  return '${bands.last.$2} $direction'.trim();
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
///
/// THE SUBSET IS SELF-SELECTED AND MAY HAVE ONE MEMBER, in which case this
/// returns that member's opinion under a name that says consensus. It is the
/// caller's job to have established that rain is expected at all before
/// asking when it starts — see [computeDayOverDay], and 2026-09-08.
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

  final votes = [for (final p in todayDay0Predictions) if (p.rain != null) p.rain!];
  final bool? todayRain =
      votes.isEmpty ? null : votes.where((v) => v).length > votes.length / 2;

  // Both days described by AMOUNT and TIMING, then compared — rather than by
  // whether any hour crossed 0.5 mm, which called a clear day with evening
  // storms "another wet day". todayRain above is still computed and still
  // stored, because it is what the accuracy record scores; it is simply no
  // longer what the reader is handed.
  final todayPrecip = mean([for (final p in todayDay0Predictions) p.precipMm]);
  // THE ONSET ANSWERS "WHEN", NEVER "WHETHER", so it is gated on the same
  // vote todayRainExpected reports, and the block can no longer contradict
  // itself. Measured 2026-09-08: of six models ecmwf alone forecast rain,
  // from 16:00 at 8.7 mm, against 0.3-0.7 mm elsewhere. The mean it dragged
  // to 2.12 banded as "largely dry" and the median of a ONE-MEMBER list named
  // the hour, so the phrase read "dry until evening showers today" beside
  // "today_rain_expected": false — and the forecaster resolved toward the
  // prose. The minority's storm still reaches the reader through the
  // convective block and Today's Forecast.
  final todayOnset =
      todayRain == true ? consensusOnset(todayDay0Predictions) : null;
  // Today has no thunder observation — it has not happened yet. Today's
  // convective risk is a forecast, and belongs to the hazard sections.
  final todayCharacter = describeDayRain(todayPrecip, todayOnset, null);
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
      // ONE STATEMENT, NOT TWO — item 83. This was "X today; yesterday was
      // Y", a second sentence smuggled into a slot that allows one, spending
      // the reader's opening words on a day already over. "after a Y day"
      // using the full character was tried and rejected because the phrases
      // vary in shape: "after a dry until evening thunderstorms day" is not
      // English. So YESTERDAY CONTRIBUTES ONE WORD, and thunder outranks the
      // band — 2026-08-24 thundered over the city and was reported the next
      // morning as "dry again", to readers who had stood in it.
      final yesterdaySummary = yesterdayActual.thunder == true
          ? 'thundery'
          : dayRainBand(yesterdayActual.precipMm);
      rainContrast = '$todayCharacter, after a $yesterdaySummary day';
    }
  }

  final highLabel = _bandLabel(highDelta, tempChangeBandsC, 'warmer', 'cooler');
  final windLabel =
      _bandLabel(windDelta, windChangeBandsKmh, 'windier', 'calmer');

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
    highLabel: highLabel,
    windLabel: windLabel,
    rainContrast: rainContrast,
    overviewComparison:
        describeDayOverDay(highLabel, windLabel, rainContrast),
  );
}


/// The whole day-over-day comparison as finished, punctuated sentences.
/// ROADMAP item 83 — THE COMPOSITION CONTRACT.
///
/// The three labels are computed in code and the prompt orders them used
/// verbatim. That half of the bargain works. The other half was never
/// written down: a phrase the model may not alter must be GRAMMATICAL WHERE
/// IT LANDS and must CARRY ITS OWN BASELINE, because the model has been
/// forbidden from fixing either. One real Overview, 2026-09-08:
///
///   "Slightly warmer and calmer today, with dry until evening showers
///    today; yesterday was largely dry — much the same through Friday..."
///
/// "dry until evening showers" is a sentence opener with no legal place
/// after "with"; two baselines are welded with neither stated (the labels
/// measure against YESTERDAY, the extended trend against TODAY); the
/// "; yesterday was" half is a second statement in a one-statement slot; and
/// "today" appears twice.
///
/// THE FIX IS NOT A PROMPT RULE. It was tried in this exact spot — see
/// `PROMPT_COMPARISON_FIELDS` in the Python — where a rule lost to a payload
/// supplying its own counter-example, and deleting the field is what worked.
/// Prompt rule 8 ALREADY told the model to give a non-fitting phrase its own
/// sentence, and the model still wrote "with dry until evening showers
/// today". A rule instructing a model to repair input it was told not to
/// alter is a rule against itself.
///
/// So code composes, because code is what knows the shape of the phrases it
/// wrote. What is left to the model is the judgement code cannot do: WHETHER
/// to lead with this at all. Three quiet labels do not make a quiet day —
/// nothing here measures the sky, the air quality or how it felt.
///
/// Returns null when there is nothing to compare, which is the prompt's
/// existing "omit it" signal and needs no new rule. THE RAIN PHRASE ALWAYS
/// GETS ITS OWN SENTENCE: it is written as a sentence opener and there is no
/// preposition it survives.
String? describeDayOverDay(
  String? highLabel,
  String? windLabel,
  String? rainContrast,
) {
  final quietHigh = tempChangeBandsC.first.$2;
  final quietWind = windChangeBandsKmh.first.$2;

  final moved = [
    for (final (label, quiet) in [(highLabel, quietHigh), (windLabel, quietWind)])
      if (label != null && label != quiet) label,
  ];

  final sentences = <String>[];
  if (moved.isNotEmpty) {
    // "than yesterday" ONCE, on the clause that owns the comparison. The
    // unmoved label is dropped rather than listed: "slightly warmer and
    // similar winds" is an enumeration of one fact and one non-fact.
    sentences.add('${moved.join(' and ')} than yesterday');
  } else if ((rainContrast == null || rainContrast.isEmpty) &&
      highLabel != null &&
      windLabel != null) {
    // All three quiet. This is the ONLY case that earns the phrase, and it is
    // a claim about three measurements, not about the day — so a MISSING
    // label withholds it too. A null wind label is absent data, not a quiet
    // wind, and the phrase would assert a baseline never measured.
    sentences.add('much like yesterday');
  }

  if (rainContrast != null && rainContrast.isNotEmpty) {
    sentences.add(rainContrast);
  }

  if (sentences.isEmpty) return null;

  return sentences
      .map((s) => '${s[0].toUpperCase()}${s.substring(1)}.')
      .join(' ');
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

