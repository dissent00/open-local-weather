#!/usr/bin/env python3
"""Writes app/olw_core/lib/src/glossary.dart from glossary.py.

ROADMAP item 56. The two-language prose mirror has been broken twice this
project by hand-copying — once by missing a change, once by trailing
whitespace — so this half is generated. Run it after any glossary edit, then
re-run spec/export_vectors.py; the vector test compares the two.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlocalweather.glossary import EPA_AQI, GLOSSARY, NOAA_MARINE  # noqa: E402

DART = pathlib.Path(__file__).resolve().parents[1] / "app/olw_core/lib/src/glossary.dart"
HEADER_TERMS = (
    'knots 171, hPa 156,\n/// "Day+N" 127, AQI 125, J/kg 103, convective 88, '
    "CAPE 69, gust 67,\n/// instability 62, PM2.5/PM10 54, UV 41, synoptic 37, MSLP 29."
)


def quote(text: str) -> str:
    # Dart single-quoted strings: `$` interpolates and a backslash escapes.
    assert "$" not in text, f"a dollar sign would interpolate in Dart: {text[:60]}"
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def main() -> None:
    aliases = {NOAA_MARINE: "_noaaMarine", EPA_AQI: "_epaAqi"}
    entries = []
    for e in GLOSSARY:
        source = aliases.get(e.source) or ("null" if e.source is None else quote(e.source))
        entries.append(
            "  GlossaryEntry(\n"
            f"    {quote(e.term)},\n"
            f"    {quote(e.definition)},\n"
            f"    {source},\n"
            "  ),"
        )
    body = "\n".join(entries)
    DART.write_text(f"""// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The forecast's vocabulary, defined once and read by every surface.
///
/// ROADMAP item 56. The narrative is written to be technical where it needs
/// to be, and nothing explained its terms. Measured across the 30 stored
/// narratives (129,951 characters) on 2026-09-09: {HEADER_TERMS}
///
/// STATIC DATA, NOT AN LLM CALL. A definition has one right answer that does
/// not depend on today's weather; generated per issuance it would cost a call
/// and would define CAPE differently on Tuesday than on Monday.
///
/// GENERATED FROM src/openlocalweather/glossary.py by
/// spec/generate_glossary_dart.py — do not hand-edit.
library;

class GlossaryEntry {{
  /// [source] names the publishing body where the numbers come from, and is
  /// null where the entry is this project's own plain-English wording. An
  /// invented citation is worse than none.
  const GlossaryEntry(this.term, this.definition, this.source);

  final String term;
  final String definition;
  final String? source;
}}

const _noaaMarine =
    {quote(NOAA_MARINE)};
const _epaAqi = {quote(EPA_AQI)};

const List<GlossaryEntry> glossary = [
{body}
];
""")
    print(f"wrote {DART} — {len(GLOSSARY)} entries")


if __name__ == "__main__":
    main()
