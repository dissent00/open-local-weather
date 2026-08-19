// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'dates.dart';
import 'config.dart';
import 'extract.dart';
import 'llm/prompt.dart';
import 'llm/provider.dart';
import 'llm/schema.dart';
import 'models.dart';
import 'open_meteo.dart';

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
  String localBulletinSourceName = '',
  String localBulletinText = '',
  String? morningNarrative,
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

  // Future.wait rather than sequential awaits, so that when one required
  // fetch fails the other's rejection is still OBSERVED. Awaiting them one
  // at a time means the second future rejects with nobody listening, which
  // Dart surfaces as an unhandled async error — a crash log in an app, and
  // noise that obscures the actual failure.
  final required = await Future.wait([hourlyFuture, dailyFuture]);
  final hourly = required[0];
  final daily = required[1];
  final airQuality = await airQualityFuture;

  final day0 = extractDay0PredictionsFromHourly(hourly, models);
  final day3 = extractDayNPredictionsFromDaily(daily, 3, models);
  final day7 = extractDayNPredictionsFromDaily(daily, 7, models);

  final systemPrompt = buildSystemPrompt(location, isRefresh: morningNarrative != null);
  final userPrompt = buildUserPrompt(
    today: today,
    yesterday: addDays(today, -1),
    publicWebpageUrl: publicWebpageUrl,
    verificationContext: verificationContext,
    trackRecordContext: trackRecordContext,
    historicalLogs: historicalLogs,
    groundAqiReadings: groundAqiReadings,
    groundAqiSummary: groundAqiSummary,
    yesterdayActual: yesterdayActual,
    todayWeatherData: {
      'primary_today_hourly': hourly,
      'primary_extended_daily': daily,
      'secondary_today_hourly': null,
      'secondary_extended_daily': null,
      'regional_pressure': null,
      'air_quality': airQuality,
      'airport_metar': null,
    },
    localBulletinSourceName: localBulletinSourceName,
    localBulletinText: localBulletinText,
    morningNarrative: morningNarrative,
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
    day0Predictions: day0,
    day3Predictions: day3,
    day7Predictions: day7,
    systemPrompt: systemPrompt,
    userPrompt: userPrompt,
  );
}
