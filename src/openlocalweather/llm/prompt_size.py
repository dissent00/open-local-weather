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
