// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'daypart.dart';
import 'models.dart';
import 'rounding.dart';
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
/// ABSOLUTE wind level, as opposed to the change bands below.
///
/// Every other label in this file compares today against yesterday, so two
/// consecutive gales read "similar winds" and the reader is never told it is
/// dangerous. A warning threshold and a "normal" band are different things
/// and only the second needs a local record.
///
/// THE THRESHOLDS ARE NOAA'S, IN KNOTS, AND THEY APPLY TO GUSTS BY
/// DEFINITION. https://www.weather.gov/marine/faq — a Gale Warning is
/// "sustained surface winds, OR FREQUENT GUSTS, in the range of 34 knots to
/// 47 knots inclusive", and Storm and Hurricane Force are worded the same.
///
/// THIS REPLACED A LOCALLY-DERIVED LADDER. Beaufort is defined on sustained
/// wind, so a first attempt converted its boundaries with a gust factor
/// measured at this site — a constant fitted to one deployment, which is the
/// failure the temperature band ceiling already records. NOAA needs no
/// conversion because the standard covers gusts, so the constant is gone
/// rather than corrected.
///
/// Descriptors rather than warning-product names: "gusts reaching gale force"
/// reads to anyone, and "Gale Warning" is a US product this deployment does
/// not issue. The boundaries coincide with Beaufort's at 34, 48 and 64 knots.
///
/// Small Craft Advisory is regionally variable in the source (20-25 kt); the
/// Great Lakes figure is used, being the large-inland-water criterion and one
/// of the two that explicitly says "or frequent gusts".
const List<(int, String, String)> windWarningBandsKt = [
  // (knots at or above, descriptor, the NOAA product it corresponds to)
  (25, 'strong breeze', 'Small Craft Advisory (Great Lakes criterion)'),
  (34, 'gale force', 'Gale Warning'),
  (48, 'storm force', 'Storm Warning'),
  (64, 'hurricane force', 'Hurricane Force Wind Warning'),
];

const double knotsToKmh = 1.852;

/// NOAA's marine wind descriptor for a gust reading, or null below the lowest
/// band.
///
/// WHAT THIS SYSTEM HOLDS IS A DAILY PEAK, WHICH IS ONE GUST, and NOAA's
/// criterion is FREQUENT gusts. A single peak crossing 34 kt is not a Gale
/// Warning and nothing here may say it is — the wording that reaches a reader
/// is "gusts reaching gale force", a statement about a gust, which is what
/// was measured. Establishing "frequent" needs hourly wind, which the daily
/// comparison does not hold, so this over-warns relative to the standard.
///
/// It also cannot tell a convective downburst from a synoptic gale; NOAA
/// separates them with a Special Marine Warning.
String? windWarning(double? gustKmh) {
  if (gustKmh == null) return null;

  String? name;
  for (final (knots, descriptor, _) in windWarningBandsKt) {
    if (gustKmh >= knots * knotsToKmh) name = descriptor;
  }

  return name;
}

/// Cloud change bands, read the same way as [tempChangeBandsC].
///
/// THE UNIT IS EIGHTHS, EXPRESSED IN PERCENT. Sky cover is measured in oktas,
/// so the threshold below which a sky did not really change is ONE OKTA,
/// 12.5 points. Three oktas, 37.5 points, is a sky two whole categories away
/// on the NWS band table, which is "much". The standard's own resolution
/// converted — nothing invented and no local measurement.
const List<(double, String)> cloudChangeBandsPct = [
  (12.5, 'similar cloud'),
  (37.5, ''),
  (99.0, 'much'),
];

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

  /// The gust operand the LABEL was actually banded from — calibration.dart,
  /// and the reason the raw consensus above is kept beside it. A delta
  /// computed from one number and stored beside another cannot be checked
  /// afterwards, which is most of what the record is for. Null means no model
  /// had enough verified checks and the raw consensus was used.
  final double? todayCalibratedPeakWindKmh;
  /// Surfaced beside the derived label for the same reason todayRainExpected
  /// is: a raw observation is harder for the LLM to misread than a phrase
  /// alone. Null means no station observation, not "no thunder".
  final bool? yesterdayThunder;
  final double? highDeltaC;
  final double? lowDeltaC;
  final double? windDeltaKmh;
  final String? highLabel;
  final String? windLabel;

  /// Items 87, 65 and 83. The fourth measurement, and the one the operator's
  /// founding objection was about: three quiet vectors are not a quiet day.
  final String? cloudLabel;
  final String? rainContrast;
  /// The three labels above, composed into finished sentences — item 83.
  /// This is what the PROMPT is given; the labels themselves stay in the
  /// record because that is what is stored and scored.
  final String? overviewComparison;

  /// Where yesterday's observed values were taken — item 98. `yesterday_rain`
  /// is a reanalysis grid CELL about 9 km across and `yesterday_thunder` is
  /// one airport station; they disagreed on 4 of 14 days here, and nothing
  /// said they were different places. Carried so the prompt view can name the
  /// source beside each boolean; the comparison itself never reads it.
  final Map<String, String>? provenance;

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
    this.todayCalibratedPeakWindKmh,
    this.highDeltaC,
    this.lowDeltaC,
    this.windDeltaKmh,
    this.highLabel,
    this.windLabel,
    this.cloudLabel,
    this.rainContrast,
    this.overviewComparison,
    this.provenance,
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
        'today_calibrated_peak_wind_kmh': todayCalibratedPeakWindKmh,
        'high_delta_c': highDeltaC,
        'low_delta_c': lowDeltaC,
        'wind_delta_kmh': windDeltaKmh,
        'high_label': highLabel,
        'wind_label': windLabel,
        'cloud_label': cloudLabel,
        'rain_contrast': rainContrast,
        'overview_comparison': overviewComparison,
        'provenance': provenance,
      };
}

/// One decimal place, matching Python's `round(v, 1)`.
///
/// The reason this is not `(v * 10).roundToDouble() / 10` is in rounding.dart,
/// and it is not a nicety: the broken form disagreed with Python on 600 of
/// 32,001 swept values and on roughly 3.6% of realistic days, because these
/// deltas are computed from a MEAN over two to six models and a mean lands on
/// x.x5 constantly. Two of those crossed a band edge and changed the words a
/// reader sees.
double? _round1(double? v) => v == null ? null : roundLikePython(v, 1);

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
/// Whether a timing qualifier is still a FORECAST at [issuedHour].
///
/// Upstream ROADMAP item 118. Every timing phrase here says, in effect, "and
/// not before" — "dry until evening showers" asserts the hours before the
/// evening were dry. That is a forecast at 06:00 and a claim about the past
/// at 18:00, and nothing checked which one it was.
///
/// NOT "did it actually rain": this module holds no observations of a day in
/// progress. The rule is narrower — do not assert what a period was like once
/// that period has elapsed. Same discipline as "unknown is not false".
///
/// Measured case, 2026-09-12: the day's first issuance went out at 18:01 with
/// the blend's onset at 18:00, and this composed "dry until evening
/// thunderstorms" for a day GFS had already given 3.5 mm at 15:00. The prompt
/// locks the phrase VERBATIM, so no instruction could have repaired it.
///
/// [issuedHour] is null for a day that is OVER and described from
/// observations — yesterday's side — where the timing is a report rather than
/// a claim. Passed explicitly rather than defaulted so a caller must decide
/// which day it holds.
bool _onsetIsAhead(String? onset, int? issuedHour) {
  if (onset == null) return false;
  if (issuedHour == null) return true;

  final onsetHour = int.tryParse(onset.split(':').first);
  if (onsetHour == null) return false;

  return onsetHour > issuedHour;
}

String? describeDayRain(
  double? precipMm,
  String? onset,
  bool? thunder, {
  required int? issuedHour,
  ConvectiveTiming? thunderTiming,
  String? onsetWord,
  ObservedSoFar? observed,
  String? stationLabel,
}) {
  if (precipMm == null) return null;

  final band = dayRainBand(precipMm)!;
  final when = _onsetIsAhead(onset, issuedHour) ? (onsetWord ?? _onsetPhrase(onset)) : null;

  // Upstream item 158, step 1: what the station has ALREADY reported today
  // outranks the forecast's shape of the day, named with the station and its
  // reach. An observed onset is a fact, and "dry until evening showers"
  // beside a station that saw rain at 14:00 is the 2026-09-12 case.
  final report = _stationReport(observed, stationLabel);
  if (report != null) {
    if (report.rainReported) {
      if (thunder == true && thunderTiming != null) {
        final more = report.thunderReported ? 'more ' : '';
        return '${report.head}, with $more${describeConvectiveTiming(thunderTiming)}';
      }
      return report.head;
    }
    if (thunder == true && thunderTiming != null) {
      return '${report.head}, more possible ${thunderTiming.peak}';
    }
    return report.head;
  }

  // Thunder outranks the amount. A storm that passes over the city and drops
  // half a millimetre is what the reader remembers about the day, and calling
  // that day "dry" to their face is how this project loses their trust — they
  // were standing outside in it. Measured case: 2026-08-24, told to readers
  // the next morning as "dry again".
  if (thunder == true) {
    if (thunderTiming != null) {
      // With a time, the thunder is a clause of its own and the day keeps its
      // shape. "Dry by day" only when the thunder waits for the light to go.
      final lead = band == dryDayLabel
          ? (_thunderAfterDaylight(thunderTiming) ? 'dry by day' : 'dry')
          : _rainShape(band, when);
      return '$lead, with ${describeConvectiveTiming(thunderTiming)}';
    }
    if (band == 'dry') return 'dry but thundery';
    if (when == 'evening') return 'dry until evening thunderstorms';
    if (when == 'afternoon') return '$band with afternoon thunderstorms';
    return '$band with thunderstorms';
  }

  return _rainShape(band, when);
}

class _StationReport {
  const _StationReport(this.head, this.rainReported, this.thunderReported);
  final String head;
  final bool rainReported;
  final bool thunderReported;
}

/// The report, or null when the station has reported neither today or gave
/// no reach to date it by.
_StationReport? _stationReport(ObservedSoFar? observed, String? stationLabel) {
  final reach = observed?.reportedThrough;
  if (observed == null || reach == null || reach.isEmpty) return null;
  final label = stationLabel ?? 'the station';
  if (observed.precipitation == true) {
    return _StationReport('showers reported at $label as of $reach', true, observed.thunder == true);
  }
  if (observed.thunder == true) {
    return _StationReport('thunder reported at $label as of $reach', false, true);
  }
  return null;
}

bool _thunderAfterDaylight(ConvectiveTiming timing) {
  final word = timing.onset ?? timing.peak;
  return word == 'from the evening' ||
      word == 'this evening' ||
      word == 'overnight' ||
      word.startsWith('from tomorrow') ||
      word.startsWith('tomorrow');
}

/// The band with its timing — the phrase for a day without thunder, and the
/// lead of one with timed thunder.
String _rainShape(String band, String? when) {
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
/// What a day-over-day comparison is ABOUT at a given hour, or null when it
/// should not appear at all — upstream ROADMAP item 104, contract item 8.
const String comparisonSubjectToday = 'today';
const String comparisonSubjectTomorrow = 'tomorrow';

/// Where a day stops being mostly ahead. Local noon, and it is the day's own
/// midpoint rather than a round number that happens to look like one.
const int comparisonMorningEndsHour = 12;

/// Whether a day-over-day comparison is worth reading at this hour, and what
/// it should be about.
///
/// THE OPERATOR'S FIVE SCENARIOS are the specification: the day ahead at
/// 03:00 and 06:00, nothing at 15:00 or 18:00 — "I've already lived enough of
/// it that I don't care how it compares to yesterday" — and tomorrow from
/// sunrise at 20:00.
///
/// BOTH BOUNDARIES ARE THE DAY'S OWN. Noon separates a day mostly ahead from
/// one mostly lived. SUNSET is where "the day ahead" stops meaning today,
/// which is why 18:00 is suppressed and 20:00 is not on a day whose sun sets
/// at 18:39. A fixed evening hour would put that pivot in the wrong place
/// twice a year at latitude, and always for a fork somewhere else.
///
/// NULL WHEN THE CLOCK IS UNKNOWN, and null after sunset when there IS no
/// sunset — a missing boundary must not promote an afternoon into a
/// comparison.
String? comparisonSubject(int? issuedHour, {int? sunsetHour}) {
  if (issuedHour == null || issuedHour < 0 || issuedHour >= 24) return null;

  if (issuedHour < comparisonMorningEndsHour) return comparisonSubjectToday;

  if (sunsetHour != null && issuedHour > sunsetHour) {
    return comparisonSubjectTomorrow;
  }

  return null;
}

DayOverDayComparison? computeDayOverDay(
  DailyActual? yesterdayActual,
  List<ModelPrediction> todayDay0Predictions, {
  bool? todayConvective,
  required int? issuedHour,
  int? sunsetHour,
  double? calibratedWindKmh,
  List<ModelPrediction>? tomorrowPredictions,
  String? todayName,
  String? tomorrowName,
  DailyActual? todayActual,
  ObservedSoFar? observedSoFar,
  String? stationLabel,
  ConvectiveTiming? convectiveTiming,
  String? Function(String)? onsetWordFor,
}) {
  // The daypart gate — contract item 8. See comparisonSubject for why.
  final subject = comparisonSubject(issuedHour, sunsetHour: sunsetHour);
  if (subject == null) {
    return null;
  }

  // WHICH DAY THE NUMBERS DESCRIBE, and what the sentence calls it. After
  // sunset the subject is tomorrow, so the consensus must be built from
  // TOMORROW'S predictions — until this existed the gate said "tomorrow"
  // while the arithmetic went on averaging today's Day+0 row.
  //
  // NULL RATHER THAN A FALLBACK TO TODAY'S: a tomorrow comparison computed
  // from today's numbers against yesterday's observation means "Tuesday will
  // be cooler than Sunday", which is worse than silence.
  //
  // NAMES ARE OPTIONAL AND THE FALLBACK IS PLAINER, NOT WRONGER. Without a
  // weekday the sentence still says "today" and "tomorrow", unambiguous in
  // every case except the one the names exist for — a reader opening the page
  // the next morning.
  var baseline = yesterdayActual;
  var predictions = todayDay0Predictions;
  var baselineComparative = 'yesterday';
  var baselineSimilarity = 'yesterday';
  String? subjectPrefix;
  // Tomorrow has not started, so every hour of it is ahead of this issuance
  // and no timing qualifier in it is a claim about elapsed hours — see
  // _onsetIsAhead, whose null means exactly that the hour bound does not
  // apply.
  int? characterIssuedHour = issuedHour;

  if (subject == comparisonSubjectTomorrow) {
    if (tomorrowPredictions == null || tomorrowPredictions.isEmpty) {
      return null;
    }
    // AND THE BASELINE MOVES WITH THE SUBJECT. "Tomorrow against yesterday"
    // is a comparison nobody asked for: the 20:00 scenario is tomorrow
    // against TODAY'S daytime, which at that hour is closed and final. Only
    // the STATION observes today — archive-api serves the current day as
    // model output — so this baseline arrives already narrowed to what a
    // station can honestly supply, and wind, cloud and the rain amount are
    // absent by design. See observedBaseline upstream.
    baseline = todayActual;
    predictions = tomorrowPredictions;
    final todayPhrase = todayName != null ? 'today ($todayName)' : 'today';
    baselineComparative = '$todayPhrase was';
    baselineSimilarity = todayPhrase;
    subjectPrefix =
        tomorrowName != null ? '$tomorrowName will be ' : 'tomorrow will be ';
    characterIssuedHour = null;
  }

  // A GAP MUST READ AS A GAP, not a day with unremarkable weather — checked
  // HERE, after the subject has chosen which day is the baseline, rather than
  // on the parameter. Before the evening subject the two were the same thing;
  // now a 20:00 run with today's observations in hand and no record for
  // yesterday is a comparison that CAN be made, and testing the parameter
  // would have refused it.
  if (baseline == null) return null;

  final consensusHigh = mean([for (final p in predictions) p.highC]);
  final consensusLow = mean([for (final p in predictions) p.lowC]);
  final consensusWind = mean([for (final p in predictions) p.windKmh]);

  double? delta(double? today, double? yesterday) =>
      (today == null || yesterday == null) ? null : _round1(today - yesterday);

  // The sky — items 87, 65 and 83. Percent on both sides, so like for like:
  // the models forecast cloud_cover and the reanalysis observed it. The
  // station's eighths sit beside it as a cross-check, exactly as the
  // station's sustained wind sits beside the scored gust.
  final consensusCloud =
      mean([for (final p in predictions) p.cloudCoverPct]);
  final cloudDelta = delta(consensusCloud, baseline.cloudCoverPct);

  // THE GUST OPERAND IS THE CALIBRATED ONE — see calibration.dart for the
  // measurement, and for why the correction never touches the scored rows.
  // The label was reporting a model bias as weather: over 34 mornings it read
  // "calmer than yesterday" fourteen times and "windier" NOT ONCE, at a mean
  // delta of -8.31 km/h against an 8.0 km/h no-change band, so the band was
  // being cleared by the bias alone. Corrected, the mean is +0.24 km/h.
  //
  // THE RAW CONSENSUS IS NOT A FALLBACK ON PURPOSE — it is what a day with too
  // little verified history gets, because nothing has measured a bias to
  // remove there. Which was used is recorded, not inferred.
  final windForLabel = calibratedWindKmh ?? consensusWind;

  final highDelta = delta(consensusHigh, baseline.highC);
  final lowDelta = delta(consensusLow, baseline.lowC);
  final windDelta = delta(windForLabel, baseline.peakWindKmh);

  final votes = [for (final p in predictions) if (p.rain != null) p.rain!];
  final bool? todayRain =
      votes.isEmpty ? null : votes.where((v) => v).length > votes.length / 2;

  // Both days described by AMOUNT and TIMING, then compared — rather than by
  // whether any hour crossed 0.5 mm, which called a clear day with evening
  // storms "another wet day". todayRain above is still computed and still
  // stored, because it is what the accuracy record scores; it is simply no
  // longer what the reader is handed.
  final todayPrecip = mean([for (final p in predictions) p.precipMm]);
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
      todayRain == true ? consensusOnset(predictions) : null;
  // SYMMETRY. Today's side used to pass null always, on the reasoning that
  // today has no thunder OBSERVATION. True, and it made the comparison
  // structurally incapable of calling today thundery while yesterday always
  // could be, so every thundery yesterday manufactured a change — a live
  // Overview read "Largely dry, after a thundery day. Thunderstorms are
  // possible this evening", drawing a contrast and then denying it.
  //
  // Today does have a thunder signal: the convective flag. THE RULE IS
  // GENERAL — a dimension may enter this comparison only if BOTH days can be
  // measured on it.
  // Upstream ROADMAP item 118. Today is a day IN PROGRESS and its phrase is
  // composed from a forecast, so a timing qualifier whose hour has passed is
  // a claim about hours nobody here can see — see _onsetIsAhead.
  // Upstream item 158, step 1. The station's report and the thunder's
  // timing belong to a day IN PROGRESS — today's subject. Tomorrow has no
  // report yet and its thunder is not in the hours ahead's window.
  final inProgress = subject == comparisonSubjectToday;
  final todayCharacter = describeDayRain(
    todayPrecip,
    todayOnset,
    todayConvective,
    issuedHour: characterIssuedHour,
    thunderTiming: inProgress ? convectiveTiming : null,
    onsetWord: (inProgress && onsetWordFor != null && todayOnset != null) ? onsetWordFor(todayOnset) : null,
    observed: inProgress ? observedSoFar : null,
    stationLabel: stationLabel,
  );
  final yesterdayCharacter = describeDayRain(
      baseline.precipMm,
      // observedOnset(), not onsetHour: a shower the reanalysis missed
      // entirely leaves onsetHour null, and the dry band's shower phrases are
      // reached by TIMING. Without this the description says "dry" for a day
      // the record scores as wet — the same contradiction, one layer down,
      // that item 42 was raised to fix.
      baseline.observedOnset(),
      baseline.thunder,
      // The day is OVER and this is built from observations, so its timing is
      // a report rather than a claim and is never suppressed.
      issuedHour: null);

  String? rainContrast;
  // Hoisted because the keys are computed inside the block below and the
  // composed sentence needs to know whether they matched.
  var rainKeysMatch = false;
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
    // TEST WHAT THE SUMMARY REPORTS. This compared full CHARACTER phrases
    // while the else-branch reports only the BAND, so two days in one band
    // could be framed as a change: "Largely dry, after a largely dry day"
    // was reachable. The key is now the pair the summary is built from.
    final todayBand = dayRainBand(todayPrecip);
    final yesterdayBand = dayRainBand(baseline.precipMm);
    final todayKey = '$todayBand|${todayConvective == true}';
    final yesterdayKey = '$yesterdayBand|${baseline.thunder == true}';
    rainKeysMatch = todayKey == yesterdayKey;
    final bothDry = todayBand == dryDayLabel && yesterdayBand == dryDayLabel;

    if (bothDry &&
        todayCharacter == todayBand &&
        baseline.thunder != true) {
      // Null is already the prompt's "omit the comparison" signal.
      rainContrast = null;
    } else if (todayKey == yesterdayKey) {
      // The sentence this lands in already opens with a day-over-day
      // comparison, so appending ", like yesterday" said it twice.
      rainContrast = '$todayCharacter again';
    } else {
      // NOTHING ABOUT YESTERDAY HERE AT ALL. This slot has shed two backward
      // glances: "X today; yesterday was Y" was a second sentence smuggled
      // into a slot that allows one, and its replacement, ", after a Y day",
      // was still spending the Overview's opening on a day already over.
      //
      // Raised by the operator 2026-09-10 from that morning's live Overview:
      // "Much calmer than yesterday. Dry until evening thunderstorms, after a
      // thundery day."
      //
      // AND THE TAIL NAMED THE WRONG DIMENSION. This branch is reached only
      // when the days DIFFER, and that day they differed on the band —
      // yesterday measurably wet, today dry until the evening. The summary
      // word put thunder ahead of the band, so it named the one dimension
      // where the days AGREED. The lead sentence already carries "than
      // yesterday", and once per Overview is enough.
      //
      // Recurrence still reaches the reader through the "again" branch
      // above, which is the one case where yesterday is the news.
      rainContrast = todayCharacter;
    }
  }

  final highLabel = _bandLabel(highDelta, tempChangeBandsC, 'warmer', 'cooler');
  final cloudLabel =
      _bandLabel(cloudDelta, cloudChangeBandsPct, 'cloudier', 'clearer');
  final rainUnchanged = rainContrast != null && rainKeysMatch;
  final windLabel =
      _bandLabel(windDelta, windChangeBandsKmh, 'windier', 'calmer');

  return DayOverDayComparison(
    yesterdayHighC: baseline.highC,
    yesterdayLowC: baseline.lowC,
    yesterdayRain: baseline.rain,
    yesterdayThunder: baseline.thunder,
    yesterdayPeakWindKmh: baseline.peakWindKmh,
    todayRainExpected: todayRain,
    todayConsensusHighC: _round1(consensusHigh),
    todayConsensusLowC: _round1(consensusLow),
    todayConsensusPeakWindKmh: _round1(consensusWind),
    todayCalibratedPeakWindKmh: _round1(calibratedWindKmh),
    highDeltaC: highDelta,
    lowDeltaC: lowDelta,
    windDeltaKmh: windDelta,
    highLabel: highLabel,
    windLabel: windLabel,
    cloudLabel: cloudLabel,
    rainContrast: rainContrast,
    provenance: baseline.provenance == null
        ? null
        : Map<String, String>.from(baseline.provenance!),
    overviewComparison: describeDayOverDay(
      highLabel,
      windLabel,
      rainContrast,
      cloudLabel: cloudLabel,
      todayCharacter: todayCharacter,
      rainUnchanged: rainUnchanged,
      // THE WARNING TAKES THE CALIBRATED GUST TOO, and this is the consumer
      // where it matters most. Every other label here is relative and a shared
      // bias partly cancels; a warning is a LEVEL against NOAA's absolute
      // thresholds, so a gust 12 km/h low sits a whole band below where it
      // belongs and the day it matters is the day it stays silent.
      windWarningName: windWarning(windForLabel),
      baselineComparative: baselineComparative,
      baselineSimilarity: baselineSimilarity,
      subjectPrefix: subjectPrefix,
    ),
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
  String? rainContrast, {
  String? cloudLabel,
  String? todayCharacter,
  bool rainUnchanged = false,
  String? windWarningName,
  /// THE BASELINE IS NAMED TWICE BECAUSE ENGLISH NAMES IT TWICE — upstream
  /// item 104, contract item 8. "Warmer than yesterday" and "much like
  /// yesterday" take the same word; "warmer than today (Monday) WAS" and
  /// "much like today (Monday)" do not. The comparative needs a verb to place
  /// a day still in progress and the similarity form reads as a stammer with
  /// one, so both are passed rather than one plus a rule.
  String baselineComparative = 'yesterday',
  String baselineSimilarity = 'yesterday',
  /// The day the RAIN half is about. "Dry until evening showers." read at
  /// 20:00 on Monday is about Monday night to anyone not told otherwise. Null
  /// for a comparison about today, which has no ambiguity to resolve.
  String? subjectPrefix,
}) {
  final dimensions = [
    (highLabel, tempChangeBandsC.first.$2),
    (windLabel, windChangeBandsKmh.first.$2),
    (cloudLabel, cloudChangeBandsPct.first.$2),
  ];

  final moved = [
    for (final (label, quiet) in dimensions)
      if (label != null && label != quiet) label,
  ];

  final measured = dimensions.every((d) => d.$1 != null);
  final hasRain = rainContrast != null && rainContrast.isNotEmpty;

  String? lead;
  if (moved.isNotEmpty) {
    // "than yesterday" ONCE, on the clause that owns the comparison. The
    // unmoved label is dropped rather than listed: "slightly warmer and
    // similar winds" is an enumeration of one fact and one non-fact.
    lead = '${moved.join(' and ')} than $baselineComparative';
  } else if (measured && (rainUnchanged || !hasRain)) {
    // NOTHING MOVED ON ANY DIMENSION, so say that rather than reporting one
    // of them: an Overview opening "Largely dry with thunderstorms again"
    // tells the reader the rain is unchanged and says nothing about the
    // temperature or wind, which were unchanged too.
    //
    // A claim about the measurements, not the day, so a MISSING label
    // withholds it — a null wind label is absent data, not a quiet wind.
    lead = 'much like $baselineSimilarity';
  }

  final sentences = <String>[if (lead != null) lead];

  if (hasRain) {
    // The lead already made the comparison, so the rain half drops its own
    // "again" and simply describes today.
    final phrase = lead == 'much like $baselineSimilarity' && todayCharacter != null
        ? todayCharacter
        : rainContrast;
    // The day name goes on the rain half and NOT on the lead, which already
    // carries its own baseline — two day names in one breath read as two
    // forecasts.
    sentences.add(subjectPrefix != null ? '$subjectPrefix$phrase' : phrase);
  }

  if (windWarningName != null) {
    // A LEVEL, NOT A CHANGE, in its own sentence so nothing can suppress it.
    sentences.add('gusting to $windWarningName');
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
  String lastDayName, {
  double? todayWindKmh,
  List<double?>? dayWindsKmh,
}) {
  final highs = [
    for (final h in dayHighsC)
      if (h != null) h
  ];
  if (todayHighC == null || highs.isEmpty) return null;

  // The END of the span against today, not the mean. A reader planning three
  // days out wants to know where it ends up, and a warm-cool-warm sequence
  // averages into a steadiness none of the three days has.
  final delta = highs.last - todayHighC;

  // Rain is reported only when it ARRIVES. A dry spell continuing is already
  // carried by "much the same", and a second clause saying so is the
  // enumeration item 48 was raised to stop. Computed here rather than below
  // because the scope noun depends on it.
  final anyWet = dayPrecipMm.any((p) => p != null && dayRainBand(p) != dryDayLabel);

  // NAME THE SCOPE YOU ACTUALLY MEASURED, and widen it when you can. Wind was
  // available at these leads and discarded, so a three-day build in gusts
  // under a flat temperature read as "much the same". With it in,
  // "conditions" is honest when every measured dimension is steady.
  //
  // Cloud and convective risk are NOT available at these leads, so
  // "conditions" means temperature, wind and rain — wider than before and
  // narrower than the word suggests. It widens again when cloud lands.
  double? windDelta;
  if (todayWindKmh != null && dayWindsKmh != null) {
    final winds = [
      for (final w in dayWindsKmh)
        if (w != null) w
    ];
    if (winds.isNotEmpty) windDelta = winds.last - todayWindKmh;
  }

  final moving = <String>[];
  if (delta >= extendedTrendThresholdC) {
    moving.add('warming');
  } else if (delta <= -extendedTrendThresholdC) {
    moving.add('cooling');
  }

  // The same threshold the day-over-day comparison calls "not worth remarking
  // on", so one place does not report a change the other calls noise.
  if (windDelta != null && windDelta.abs() >= windChangeBandsKmh.first.$1) {
    moving.add(windDelta > 0 ? 'becoming windier' : 'becoming calmer');
  }

  // A LEVEL, NOT A TREND, for the same reason the day-over-day half needs
  // one: four dangerous days running are "conditions much the same".
  double? spanMax;
  for (final w in dayWindsKmh ?? const <double?>[]) {
    if (w != null && (spanMax == null || w > spanMax)) spanMax = w;
  }
  final spanWarning = windWarning(spanMax);

  final String trend;
  if (moving.isNotEmpty) {
    trend = '${moving.join(' and ')} through $lastDayName';
  } else {
    // A SCOPE NOUN CANNOT COVER WHAT THE TAIL IS ABOUT TO CONTRADICT.
    // "conditions much the same, with rain becoming more likely" denies
    // itself, and so does "winds much the same, with gusts reaching gale" —
    // steady and dangerous are both true of that wind, and welding them into
    // one clause reads as a mistake rather than as two facts.
    final String scope;
    if (windDelta == null || spanWarning != null) {
      scope = 'temperatures';
    } else if (anyWet) {
      scope = 'temperatures and winds';
    } else {
      scope = 'conditions';
    }
    trend = '$scope much the same through $lastDayName';
  }

  // ONE "with", however many things follow it.
  final tails = <String>[
    if (spanWarning != null) 'gusts reaching $spanWarning',
    if (anyWet) 'rain becoming more likely',
  ];

  return tails.isEmpty ? trend : '$trend, with ${tails.join(' and ')}';
}


/// The four fields the prompt is given, in the order Python lists them.
///
/// Upstream ROADMAP item 88, divergence 5. The stored comparison carries
/// seventeen fields and `provenance`; the prompt gets these four and a
/// rebuilt `observed_from`, and nothing else.
const List<String> promptComparisonFields = [
  'yesterday_rain',
  'yesterday_thunder',
  'today_rain_expected',
  'overview_comparison',
];

/// Which stored provenance key each exposed boolean's source comes from.
const Map<String, String> observedFieldSources = {
  'yesterday_rain': 'rain',
  'yesterday_thunder': 'thunder',
};

/// The labels and the booleans, never the numbers behind them — plus where
/// each observation was taken.
///
/// Port of `comparison.comparison_for_prompt`, which had no Dart counterpart
/// at all — upstream ROADMAP item 88, divergence 5.
///
/// WHAT IT LEAVES OUT IS THE POINT, and leaving it out is not a size saving.
/// `DayOverDayComparison.toJson()` emits all seventeen fields, including the
/// raw deltas. Two of those were deliberately removed from the prompt on
/// 2026-09-05: a rule telling the forecaster not to re-derive a comparison
/// cannot beat a payload that hands it the arithmetic to re-derive it WITH.
/// Handing over `toJson()` would restore exactly the counter-example the rule
/// was losing to, and it would look like passing the data through.
///
/// LATENT WHEN THIS WAS WRITTEN, because the app passes no `yesterdayActual`
/// and so composes no comparison. It is here so that whoever wires that block
/// finds a narrowing function rather than a `toJson()` that looks ready to
/// use.
///
/// `observed_from` is REBUILT rather than passed through: the stored
/// provenance is keyed by the observation's own field name (`rain`,
/// `thunder`) and the prompt sees the exposed names. It is omitted entirely
/// when nothing is stamped, rather than emitted empty — an empty map would
/// claim the sources were looked up and found absent.
Map<String, Object?>? comparisonForPrompt(Map<String, Object?>? comparison) {
  if (comparison == null) return null;

  final view = <String, Object?>{
    for (final k in promptComparisonFields)
      if (comparison.containsKey(k)) k: comparison[k],
  };

  final provenance = (comparison['provenance'] as Map?)?.cast<String, Object?>() ?? const {};
  final sources = <String, Object?>{
    for (final e in observedFieldSources.entries)
      if (provenance.containsKey(e.value) && view.containsKey(e.key))
        e.key: provenance[e.value],
  };
  if (sources.isNotEmpty) view['observed_from'] = sources;

  return view;
}
