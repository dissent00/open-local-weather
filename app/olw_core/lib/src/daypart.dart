/// Where the current moment sits in the day, as a human would feel it.
///
/// A faithful port of `openlocalweather/daypart.py` — see that file for the
/// full reasoning. In brief: the evening run read like an early-afternoon
/// summary because the prompt carried a date and no time, so the model could
/// not tell 06:00 from 18:00.
///
/// Phases are sun-relative rather than clock-based. Kisumu's sunset barely
/// moves across the year, but this is meant to be forked anywhere, and at 60°N
/// "18:00" is mid-afternoon in June and long dark in December.
///
/// Every figure here is computed rather than asked of a model, exactly as in
/// Python. Solar noon is the midpoint of sunrise and sunset, so no ephemeris
/// is needed. The two implementations are held together by
/// `spec/vectors/daypart.json`.
library;

// Deliberately no dart:io: this package runs on web, where it does not exist.
// That rules out HttpDate.parse, hence the small RFC-1123 parser below.

/// How long before sunset the light starts visibly going. Not an astronomical
/// quantity — civil twilight is defined after sunset — but the point at which
/// a person outdoors would say the evening is coming on.
const Duration duskLead = Duration(minutes: 90);

/// Sunrise is a moment; "early morning" is the stretch around it.
const Duration dawnLead = Duration(minutes: 60);
const Duration dawnTrail = Duration(minutes: 30);

/// How long after sunset it still reads as evening rather than night.
const Duration eveningLength = Duration(hours: 4);

/// Either side of solar noon that reads as "the middle of the day".
const Duration middayHalfWidth = Duration(hours: 2);

/// Beyond these the sun-relative phases stop meaning anything: under the
/// midnight sun there is no dusk to be 90 minutes before, and in polar night
/// there is no morning. Measured, not assumed — Open-Meteo reports
/// Longyearbyen in June as sunrise 00:00 with sunset exactly 24 hours later.
const Duration continuousDaylight = Duration(hours: 22);
const Duration continuousDark = Duration(hours: 2);

/// Periods named once, so "tonight" cannot be read as "this evening" by one
/// run and "the small hours" by the next.
const String tonight = 'tonight (dusk, evening and overnight through to dawn)';
const String today = 'today';
const String restOfToday = 'the rest of today';
const String tomorrow = 'tomorrow';
const String untilDawn = 'the remaining hours until dawn';

/// Used when sunrise and sunset are unavailable. Midnight is midnight at every
/// latitude, so this stays exactly true where "tonight" would be a guess.
const String restOfTodayToMidnight = 'the rest of today, through to midnight';

/// How far ahead a forecast issued now should look. Thirty hours so a run at
/// any time of day reaches through tonight and well into tomorrow.
const int forwardHoursCount = 30;

/// How far the system clock may drift from the server's before it is treated
/// as wrong rather than merely imprecise.
const Duration maxClockSkew = Duration(minutes: 5);

/// The moment a forecast is issued, reduced to things worth saying.
class DayPart {
  const DayPart({
    required this.localTime,
    required this.phase,
    required this.minutesSinceSunrise,
    required this.minutesToSunset,
    required this.sunrise,
    required this.sunset,
    required this.daylightHoursLeft,
    required this.statement,
    required this.horizon,
  });

  final String localTime;
  final String phase;
  final int? minutesSinceSunrise;
  final int? minutesToSunset;
  final String sunrise;
  final String sunset;

  /// Whole hours of daylight left, rounded DOWN: "2 hours of daylight left"
  /// should not be said with 2 hours and 5 minutes of it.
  final int daylightHoursLeft;

  /// A ready-made sentence for the prompt, so the arithmetic and the phrasing
  /// are both deterministic.
  final String statement;

  /// What a reader at this hour actually wants, most pressing first.
  final List<String> horizon;

  Map<String, Object?> toJson() => {
        'local_time': localTime,
        'phase': phase,
        'minutes_since_sunrise': minutesSinceSunrise,
        'minutes_to_sunset': minutesToSunset,
        'sunrise': sunrise,
        'sunset': sunset,
        'daylight_hours_left': daylightHoursLeft,
        'statement': statement,
        'horizon': horizon,
      };
}

/// Whole minutes, rounded half-UP by integer arithmetic.
///
/// NOT `.round()`. Dart rounds half away from zero and Python's round() is
/// half-to-even, so a duration with a 30-second remainder would come out a
/// minute apart in the two implementations — the same divergence that once put
/// "1006 hPa" on the site and "1007 hPa" in the app, which no behavioural test
/// catches because both answers look reasonable.
int _mins(Duration d) {
  final seconds = d.inSeconds;
  return seconds >= 0 ? (seconds + 30) ~/ 60 : -((-seconds + 30) ~/ 60);
}

String _hhmm(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

/// Durations as a person says them, not as a clock reports them.
String describeSpan(int minutes) {
  if (minutes < 1) return 'less than a minute';
  if (minutes < 60) return '$minutes minutes';
  final hours = minutes ~/ 60;
  final rem = minutes % 60;
  final hourWord = hours == 1 ? 'hour' : 'hours';
  return rem == 0 ? '$hours $hourWord' : '$hours $hourWord $rem minutes';
}

/// The phase of the day, by the sun rather than the clock.
String classifyPhase(DateTime now, DateTime sunrise, DateTime sunset) {
  final span = sunset.difference(sunrise);
  final solarNoon = sunrise.add(Duration(seconds: span.inSeconds ~/ 2));

  // Where the sun does not rise or set, fall back to position around solar
  // noon. The phases still order the day correctly; what changes is that the
  // statement stops talking about sunrise and sunset, because "3 hours until
  // sunset" during the midnight sun is worse than saying nothing.
  if (span >= continuousDaylight) {
    if (now.isBefore(solarNoon.subtract(middayHalfWidth))) return 'polar_morning';
    if (now.isBefore(solarNoon.add(middayHalfWidth))) return 'polar_midday';
    return 'polar_afternoon';
  }
  if (span <= continuousDark) return 'polar_night';

  if (now.isBefore(sunrise.subtract(dawnLead))) return 'night';
  if (now.isBefore(sunrise.add(dawnTrail))) return 'dawn';
  if (now.isBefore(solarNoon.subtract(middayHalfWidth))) return 'morning';
  if (now.isBefore(solarNoon.add(middayHalfWidth))) return 'midday';
  if (now.isBefore(sunset.subtract(duskLead))) return 'afternoon';
  if (now.isBefore(sunset)) return 'dusk';
  if (now.isBefore(sunset.add(eveningLength))) return 'evening';
  return 'night';
}

List<String> _horizonFor(String phase) => const {
      'polar_morning': [today, tonight],
      'polar_midday': [restOfToday, tonight],
      'polar_afternoon': [restOfToday, tonight, tomorrow],
      'polar_night': [today, tonight],
      'night': [untilDawn, today],
      'dawn': [today, tonight],
      'morning': [today, tonight],
      'midday': [restOfToday, tonight],
      'afternoon': [restOfToday, tonight, tomorrow],
      'dusk': [tonight, tomorrow],
      'evening': [tonight, tomorrow],
    }[phase]!;

/// One plain sentence placing the reader in the day.
///
/// Deliberately flat. An earlier draft read "it is 18:15 and the light is
/// going", which is more writing than the fact deserves and sets a register
/// the forecast then has to match or clash with.
String _statement(
  String phase,
  DateTime now,
  DateTime sunrise,
  DateTime sunset,
  DateTime? nextSunrise,
) {
  final t = _hhmm(now);
  if (phase.startsWith('polar_')) {
    return phase == 'polar_night'
        ? 'It is $t. The sun does not rise at this time of year.'
        : 'It is $t. The sun does not set at this time of year.';
  }
  if (phase == 'dawn') {
    // Dawn straddles sunrise, so the sentence has to know which side of it we
    // are on. "Sunrise is in 20 minutes" ten minutes after it happened is the
    // kind of error a reader spots by looking out of a window.
    return now.isBefore(sunrise)
        ? 'It is $t. Sunrise is in ${describeSpan(_mins(sunrise.difference(now)))}.'
        : 'It is $t. The sun rose at ${_hhmm(sunrise)}.';
  }
  if (phase == 'morning') {
    return 'It is $t. Sunrise was at ${_hhmm(sunrise)}, sunset is at ${_hhmm(sunset)}.';
  }
  if (phase == 'midday') return 'It is $t. Sunset is at ${_hhmm(sunset)}.';
  if (phase == 'afternoon' || phase == 'dusk') {
    return 'It is $t. Sunset is in ${describeSpan(_mins(sunset.difference(now)))}.';
  }
  if (phase == 'evening') return 'It is $t. The sun set at ${_hhmm(sunset)}.';
  // Night spans midnight, so after sunset the sunrise worth naming is
  // tomorrow's. Pointing at this morning's would be obviously wrong.
  final rise = (nextSunrise != null && now.isAfter(sunset)) ? nextSunrise : sunrise;
  return 'It is $t. Sunrise is at ${_hhmm(rise)}.';
}

/// Reduces the issuance moment to labels, a sentence and a horizon.
///
/// All times are LOCAL for the forecast location. Open-Meteo returns naive
/// local strings when `timezone=` is set, so these are naive too — mixing the
/// two would silently shift every phase boundary.
DayPart summarizeDaypart(
  DateTime now,
  DateTime sunrise,
  DateTime sunset, [
  DateTime? nextSunrise,
]) {
  final phase = classifyPhase(now, sunrise, sunset);
  final beforeSunrise = now.isBefore(sunrise);
  final afterSunset = now.isAfter(sunset);

  var daylightLeft = (afterSunset || beforeSunrise)
      ? Duration.zero
      : sunset.difference(now);
  // "Hours of daylight left" is meaningless in both polar cases — either all
  // of them or none — so it is reported as zero and the statement carries the
  // meaning instead.
  if (phase.startsWith('polar_')) daylightLeft = Duration.zero;

  return DayPart(
    localTime: _hhmm(now),
    phase: phase,
    minutesSinceSunrise: beforeSunrise ? null : _mins(now.difference(sunrise)),
    minutesToSunset: afterSunset ? null : _mins(sunset.difference(now)),
    sunrise: _hhmm(sunrise),
    sunset: _hhmm(sunset),
    daylightHoursLeft: daylightLeft.inHours,
    statement: _statement(phase, now, sunrise, sunset, nextSunrise),
    horizon: _horizonFor(phase),
  );
}

/// The issuance moment when sunrise and sunset could not be fetched.
///
/// The clock is not the sun: reading the system clock cannot fail over the
/// network, while sunrise and sunset can, so losing the second is no reason to
/// discard the first.
///
/// Phase is "unknown" rather than guessed — inferring dusk from a clock
/// reading is precisely what this module exists to avoid. The horizon stays
/// precise anyway, because midnight is midnight at every latitude.
DayPart daypartWithoutSun(DateTime now) => DayPart(
      localTime: _hhmm(now),
      phase: 'unknown',
      minutesSinceSunrise: null,
      minutesToSunset: null,
      sunrise: '',
      sunset: '',
      daylightHoursLeft: 0,
      statement: 'It is ${_hhmm(now)}. Sunrise and sunset could not be '
          'retrieved for this location today, so treat the part of day as '
          'unknown.',
      horizon: const [restOfTodayToMidnight, tomorrow],
    );

/// Trims multi-model hourly data to the hours still ahead.
///
/// NARRATIVE ONLY. Nothing scored passes through here — per-model predictions
/// come from the untrimmed day-0 fetch, and must, because scoring a partial
/// day against a full day's observation would quietly reward a model for the
/// hours it was not asked about.
Map<String, Object?> forwardHours(
  Map<String, Object?>? hourlyMultiModel,
  DateTime now, {
  int hoursAhead = forwardHoursCount,
}) {
  if (hourlyMultiModel == null || hourlyMultiModel.isEmpty) {
    return hourlyMultiModel ?? {};
  }
  final h = hourlyMultiModel['hourly'];
  if (h is! Map) return hourlyMultiModel;
  final hourly = h.cast<String, Object?>();
  final times = hourly['time'];
  if (times is! List || times.isEmpty) return hourlyMultiModel;

  final currentHour = DateTime(now.year, now.month, now.day, now.hour);
  final keep = <int>[];
  for (var i = 0; i < times.length; i++) {
    final parsed = DateTime.parse(times[i] as String);
    if (!parsed.isBefore(currentHour)) keep.add(i);
    if (keep.length >= hoursAhead) break;
  }

  // No overlap at all means the data does not cover now — a stale cache or a
  // timezone mismatch. Returning it untrimmed would hand back the wrong day
  // dressed as the right one, so return an explicitly empty series.
  if (keep.isEmpty) {
    return {...hourlyMultiModel, 'hourly': {for (final k in hourly.keys) k: <Object?>[]}};
  }

  return {
    ...hourlyMultiModel,
    'hourly': {
      for (final entry in hourly.entries)
        entry.key: entry.value is List
            ? [
                for (final i in keep)
                  if (i < (entry.value as List).length) (entry.value as List)[i]
              ]
            : entry.value,
    },
  };
}

/// The local time to use, and a warning if the system clock cannot be trusted.
///
/// `DateTime.now()` renders the current instant, so the host's own timezone
/// setting is irrelevant. What it does depend on is the machine's clock being
/// right in absolute terms — an unsynced VM, a clock that drifted while the
/// host was suspended. That failure is silent: a forecast confidently written
/// for the wrong part of the day.
///
/// Every Open-Meteo response carries a `Date` header (the server's own UTC
/// clock) and `utc_offset_seconds` for the location. Together they reconstruct
/// local time without trusting this machine at all.
({DateTime now, String? warning}) reconcileNow(
  DateTime systemLocal,
  String? serverDateHeader,
  int? utcOffsetSeconds,
) {
  if (serverDateHeader == null ||
      serverDateHeader.isEmpty ||
      utcOffsetSeconds == null) {
    return (now: systemLocal, warning: null);
  }

  final serverUtc = parseHttpDate(serverDateHeader);
  // An unparseable header is not a reason to distrust the clock; it is a
  // reason to stop checking.
  if (serverUtc == null) return (now: systemLocal, warning: null);

  final serverLocal = serverUtc
      .add(Duration(seconds: utcOffsetSeconds))
      .toUtc();
  final naiveServerLocal = DateTime(serverLocal.year, serverLocal.month,
      serverLocal.day, serverLocal.hour, serverLocal.minute, serverLocal.second);

  final skew = naiveServerLocal.difference(systemLocal).abs();
  if (skew <= maxClockSkew) return (now: systemLocal, warning: null);

  return (
    now: naiveServerLocal,
    warning: 'System clock disagrees with the forecast server by '
        '${skew.inMinutes} minutes. Using the server\'s time. Check NTP on '
        'this host — a wrong clock silently produces a forecast written for '
        'the wrong part of the day.'
  );
}

const _months = {
  'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
  'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
};

/// Parses an RFC-1123 HTTP date, e.g. "Sat, 22 Aug 2026 04:00:00 GMT".
///
/// Hand-rolled because `HttpDate.parse` lives in dart:io, which does not exist
/// on web — and this package has to keep working there. Returns null rather
/// than throwing: a malformed header should stop the clock check, not a run.
DateTime? parseHttpDate(String value) {
  final m = RegExp(
    r'^[A-Za-z]{3},\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+'
    r'(\d{2}):(\d{2}):(\d{2})\s+GMT$',
  ).firstMatch(value.trim());
  if (m == null) return null;
  final month = _months[m.group(2)!];
  if (month == null) return null;
  return DateTime.utc(
    int.parse(m.group(3)!),
    month,
    int.parse(m.group(1)!),
    int.parse(m.group(4)!),
    int.parse(m.group(5)!),
    int.parse(m.group(6)!),
  );
}
