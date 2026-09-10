/// Wind direction, which is the one quantity here that cannot be averaged.
///
/// Upstream ROADMAP item 59. Every other figure this project blends — the
/// day's high, the peak gust, cloud cover, CAPE — lives on a number line, and
/// a mean of them means something. A compass bearing does not: 350 and 10
/// degrees are twenty degrees apart and both nearly north, and their
/// arithmetic mean is 180, due SOUTH of both.
///
/// So directions are combined as UNIT VECTORS. The resultant's angle is the
/// consensus bearing and its LENGTH is how much the models agree: 1.0 is
/// identical, 0.0 is a set that cancels out and genuinely has no mean
/// direction. That length is not a bolt-on confidence score — it falls out of
/// the same arithmetic, and it is why this cannot invent a bearing no model
/// holds.
library;

import 'dart:math' as math;

import 'config.dart';

/// The 16-point rose, from north. Chosen over the 8-point compass because the
/// models routinely sit one point apart, and rounding to eight is a coarser
/// answer than the agreement supports.
const List<String> compassPoints = [
  'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW',
];
const double _pointWidthDeg = 360.0 / 16;

/// The rose point a bearing falls in. 0 is north, 90 east.
String compassPoint(double degrees) {
  final i = ((degrees % 360) / _pointWidthDeg + 0.5).floor() % compassPoints.length;
  return compassPoints[i];
}

/// (bearing, agreement) for a set of compass directions, or null if empty.
///
/// When agreement is near zero the bearing is arbitrary — atan2 of two
/// near-zero components — which is why callers must gate on the agreement
/// rather than trust the angle. [consensusDirection] is that gate.
({double bearing, double agreement})? vectorMean(List<double> degrees) {
  if (degrees.isEmpty) return null;

  var x = 0.0, y = 0.0;
  for (final d in degrees) {
    x += math.cos(d * math.pi / 180);
    y += math.sin(d * math.pi / 180);
  }
  x /= degrees.length;
  y /= degrees.length;

  // NORMALISED TO [0, 360), and the guard is not decorative. For a due-north
  // set atan2 returns a hair BELOW zero, and a tiny negative modulo 360 is
  // 360.0 exactly once the subtraction rounds — so north came back as 360.0.
  // Python carries the same guard; without it the two languages disagree on
  // one vector case and nothing else.
  var bearing = (math.atan2(y, x) * 180 / math.pi) % 360.0;
  if (bearing >= 360.0) bearing = 0.0;

  return (bearing: bearing, agreement: math.sqrt(x * x + y * y));
}

/// One rose point the models actually share, or null.
String? consensusDirection(
  List<double> degrees, {
  double gate = windDirectionAgreementGate,
}) {
  if (degrees.length < windDirectionMinModels) return null;

  final mean = vectorMean(degrees);
  if (mean == null) return null;

  return mean.agreement >= gate ? compassPoint(mean.bearing) : null;
}

/// The hours the day's shape is sampled at, and the words for them.
///
/// Three points, not twenty-four: a phrase naming every hour is a table. 09:00
/// is deliberately NOT sampled — it is the mid-morning transition where the
/// models place the same turn at different hours, so the one hour guaranteed
/// to be contested is the one hour not asked about.
const List<(int, String)> shiftAnchors = [
  (3, 'overnight'),
  (12, 'by midday'),
  (18, 'into the evening'),
];

const Map<String, String> _adjective = {
  'N': 'northerly', 'NNE': 'north-northeasterly', 'NE': 'northeasterly',
  'ENE': 'east-northeasterly', 'E': 'easterly', 'ESE': 'east-southeasterly',
  'SE': 'southeasterly', 'SSE': 'south-southeasterly', 'S': 'southerly',
  'SSW': 'south-southwesterly', 'SW': 'southwesterly', 'WSW': 'west-southwesterly',
  'W': 'westerly', 'WNW': 'west-northwesterly', 'NW': 'northwesterly',
  'NNW': 'north-northwesterly',
};
const Map<String, String> _plain = {
  'N': 'north', 'NNE': 'north-northeast', 'NE': 'northeast', 'ENE': 'east-northeast',
  'E': 'east', 'ESE': 'east-southeast', 'SE': 'southeast', 'SSE': 'south-southeast',
  'S': 'south', 'SSW': 'south-southwest', 'SW': 'southwest', 'WSW': 'west-southwest',
  'W': 'west', 'WNW': 'west-northwest', 'NW': 'northwest', 'NNW': 'north-northwest',
};

int? _hourOf(String stamp) {
  final parts = stamp.split('T');
  if (parts.length < 2 || parts[1].length < 2) return null;
  return int.tryParse(parts[1].substring(0, 2));
}

List<double> _directionsAt(Map<String, Object?> hourly, List<String> models, int hour) {
  final hours = hourly['hourly'];
  if (hours is! Map) return const [];
  final times = hours['time'];
  if (times is! List) return const [];

  int? idx;
  for (var i = 0; i < times.length; i++) {
    if (_hourOf('${times[i]}') == hour) {
      idx = i;
      break;
    }
  }
  if (idx == null) return const [];

  final out = <double>[];
  for (final model in models) {
    final series = hours['wind_direction_10m_$model'] ?? hours['wind_direction_10m'];
    if (series is! List || idx >= series.length) continue;
    final v = series[idx];
    if (v is num) out.add(v.toDouble());
  }
  return out;
}

/// One finished clause for how the wind turns through the day, or null.
///
/// Measured 2026-09-10: the models' agreement on a single daily bearing swings
/// from 0.95 at midday to 0.48 at 19:00, but the DAYS agree with each other at
/// 0.98-0.99. Lake Victoria runs a land breeze overnight and a lake breeze
/// from midday, and that is the same day every day. Asked for one direction
/// the models argue; asked which way it turns, they do not.
///
/// Lowercase and unpunctuated, for the same reason describeExtendedTrend ships
/// a clause: the prompt uses it verbatim, so anything left to phrase is
/// something that can be phrased wrong.
String? describeWindShift(Map<String, Object?> hourly, List<String> models) {
  final named = <(String, String)>[];
  for (final (hour, label) in shiftAnchors) {
    final point = consensusDirection(_directionsAt(hourly, models, hour));
    if (point != null) named.add((point, label));
  }

  if (named.length < 2) return null;

  // Nothing turned. Said rather than skipped: a steady wind all day is a real
  // planning answer, exactly as a steady temperature spell is.
  if (named.map((e) => e.$1).toSet().length == 1) {
    return '${_adjective[named.first.$1]} throughout';
  }

  // EVERY AGREED ANCHOR IS NAMED, not just the ends. Reporting first and last
  // threw away midday on this location's commonest day — the hour the models
  // agree on most strongly and the one a boater is asking about.
  final first = named.first;
  final turns = named.skip(1).map((e) => '${_plain[e.$1]} ${e.$2}').join(' and ');
  return '${_adjective[first.$1]} ${first.$2}, turning $turns';
}
