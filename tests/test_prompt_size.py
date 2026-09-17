"""ROADMAP item 148, step 1: the prompt is measured per block on every run."""

import json

import pytest
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


# ---------------------------------------------------------------------------
# Step 2: warn on growth, not just on size
# ---------------------------------------------------------------------------


def test_growth_is_measured_against_the_median_of_the_trailing_first_issuances():
    """The median, not the previous run: one odd day must not re-base the
    comparison, and the two additions the record holds (+5.5% on 09-11,
    +2.4% on 09-14) were both against a steady week."""
    from openlocalweather.llm.prompt_size import prompt_growth

    got = prompt_growth(153_000, [150_000, 151_000, 152_000, 149_000, 150_500])
    assert got is not None
    assert got.trailing_median_chars == 150_500
    assert got.growth_pct == pytest.approx((153_000 - 150_500) / 150_500 * 100, abs=0.005)


def test_the_median_is_a_size_that_was_actually_sent():
    """median_low on an even count, so the figure the notice quotes is a real
    prompt rather than the midpoint of two."""
    from openlocalweather.llm.prompt_size import prompt_growth

    got = prompt_growth(160_000, [150_000, 152_000, 154_000, 156_000])
    assert got.trailing_median_chars == 152_000


def test_too_few_prior_runs_is_no_growth_figure_rather_than_a_wild_one():
    from openlocalweather.llm.prompt_size import PROMPT_GROWTH_MIN_RUNS, prompt_growth

    assert prompt_growth(160_000, [150_000] * (PROMPT_GROWTH_MIN_RUNS - 1)) is None
    assert prompt_growth(160_000, []) is None
    assert prompt_growth(160_000, [150_000] * PROMPT_GROWTH_MIN_RUNS) is not None


def test_shrinkage_is_measured_but_negative():
    """A cut is a decision and never warns; the figure is still stored so the
    series shows it."""
    from openlocalweather.llm.prompt_size import prompt_growth

    got = prompt_growth(120_000, [150_000, 150_000, 150_000])
    assert got.growth_pct == pytest.approx(-20.0)


def test_the_blocks_that_grew_are_named_largest_first_and_only_top_level():
    """The total says the prompt grew; the block says what did. Sub-blocks of
    the guidance block are inside their parent's figure and would double
    count it; a block that shrank is not what anyone is looking for."""
    from openlocalweather.llm.prompt_size import blocks_that_grew

    current = {"A": 1_000, "B": 5_000, "C": 2_000, "D": 900, "TODAY'S MULTI-MODEL GUIDANCE/x": 400}
    previous = {"A": 800, "B": 2_000, "C": 2_500, "D": 100, "TODAY'S MULTI-MODEL GUIDANCE/x": 100}
    assert blocks_that_grew(current, previous, limit=2) == [("B", 3_000), ("D", 800)]
    assert blocks_that_grew(current, previous) == [("B", 3_000), ("D", 800), ("A", 200)]


def test_a_new_block_counts_as_grown_by_its_whole_size():
    from openlocalweather.llm.prompt_size import blocks_that_grew

    assert blocks_that_grew({"A": 100, "NEW": 700}, {"A": 100}) == [("NEW", 700)]


def test_a_first_issuance_stores_its_growth_and_says_so_when_it_grew(tmp_path, capsys):
    """The pipeline half: the archive supplies the trailing series, today's own
    archive is never in it, a re-issue stores nothing, and the notice names
    the block that grew."""
    from datetime import date, datetime, timezone

    from openlocalweather.pipeline import _prompt_size_with_growth
    from openlocalweather.store.prompt_archive import write_prompt_archive

    steady = "PREAMBLE\nCALENDAR:\n" + "x" * 1_000 + "\nHOURS AHEAD:\n" + "y" * 1_000
    for day in (10, 11, 12):
        write_prompt_archive(
            tmp_path, date(2026, 9, day),
            issued_at=datetime(2026, 9, day, 3, tzinfo=timezone.utc),
            judgment_prompt="j", narrative_prompt="n", user_prompt=steady, llm_model="t",
        )
    grown = steady + "\nA NEW BLOCK:\n" + "z" * 500

    size = _prompt_size_with_growth(
        "j", "n", grown, first_issuance=True, data_dir=tmp_path, today=date(2026, 9, 13)
    )
    assert size.trailing_median_chars == len(steady)
    assert size.growth_pct == pytest.approx((len(grown) - len(steady)) / len(steady) * 100, abs=0.01)
    err = capsys.readouterr().err
    assert "NOTICE: the prompt grew" in err
    assert "A NEW BLOCK +" in err

    again = _prompt_size_with_growth(
        "j", "n", grown, first_issuance=False, data_dir=tmp_path, today=date(2026, 9, 13)
    )
    assert again.growth_pct is None and again.trailing_median_chars is None
    assert capsys.readouterr().err == ""
