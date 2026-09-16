"""ROADMAP item 148, step 1: the prompt is measured per block on every run."""

import json
from pathlib import Path

from openlocalweather.llm.prompt_size import (
    GUIDANCE_BLOCK,
    measure_prompt,
    prompt_block_sizes,
    sub_block_key,
)

VECTORS = Path(__file__).resolve().parents[1] / "spec" / "vectors"


def _case(name: str) -> str:
    cases = json.loads((VECTORS / "llm_user_prompt.json").read_text())["cases"]
    return next(c["expected"] for c in cases if c["name"] == name)


# THE HEADER LIST IS PINNED ON PURPOSE. A block whose header is written in a
# different style would be folded into its predecessor silently, and the
# series this instrument exists to keep would carry the fold forever. Adding
# a block means adding it here, deliberately.
FULLY_POPULATED_HEADERS = [
    "CALENDAR",
    "FORECAST WINDOWS",
    "HOURS AHEAD",
    "TODAY'S MULTI-MODEL GUIDANCE",
    "EXTRACTED PER-MODEL PREDICTIONS",
    "GUIDANCE RECENCY",
    "CONVECTIVE INSTABILITY",
    "DAY-OVER-DAY COMPARISON",
    "NEXT THREE DAYS",
    "WIND DIRECTION",
    "WIND SHIFT",
    "CALIBRATED PEAK GUST",
    "OBSERVED SO FAR TODAY",
    "GROUND AQI STATIONS",
    "GROUND AQI SUMMARY",
    "GROUND AQI LAST KNOWN",
    "LOCAL BULLETIN",
    "PRE-COMPUTED VERIFICATION RESULTS",
    "MODEL TRACK RECORD",
    "LONG-RUN REVIEW",
]


def test_every_block_of_the_fully_populated_prompt_is_found_and_nothing_is_lost():
    prompt = _case("fully populated")
    sizes = prompt_block_sizes(prompt)

    top_level = [k for k in sizes if "/" not in k]
    assert top_level == ["PREAMBLE", *FULLY_POPULATED_HEADERS]
    assert sum(v for k, v in sizes.items() if "/" not in k) == len(prompt)
    assert all(v > 0 for v in sizes.values())


def test_the_guidance_block_is_split_per_source_and_the_pieces_fit_inside_it():
    prompt = _case("fully populated")
    sizes = prompt_block_sizes(prompt)

    subs = {k: v for k, v in sizes.items() if k.startswith(GUIDANCE_BLOCK + "/")}
    assert subs, "the guidance block has sources and none were split out"
    # The pieces are the spans between top-level keys, so they cover the
    # block minus its header line and opening brace.
    assert 0 < sum(subs.values()) < sizes[GUIDANCE_BLOCK]
    assert sum(subs.values()) > 0.9 * sizes[GUIDANCE_BLOCK]


def test_a_block_that_is_absent_is_absent_rather_than_zero():
    sizes = prompt_block_sizes(_case("no ground stations configured — the blocks are absent"))
    assert "GROUND AQI STATIONS" not in sizes
    assert "LOCAL BULLETIN" in sizes


def test_a_lower_case_heading_is_not_a_block():
    # The rule is the template's own convention. Prose that happens to end
    # in a colon must not start a block, or the sizes would depend on what
    # the day's sentences said.
    prompt = "ISSUED: x\n\nCALENDAR (pre-computed):\nsome text\nNote the following:\nmore\n"
    sizes = prompt_block_sizes(prompt)
    assert list(sizes) == ["PREAMBLE", "CALENDAR"]


def test_measure_prompt_records_all_three_prompts():
    size = measure_prompt("j" * 100, "n" * 50, _case("fully populated"))
    assert size.judgment_prompt_chars == 100
    assert size.narrative_prompt_chars == 50
    assert size.user_prompt_chars == len(_case("fully populated"))
    assert size.blocks["MODEL TRACK RECORD"] > 0


def test_sub_block_key_is_stable():
    assert sub_block_key("primary_extended_daily") == "TODAY'S MULTI-MODEL GUIDANCE/primary_extended_daily"
