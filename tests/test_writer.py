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
    assert "today 125 words; extended 140 words" in prompt
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
    ("Winds to 39 km/h (21 kt), turning south by midday and southwest into the evening.", "wind shift not as the brief has it"),
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


def test_the_ask_is_the_request_production_sends():
    """Item 191 step (d): `writer_ask` is what `olw write-up` sends and what
    `tools/probe_writer.py` measures, so a route's pass rate in the probe
    is its pass rate on the production request."""
    from openlocalweather.brief import TIER_FULL
    from openlocalweather.config import load_location_config
    from openlocalweather.writer import brief_inputs, writer_ask

    location = load_location_config("config/location.yaml")
    prompt = (FIXTURES / "user_prompt_2026-10-07.txt").read_text()
    entry = json.loads((FIXTURES / "brief_entry_2026-10-07.json").read_text())

    inputs = brief_inputs(location, entry, prompt)
    ask = writer_ask(location, inputs)

    assert inputs.secondary_name == location.secondary_point.name
    assert ask.sections == sections_to_ask(location.write_up_sections, inputs)
    assert ask.brief == render_brief(inputs, tier=TIER_FULL, sections=ask.sections)
    assert ask.system_prompt == build_writer_prompt(
        ask.sections, place=location.primary_place_name, secondary_name=inputs.secondary_name,
        met_service_name=location.local_bulletin_source_name,
    )


def test_the_models_typography_is_normalised_before_the_audit():
    """gpt-oss on Groq (2026-10-12) writes U+2011 non-breaking hyphens in
    "west‑southwesterly" and U+202F narrow spaces between a figure and its
    unit, 86 and 288 times over eight write-ups; eight locked phrases were
    refused for the hyphen alone. Typography is not a change of meaning:
    the audit and the page read the plain forms."""
    from openlocalweather.writer import normalise_prose

    assert normalise_prose("west\u2011southwesterly at 9\u202fkm/h, 30\u00a0°C, pre\u2010computed") == (
        "west-southwesterly at 9 km/h, 30 °C, pre-computed"
    )
    inputs = _inputs()
    brief = render_brief(inputs, sections=list(SECTIONS))
    phrase = inputs.wind_shift
    assert phrase and "-" in phrase, phrase
    odd = phrase.replace("-", "\u2011")
    assert audit_section("today", f"Wind {odd}.", brief, inputs) == []


def test_the_gulf_timeline_is_locked_by_bearing_and_figures():
    """Groq's gpt-oss (2026-10-12 and 13) quoted every figure of the Gulf
    timeline and wrote the legs its own way — "Overnight winds are 6 km/h
    (3 kt) gusting 8 km/h (5 kt)", "By midday the flow shifts
    south-southwesterly at..." — and a leg locked whole refused all of
    them. What a leg means is its bearing and its figure pair; those are
    locked, and the wording around them is the writer's."""
    from openlocalweather.writer import _locked_phrases

    inputs = _inputs()
    brief = render_brief(inputs, sections=list(SECTIONS))
    locked = _locked_phrases("secondary", inputs)
    # The pairs moved to `_pair_defects` on 2026-11-02; the locks are the bearings.
    assert locked == ["northeasterly", "south-southwesterly", "southerly"], locked
    legs = inputs.secondary_wind["timeline"].split(", then ")
    own_way = (
        f"Overnight winds on the Gulf are {legs[0].split(' at ', 1)[1]}. By midday the flow shifts "
        f"{legs[1].replace(' by midday at ', ' at ')}. In the evening it strengthens to "
        f"{legs[2].split(' at ', 1)[1]}."
    )
    assert audit_section("secondary", own_way, brief, inputs) == []
    bearing = legs[1].split(" ")[0]
    assert "-" in bearing, bearing
    # "southwesterly" would pass here: this brief's wind phrase says
    # "southwest", and a bearing the brief names anywhere is allowed.
    strayed = own_way.replace(bearing, "westerly")
    assert audit_section("secondary", strayed, brief, inputs) == ["bearings not in the brief: westerly"]
    assert audit_section("today", "A southwesterly breeze later.", brief, inputs) == [], (
        "the brief says 'southwest'; its adverb form is the same bearing"
    )


def test_code_writes_the_observed_line_and_the_model_does_not():
    """Groq's gpt-oss lost Today on 10-26 and 10-27 for rewording the
    observed line, and on 10-22 for one word over a cap the line's 30
    words sat inside. Every passing model Today (7 of 7 to 10-29) ended
    with the line verbatim, so it is code's: the prompt stops asking for
    it, an exact copy is stripped, a part of it is still a refusal, and
    the page appends it after the model's text as the floor does."""
    i = _inputs()
    line = i.observed_so_far
    prompt = build_writer_prompt(["today"], place="Kisumu", secondary_name=None, met_service_name=None)
    assert "OBSERVED SO FAR line verbatim" not in prompt
    assert "today 125 words" in prompt

    own = " ".join(["Dry"] * WORD_CAPS["today"])
    assert audit_section("today", f"{own} {line}", _brief(i), i) == []
    assert any(d.startswith("phrase not verbatim") for d in audit_section(
        "today", "Dry. " + line.replace("no rain; no thunder", "no rain or thunder"), _brief(i), i))

    for answer in ("Showers later.", f"Showers later. {line}"):
        markdown, _ = compose_write_up(
            {"today": answer}, {"today": []}, {}, ["today"],
            secondary_name=None, model_name="Gemini 3.6 Flash", sign_off_line="Written by code.", observed_line=line,
        )
        assert f"## Today's Forecast\n\nShowers later. {line}\n" in markdown, markdown
        assert markdown.count("As of 06:01") == 1, markdown


def test_the_prompts_examples_carry_no_figure_to_copy():
    """10-23 and 10-24: Groq's Today wrote "39 km/h" where the briefs'
    gusts were 37 and 41. 39 was in neither brief, only in this prompt's
    own example. An example with a figure is a figure offered."""
    prompt = build_writer_prompt(list(SECTIONS), place="Kisumu", secondary_name="Winam Gulf", met_service_name="Kenya Met")
    assert "39 km/h" not in prompt and "21 kt" not in prompt


def test_an_en_dash_before_a_figure_after_a_space_is_a_minus():
    """Groq's gpt-oss (probe, 2026-10-29) wrote "Trend –1.6 hPa" for the
    brief's "trend -1.6 hPa" with U+2013, and the audit read an unsigned
    1.6 that no brief carried. The en dash is mostly a range — 285 of 287
    before a digit in the stored texts, every one after a digit, letter
    or degree sign — and the 2 minus signs both followed a space."""
    from openlocalweather.writer import normalise_prose

    assert normalise_prose("Trend –1.6 hPa (–0.1 hPa)") == "Trend -1.6 hPa (-0.1 hPa)"
    assert normalise_prose("–3°C at dawn") == "-3°C at dawn"
    assert normalise_prose("29°C–31, August 17th–19, 10–12, gradient – a spread") == (
        "29°C–31, August 17th–19, 10–12, gradient – a spread"
    )


def test_a_narrow_space_inside_a_figure_groups_thousands():
    """Groq's gpt-oss (probe, 2026-10-29) wrote CAPE as "1\u202f710\u202fJ/kg",
    and the narrow space, mapped to a space, split each figure in two: 1
    and 710, each checked alone. That refused correct figures and could
    pass a wrong one whose halves the brief happened to carry. Only
    there, of 113 grouped thousands in the stored texts; the other 109
    used a comma."""
    from openlocalweather.writer import normalise_prose

    assert normalise_prose("1\u202f710\u202fJ/kg, 12\u202f000 m, 3\u202f340") == "1710 J/kg, 12000 m, 3340"
    assert normalise_prose("1\u202f7100, 18\u202f00, at 5\u202fkm/h") == "1 7100, 18 00, at 5 km/h"
    i = _inputs()
    assert audit_section("severe", "UKMO builds 3\u202f440\u202fJ/kg by 17:00.", _brief(i), i) == []
    assert audit_section("severe", "ECMWF builds 1\u202f320\u202fJ/kg.", _brief(i), i) == [
        "figures not in the brief: 1320J/kg"
    ]


def test_severe_reads_the_cape_block_by_its_name():
    prompt = build_writer_prompt(["severe"], place="Kisumu", secondary_name=None, met_service_name=None)
    assert "thunder ahead from PEAK CAPE AHEAD" in prompt and "PEAK CAPE TODAY" not in prompt


@pytest.mark.parametrize("text, expected", [
    ("A high of 30 °C.", "A high of 30°C (86°F)."),
    ("A high of 30°C / 86°F.", "A high of 30°C (86°F)."),
    ("A high of 30°C (87°F).", "A high of 30°C (87°F)."),
    ("A high of 30°C (91°F).", "A high of 30°C (86°F)."),
    ("Highs range from 30 °C to 31 °C.", "Highs range from 30°C (86°F) to 31°C (88°F)."),
    ("Highs 29 to 31 °C.", "Highs 29 to 31°C (84 to 88°F)."),
    ("Highs 29–31°C.", "Highs 29–31°C (84–88°F)."),
    ("The call's 31.5 °C sits between.", "The call's 31.5°C (88.7°F) sits between."),
    ("Lows near -3 °C.", "Lows near -3°C (27°F)."),
    ("Gusts to 35 km/h.", "Gusts to 35 km/h (19 kt)."),
    ("Gusts to 35 km/h (19 kt).", "Gusts to 35 km/h (19 kt)."),
    ("Gusts to 29 km/h (15 kt).", "Gusts to 29 km/h (15 kt)."),
    ("Gusts to 35 km/h (25 kt).", "Gusts to 35 km/h (19 kt)."),
    ("Winds 6–8 km/h.", "Winds 6–8 km/h (3–4 kt)."),
    ("Highs 29-31°C (85-88°F).", "Highs 29-31°C (85-88°F)."),
    ("Lows -3 to -1 °C (27 to 30 °F).", "Lows -3 to -1°C (27 to 30°F)."),
    ("Rain of 0.2–2.1 mm.", "Rain of 0.2–2.1 mm (0.01–0.08 in)."),
    ("Bringing 14.5 mm (0.57 in) of rain.", "Bringing 14.5 mm (0.57 in) of rain."),
    ("About 10 mm.", "About 10 mm (0.39 in)."),
    ("All four models wet (0.8–6.1 mm).", "All four models wet (0.8–6.1 mm [0.03–0.24 in])."),
    ("Highs (29 °C) then (31 °C).", "Highs (29°C [84°F]) then (31°C [88°F])."),
    ("No figure at all, 1009 hPa, 1710 J/kg, 13:00.", "No figure at all, 1009 hPa, 1710 J/kg, 13:00."),
])
def test_code_completes_the_models_units(text, expected):
    """The model writes °C, km/h and mm; code adds °F, knots and inches
    from the figure as written (2026-11-02). A pair the model wrote stays
    when its second figure is within one display step of code's: the
    brief's own pairs convert from the TRUE value, so "29 km/h (15 kt)"
    (28.5 to 28.7 km/h) is right and recomputing it from 29 would print 16, the
    rounding-twice bug. Further off is a mis-conversion, and code's
    replaces it. Inside a bracket the second unit takes square ones."""
    from openlocalweather.writer import complete_units

    assert complete_units(text) == expected


def test_the_page_completes_the_models_units_and_not_codes():
    markdown, _ = compose_write_up(
        {"today": "A high of 30 °C."}, {"today": []}, {"extended": "Code's 29°C (84°F)."}, ["today", "extended"],
        secondary_name=None, model_name="Gemini 3.6 Flash", sign_off_line="Written by code.",
        observed_line="As of 06:01, reports through 05:00: high so far 21°C (70°F).",
    )
    assert "A high of 30°C (86°F). As of 06:01, reports through 05:00: high so far 21°C (70°F)." in markdown
    assert "Code's 29°C (84°F)." in markdown


@pytest.mark.parametrize("text, reason", [
    ("Overnight 7 km/h (4 kt) gusting to 12 km/h (6 kt); by midday 9 km/h (5 kt) with gusts to 22 km/h (12 kt).", None),
    ("Overnight 7 km/h gusting 12 km/h, by midday 9 km/h gusting up to 22 km/h.", None),
    ("Overnight 12 km/h (6 kt) gusting 7 km/h (4 kt).", "gust pair not in the brief: 12 km/h gusting 7 km/h"),
    ("By midday 9 km/h gusting 16 km/h.", "gust pair not in the brief: 9 km/h gusting 16 km/h"),
    ("Overnight 12 km/h with gusts to 7 km/h.", "gust pair not in the brief: 12 km/h gusting 7 km/h"),
    ("Light, the peak gust 26 km/h on the Gulf.", None),
])
def test_a_gulf_pair_is_locked_by_its_figures_not_its_connector(text, reason):
    """Groq's gpt-oss lost the Gulf section 2 of 8 probe days (10-30, 11-01)
    and twice live for "gusting to 12 km/h" and "with gusts to 18 km/h",
    every figure right. A pair means a sustained figure and its gust:
    every "X km/h gusting Y km/h" the section writes, whatever the
    connector and with or without knots, must be one of the brief's."""
    i = _inputs()
    defects = audit_section("secondary", text, _brief(i), i)
    if reason is None:
        assert not [d for d in defects if "gust pair" in d or "verbatim" in d], defects
    else:
        assert reason in defects, defects


@pytest.mark.parametrize("text, reason", [
    ("Winds shift from a north-northeasterly overnight to a southwest by midday and south into the evening.", None),
    ("North-northeasterly overnight, then southwest by midday.", None),
    ("Winds north-northeasterly overnight, south by midday.", "wind shift not as the brief has it: south by midday"),
    ("Southwest overnight, turning south into the evening.", "wind shift not as the brief has it: Southwest overnight"),
])
def test_the_wind_shift_is_locked_by_bearing_and_time(text, reason):
    """11-02's probe wrote "shift from a north-northeasterly overnight to a
    south-southwest by midday and southwest into the evening": every
    bearing at its time, the connectors its own, refused for not being
    the phrase verbatim. What the phrase means is which bearing at which
    time; a bearing written at a time must be the brief's for that time."""
    i = _inputs()
    defects = audit_section("today", text, _brief(i), i)
    if reason is None:
        assert not [d for d in defects if "wind shift" in d or "verbatim" in d], defects
    else:
        assert reason in defects, defects


def test_a_steady_wind_stays_locked_verbatim():
    """"Northerly throughout" names no time, so it has no parts to lock."""
    from openlocalweather.writer import _locked_phrases

    assert "northerly throughout" in _locked_phrases("today", _inputs(wind_shift="northerly throughout"))
    assert _inputs().wind_shift not in _locked_phrases("today", _inputs())
