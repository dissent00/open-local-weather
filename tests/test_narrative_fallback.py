"""Which chain links the narrative may use — ROADMAP item 180.

Two rules, both decided once the scored call has returned:

- `llm_fallback_calls: scored_call` (2026-09-25): the free fallback made the
  scored call 2 of 2 times and the narrative 1 of 5, each failure up to two
  1700s deadlines, so only the first link is asked for the narrative.
- 2026-09-26: a link that FAILED the scored call is not retried for the
  narrative. On 2026-09-25 15:01Z Gemini's four 503s on the scored call were
  followed three minutes later by four more on the narrative.

And when the narrative runs out of links, the record names the failure that
caused it, not the refusal that ended it.

These drive the REAL FallbackProvider through the pipeline's own
`_generate_forecast`, with links that call the two hooks where a real
provider does: `before_attempt` first, `after_attempt` once a request ends.
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
    """Fails the schemas in `fails`, and records every request it SENT."""

    def __init__(self, model: str, *, fails=(), attempts: int = 1):
        self.model = model
        self.fails = set(fails)
        self.attempts = attempts
        self.sent: list[str] = []
        self.before_attempt = None
        self.after_attempt = None
        self.after_response = None

    def generate(self, system_prompt, user_prompt, response_schema):
        name = response_schema.__name__
        if name not in self.fails:
            self._attempt(name, "http_200")
            return JUDGMENT if response_schema is GeminiJudgmentResponse else NARRATIVE

        for _ in range(self.attempts):
            self._attempt(name, "http_503")
        raise LLMUnavailableError(f"{self.model} is down")

    def _attempt(self, name, outcome):
        if self.before_attempt is not None:
            self.before_attempt()
        self.sent.append(name)
        if self.after_attempt is not None:
            self.after_attempt(outcome, 0.1)


J, N = "GeminiJudgmentResponse", "GeminiNarrativeResponse"


def _run(chain, fallback_calls):
    from openlocalweather.pipeline import _generate_forecast

    return _generate_forecast(chain, "judge", "narrate", "data", {}, fallback_calls=fallback_calls)


def test_a_primary_that_failed_the_scored_call_is_not_retried_for_the_narrative():
    gemini = _Link("gemini-3.6-flash", fails={J, N}, attempts=4)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free")
    chain = FallbackProvider([gemini, openrouter])

    call, _, served = _run(chain, "scored_call")

    assert gemini.sent == [J] * 4
    assert openrouter.sent == [J]
    assert served["judgment"][1] == "nex-agi/nex-n2.5-pro:free"
    # THE RECORD NAMES THE CAUSE, not the refusal that ended the chain.
    assert "(gemini-3.6-flash) failed the scored call" in call.narrative_error
    assert "http_503 x4" in call.narrative_error


def test_under_both_calls_the_narrative_goes_straight_to_the_fallback():
    gemini = _Link("gemini-3.6-flash", fails={J, N}, attempts=4)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free")
    chain = FallbackProvider([gemini, openrouter])

    call, _, _ = _run(chain, "both_calls")

    assert gemini.sent == [J] * 4
    assert openrouter.sent == [J, N]
    assert call.narrative_error is None


def test_a_primary_that_served_the_scored_call_is_asked_for_the_narrative():
    """The retries that rescued 2026-09-25's morning call stay: a link is
    skipped only if it FAILED the scored call, not if it struggled."""
    gemini = _Link("gemini-3.6-flash", fails={N}, attempts=4)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free")
    chain = FallbackProvider([gemini, openrouter])

    call, _, _ = _run(chain, "scored_call")

    assert gemini.sent == [J] + [N] * 4
    assert openrouter.sent == []
    assert "not asked for the narrative" in call.narrative_error
    assert "(gemini-3.6-flash) failed the narrative (http_503 x4)" in call.narrative_error


def test_a_healthy_primary_serves_both_and_the_fallback_is_never_asked():
    gemini = _Link("gemini-3.6-flash")
    openrouter = _Link("nex-agi/nex-n2.5-pro:free")
    chain = FallbackProvider([gemini, openrouter])

    call, _, _ = _run(chain, "scored_call")

    assert gemini.sent == [J, N]
    assert openrouter.sent == []
    assert call.narrative_error is None


def test_a_refused_link_never_reaches_the_cap_and_the_hooks_come_back():
    """A request never sent must not be a ledger row: the gate runs before
    the hook that records the attempt, and both hooks are restored after."""
    counted: list[str] = []
    completed: list[str] = []
    gemini = _Link("gemini-3.6-flash", fails={J, N}, attempts=4)
    openrouter = _Link("nex-agi/nex-n2.5-pro:free")
    chain = FallbackProvider([gemini, openrouter])

    def cap():
        counted.append(chain.active_provider.model)

    def done(outcome, elapsed_s):
        completed.append(outcome)

    chain.before_attempt = cap
    chain.after_attempt = done

    _run(chain, "scored_call")

    assert counted == ["gemini-3.6-flash"] * 4 + ["nex-agi/nex-n2.5-pro:free"]
    assert completed == ["http_503"] * 4 + ["http_200"]
    for link in (gemini, openrouter):
        assert link.before_attempt is cap and link.after_attempt is done


def test_a_single_provider_is_untouched():
    gemini = _Link("gemini-3.6-flash")

    call, _, _ = _run(gemini, "scored_call")

    assert gemini.sent == [J, N]
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
