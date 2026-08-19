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
