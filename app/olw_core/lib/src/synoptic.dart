// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'config.dart';

/// Turning a coarse pressure field into statements a forecaster would make.
///
/// Port of the Python `synoptic.py`. The Synoptic Overview previously had no
/// synoptic data behind it: the near-field points span roughly 125 x 55 km,
/// while highs, lows and the ITCZ have wavelengths of 1,000-4,000 km, so the
/// whole domain fit inside a single system's gradient. A wider ring fixes
/// the data; this module derives the descriptive terms IN CODE, because
/// handing an LLM nine raw arrays and asking which quadrant is lowest is the
/// arithmetic-by-eye the day-over-day comparison already had to be rescued
/// from.
///
/// WHAT THIS DELIBERATELY DOES NOT CLAIM. Point pressure at 12-degree
/// spacing supports "lower pressure lies to the northeast and is deepening".
/// It does not support a named storm centre, a track, or a frontal position:
/// the true centre may sit between points or outside the ring entirely. The
/// vocabulary below is bounded accordingly and `spec/vectors/synoptic.json`
/// holds both implementations to it — an implementation that quietly widened
/// the claim would overstate what the data can carry.

const Map<String, String> _compassNames = {
  'N': 'north',
  'NE': 'northeast',
  'E': 'east',
  'SE': 'southeast',
  'S': 'south',
  'SW': 'southwest',
  'W': 'west',
  'NW': 'northwest',
  'centre': 'overhead',
};

/// A description of the large-scale pressure pattern, ready to narrate.
class SynopticSnapshot {
  const SynopticSnapshot({
    required this.centreMslpHpa,
    required this.lowestLabel,
    required this.lowestMslpHpa,
    required this.highestLabel,
    required this.highestMslpHpa,
    required this.gradientHpa,
    required this.gradientStrength,
    required this.tendencies,
    required this.statements,
  });

  final double? centreMslpHpa;
  final String? lowestLabel;
  final double? lowestMslpHpa;
  final String? highestLabel;
  final double? highestMslpHpa;
  final double? gradientHpa;
  final String? gradientStrength;

  /// Per-quadrant 72-hour tendency: "deepening" / "filling" / "steady".
  final Map<String, String> tendencies;

  /// Plain-language lines, already safe to state.
  final List<String> statements;

  Map<String, Object?> toJson() => {
        'centre_mslp_hpa': centreMslpHpa,
        'lowest_label': lowestLabel,
        'lowest_mslp_hpa': lowestMslpHpa,
        'highest_label': highestLabel,
        'highest_mslp_hpa': highestMslpHpa,
        'gradient_hpa': gradientHpa,
        'gradient_strength': gradientStrength,
        'tendencies': tendencies,
        'statements': statements,
      };
}

double? _first(List<Object?> values) {
  for (final v in values) {
    if (v != null) return (v as num).toDouble();
  }
  return null;
}

double? _last(List<Object?> values) {
  for (final v in values.reversed) {
    if (v != null) return (v as num).toDouble();
  }
  return null;
}

String _strength(double gradient) {
  for (final band in synopticGradientBandsHpa) {
    if (gradient < band.$1) return band.$2;
  }
  return synopticGradientBandsHpa.last.$2;
}

/// Matches Python's `f"{value:.0f}"`, which rounds half to EVEN — Dart's
/// `toStringAsFixed(0)` rounds half away from zero, so 1006.5 would render as
/// "1007" here and "1006" there. A one-digit divergence in a published
/// sentence is exactly the kind of drift the shared vectors exist to catch.
String _fmt0(double v) {
  final floor = v.floor();
  final frac = v - floor;
  int rounded;
  if (frac > 0.5) {
    rounded = floor + 1;
  } else if (frac < 0.5) {
    rounded = floor;
  } else {
    rounded = floor.isEven ? floor : floor + 1;
  }
  return rounded.toString();
}

/// Reduces the ring to labels. Returns null if nothing usable arrived — an
/// absent synoptic picture must read as absent, not as a flat field.
SynopticSnapshot? summarizeSynoptic(Map<String, Object?>? payload) {
  final rawPoints = payload?['points'];
  if (rawPoints is! List) return null;

  final readings = <(String?, double, double?)>[];
  for (final p in rawPoints) {
    if (p is! Map) continue;
    final series = p['mslp_hpa'];
    final values = series is List ? series : const <Object?>[];
    final now = _first(values);
    if (now == null) continue;
    readings.add((p['label'] as String?, now, _last(values)));
  }
  if (readings.length < 3) return null;

  double? centre;
  for (final r in readings) {
    if (r.$1 == 'centre') {
      centre = r.$2;
      break;
    }
  }
  final outer = readings.where((r) => r.$1 != 'centre').toList();
  if (outer.isEmpty) return null;

  var lowest = outer.first;
  var highest = outer.first;
  for (final r in outer) {
    if (r.$2 < lowest.$2) lowest = r;
    if (r.$2 > highest.$2) highest = r;
  }
  final gradient = highest.$2 - lowest.$2;

  final tendencies = <String, String>{};
  for (final (label, now, later) in readings) {
    if (later == null || label == null) continue;
    final delta = later - now;
    if (delta.abs() < synopticTendencyThresholdHpa) {
      tendencies[label] = 'steady';
    } else {
      tendencies[label] = delta < 0 ? 'deepening' : 'filling';
    }
  }

  final roundedGradient = (gradient * 10).round() / 10;
  final snapshot = SynopticSnapshot(
    centreMslpHpa: centre,
    lowestLabel: lowest.$1,
    lowestMslpHpa: lowest.$2,
    highestLabel: highest.$1,
    highestMslpHpa: highest.$2,
    gradientHpa: roundedGradient,
    gradientStrength: _strength(gradient),
    tendencies: tendencies,
    statements: const [],
  );
  return SynopticSnapshot(
    centreMslpHpa: snapshot.centreMslpHpa,
    lowestLabel: snapshot.lowestLabel,
    lowestMslpHpa: snapshot.lowestMslpHpa,
    highestLabel: snapshot.highestLabel,
    highestMslpHpa: snapshot.highestMslpHpa,
    gradientHpa: snapshot.gradientHpa,
    gradientStrength: snapshot.gradientStrength,
    tendencies: snapshot.tendencies,
    statements: _statements(snapshot),
  );
}

/// Sentences bounded by what point sampling at this spacing can support.
///
/// Note the vocabulary: "lower pressure lies to the northeast", never "a low
/// is centred over Somalia". The ring cannot locate a centre — only which
/// sampled direction is lowest.
List<String> _statements(SynopticSnapshot s) {
  final lines = <String>[];
  final lowDir = _compassNames[s.lowestLabel] ?? s.lowestLabel;
  final highDir = _compassNames[s.highestLabel] ?? s.highestLabel;

  if (s.gradientHpa != null) {
    lines.add('Across roughly 2,600 km, pressure is lowest toward the $lowDir '
        '(${_fmt0(s.lowestMslpHpa!)} hPa) and highest toward the $highDir '
        '(${_fmt0(s.highestMslpHpa!)} hPa) — a ${_fmt0(s.gradientHpa!)} hPa spread, '
        'a ${s.gradientStrength} large-scale gradient.');
  }

  final lowTrend = s.tendencies[s.lowestLabel];
  if (lowTrend != null && lowTrend != 'steady') {
    lines.add('The lower pressure to the $lowDir is $lowTrend over the next three days.');
  }

  // Any OTHER direction where pressure is falling. Reporting only the
  // currently-lowest quadrant would miss this entirely: a system approaching
  // from the west shows up as the west falling well before the west is the
  // lowest point on the ring.
  final deepening = s.tendencies.entries
      .where((e) => e.value == 'deepening' && e.key != s.lowestLabel && e.key != 'centre')
      .map((e) => e.key)
      .toList()
    ..sort();
  if (deepening.isNotEmpty) {
    final names = deepening.map((d) => _compassNames[d] ?? d).join(', ');
    lines.add('Pressure is also falling toward the $names — a large-scale feature '
        'building in that direction, though this sampling cannot say how fast '
        'or whether it will reach here.');
  }

  final centreTrend = s.tendencies['centre'];
  if (centreTrend != null) {
    const words = {'deepening': 'falling', 'filling': 'rising', 'steady': 'near-steady'};
    lines.add('Pressure overhead is ${words[centreTrend]}.');
  }

  lines.add('Sampling is a nine-point ring at 12-degree spacing, so this locates a '
      'direction, not a centre or a front.');
  return lines;
}
