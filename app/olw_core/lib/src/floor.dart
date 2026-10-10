// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The floor: the write-up code writes — upstream ROADMAP item 190.
///
/// Port of `floor.py`; the reasoning lives there. A pure function over
/// [FloorInputs], held to the Python by `spec/vectors/floor.json`, so the
/// page and the phone compose the same text from the same values. Only
/// phrases that exist verbatim, joined by fixed frames; a sentence that
/// fails [phraseDefect] is dropped.
library;

import 'comparison.dart' show describeDayRain;
import 'dates.dart';
import 'daypart.dart' show onsetWord;
import 'instability.dart' show convectiveTier;
import 'models.dart' show formatTempC, disagreementRainWhileDry, disagreementHighExceeded, disagreementOnsetAlreadyPassed, disagreementGustExceeded;
import 'phrasing.dart';
import 'rounding.dart';
import 'scales.dart';
import 'scoring.dart' show mean;
import 'tiles.dart' show kmhPerKnot, skySourceStation;

/// Who wrote the narrative — stored beside it as `narrative_source`.
const String narrativeSourceCode = 'code';
const String narrativeSourceLlm = 'llm';

const String signOffWithModel =
    'Written by code; a discussion follows when a model answers.';
const String signOffWithoutModel = 'Written by code.';

const String todayHeading = "## Today's Forecast";
const String extendedHeading = '## Extended Outlook';

/// Highs are shown as a range when the models spread more than this.
const double highRangeSpreadC = 2.0;

const Map<String, String> _anchorWhen = {
  'early': 'early',
  'midday': 'at midday',
  'evening': 'in the evening',
};
const String _stormGusts =
    'Any thunderstorm brings sudden gusts well above this figure; '
    'the water is unsafe while one is nearby.';

/// Everything the floor says, as plain values — the vector's input shape.
/// Mirrors `FloorInputs`; the served leads arrive already resolved.
class FloorInputs {
  const FloorInputs({
    required this.date,
    required this.tempHighLowDisplay,
    this.issuedLocalTime,
    this.sunrise,
    this.sunset,
    this.overviewComparison,
    this.cloudAnchors = const [],
    this.windAnchors = const [],
    this.peakWindPrimaryKmh,
    this.peakWindSecondaryKmh,
    this.uvIndex,
    this.airQualityIndex,
    this.groundAqi = const [],
    this.servedToday = const {},
    this.extendedCalls = const [],
    this.highsByLead = const {},
    this.day0PeakCapeJkg = const [],
    this.extendedTrend,
    this.extendedOutlook,
    this.secondaryName,
    this.modelConfigured = true,
    this.observedLine,
    this.observed = const {},
    this.stationName,
    this.stationCodes = const [],
  });

  /// ISO date, the day the floor is about.
  final String date;
  final String tempHighLowDisplay;
  final String? issuedLocalTime;
  final String? sunrise;
  final String? sunset;
  final String? overviewComparison;
  final List<Map<String, Object?>> cloudAnchors;
  final List<Map<String, Object?>> windAnchors;
  final double? peakWindPrimaryKmh;
  final double? peakWindSecondaryKmh;
  final double? uvIndex;
  final int? airQualityIndex;
  final List<Map<String, Object?>> groundAqi;
  final Map<String, Object?> servedToday;
  final List<Map<String, Object?>> extendedCalls;
  final Map<String, List<double>> highsByLead;
  final List<double?> day0PeakCapeJkg;
  final String? extendedTrend;

  /// The two paragraphs of item 190 step 3, which replace the trend clause
  /// and the lead lines where the run composed them.
  final String? extendedOutlook;
  final String? secondaryName;
  final bool modelConfigured;

  /// The right-now rule, upstream 2026-10-10: the station's line closes
  /// Today's Forecast, and a reading that contradicts the served call is
  /// said right after the opener, from [stationCodes] — the notable
  /// disagreements judged against the SERVED call. The scored row stays.
  final String? observedLine;
  final Map<String, Object?> observed;
  final String? stationName;
  final List<String> stationCodes;

  factory FloorInputs.fromJson(Map<String, Object?> j) => FloorInputs(
        date: j['date'] as String,
        tempHighLowDisplay: j['temp_high_low_display'] as String,
        issuedLocalTime: j['issued_local_time'] as String?,
        sunrise: j['sunrise'] as String?,
        sunset: j['sunset'] as String?,
        overviewComparison: j['overview_comparison'] as String?,
        cloudAnchors: _maps(j['cloud_anchors']),
        windAnchors: _maps(j['wind_anchors']),
        peakWindPrimaryKmh: (j['peak_wind_primary_kmh'] as num?)?.toDouble(),
        peakWindSecondaryKmh: (j['peak_wind_secondary_kmh'] as num?)?.toDouble(),
        uvIndex: (j['uv_index'] as num?)?.toDouble(),
        airQualityIndex: (j['air_quality_index'] as num?)?.toInt(),
        groundAqi: _maps(j['ground_aqi']),
        servedToday: ((j['served_today'] as Map?) ?? const {}).cast<String, Object?>(),
        extendedCalls: _maps(j['extended_calls']),
        highsByLead: {
          for (final e in ((j['highs_by_lead'] as Map?) ?? const {}).entries)
            e.key as String: [for (final h in e.value as List) (h as num).toDouble()],
        },
        day0PeakCapeJkg: [
          for (final c in (j['day0_peak_cape_jkg'] as List?) ?? const []) (c as num?)?.toDouble()
        ],
        extendedTrend: j['extended_trend'] as String?,
        extendedOutlook: j['extended_outlook'] as String?,
        secondaryName: j['secondary_name'] as String?,
        modelConfigured: (j['model_configured'] as bool?) ?? true,
        observedLine: j['observed_line'] as String?,
        observed: ((j['observed'] as Map?) ?? const {}).cast<String, Object?>(),
        stationName: j['station_name'] as String?,
        stationCodes: [for (final c in (j['station_codes'] as List?) ?? const []) c as String],
      );

  static List<Map<String, Object?>> _maps(Object? raw) => [
        for (final m in (raw as List?) ?? const []) (m as Map).cast<String, Object?>()
      ];
}

/// The floor as Markdown with the write-up's headings. Mirrors `compose_floor`.
String composeFloor(FloorInputs i) {
  final sections = [for (final (heading, text) in _floorSectionRows(i)) if (text.isNotEmpty) '$heading\n\n$text'];
  sections.add(signOff(i));
  return '${sections.join('\n\n')}\n';
}

/// The texts code writes, by section id — mirrors `floor_section_texts`,
/// for the writer's composition (upstream item 191 step (b)).
Map<String, String> floorSectionTexts(FloorInputs i) {
  final rows = _floorSectionRows(i);
  return {
    for (final (id, (_, text)) in [('today', rows[0]), ('extended', rows[1]), ('secondary', rows[2])])
      if (text.isNotEmpty) id: text,
  };
}

/// The stamp and who wrote it — mirrors `sign_off`.
String signOff(FloorInputs i) => _sentences(_signOffParts(i));

List<(String, String)> _floorSectionRows(FloorInputs i) {
  final outlook = i.extendedOutlook?.trim();
  final extended = outlook != null && outlook.isNotEmpty ? outlook : _sentences(_extendedParts(i));
  final boaters = i.secondaryName == null ? '' : _sentences(_boatersParts(i));
  return [
    (todayHeading, _sentences(_todayParts(i))),
    (extendedHeading, extended),
    ('## ${i.secondaryName} — Conditions for Boaters', boaters),
  ];
}

String _sentences(List<String?> parts) =>
    [for (final p in parts) if (p != null && phraseDefect(p) == null) p].join(' ');

String? _sentence(String? text) {
  if (text == null || text.isEmpty) return null;
  final t = text.trim();
  final cased = t[0].toUpperCase() + t.substring(1);
  return cased.endsWith('.') ? cased : '$cased.';
}

// --- Today's Forecast ---

List<String?> _todayParts(FloorInputs i) => [
      _opener(i),
      ..._stationParts(i),
      _sentence(i.tempHighLowDisplay),
      _sky(i.cloudAnchors),
      _wind(i.windAnchors, i.peakWindPrimaryKmh),
      _uv(i.uvIndex),
      _airQuality(i),
      _sentence(i.observedLine),
    ];

/// One sentence per code the station's readings fired against the served
/// call, in the codes' order — mirrors `floor._station_parts`.
List<String?> _stationParts(FloorInputs i) {
  final station = i.stationName;
  if (station == null || station.isEmpty || i.stationCodes.isEmpty) return const [];
  final o = i.observed;
  final served = i.servedToday;
  final parts = <String>[];
  for (final code in i.stationCodes) {
    final onset = o['precipitation_onset'];
    final calledOnset = served['onset_hour'];
    if (code == disagreementOnsetAlreadyPassed && _truthy(onset) && _truthy(calledOnset)) {
      parts.add('Rain began at $station from $onset, ahead of the $calledOnset called.');
    } else if (code == disagreementRainWhileDry && o['precipitation'] == true) {
      parts.add('$station has already reported rain today, against a dry call; the day is not dry.');
    } else if (code == disagreementHighExceeded && o['high_c'] != null && served['high_c'] != null) {
      parts.add(
        '$station has already recorded ${formatTempC((o['high_c'] as num).toDouble(), decimals: 1)}, '
        'above the ${formatTempC((served['high_c'] as num).toDouble(), decimals: 1)} called.',
      );
    } else if (code == disagreementGustExceeded && o['peak_gust_kmh'] != null && i.peakWindPrimaryKmh != null) {
      parts.add(
        '$station has already gusted to ${_kmhAndKt((o['peak_gust_kmh'] as num).toDouble())}, '
        'above the ${_kmhAndKt(i.peakWindPrimaryKmh!)} called.',
      );
    }
  }
  return [for (final p in parts) _sentence(p)];
}

bool _truthy(Object? value) => value != null && '$value'.isNotEmpty && value != false;

String? _opener(FloorInputs i) {
  if (i.overviewComparison != null && i.overviewComparison!.isNotEmpty) {
    return _sentence(i.overviewComparison);
  }
  final onset = i.servedToday['onset_hour'] as String?;
  return _sentence(describeDayRain(
    (i.servedToday['precip_mm'] as num?)?.toDouble(),
    onset,
    _thunderTier(i) != null,
    issuedHour: _issuedHour(i),
    onsetWord: _onsetWord(i, onset),
  ));
}

String? _sky(List<Map<String, Object?>> anchors) {
  final present = [
    for (final a in anchors)
      if (_anchorWhen.containsKey(a['when']) && (a['cover'] as String?)?.isNotEmpty == true)
        (a['when'] as String, a['cover'] as String, a['source'] == skySourceStation),
  ];
  if (present.isEmpty) return null;

  final covers = {for (final (_, cover, _) in present) cover};
  final anyReported = present.any((p) => p.$3);
  if (covers.length == 1 && present.length == _anchorWhen.length && !anyReported) {
    return 'Sky ${present.first.$2.toLowerCase()} through the day.';
  }
  // "as reported": the station's sky for an hour already lived.
  return 'Sky ${_join([
        for (final (at, cover, reported) in present)
          '${cover.toLowerCase()} ${_anchorWhen[at]}${reported ? ' as reported' : ''}'
      ])}.';
}

String? _wind(List<Map<String, Object?>> anchors, double? gustKmh) {
  final present = [
    for (final a in anchors)
      if (_anchorWhen.containsKey(a['when']) && (a['direction'] as String?)?.isNotEmpty == true)
        (a['when'] as String, a['direction'] as String),
  ];
  final gusts = gustKmh == null ? null : 'gusts to ${_kmhAndKt(gustKmh)}';

  if (present.isEmpty) return gusts == null ? null : _sentence(gusts);

  final directions = {for (final (_, d) in present) d};
  final String turn;
  if (directions.length == 1 && present.length == _anchorWhen.length) {
    turn = 'Wind ${present.first.$2} through the day';
  } else {
    turn = 'Wind ${_join([for (final (at, d) in present) '$d ${_anchorWhen[at]}'])}';
  }
  return gusts == null ? '$turn.' : '$turn; $gusts.';
}

String? _uv(double? index) {
  if (index == null) return null;
  return 'UV index ${_fixed(index, 1)} (${uvBand(index)!.toLowerCase()}).';
}

String? _airQuality(FloorInputs i) {
  final readings = [for (final r in i.groundAqi) if (r['aqi'] != null) r];
  if (readings.isNotEmpty) {
    var worst = readings.first;
    for (final r in readings) {
      if ((r['aqi'] as num) > (worst['aqi'] as num)) worst = r;
    }
    final aqi = (worst['aqi'] as num).toInt();
    final where = readings.length > 1
        ? 'at ${worst['name']}, the highest of ${readings.length} stations'
        : 'at ${worst['name']}';
    return 'Air quality $aqi (${aqiBand(aqi)!.toLowerCase()}) $where.';
  }
  final index = i.airQualityIndex;
  if (index == null) return null;
  return 'Air quality $index (${aqiBand(index)!.toLowerCase()}), by the CAMS model.';
}

// --- Extended Outlook ---

List<String?> _extendedParts(FloorInputs i) => [
      _sentence(i.extendedTrend),
      for (final call in i.extendedCalls) _leadSentence(i, call),
    ];

String? _leadSentence(FloorInputs i, Map<String, Object?> call) {
  final lead = (call['lead_time_days'] as num).toInt();
  final rain = call['rain'] as bool;
  final probability = (call['rain_probability_pct'] as num?)?.toInt();

  final weekday = weekdayName(addDays(parseDate(i.date), lead));
  final String words;
  if (rain) {
    words = probability == null ? 'rain likely' : 'rain likely, $probability% chance';
  } else {
    words = probability == null ? 'dry' : 'dry, $probability% chance of rain';
  }

  final highs = _highs(i.highsByLead['$lead'] ?? const []);
  return '$weekday (Day+$lead): $words${highs == null ? '' : '; $highs'}.';
}

String? _highs(List<double> highs) {
  if (highs.isEmpty) return null;
  final lo = highs.reduce((a, b) => a < b ? a : b);
  final hi = highs.reduce((a, b) => a > b ? a : b);
  if (hi - lo > highRangeSpreadC) {
    return 'highs ${_fixed(lo, 0)} to ${_fixed(hi, 0)} °C';
  }
  return 'highs around ${_fixed(mean(highs)!, 0)} °C';
}

// --- Conditions for Boaters ---

List<String?> _boatersParts(FloorInputs i) {
  final gust = i.peakWindSecondaryKmh;
  if (gust == null) return const [];
  return [
    'Peak gust ${_kmhAndKt(gust)}.',
    _thunderTier(i) != null ? _stormGusts : null,
  ];
}

// --- The sign-off ---

List<String?> _signOffParts(FloorInputs i) => [
      i.issuedLocalTime == null ? null : 'Figures issued ${i.issuedLocalTime}.',
      i.modelConfigured ? signOffWithModel : signOffWithoutModel,
    ];

// --- Shared ---

String? _thunderTier(FloorInputs i) => convectiveTier(i.day0PeakCapeJkg);

int? _issuedHour(FloorInputs i) {
  final issued = i.issuedLocalTime;
  if (issued == null || issued.isEmpty) return null;
  return int.tryParse(issued.split(':').first);
}

String? _onsetWord(FloorInputs i, String? onset) {
  final now = _clock(i, i.issuedLocalTime);
  final sunrise = _clock(i, i.sunrise);
  final sunset = _clock(i, i.sunset);
  if (now == null || sunrise == null || sunset == null) return null;
  return onsetWord(onset, now: now, sunrise: sunrise, sunset: sunset);
}

DateTime? _clock(FloorInputs i, String? hhmm) {
  if (hhmm == null) return null;
  final parts = hhmm.split(':');
  if (parts.length < 2) return null;
  final hour = int.tryParse(parts[0]);
  final minute = int.tryParse(parts[1]);
  if (hour == null || minute == null) return null;
  final DateTime day;
  try {
    day = parseDate(i.date);
  } catch (_) {
    return null;
  }
  return DateTime(day.year, day.month, day.day, hour, minute);
}

/// Python's `f"{x:.{places}f}"`: half to even, then printed.
String _fixed(double value, int places) => roundLikePython(value, places).toStringAsFixed(places);

String _kmhAndKt(double kmh) => '${_fixed(kmh, 0)} km/h (${_fixed(kmh / kmhPerKnot, 0)} kt)';

String _join(List<String> items) {
  if (items.length <= 1) return items.join();
  return '${items.sublist(0, items.length - 1).join(', ')} and ${items.last}';
}
