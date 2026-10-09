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
import 'py_text.dart';

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
  final lines = [for (final line in splitLinesLikePython(markdown)) stripLikePython(line)];
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
