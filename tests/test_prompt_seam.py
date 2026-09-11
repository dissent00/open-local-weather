"""The seam between judgment and rendering — ROADMAP item 59, steps 2 and 3.

Step 1 unwelded: three paragraphs about `extended_properties` had leaked out
of the judgment section into the middle of STEP 2's narrative instructions,
and were moved back. Step 2 was this guard. STEP 3 SPLIT THE PROMPT IN TWO,
and the seam it guards is no longer a boundary inside one string — it is the
boundary between two calls.

WHAT THE SPLIT ALREADY GUARANTEES, AND WHY THIS FILE STILL EXISTS. The
narrative call's schema has no `today_properties` and no `extended_properties`
in it, so the renderer cannot return a scored value whatever its prompt says.
That is a stronger guarantee than anything here. What it does NOT stop is the
renderer being TOLD to decide something — an instruction that makes the prose
argue with the call it was handed, which produces a forecast whose sentences
and whose scored record disagree. That is a prompt defect, it is invisible to
the schema, and it is what these assertions are for.

WHAT THE SEAM IS. A handful of the fields the forecaster returns are stored
as a prediction and scored against tomorrow's observations, beside GFS and
ECMWF. The rest are prose. A bad rule about prose produces a clumsy sentence;
a bad rule about a scored field produces a wrong forecast that the record then
carries for weeks.

WHY THE FIELD LIST IS DERIVED AND NOT TYPED OUT. `_blend_prediction` and
`_extended_blend_predictions` are the two functions that turn the forecaster's
answer into scored rows; whatever they read off `today_properties` and
`extended_properties` is, by definition, what the record scores. Reading them
with `ast` means adding a newly-scored field automatically brings it under
this guard. A hand-written list would go stale the first time someone widened
item 72's schema, and it would go stale silently, which is the failure this
whole file exists to prevent.

WHY THERE IS NO DART MIRROR. `spec/vectors/llm_system_prompt.json` already
pins the rendered prompts character-for-character in both languages across
all seven branches, so any property proved of the Python strings holds of the
Dart ones. A mirror here would assert the same fact twice.

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
from openlocalweather.llm.prompt import build_judgment_prompt, build_narrative_prompt
from openlocalweather.llm.schema import GeminiNarrativeResponse

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / "src" / "openlocalweather" / "pipeline.py"
VECTORS = REPO_ROOT / "spec" / "vectors" / "llm_system_prompt.json"

# Each prompt's own numbering. Splitting on these rather than on prose means a
# renumbering breaks this file loudly instead of quietly voiding every
# assertion below it — see `test_the_prompts_still_have_the_sections_this_file_reads`.
#
# The two lists are separate because the numbers restart in each prompt: the
# narrative call's "1." is STEP 1 and the judgment call's "1." is the scored
# field list, and a single table could not describe both.
JUDGMENT_MARKERS = (
    ("JUDGMENT", "\n1. today_properties FIELDS"),
    ("MISSING_BLOCK", "\n2. A MISSING BLOCK"),
)

NARRATIVE_MARKERS = (
    ("STEP1", "\n1. STEP 1:"),
    ("NARRATIVE", "\n2. STEP 2:"),
    ("FORMATTING", "\n3. FORMATTING RULES:"),
    ("WHATSAPP", "\n4. WHATSAPP SUMMARY"),
    ("LEFT_OUT", "\n5. BEFORE YOU RETURN"),
    ("MISSING_BLOCK", "\n6. A MISSING BLOCK"),
    ("GRAMMAR", "\n7. PROPER GRAMMAR"),
)

JUDGMENT = "JUDGMENT"

# THE NARRATIVE PROMPT may NAME a scored field, but only to defer to it: to
# say the prose must agree with the value it was handed, or that a prose rule
# does not reach it. What it may not do is decide one. Each entry below is the
# exact text that carries the mention, so a new mention fails rather than
# hiding inside a count, and a deleted one fails too rather than rotting here.
#
# THE LIST SHRANK AT THE SPLIT, from four entries to three. The fourth was
# 'your own "rain" boolean is allowed to depart from it', and it is gone
# because the sentence no longer names a field: the renderer is not the thing
# that makes the call, so the rule now speaks of THE CALL YOU WERE GIVEN.
# That is the split doing the work this allowlist used to do by hand.
DEFERENCES: tuple[tuple[str, str], ...] = (
    (
        "the given today_properties.temp_high_c is the day's high; state it once",
        "Deference. A run published three different highs for one day in one "
        "section. This points the prose at the field rather than setting the "
        "field, and since the split the field is one the renderer was handed.",
    ),
    (
        "THE CALL YOU WERE GIVEN describes the WHOLE calendar day: "
        "temp_high_c is the day's high whether or not it has already happened",
        "A firewall, and the direction matters: it stops the surrounding "
        "narrative rule — cover the hours AHEAD, never the future tense for "
        "something past — from narrowing a field that is scored against the "
        "whole day and compared against every other day in the record.",
    ),
    (
        '"onset_hour" is scored and cannot - whether it took an hour at all '
        "was decided in the judgment call and is given to you, not decided here",
        "Deference, and it was debt until 2026-09-10. This sentence used to "
        "DECIDE the field, repeating the judgment section in nearly the same "
        "words. What it says now is the part that belongs in a rendering "
        "section: the prose field may carry a range and the scored one may "
        "not. Since the split it also names where the decision was made.",
    ),
)


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


def _sections(prompt: str, markers: tuple[tuple[str, str], ...]) -> dict[str, str]:
    marks = [(name, prompt.find(m)) for name, m in markers]
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


def _branches() -> list[tuple[str, str, str]]:
    """(case name, judgment prompt, narrative prompt) for every pinned branch."""
    out = []
    for case in json.loads(VECTORS.read_text())["cases"]:
        i = case["input"]
        loc = i["location"]
        sec = loc["secondary_point"]
        location = LocationConfig(
            region_name=loc["region_name"],
            primary_place_name=loc["primary_place_name"],
            timezone="UTC",
            primary_point=Point(lat=0.0, lon=0.0),
            secondary_point=SecondaryPoint(
                enabled=sec["enabled"],
                name=sec["name"],
                section_label=sec["section_label"],
            ),
        )
        flags = dict(
            historical_lookback_days=i["historical_lookback_days"],
            rolling_window_short=i["rolling_window_short"],
            rolling_window_long=i["rolling_window_long"],
            is_reissue=i["is_reissue"],
            ground_stations_configured=i["ground_stations_configured"],
            local_bulletin_configured=i["local_bulletin_configured"],
            extended_outlook_available=i["extended_outlook_available"],
        )
        out.append(
            (
                case["name"],
                build_judgment_prompt(location, **flags),
                build_narrative_prompt(location, **flags),
            )
        )
    assert len(out) == 7, f"expected the seven pinned branches, got {len(out)}"
    return out


def test_the_prompts_still_have_the_sections_this_file_reads():
    """The empty-input trap, at section granularity. The leak test asks
    whether a scored field is MENTIONED in the narrative prompt, so a section
    that rendered as nothing would satisfy it perfectly while checking
    nothing. `_sections` already asserts every marker is present and in
    order; this adds that each one has a body."""
    for name, judgment, narrative in _branches():
        for prompt, markers in ((judgment, JUDGMENT_MARKERS), (narrative, NARRATIVE_MARKERS)):
            sections = _sections(prompt, markers)
            assert len(sections) == len(markers), name
            for section, text in sections.items():
                assert len(text.strip()) > 40, f"{name}: {section} rendered all but empty"


def test_every_scored_field_is_governed_by_the_judgment_prompt():
    """A field the record scores must have a home. This is the half that
    catches a rule DELETED rather than misplaced: drop the paragraph defining
    `precip_mm` and nothing else in the suite notices, because the schema
    still accepts it and every vector still passes."""
    fields = _scored_fields()
    for name, judgment, _narrative in _branches():
        section = _sections(judgment, JUDGMENT_MARKERS)[JUDGMENT]
        for field in sorted(fields):
            assert _mentions(section, field), f"{name}: {field} is scored but ungoverned"


def test_no_rule_about_a_scored_field_leaks_into_the_narrative_prompt():
    """Against the WHOLE narrative prompt, not section by section.

    Before the split this had to be per-section, because the judgment rules
    lived in the same string and had to be excluded. They are now in a
    different call, so the stronger claim is available: NOWHERE in the
    document the renderer reads may a scored field be decided.
    """
    fields = _scored_fields()
    for name, _judgment, narrative in _branches():
        allowed = []
        for snippet, _why in DEFERENCES:
            spans = [m.span() for m in re.finditer(re.escape(snippet), narrative)]
            assert len(spans) == 1, (
                f"{name}: allowlisted text matches {len(spans)} times, expected "
                f"once — the entry is stale: {snippet!r}"
            )
            allowed.append(spans[0])

        for field in sorted(fields):
            for hit in _mentions(narrative, field):
                covered = any(lo <= hit.start() and hit.end() <= hi for lo, hi in allowed)
                assert covered, (
                    f"{name}: the narrative prompt decides something about {field!r}, a "
                    f"field the record scores. Rules about scored values belong in the "
                    f"JUDGMENT prompt. If this really is a deference reference, add it "
                    f"to DEFERENCES with the reason. Context: "
                    f"...{narrative[max(0, hit.start() - 90):hit.end() + 90]}..."
                )


def test_the_renderer_has_nowhere_to_put_a_scored_field():
    """The structural half, and the one that cannot be edited away.

    Every assertion above is about what the narrative prompt SAYS, and a
    prompt is a string someone can change. This is about what the narrative
    call can RETURN: its schema contains no scored field, so a renderer that
    decided one anyway would have no way to report the decision. That is the
    guarantee ROADMAP item 59 was raised to get.
    """
    returnable = set(GeminiNarrativeResponse.model_fields)

    assert "today_properties" not in returnable
    assert "extended_properties" not in returnable
    # Derived, like everything else here: a newly-scored leaf field must not
    # appear at the top level of the narrative response either.
    assert not (_scored_fields() & returnable), sorted(_scored_fields() & returnable)
