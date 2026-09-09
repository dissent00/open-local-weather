"""ROADMAP item 84 — the register must not drift from the code.

Item 84 asked for a static table. A static table is exactly what let
`cloud_cover` be fetched in three separate places and discarded in all three:
the answer was knowable and nobody knew it. So the register is generated, and
this asserts the committed copy still matches what the code says.

Same shape as the vector guards and the README coverage-count guard — a
document nothing checks is a document that will eventually lie.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs-internal" / "OBSERVATION_REGISTER.md"
GENERATOR = ROOT / "spec" / "generate_observation_register.py"


def test_the_register_matches_the_code_it_describes():
    committed = REGISTER.read_text()
    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True, cwd=ROOT)
    regenerated = REGISTER.read_text()

    if committed != regenerated:
        REGISTER.write_text(committed)  # leave the tree as it was found
    assert committed == regenerated, (
        "OBSERVATION_REGISTER.md is stale — something fetched, stored, scored or "
        "labelled has changed. Run spec/generate_observation_register.py."
    )


def test_the_register_still_carries_the_rows_that_are_absent():
    """The empty rows are the point. A register listing only what exists
    answers no question anyone has asked — and every gap this project has
    found was found from a bad output, after the fact."""
    body = REGISTER.read_text()
    for absent in ("humidity", "fog", "wind chill", "dew point", "visibility"):
        assert f"| {absent} |" in body, f"{absent} dropped from the register"


def test_the_two_load_bearing_columns_are_three_valued():
    """Item 84 names `observed` and `in prompt` as the pair that decides
    whether a prompt edit is legal. Two values each was wrong: "in prompt:
    yes" was true for humidity because the prompt names it in a PROHIBITION,
    and "observed: no" was true for dew point, which is in every METAR and
    parsed by nothing."""
    body = REGISTER.read_text()
    assert "**banned**" in body, "a banned variable must not read as mentionable"
    assert "**unparsed**" in body, "a discarded source must not read as no source"
