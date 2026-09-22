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

/// The precision the age is SHOWN at, everywhere it is shown — upstream
/// ROADMAP item 142, finding 5.
const int staleDisplayDecimals = 1;

/// True if the reading is stale OR of unknown freshness.
///
/// Both are excluded from the confident range for the same reason; they are
/// only worded differently where they're surfaced to a reader.
///
/// JUDGED AT DISPLAY PRECISION — upstream item 142, finding 5. The prompt
/// tells the forecaster stale means MORE THAN three hours, and the payload
/// handed it `"hours_old": 3.0, "stale": true`: the age was rounded to a
/// tenth for display and compared unrounded, so anything between 3.00 and
/// 3.05 showed as exactly the threshold and judged past it.
///
/// Rounding first makes the two agree by construction. `_roundHalfEven`
/// rather than Dart's `.round()` for the usual reason — see models.dart.
bool isStale(GroundAqiReading reading, DateTime now) {
  final age = hoursOld(reading, now);
  if (age == null) return true;
  final shown = _roundHalfEven(age * 10) / 10;
  return shown > staleThresholdHours;
}

/// Matches Python's `round()`, which is half-to-EVEN. Local to this file
/// because aqi.dart imports nothing from models.dart.
int _roundHalfEven(double v) {
  final floor = v.floorToDouble();
  final diff = v - floor;
  if (diff > 0.5) return floor.toInt() + 1;
  if (diff < 0.5) return floor.toInt();
  return floor.toInt().isEven ? floor.toInt() : floor.toInt() + 1;
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


/// A re-issue's readings, in which a fresher absence never replaces an older
/// measurement.
///
/// Confirmed live on 2026-08-22 in the pipeline this shares its logic with:
/// the morning run captured three stations with real values — one of them
/// 160, Unhealthy for Sensitive Groups — and the 11:00Z re-fetch returned the
/// same three with a null AQI. Storing those left the day showing three
/// nulls, and the most actionable number of the day was gone. Nothing had
/// failed; upstream simply had no composite AQI at that hour, which is
/// ordinary.
///
/// A station missing from [fresh] is treated the same as one that came back
/// null: a station whose fetch fails is dropped from the list, so absence IS
/// a failed fetch and cannot be told apart from one.
///
/// The kept reading keeps its ORIGINAL measuredAt, which is the point:
/// [hoursOld] and [isStale] then describe it honestly, and the narrative can
/// say the last real reading was 160 at midnight and is nine hours old.
///
/// Stations are matched on stationId, not name — `name` is the display label
/// and can be changed, stationId is the identity the source answers to. Order
/// follows [fresh], with stored-only stations appended.
List<GroundAqiReading> mergeGroundAqi(
  List<GroundAqiReading> stored,
  List<GroundAqiReading> fresh,
) {
  final storedById = {for (final r in stored) r.stationId: r};
  final merged = <GroundAqiReading>[];

  for (final reading in fresh) {
    final previous = storedById.remove(reading.stationId);
    if (reading.aqi == null && previous != null && previous.aqi != null) {
      merged.add(previous);
      continue;
    }

    merged.add(reading);
  }

  merged.addAll(storedById.values);
  return merged;
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
/// What the last-known block says when there is nothing to say.
///
/// [lastKnownGroundAqi] returns null when no reading carries BOTH a numeric
/// AQI and a timestamp, and that is THREE situations. The block asserted one —
/// "no station has a timestamped reading at all" — and on the commonest it is
/// false: upstream measured 11 of 43 stored days with no numeric AQI from any
/// station, and on the two inside the prompt archive every station carried a
/// timestamp and an age while the block denied it. Upstream item 163.
///
/// THE INSTRUCTION RIDES IN THE BLOCK, not in the system prompt's air-quality
/// rule. A deployment whose stations are reliable never reaches this branch,
/// so a rule sentence would cost it characters on every run for a case it
/// never hits.
///
/// Nothing here is local: a station feeding particulates while the aggregator
/// has not computed an index is how WAQI reports, not how one place's sensors
/// behave.
const String lastKnownNoStations =
    'Unavailable — no station reported at all. Air quality comes from the '
    'model guidance alone; say so plainly rather than going silent.';
const String lastKnownNoNumericAqi =
    'Unavailable — the stations are reporting but none of them carried a '
    'numeric AQI. Say that, and take the figure from the model guidance. They '
    'are NOT down and NOT absent: their own readings, with their timestamps '
    'and ages, are in GROUND AQI STATIONS above. Do not convert a PM figure '
    'into an AQI yourself.';
const String lastKnownNoTimestamp =
    'Unavailable — a station reported a numeric AQI but none of those readings '
    'carries a timestamp, so there is no most-recent to name. Quote the value '
    'without claiming when it was taken.';

/// Which kind of nothing [lastKnownGroundAqi] found.
///
/// THE ORDER OF THE BRANCHES IS THE POINT. "No numeric AQI" is tested before
/// "no timestamp" because a reading can lack both, and of the two the missing
/// NUMBER is what stops the block having anything to quote.
/// A reading's AQI, whether it arrives typed or as a map.
///
/// BOTH SHAPES ARE REAL at this seam: the runner holds `GroundAqiReading`
/// objects, and the prompt layer is loosely typed — the vector fixtures feed
/// it plain maps. A version that only read `.aqi` worked in one and threw in
/// the other.
Object? _aqiValue(Object? reading) {
  if (reading is GroundAqiReading) return reading.aqi;
  if (reading is Map) return reading['aqi'];
  return null;
}

String lastKnownAbsence(Object? readings) {
  final list = readings is Iterable ? readings.toList() : const [];
  if (list.isEmpty) return lastKnownNoStations;
  if (!list.any((r) => _aqiValue(r) != null)) return lastKnownNoNumericAqi;
  return lastKnownNoTimestamp;
}

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
