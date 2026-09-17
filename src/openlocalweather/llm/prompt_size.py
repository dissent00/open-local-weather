"""How big the prompt is, block by block — ROADMAP item 148, step 1.

The prompt was measured at 165,000 characters on 2026-09-14 and nobody had
measured it before: it had been growing for weeks against a number nobody
held, and both tables that finally sized it (items 134 and 147) were rebuilt
by hand from an archived prompt. This module makes that table a function of
the prompt, so every run records its own and growth is a query.

CHARACTERS, NOT TOKENS. Tokens are the provider's count and are already
stored per entry (`LogEntryMeta.input_tokens`); characters are what this
project controls and what both prior tables used, so the series continues.

BLOCKS ARE FOUND BY THE TEMPLATE'S OWN CONVENTION: a header is an all-caps
phrase at column 0 ending in a colon, with an optional parenthetical on the
same line. That is a rule about the rendered text, so it works unchanged on
the prompt archive (every user prompt since 2026-09-04) and on a run in
flight, and the two cannot disagree. The cost is that a header written in a
different style is folded into its predecessor silently — which is why
tests/test_prompt_size.py pins the exact header list of the fully populated
vector case, and a new block is added there deliberately.

THE GUIDANCE BLOCK IS SPLIT PER SOURCE, operator's choice 2026-09-16: it is
35% of the prompt and where item 134 measured ~87% of the growth, and a
single number for it would hide which source grew. Its JSON is rendered with
a two-space indent, so each top-level key starts a line at exactly two
spaces; the pieces are the spans between those lines. They sum to the block
minus its header and opening brace, which the test states rather than
rounding away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median_low

from openlocalweather.defaults import PROMPT_GROWTH_MIN_RUNS
from openlocalweather.models import PromptSize

# The block whose sources are sized individually.
GUIDANCE_BLOCK = "TODAY'S MULTI-MODEL GUIDANCE"

# Text before the first header — the ISSUED line — is real prompt and is
# counted, under a name no template header will ever collide with.
PREAMBLE = "PREAMBLE"

_HEADER = re.compile(r"^([A-Z][A-Z0-9' /+-]*[A-Z0-9])( \([^\n]*\))?:[ \t]*$", re.MULTILINE)
_TOP_LEVEL_KEY = re.compile(r'^  "([A-Za-z0-9_]+)": ', re.MULTILINE)


def sub_block_key(source: str) -> str:
    return f"{GUIDANCE_BLOCK}/{source}"


def prompt_block_sizes(user_prompt: str) -> dict[str, int]:
    """Characters per block, in prompt order, top-level blocks summing to
    the whole prompt; the guidance block's sources follow it as
    `TODAY'S MULTI-MODEL GUIDANCE/<source>` entries."""
    headers = list(_HEADER.finditer(user_prompt))
    sizes: dict[str, int] = {}

    first = headers[0].start() if headers else len(user_prompt)
    sizes[PREAMBLE] = first

    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(user_prompt)
        name = match.group(1)
        sizes[name] = end - match.start()

        if name == GUIDANCE_BLOCK:
            sizes.update(_guidance_sources(user_prompt[match.start():end]))

    return sizes


def _guidance_sources(block: str) -> dict[str, int]:
    keys = list(_TOP_LEVEL_KEY.finditer(block))
    out: dict[str, int] = {}
    for i, match in enumerate(keys):
        end = keys[i + 1].start() if i + 1 < len(keys) else len(block)
        out[sub_block_key(match.group(1))] = end - match.start()
    return out


def measure_prompt(judgment_prompt: str, narrative_prompt: str, user_prompt: str) -> PromptSize:
    """What this run sent, sized. The system prompts are hashed in the
    archive and never stored, so their sizes exist only here."""
    return PromptSize(
        user_prompt_chars=len(user_prompt),
        judgment_prompt_chars=len(judgment_prompt),
        narrative_prompt_chars=len(narrative_prompt),
        blocks=prompt_block_sizes(user_prompt),
    )


# --- Step 2: growth, not just size -----------------------------------------
#
# A ceiling fires once and then gets raised. The failure this instrument was
# built for is a block added without anyone deciding to add it — the prompt
# was 165,000 characters on 2026-09-14 and nobody had measured it — and that
# shows as a step against the recent past, not as a level. So the question
# asked on each first issuance is "how far above the last week's median is
# this one", and the answer is stored on the run and read back by the weekly
# check. Nothing refuses a run on it; that is step 3 and waits on item 132.


@dataclass(frozen=True)
class PromptGrowth:
    # The trailing median is a size that was actually sent (median_low), so
    # the figure a notice quotes is a real prompt rather than a midpoint.
    trailing_median_chars: int
    # Percent above (positive) or below (negative) that median, two places.
    growth_pct: float


def prompt_growth(
    current_chars: int, trailing_chars: list[int], *, min_runs: int = PROMPT_GROWTH_MIN_RUNS
) -> PromptGrowth | None:
    """This prompt against the median of the trailing first issuances, or
    None when there are too few of them to call anything a median."""
    if len(trailing_chars) < min_runs:
        return None

    median = median_low(trailing_chars)
    return PromptGrowth(
        trailing_median_chars=median,
        growth_pct=round((current_chars - median) / median * 100, 2),
    )


def blocks_that_grew(
    current: dict[str, int], previous: dict[str, int], *, limit: int = 3
) -> list[tuple[str, int]]:
    """The top-level blocks that grew against the previous first issuance,
    largest growth first. The total says the prompt grew; this says what did.

    Top-level only: the guidance block's per-source entries sit inside their
    parent's figure and would count it twice. A block that shrank is not
    what anyone reading a growth notice is looking for, and a block that is
    new counts by its whole size.
    """
    grown = [
        (name, chars - previous.get(name, 0))
        for name, chars in current.items()
        if "/" not in name and chars > previous.get(name, 0)
    ]
    grown.sort(key=lambda item: -item[1])
    return grown[:limit]
