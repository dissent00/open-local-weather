// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'daypart.dart';
import 'solar.dart';
import 'dates.dart';
import 'comparison.dart';
import 'scoring.dart';
import 'config.dart';
import 'cycle.dart';
import 'extract.dart';
import 'instability.dart';
import 'llm/forecast_call.dart';
import 'llm/prompt.dart';
import 'llm/provider.dart';
import 'llm/schema.dart';
import 'models.dart';
import 'open_meteo.dart';
import 'synoptic.dart';

/// End-to-end forecast generation: fetch, extract, prompt, synthesise.
///
/// The app-side equivalent of the Python `pipeline.py` run, minus everything
/// that is a server concern — no git, no HTML publishing, no email, and no
/// met-service scraping (see APP_ARCHITECTURE.md for why that last one stays
/// on the server and arrives as data).
///
/// Deliberately takes its inputs rather than reaching for them:
/// `verificationContext` and `trackRecordContext` come from stored history,
/// which on a phone is a local database rather than JSON files on disk. A
/// first run passes empty ones, and that is not an error state — it is what
/// every new user's first forecast looks like, and the prompt has explicit
/// "Unavailable" handling for exactly it.
/// Stable identifiers for the degradations a run can record. Must match
/// `models.py`'s DEGRADATION_* constants — the app and the server publish
/// into the same vocabulary, and a code that differs by a character would
/// silently stop matching rather than fail.
const String degradationHoursAheadNarrowed = 'hours_ahead_narrowed';

/// THE WRITE-UP FAILED AND THE FORECAST DID NOT — upstream ROADMAP item 59
/// step 3. The only degradation that is not a missing INPUT: it names the
/// second of the two LLM calls failing after the first had already
/// succeeded. Losing the scored call because the prose blipped would put a
/// hole in the accuracy record to avoid publishing a short page.
const String degradationNarrative = 'narrative_unavailable';

/// One block the prompt expects that arrived absent or narrower than usual.
///
/// Port of `models.RunDegradation`; see that class for the full reasoning and
/// ROADMAP item 53.4 for the incident. The short version: a run that lost a
/// block used to look exactly like a run that did not, in the committed
/// record and on every surface a reader sees, and the gap was found because
/// someone was rained on.
///
/// TWO TEXTS, FOR TWO PLACES. [summary] is what a reader is shown at the top
/// of the forecast: plain, no jargon, and it says what the gap MEANS rather
/// than which fetch failed. [detail] is the technical account and belongs in
/// the notes at the end. The top of a forecast is where somebody decides
/// whether to go outside, and "the forward hourly window did not arrive"
/// tells that person nothing they can use.
///
/// [code] is matched on; both texts are written for people and may be
/// reworded freely.
class RunDegradation {
  const RunDegradation({
    required this.code,
    required this.summary,
    required this.detail,
  });

  final String code;
  final String summary;
  final String detail;

  Map<String, dynamic> toJson() =>
      {'code': code, 'summary': summary, 'detail': detail};

  factory RunDegradation.fromJson(Map<String, Object?> j) => RunDegradation(
        code: (j['code'] ?? '') as String,
        summary: (j['summary'] ?? '') as String,
        detail: (j['detail'] ?? '') as String,
      );
}

class ForecastRun {
  const ForecastRun({
    required this.response,
    required this.day0Predictions,
    required this.day3Predictions,
    required this.day7Predictions,
    required this.judgmentPrompt,
    required this.narrativePrompt,
    required this.userPrompt,
    required this.degradations,
  });

  final ForecastResponse response;
  final List<ModelPrediction> day0Predictions;
  final List<ModelPrediction> day3Predictions;
  final List<ModelPrediction> day7Predictions;

  /// What this run did not have. Empty means it looked and found nothing
  /// missing, which is a different answer from a stored record that never
  /// asked — see `models.LogEntryMeta.degradations` for why the persisted
  /// form on the server side is three-valued. Nothing is null here because
  /// this object is only ever produced BY a run, so the question was always
  /// asked; it is whatever stores it that has to keep "not recorded"
  /// distinguishable.
  final List<RunDegradation> degradations;

  /// Retained so a run can be inspected or replayed. On a metered device this
  /// is also what makes "why did it say that?" answerable without a re-run.
  ///
  /// TWO SYSTEM PROMPTS since upstream ROADMAP item 59 step 3. Kept apart
  /// rather than concatenated: the first question anyone asks of a surprising
  /// forecast is which of the two calls produced it, and a joined string
  /// cannot answer that.
  ///
  /// `userPrompt` is the JUDGMENT call's. The narrative call is sent this
  /// same message with THE FORECASTER'S CALL appended, which is recoverable
  /// from these three by `buildNarrativeUserPrompt`.
  final String judgmentPrompt;
  final String narrativePrompt;
  final String userPrompt;
}

/// Generates one forecast.
///
/// Fetches are issued concurrently — on a phone this is often a slow mobile
/// link, and four sequential round trips is the difference between a
/// responsive tap-to-generate and one that feels broken. The optional sources
/// degrade to null rather than failing the run, matching the Python
/// pipeline's treatment of air quality and the secondary point; the primary
/// hourly and daily fetches are required, and a failure there aborts.
/// The forecaster's own call at a lead beyond today — ROADMAP item 72.
///
/// RAIN ONLY: the one variable item 58's Brier can already score. Everything
/// else is absent rather than zero, exactly as the Day+0 blend treats peak
/// wind — a field the forecaster was not asked to commit to must not enter
/// the record as a value it never gave.
///
/// An empty result is a legitimate answer and not a gap. A guessed boolean is
/// scored wrong exactly as confidently as a real one, so a run that declined
/// to call a lead leaves no row for it.
///
/// Mirrors `_extended_blend_predictions` in the Python pipeline, pinned by
/// spec/vectors/extended_blend_predictions.json.
List<ModelPrediction> extendedBlendPredictions(
  List<ExtendedDayProperties> extended,
  int leadTimeDays,
) =>
    extended
        .where((e) => e.leadTimeDays == leadTimeDays)
        .map((e) => ModelPrediction(
              model: blendModelId,
              rain: e.rain,
              rainProbabilityPct: e.rainProbabilityPct,
              onset: null,
              precipMm: null,
              windKmh: null,
              highC: null,
              lowC: null,
              mslpTrend: null,
            ))
        .toList();

/// The forecaster's own Day+0 call, in the form the record can score.
///
/// Built from the STRUCTURED fields rather than parsed back out of the prose,
/// so what is scored is what the forecaster committed to rather than what a
/// regex could recover from a sentence. Mirrors `_blend_prediction` in the
/// Python pipeline.
ModelPrediction blendPrediction(TodayProperties tp) => ModelPrediction(
      model: blendModelId,
      rain: tp.rain,
      onset: tp.onsetHour,
      highC: tp.tempHighC,
      lowC: tp.tempLowC,
      precipMm: tp.precipMm,
      // The forecaster's own probability, carried into the scored row so it is
      // Brier-scored as a peer of the models it synthesizes — ROADMAP item 58.
      // This line was MISSING while the doc comment above claimed the function
      // mirrored the Python, so on the app path the probability was asked for
      // in the prompt, parsed by the schema, and never scored. Pinned now by
      // spec/vectors/blend_prediction.json, including a committed zero: `0` is
      // falsy in both languages, and a builder testing truthiness rather than
      // nullness turns "no chance of rain" into "declined to answer".
      rainProbabilityPct: tp.rainProbabilityPct,
      // Deliberately absent, not zero. peakWindKmh is the SECONDARY point's
      // and mslpTrend24h is prose; scoring either against the primary point's
      // observations would compare two different things, and a null reads as
      // "not forecast" everywhere in this record.
      windKmh: null,
      mslpTrend: null,
    );

Future<ForecastRun> generateForecast({
  required OpenMeteoClient client,
  required LlmProvider llm,
  required LocationConfig location,
  required DateTime today,
  required String publicWebpageUrl,
  List<String> models = defaultModels,
  Object? verificationContext = const <Object>[],
  Object? trackRecordContext = const <Object>[],
  Object? historicalLogs = const <Object>[],
  Object? reviewContext,
  Object? yesterdayActual,
  Object? groundAqiReadings,
  Object? groundAqiSummary,

  /// Whether this deployment polls ground AQI stations at all.
  ///
  /// Defaults to false because an app has none until someone configures
  /// them, and a deployment with no stations must not be told about a source
  /// it does not have — every ground-station passage and block drops out
  /// instead. Pass true (with the readings) once stations are configured.
  ///
  /// On a RE-ISSUE, merge the fresh readings against the stored ones with
  /// [mergeGroundAqi] before passing them: a re-fetch that comes back with no
  /// value must not erase a real reading the day's first run captured.
  bool groundStationsConfigured = false,

  /// The most recent real ground reading, with its age — what the narrative
  /// quotes when nothing is fresh enough for the summary above. Supplied by
  /// the caller alongside `groundAqiSummary`, which it computes the same way.
  Object? groundAqiLastKnown,
  String localBulletinSourceName = '',
  String localBulletinText = '',

  /// Whether a national met service is wired for this location.
  ///
  /// Derived from [localBulletinSourceName] rather than passed: a source with
  /// no name is not a source. False drops the LOCAL BULLETIN block and the
  /// peer-model guidance, and the system prompt states the absence once so no
  /// forecast is attributed to a service that was never consulted.
  /// Previous issuances today, oldest first. Empty or null means this is
  /// the day's first run.
  List<Map<String, Object?>>? earlierToday,

  /// Where this run sits in the day — see `daypart` in the Python pipeline.
  ///
  /// Left null on the normal path: this function derives it, mirroring what
  /// the Python pipeline does. Pass one only to pin the moment in a test.
  Object? issuance,

  /// Hourly guidance trimmed to the hours still ahead. Narrative only.
  /// Derived here when null, as above.
  Object? forwardHourly,

  /// The local wall-clock moment this run is issued at. Injectable so a test
  /// can pin it; defaults to now in the location's timezone.
  DateTime? nowLocal,
}) async {
  final hourlyFuture = client.fetchForecastHourlyToday(
    lat: location.lat, lon: location.lon, models: models, timezone: location.timezone,
  );
  final dailyFuture = client.fetchForecastDailyExtended(
    lat: location.lat, lon: location.lon, models: models, timezone: location.timezone,
  );
  // Optional: a missing air-quality response costs a section of the
  // narrative, not the forecast.
  final airQualityFuture = client
      .fetchAirQuality(lat: location.lat, lon: location.lon, timezone: location.timezone)
      .then<Map<String, Object?>?>((v) => v)
      .catchError((_) => null);
  // Also optional, and for the same reason. Without it the Synoptic Overview
  // has no large-scale picture to describe — the prompt is told to say so
  // rather than substituting the local gradient, which is what made that
  // section hollow before the ring existed.
  final synopticFuture = client
      .fetchSynopticPressure(lat: location.lat, lon: location.lon, timezone: location.timezone)
      .then<Map<String, Object?>?>((v) => v)
      .catchError((_) => null);

  // Future.wait rather than sequential awaits, so that when one required
  // fetch fails the other's rejection is still OBSERVED. Awaiting them one
  // at a time means the second future rejects with nobody listening, which
  // Dart surfaces as an unhandled async error — a crash log in an app, and
  // noise that obscures the actual failure.
  final required = await Future.wait([hourlyFuture, dailyFuture]);
  final hourly = required[0];
  final daily = required[1];
  final airQuality = await airQualityFuture;
  final synoptic = summarizeSynoptic(await synopticFuture);

  // Where this run sits in the day, and the hours still ahead of it.
  //
  // The clock, the sun, and the forward window fail SEPARATELY — see the
  // Python pipeline, where putting all three behind one try meant a failed
  // astronomical lookup also discarded the local time and the forward window,
  // and the prompt reported "time of day unavailable" for a run that knew
  // perfectly well what time it was.
  var now = nowLocal ?? DateTime.now();
  var resolvedIssuance = issuance;
  if (resolvedIssuance == null) {
    try {
      // Both of these ride on a response already fetched. The sun used to have
      // a request of its own; it is computed now, and the clock check that
      // shared that request moved onto this one — which is mandatory, so the
      // check is harder to lose than it was.
      final utcOffsetSeconds = (hourly['utc_offset_seconds'] as num?)?.toInt();
      final reconciled =
          reconcileNow(now, hourly['_serverDate'] as String?, utcOffsetSeconds);
      // The corrected clock has to reach the forward-window trim below as
      // well, or the two halves of the prompt describe different moments.
      now = reconciled.now;

      if (utcOffsetSeconds == null) {
        // The location's offset is the one input the computation cannot do
        // without, and Dart has no timezone database to fall back on. The
        // device's own offset would be a guess about where the user is
        // standing relative to the place they asked about.
        resolvedIssuance = daypartWithoutSun(now);
      } else {
        final sun = sunTimes(location.lat, location.lon, now, utcOffsetSeconds);
        // DateTime.utc for tomorrow's date, not DateTime: only the calendar
        // fields are read, and a local constructor can shift them. In a zone
        // whose clocks go back AT midnight — Santiago, Havana — DateTime(y, m,
        // d + 1) returns 23:00 on day d, so tomorrow's sunrise would silently
        // be today's.
        final next = sunTimes(location.lat, location.lon,
            DateTime.utc(now.year, now.month, now.day + 1), utcOffsetSeconds);
        resolvedIssuance =
            summarizeDaypart(now, sun.sunrise, sun.sunset, next.sunrise);
      }
    } catch (_) {
      // Losing the sun is no reason to discard the clock.
      resolvedIssuance = daypartWithoutSun(now);
    }
  }

  var resolvedForward = forwardHourly;
  var forwardWindowNarrowed = false;
  final degradations = <RunDegradation>[];
  if (resolvedForward == null) {
    try {
      resolvedForward = forwardHours(
        await client.fetchForecastHourlyForward(
            lat: location.lat, lon: location.lon, models: models,
            timezone: location.timezone),
        now,
      );
    } catch (_) {
      // The full calendar day is still supplied; this only costs near-term
      // hour-by-hour detail.
    }

    if (resolvedForward == null) {
      // THE DAY-0 FETCH ALREADY HAS THIS DATA. `fetchForecastHourlyToday`
      // asks the same host and the same endpoint for the same hourly
      // variables — cape included — differing only in forecast_days=1, and
      // it is awaited as a REQUIRED fetch above, so reaching this line means
      // it succeeded.
      //
      // Measured on the pipeline 2026-08-29 and 08-30: the forward call
      // read-timed out on three consecutive runs while the day-0 call
      // succeeded in every one, and the convective outlook was published as
      // "unavailable" with a 1830 J/kg UKMO peak sitting in memory. A reader
      // was rained on that evening. See ROADMAP item 53.
      //
      // WHAT IT DOES NOT COVER: forecast_days=1 stops at 23:00 local, so an
      // evening run sees this evening and nothing past midnight. That is the
      // peak worth warning about and it is not the whole window, which is why
      // the narrowing is flagged rather than passed off as a full one.
      resolvedForward = forwardHours(hourly, now);
      forwardWindowNarrowed = true;
      degradations.add(RunDegradation(
        code: degradationHoursAheadNarrowed,
        summary: "Part of tonight's data did not arrive. This forecast covers "
            'the rest of today only — where it says nothing about later '
            'tonight, that is missing information, not a quiet night.',
        detail: 'The forward hourly window did not arrive, so the hours-ahead '
            'guidance and the convective outlook were trimmed from the day-0 '
            'fetch instead, which stops at 23:00 local. '
            '${nextGuidanceSentence(nowLocal: now, utcOffsetSeconds: (hourly['utc_offset_seconds'] as num?)?.toInt())}',
      ));
    }
  }

  // From the trimmed forward window, never the calendar day: a CAPE peak that
  // already passed this morning is not a reason to warn about tonight.
  final instability = summarizeInstability(
    (resolvedForward as Map<String, Object?>?) ?? const <String, Object?>{},
    models,
  );

  final day0 = extractDay0PredictionsFromHourly(hourly, models);
  final day3 = extractDayNPredictionsFromDaily(daily, 3, models);
  final day7 = extractDayNPredictionsFromDaily(daily, 7, models);

  // ROADMAP item 61 — where the Overview learns to look past today.
  //
  // Days 1-3 from the SAME daily source and the SAME model list as the scored
  // day3 row above, so the clause a reader acts on and the number the record
  // scores cannot describe different weather. Consensus per day, then banded
  // in comparison.dart: the arithmetic lives in code and the prompt is handed
  // one finished phrase to use verbatim.
  //
  // Mirrors pipeline.py's call exactly, including which predictions feed
  // todayHighC: the models as extracted, with no baselines. Python appends
  // those AFTER this call, and a mean that included climatology would band a
  // different trend from the one the site publishes.
  final extendedDays = [
    for (final n in const [1, 2, 3]) extractDayNPredictionsFromDaily(daily, n, models),
  ];
  final extendedTrend = describeExtendedTrend(
    mean([for (final p in day0) p.highC]),
    [for (final d in extendedDays) mean([for (final p in d) p.highC])],
    [for (final d in extendedDays) mean([for (final p in d) p.precipMm])],
    weekdayName(addDays(today, 3)),
    // Wind at these leads was available and discarded, so a three-day build
    // in gusts under a flat temperature read as "much the same". It is also
    // what lets the clause say "conditions" rather than "temperatures".
    todayWindKmh: mean([for (final p in day0) p.windKmh]),
    dayWindsKmh: [for (final d in extendedDays) mean([for (final p in d) p.windKmh])],
  );

  // The app has no metadata fetch (fetch/model_run.py's HTTP call to
  // Open-Meteo's own meta.json), so guidance recency here is always the
  // DERIVED floor from cycle.dart, never OBSERVED — see cycle.py's
  // docstring for what that means. Still computed rather than reported
  // "Unavailable" every run: the derived floor costs nothing, and hiding a
  // number the app can produce for free would be a worse forecast than an
  // approximate one. `newer_than_previous_issuance` is always null — this
  // function holds no previous issuance to diff against; that comparison
  // belongs to whatever persists issuances on this side, not here.
  final alignedCycle = alignedCycleAt(now.toUtc());
  final guidanceRecency = {
    'models_last_aligned_at': _isoLikePython(alignedCycle.initialisedAt),
    'hours_old': roundHoursToTenths(alignedCycle.ageHours),
    'source': 'derived',
    'newer_than_previous_issuance': null,
  };

  final isReissue = earlierToday != null && earlierToday.isNotEmpty;
  final judgmentPrompt = buildJudgmentPrompt(
    location,
    isReissue: isReissue,
    groundStationsConfigured: groundStationsConfigured,
    localBulletinConfigured: localBulletinSourceName.isNotEmpty,
  );
  final narrativePrompt = buildNarrativePrompt(
    location,
    isReissue: isReissue,
    groundStationsConfigured: groundStationsConfigured,
    localBulletinConfigured: localBulletinSourceName.isNotEmpty,
  );
  final userPrompt = buildUserPrompt(
    today: today,
    yesterday: addDays(today, -1),
    publicWebpageUrl: publicWebpageUrl,
    verificationContext: verificationContext,
    trackRecordContext: trackRecordContext,
    historicalLogs: historicalLogs,
    groundAqiReadings: groundAqiReadings,
    groundAqiSummary: groundAqiSummary,
    groundAqiLastKnown: groundAqiLastKnown,
    groundStationsConfigured: groundStationsConfigured,
    localBulletinConfigured: localBulletinSourceName.isNotEmpty,
    instability: instability?.toJson(),
    yesterdayActual: yesterdayActual,
    todayWeatherData: {
      'primary_today_hourly': hourly,
      'primary_extended_daily': daily,
      'secondary_today_hourly': null,
      'secondary_extended_daily': null,
      'regional_pressure': null,
      'air_quality': airQuality,
      'airport_metar': null,
      // Derived in code, never handed over raw — the prompt is instructed to
      // use these labels and statements as given rather than working out
      // which quadrant is lowest by eye.
      'synoptic_scale_pressure': synoptic?.toJson(),
    },
    localBulletinSourceName: localBulletinSourceName,
    localBulletinText: localBulletinText,
    earlierToday: earlierToday,
    issuance: resolvedIssuance,
    forwardHourly: resolvedForward,
    forwardWindowNarrowed: forwardWindowNarrowed,
    reviewContext: reviewContext,
    // The same extracted values that get scored, so the narrative and the
    // accuracy record describe one set of numbers rather than two.
    modelPredictionsContext: {
      'day0': day0.map((p) => p.toJson()).toList(),
      'day3': day3.map((p) => p.toJson()).toList(),
      'day7': day7.map((p) => p.toJson()).toList(),
    },
    guidanceRecency: guidanceRecency,
    extendedTrend: extendedTrend,
  );

  // TWO CALLS since upstream ROADMAP item 59 step 3, and the doubling is
  // spend against the reader's own cap — see item 26.
  final call = await generateForecastResponse(
    provider: llm,
    judgmentPrompt: judgmentPrompt,
    narrativePrompt: narrativePrompt,
    userPrompt: userPrompt,
  );
  final response = call.response;

  // The write-up failed and the scored call did not. Recorded rather than
  // rethrown: the numbers are real, and losing them to publish nothing would
  // put a hole in the accuracy record — see [degradationNarrative].
  if (call.narrativeError != null) {
    degradations.add(RunDegradation(
      code: degradationNarrative,
      summary:
          "Today's figures are here, but the write-up that normally explains "
          'them could not be produced this time. The numbers are the same '
          'ones this forecast is scored on.',
      detail:
          'The rendering call failed after its retries while the judgment '
          'call had already succeeded, so the scored prediction was kept '
          'without a narrative: ${call.narrativeError}',
    ));
  }
  return ForecastRun(
    degradations: degradations,
    response: response,
    // The blend joins Day+0 as a peer of the models it synthesizes, so what
    // gets scored tomorrow includes the forecast this run actually produced
    // and not only the guidance that fed it. Day+0 only: today_properties is
    // a call about today, and there is no extended-range equivalent to score
    // until the outlook carries structured numbers too.
    day0Predictions: [...day0, blendPrediction(response.todayProperties)],
    // And joins Day+3 and Day+7 the same way — ROADMAP item 72's minimal
    // shape, rain and its probability, which is what Brier scores. Without
    // these the app recorded no forecaster call at the leads where the models
    // disagree most, which is the whole argument for asking at all.
    day3Predictions: [
      ...day3,
      ...extendedBlendPredictions(response.extendedProperties, 3),
    ],
    day7Predictions: [
      ...day7,
      ...extendedBlendPredictions(response.extendedProperties, 7),
    ],
    judgmentPrompt: judgmentPrompt,
    narrativePrompt: narrativePrompt,
    userPrompt: userPrompt,
  );
}

/// Python's `datetime.isoformat()`, reproduced exactly — same measured
/// finding as `_isoLikePython` in aqi.dart: Dart's `toIso8601String()` would
/// render UTC as "...T00:00:00.000Z", Python as "...T00:00:00+00:00", and
/// this value is stated verbatim in the prompt.
String _isoLikePython(DateTime value) {
  final utc = value.toUtc();
  String pad(int n, int width) => n.toString().padLeft(width, '0');

  final micros = utc.microsecond + utc.millisecond * 1000;
  final fraction = micros == 0 ? '' : '.${pad(micros, 6)}';

  return '${pad(utc.year, 4)}-${pad(utc.month, 2)}-${pad(utc.day, 2)}'
      'T${pad(utc.hour, 2)}:${pad(utc.minute, 2)}:${pad(utc.second, 2)}'
      '$fraction+00:00';
}
