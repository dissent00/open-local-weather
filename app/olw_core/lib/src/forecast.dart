// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'daypart.dart';
import 'dates.dart';
import 'config.dart';
import 'extract.dart';
import 'instability.dart';
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
class ForecastRun {
  const ForecastRun({
    required this.response,
    required this.day0Predictions,
    required this.day3Predictions,
    required this.day7Predictions,
    required this.systemPrompt,
    required this.userPrompt,
  });

  final ForecastResponse response;
  final List<ModelPrediction> day0Predictions;
  final List<ModelPrediction> day3Predictions;
  final List<ModelPrediction> day7Predictions;

  /// Retained so a run can be inspected or replayed. On a metered device this
  /// is also what makes "why did it say that?" answerable without a re-run.
  final String systemPrompt;
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
  final now = nowLocal ?? DateTime.now();
  var resolvedIssuance = issuance;
  if (resolvedIssuance == null) {
    try {
      final sun = await client.fetchSunTimes(
          lat: location.lat, lon: location.lon, timezone: location.timezone);
      final reconciled = reconcileNow(
        now,
        sun['_serverDate'] as String?,
        (sun['utc_offset_seconds'] as num?)?.toInt(),
      );
      final daily = (sun['daily'] as Map?)?.cast<String, Object?>() ?? {};
      final rises = daily['sunrise'] as List?;
      final sets = daily['sunset'] as List?;
      resolvedIssuance = (rises == null || sets == null || rises.isEmpty || sets.isEmpty)
          // Polar night returns no sunrise or sunset at all. Not an error, and
          // not something to fail a forecast over.
          ? daypartWithoutSun(reconciled.now)
          : summarizeDaypart(
              reconciled.now,
              DateTime.parse(rises[0] as String),
              DateTime.parse(sets[0] as String),
              rises.length > 1 ? DateTime.parse(rises[1] as String) : null,
            );
    } catch (_) {
      // Losing the sun is no reason to discard the clock.
      resolvedIssuance = daypartWithoutSun(now);
    }
  }

  var resolvedForward = forwardHourly;
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

  final systemPrompt = buildSystemPrompt(
    location,
    isReissue: earlierToday != null && earlierToday.isNotEmpty,
    groundStationsConfigured: groundStationsConfigured,
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
    reviewContext: reviewContext,
    // The same extracted values that get scored, so the narrative and the
    // accuracy record describe one set of numbers rather than two.
    modelPredictionsContext: {
      'day0': day0.map((p) => p.toJson()).toList(),
      'day3': day3.map((p) => p.toJson()).toList(),
      'day7': day7.map((p) => p.toJson()).toList(),
    },
  );

  final response = await llm.generate(systemPrompt: systemPrompt, userPrompt: userPrompt);
  return ForecastRun(
    response: response,
    // The blend joins Day+0 as a peer of the models it synthesizes, so what
    // gets scored tomorrow includes the forecast this run actually produced
    // and not only the guidance that fed it. Day+0 only: today_properties is
    // a call about today, and there is no extended-range equivalent to score
    // until the outlook carries structured numbers too.
    day0Predictions: [...day0, blendPrediction(response.todayProperties)],
    day3Predictions: day3,
    day7Predictions: day7,
    systemPrompt: systemPrompt,
    userPrompt: userPrompt,
  );
}
