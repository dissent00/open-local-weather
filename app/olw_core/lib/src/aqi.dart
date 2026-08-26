// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
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


/// Python's `datetime.isoformat()`, reproduced exactly.
///
/// Dart's own `toIso8601String()` renders UTC as "...T05:30:00.000Z" while
/// Python renders "...T05:30:00+00:00". The shared vectors pin the Python
/// form, and this value is user-visible in the prompt, so the two
/// implementations have to agree character for character.
String _isoLikePython(DateTime value) {
  final utc = value.toUtc();
  String pad(int n, int width) => n.toString().padLeft(width, '0');

  final micros = utc.microsecond + utc.millisecond * 1000;
  final fraction = micros == 0 ? '' : '.${pad(micros, 6)}';

  return '${pad(utc.year, 4)}-${pad(utc.month, 2)}-${pad(utc.day, 2)}'
      'T${pad(utc.hour, 2)}:${pad(utc.minute, 2)}:${pad(utc.second, 2)}'
      '$fraction+00:00';
}

/// The most recent numeric ground reading, with its age.
///
/// Independent of freshness on purpose: this answers "when did anyone last
/// actually measure the air, and what did they get", which is a different
/// question from [summarizeGroundAqi]'s "what is it right now". Callers
/// decide which to state; [GroundAqiLastKnown.stale] carries what they need
/// to word it.
///
/// Readings with no timestamp are skipped entirely — "most recent" is a claim
/// about time, and one cannot be made about a reading whose time is unknown.
/// Ties resolve to the highest AQI, matching the worst-station rule already
/// used for the fresh range.
GroundAqiLastKnown? lastKnownGroundAqi(
  List<GroundAqiReading> readings,
  DateTime now,
) {
  final dated = readings
      .where((r) => r.aqi != null && r.measuredAt != null)
      .toList();
  if (dated.isEmpty) return null;

  var newest = dated.first.measuredAt!;
  for (final r in dated.skip(1)) {
    if (r.measuredAt!.isAfter(newest)) newest = r.measuredAt!;
  }

  final atNewest =
      dated.where((r) => r.measuredAt!.isAtSameMomentAs(newest)).toList();

  // Strictly greater keeps the first on a tie, matching Python's max().
  var worst = atNewest.first;
  for (final r in atNewest.skip(1)) {
    if (r.aqi! > worst.aqi!) worst = r;
  }

  return GroundAqiLastKnown(
    stationName: worst.name,
    aqi: worst.aqi!,
    measuredAt: _isoLikePython(newest),
    hoursOld: hoursOld(worst, now)!,
    stale: isStale(worst, now),
    stationsReporting: atNewest.length,
  );
}
