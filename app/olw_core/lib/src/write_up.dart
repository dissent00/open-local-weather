/// The write-up gate — upstream `write_up.py` (2026-10-09), pinned by
/// `spec/vectors/write_up_audit.json`.
///
/// An answer missing the headings it was asked for is not a write-up. Four
/// mornings running, a free gateway model answered 600 to 800 characters
/// under none or one of the seven headings, and each answer replaced a floor
/// that carried the Extended Outlook: the reader lost the week to a
/// paragraph. Measured upstream on the 50 stored LLM narratives: every
/// Gemini narrative under the current heading set passes, the thin ones are
/// refused.
library;

import 'config.dart';

/// The headings STEP 2 asks for, in its order — `prompt.narrative_headings`.
///
/// The template in llm/prompt.dart is not built from this list, for the
/// reason the Python side gives: its text is pinned by hash in the prompt
/// archive. The two are pinned together by test instead.
List<String> narrativeHeadings(SecondaryPoint secondary) => [
      "## Today's Forecast",
      '## Extended Outlook',
      '## Severe Weather / Hazard Potential',
      if (secondary.enabled) '## ${secondary.name} — ${secondary.sectionLabel}',
      '## Detailed Discussion',
      '### Synoptic Overview',
      '### Forecaster Confidence Notes',
    ];

/// Why an answer is not the write-up it was asked for, or empty when it is.
///
/// Three defects, named in the prompt's order, one per heading: missing, out
/// of order, or empty. A heading is a line of its own, stripped. A parent
/// heading holding only its subsections is not empty — Detailed Discussion
/// is two subsections and nothing of its own on every Gemini day of the
/// record. Headings the prompt did not ask for are ignored.
List<String> auditWriteUp(String markdown, List<String> headings) {
  final lines = [for (final line in _splitLinesLikePython(markdown)) _stripLikePython(line)];
  final headingLines = [
    for (var i = 0; i < lines.length; i++)
      if (lines[i].startsWith('#')) i,
  ];
  final defects = <String>[];
  var lastAt = -1;

  for (final heading in headings) {
    final at = lines.indexOf(heading);
    if (at < 0) {
      defects.add('missing $heading');
      continue;
    }
    if (at < lastAt) {
      defects.add('out of order $heading');
      continue;
    }
    lastAt = at;

    final following = [for (final i in headingLines) if (i > at) i];
    final end = following.isEmpty ? lines.length : following.first;
    if (lines.sublist(at + 1, end).any((line) => line.isNotEmpty)) {
      continue;
    }
    if (following.isNotEmpty && lines[end].startsWith('${_level(heading)}#')) {
      continue;
    }
    defects.add('empty $heading');
  }

  return defects;
}

/// The run of '#' that opens a heading line.
String _level(String heading) {
  var n = 0;
  while (n < heading.length && heading[n] == '#') {
    n++;
  }
  return heading.substring(0, n);
}

/// Python's `str.splitlines()`: its separators, "\r\n" as one, and no empty
/// last line after a trailing separator. Dart's LineSplitter knows three of
/// these, and a heading beside a separator the model emitted has to read the
/// same way in both languages.
List<String> _splitLinesLikePython(String text) {
  const separators = {
    0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029,
  };
  final lines = <String>[];
  final buffer = StringBuffer();
  final units = text.runes.toList();
  for (var i = 0; i < units.length; i++) {
    final c = units[i];
    if (!separators.contains(c)) {
      buffer.writeCharCode(c);
      continue;
    }
    if (c == 0x0D && i + 1 < units.length && units[i + 1] == 0x0A) {
      i++;
    }
    lines.add(buffer.toString());
    buffer.clear();
  }
  if (buffer.isNotEmpty) {
    lines.add(buffer.toString());
  }
  return lines;
}

/// Python's `str.strip()` with no argument: the characters `str.isspace`
/// knows, which is not Dart's `trim` set — Dart strips the BOM and not
/// U+001C to U+001F; Python the reverse.
String _stripLikePython(String line) {
  const spaces = {
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0,
    0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
    0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
  };
  final units = line.runes.toList();
  var start = 0;
  var end = units.length;
  while (start < end && spaces.contains(units[start])) {
    start++;
  }
  while (end > start && spaces.contains(units[end - 1])) {
    end--;
  }
  return String.fromCharCodes(units.sublist(start, end));
}
