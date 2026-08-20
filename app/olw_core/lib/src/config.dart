// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Minimal location configuration — the fields the forecast logic actually
/// reads. Mirrors the Python `config.py` shape but only as far as the port
/// currently needs; the app's settings screen will grow this.
library;

class SecondaryPoint {
  /// A lake, coastline, mountain — anything warranting its own narrative
  /// section. Disabled entirely for locations without one, which removes the
  /// section from the prompt rather than leaving an empty heading.
  final bool enabled;
  final String name;
  final String sectionLabel;
  final double lat;
  final double lon;

  const SecondaryPoint({
    this.enabled = false,
    this.name = '',
    this.sectionLabel = '',
    this.lat = 0.0,
    this.lon = 0.0,
  });
}

class LocationConfig {
  final String regionName;
  final String primaryPlaceName;
  final String timezone;
  final double lat;
  final double lon;
  final SecondaryPoint secondaryPoint;

  const LocationConfig({
    required this.regionName,
    required this.primaryPlaceName,
    required this.timezone,
    required this.lat,
    required this.lon,
    this.secondaryPoint = const SecondaryPoint(),
  });
}

/// Pipeline-wide constants, mirroring Python's `defaults.py`.
const int historicalLookbackDays = 30;
const int rollingWindowShort = 10;
const int rollingWindowLong = 30;

/// The Open-Meteo models queried for every multi-model fetch.
///
/// This is the `models=` API parameter, so nothing may be added here that
/// Open-Meteo cannot serve. The set of models with a TRACK RECORD is a
/// superset — a local met service is scored alongside these but arrives from
/// its own source, not from Open-Meteo. Mirrors `MODELS` in the Python
/// implementation, which draws the same distinction via `scored_models()`.
const List<String> defaultModels = [
  'gfs_seamless',
  'ecmwf_ifs025',
  'icon_seamless',
  'ukmo_seamless',
  'best_match',
];

/// Lead times tracked and scored independently.
const List<int> leadTimesDays = [0, 3, 7];


/// --- Weekly review (see review.dart) ---
///
/// How many verified checks a claim needs before it may be stated, and how
/// strongly. Ordered (upperBoundExclusive, label). Mirrors
/// REVIEW_CONFIDENCE_BANDS in the Python implementation; the shared vectors
/// enforce that they stay identical, because a surface that ranked models one
/// check earlier than the other would publish a claim the other withholds.
const List<(int, String)> reviewConfidenceBands = [
  (5, 'insufficient'),
  (10, 'provisional'),
  (30, 'usable'),
  (1000000000, 'established'),
];

const int reviewMinChecksForComparison = 10;
const double reviewComparisonMinGapPct = 15.0;
const double reviewTempBiasThresholdC = 1.0;
const double reviewWindBiasThresholdKmh = 8.0;

/// Lead times, under a name that does not collide with the parameter it is
/// the default for.
const List<int> leadTimesDays_ = leadTimesDays;
