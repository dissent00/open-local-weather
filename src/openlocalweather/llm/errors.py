"""Failures shared by every provider, and by the code that orchestrates them.

MOVED HERE 2026-09-11, from gemini.py. It had always lived there and both
other providers imported it across — `anthropic.py` raising a class defined
in `gemini.py` is a wart, and ROADMAP item 59 step 3 turned it into a real
layering problem: `forecast_call.py` sits ABOVE the providers and has to
catch this, and a provider-agnostic module importing one provider by name is
exactly the punch-through AGENTS.md forbids.

`gemini.py` re-exports it, so `from openlocalweather.llm.gemini import
LLMResponseError` keeps working — cli.py and several tests do that, and
breaking them would be churn for no gain.
"""

from __future__ import annotations


class LLMResponseError(RuntimeError):
    """The LLM call failed outright (network/HTTP error, no candidates) or
    its response didn't validate against the requested schema.

    A judgment call that raises this aborts the run, and always has —
    mirrors the original pipeline's "Critical Error: Pipeline aborted due to
    Gemini API failure" behavior. A RENDERING call that raises it is caught
    in forecast_call.py and degrades the issuance instead, because by then
    the scored forecast exists and is worth keeping; see
    DEGRADATION_NARRATIVE.
    """


class LLMUnavailableError(LLMResponseError):
    """The vendor could not be reached or would not finish — as distinct from
    answering with something we could not use.

    ROADMAP item 81, added 2026-09-21 for the fallback chain. A chain has to
    decide, at the moment one provider fails, whether trying the NEXT one is
    a repair or a waste, and the two failures look identical through
    `LLMResponseError`:

    - A 503 after four attempts across eight and a half minutes means Google
      is shedding load. Another vendor is very likely fine. Measured over
      2026-08-28 to 09-21: five such failures in 29 evening runs and none in
      25 morning ones (p = 0.038), all of them 500 or 503.
    - A response that fails schema validation means OUR prompt, OUR schema or
      this model's inability to follow them. The next model is handed the same
      prompt and the same schema, so it will very likely fail the same way —
      at double the cost, and with the real fault hidden behind a second
      error message.

    So only THIS one falls through. A SUBCLASS rather than a sibling because
    every existing `except LLMResponseError` must keep catching both: the
    judgment call still aborts the run and the rendering call still degrades
    the issuance, whichever kind of failure ended the chain.
    """
