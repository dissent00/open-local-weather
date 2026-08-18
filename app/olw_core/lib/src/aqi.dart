import 'models.dart';

/// Beyond this, a reading no longer represents current conditions. WAQI
/// stations commonly update hourly; a few hours' lag is ordinary latency,
/// but 3+ hours crosses from "a bit behind" into "not this morning's air".
const double staleThresholdHours = 3.0;

/// Age of a reading in hours, or `null` if it carries no timestamp at all —
/// unknown freshness, which is never treated as fresh.
double? hoursOld(GroundAqiReading reading, DateTime now) {
  final measured = reading.measuredAt;
  if (measured == null) return null;
  return now.difference(measured).inMicroseconds / Duration.microsecondsPerHour;
}

/// True if the reading is stale OR of unknown freshness.
///
/// Both are excluded from the confident range for the same reason; they are
/// only worded differently where they're surfaced to a reader.
bool isStale(GroundAqiReading reading, DateTime now) {
  final age = hoursOld(reading, now);
  return age == null || age > staleThresholdHours;
}

/// Deterministic range and worst-station summary across ground stations.
///
/// Returns `null` when no station has a numeric, sufficiently-fresh AQI —
/// whether because every station returned WAQI's "-" no-data sentinel, every
/// reading is too stale to trust, or the list is empty.
///
/// Stale readings are EXCLUDED from the range but still counted in
/// [GroundAqiSummary.stationsStale] and `stationsTotal`, so the site and
/// narrative can say "2 of 3 stations excluded as stale" rather than quietly
/// pretending those stations don't exist.
GroundAqiSummary? summarizeGroundAqi(
  List<GroundAqiReading> readings,
  DateTime now,
) {
  final staleCount =
      readings.where((r) => r.aqi != null && isStale(r, now)).length;
  final freshWithAqi =
      readings.where((r) => r.aqi != null && !isStale(r, now)).toList();
  if (freshWithAqi.isEmpty) return null;

  // Ties resolve to whichever station comes first — arbitrary but stable,
  // matching Python's max()/min() first-wins behaviour.
  var worst = freshWithAqi.first;
  var best = freshWithAqi.first;
  for (final r in freshWithAqi.skip(1)) {
    if (r.aqi! > worst.aqi!) worst = r;
    if (r.aqi! < best.aqi!) best = r;
  }

  return GroundAqiSummary(
    aqiMin: best.aqi!,
    aqiMax: worst.aqi!,
    highestStationName: worst.name,
    stationsWithAqi: freshWithAqi.length,
    stationsStale: staleCount,
    stationsTotal: readings.length,
  );
}
