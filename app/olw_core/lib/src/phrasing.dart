/// Is a composed phrase shaped like something a reader can be handed?
///
/// Mirrors `phrasing.py`, whose docstring carries the full account. In short:
/// upstream ROADMAP item 158 has code compose the Overview's sentences and the
/// prompt orders the model to use them VERBATIM, which is the right trade — a
/// composed sentence cannot be re-welded or widened — but it leaves nothing
/// between the composer's output and the reader. On 2026-09-20 and 09-21 a
/// join artefact reached two published Overviews as "much the same through
/// Thursday, with , and showers and thunderstorms likely each day".
///
/// `extended_trend.json` could not catch it: `export_vectors.py` computes each
/// expected by CALLING the Python function, so the broken string was pinned as
/// the answer and this file's mirror matched it exactly. A golden vector proves
/// the two languages agree; it cannot say the answer was right. This check can,
/// because a claim about SHAPE is true independently of what any composer
/// produced — and it is itself vector-pinned, in `phrase_defect.json`.
library;

/// The marks a phrase may not carry a space in front of. A space before one is
/// a list joined with an empty item still in it, which is the shape the
/// 2026-09-20 defect took (", and" built onto "").
const List<String> _spaceBefore = [' ,', ' .', ' ;', ' :', ' !', ' ?'];

/// A phrase that stops on a joining word stopped in the middle. Matched with a
/// leading space so "background" does not read as a trailing "and".
const List<String> _unfinishedEndings = [
  ',',
  ';',
  ':',
  ' and',
  ' or',
  ' with',
  ' but',
  ' from',
];

/// A phrase that STARTS on one began in the middle. The composers all produce
/// fragments that open on their own subject.
const List<String> _unfinishedOpenings = [',', ';', ':', 'and ', 'or ', 'but ', 'with '];

/// The reason [text] is not shaped like a finished phrase, or null.
///
/// NULL IN IS NULL OUT, and that is not an oversight. Every composer here
/// returns null for a legitimate absence — the models share no bearing, the
/// comparison found nothing worth a sentence — and the prompt renders that as
/// the block being absent. An absence is the designed answer; only a PRESENT
/// string can be malformed.
String? phraseDefect(String? text) {
  if (text == null) {
    return null;
  }

  if (text.trim().isEmpty) {
    return 'empty';
  }

  if (text.contains('  ')) {
    return 'doubled space';
  }

  for (final mark in _spaceBefore) {
    if (text.contains(mark)) {
      return "space before '${mark.trim()}'";
    }
  }

  // ",," is a list joined with an empty item still in it. The space-before
  // check above already catches ", ," — this one is for the unspaced form and
  // for the message it gives, which names the cause.
  if (text.contains(',,')) {
    return 'empty item in a list';
  }

  final stripped = text.trimRight();
  for (final ending in _unfinishedEndings) {
    if (stripped.endsWith(ending)) {
      return "ends on '${ending.trim()}'";
    }
  }

  final lowered = text.trimLeft().toLowerCase();
  for (final opening in _unfinishedOpenings) {
    if (lowered.startsWith(opening)) {
      return "starts on '${opening.trim()}'";
    }
  }

  return null;
}
