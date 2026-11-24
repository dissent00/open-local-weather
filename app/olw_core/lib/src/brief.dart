/// The brief: the writer's input — upstream `brief.py`, ROADMAP item 191.
///
/// Parsed from the user prompt the run built and the stored day, rendered
/// in two tiers for the sections a deployment writes; pinned by
/// `spec/vectors/brief_inputs.json` and `brief.json`. The Python side
/// carries the reasoning; this is the same text from the same values.
library;

import 'dart:convert';

import 'outlook.dart' show modelShortNames, shortModelName;
import 'py_text.dart';
import 'rounding.dart';
import 'models.dart' show formatKmh;
import 'dates.dart' show addDays, formatDate, parseDate;

const sectionToday = 'today';
const sectionExtended = 'extended';
const sectionSevere = 'severe';
const sectionSecondary = 'secondary';
const sectionSynoptic = 'synoptic';
const sectionConfidence = 'confidence';
const List<String> briefSections = [
  sectionToday, sectionExtended, sectionSevere, sectionSecondary, sectionSynoptic, sectionConfidence,
];
const List<String> defaultBriefSections = [sectionToday, sectionExtended, sectionSevere, sectionSecondary];

const tierFull = 'full';
const tierMini = 'mini';

const Map<String, List<String>> _blocksBySection = {
  sectionToday: [
    'issued', 'windows', 'call', 'day_over_day', 'thunder', 'sky', 'wind', 'uv', 'observed',
    'footnotes', 'air', 'bulletin',
  ],
  sectionExtended: ['calendar', 'next_three_days', 'sky_by_day', 'days_ahead', 'models_ahead', 'record'],
  sectionSevere: ['thunder', 'cape', 'call', 'bulletin'],
  sectionSecondary: ['secondary_wind', 'thunder'],
  sectionSynoptic: ['synoptic', 'basin', 'recency', 'call'],
  sectionConfidence: ['models_today', 'review', 'record', 'sufficiency', 'bulletin', 'recency'],
};
const Set<String> _miniDroppedBlocks = {
  'observed', 'footnotes', 'secondary_wind', 'sky_by_day', 'uv', 'recency', 'synoptic', 'calendar', 'windows',
};
const _reviewFindingsFull = 12;
const _reviewFindingsMini = 3;
const _bulletinMaxChars = 700;
const _basinSteadyHpa = 1.5;
const _established = 'established';
const _unavailable = 'Unavailable';
const Set<String> _hiddenModels = {'olw_blend', 'olw_code_blend', 'olw_lite'};

const List<String> _order = [
  'issued', 'windows', 'calendar', 'recency', 'call', 'day_over_day', 'thunder', 'cape', 'sky', 'wind',
  'secondary_wind', 'uv', 'observed', 'footnotes', 'air', 'bulletin', 'next_three_days', 'sky_by_day',
  'days_ahead', 'models_today', 'models_ahead', 'synoptic', 'basin', 'record', 'review', 'sufficiency',
];

typedef _Row = Map<String, Object?>;

/// Everything the brief says, as plain values; `toJson`/`fromJson`
/// round-trip so a vector case renders the same text.
class BriefInputs {
  BriefInputs({
    this.issued,
    this.calendar = const [],
    this.windows = const [],
    this.recency,
    this.instability,
    this.dayOverDay,
    this.nextThreeDays,
    this.skyAnchors,
    this.skyByDay = const [],
    this.windDirections,
    this.windShift,
    this.secondaryWind,
    this.peakUv,
    this.calibratedGustKmh,
    this.observedSoFar,
    this.footnotes = const [],
    this.groundAqiStations = const [],
    this.groundAqiSummary,
    this.groundAqiLastKnown,
    this.localBulletin,
    this.predictions = const [],
    this.synopticStatements = const [],
    this.basinPressure,
    this.reviewFindings = const [],
    this.dataSufficiency,
    this.trackRecord = const [],
    this.servedCall,
    this.tempDisplay,
    this.extendedDays = const [],
    this.secondaryName,
    this.metServiceName,
    this.metServiceModelId,
  });

  final String? issued;
  final List<_Row> calendar;
  final List<String> windows;
  final _Row? recency;
  final _Row? instability;
  final String? dayOverDay;
  final String? nextThreeDays;
  final _Row? skyAnchors;
  final List<_Row> skyByDay;
  final _Row? windDirections;
  final String? windShift;
  final _Row? secondaryWind;
  final _Row? peakUv;
  final double? calibratedGustKmh;
  final String? observedSoFar;
  final List<String> footnotes;
  final List<_Row> groundAqiStations;
  final _Row? groundAqiSummary;
  final _Row? groundAqiLastKnown;
  final String? localBulletin;
  final List<_Row> predictions;
  final List<String> synopticStatements;
  final _Row? basinPressure;
  final List<_Row> reviewFindings;
  final String? dataSufficiency;
  final List<_Row> trackRecord;
  final _Row? servedCall;
  final String? tempDisplay;
  final List<_Row> extendedDays;
  final String? secondaryName;
  final String? metServiceName;
  final String? metServiceModelId;

  Map<String, Object?> toJson() => {
        'issued': issued,
        'calendar': calendar,
        'windows': windows,
        'recency': recency,
        'instability': instability,
        'day_over_day': dayOverDay,
        'next_three_days': nextThreeDays,
        'sky_anchors': skyAnchors,
        'sky_by_day': skyByDay,
        'wind_directions': windDirections,
        'wind_shift': windShift,
        'secondary_wind': secondaryWind,
        'peak_uv': peakUv,
        'calibrated_gust_kmh': calibratedGustKmh,
        'observed_so_far': observedSoFar,
        'footnotes': footnotes,
        'ground_aqi_stations': groundAqiStations,
        'ground_aqi_summary': groundAqiSummary,
        'ground_aqi_last_known': groundAqiLastKnown,
        'local_bulletin': localBulletin,
        'predictions': predictions,
        'synoptic_statements': synopticStatements,
        'basin_pressure': basinPressure,
        'review_findings': reviewFindings,
        'data_sufficiency': dataSufficiency,
        'track_record': trackRecord,
        'served_call': servedCall,
        'temp_display': tempDisplay,
        'extended_days': extendedDays,
        'secondary_name': secondaryName,
        'met_service_name': metServiceName,
        'met_service_model_id': metServiceModelId,
      };

  factory BriefInputs.fromJson(Map<String, Object?> raw) => BriefInputs(
        issued: raw['issued'] as String?,
        calendar: _rows(raw['calendar']),
        windows: _strings(raw['windows']),
        recency: _map(raw['recency']),
        instability: _map(raw['instability']),
        dayOverDay: raw['day_over_day'] as String?,
        nextThreeDays: raw['next_three_days'] as String?,
        skyAnchors: _map(raw['sky_anchors']),
        skyByDay: _rows(raw['sky_by_day']),
        windDirections: _map(raw['wind_directions']),
        windShift: raw['wind_shift'] as String?,
        secondaryWind: _map(raw['secondary_wind']),
        peakUv: _map(raw['peak_uv']),
        calibratedGustKmh: (raw['calibrated_gust_kmh'] as num?)?.toDouble(),
        observedSoFar: raw['observed_so_far'] as String?,
        footnotes: _strings(raw['footnotes']),
        groundAqiStations: _rows(raw['ground_aqi_stations']),
        groundAqiSummary: _map(raw['ground_aqi_summary']),
        groundAqiLastKnown: _map(raw['ground_aqi_last_known']),
        localBulletin: raw['local_bulletin'] as String?,
        predictions: _rows(raw['predictions']),
        synopticStatements: _strings(raw['synoptic_statements']),
        basinPressure: _map(raw['basin_pressure']),
        reviewFindings: _rows(raw['review_findings']),
        dataSufficiency: raw['data_sufficiency'] as String?,
        trackRecord: _rows(raw['track_record']),
        servedCall: _map(raw['served_call']),
        tempDisplay: raw['temp_display'] as String?,
        extendedDays: _rows(raw['extended_days']),
        secondaryName: raw['secondary_name'] as String?,
        metServiceName: raw['met_service_name'] as String?,
        metServiceModelId: raw['met_service_model_id'] as String?,
      );

  /// The user prompt and the stored day's JSON, read together.
  factory BriefInputs.fromUserPrompt(
    String userPrompt,
    Map<String, Object?> entry, {
    required String? secondaryName,
    required String? metServiceName,
    String? metServiceModelId,
  }) {
    final b = _blocks(userPrompt);
    final guidance = _jsonBody(b["TODAY'S MULTI-MODEL GUIDANCE"] ?? '') ?? const <String, Object?>{};
    final synoptic = _map(guidance['synoptic_scale_pressure']) ?? const <String, Object?>{};
    final review = _jsonBody(b['LONG-RUN REVIEW'] ?? '') ?? const <String, Object?>{};
    final trackColumns = ['model', 'lead_time_days', 'rolling_30_rain_pct', 'all_time_checks'];
    return BriefInputs(
      issued: _issued(b['PREAMBLE'] ?? ''),
      calendar: _tableRows(b['CALENDAR']),
      windows: [
        for (final line in _phraseLines(b['FORECAST WINDOWS']))
          if (line.startsWith('- ')) line.substring(2),
      ],
      recency: _jsonBody(b['GUIDANCE RECENCY'] ?? ''),
      instability: _jsonBody(b['CONVECTIVE INSTABILITY'] ?? ''),
      dayOverDay: entry['overview_comparison'] as String?,
      nextThreeDays: _phrase(b['NEXT THREE DAYS']),
      skyAnchors: _jsonBody(b['SKY AT EACH ANCHOR'] ?? ''),
      skyByDay: _tableRows(b['SKY BY DAY']),
      windDirections: _jsonBody(b['WIND DIRECTION'] ?? ''),
      windShift: _phrase(b['WIND SHIFT']),
      secondaryWind: _jsonBody(b['SECONDARY POINT WIND'] ?? ''),
      peakUv: _jsonBody(b['PEAK UV INDEX'] ?? ''),
      calibratedGustKmh: _leadingNumber(_phrase(b['CALIBRATED PEAK GUST'])),
      observedSoFar: _phrase(b['OBSERVED SO FAR TODAY']),
      footnotes: [
        ..._phraseLines(b['OVERNIGHT LOW FOOTNOTE']),
        ..._phraseLines(b['OBSERVATION FOOTNOTES']),
      ],
      groundAqiStations: _tableRows(b['GROUND AQI STATIONS']),
      groundAqiSummary: _jsonBody(b['GROUND AQI SUMMARY'] ?? ''),
      groundAqiLastKnown: _jsonBody(b['GROUND AQI LAST KNOWN'] ?? ''),
      localBulletin: _phrase(b['LOCAL BULLETIN'], keepUnavailable: true),
      predictions: _tableRows(b['EXTRACTED PER-MODEL PREDICTIONS']),
      synopticStatements: _strings(synoptic['statements']),
      basinPressure: reduceBasinPressure(guidance['regional_pressure'] as List?),
      reviewFindings: [
        for (final f in (review['findings'] as List? ?? const []))
          if ((f as Map)['confidence'] == _established)
            {'kind': f['kind'], 'checks': f['checks'], 'claim': f['claim']},
      ],
      dataSufficiency: review['data_sufficiency'] as String?,
      trackRecord: [
        for (final row in _tableRows(b['MODEL TRACK RECORD']))
          {for (final k in trackColumns) k: row[k]},
      ],
      servedCall: _map(entry['served_call']),
      tempDisplay: entry['temp_high_low_display'] as String?,
      extendedDays: _rows(entry['extended_days']),
      secondaryName: secondaryName,
      metServiceName: metServiceName,
      metServiceModelId: metServiceModelId,
    );
  }
}

/// The regional pressure points reduced to one line's worth — mirrors
/// `brief.basin_pressure`.
Map<String, Object?>? reduceBasinPressure(List? points) {
  final today = <double>[];
  final changes = <double>[];
  for (final point in points ?? const []) {
    final daily = _map((point as Map)['daily']) ?? const <String, Object?>{};
    final series = daily['pressure_msl_mean'] as List? ?? const [];
    final values = [for (final v in series.take(3)) if (v is num) v];
    if (series.isNotEmpty && series[0] is num) {
      today.add((series[0] as num).toDouble());
    }
    if (values.length == 3 && series.length >= 3) {
      changes.add((series[2] as num).toDouble() - (series[0] as num).toDouble());
    }
  }
  if (today.isEmpty) return null;

  double? change;
  if (changes.isNotEmpty) {
    var sum = 0.0;
    for (final c in changes) {
      sum += c;
    }
    change = roundLikePython(sum / changes.length, 1);
  }
  return {
    'points': today.length,
    'today_min_hpa': roundLikePython(today.reduce((a, b) => a < b ? a : b), 1),
    'today_max_hpa': roundLikePython(today.reduce((a, b) => a > b ? a : b), 1),
    'change_72h_hpa': change,
  };
}

// --- the renderer -------------------------------------------------------

/// The brief's text for the enabled sections at the given tier.
String renderBrief(BriefInputs inputs, {String tier = tierFull, List<String> sections = defaultBriefSections}) {
  if (tier != tierFull && tier != tierMini) {
    throw ArgumentError('unknown brief tier $tier');
  }
  final wanted = <String>[];
  for (final section in briefSections) {
    if (!sections.contains(section)) continue;
    for (final block in _blocksBySection[section]!) {
      if (!wanted.contains(block)) wanted.add(block);
    }
  }
  final kept = tier == tierMini ? [for (final b in wanted) if (!_miniDroppedBlocks.contains(b)) b] : wanted;

  final renderers = <String, String? Function(BriefInputs, String)>{
    'issued': _issuedBlock, 'windows': _windowsBlock, 'calendar': _calendarBlock, 'recency': _recencyBlock,
    'call': _callBlock, 'day_over_day': _dayOverDayBlock, 'thunder': _thunderBlock, 'cape': _capeBlock,
    'sky': _skyBlock, 'wind': _windBlock, 'secondary_wind': _secondaryWindBlock, 'uv': _uvBlock,
    'observed': _observedBlock, 'footnotes': _footnotesBlock, 'air': _airBlock, 'bulletin': _bulletinBlock,
    'next_three_days': _nextThreeDaysBlock, 'sky_by_day': _skyByDayBlock, 'days_ahead': _daysAheadBlock,
    'models_today': _modelsTodayBlock, 'models_ahead': _modelsAheadBlock, 'synoptic': _synopticBlock,
    'basin': _basinBlock, 'record': _recordBlock, 'review': _reviewBlock, 'sufficiency': _sufficiencyBlock,
  };
  final parts = <String>[];
  for (final block in _order) {
    if (!kept.contains(block)) continue;
    final text = renderers[block]!(inputs, tier);
    if (text != null && text.isNotEmpty) parts.add(text);
  }
  return '${parts.join('\n')}\n';
}

String? _issuedBlock(BriefInputs i, String tier) => i.issued != null && i.issued!.isNotEmpty ? 'ISSUED: ${i.issued}' : null;

String? _windowsBlock(BriefInputs i, String tier) {
  if (i.windows.isEmpty) return null;
  return 'WINDOWS (the periods this forecast covers, with their hours): ${i.windows.join('; ')}';
}

String? _calendarBlock(BriefInputs i, String tier) {
  if (i.calendar.isEmpty) return null;
  final days = <String>[];
  for (final row in i.calendar) {
    final label = '${row['day_name']} ${row['date']}';
    days.add(label + (_int(row['lead_time_days']) == 0 ? ' (today)' : ''));
  }
  return 'CALENDAR (use these day names and dates as given; derive no others): ${days.join('; ')}';
}

String? _recencyBlock(BriefInputs i, String tier) {
  final r = i.recency;
  if (r == null || r.isEmpty) return null;
  final at = headLikePython('${r['models_last_aligned_at'] ?? ''}', 16).replaceAll('T', ' ');
  return "GUIDANCE: the models' cycle of $at UTC, ${_str(r['hours_old'])} h old at issue.";
}

String? _callBlock(BriefInputs i, String tier) {
  final c = _map((i.servedCall ?? const {})['today_properties']) ?? const <String, Object?>{};
  if (c.isEmpty) return null;
  final lines = ["THE CALL (code's, already served to the reader; state these figures as given):"];
  final rain = c['rain'];
  final rainWords = c['rain_expected'];
  final onset = c['onset_window'] ?? c['onset_hour'];
  final amount = c['precip_mm'];
  final prob = c['rain_probability_pct'];
  var rainLine = '  rain: ${rain == true ? 'yes' : rain == false ? 'no' : 'unknown'}';
  if (rainWords != null && '$rainWords'.isNotEmpty) rainLine += ', $rainWords';
  if (rain == true && onset != null) rainLine += '; onset $onset';
  if (rain == true && amount != null) rainLine += '; ${_num(amount)} mm';
  if (prob != null) rainLine += '; ${_num(prob)}%';
  lines.add(rainLine);
  if (i.tempDisplay != null && i.tempDisplay!.isNotEmpty) lines.add('  temperature: ${i.tempDisplay}');
  final gust = c['peak_wind_primary_kmh'];
  if (gust != null) {
    var gustLine = '  peak gust: ${_kmhAndKt((gust as num).toDouble())} ashore';
    final secondaryGust = c['peak_wind_secondary_kmh'];
    if (secondaryGust != null && i.secondaryName != null) {
      gustLine += '; ${_kmhAndKt((secondaryGust as num).toDouble())} on ${i.secondaryName}';
    }
    lines.add(gustLine);
  }
  final trend = c['mslp_trend_24h'];
  final pattern = c['synoptic_pattern'];
  if (_truthy(trend) || _truthy(pattern)) {
    final parts = [if (_truthy(trend)) 'trend $trend', if (_truthy(pattern)) '$pattern'];
    lines.add('  pressure: ${parts.join('; ')}');
  }
  final aqi = c['air_quality_aqi'];
  if (aqi != null) lines.add('  air quality: AQI ${_num(aqi)} (model estimate)');
  for (final lead in (i.servedCall ?? const {})['extended_properties'] as List? ?? const []) {
    final l = lead as Map;
    final day = _dayName(i, _int(l['lead_time_days']));
    final r = l['rain'];
    final words = r == true ? 'rain' : r == false ? 'dry' : 'no call';
    final p = l['rain_probability_pct'];
    lines.add(
      '  Day+${l['lead_time_days']}${day != null ? ' ($day)' : ''}: $words${p != null ? ', ${_num(p)}%' : ''}',
    );
  }
  return lines.join('\n');
}

String? _dayOverDayBlock(BriefInputs i, String tier) =>
    _truthy(i.dayOverDay) ? 'SINCE YESTERDAY (verbatim): ${i.dayOverDay}' : null;

String? _thunderBlock(BriefInputs i, String tier) {
  final inst = i.instability;
  if (inst == null || inst.isEmpty) return null;
  final timing = inst['timing'];
  if (inst['convective'] != true) {
    return "THUNDER: no model's instability supports thunderstorms; say nothing about thunder.";
  }
  if (_truthy(timing)) return 'THUNDER (verbatim, capitalised, with a full stop): $timing';
  return 'THUNDER: possible; the models do not agree on when, so name no hour.';
}

String? _capeBlock(BriefInputs i, String tier) {
  final inst = i.instability ?? const <String, Object?>{};
  final byModel = _map(inst['peak_cape_by_model']) ?? const <String, Object?>{};
  if (byModel.isEmpty) return null;
  final parts = [
    for (final e in byModel.entries) if (e.value != null) '${shortModelName(e.key)} ${_num(e.value, 0)}',
  ];
  final above = [for (final m in inst['models_above_threshold'] as List? ?? const []) shortModelName('$m')];
  // Ahead, not today: the forward window's peak; MODELS TODAY no longer
  // carries the calendar day's — see brief.py.
  var line = 'PEAK CAPE AHEAD (J/kg, per model, over the hours the THUNDER line covers): ${parts.join('; ')}';
  if (above.isNotEmpty) line += '. Above the thunderstorm threshold: ${above.join(', ')}';
  final peakHour = inst['peak_hour'];
  if (_truthy(peakHour)) line += '; peak at $peakHour';
  return '$line.';
}

String? _skyBlock(BriefInputs i, String tier) {
  final anchors = i.skyAnchors;
  if (anchors == null || anchors.isEmpty) return null;
  final parts = [for (final e in anchors.entries) if (_truthy(e.value)) '${e.key} ${e.value}'];
  return parts.isEmpty ? null : "SKY (the tile's word at each anchor; use these words for these hours): ${parts.join('; ')}";
}

String? _windBlock(BriefInputs i, String tier) {
  final lines = <String>[];
  if (_truthy(i.windShift)) lines.add('WIND (verbatim): ${i.windShift}');
  final directions = i.windDirections;
  if (directions != null && directions.isNotEmpty) {
    final parts = [
      for (final e in directions.entries) '${e.key} ${_truthy(e.value) ? e.value : 'no agreed bearing'}',
    ];
    lines.add('WIND AT EACH ANCHOR (name a bearing only at an anchor that has one): ${parts.join('; ')}');
  }
  return lines.isEmpty ? null : lines.join('\n');
}

String? _secondaryWindBlock(BriefInputs i, String tier) {
  final w = i.secondaryWind;
  if (w == null || w.isEmpty || i.secondaryName == null) return null;
  var line = '${i.secondaryName!.toUpperCase()} WIND (verbatim timeline): ${w['timeline']}';
  final gust = w['consensus_gust_kmh'];
  if (gust != null) line += '; peak gust ${_kmhAndKt((gust as num).toDouble())}';
  return line;
}

String? _uvBlock(BriefInputs i, String tier) {
  final uv = i.peakUv;
  if (uv == null || uv.isEmpty || uv['index'] == null) return null;
  final day = _dayForDate(i, '${uv['date']}') ?? '${uv['date']}';
  return 'UV: peak index ${_num(uv['index'])} $day, from ${shortModelName('${uv['source']}')}.';
}

String? _observedBlock(BriefInputs i, String tier) =>
    _truthy(i.observedSoFar) ? 'OBSERVED SO FAR (verbatim; measured, not forecast): ${i.observedSoFar}' : null;

String? _footnotesBlock(BriefInputs i, String tier) {
  if (i.footnotes.isEmpty) return null;
  return 'FOOTNOTES (verbatim or not at all):\n${[for (final f in i.footnotes) '  - $f'].join('\n')}';
}

String? _airBlock(BriefInputs i, String tier) {
  final lines = <String>[];
  if (i.groundAqiStations.isNotEmpty) {
    final rows = <String>[];
    for (final s in i.groundAqiStations) {
      if (s['aqi'] == null) continue;
      final pm = [
        if (s['pm25'] != null) 'PM2.5 ${s['pm25']}',
        if (s['pm10'] != null) 'PM10 ${s['pm10']}',
      ].join(', ');
      final stale = '${s['stale']}'.toLowerCase() == 'true' ? ' (stale)' : '';
      rows.add("${s['name']} AQI ${s['aqi']}${pm.isNotEmpty ? ' ($pm)' : ''}$stale");
    }
    if (rows.isNotEmpty) lines.add('AIR QUALITY, ground stations: ${rows.join('; ')}');
  }
  final summary = i.groundAqiSummary;
  if (summary != null && _truthy(summary['highest_station_name'])) {
    lines.add(
      "  highest station: ${summary['highest_station_name']} at AQI ${summary['aqi_max']}, "
      "${summary['stations_with_aqi']} of ${summary['stations_total']} reporting",
    );
  }
  final last = i.groundAqiLastKnown;
  if (tier == tierFull && last != null && last['aqi'] != null && i.groundAqiStations.isEmpty) {
    lines.add("  last known reading: ${last['station_name']} AQI ${last['aqi']}, ${last['hours_old']} h old");
  }
  return lines.isEmpty ? null : lines.join('\n');
}

String? _bulletinBlock(BriefInputs i, String tier) {
  if (i.metServiceName == null) return null;
  var text = stripLikePython(i.localBulletin ?? '');
  if (text.isEmpty || text.startsWith(_unavailable)) {
    return 'LOCAL MET SERVICE (${i.metServiceName}): no bulletin this run; say so in one clause.';
  }
  if (lengthLikePython(text) > _bulletinMaxChars) {
    text = '${rstripLikePython(headLikePython(text, _bulletinMaxChars))} …';
  }
  final lines = [
    for (final l in splitLinesLikePython(text)) if (stripLikePython(l).isNotEmpty) '  $l',
  ];
  return 'LOCAL MET SERVICE (${i.metServiceName}), its own forecast:\n${lines.join('\n')}';
}

String? _nextThreeDaysBlock(BriefInputs i, String tier) =>
    _truthy(i.nextThreeDays) ? 'NEXT THREE DAYS (verbatim): ${i.nextThreeDays}' : null;

String? _skyByDayBlock(BriefInputs i, String tier) {
  if (i.skyByDay.isEmpty) return null;
  final parts = [for (final row in i.skyByDay) if (_truthy(row['sky'])) '${row['day_name']} ${row['sky']}'];
  return parts.isEmpty ? null : 'SKY BY DAY (one word per day, as given): ${parts.join('; ')}';
}

String? _daysAheadBlock(BriefInputs i, String tier) {
  if (i.extendedDays.isEmpty) return null;
  final lines = ['DAYS AHEAD (the models together, per day):'];
  for (final d in i.extendedDays) {
    final bits = <String>[];
    final models = d['models'] as List? ?? const [];
    if (models.isNotEmpty) bits.add('${d['wet_votes'] ?? 0} of ${models.length} models wet');
    final amounts = [
      for (final v in (_map(d['precip_by_model']) ?? const <String, Object?>{}).values) if (v is num) v.toDouble(),
    ];
    if (amounts.isNotEmpty) {
      final lo = amounts.reduce((a, b) => a < b ? a : b);
      final hi = amounts.reduce((a, b) => a > b ? a : b);
      bits.add(lo != hi ? 'rain ${_num(lo, 1)}–${_num(hi, 1)} mm by model' : 'rain ${_num(lo, 1)} mm');
    }
    if (d['high_min_c'] != null && d['high_max_c'] != null) {
      final lo = d['high_min_c'] as num;
      final hi = d['high_max_c'] as num;
      bits.add(_num(lo, 0) != _num(hi, 0) ? 'high ${_num(lo, 0)}–${_num(hi, 0)} °C' : 'high ${_num(lo, 0)} °C');
    }
    if (d['low_c'] != null) bits.add('low ${_num(d['low_c'], 0)} °C');
    if (d['wind_kmh'] != null) bits.add('gusts to ${_kmhAndKt((d['wind_kmh'] as num).toDouble())}');
    if (_truthy(d['thunder'])) bits.add('thunder ${d['thunder']}');
    if (_truthy(d['sky'])) bits.add('${d['sky']}'.toLowerCase());
    lines.add("  ${d['day_name']} (Day+${d['lead_time_days']}): ${bits.join('; ')}");
  }
  return lines.join('\n');
}

const List<(String, String)> _modelColumnsToday = [
  ('rain', 'rain'), ('onset', 'onset'), ('precip_mm', 'mm'), ('rain_probability_pct', '%'),
  ('wind_kmh', 'gust km/h'), ('high_c', 'high'), ('low_c', 'low'),
];
const List<(String, String)> _modelColumnsAhead = [
  ('rain', 'rain'), ('precip_mm', 'mm'), ('rain_probability_pct', '%'), ('high_c', 'high'), ('low_c', 'low'),
];

String? _modelsTodayBlock(BriefInputs i, String tier) => _modelsTable(
    "MODELS TODAY (each model's own Day+0 call; these exact values are scored)", i, 'day0', _modelColumnsToday);

String? _modelsAheadBlock(BriefInputs i, String tier) {
  final parts = [
    for (final lead in [3, 7]) _modelsTable('MODELS AT DAY+$lead', i, 'day$lead', _modelColumnsAhead),
  ];
  final present = [for (final p in parts) if (p != null) p];
  return present.isEmpty ? null : present.join('\n');
}

String? _modelsTable(String title, BriefInputs i, String lead, List<(String, String)> columns) {
  final rows = [for (final r in i.predictions) if (r['lead'] == lead) r];
  if (rows.isEmpty) return null;
  final header = 'model\t${[for (final c in columns) c.$2].join('\t')}';
  final lines = ['$title:', header];
  for (final r in rows) {
    final cells = [for (final c in columns) _cell(r[c.$1])];
    lines.add('${_name(i, '${r['model']}')}\t${cells.join('\t')}');
  }
  return lines.join('\n');
}

String? _synopticBlock(BriefInputs i, String tier) {
  if (i.synopticStatements.isEmpty) {
    return 'LARGE SCALE: the pressure ring could not be assessed this run; say so rather than substituting the local gradient.';
  }
  return 'LARGE SCALE (verbatim statements, from a nine-point ring about 2,600 km across):\n'
      '${[for (final s in i.synopticStatements) '  - $s'].join('\n')}';
}

String? _basinBlock(BriefInputs i, String tier) {
  final b = i.basinPressure;
  if (b == null || b.isEmpty) return null;
  final change = b['change_72h_hpa'] as num?;
  final String tendency;
  if (change == null) {
    tendency = 'no three-day tendency available';
  } else if (change.abs() < _basinSteadyHpa) {
    tendency = 'near-steady over three days (${_signed1(change.toDouble())} hPa)';
  } else {
    tendency = "${change > 0 ? 'rising' : 'falling'} by ${_fixed(change.abs().toDouble(), 1)} hPa over three days";
  }
  return "BASIN PRESSURE: across ${b['points']} points, today's mean sea-level pressure sits between "
      "${_str(b['today_min_hpa'])} and ${_str(b['today_max_hpa'])} hPa; $tendency.";
}

String? _recordBlock(BriefInputs i, String tier) {
  if (i.trackRecord.isEmpty) return null;
  final lines = <String>[];
  for (final lead in [0, 3, 7]) {
    final rows = [
      for (final r in i.trackRecord)
        if (_int(r['lead_time_days']) == lead && _int(r['all_time_checks']) != null && !_hiddenModels.contains(r['model'])) r,
    ];
    if (rows.isEmpty) continue;
    var checks = 0;
    for (final r in rows) {
      final c = _int(r['all_time_checks']) ?? 0;
      if (c > checks) checks = c;
    }
    final scored = [
      for (final r in rows)
        if ((_int(r['all_time_checks']) ?? 0) >= 10 && _double(r['rolling_30_rain_pct']) != null) r,
    ];
    if (scored.isNotEmpty) {
      var best = scored.first;
      for (final r in scored) {
        if ((_double(r['rolling_30_rain_pct']) ?? 0) > (_double(best['rolling_30_rain_pct']) ?? 0)) best = r;
      }
      lines.add(
        "  Day+$lead: best rain record ${_name(i, '${best['model']}')}, right "
        "${_num(best['rolling_30_rain_pct'], 0)}% of the last 30 checks; $checks checks all time",
      );
    } else {
      lines.add('  Day+$lead: too few checks to rank any model yet ($checks all time)');
    }
  }
  if (lines.isEmpty) return null;
  return "RECORD (this place's own verification; the only rankings you may use beyond REVIEW):\n${lines.join('\n')}";
}

String? _reviewBlock(BriefInputs i, String tier) {
  if (i.reviewFindings.isEmpty) return null;
  final limit = tier == tierMini ? _reviewFindingsMini : _reviewFindingsFull;
  final indexed = [for (var k = 0; k < i.reviewFindings.length; k++) (k, i.reviewFindings[k])];
  // Rankings first, then by the claim as it reads; the index keeps Python's
  // stable order for equal keys.
  indexed.sort((a, b) {
    final ka = a.$2['kind'] == 'ranking' ? 0 : 1;
    final kb = b.$2['kind'] == 'ranking' ? 0 : 1;
    if (ka != kb) return ka - kb;
    final c = comparePython('${a.$2['claim']}', '${b.$2['claim']}');
    return c != 0 ? c : a.$1 - b.$1;
  });
  final ranked = indexed.take(limit);
  return 'REVIEW (established findings only; the only biases and rankings you may state):\n'
      '${[for (final f in ranked) "  - ${_shortNames(i, '${f.$2['claim']}')} (${f.$2['checks']} checks)"].join('\n')}';
}

String? _sufficiencyBlock(BriefInputs i, String tier) {
  if (tier == tierMini || !_truthy(i.dataSufficiency)) return null;
  return 'DATA SUFFICIENCY: ${_shortNames(i, i.dataSufficiency!)}';
}

// --- parsing the prompt -------------------------------------------------

// --- the drivers: item 200 ---------------------------------------------------
//
// Mirrors `brief.render_drivers` and `brief.recent_calls`; the measured
// thresholds and why days 1-2 meet 4-5 are recorded there.

const List<int> _followsEarlyLeads = [1, 2];
const List<int> _followsLateLeads = [4, 5];
const _followsHighC = 2.0;
const _followsWetShare = 0.375;
const recentDays = 7;
const _highsClaim = 'daytime highs';
const _patternStatements = 2;
const _day0 = 'day0';
const _day0Claim = 'At Day+0, ';
const Map<String, String> _skyWhen = {'early': 'early', 'midday': 'at midday', 'evening': 'in the evening'};

/// Each of the [days] before [today] that has a served call: what code
/// called and what was observed, newest first.
List<Map<String, Object?>> recentCalls(
  Map<String, Object?> entries,
  Map<String, Object?> actuals,
  String today, {
  int days = recentDays,
}) {
  final out = <Map<String, Object?>>[];
  for (var ago = 1; ago <= days; ago++) {
    final day = formatDate(addDays(parseDate(today), -ago));
    final served = _map(_map(entries[day])?['served_call']);
    final call = _map(served?['today_properties']) ?? const {};
    if (call.isEmpty) continue;

    final seen = _map(actuals[day]) ?? const {};
    out.add({
      'date': day,
      'days_ago': ago,
      'called_rain': call['rain'],
      'called_high_c': call['temp_high_c'],
      'observed_rain': seen['rain'],
      'observed_precip_mm': seen['precip_mm'],
      'observed_high_c': seen['high_c'],
      'station_rain': seen['precipitation'],
      'station_thunder': seen['thunder'],
    });
  }
  return out;
}

/// The Discussion's drivers: code's reading of the guidance, each line a
/// stated fact for the writer to explain. [recent] is [recentCalls].
String renderDrivers(BriefInputs inputs, List<Map<String, Object?>> recent) {
  final lines = [
    ..._setupLines(inputs),
    ..._followsLines(inputs),
    ..._leanLines(inputs),
    ..._yesterdayLines(recent),
    ..._metServiceLines(inputs),
  ];
  if (inputs.synopticStatements.isNotEmpty) {
    lines.add('  pattern: ${inputs.synopticStatements.take(_patternStatements).join(' ')}');
  }
  if (lines.isEmpty) return '';
  return "DRIVERS (code's reading of the guidance; explain these, do not list them):\n${lines.join('\n')}\n";
}

_Row _todayCall(BriefInputs i) => _map(i.servedCall?['today_properties']) ?? const {};

List<String> _setupLines(BriefInputs i) {
  final lines = <String>[];
  final inst = i.instability ?? const {};
  final capes = [
    for (final v in (_map(inst['peak_cape_by_model']) ?? const {}).values)
      if (v != null) _double(v)!,
  ];
  if (capes.isNotEmpty) {
    final above = (inst['models_above_threshold'] as List? ?? const []).length;
    final convective = _truthy(inst['convective']);
    var line = convective
        ? '  today, instability: $above of ${capes.length} models above the thunderstorm threshold'
        : '  today, instability: no model above the thunderstorm threshold';
    final lo = capes.reduce((a, b) => a < b ? a : b);
    final hi = capes.reduce((a, b) => a > b ? a : b);
    line += ', peak CAPE ${_num(lo, 0)} to ${_num(hi, 0)} J/kg';
    if (_truthy(inst['peak_model'])) line += ' (highest ${shortModelName('${inst['peak_model']}')})';
    if (convective && _truthy(inst['peak_hour'])) line += ', peaking at ${inst['peak_hour']}';
    lines.add(line);
  }

  final rows = _day0ModelRows(i);
  final call = _todayCall(i);
  if (rows.isNotEmpty || call.isNotEmpty) {
    final bits = <String>[];
    if (rows.isNotEmpty) {
      final wet = rows.where((r) => r['rain'] == 'true').length;
      bits.add('$wet of ${rows.length} models wet');
    }
    if (_truthy(call['rain'])) {
      final onset = _truthy(call['onset_window']) ? call['onset_window'] : call['onset_hour'];
      var said = 'the call: rain';
      if (_truthy(onset)) {
        final text = '$onset';
        said += ', onset ${text.substring(0, 1).toLowerCase()}${text.substring(1)}';
      }
      if (call['precip_mm'] != null) said += ', ${_num(call['precip_mm'], 1)} mm';
      bits.add(said);
    } else if (call['rain'] == false) {
      bits.add('the call: dry');
    }
    lines.add('  today, rain: ${bits.join('; ')}');
  }

  if (i.windShift != null && i.windShift!.isNotEmpty) lines.add('  today, wind: ${i.windShift}');
  final sky = [
    for (final e in (i.skyAnchors ?? const {}).entries)
      if (_truthy(e.value)) (e.key, '${e.value}'.toLowerCase()),
  ];
  if (sky.isNotEmpty) {
    if ({for (final s in sky) s.$2}.length == 1) {
      lines.add('  today, sky: ${sky.first.$2} all day');
    } else {
      lines.add('  today, sky: ${sky.map((s) => '${s.$2} ${_skyWhen[s.$1] ?? s.$1}').join(', ')}');
    }
  }
  return lines;
}

List<String> _followsLines(BriefInputs i) {
  final byLead = <int?, _Row>{for (final d in i.extendedDays) _int(d['lead_time_days']): d};
  final early = [for (final lead in _followsEarlyLeads) byLead[lead]];
  final late = [for (final lead in _followsLateLeads) byLead[lead]];
  final all = [...early, ...late];
  if (all.any((d) => d == null)) return const [];

  Set<String> modelsOf(_Row? d) => {for (final m in d!['models'] as List? ?? const []) '$m'};
  final models = modelsOf(early.first);
  if (models.isEmpty || all.any((d) => !_sameSet(modelsOf(d), models))) return const [];
  if (all.any((d) => d!['high_c'] == null)) return const [];

  final n = models.length;
  String span(List<_Row?> ds) => '${ds.first!['day_name']}–${ds.last!['day_name']}';
  final lines = ['  what follows (${span(early)} against ${span(late)}, the same $n models):'];

  final highA = (_double(early[0]!['high_c'])! + _double(early[1]!['high_c'])!) / 2;
  final highB = (_double(late[0]!['high_c'])! + _double(late[1]!['high_c'])!) / 2;
  final change = highB - highA;
  if (change.abs() >= _followsHighC) {
    final word = change > 0 ? 'warmer' : 'cooler';
    lines.add('    highs ${_num(highA, 0)} °C, then ${_num(highB, 0)} °C: turning $word');
  } else {
    lines.add('    highs steady near ${_num(highA, 0)} °C');
  }

  final wetA = early.fold<int>(0, (sum, d) => sum + (_int(d!['wet_votes']) ?? 0));
  final wetB = late.fold<int>(0, (sum, d) => sum + (_int(d!['wet_votes']) ?? 0));
  String perDay(int votes) => _num(votes / 2);
  if ((wetB - wetA).abs() / (2 * n) >= _followsWetShare) {
    final word = wetB > wetA ? 'wetter' : 'drier';
    lines.add('    wet models ${perDay(wetA)} of $n a day, then ${perDay(wetB)} of $n: turning $word');
  } else {
    lines.add('    wet models steady near ${perDay(wetA)} of $n a day');
  }

  final leads = [for (final k in byLead.keys) if (k != null && k > _followsLateLeads.last) k]..sort();
  final beyond = [
    for (final k in leads)
      if ((byLead[k]!['models'] as List? ?? const []).length < n) byLead[k]!,
  ];
  if (beyond.isNotEmpty) {
    final reach = beyond.map((d) => "${d['day_name']} (${(d['models'] as List? ?? const []).length})").join(' and ');
    lines.add('    beyond ${late.last!['day_name']}: fewer models reach $reach');
  }
  return lines;
}

bool _sameSet(Set<String> a, Set<String> b) => a.length == b.length && a.containsAll(b);

List<String> _leanLines(BriefInputs i) {
  final call = _todayCall(i);
  final rows = _day0ModelRows(i);
  final lines = <String>[];
  String? highModel;
  for (final (key, label, unit, more) in const [
    ('high_c', 'high', '°C', 'warmer'),
    ('precip_mm', 'rain', 'mm', 'wetter'),
  ]) {
    final target = _double(call[key == 'high_c' ? 'temp_high_c' : key]);
    final values = [
      for (final r in rows)
        if (_double(r[key]) != null) ('${r['model']}', _double(r[key])!),
    ];
    if (target == null || values.isEmpty) continue;

    var nearest = values.first;
    for (final v in values) {
      if ((v.$2 - target).abs() < (nearest.$2 - target).abs()) nearest = v;
    }
    if (key == 'high_c') highModel = nearest.$1;
    final above = values.where((v) => v.$2 > target).length;
    lines.add(
      "  the lean, $label: the call's ${_num(target, 1)} $unit is nearest ${_name(i, nearest.$1)} "
      '(${_num(nearest.$2, 1)} $unit); $above of ${values.length} models $more',
    );
  }

  if (highModel != null) {
    final prefix = '$_day0Claim$highModel ';
    for (final f in i.reviewFindings) {
      final claim = '${f['claim']}';
      if (!claim.startsWith(prefix) || !claim.contains(_highsClaim)) continue;
      lines.add('  the lean, record: ${_stripDots(_shortNames(i, claim.substring(_day0Claim.length)))}');
      break;
    }
  }
  if (_truthy((i.instability ?? const {})['convective'])) {
    final storms = [
      for (final f in i.reviewFindings)
        if (f['kind'] == 'convective' && '${f['claim']}'.startsWith(_day0Claim))
          _stripDots(_shortNames(i, '${f['claim']}'.substring(_day0Claim.length))),
    ];
    if (storms.isNotEmpty) lines.add('  the record, thunder: ${storms.join('; ')}');
  }
  return lines;
}

/// Python's `rstrip(".")`.
String _stripDots(String text) => text.replaceFirst(RegExp(r'\.+$'), '');

List<String> _yesterdayLines(List<Map<String, Object?>> recent) {
  final lines = <String>[];
  Map<String, Object?>? yesterday;
  for (final r in recent) {
    if (r['days_ago'] == 1) {
      yesterday = r;
      break;
    }
  }
  if (yesterday != null) {
    String rain(Object? v) => _truthy(v) ? 'rain' : v == false ? 'dry' : 'unknown';
    if (yesterday['observed_rain'] == null && yesterday['observed_high_c'] == null) {
      lines.add('  yesterday: called ${rain(yesterday['called_rain'])}; not yet observed');
    } else {
      var line = '  yesterday: called ${rain(yesterday['called_rain'])}, observed ${rain(yesterday['observed_rain'])}';
      if (yesterday['observed_precip_mm'] != null) line += ' (${_num(yesterday['observed_precip_mm'], 1)} mm)';
      final reported = [
        for (final (word, key) in const [('rain', 'station_rain'), ('thunder', 'station_thunder')])
          if (_truthy(yesterday[key])) word,
      ];
      if (reported.isNotEmpty) line += ', the station reporting ${reported.join(' and ')}';
      if (yesterday['called_high_c'] != null && yesterday['observed_high_c'] != null) {
        line += '; called a high of ${_num(yesterday['called_high_c'], 1)} °C, '
            'observed ${_num(yesterday['observed_high_c'], 1)} °C';
      }
      lines.add(line);
    }
  }
  final scored = [
    for (final r in recent)
      if (r['called_rain'] is bool && r['observed_rain'] is bool) r,
  ];
  if (scored.isNotEmpty) {
    final right = scored.where((r) => r['called_rain'] == r['observed_rain']).length;
    lines.add('  the rain call: right on $right of the last ${scored.length} days observed');
  }
  return lines;
}

List<String> _metServiceLines(BriefInputs i) {
  final name = i.metServiceName;
  if (name == null || name.isEmpty) return const [];
  _Row? row;
  for (final r in i.predictions) {
    if (r['lead'] == _day0 && r['model'] == i.metServiceModelId) {
      row = r;
      break;
    }
  }
  final called = _todayCall(i)['rain'];
  if (row == null || (row['rain'] != 'true' && row['rain'] != 'false')) {
    return ['  met service: no forecast from $name this run'];
  }
  final theirs = row['rain'] == 'true';
  final word = theirs ? 'rain' : 'dry';
  if (called == null) return ['  met service: $name calls $word'];
  if (theirs == _truthy(called)) return ['  met service: $name agrees, $word'];
  return ["  met service: $name calls $word, against the call's ${_truthy(called) ? 'rain' : 'dry'}"];
}

/// Today's per-model rows, without the met service and the blends.
List<_Row> _day0ModelRows(BriefInputs i) => [
      for (final r in i.predictions)
        if (r['lead'] == _day0 && !_hiddenModels.contains(r['model']) && r['model'] != i.metServiceModelId) r,
    ];

final RegExp _header = RegExp(r"^([A-Z][A-Z0-9' /+-]*[A-Z0-9])( \([^\n]*\))?:[ \t]*$");

/// Each top-level block's text below its header line, keyed by the header's
/// name — `prompt_size.prompt_block_sizes` walked line by line, so `^` and
/// `$` mean exactly what Python's MULTILINE anchors mean.
Map<String, String> _blocks(String userPrompt) {
  final starts = <(int, String)>[];
  var offset = 0;
  for (final line in userPrompt.split('\n')) {
    final m = _header.firstMatch(line);
    if (m != null) starts.add((offset, m.group(1)!));
    offset += line.length + 1;
  }
  final out = <String, String>{};
  final first = starts.isEmpty ? userPrompt.length : starts.first.$1;
  out['PREAMBLE'] = _body(userPrompt.substring(0, first));
  for (var k = 0; k < starts.length; k++) {
    final end = k + 1 < starts.length ? starts[k + 1].$1 : userPrompt.length;
    out[starts[k].$2] = _body(userPrompt.substring(starts[k].$1, end));
  }
  return out;
}

String _body(String text) {
  final nl = text.indexOf('\n');
  return nl < 0 ? '' : text.substring(nl + 1);
}

String? _issued(String preamble) {
  for (final line in splitLinesLikePython(preamble)) {
    if (line.startsWith('ISSUED: ')) return stripLikePython(line.substring('ISSUED: '.length));
  }
  return null;
}

Map<String, Object?>? _jsonBody(String body) {
  final start = body.indexOf('{');
  final end = body.lastIndexOf('}');
  if (start < 0 || end < 0) return null;
  try {
    return _map(jsonDecode(body.substring(start, end + 1)));
  } on FormatException {
    return null;
  }
}

List<_Row> _tableRows(String? body) {
  final lines = [for (final l in splitLinesLikePython(_stripNewlines(body ?? ''))) if (stripLikePython(l).isNotEmpty) l];
  if (lines.length < 2 || !lines[0].contains('\t')) return [];
  final columns = lines[0].split('\t');
  final rows = <_Row>[];
  for (final line in lines.skip(1)) {
    final cells = line.split('\t');
    if (cells.length != columns.length) continue;
    rows.add({for (var k = 0; k < columns.length; k++) columns[k]: cells[k] == '-' ? null : cells[k]});
  }
  return rows;
}

String _stripNewlines(String text) {
  var start = 0;
  var end = text.length;
  while (start < end && text[start] == '\n') {
    start++;
  }
  while (end > start && text[end - 1] == '\n') {
    end--;
  }
  return text.substring(start, end);
}

String? _phrase(String? body, {bool keepUnavailable = false}) {
  final text = stripLikePython(body ?? '');
  if (text.isEmpty || (text.startsWith(_unavailable) && !keepUnavailable)) return null;
  return text;
}

List<String> _phraseLines(String? body) {
  final text = _phrase(body);
  if (text == null) return [];
  return [for (final l in splitLinesLikePython(text)) if (stripLikePython(l).isNotEmpty) stripLikePython(l)];
}

double? _leadingNumber(String? text) {
  if (text == null || text.isEmpty) return null;
  final head = text.split(' ').first;
  return double.tryParse(head);
}

// --- small helpers ------------------------------------------------------

int? _int(Object? value) {
  if (value == null) return null;
  if (value is num) return value.toInt();
  final d = double.tryParse('$value');
  return d?.toInt();
}

double? _double(Object? value) {
  if (value == null) return null;
  if (value is num) return value.toDouble();
  return double.tryParse('$value');
}

/// A number as the page shows it — mirrors `brief._num`: a whole number
/// without its point; at a precision when one is given; else as given.
String _num(Object? value, [int? decimals]) {
  final f = _double(value);
  if (f == null) return '$value';
  if (decimals == null) {
    return f == f.truncateToDouble() ? '${f.toInt()}' : _str(value);
  }
  return _fixed(f, decimals);
}

/// Python's `str(value)` for the values the brief meets: a double prints
/// with its point ("9.0"), an int without, a string as itself.
String _str(Object? value) {
  if (value is double) {
    return value == value.truncateToDouble() && value.abs() < 1e16 ? '${value.toInt()}.0' : '$value';
  }
  return '$value';
}

String _cell(Object? value) {
  if (value == null) return '-';
  if (value is bool) return value ? 'yes' : 'no';
  if (value == 'true' || value == 'false') return value == 'true' ? 'yes' : 'no';
  return _num(value);
}

String _fixed(double value, int places) => roundLikePython(value, places).toStringAsFixed(places);

/// Python's `f"{x:+.1f}"`: the sign always, a negative zero kept.
String _signed1(double value) {
  final text = _fixed(value.abs(), 1);
  final negative = value.isNegative;
  return negative ? '-$text' : '+$text';
}

String _kmhAndKt(double kmh) => formatKmh(kmh);

bool _truthy(Object? value) => value != null && '$value'.isNotEmpty && value != false;

String? _dayForDate(BriefInputs i, String date) {
  for (final row in i.calendar) {
    if (row['date'] == date) return _int(row['lead_time_days']) == 0 ? 'today' : '${row['day_name']}';
  }
  return null;
}

String? _dayName(BriefInputs i, int? lead) {
  for (final row in i.calendar) {
    if (_int(row['lead_time_days']) == lead) return '${row['day_name']}';
  }
  return null;
}

String _name(BriefInputs i, String model) {
  if (i.metServiceModelId != null && model == i.metServiceModelId && i.metServiceName != null) {
    return i.metServiceName!;
  }
  return shortModelName(model);
}

String _shortNames(BriefInputs i, String text) {
  final ids = modelShortNames.keys.toList();
  final indexed = [for (var k = 0; k < ids.length; k++) (k, ids[k])];
  indexed.sort((a, b) => b.$2.length != a.$2.length ? b.$2.length - a.$2.length : a.$1 - b.$1);
  var out = text;
  for (final e in indexed) {
    out = out.replaceAll(e.$2, modelShortNames[e.$2]!);
  }
  if (i.metServiceModelId != null && i.metServiceName != null) {
    out = out.replaceAll(i.metServiceModelId!, i.metServiceName!);
  }
  return out;
}

Map<String, Object?>? _map(Object? value) => value is Map ? value.cast<String, Object?>() : null;

List<_Row> _rows(Object? value) => [for (final v in value as List? ?? const []) (v as Map).cast<String, Object?>()];

List<String> _strings(Object? value) => [for (final v in value as List? ?? const []) '$v'];
