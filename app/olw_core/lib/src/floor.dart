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
import 'outlook.dart' show modelShortNames, shortModelName;
import 'py_text.dart' show comparePython;
import 'tiles.dart' show kmhPerKnot, skySourceStation;

/// Who wrote the narrative — stored beside it as `narrative_source`.
const String narrativeSourceCode = 'code';
const String narrativeSourceLlm = 'llm';

const String signOffWithModel =
    'Written by code; a discussion follows when a model answers.';
const String signOffWithoutModel = 'Written by code.';

const String todayHeading = "## Today's Forecast";
const String extendedHeading = '## Extended Outlook';
const String severeHeading = '## Severe Weather / Hazard Potential';
const String discussionHeading = '## Detailed Discussion';
const String synopticHeading = '### Synoptic Overview';
const String confidenceHeading = '### Forecaster Confidence Notes';

/// The sections code writes, in the page's order — all six since upstream
/// item 191 step (c). [FloorInputs.enabledSections] says which a day shows.
const List<String> floorSectionIds = ['today', 'extended', 'severe', 'secondary', 'synoptic', 'confidence'];
const List<String> defaultFloorSections = ['today', 'extended', 'secondary'];
const _confidenceFindings = 3;
const _basinSteadyHpa = 1.5;

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
    this.enabledSections = defaultFloorSections,
    this.modelsToday = const [],
    this.metServiceName,
    this.metServiceCall,
    this.synopticStatements = const [],
    this.basinPressure,
    this.mslpTrend24h,
    this.reviewFindings = const [],
    this.leadRecords = const [],
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

  /// The other three sections — upstream item 191 step (c): Severe Weather
  /// from each model's peak CAPE; the Synoptic Overview from the ring's
  /// statements, the basin's pressure and the trend overhead; the
  /// Confidence Notes from the record's lead rankings, the review's
  /// established findings and where the call sits among today's models.
  final List<String> enabledSections;
  final List<Map<String, Object?>> modelsToday;
  final String? metServiceName;
  final Map<String, Object?>? metServiceCall;
  final List<String> synopticStatements;
  final Map<String, Object?>? basinPressure;
  final String? mslpTrend24h;
  final List<Map<String, Object?>> reviewFindings;
  final List<Map<String, Object?>> leadRecords;

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
        enabledSections: j['enabled_sections'] == null
            ? defaultFloorSections
            : [for (final s in j['enabled_sections'] as List) s as String],
        modelsToday: _maps(j['models_today']),
        metServiceName: j['met_service_name'] as String?,
        metServiceCall: (j['met_service_call'] as Map?)?.cast<String, Object?>(),
        synopticStatements: [for (final s in (j['synoptic_statements'] as List?) ?? const []) s as String],
        basinPressure: (j['basin_pressure'] as Map?)?.cast<String, Object?>(),
        mslpTrend24h: j['mslp_trend_24h'] as String?,
        reviewFindings: _maps(j['review_findings']),
        leadRecords: _maps(j['lead_records']),
      );

  static List<Map<String, Object?>> _maps(Object? raw) => [
        for (final m in (raw as List?) ?? const []) (m as Map).cast<String, Object?>()
      ];
}

/// The floor as Markdown with the write-up's headings. Mirrors `compose_floor`.
String composeFloor(FloorInputs i) {
  final parts = <String>[];
  var discussionOpen = false;
  for (final (section, heading, text) in _floorSectionRows(i)) {
    if (text.isEmpty || !i.enabledSections.contains(section)) continue;
    if ((section == 'synoptic' || section == 'confidence') && !discussionOpen) {
      parts.add(discussionHeading);
      discussionOpen = true;
    }
    parts.add('$heading\n\n$text');
  }
  parts.add(signOff(i));
  return '${parts.join('\n\n')}\n';
}

/// Every text code can write, by section id, enabled or not — mirrors
/// `floor_section_texts`, for the writer's composition.
Map<String, String> floorSectionTexts(FloorInputs i) => {
      for (final (section, _, text) in _floorSectionRows(i))
        if (text.isNotEmpty) section: text,
    };

/// The stamp and who wrote it — mirrors `sign_off`.
String signOff(FloorInputs i) => _sentences(_signOffParts(i));

List<(String, String, String)> _floorSectionRows(FloorInputs i) {
  final outlook = i.extendedOutlook?.trim();
  final extended = outlook != null && outlook.isNotEmpty ? outlook : _sentences(_extendedParts(i));
  final boaters = i.secondaryName == null ? '' : _sentences(_boatersParts(i));
  return [
    ('today', todayHeading, _sentences(_todayParts(i))),
    ('extended', extendedHeading, extended),
    ('severe', severeHeading, _sentences(_severeParts(i))),
    ('secondary', '## ${i.secondaryName} — Conditions for Boaters', boaters),
    ('synoptic', synopticHeading, _sentences(_synopticParts(i))),
    ('confidence', confidenceHeading, _sentences(_confidenceParts(i))),
  ];
}

// --- Severe Weather — upstream item 191 step (c)

List<String?> _severeParts(FloorInputs i) {
  final tier = _thunderTier(i);
  if (tier == null) return const [];
  final withCape = [
    for (final m in i.modelsToday) if (m['peak_cape_jkg'] != null) ('${m['model']}', (m['peak_cape_jkg'] as num).toDouble()),
  ];
  String? cape;
  if (withCape.isNotEmpty) {
    final indexed = [for (var k = 0; k < withCape.length; k++) (k, withCape[k])];
    indexed.sort((a, b) => a.$2.$2 != b.$2.$2 ? (b.$2.$2 > a.$2.$2 ? 1 : -1) : a.$1 - b.$1);
    cape = 'Convective instability today: '
        '${_join([for (final e in indexed) '${e.$2.$1} ${roundLikePython(e.$2.$2, 0).toInt()} J/kg'])}.';
  }
  final gust = i.peakWindPrimaryKmh;
  final hazard = gust != null
      ? 'Thunder $tier; any thunderstorm brings sudden gusts well above the ${_kmhAndKt(gust)} forecast.'
      : 'Thunder $tier; any thunderstorm brings sudden gusts well above the forecast wind.';
  return [cape, hazard];
}

// --- Synoptic Overview — upstream item 191 step (c)

List<String?> _synopticParts(FloorInputs i) {
  final parts = <String?>[];
  if (i.synopticStatements.isNotEmpty) {
    parts.addAll(i.synopticStatements);
  } else {
    parts.add('The large-scale pressure ring could not be assessed this run.');
  }
  final b = i.basinPressure;
  if (b != null && b['today_min_hpa'] != null && b['today_max_hpa'] != null) {
    final change = b['change_72h_hpa'] as num?;
    final String tendency;
    if (change == null) {
      tendency = '';
    } else if (change.abs() < _basinSteadyHpa) {
      tendency = ', near-steady over three days (${_signed1(change.toDouble())} hPa)';
    } else {
      tendency = ", ${change > 0 ? 'rising' : 'falling'} by ${_fixed(change.abs().toDouble(), 1)} hPa over three days";
    }
    parts.add(
      "Across the basin's ${b['points']} points, pressure today sits between ${_str(b['today_min_hpa'])} "
      'and ${_str(b['today_max_hpa'])} hPa$tendency.',
    );
  }
  if (i.mslpTrend24h != null && i.mslpTrend24h!.isNotEmpty) {
    parts.add('Pressure here over the last 24 hours: ${i.mslpTrend24h}.');
  }
  return parts;
}

// --- Forecaster Confidence Notes — upstream item 191 step (c)

List<String?> _confidenceParts(FloorInputs i) {
  final parts = <String?>[];
  final ranked = [
    for (final r in i.leadRecords) if (r['best_model'] != null && '${r['best_model']}'.isNotEmpty && r['rain_pct'] != null) r,
  ];
  if (ranked.isNotEmpty) {
    parts.add('On rain the record ranks ${_join([
          for (final r in ranked)
            "${shortModelName('${r['best_model']}')} first at Day+${r['lead_time_days']}, right "
                '${roundLikePython((r['rain_pct'] as num).toDouble(), 0).toInt()}% of the last 30 checks'
        ])}.');
  } else if (i.leadRecords.isNotEmpty) {
    parts.add('The record is too thin to rank the models on rain yet.');
  }

  final highs = [for (final m in i.modelsToday) if (m['high_c'] != null) ('${m['model']}', (m['high_c'] as num).toDouble())];
  final callHigh = (i.servedToday['high_c'] as num?)?.toDouble();
  if (highs.length >= 2 && callHigh != null) {
    var warmest = highs.first;
    var coolest = highs.first;
    for (final h in highs) {
      if (h.$2 > warmest.$2) warmest = h;
      if (h.$2 < coolest.$2) coolest = h;
    }
    if (warmest.$2 != coolest.$2) {
      final where = coolest.$2 < callHigh && callHigh < warmest.$2
          ? 'between them'
          : callHigh >= warmest.$2
              ? 'at the warm end'
              : 'at the cool end';
      parts.add(
        "On today's high ${warmest.$1} is the warmest model at ${_fixed(warmest.$2, 1)} °C and ${coolest.$1} the "
        "coolest at ${_fixed(coolest.$2, 1)} °C; the call's ${_fixed(callHigh, 1)} °C sits $where.",
      );
    }
  }

  final met = i.metServiceCall;
  if (i.metServiceName != null && met != null) {
    final bits = <String>[];
    if (met['high_c'] != null) bits.add('a high of ${_fixed((met['high_c'] as num).toDouble(), 1)} °C');
    if (met['rain'] != null) bits.add(met['rain'] == true ? 'rain' : 'a dry day');
    if (bits.isNotEmpty) {
      final servedRain = i.servedToday['rain'];
      var agreement = '';
      if (met['rain'] != null && servedRain != null) {
        agreement = met['rain'] == servedRain ? ', agreeing with the call on rain' : ', against the call on rain';
      }
      parts.add('${i.metServiceName} calls ${_join(bits)}$agreement.');
    }
  }

  final indexed = [for (var k = 0; k < i.reviewFindings.length; k++) (k, i.reviewFindings[k])];
  indexed.sort((a, b) {
    final ka = a.$2['kind'] == 'ranking' ? 0 : 1;
    final kb = b.$2['kind'] == 'ranking' ? 0 : 1;
    if (ka != kb) return ka - kb;
    // Python sorts `str(None)`, so a missing claim orders as "None".
    final c = comparePython(_pyStr(a.$2['claim']), _pyStr(b.$2['claim']));
    return c != 0 ? c : a.$1 - b.$1;
  });
  for (final f in indexed.take(_confidenceFindings)) {
    final claim = _shortNames('${f.$2['claim'] ?? ''}');
    if (claim.isNotEmpty) parts.add(claim.endsWith('.') ? claim : '$claim.');
  }
  return parts;
}

String _shortNames(String text) {
  final ids = modelShortNames.keys.toList();
  final indexed = [for (var k = 0; k < ids.length; k++) (k, ids[k])];
  indexed.sort((a, b) => b.$2.length != a.$2.length ? b.$2.length - a.$2.length : a.$1 - b.$1);
  var out = text;
  for (final e in indexed) {
    out = out.replaceAll(e.$2, modelShortNames[e.$2]!);
  }
  return out;
}

String _pyStr(Object? value) => value == null ? 'None' : '$value';

/// Python's `f"{x:+.1f}"`: the sign always, a negative zero kept.
String _signed1(double value) => '${value.isNegative ? '-' : '+'}${_fixed(value.abs(), 1)}';

/// Python's `str(value)` for a figure the basin holds: a double with its
/// point, an int without.
String _str(Object? value) {
  if (value is double) {
    return value == value.truncateToDouble() && value.abs() < 1e16 ? '${value.toInt()}.0' : '$value';
  }
  return '$value';
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
