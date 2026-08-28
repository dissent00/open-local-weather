// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Sunrise and sunset, computed rather than fetched.
///
/// A faithful port of `openlocalweather/solar.py` — read that file for why
/// this exists (six consecutive days of null sun times from a call that never
/// once succeeded on GitHub's runners), for the measured accuracy by latitude,
/// and for the two polar conventions. The two implementations are held
/// together by `spec/vectors/solar.json`.
///
/// EVERY EXPRESSION HERE IS SHAPED TO MATCH PYTHON'S, not merely to be
/// equivalent. `_radians` multiplies by a folded `pi / 180` because that is
/// what CPython's `math.radians` does; the obliquity term is squared by
/// multiplication in both. A last-bit difference would almost always vanish
/// under the truncation to whole minutes, but "almost always" is how the
/// 1006/1007 hPa divergence got published.
///
/// NAIVE DATETIMES. Sunrise and sunset come back as non-UTC [DateTime]s
/// carrying the location's wall clock, matching `daypart` and Python. They are
/// not instants: a device in another timezone reads the same fields. The one
/// place that leaks is a zone whose daylight saving starts exactly at
/// midnight, where `DateTime(y, m, d)` returns 01:00 — a hazard this package
/// already carries wherever it parses a local timestamp, not one introduced
/// here.
library;

import 'dart:math' as math;

/// The sun's centre when its upper limb sits on the horizon, refraction
/// included: half a degree of solar radius plus 34 arc minutes of standard
/// refraction. Open-Meteo's convention, and every published sunrise table's.
const double sunAltitudeAtRiseSetDeg = -0.833;

/// Julian Date of 2000-01-01 12:00 UT, the epoch NOAA's polynomials are in.
const double j2000 = 2451545.0;
const double daysPerJulianCentury = 36525.0;

/// Julian Date of the Unix epoch, 1970-01-01 00:00 UT.
const double unixEpochJd = 2440587.5;

const double secondsPerDay = 86400.0;
const int minutesPerDay = 1440;
const int minutesPerHour = 60;

/// The earth turns a degree every four minutes, which is what converts an
/// hour angle in degrees into a time either side of solar noon.
const double minutesPerDegreeOfRotation = 4.0;

/// Sunrise and sunset are found by fixed-point iteration: estimate the time,
/// take the sun's position AT that time, correct the estimate. Three passes
/// puts every latitude tested below a second of movement between passes.
const int refinementPasses = 3;

const int _microsecondsPerSecond = 1000000;
const int _microsecondsPerMinute = 60 * _microsecondsPerSecond;

double _radians(double deg) => deg * (math.pi / 180.0);

double _degrees(double rad) => rad * (180.0 / math.pi);

/// Sunrise and sunset for one local date, as naive local wall clock.
///
/// Whole minutes, truncated. The prompt only ever shows `HH:MM`, and flooring
/// is the one form of minute-taking Python and Dart cannot disagree about —
/// Python's `round()` is half-to-even and Dart's is half-away-from-zero.
class SunTimes {
  final DateTime sunrise;
  final DateTime sunset;

  const SunTimes(this.sunrise, this.sunset);
}

/// The equation of time in minutes, and the sun's declination in radians.
///
/// A transcription of NOAA's solar position equations. The coefficients are
/// published constants of the earth's orbit, not choices made here, so they
/// stay inline rather than each getting a name that repeats the number back.
/// [jd] is a Julian Date; `t` is Julian centuries since J2000.
({double equationOfTime, double declination}) _solarPosition(double jd) {
  final t = (jd - j2000) / daysPerJulianCentury;

  final geomMeanLong =
      _radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0);
  final geomMeanAnom = _radians(357.52911 + t * (35999.05029 - 0.0001537 * t));
  final eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);

  final equationOfCentre =
      math.sin(geomMeanAnom) * (1.914602 - t * (0.004817 + 0.000014 * t)) +
          math.sin(2 * geomMeanAnom) * (0.019993 - 0.000101 * t) +
          math.sin(3 * geomMeanAnom) * 0.000289;
  final trueLong = _degrees(geomMeanLong) + equationOfCentre;

  // The moon's ascending node, which wobbles the apparent position slightly.
  final node = _radians(125.04 - 1934.136 * t);
  final apparentLong = _radians(trueLong - 0.00569 - 0.00478 * math.sin(node));

  final meanObliquity = 23.0 +
      (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0;
  final obliquity = _radians(meanObliquity + 0.00256 * math.cos(node));

  final declination = math.asin(math.sin(obliquity) * math.sin(apparentLong));

  var y = math.tan(obliquity / 2.0);
  y = y * y;
  final equationOfTime = minutesPerDegreeOfRotation *
      _degrees(y * math.sin(2 * geomMeanLong) -
          2 * eccentricity * math.sin(geomMeanAnom) +
          4 * eccentricity * y * math.sin(geomMeanAnom) * math.cos(2 * geomMeanLong) -
          0.5 * y * y * math.sin(4 * geomMeanLong) -
          1.25 * eccentricity * eccentricity * math.sin(2 * geomMeanAnom));

  return (equationOfTime: equationOfTime, declination: declination);
}

double _julianDate(DateTime utc) =>
    (utc.microsecondsSinceEpoch / _microsecondsPerSecond) / secondsPerDay + unixEpochJd;

/// Half the length of the day, as an angle. Null where the sun never crosses
/// the horizon at all — either way round; the caller knows which from the
/// declination's sign against the latitude's.
double? _hourAngleDeg(double latDeg, double declination) {
  final lat = _radians(latDeg);
  final cosHa = (math.sin(_radians(sunAltitudeAtRiseSetDeg)) -
          math.sin(lat) * math.sin(declination)) /
      (math.cos(lat) * math.cos(declination));

  if (cosHa > 1.0 || cosHa < -1.0) {
    return null;
  }
  return _degrees(math.acos(cosHa));
}

double _minutesOfDay(DateTime t) =>
    t.hour * minutesPerHour + t.minute + t.second / minutesPerHour;

/// Solar noon expressed as the crossing within twelve hours of [noonUtc].
DateTime _wrapToNearest(DateTime noonUtc, double lon, double equationOfTime) {
  final minutesFromUtcMidnight =
      minutesPerDay / 2 - minutesPerDegreeOfRotation * lon - equationOfTime;
  var delta = minutesFromUtcMidnight - _minutesOfDay(noonUtc);
  delta = (delta + minutesPerDay / 2) % minutesPerDay - minutesPerDay / 2;
  return noonUtc.add(_minutes(delta));
}

/// A fractional number of minutes as a [Duration], to microsecond resolution.
///
/// Python's `timedelta(minutes=x)` rounds a fractional microsecond half to
/// even; this rounds half away from zero. The two can differ by one
/// microsecond, which is 16 orders of magnitude below the minute this value is
/// eventually truncated to.
Duration _minutes(double m) =>
    Duration(microseconds: (m * _microsecondsPerMinute).round());

/// The moment the sun crosses the meridian, nearest to the local noon of
/// [day].
///
/// "Nearest" rather than "on the same UTC date", because a timezone need not
/// match its longitude: Kiritimati keeps UTC+14 at 157 degrees WEST, so its
/// local noon and its solar noon sit on different UTC dates.
DateTime _solarNoonUtc(double lon, DateTime day, int utcOffsetSeconds) {
  // DateTime.utc as a carrier for a naive wall clock, then shifted off it by
  // the location's offset to get a real instant. Not the device's zone: this
  // must give the same answer on a phone in Nairobi and one in Toronto.
  final localNoon = DateTime.utc(day.year, day.month, day.day, 12);
  final noonUtc = localNoon.subtract(Duration(seconds: utcOffsetSeconds));

  var equationOfTime = _solarPosition(_julianDate(noonUtc)).equationOfTime;
  for (var i = 0; i < refinementPasses; i++) {
    final transit = _wrapToNearest(noonUtc, lon, equationOfTime);
    equationOfTime = _solarPosition(_julianDate(transit)).equationOfTime;
  }

  return _wrapToNearest(noonUtc, lon, equationOfTime);
}

/// A UTC instant as naive local time, truncated to the minute.
///
/// TRUNCATED, NOT ROUNDED — see the Python original. Open-Meteo truncates and
/// so does the Kenya Met Department bulletin the app prints beside its own
/// figures; both give Kisumu 18:47 on 2026-08-19 where rounding gives 18:48.
DateTime _toWholeMinutes(DateTime utc, int utcOffsetSeconds) {
  final shifted = utc.add(Duration(seconds: utcOffsetSeconds));
  final minutes = (shifted.microsecondsSinceEpoch / _microsecondsPerMinute).floor();
  final whole = DateTime.fromMicrosecondsSinceEpoch(
    minutes * _microsecondsPerMinute,
    isUtc: true,
  );
  return DateTime(whole.year, whole.month, whole.day, whole.hour, whole.minute);
}

/// Sunrise and sunset on [day], in local wall-clock time.
///
/// Only [day]'s calendar fields are read, so its UTC flag does not matter —
/// but a caller building it with `DateTime(y, m, d + 1)` should know that a
/// zone whose clocks go back at midnight returns 23:00 on the day before.
///
/// [utcOffsetSeconds] is the LOCATION's offset, not the device's. Dart has no
/// IANA timezone database, so the caller supplies it — `generateForecast`
/// takes it from the `utc_offset_seconds` Open-Meteo returns on a response it
/// is already fetching. One offset stands for the whole day, which is
/// Open-Meteo's convention too.
///
/// POLAR CONVENTIONS, matched to Open-Meteo's so [classifyPhase] needs no
/// change: the midnight sun is local midnight to local midnight the following
/// day, a 24 hour span, and polar night is local midnight to local midnight, a
/// span of zero. Both verified against the live API on 2026-08-28.
SunTimes sunTimes(double lat, double lon, DateTime day, int utcOffsetSeconds) {
  final transit = _solarNoonUtc(lon, day, utcOffsetSeconds);
  final declination = _solarPosition(_julianDate(transit)).declination;
  final hourAngle = _hourAngleDeg(lat, declination);

  if (hourAngle == null) {
    final midnight = DateTime(day.year, day.month, day.day);
    // Which side of the horizon the sun is stuck on. The declination and the
    // latitude share a sign under the midnight sun and oppose it in polar
    // night.
    final sunlit = (declination >= 0) == (lat >= 0);
    return SunTimes(
      midnight,
      // Built from the date parts rather than by adding 24 hours, which in a
      // zone that changes its clocks would land an hour out.
      sunlit ? DateTime(day.year, day.month, day.day + 1) : midnight,
    );
  }

  var rise = transit.subtract(_minutes(minutesPerDegreeOfRotation * hourAngle));
  var fall = transit.add(_minutes(minutesPerDegreeOfRotation * hourAngle));

  // The declination at solar noon is not quite the declination at sunrise, and
  // near the solstices at high latitude that difference is minutes. Each pass
  // re-reads the sun's position at the current estimate. A pass whose hour
  // angle has gone out of range is discarded rather than acted on: it means
  // the estimate has wandered to a day the sun does not cross the horizon, and
  // the crossing found at the transit is the real one.
  for (var i = 0; i < refinementPasses; i++) {
    final riseHa = _hourAngleDeg(lat, _solarPosition(_julianDate(rise)).declination);
    final fallHa = _hourAngleDeg(lat, _solarPosition(_julianDate(fall)).declination);
    if (riseHa == null || fallHa == null) {
      break;
    }
    rise = transit.subtract(_minutes(minutesPerDegreeOfRotation * riseHa));
    fall = transit.add(_minutes(minutesPerDegreeOfRotation * fallHa));
  }

  return SunTimes(
    _toWholeMinutes(rise, utcOffsetSeconds),
    _toWholeMinutes(fall, utcOffsetSeconds),
  );
}
