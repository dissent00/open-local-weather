"""The seam between judgment and rendering — ROADMAP item 59, step 2.

Step 1 unwelded: three paragraphs about `extended_properties` had leaked out
of the judgment section into the middle of STEP 2's narrative instructions,
and were moved back. This is the guard that stops it welding together again.

WHAT THE SEAM IS. A handful of the fields the forecaster returns are stored
as a prediction and scored against tomorrow's observations, beside GFS and
ECMWF. The rest are prose. A bad rule about prose produces a clumsy sentence;
a bad rule about a scored field produces a wrong forecast that the record then
carries for weeks. Today both kinds of rule are edits to the same string, so
the only thing separating them is WHERE they sit — which is a property nothing
checked until this file.

WHY THE FIELD LIST IS DERIVED AND NOT TYPED OUT. `_blend_prediction` and
`_extended_blend_predictions` are the two functions that turn the forecaster's
answer into scored rows; whatever they read off `today_properties` and
`extended_properties` is, by definition, what the record scores. Reading them
with `ast` means adding a newly-scored field automatically brings it under
this guard. A hand-written list would go stale the first time someone widened
item 72's schema, and it would go stale silently, which is the failure this
whole file exists to prevent.

WHY THERE IS NO DART MIRROR. `spec/vectors/llm_system_prompt.json` already
pins the rendered prompt character-for-character in both languages across all
seven branches, so any property proved of the Python string holds of the Dart
one. A mirror here would assert the same fact twice.

WHAT THIS FILE DOES NOT COVER. The AST half reads the PYTHON blend builders
only, so it says nothing about whether Dart's `blendPrediction` commits the
same fields. Deriving the set is what exposed that it does not — see ROADMAP
item 59 for the two divergences and what is owed on them.
"""

import ast
import json
import pathlib
import re

from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.llm.prompt import build_system_prompt

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / "src" / "openlocalweather" / "pipeline.py"
VECTORS = REPO_ROOT / "spec" / "vectors" / "llm_system_prompt.json"

# The prompt's own numbering. Splitting on these rather than on prose means a
# renumbering breaks this file loudly instead of quietly voiding every
# assertion below it — see `test_the_prompt_still_has_the_sections_this_file_reads`.
SECTION_MARKERS = (
    ("STEP1", "\n1. STEP 1:"),
    ("NARRATIVE", "\n2. STEP 2:"),
    ("FORMATTING", "\n3. FORMATTING RULES:"),
    ("JUDGMENT", "\n4. today_properties FIELDS"),
    ("WHATSAPP", "\n5. WHATSAPP SUMMARY"),
    ("LEFT_OUT", "\n6. BEFORE YOU RETURN"),
    ("MISSING_BLOCK", "\n7. A MISSING BLOCK"),
    ("GRAMMAR", "\n8. PROPER GRAMMAR"),
)

JUDGMENT = "JUDGMENT"

# Rendering sections may NAME a scored field, but only to defer to it: to say
# the prose must agree with the field, or that a prose rule does not reach it.
# What they may not do is decide its value. Each entry below is the exact text
# that carries the mention, so a new one fails rather than hiding inside a
# count, and a deleted one fails too rather than rotting here.
DEFERENCES: dict[str, tuple[tuple[str, str], ...]] = {
    "NARRATIVE": (
        (
            'so your own "rain" boolean is allowed to depart from it',
            "Deference. 'overview_comparison' is code-composed from the models' "
            "mean and must be published verbatim; this sentence exists to say "
            "that publishing it unedited does not bind the scored call. Delete "
            "it and the model's only way to reconcile the two is to edit a "
            "locked value, which is the bug the composed comparison was built "
            "to end.",
        ),
        (
            "today_properties.temp_high_c is the day's high; state it once",
            "Deference. A run published three different highs for one day in "
            "one section. This points the prose at the field rather than "
            "setting the field, and it is the field that settles ties.",
        ),
        (
            "today_properties stays your blended call for the WHOLE calendar "
            "day: temp_high_c is the day's high whether or not it has already "
            "happened",
            "A firewall, and the direction matters: it stops the surrounding "
            "narrative rule — cover the hours AHEAD, never the future tense "
            "for something past — from narrowing a field that is scored "
            "against the whole day and compared against every other day in "
            "the record. Removing this mention would re-open exactly the leak "
            "this file guards.",
        ),
    ),
    "FORMATTING": (
        (
            '"onset_hour" is scored and cannot - whether it takes an hour at '
            "all is decided under today_properties FIELDS below, not here",
            "Deference, and it was debt until 2026-09-10. This sentence used "
            "to DECIDE the field — 'must be null unless the models agree "
            "closely enough that you would defend one hour' — repeating "
            "section 4 in nearly the same words, one fact twice. What it says "
            "now is the part that belongs in a rendering section: the prose "
            "field may carry a range and the scored one may not. The decision "
            "itself moved out.",
        ),
    ),
}


def _scored_fields() -> frozenset[str]:
    """The fields the record actually scores, read off the two functions that
    build the scored rows."""
    tree = ast.parse(PIPELINE.read_text())
    wanted = {"_blend_prediction": "tp", "_extended_blend_predictions": "e"}
    found: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in wanted:
            continue
        param = wanted[node.name]
        found[node.name] = {
            n.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == param
        }

    missing = sorted(set(wanted) - set(found))
    assert not missing, f"pipeline.py no longer defines {missing}; this guard reads nothing"

    fields = frozenset().union(*found.values())
    # The empty-input trap. Every assertion in this file iterates over this
    # set, so a rename that silently emptied it would turn the whole file
    # green while checking nothing at all.
    assert "rain" in fields and "temp_high_c" in fields, f"derivation looks wrong: {sorted(fields)}"
    return fields


def _sections(prompt: str) -> dict[str, str]:
    marks = [(name, prompt.find(m)) for name, m in SECTION_MARKERS]
    for name, at in marks:
        assert at > 0, f"section {name} not found in the rendered prompt"
    assert marks == sorted(marks, key=lambda pair: pair[1]), f"sections out of order: {marks}"

    out = {}
    for k, (name, start) in enumerate(marks):
        end = marks[k + 1][1] if k + 1 < len(marks) else len(prompt)
        out[name] = prompt[start:end]
    return out


def _mentions(text: str, field: str) -> list[re.Match[str]]:
    """Occurrences of `field` as a FIELD NAME rather than as English.

    `rain` is the awkward one: it is a scored field, a substring of three
    other field names, and the most common noun in the document. Only its
    quoted and dotted forms are field references; the word in a sentence is
    not, and counting those would bury the four real mentions under hundreds.
    """
    if field == "rain":
        return list(re.finditer(r'"rain"|today_properties\.rain\b', text))
    return list(re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])", text))


def _branches() -> list[tuple[str, str]]:
    """(case name, rendered prompt) for every branch the vectors pin."""
    out = []
    for case in json.loads(VECTORS.read_text())["cases"]:
        i = case["input"]
        loc = i["location"]
        sec = loc["secondary_point"]
        out.append(
            (
                case["name"],
                build_system_prompt(
                    LocationConfig(
                        region_name=loc["region_name"],
                        primary_place_name=loc["primary_place_name"],
                        timezone="UTC",
                        primary_point=Point(lat=0.0, lon=0.0),
                        secondary_point=SecondaryPoint(
                            enabled=sec["enabled"],
                            name=sec["name"],
                            section_label=sec["section_label"],
                        ),
                    ),
                    historical_lookback_days=i["historical_lookback_days"],
                    rolling_window_short=i["rolling_window_short"],
                    rolling_window_long=i["rolling_window_long"],
                    is_reissue=i["is_reissue"],
                    ground_stations_configured=i["ground_stations_configured"],
                    local_bulletin_configured=i["local_bulletin_configured"],
                    extended_outlook_available=i["extended_outlook_available"],
                ),
            )
        )
    assert len(out) == 7, f"expected the seven pinned branches, got {len(out)}"
    return out


def test_the_prompt_still_has_the_sections_this_file_reads():
    """The empty-input trap, at section granularity. The leak test asks
    whether a scored field is MENTIONED in each rendering section, so a
    section that rendered as nothing would satisfy it perfectly while
    checking nothing. `_sections` already asserts every marker is present
    and in order; this adds that each one has a body."""
    for name, prompt in _branches():
        sections = _sections(prompt)
        assert len(sections) == len(SECTION_MARKERS), name
        for section, text in sections.items():
            assert len(text.strip()) > 40, f"{name}: {section} rendered all but empty"


def test_every_scored_field_is_governed_by_the_judgment_section():
    """A field the record scores must have a home. This is the half that
    catches a rule DELETED rather than misplaced: drop the paragraph defining
    `precip_mm` and nothing else in the suite notices, because the schema
    still accepts it and every vector still passes."""
    fields = _scored_fields()
    for name, prompt in _branches():
        judgment = _sections(prompt)[JUDGMENT]
        for field in sorted(fields):
            assert _mentions(judgment, field), f"{name}: {field} is scored but ungoverned"


def test_no_rule_about_a_scored_field_leaks_into_the_rendering_sections():
    fields = _scored_fields()
    for name, prompt in _branches():
        sections = _sections(prompt)
        for section, text in sections.items():
            if section == JUDGMENT:
                continue

            allowed = []
            for snippet, _why in DEFERENCES.get(section, ()):
                spans = [m.span() for m in re.finditer(re.escape(snippet), text)]
                assert len(spans) == 1, (
                    f"{name}: allowlisted text for {section} matches {len(spans)} times, "
                    f"expected once — the entry is stale: {snippet!r}"
                )
                allowed.append(spans[0])

            for field in sorted(fields):
                for hit in _mentions(text, field):
                    covered = any(lo <= hit.start() and hit.end() <= hi for lo, hi in allowed)
                    assert covered, (
                        f"{name}: {section} decides something about {field!r}, a field the "
                        f"record scores. Rules about scored values belong in section 4. "
                        f"If this really is a deference reference, add it to DEFERENCES "
                        f"with the reason. Context: ...{text[max(0, hit.start() - 90):hit.end() + 90]}..."
                    )
