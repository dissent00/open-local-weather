"""The write-up gate — an answer missing the headings it was asked for is not
a write-up (2026-10-09).

Measured on the 50 stored LLM narratives before this was written: every
Gemini narrative since the current heading set (2026-09-10, 15 of 15)
carries all seven headings; the four gateway texts of 2026-10-05 to 10-09
carry none or one, and each had replaced a floor that held the Extended
Outlook. The 2026-09-22 nemotron text lost its newlines and carries none.
"""

from pathlib import Path

import pytest

from openlocalweather.config import load_location_config
from openlocalweather.llm.prompt import build_narrative_prompt, narrative_headings
from openlocalweather.write_up import audit_write_up

CONFIG = "config/location.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
HEADINGS = [
    "## Today's Forecast",
    "## Extended Outlook",
    "## Severe Weather / Hazard Potential",
    "## Winam Gulf — Conditions for Boaters",
    "## Detailed Discussion",
    "### Synoptic Overview",
    "### Forecaster Confidence Notes",
]


def _text(*sections):
    return "\n".join(f"{heading}\n{body}" for heading, body in sections)


COMPLIANT = _text(*[(h, "Written.") for h in HEADINGS])


def test_the_live_prompt_asks_for_these_headings_in_this_order():
    """The audit's list and the prompt's text cannot drift apart. The prompt
    is not built from the list, because its text is pinned by hash in the
    archive and a rebuilt prompt would refuse every archived day."""
    location = load_location_config(CONFIG)
    lines = [line.strip() for line in build_narrative_prompt(location).splitlines()]

    assert narrative_headings(location) == HEADINGS
    positions = [lines.index(h) for h in HEADINGS]
    assert positions == sorted(positions)


def test_without_a_secondary_point_there_is_no_boaters_heading():
    location = load_location_config(CONFIG)
    off = location.model_copy(
        update={"secondary_point": location.secondary_point.model_copy(update={"enabled": False})}
    )

    assert narrative_headings(off) == [h for h in HEADINGS if "Boaters" not in h]
    assert "Conditions for Boaters" not in build_narrative_prompt(off)


def test_a_complete_answer_passes():
    assert audit_write_up(COMPLIANT, HEADINGS) == []


def test_a_parent_heading_may_hold_only_its_subsections():
    """Detailed Discussion is two subsections and nothing of its own on
    every Gemini day of the record."""
    assert audit_write_up(COMPLIANT.replace("## Detailed Discussion\nWritten.", "## Detailed Discussion"), HEADINGS) == []


def test_extra_headings_are_not_defects():
    """The record carried an Overview above Today's Forecast until item 159."""
    assert audit_write_up("## Overview\nGone since.\n" + COMPLIANT, HEADINGS) == []


@pytest.mark.parametrize("text, defects", [
    (COMPLIANT.replace("## Extended Outlook\nWritten.", ""), ["missing ## Extended Outlook"]),
    # Today's Forecast after Extended Outlook.
    (_text(("## Extended Outlook", "Written."), ("## Today's Forecast", "Written."), *[(h, "Written.") for h in HEADINGS[2:]]),
     ["out of order ## Extended Outlook"]),
    (COMPLIANT.replace("## Severe Weather / Hazard Potential\nWritten.", "## Severe Weather / Hazard Potential\n"),
     ["empty ## Severe Weather / Hazard Potential"]),
    # A heading has to be a line of its own: the 2026-09-22 text had lost its newlines.
    ("## Today's ForecastnShowers.n## Extended Outlooknthe week." , [f"missing {h}" for h in HEADINGS]),
    # One run with the headings as the only text.
    ("\n".join(HEADINGS), [f"empty {h}" for h in HEADINGS if h != "## Detailed Discussion"]),
])
def test_defects_are_named_in_the_prompts_order(text, defects):
    assert audit_write_up(text, HEADINGS) == defects


def test_a_heading_with_trailing_whitespace_still_counts():
    assert audit_write_up(COMPLIANT.replace("## Today's Forecast\n", "## Today's Forecast  \r\n"), HEADINGS) == []


def test_the_record_before_the_gulf_was_renamed_passes_under_its_own_list():
    """The heading list is the prompt's at the time: the secondary point was
    Lake Victoria until 2026-09-10, and those days must not be rejected by
    today's name."""
    old = [h.replace("Winam Gulf", "Lake Victoria") for h in HEADINGS]
    text = _text(*[(h, "Written.") for h in old])

    assert audit_write_up(text, old) == []
    assert audit_write_up(text, HEADINGS) == ["missing ## Winam Gulf — Conditions for Boaters"]


def test_geminis_write_up_of_2026_10_07_passes():
    assert audit_write_up((FIXTURES / "write_up_2026-10-07.md").read_text(), HEADINGS) == []


def test_the_gateways_text_of_2026_10_09_is_refused_on_every_heading():
    assert audit_write_up((FIXTURES / "write_up_2026-10-09.md").read_text(), HEADINGS) == [
        f"missing {h}" for h in HEADINGS
    ]
