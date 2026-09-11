"""The forecast as two calls — ROADMAP item 59 step 3.

ONE DEFINITION OF THE ORDER, because there are two callers. `pipeline.py`
runs it for a real issuance and `replay.py` runs it against archived inputs,
and if each carried its own copy of "judgment first, then narrative with the
judgment appended" the two would eventually disagree about what a forecast
is. A replay that renders from a different call order is not a replay.
"""

from __future__ import annotations

from collections.abc import Callable

from openlocalweather.llm.prompt import build_narrative_user_prompt
from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    GeminiJudgmentResponse,
    GeminiNarrativeResponse,
    merge_forecast_response,
)

JUDGMENT = "judgment"
NARRATIVE = "narrative"


def generate_forecast(
    provider,
    judgment_prompt: str,
    narrative_prompt: str,
    user_prompt: str,
    on_call: Callable[[str], None] | None = None,
) -> GeminiForecastResponse:
    """Two calls, in the only order they can happen in.

    The renderer is handed the judgment's answer, so the judgment has to have
    happened first. That is the whole shape of the split: one call decides,
    the other describes what was decided.

    `on_call` fires after each call with its name, for a caller that needs
    the provider's per-call report before the next call overwrites it —
    which is exactly what `pipeline.py` needs and `replay.py` does not.
    Called AFTER the call returns, so a raised call fires nothing.
    """
    judgment: GeminiJudgmentResponse = provider.generate(
        judgment_prompt, user_prompt, GeminiJudgmentResponse
    )
    if on_call is not None:
        on_call(JUDGMENT)

    narrative: GeminiNarrativeResponse = provider.generate(
        narrative_prompt,
        build_narrative_user_prompt(user_prompt, judgment),
        GeminiNarrativeResponse,
    )
    if on_call is not None:
        on_call(NARRATIVE)

    return merge_forecast_response(judgment, narrative)
