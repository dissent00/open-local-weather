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
