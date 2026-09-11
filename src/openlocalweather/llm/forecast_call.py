"""The forecast as two calls — ROADMAP item 59 step 3.

ONE DEFINITION OF THE ORDER, because there are two callers. `pipeline.py`
runs it for a real issuance and `replay.py` runs it against archived inputs,
and if each carried its own copy of "judgment first, then narrative with the
judgment appended" the two would eventually disagree about what a forecast
is. A replay that renders from a different call order is not a replay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from openlocalweather.llm.errors import LLMResponseError
from openlocalweather.llm.prompt import build_narrative_user_prompt
from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    GeminiJudgmentResponse,
    GeminiNarrativeResponse,
    merge_forecast_response,
)

JUDGMENT = "judgment"
NARRATIVE = "narrative"

# What the reader is shown where the forecast would have been.
#
# NOT EMPTY, and not an apology. An empty narrative renders as a missing
# section — the page keeps its stat tiles and simply looks short, which reads
# as a forecast that had nothing to say rather than one whose write-up
# failed. The numbers above it are real and were decided normally; this says
# exactly that, and nothing it cannot support.
NARRATIVE_UNAVAILABLE_MARKDOWN = (
    "## Overview\n\n"
    "The forecast below could not be written up this issuance: the "
    "model call that turns the day's figures into prose did not complete. "
    "The figures themselves were produced normally and are shown as usual — "
    "they are the same numbers this forecast is scored on.\n\n"
    "There is no discussion, no extended outlook and no hazard section for "
    "this issuance. Check a later one."
)

NARRATIVE_UNAVAILABLE_VERIFICATION = (
    "Not written this issuance — the write-up call did not complete."
)


@dataclass(frozen=True)
class ForecastCall:
    """The merged forecast, and whether half of it had to be invented.

    `narrative_error` carries the provider's own message when the rendering
    call failed, so the caller can record a degradation rather than infer one
    from placeholder prose. None on a clean run.
    """

    response: GeminiForecastResponse
    narrative_error: str | None = None


def _unavailable_narrative() -> GeminiNarrativeResponse:
    """The prose half, when there is none.

    Every field is filled with something honest rather than left at its
    default: `today_narrative` is what the page renders, and the two
    verification fields are STORED BACK onto predictions and read by later
    runs. A silent empty string there would reach a future forecaster as a
    verification that happened and said nothing.
    """
    return GeminiNarrativeResponse(
        yesterday_verification=NARRATIVE_UNAVAILABLE_VERIFICATION,
        verification_notes=[],
        skill_profile_summaries=[],
        today_narrative=NARRATIVE_UNAVAILABLE_MARKDOWN,
        whatsapp_summary=None,
    )


def generate_forecast(
    provider,
    judgment_prompt: str,
    narrative_prompt: str,
    user_prompt: str,
    on_call: Callable[[str], None] | None = None,
) -> ForecastCall:
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

    # THE SECOND CALL MAY FAIL WITHOUT COSTING THE FIRST. By here the scored
    # call exists and has been paid for, and it is the half the record
    # verifies against observations. Losing it because the write-up blipped
    # would put a hole in the accuracy record to avoid publishing a short
    # page — see DEGRADATION_NARRATIVE.
    #
    # Narrow on purpose: only the provider's own failure is caught, so a bug
    # in the merge or the prompt builder still raises.
    narrative_error: str | None = None
    try:
        narrative: GeminiNarrativeResponse = provider.generate(
            narrative_prompt,
            build_narrative_user_prompt(user_prompt, judgment),
            GeminiNarrativeResponse,
        )
        if on_call is not None:
            on_call(NARRATIVE)
    except LLMResponseError as e:
        narrative = _unavailable_narrative()
        narrative_error = str(e)

    return ForecastCall(
        response=merge_forecast_response(judgment, narrative),
        narrative_error=narrative_error,
    )
