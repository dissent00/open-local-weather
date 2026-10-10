/// The writer: the prompt, the audit and the composition — upstream
/// `writer.py`, ROADMAP item 191 step (b). The Python side carries the
/// reasoning; this is the same text from the same values, pinned by
/// `writer_prompt.json`, `sections_to_ask.json`, `audit_section.json`,
/// `compose_write_up.json` and `model_display_name.json`.
library;

import 'brief.dart';
import 'floor.dart' show confidenceHeading, discussionHeading, extendedHeading, severeHeading, synopticHeading, todayHeading;
import 'outlook.dart' show modelShortNames;
import 'phrasing.dart';
import 'py_text.dart';
import 'rounding.dart';
import 'tiles.dart' show kmhPerKnot;

/// Word caps per section, near the record's 90th percentile — see writer.py.
const Map<String, int> wordCaps = {
  sectionToday: 155,
  sectionExtended: 140,
  sectionSevere: 90,
  sectionSecondary: 95,
  sectionSynoptic: 140,
  sectionConfidence: 200,
};

const Set<String> _readerSections = {sectionToday, sectionExtended, sectionSecondary};
const Set<String> _noModelNameSections = {sectionToday, sectionSecondary};
const List<String> _pipelineWords = [
  'calibrated', 'consensus', 'pre-computed', 'precomputed', 'blend', 'guidance', 'the brief',
];
const _mmPerInch = 25.4;
final List<String> _modelIds = [...modelShortNames.keys, 'kenya_met', 'olw_blend', 'olw_code_blend'];

final RegExp _unitNumber = RegExp(
  r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(°\s?C|°\s?F|km/h|kt\b|mm\b|\bin\b|%|hPa|J/kg)?",
  caseSensitive: false,
);
const Map<String, String> _unitKeys = {
  '°c': 'c', '°f': 'f', 'km/h': 'kmh', 'kt': 'kt', 'mm': 'mm', 'in': 'in', '%': 'pct', 'hpa': 'hpa', 'j/kg': 'jkg',
};

/// The enabled sections that apply today — mirrors `sections_to_ask`.
List<String> sectionsToAsk(List<String> enabled, BriefInputs inputs) => [
      for (final section in briefSections)
        if (enabled.contains(section) &&
            !(section == sectionSevere && (inputs.instability ?? const {})['convective'] != true) &&
            !(section == sectionSecondary && inputs.secondaryName == null))
          section,
    ];

String sectionHeading(String section, {required String? secondaryName}) => switch (section) {
      sectionToday => todayHeading,
      sectionExtended => extendedHeading,
      sectionSevere => severeHeading,
      sectionSecondary => '## ${secondaryName ?? ''} — Conditions for Boaters',
      sectionSynoptic => synopticHeading,
      _ => confidenceHeading,
    };

// --- the prompt ---------------------------------------------------------

/// The system prompt — mirrors `build_writer_prompt` word for word.
String buildWriterPrompt(
  List<String> sections, {
  required String place,
  required String? secondaryName,
  required String? metServiceName,
}) {
  final fields = [for (final s in sections) '"$s"'].join(', ');
  final caps = [for (final s in sections) '$s ${wordCaps[s]} words'].join('; ');
  final met = metServiceName ?? 'the national met service';
  final guides = <String, String>{
    sectionToday: 'TODAY\'S FORECAST ("today"): open on a weather condition or a temperature, never on a time or the '
        'sun. What is still ahead, as the ISSUED line and WINDOWS say; the thunder phrase verbatim where '
        'one is given; the sky in SKY\'s words for those hours; the WIND phrase verbatim; gusts as '
        '"39 km/h (21 kt)"; the UV peak; air quality from the ground stations where given, the model '
        'estimate otherwise; the OBSERVED SO FAR line verbatim at the end, the one place the past '
        'tense is allowed. Nothing about humidity, feels-like or visibility: nothing measures them.',
    sectionExtended: 'EXTENDED OUTLOOK ("extended"): the NEXT THREE DAYS phrase verbatim first; the sky in SKY BY '
        'DAY\'s words; the days from DAYS AHEAD, rain as the models\' count ("three of four models wet") '
        'and thunder in the day\'s own word, possible staying possible; the Day+3 and Day+7 calls from '
        'THE CALL; highs as the range when the models spread; the RECORD line where given.',
    sectionSevere: 'SEVERE WEATHER ("severe"): thunder today from PEAK CAPE TODAY, per model, with the hazard any '
        'thunderstorm brings: sudden gusts well above the forecast wind. A warning from the met '
        'service\'s bulletin where it carries one; otherwise none.',
    sectionSecondary: '${(secondaryName ?? 'THE SECONDARY POINT').toUpperCase()} ("secondary"): the wind timeline verbatim, the '
        'consensus gust, and the thunderstorm gust hazard where thunder is possible.',
    sectionSynoptic: 'SYNOPTIC OVERVIEW ("synoptic"): the LARGE SCALE statements as given, then the BASIN PRESSURE '
        'line, then the pressure trend from THE CALL. Lower pressure lies toward a direction; never a '
        'centre, a track, a speed of approach or a front.',
    sectionConfidence: 'FORECASTER CONFIDENCE NOTES ("confidence"): what REVIEW and RECORD say about today\'s models, '
        'and where THE CALL\'s figures sit against MODELS TODAY; name a model sitting on the wrong side '
        'of its own record; $met\'s own figures and whether they agree with the call, or that no '
        'bulletin came; DATA SUFFICIENCY in substance. Never how the call weighed the models, and '
        'never the first person.',
  };
  final paragraphs = [for (final s in sections) guides[s]!].join('\n\n');
  return '''You write the daily forecast's prose for $place. Everything you may say is in the brief that follows this message; the figures were decided in code and are already on the page. Write for a reader deciding what to do next.

RETURN JSON with one field per section asked for: $fields. Each field is a short Markdown passage with no heading, since the page adds the headings. Leave a field null rather than pad it. Caps: $caps.

RULES CHECKED IN CODE. A section that breaks one is dropped and the reader gets code's version of it.
1. Every number you write is in the brief, or is one of its figures converted (Celsius to Fahrenheit, km/h to knots, mm to inches) or rounded as the page shows it. Never a figure of your own: no averages, no "around 35", no chance the brief does not give.
2. A phrase marked verbatim is used whole or not at all.
3. Model names as the brief names them, never an id such as gfs_seamless; none at all in today or the secondary point's section.
4. No pipeline words in today, extended or the secondary point's section: calibrated, consensus, pre-computed, blend, guidance, the brief.
5. One value per quantity per section: the call's high is the day's high, said once.

$paragraphs

Plain prose in complete sentences: no lists, no emojis, no headings. Both units for temperatures and rain where the brief gives both; wind as km/h with knots in brackets.''';
}

// --- the audit ----------------------------------------------------------

/// The figures a section may write, by the unit it writes them with —
/// mirrors `allowed_numbers`.
Map<String, Set<double>> allowedNumbers(String brief) {
  final bare = <double>{};
  for (final (value, _) in _figures(brief)) {
    bare.addAll({value, roundLikePython(value, 0), roundLikePython(value, 1)});
  }
  final byUnit = {
    for (final k in ['bare', 'c', 'f', 'kmh', 'kt', 'mm', 'in', 'pct', 'hpa', 'jkg']) k: Set<double>.of(bare),
  };
  for (final value in bare) {
    final f = value * 9 / 5 + 32;
    byUnit['f']!.addAll({roundLikePython(f, 0), roundLikePython(f, 1)});
    final kt = value / kmhPerKnot;
    byUnit['kt']!.addAll({roundLikePython(kt, 0), roundLikePython(kt, 1)});
    final inches = value / _mmPerInch;
    byUnit['in']!.addAll({roundLikePython(inches, 1), roundLikePython(inches, 2)});
  }
  return byUnit;
}

List<(double, String?)> _figures(String text) => [
      for (final m in _unitNumber.allMatches(text))
        if (_value(m.group(1)!) != null) (_value(m.group(1)!)!, _unitKey(m.group(1)!, m.group(2))),
    ];

String? _unitKey(String token, String? unit) {
  if (unit == null) return null;
  final key = _unitKeys[unit.toLowerCase().replaceAll(' ', '')];
  if (key == 'in' && !token.contains('.')) return null;
  return key;
}

double? _value(String token) => double.tryParse(token.replaceAll(',', ''));

/// Why a section is not publishable, or empty when it is — mirrors
/// `audit_section`, defect by defect in the same order.
List<String> auditSection(String section, String text, String brief, BriefInputs inputs) {
  final defects = <String>[];
  final body = stripLikePython(text);
  if (body.isEmpty) return ['empty'];
  if (RegExp(r'^\s*#', multiLine: true).hasMatch(body)) defects.add('carries a heading');

  final words = _wordCount(body);
  if (words > wordCaps[section]!) defects.add('$words words, cap ${wordCaps[section]}');

  final allowed = allowedNumbers(brief);
  final strays = <String>{};
  for (final m in _unitNumber.allMatches(body)) {
    final token = m.group(1)!;
    final unit = m.group(2);
    final value = _value(token);
    final key = _unitKey(token, unit);
    final permitted = key != null ? allowed[key]! : allowed['bare']!;
    if (value != null && !permitted.contains(value)) {
      strays.add(key != null ? '$token$unit' : token);
    }
  }
  if (strays.isNotEmpty) {
    final sorted = strays.toList()..sort(comparePython);
    defects.add('figures not in the brief: ${sorted.take(6).join(', ')}');
  }

  for (final phrase in _lockedPhrases(section, inputs)) {
    if (_usesPartOf(body, phrase) && !body.toLowerCase().contains(phrase.toLowerCase())) {
      defects.add('phrase not verbatim: ${headLikePython(phrase, 40)}');
    }
  }

  final lowered = body.toLowerCase();
  if (_modelIds.any(lowered.contains)) defects.add('names a model id');
  if (_noModelNameSections.contains(section)) {
    final names = [
      for (final n in modelShortNames.values) if (RegExp('\\b${RegExp.escape(n)}\\b').hasMatch(body)) n,
    ];
    if (names.isNotEmpty) defects.add("names a model in a reader's section: ${names.join(', ')}");
  }
  if (_readerSections.contains(section)) {
    final found = [
      for (final w in _pipelineWords) if (RegExp('\\b${RegExp.escape(w)}\\b').hasMatch(lowered)) w,
    ];
    if (found.isNotEmpty) defects.add('pipeline words: ${found.join(', ')}');
  }

  for (final paragraph in body.split('\n\n')) {
    final reason = phraseDefect(stripLikePython(paragraph));
    if (reason != null) {
      defects.add('shape: $reason');
      break;
    }
  }
  return defects;
}

/// Python's `len(text.split())`.
int _wordCount(String text) => [for (final w in text.split(RegExp(r'\s+'))) if (w.isNotEmpty) w].length;

List<String> _lockedPhrases(String section, BriefInputs i) {
  final phrases = switch (section) {
    sectionToday => [(i.instability ?? const {})['timing'], i.windShift, i.observedSoFar],
    sectionExtended => [i.nextThreeDays],
    sectionSecondary => [(i.secondaryWind ?? const {})['timeline']],
    _ => const <Object?>[],
  };
  return [for (final p in phrases) if (p != null && '$p'.isNotEmpty) '$p'];
}

bool _usesPartOf(String text, String phrase, {int window = 4}) {
  final words = [for (final w in phrase.toLowerCase().split(RegExp(r'\s+'))) if (w.isNotEmpty) w];
  final lowered = text.toLowerCase();
  final stops = words.length - window + 1;
  for (var k = 0; k < (stops < 1 ? 1 : stops); k++) {
    if (lowered.contains(words.sublist(k, k + window > words.length ? words.length : k + window).join(' '))) {
      return true;
    }
  }
  return false;
}

// --- the composition ----------------------------------------------------

/// The page's Markdown and who wrote each section — mirrors
/// `compose_write_up`.
(String, Map<String, String>) composeWriteUp(
  Map<String, String?> answers,
  Map<String, List<String>> verdicts,
  Map<String, String> codeSections,
  List<String> enabled, {
  required String? secondaryName,
  required String? modelName,
  required String signOffLine,
}) {
  final parts = <String>[];
  final sources = <String, String>{};
  var discussionOpen = false;
  for (final section in briefSections) {
    if (!enabled.contains(section)) continue;
    final answer = stripLikePython(answers[section] ?? '');
    final String text;
    final String source;
    if (answer.isNotEmpty && (verdicts[section] ?? const []).isEmpty) {
      text = answer;
      source = 'llm';
    } else if ((codeSections[section] ?? '').isNotEmpty) {
      text = codeSections[section]!;
      source = 'code';
    } else {
      continue;
    }
    if ((section == sectionSynoptic || section == sectionConfidence) && !discussionOpen) {
      parts.add(discussionHeading);
      discussionOpen = true;
    }
    parts.add('${sectionHeading(section, secondaryName: secondaryName)}\n\n$text');
    sources[section] = source;
  }

  final written = [
    for (final e in sources.entries)
      if (e.value == 'llm') sectionHeading(e.key, secondaryName: secondaryName).replaceFirst(RegExp(r'^[# ]+'), ''),
  ];
  final credit = written.isNotEmpty && modelName != null
      ? '${_join(written)} by $modelName; the rest written by code.'
      : signOffLine;
  parts.add(credit);
  return ('${parts.join('\n\n')}\n', sources);
}

String _join(List<String> items) {
  if (items.length <= 1) return items.join();
  return '${items.sublist(0, items.length - 1).join(', ')} and ${items.last}';
}

/// The sign-off's name for a model id — mirrors `model_display_name`.
String modelDisplayName(String modelId) {
  final name = modelId.split('/').last.split(':').first;
  if (name.startsWith('gemini')) {
    return [
      for (final w in name.split('-'))
        if (w.isNotEmpty && !RegExp(r'^\d').hasMatch(w)) '${w[0].toUpperCase()}${w.substring(1).toLowerCase()}' else w,
    ].join(' ');
  }
  return name;
}
