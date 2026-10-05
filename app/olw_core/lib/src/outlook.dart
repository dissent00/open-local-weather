// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The Extended Outlook, composed in code — upstream ROADMAP item 190 step 3.
///
/// Port of `outlook.py`; the reasoning lives there. Two spans in one
/// vocabulary: the trend clause through Day+3 with the sky, rain's support
/// among the models, a day that stands out for wind, the scored call and
/// the record; then the same trend measured from Day+3, which models still
/// reach, the wetter solutions, the Day+7 call and its record. Pinned by
/// `spec/vectors/extended_days.json` and `extended_outlook.json`.
library;

import 'comparison.dart' show describeExtendedTrend, windChangeBandsKmh;
import 'config.dart';
import 'dates.dart';
import 'extract.dart';
import 'instability.dart' show convectiveTier;
import 'phrasing.dart';
import 'rounding.dart';
import 'scoring.dart' show mean;
import 'tiles.dart' show skyByDay;
import 'verify.dart' show TrackRecordEntry;

const List<int> nearLeads = [1, 2, 3];
const List<int> farLeads = [4, 5, 6, 7];
const List<int> scoredLeads = [3, 7];

/// A record shorter than this ranks nobody — the review's ten-check floor.
const int thinRecordChecks = 10;

/// Rain support moving by fewer votes than this is noise among four models.
const int supportMoveVotes = 2;

/// Models whose far-span totals sit this far apart are different solutions.
const double precipDisagreementMm = 5.0;

const Map<String, String> modelShortNames = {
  'gfs_seamless': 'GFS',
  'ecmwf_ifs025': 'ECMWF',
  'icon_seamless': 'ICON',
  'ukmo_seamless': 'UKMO',
  'jma_seamless': 'JMA',
  bestMatchModelId: 'Best Match',
};

String shortModelName(String model) => modelShortNames[model] ?? model;

/// One day beyond today, as the models see it together. Mirrors `ExtendedDay`.
class ExtendedDay {
  const ExtendedDay({
    required this.leadTimeDays,
    required this.date,
    required this.dayName,
    this.models = const [],
    this.wetVotes = 0,
    this.precipMm,
    this.precipByModel = const {},
    this.highC,
    this.highMinC,
    this.highMaxC,
    this.lowC,
    this.windKmh,
    this.thunder,
    this.sky,
  });

  final int leadTimeDays;
  final String date;
  final String dayName;
  final List<String> models;
  final int wetVotes;
  final double? precipMm;
  final Map<String, double> precipByModel;
  final double? highC;
  final double? highMinC;
  final double? highMaxC;
  final double? lowC;
  final double? windKmh;
  final String? thunder;
  final String? sky;

  factory ExtendedDay.fromJson(Map<String, Object?> j) => ExtendedDay(
        leadTimeDays: (j['lead_time_days'] as num).toInt(),
        date: j['date'] as String,
        dayName: j['day_name'] as String,
        models: ((j['models'] as List?) ?? const []).cast<String>(),
        wetVotes: (j['wet_votes'] as num?)?.toInt() ?? 0,
        precipMm: (j['precip_mm'] as num?)?.toDouble(),
        precipByModel: {
          for (final e in ((j['precip_by_model'] as Map?) ?? const {}).entries)
            e.key as String: (e.value as num).toDouble(),
        },
        highC: (j['high_c'] as num?)?.toDouble(),
        highMinC: (j['high_min_c'] as num?)?.toDouble(),
        highMaxC: (j['high_max_c'] as num?)?.toDouble(),
        lowC: (j['low_c'] as num?)?.toDouble(),
        windKmh: (j['wind_kmh'] as num?)?.toDouble(),
        thunder: j['thunder'] as String?,
        sky: j['sky'] as String?,
      );

  Map<String, Object?> toJson() => {
        'lead_time_days': leadTimeDays,
        'date': date,
        'day_name': dayName,
        'models': models,
        'wet_votes': wetVotes,
        'precip_mm': precipMm,
        'precip_by_model': precipByModel,
        'high_c': highC,
        'high_min_c': highMinC,
        'high_max_c': highMaxC,
        'low_c': lowC,
        'wind_kmh': windKmh,
        'thunder': thunder,
        'sky': sky,
      };
}

/// What the record says about a scored lead. Mirrors `LeadRecord`.
class LeadRecord {
  const LeadRecord({required this.leadTimeDays, this.bestModel, this.rainPct, this.checks = 0});

  final int leadTimeDays;
  final String? bestModel;
  final double? rainPct;
  final int checks;

  factory LeadRecord.fromJson(Map<String, Object?> j) => LeadRecord(
        leadTimeDays: (j['lead_time_days'] as num).toInt(),
        bestModel: j['best_model'] as String?,
        rainPct: (j['rain_pct'] as num?)?.toDouble(),
        checks: (j['checks'] as num?)?.toInt() ?? 0,
      );
}

class OutlookInputs {
  const OutlookInputs({
    this.days = const [],
    this.todayHighC,
    this.todayWindKmh,
    this.served = const {},
    this.records = const [],
    this.metServiceName,
    this.metServiceDay3Rain,
  });

  final List<ExtendedDay> days;
  final double? todayHighC;
  final double? todayWindKmh;

  /// The served call per scored lead, keyed by the lead as a string.
  final Map<String, Map<String, Object?>> served;
  final List<LeadRecord> records;
  final String? metServiceName;
  final bool? metServiceDay3Rain;

  factory OutlookInputs.fromJson(Map<String, Object?> j) => OutlookInputs(
        days: [
          for (final d in (j['days'] as List?) ?? const []) ExtendedDay.fromJson((d as Map).cast<String, Object?>())
        ],
        todayHighC: (j['today_high_c'] as num?)?.toDouble(),
        todayWindKmh: (j['today_wind_kmh'] as num?)?.toDouble(),
        served: {
          for (final e in ((j['served'] as Map?) ?? const {}).entries)
            '${e.key}': (e.value as Map).cast<String, Object?>(),
        },
        records: [
          for (final r in (j['records'] as List?) ?? const []) LeadRecord.fromJson((r as Map).cast<String, Object?>())
        ],
        metServiceName: j['met_service_name'] as String?,
        metServiceDay3Rain: j['met_service_day3_rain'] as bool?,
      );
}

/// The day table for Day+1 to Day+7 from the daily arrays. Mirrors
/// `extended_days`: the same extraction the scored leads use.
List<ExtendedDay> extendedDays(Map<String, Object?>? daily, List<String> models, DateTime today) {
  final times = ((((daily ?? const {})['daily'] as Map?) ?? const {})['time'] as List?)?.cast<String>() ?? const [];
  final skies = {
    for (final row in skyByDay(daily ?? const {}, models, today: today))
      (row['lead_time_days'] as num).toInt(): row['sky'] as String?,
  };
  final out = <ExtendedDay>[];
  for (final row in forwardCalendar(today)) {
    final lead = (row['lead_time_days'] as num).toInt();
    final date = row['date'] as String;
    if (!(nearLeads.contains(lead) || farLeads.contains(lead)) || !times.contains(date)) continue;
    final predictions = [
      for (final p in extractDayNPredictionsFromDaily(daily!, times.indexOf(date), models))
        if (p.highC != null) p,
    ];
    if (predictions.isEmpty) continue;
    final highs = [for (final p in predictions) p.highC!];
    out.add(ExtendedDay(
      leadTimeDays: lead,
      date: date,
      dayName: row['day_name'] as String,
      models: [for (final p in predictions) p.model],
      wetVotes: predictions.where((p) => p.rain == true).length,
      precipMm: _round1(mean([for (final p in predictions) p.precipMm])),
      precipByModel: {
        for (final p in predictions)
          if (p.precipMm != null) p.model: roundLikePython(p.precipMm!, 1),
      },
      highC: _round1(mean(highs)),
      highMinC: highs.reduce((a, b) => a < b ? a : b),
      highMaxC: highs.reduce((a, b) => a > b ? a : b),
      lowC: _round1(mean([for (final p in predictions) p.lowC])),
      windKmh: _round1(mean([for (final p in predictions) p.windKmh])),
      thunder: convectiveTier([for (final p in predictions) if (p.model != bestMatchModelId) p.peakCapeJkg]),
      sky: skies[lead],
    ));
  }
  return out;
}

/// The best visible model at each scored lead by rolling 30-check rain
/// accuracy, over models with at least [thinRecordChecks] checks. Mirrors
/// `lead_records`.
List<LeadRecord> leadRecords(List<TrackRecordEntry> entries,
    {required List<String> visibleModels, List<int> leads = scoredLeads}) {
  final out = <LeadRecord>[];
  for (final lead in leads) {
    final candidates = [
      for (final e in entries)
        if (e.leadTimeDays == lead &&
            visibleModels.contains(e.model) &&
            e.rolling30RainPct != null &&
            e.allTimeChecks >= thinRecordChecks)
          e,
    ];
    if (candidates.isEmpty) {
      out.add(LeadRecord(leadTimeDays: lead));
      continue;
    }
    var best = candidates.first;
    for (final e in candidates) {
      if (e.rolling30RainPct! > best.rolling30RainPct! ||
          (e.rolling30RainPct == best.rolling30RainPct && e.allTimeChecks > best.allTimeChecks)) {
        best = e;
      }
    }
    out.add(LeadRecord(
        leadTimeDays: lead, bestModel: best.model, rainPct: roundLikePython(best.rolling30RainPct!, 1), checks: best.allTimeChecks));
  }
  return out;
}

/// The outlook as two paragraphs, or null with no day to speak about.
/// Mirrors `describe_extended_outlook`.
String? describeExtendedOutlook(OutlookInputs i) {
  final byLead = {for (final d in i.days) d.leadTimeDays: d};
  final near = [for (final n in nearLeads) if (byLead.containsKey(n)) byLead[n]!];
  final far = [for (final n in farLeads) if (byLead.containsKey(n)) byLead[n]!];
  if (near.isEmpty) return null;

  final paragraphs = [_paragraph(_nearParts(i, near, far)), _paragraph(_farParts(i, near, far))];
  final text = [for (final p in paragraphs) if (p.isNotEmpty) p].join('\n\n');
  return text.isEmpty ? null : text;
}

// --- The near term ---

List<String?> _nearParts(OutlookInputs i, List<ExtendedDay> near, List<ExtendedDay> far) {
  final trend = describeExtendedTrend(
    i.todayHighC,
    [for (final d in near) d.highC],
    [for (final d in near) d.precipMm],
    [for (final d in near) d.dayName],
    todayWindKmh: i.todayWindKmh,
    dayWindsKmh: [for (final d in near) d.windKmh],
    dayThunder: [for (final d in near) d.thunder],
    dayAfterPrecipMm: far.isEmpty ? null : far.first.precipMm,
  );
  final last = near.last;
  return [
    _sentence(trend),
    _sky(near),
    _support(near),
    _windier(near, trend),
    _call(i, last),
    _record(i, last.leadTimeDays),
  ];
}

String? _sky(List<ExtendedDay> days) {
  final words = [for (final d in days) if (d.sky != null && d.sky!.isNotEmpty) (d.dayName, d.sky!)];
  if (words.isEmpty) return null;
  final distinct = {for (final (_, w) in words) w};
  if (distinct.length == 1) {
    if (words.length == days.length && days.length > 1) {
      return 'Skies ${words.first.$2.toLowerCase()} throughout.';
    }
    return 'Skies ${words.first.$2.toLowerCase()} ${_nameRun([for (final (n, _) in words) n])}.';
  }
  final runs = <(String, List<String>)>[];
  for (final (name, word) in words) {
    if (runs.isNotEmpty && runs.last.$1 == word) {
      runs.last.$2.add(name);
    } else {
      runs.add((word, [name]));
    }
  }
  final clauses = [for (final (word, names) in runs) '${word.toLowerCase()} ${_nameRun(names)}'];
  return 'Skies ${clauses.first}, then ${_join(clauses.sublist(1))}.';
}

String? _support(List<ExtendedDay> days) {
  final votes = [for (final d in days) if (d.models.isNotEmpty) (d.dayName, d.wetVotes, d.models.length)];
  if (votes.length < 2) return null;
  final counts = [for (final v in votes) v.$2];
  var rising = true, falling = true;
  for (var k = 1; k < counts.length; k++) {
    if (counts[k] < counts[k - 1]) rising = false;
    if (counts[k] > counts[k - 1]) falling = false;
  }
  if ((counts.last - counts.first).abs() < supportMoveVotes || rising == falling) return null;
  final (firstName, firstVotes, firstN) = votes.first;
  final (lastName, lastVotes, lastN) = votes.last;
  final verb = rising ? 'gains' : 'loses';
  return 'Rain $verb support through $lastName, from ${_votes(firstVotes, firstN)} '
      '$firstName to ${_votes(lastVotes, lastN)} $lastName.';
}

String? _windier(List<ExtendedDay> days, String? trend) {
  if (trend != null && (trend.contains('windier') || trend.contains('calmer'))) return null;
  final winds = [for (final d in days) if (d.windKmh != null) (d.dayName, d.windKmh!)];
  if (winds.length < 2) return null;
  final band = windChangeBandsKmh.first.$1;
  for (final (name, wind) in winds) {
    final others = [for (final (n, w) in winds) if (n != name) w];
    if (wind - others.reduce((a, b) => a > b ? a : b) >= band) {
      return 'Windier on $name, gusts to ${_fixed(wind, 0)} km/h.';
    }
  }
  return null;
}

String? _call(OutlookInputs i, ExtendedDay day) {
  final served = i.served['${day.leadTimeDays}'];
  if (served == null || served['rain'] == null) return null;
  var words = (served['rain'] as bool) ? 'rain' : 'dry';
  final probability = (served['rain_probability_pct'] as num?)?.toInt();
  if (probability != null) words += ', $probability%';
  if (day.models.isNotEmpty) words += ', with ${_votes(day.wetVotes, day.models.length)} wet';
  final highs = _highs(day);
  var sentence = "${day.dayName}'s call: $words${highs == null ? '' : '; $highs'}.";
  if (day.leadTimeDays == 3 && i.metServiceDay3Rain != null && i.metServiceName != null) {
    sentence += " ${i.metServiceName} calls it ${i.metServiceDay3Rain! ? 'wet' : 'dry'}.";
  }
  return sentence;
}

String? _record(OutlookInputs i, int lead) {
  LeadRecord? record;
  for (final r in i.records) {
    if (r.leadTimeDays == lead) {
      record = r;
      break;
    }
  }
  if (record == null) return null;
  if (record.bestModel == null || record.rainPct == null) {
    return 'The record at this lead is too short to rank the models.';
  }
  return 'At this lead ${shortModelName(record.bestModel!)} has the best record, '
      'right ${_fixed(record.rainPct!, 0)}% of the time over ${record.checks} checks.';
}

// --- The far term ---

List<String?> _farParts(OutlookInputs i, List<ExtendedDay> near, List<ExtendedDay> far) {
  if (far.isEmpty) return const [];
  final base = near.last;
  final trend = describeExtendedTrend(
    base.highC,
    [for (final d in far) d.highC],
    [for (final d in far) d.precipMm],
    [for (final d in far) d.dayName],
    todayWindKmh: base.windKmh,
    dayWindsKmh: [for (final d in far) d.windKmh],
    dayThunder: [for (final d in far) d.thunder],
    dayAfterPrecipMm: null,
  );
  final last = far.last;
  return [
    trend == null ? null : _sentence('further out, $trend'),
    _reach(near, far),
    _solutions(far),
    _call(i, last),
    _record(i, last.leadTimeDays),
  ];
}

String? _reach(List<ExtendedDay> near, List<ExtendedDay> far) {
  final full = near.first.models;
  for (final d in far) {
    if (d.models.length < full.length) {
      final kept = [for (final m in d.models) shortModelName(m)];
      if (kept.isEmpty) return null;
      ExtendedDay? previous;
      for (final x in [...near, ...far]) {
        if (x.leadTimeDays == d.leadTimeDays - 1) previous = x;
      }
      final after = previous == null ? d.dayName : previous.dayName;
      return 'Only ${_join(kept)} ${kept.length == 1 ? 'reaches' : 'reach'} past $after.';
    }
  }
  return null;
}

String? _solutions(List<ExtendedDay> far) {
  final totals = <String, double>{};
  for (final d in far) {
    for (final e in d.precipByModel.entries) {
      totals[e.key] = (totals[e.key] ?? 0.0) + e.value;
    }
  }
  if (totals.length < 2) return null;
  final values = totals.values.toList();
  final lo = values.reduce((a, b) => a < b ? a : b), hi = values.reduce((a, b) => a > b ? a : b);
  if (hi - lo < precipDisagreementMm) return null;
  final midpoint = (hi + lo) / 2;
  final wetter = [for (final e in totals.entries) if (e.value >= midpoint) shortModelName(e.key)];
  final drier = [for (final e in totals.entries) if (e.value < midpoint) shortModelName(e.key)];
  final span = far.length > 1 ? 'from ${far.first.dayName} to ${far.last.dayName}' : far.first.dayName;
  return '${_join(wetter)} ${wetter.length == 1 ? 'is' : 'are'} the wetter '
      '${wetter.length == 1 ? 'solution' : 'solutions'} $span; '
      '${_join(drier)} ${drier.length == 1 ? 'keeps' : 'keep'} it drier.';
}

// --- Shared ---

String? _highs(ExtendedDay day) {
  if (day.highMinC == null || day.highMaxC == null) return null;
  if (day.highMaxC! - day.highMinC! > 2.0) {
    return 'highs ${_fixed(day.highMinC!, 0)} to ${_fixed(day.highMaxC!, 0)} °C';
  }
  return 'highs around ${_fixed(day.highC!, 0)} °C';
}

String _votes(int wet, int total) {
  if (total == 1) return wet > 0 ? 'the one model that reaches it' : 'no model';
  if (wet == total) return total == 2 ? 'both models' : 'all ${_number(total)} models';
  if (wet == 0) return 'no model';
  return '${_number(wet)} of ${_number(total)} models';
}

const Map<int, String> _numbers = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six'};

String _number(int n) => _numbers[n] ?? '$n';

String _nameRun(List<String> names) => names.length <= 2 ? _join(names) : '${names.first} to ${names.last}';

String _paragraph(List<String?> parts) =>
    [for (final p in parts) if (p != null && p.isNotEmpty && phraseDefect(p) == null) p].join(' ');

String? _sentence(String? text) {
  if (text == null || text.isEmpty) return null;
  final t = text.trim();
  final cased = t[0].toUpperCase() + t.substring(1);
  return cased.endsWith('.') ? cased : '$cased.';
}

double? _round1(double? value) => value == null ? null : roundLikePython(value, 1);

String _fixed(double value, int places) => roundLikePython(value, places).toStringAsFixed(places);

String _join(List<String> items) {
  if (items.length <= 1) return items.join();
  return '${items.sublist(0, items.length - 1).join(', ')} and ${items.last}';
}
