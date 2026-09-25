"""The fallback serves the scored call only — ROADMAP item 180, 2026-09-25.

The free fallback made the scored call 2 of 2 times and the narrative 1 of 5,
and a failed narrative on it costs up to two 1700s deadlines. So the
operator's `llm_fallback_calls: scored_call` keeps the chain for the judgment
and gives the narrative the primary link alone.

These drive the REAL FallbackProvider through the pipeline's own
`_generate_forecast`, with links that call `before_attempt` where a real
provider does — first thing in each attempt, before the request.
"""

import pytest

from openlocalweather.llm.errors import LLMUnavailableError
from openlocalweather.llm.fallback import FallbackProvider
from openlocalweather.llm.schema import (
    GeminiJudgmentResponse,
    GeminiNarrativeResponse,
    TodayProperties,
)

JUDGMENT = GeminiJudgmentResponse(
    today_properties=TodayProperties(rain_expected="Dry", temp_high_c=30.0, temp_low_c=18.0, rain=False)
)
NARRATIVE = GeminiNarrativeResponse(yesterday_verification="", today_narrative="A dry day.")


class _Link:
    """Answers or fails per schema, and records every request it SENT."""

    def __init__(self, model: str, *, fails: bool):
        self.model = model
        self.fails = fails
        self.sent: list[str] = []
        self.before_attempt = None
        self.after_attempt = None
        self.after_response = None

    def generate(self, system_prompt, user_prompt, response_schema):
        if self.before_attempt is not None:
            self.before_attempt()
        self.sent.append(response_schema.__name__)
        if self.fails:
            raise LLMUnavailableError(f"{self.model} is down")
        return JUDGMENT if response_schema is GeminiJudgmentResponse else NARRATIVE


def _run(chain, fallback_calls):
    from openlocalweather.pipeline import _generate_forecast

    return _generate_forecast(chain, "judge", "narrate", "data", {}, fallback_calls=fallback_calls)


def test_the_fallback_makes_the_scored_call_and_never_sends_the_narrative():
    gemini = _Link("gemini-3.6-flash", fails=True)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free", fails=False)
    chain = FallbackProvider([gemini, openrouter])

    call, _, served = _run(chain, "scored_call")

    assert openrouter.sent == ["GeminiJudgmentResponse"]
    assert gemini.sent == ["GeminiJudgmentResponse", "GeminiNarrativeResponse"]
    assert call.narrative_error is not None
    assert served["judgment"][1] == "nex-agi/nex-n2.5-pro:free"


def test_both_calls_keeps_the_old_behaviour():
    gemini = _Link("gemini-3.6-flash", fails=True)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free", fails=False)
    chain = FallbackProvider([gemini, openrouter])

    call, _, _ = _run(chain, "both_calls")

    assert openrouter.sent == ["GeminiJudgmentResponse", "GeminiNarrativeResponse"]
    assert call.narrative_error is None


def test_a_healthy_primary_serves_both_and_the_fallback_is_never_asked():
    gemini = _Link("gemini-3.6-flash", fails=False)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free", fails=False)
    chain = FallbackProvider([gemini, openrouter])

    call, _, _ = _run(chain, "scored_call")

    assert gemini.sent == ["GeminiJudgmentResponse", "GeminiNarrativeResponse"]
    assert openrouter.sent == []
    assert call.narrative_error is None


def test_the_refusal_comes_before_the_cap_counts_it():
    """A request never sent must not be a ledger row: the gate runs before
    the hook that records the attempt, and the hook is back afterwards."""
    counted: list[str] = []
    gemini = _Link("gemini-3.6-flash", fails=True)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free", fails=False)
    chain = FallbackProvider([gemini, openrouter])

    def cap():
        counted.append(chain.active_provider.model)

    chain.before_attempt = cap

    _run(chain, "scored_call")

    assert counted == ["gemini-3.6-flash", "nex-agi/nex-n2.5-pro:free", "gemini-3.6-flash"]
    assert gemini.before_attempt is cap and openrouter.before_attempt is cap


def test_a_single_provider_is_untouched():
    gemini = _Link("gemini-3.6-flash", fails=False)

    call, _, _ = _run(gemini, "scored_call")

    assert gemini.sent == ["GeminiJudgmentResponse", "GeminiNarrativeResponse"]
    assert call.narrative_error is None


def test_config_defaults_to_both_calls_and_rejects_anything_else(tmp_path):
    from pathlib import Path

    from openlocalweather.config import load_location_config

    src = Path("config/location.example.yaml").read_text()
    path = tmp_path / "location.yaml"
    path.write_text(src)
    assert load_location_config(str(path)).llm_fallback_calls == "both_calls"

    path.write_text(src.replace("location:\n", "location:\n  llm_fallback_calls: narrative\n", 1))
    with pytest.raises(Exception, match="llm_fallback_calls"):
        load_location_config(str(path))
