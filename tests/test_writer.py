"""The writer — ROADMAP item 191 step (b): the prompt, the audit and the
composition under the operator's rule (the model's section where it
answered and passed, code's otherwise)."""

import json
from pathlib import Path

import pytest

from openlocalweather.brief import SECTIONS, BriefInputs, render_brief
from openlocalweather.writer import (
    WORD_CAPS,
    allowed_numbers,
    audit_section,
    build_writer_prompt,
    compose_write_up,
    sections_to_ask,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _inputs(**overrides) -> BriefInputs:
    prompt = (FIXTURES / "user_prompt_2026-10-07.txt").read_text()
    entry = json.loads((FIXTURES / "brief_entry_2026-10-07.json").read_text())
    i = BriefInputs.from_user_prompt(prompt, entry, secondary_name="Winam Gulf", met_service_name="Kenya Met", met_service_model_id="kenya_met")
    for k, v in overrides.items():
        setattr(i, k, v)
    return i


def _brief(i=None) -> str:
    return render_brief(i or _inputs(), sections=SECTIONS)


# --- which sections, and the prompt ----------------------------------------


def test_severe_is_asked_only_while_thunder_is_live_and_the_gulf_only_where_named():
    i = _inputs()
    assert sections_to_ask(list(SECTIONS), i) == list(SECTIONS)
    assert "severe" not in sections_to_ask(list(SECTIONS), _inputs(instability={"convective": False}))
    assert "secondary" not in sections_to_ask(list(SECTIONS), _inputs(secondary_name=None))
    assert sections_to_ask(["today", "extended"], i) == ["today", "extended"]


def test_the_prompt_asks_for_exactly_the_sections_and_stays_short():
    prompt = build_writer_prompt(["today", "extended"], place="Kisumu", secondary_name="Winam Gulf", met_service_name="Kenya Met")

    assert '"today", "extended"' in prompt
    assert "SEVERE WEATHER" not in prompt and "SYNOPTIC OVERVIEW" not in prompt
    assert "today 155 words; extended 140 words" in prompt
    assert len(prompt) < 6_000, len(prompt)
    full = build_writer_prompt(list(SECTIONS), place="Kisumu", secondary_name="Winam Gulf", met_service_name="Kenya Met")
    assert "WINAM GULF" in full and "Kenya Met's own figures" in full and len(full) < 7_000


# --- the audit -------------------------------------------------------------


def test_the_briefs_figures_and_their_conversions_are_allowed_by_unit():
    allowed = allowed_numbers("high 31°C; gust 39 km/h; 2.2 mm; 2,600 km; CAPE 1720 J/kg")

    assert {31.0, 39.0, 2.2, 2600.0, 1720.0} <= allowed["bare"] <= allowed["c"]
    assert {88.0, 87.8} <= allowed["f"] and {21.0, 21.1} <= allowed["kt"] and {0.09, 0.1} <= allowed["in"]
    assert 35.0 not in allowed["c"] and 88.0 not in allowed["c"], "a conversion never lands in its source unit"
    assert 1720.0 in allowed["jkg"], "a table's bare figure may be written with its unit"


def test_the_extended_outlook_may_name_models_as_codes_own_does():
    i = _inputs()
    assert audit_section("extended", "Only GFS, ECMWF and ICON reach past Sunday.", _brief(i), i) == []


@pytest.mark.parametrize("text, reason", [
    ("Showers from 13:00, with a high of 31°C / 88°F and gusts to 39 km/h (21 kt).", None),
    ("A high near ABSENT°C is likely.", "figures not in the brief: ABSENT"),
    ("Rainfall near 2.2 mm (0.09 in) with a 82% chance.", None),
    ("Winds to 39 km/h (21 kt), turning southwest by midday and south into the evening.", "phrase not verbatim"),
    ("Thunder possible from midday, peaking this afternoon. Dry otherwise.", None),
    ("GFS runs warm today.", "names a model in a reader's section: GFS"),
    ("The gfs_seamless model is warm.", "names a model id"),
    ("The calibrated gust is 39 km/h (21 kt).", "pipeline words: calibrated"),
    ("## Today\nDry.", "carries a heading"),
    ("", "empty"),
    ("Showers from 13:00 in the afternoon, 2 in all.", None),
])
def test_the_audit_names_each_defect(text, reason):
    """ABSENT is a Celsius figure the brief does not hold. Chosen by looking,
    because a brief is dense: 35 is in 2026-10-07's as ICON's 34.6 km/h gust
    rounded, 37 as a PM10 reading, and the audit cannot tell one quantity's
    figure from another's — measured 2026-10-10, 19 of 26 mutated figures
    caught, the misses all values present as some other figure."""
    i = _inputs()
    brief = _brief(i)
    absent = next(f"{v / 10:.1f}" for v in range(201, 400) if v / 10 not in allowed_numbers(brief)["c"])
    text = text.replace("ABSENT", absent)
    reason = reason.replace("ABSENT", absent) if reason else None
    defects = audit_section("today", text, brief, i)

    if reason is None:
        assert defects == [], defects
    else:
        assert any(d.startswith(reason) for d in defects), defects


def test_the_word_cap_is_enforced():
    i = _inputs()
    long = " ".join(["dry"] * (WORD_CAPS["today"] + 1))
    assert any("words, cap" in d for d in audit_section("today", long, _brief(i), i))


def test_model_names_are_allowed_in_the_discussion():
    i = _inputs()
    assert audit_section("confidence", "GFS over-forecasts highs here across 56 checks; the call's 31°C sits between.", _brief(i), i) == []


def test_a_shape_defect_is_a_defect():
    i = _inputs()
    assert any(d.startswith("shape") for d in audit_section("today", "Showers, with , and thunder.", _brief(i), i))


# --- the composition -------------------------------------------------------


def test_the_models_section_replaces_codes_and_the_rest_stays_codes():
    markdown, sources = compose_write_up(
        {"today": "Model's today.", "extended": "Model's week with 99 of them.", "secondary": None},
        {"today": [], "extended": ["figures not in the brief: 99"]},
        {"today": "Code's today.", "extended": "Code's week.", "secondary": "Code's gulf."},
        ["today", "extended", "secondary"],
        secondary_name="Winam Gulf", model_name="Gemini 3.6 Flash", sign_off_line="Written by code.",
    )

    assert "## Today's Forecast\n\nModel's today." in markdown
    assert "## Extended Outlook\n\nCode's week." in markdown
    assert "## Winam Gulf — Conditions for Boaters\n\nCode's gulf." in markdown
    assert markdown.rstrip().endswith("Today's Forecast by Gemini 3.6 Flash; the rest written by code.")
    assert sources == {"today": "llm", "extended": "code", "secondary": "code"}


def test_the_discussion_opens_once_over_its_two_subsections():
    markdown, sources = compose_write_up(
        {"synoptic": "Lower pressure lies north.", "confidence": "The record is settled."},
        {"synoptic": [], "confidence": []},
        {}, ["synoptic", "confidence"],
        secondary_name=None, model_name="Gemini 3.6 Flash", sign_off_line="Written by code.",
    )

    assert markdown.count("## Detailed Discussion") == 1
    assert "### Synoptic Overview\n\nLower pressure lies north." in markdown
    assert markdown.rstrip().endswith("Synoptic Overview and Forecaster Confidence Notes by Gemini 3.6 Flash; the rest written by code.")
    assert sources == {"synoptic": "llm", "confidence": "llm"}


def test_nothing_from_the_model_keeps_the_floors_sign_off():
    markdown, sources = compose_write_up(
        {}, {}, {"today": "Code's today."}, ["today", "severe"],
        secondary_name=None, model_name="Gemini 3.6 Flash", sign_off_line="Written by code; a discussion follows when a model answers.",
    )

    assert markdown == "## Today's Forecast\n\nCode's today.\n\nWritten by code; a discussion follows when a model answers.\n"
    assert sources == {"today": "code"}
