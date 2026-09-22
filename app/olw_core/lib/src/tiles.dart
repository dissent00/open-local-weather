/// What the at-a-glance tiles carry — upstream ROADMAP item 159, this repo's
/// item 23.
///
/// THE OVERVIEW'S JOB, MOVED. The forecast's Overview is being retired because
/// most of it was a second telling: its rain sentence was already in the Rain
/// and Onset tiles and more precisely, and its three-day sentence duplicated
/// the Extended Outlook below it. The one thing with no other home was the
/// day-over-day comparison, and this is where it goes.
///
/// WHY THE SENTENCE FAILED. Across the 16 comparisons in the upstream prompt
/// archive it returned "nothing worth saying" ZERO times, so a quiet day
/// filled with "about the same" and "winds and cloud little changed" — a
/// report that there is nothing to report. A modifier belonging to one tile
/// has no joining to do and can simply be absent.
library;

import 'comparison.dart';
import 'rounding.dart';
import 'models.dart' show formatIndexAndBand;
import 'scales.dart';
import 'scoring.dart' show mean;
import 'wind.dart';

/// How many day-to-day pairs the record must hold before a percentile means
/// anything. At 30 the top decile has three observations above it; below that
/// it is noise wearing a number.
const int minPairsForNotable = 30;

/// The percentile a move must reach to be worth a tile's second line.
///
/// MEASURED upstream over 40 day-pairs. The band tables' own first threshold
/// ("is this perceptible") let something speak on 35 of 40 days; the top
/// quartile 24 of 40; the top decile 11 of 40, usually one word. The bands are
/// standards-derived and right about perceptibility, but at that station a
/// perceptible change happens most days, so perceptibility is the wrong
/// question for a tile.
const double notablePercentile = 0.9;

double? _read(Map<String, Object?> day, String field) {
  final v = day[field];
  return v is num ? v.toDouble() : null;
}

/// The size a day-to-day move must reach, per dimension, read off this
/// station's own record. A dimension with too few pairs is ABSENT, and an
/// absent gate means it never speaks.
Map<String, double> notableMoves(
  List<Map<String, Object?>> history, {
  double percentile = notablePercentile,
  int minimumPairs = minPairsForNotable,
}) {
  const fields = {
    'temp': 'high_c',
    'wind': 'peak_wind_kmh',
    'cloud': 'cloud_cover_pct',
  };
  final out = <String, double>{};

  for (final entry in fields.entries) {
    final values = [for (final day in history) _read(day, entry.value)];
    final moves = <double>[];
    for (var i = 1; i < values.length; i++) {
      final a = values[i - 1], b = values[i];
      if (a != null && b != null) moves.add((b - a).abs());
    }
    if (moves.length < minimumPairs) continue;

    moves.sort();
    final index = (moves.length * percentile).floor();
    out[entry.key] = moves[index < moves.length ? index : moves.length - 1];
  }

  return out;
}

/// One short modifier per dimension that moved enough to be worth it.
///
/// THE GATE IS LOCAL AND THE WORD IS NOT. Whether to speak comes from this
/// station's own distribution; the word comes from the same band tables the
/// prose uses, whose cloud boundaries are one and three oktas.
///
/// TEMPERATURE IS A NUMBER, NOT AN ADJECTIVE. The bands call 2.2 °C
/// "slightly" because they are built for a climate where six degrees is
/// ordinary; at the reference station 2.2 is the top decile, and "slightly
/// cooler" on a day the gate just called unusual undercuts itself.
Map<String, String> comparisonModifiers(
  Map<String, double?> deltas,
  Map<String, double> notable,
) {
  final bands = <String, (List<(double, String)>, String, String)>{
    'temp': (tempChangeBandsC, 'warmer', 'cooler'),
    'wind': (windChangeBandsKmh, 'windier', 'calmer'),
    'cloud': (cloudChangeBandsPct, 'cloudier', 'clearer'),
  };
  final said = <String, String>{};

  for (final entry in bands.entries) {
    final delta = deltas[entry.key];
    final gate = notable[entry.key];
    if (delta == null || gate == null || delta.abs() < gate) continue;

    if (entry.key == 'temp') {
      final word = delta > 0 ? entry.value.$2 : entry.value.$3;
      said[entry.key] = '${delta.abs().round()}° $word';
      continue;
    }

    final label = bandLabel(delta, entry.value.$1, entry.value.$2, entry.value.$3);
    if (label != null) said[entry.key] = label;
  }

  return said;
}

/// THE SKY, IN THE STANDARD'S OWN CATEGORIES.
///
/// NWS sky condition is reported in eighths: clear at 0, few at 1-2,
/// scattered at 3-4, broken at 5-7, overcast at 8. The boundaries here are the
/// midpoints between those categories converted to percent. Nothing invented
/// and no local measurement — the same source `cloudChangeBandsPct` draws its
/// one-okta floor from.
///
/// The WORDS are plain rather than the aviation abbreviations. A tile is read
/// by someone deciding whether to hang washing out, not by a pilot.
const List<(double, String)> skyCoverBandsPct = [
  (6.25, 'Clear'),
  (31.25, 'Mostly clear'),
  (56.25, 'Partly cloudy'),
  (93.75, 'Mostly cloudy'),
];
const String overcastLabel = 'Overcast';

/// What a tile calls each anchor hour, positionally paired with
/// `shiftAnchors`. Separate from that list's own labels because those are
/// prose for a clause — "overnight", "by midday" — and a tile has room for a
/// word. The HOURS are shared, which is the part that matters: the sky and the
/// wind must describe the same three moments.
const List<String> tileAnchorWords = ['early', 'midday', 'evening'];

/// The plain word for a sky cover percentage, or null.
String? skyWord(double? coverPct) {
  if (coverPct == null) return null;

  for (final (threshold, word) in skyCoverBandsPct) {
    if (coverPct < threshold) return word;
  }
  return overcastLabel;
}

/// The sky at each anchor hour, as a tile's lines, in time order.
///
/// A DAY'S SHAPE, NOT ITS MEAN. On 2026-09-21 the models' Day+0 cloud mean was
/// 45% with a 14-to-64 spread while the day ran clear in the morning to
/// overcast under afternoon convection — which is what the forecast's own
/// prose said. One number for that day is true and useless.
///
/// EMPTY WHEN EVERY ANCHOR IS BEHIND THE READER — upstream item 118, the same
/// rule `describeWindShift` follows. The test is "is any of it still ahead",
/// not "drop what has passed": a morning reader still wants to know the day
/// started clear.
List<Map<String, String>> cloudAnchors(
  Map<String, Object?> hourly,
  List<String> models, {
  required int issuedHour,
}) {
  final out = <Map<String, String>>[];
  final hours = <int>[];

  for (var i = 0; i < shiftAnchors.length && i < tileAnchorWords.length; i++) {
    final (hour, _) = shiftAnchors[i];
    final covers = valuesAt(hourly, models, hour, 'cloud_cover');
    if (covers.isEmpty) continue;

    // The models' MEAN, matching every other consensus here. A spread is a
    // real fact about a sky and belongs where there is room to name which
    // model said what.
    //
    // `mean`, NOT `reduce`: Python's `sum()` is Neumaier-compensated and a
    // plain left-to-right accumulation drifts from it by an ULP on mixed
    // magnitudes — see sums.dart. That ULP is invisible until a mean lands on
    // one of skyWord's boundaries, where it flips the published word. Written
    // with `reduce` on 2026-09-21 and corrected the same day; no vector case
    // had caught it, because the fixtures' covers sum exactly.
    final label = skyWord(mean(covers.cast<double?>()));
    if (label != null) {
      out.add({'when': tileAnchorWords[i], 'cover': label});
      hours.add(hour);
    }
  }

  if (out.isEmpty || hours.every((h) => h <= issuedHour)) return const [];
  return out;
}

/// The wind at each anchor hour, as a tile's lines, in time order.
///
/// THE SAME ANCHORS AND THE SAME BLOCK AS THE SKY, which is the point: two
/// tiles side by side must describe the same three moments or a reader
/// comparing them is comparing different times of day.
///
/// VALUES, NOT A SENTENCE. `describeWindShift` and `describeWindTimeline`
/// already compose prose from these hours; a tile needs the numbers, and
/// re-deriving them by parsing a clause in the renderer is the wrong side of
/// the seam — this repo's item 23.
///
/// SPEEDS STAY IN KM/H whatever the reader's unit. The unit lives in the
/// tile's header and the value is converted at render, which is what makes
/// the setting a one-label change rather than a rebuild of every string.
///
/// `direction` IS ABSENT MORE OFTEN THAN PRESENT and the tile drops the
/// letters rather than apologising in words: measured over the upstream
/// prompt archive, a single agreed bearing existed on 3 of 18 runs. A bearing
/// cannot be averaged, so this is [consensusDirection]'s gated answer and
/// nothing else.
///
/// Empty when every anchor is behind the reader — upstream item 118.
List<Map<String, Object>> windAnchors(
  Map<String, Object?> hourly,
  List<String> models, {
  required int issuedHour,
}) {
  final out = <Map<String, Object>>[];
  final hours = <int>[];

  for (var i = 0; i < shiftAnchors.length && i < tileAnchorWords.length; i++) {
    final (hour, _) = shiftAnchors[i];
    final speeds = valuesAt(hourly, models, hour, 'wind_speed_10m');
    final gusts = valuesAt(hourly, models, hour, 'wind_gusts_10m');
    if (speeds.isEmpty && gusts.isEmpty) continue;

    final anchor = <String, Object>{'when': tileAnchorWords[i]};
    final point = consensusDirection(directionsAt(hourly, models, hour));
    if (point != null) {
      anchor['direction'] = point;
    }
    if (speeds.isNotEmpty) {
      anchor['sustained_kmh'] = roundLikePython(mean(speeds.cast<double?>())!, 1);
    }
    if (gusts.isNotEmpty) {
      anchor['gust_kmh'] = roundLikePython(mean(gusts.cast<double?>())!, 1);
    }

    out.add(anchor);
    hours.add(hour);
  }

  if (out.isEmpty || hours.every((h) => h <= issuedHour)) return const [];
  return out;
}

/// Kilometres per hour in one knot.
const double kmhPerKnot = 1.852;

/// Two lines of wind and two of cloud, not three. The composers sample three
/// hours; a tile shows the TURN, which is the pair the shift clause names.
const int windTileAnchors = 2;
const int cloudTileAnchors = 2;

double? _num(Map<String, Object?> p, String key) {
  final v = p[key];
  return v is num ? v.toDouble() : null;
}

String? _str(Map<String, Object?> p, String key) {
  final v = p[key];
  if (v == null) return null;
  final s = v.toString().trim();
  return s.isEmpty ? null : s;
}

/// The anchors under [key], or nothing. Anything that is not a list of maps
/// is treated as ABSENT rather than coerced: a malformed block should render
/// no tile, not half of one.
List<Map<String, Object?>> _anchorList(Map<String, Object?> p, String key) {
  final v = p[key];
  if (v is! List) return const [];
  return [
    for (final e in v)
      if (e is Map) e.cast<String, Object?>(),
  ];
}

Map<String, Object?> _line(String text, {bool primary = false}) =>
    {'text': text, 'primary': primary};

String _windLine(Map<String, Object?> anchor, {required bool metric}) {
  String shown(double kmh) => (metric ? kmh : kmh / kmhPerKnot).round().toString();

  final sustained = anchor['sustained_kmh'];
  final gust = anchor['gust_kmh'];
  String speed = '';
  if (sustained is num && gust is num) {
    speed = '${shown(sustained.toDouble())}G${shown(gust.toDouble())}';
  } else if (gust is num) {
    speed = 'G${shown(gust.toDouble())}';
  } else if (sustained is num) {
    speed = shown(sustained.toDouble());
  }

  // ONE SPACE, NOT TWO. A double space reads as a column separator in a
  // Flutter Text and COLLAPSES in HTML, so the app would show a different
  // string from the page and the email out of one composer whose whole point
  // is that they cannot. Visual separation belongs in layout.
  final parts = [
    for (final key in ['when', 'direction'])
      if ((anchor[key]?.toString().trim() ?? '').isNotEmpty)
        anchor[key]!.toString().trim(),
    if (speed.isNotEmpty) speed,
  ];
  return parts.join(' ');
}

/// The at-a-glance tiles this record can fill, in reading order.
///
/// ONE COMPOSER FOR THREE SURFACES — upstream item 159 step 6. This app, the
/// GitHub Pages forecast and the email all show these tiles, and this decides
/// which exist, in what order, and what each line says. Before it the app
/// composed them here in Dart, the page listed seven ungrouped stats in a
/// template and the email showed none at all.
///
/// A tile with nothing to say is ABSENT rather than showing a dash.
///
/// `primary` means "a reading" and not "important": paired data stays the
/// same size, and the day-over-day modifier is the one supporting line.
List<Map<String, Object?>> composeTiles(
  Map<String, Object?> properties, {
  bool metric = true,
}) {
  final rawComparison = properties['comparison'];
  final comparison =
      rawComparison is Map ? rawComparison.cast<String, Object?>() : const <String, Object?>{};

  String? modifier(String dimension) {
    // THE STORED VALUE IS METRIC. "windier" and "much cloudier" carry no
    // unit; "3° cooler" is Celsius degrees, so an imperial reader would see a
    // magnitude wrong by a factor of 1.8. Dropped rather than converted,
    // because only the word is stored — upstream item 165.
    if (dimension == 'temp' && !metric) return null;
    final v = comparison[dimension];
    if (v == null) return null;
    final s = v.toString().trim();
    return s.isEmpty ? null : s;
  }

  final tiles = <Map<String, Object?>>[];

  Map<String, Object?> tile(String label, String? unit, List<Map<String, Object?>> lines) =>
      {'label': label, 'unit': unit, 'lines': lines};

  final high = _num(properties, 'temp_high_c'), low = _num(properties, 'temp_low_c');
  if (high != null && low != null) {
    int degrees(double c) => (metric ? c : c * 9 / 5 + 32).round();
    tiles.add(tile('High / Low', metric ? '°C' : '°F', [
      _line('${degrees(high)}° / ${degrees(low)}°', primary: true),
      if (modifier('temp') != null) _line(modifier('temp')!),
    ]));
  }

  final rain = _str(properties, 'rain_expected');
  if (rain != null) {
    final onset = _str(properties, 'onset_window');
    tiles.add(tile('Rain', null, [
      _line(rain, primary: true),
      if (onset != null) _line(onset, primary: true),
    ]));
  }

  final wind = _anchorList(properties, 'wind_anchors');
  if (wind.isNotEmpty) {
    tiles.add(tile('Wind', metric ? 'km/h' : 'kt', [
      for (final a in wind.take(windTileAnchors))
        _line(_windLine(a, metric: metric), primary: true),
      if (modifier('wind') != null) _line(modifier('wind')!),
    ]));
  } else {
    final gust = _num(properties, 'peak_wind_primary_kmh');
    if (gust != null) {
      final shown = (metric ? gust : gust / kmhPerKnot).round();
      tiles.add(tile('Wind gust', metric ? 'km/h' : 'kt', [
        _line('$shown', primary: true),
        if (modifier('wind') != null) _line(modifier('wind')!),
      ]));
    }
  }

  final sky = _anchorList(properties, 'cloud_anchors');
  if (sky.isNotEmpty) {
    tiles.add(tile('Cloud', null, [
      for (final a in sky.take(cloudTileAnchors))
        _line('${a['when'] ?? ''} ${a['cover'] ?? ''}'.trim(), primary: true),
      if (modifier('cloud') != null) _line(modifier('cloud')!),
    ]));
  }

  final uv = _num(properties, 'uv_index'), aqi = _num(properties, 'air_quality_index');
  if (uv != null || aqi != null) {
    final numbers = [
      if (uv != null) formatIndexAndBand(uv, null)!,
      if (aqi != null) formatIndexAndBand(aqi, null)!,
    ].join(' / ');
    final words = [
      if (uv != null) uvBand(uv),
      if (aqi != null) aqiBand(aqi.round()),
    ].whereType<String>().join(' / ');
    tiles.add(tile('UV / AQI', null, [
      _line(numbers, primary: true),
      if (words.isNotEmpty) _line(words),
    ]));
  } else {
    final pair = [_str(properties, 'uv_index_max'), _str(properties, 'air_quality_aqi')];
    if (pair.any((v) => v != null)) {
      tiles.add(tile('UV / AQI', null, [
        for (final v in pair)
          if (v != null) _line(v, primary: true),
      ]));
    }
  }

  // BOTH OR NEITHER. In polar night there is no sunrise, and half a pair
  // reads as a rendering fault rather than the honest answer.
  final sunrise = _str(properties, 'sunrise'), sunset = _str(properties, 'sunset');
  if (sunrise != null && sunset != null) {
    tiles.add(tile('Sun', null, [
      _line(sunrise, primary: true),
      _line(sunset, primary: true),
    ]));
  }

  return tiles;
}
