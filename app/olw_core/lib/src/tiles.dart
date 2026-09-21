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
