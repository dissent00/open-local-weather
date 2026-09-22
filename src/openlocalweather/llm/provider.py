"""LLMProvider protocol — the seam that keeps pipeline logic independent of
which LLM actually generates the narrative.

GeminiProvider (gemini.py) is the first implementation, matching the
original pipeline's free-tier Gemini usage. Groq/Cerebras/OpenRouter are an
explicit roadmap item — adding one means writing a new class that implements
this same Protocol (including its own response_schema -> provider-schema
adapter, see gemini.py's to_gemini_schema for the pattern), never touching
pipeline.py itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

# THE PROVIDER VOCABULARY, here rather than in cli.py so that `config.py` can
# validate against it — moved 2026-09-15 when provider selection became a
# config field. cli.py is the top of the stack and importing it from config
# would invert the dependency; `defaults.py` is the wrong home for the
# opposite reason, since its own docstring reserves it for constants that are
# NOT per-deployment, and this one is exactly that.
#
# ROADMAP item 81: A NAME HERE IS A ROW IN THE SUPPORTED MATRIX, NOT A VENDOR.
# `gemini` and `gemini-interactions` are the same vendor and the same model
# reached by two different APIs, and the API is what determines the shape of
# the call — the request body, where the answer lands, how structured output
# is requested, whether there is a job to poll. That is why the vendor alone
# was never enough to name a combination.
#
# "openai" selects OpenAICompatProvider, which covers OpenAI, OpenRouter,
# Groq, Together, vLLM and Ollama — see that module's docstring.
VALID_LLM_PROVIDERS = ("gemini", "gemini-interactions", "anthropic", "openai")

# WHICH CREDENTIALS EACH KIND READS — ROADMAP item 81, 2026-09-22.
#
# Two entries in a chain may share an environment prefix only when they are
# the same vendor holding the same account: `gemini` and `gemini-interactions`
# are one key reaching two APIs, and chaining them is the intended way to try
# the newer API and fall back to the older one.
#
# `anthropic` and `openai` are NOT that, and before this map they collided in
# silence. Both read LLM_API_KEY and LLM_MODEL, so a chain naming both built
# successfully and handed one of them the other's key and model id — failing
# at call time, after the attempt was spent and the ledger row written. No
# deployment had both, which is the only reason it was never seen.
CREDENTIAL_FAMILIES = {
    "gemini": "gemini",
    "gemini-interactions": "gemini",
    "anthropic": "anthropic",
    "openai": "openai",
}

# The prefix each kind reads when an entry does not name one. These are the
# variable names the project has always used, so a chain of bare strings
# reads exactly what it read before.
DEFAULT_ENV_PREFIXES = {
    "gemini": "GEMINI",
    "gemini-interactions": "GEMINI",
    "anthropic": "LLM",
    "openai": "LLM",
}

# The fallback for a deployment whose config does not name one. Gemini because
# it is the one with a free tier, and this project exists for people who will
# not be holding a paid API key.
DEFAULT_LLM_PROVIDER = "gemini"

T = TypeVar("T", bound=BaseModel)


def provider_identity(provider) -> tuple[str, str]:
    """Who to record for the request about to happen: (class name, model).

    ROADMAP item 81, 2026-09-21. The spend ledger names the provider and the
    model on every row, and it read them straight off the object the cap was
    attached to. That was exactly right while the object that took the call
    was the object that made it.

    A FALLBACK CHAIN BREAKS THAT. `attach_spend_cap` wraps the chain, so every
    row would read `FallbackProvider` and a model of "unknown" — and the
    ledger is the instrument item 132's whole question rests on, which is
    which vendor is actually serving this deployment and at what rate it
    fails. A chain that made the ledger stop naming vendors would answer the
    reliability question by destroying the evidence for it.

    So a wrapper declares which of its children is live by setting
    `active_provider`, and this resolves through it. Recursive, because a
    chain may hold a chain; `None` means nothing is delegating right now and
    the object itself is the answer.
    """
    live = resolve_active(provider)
    return type(live).__name__, getattr(live, "model", "unknown")


def resolve_active(provider):
    """The object actually taking the request, through any chain wrappers.

    Split out of `provider_identity` for ROADMAP item 170: a cap per
    credential needs the LIVE OBJECT, not just its name, because the limit
    is carried on the link that was built from the config entry. Recursive
    for the same reason identity is — a chain may hold a chain — and `None`
    means nothing is delegating, so the object itself is the answer.
    """
    active = getattr(provider, "active_provider", None)
    if active is not None:
        return resolve_active(active)
    return provider


class LLMProvider(Protocol):
    def generate(self, system_prompt: str, user_prompt: str, response_schema: type[T]) -> T:
        """Calls the LLM and returns a validated instance of response_schema.

        Implementations should raise on failure (network error, no usable
        response, schema validation failure) rather than returning None or a
        partial object — the caller is expected to treat a failed generate()
        as a reason to abort the run, same as the original pipeline's
        "Critical Error: Pipeline aborted due to Gemini API failure"
        behavior.
        """
        ...


# --- What an attempt did, and how long it took -------------------------------
#
# The vocabulary lives here rather than in spend.py because the provider layer
# is what produces it; the ledger stores an opaque string. That direction is
# what lets a provider add an outcome without a storage change.
#
# Values are facts, not judgements. An HTTP answer is recorded by its code
# whatever the code is — 200 and 400 are both "the server answered", and which
# of those counts as success is the caller's question, not this layer's.

OUTCOME_TIMEOUT = "timeout"
"""OUR OWN deadline expired — not the provider's, and not a standard cut
somewhere in the path. This is the outcome roadmap item 80 turns on: a
request that reached the ceiling never got an answer, and no ceiling is the
right instrument for a connection that may never answer.

It covers connect and read timeouts alike, because `requests.ConnectTimeout`
subclasses `Timeout` (checked, not assumed). Those are different failures —
never connected, versus connected and hung — and this value does NOT
separate them. The elapsed time does in practice: a connect timeout fails
long before the ceiling, while the failures that prompted item 80 all landed
a fraction of a second past it. Split the outcome if that ever stops holding;
do not infer the split from the word alone."""

OUTCOME_ERROR = "error"
"""Any other transport failure — refused, DNS, connection reset."""


def http_outcome(status_code: int) -> str:
    """The server answered, with this status."""
    return f"http_{status_code}"


AfterAttempt = Callable[[str, float], None]
"""Called once per request AFTER it resolves, with (outcome, elapsed seconds).

Optional, unlike `before_attempt`. A provider that never calls it stays
correct under the spend cap and simply leaves rows open — so an unfinished
row is not warned about the way an unrecorded call is. Elapsed covers the
request only, never the backoff waited afterwards: folding the retry
schedule in would make the ledger agree with whatever delay was configured,
which is the circularity that made the first reading of the 60s timeout
wrong.
"""


def report_outcome(
    after_attempt: AfterAttempt | None, outcome: str, started: float
) -> None:
    """Completes the ledger row `before_attempt` opened, if anyone is listening.

    [started] is a `time.monotonic()` reading taken immediately before the
    request — monotonic rather than wall clock, so a clock adjustment mid-call
    cannot produce a negative or wildly long duration.
    """
    if after_attempt is None:
        return
    after_attempt(outcome, time.monotonic() - started)


# --- What the model did with the request -------------------------------------


@dataclass(frozen=True)
class ResponseMeta:
    """How a call ENDED, as opposed to how its HTTP request resolved.

    `after_attempt` above answers "did the server answer, and how fast" and
    fires per request, before the body is parsed. That is a different question
    from this one, and on 2026-09-10 the difference cost a published forecast:
    the request returned HTTP 200 in 54.5s and the ledger recorded exactly
    that, while the model had spent those seconds emitting "Passtaken" 1,196
    times into a UV Index field.

    Nothing recorded why it stopped or how many tokens it spent, so when the
    question came — was this the token ceiling, or a sampler that collapsed
    well inside it? — the record could not answer, and the archived prompt was
    the only evidence left. See ROADMAP item 100.

    EVERY FIELD IS OPTIONAL because every provider reports these differently
    and some responses omit them entirely. A missing value means the provider
    did not say, never zero — the same rule the forecast payload runs on.
    """

    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # THINKING EFFORT — ROADMAP items 80 and 132, added 2026-09-15.
    #
    # Recorded because the production switch to the Interactions endpoint
    # changes TWO things at once and this is the only one of them that can be
    # seen from the record. `generateContent` is sent `thinkingLevel: "high"`;
    # the Interactions endpoint is sent no equivalent, because whether it
    # takes one is unmeasured and a silently-ignored setting is worse than an
    # absent one. So the endpoint changes and the thinking effort changes
    # with it.
    #
    # Both APIs report the count — `usageMetadata.thoughtsTokenCount` and
    # `usage.total_thought_tokens` — and neither was being stored, so "did
    # thinking actually drop, and by how much" was a question the record could
    # not answer about its own change. That is the same shape as item 100: the
    # ledger knew a call took 54.5 seconds and not what it spent them on.
    #
    # NOT a quality measure. It says how much the model thought, never how
    # well. Pair it with the accuracy record, which is the thing that can.
    thought_tokens: int | None = None
    # WHAT THE CALL WAS ALLOWED TO OMIT — ROADMAP items 59 and 102.
    #
    # These two describe the REQUEST, not how it ended, which is a real
    # strain on this class's name. They ride here anyway because the
    # question they exist to answer is about the PAIRING: three optional
    # fields came back empty on three consecutive runs and filled on a
    # re-run of the same input, and deciding whether the schema had anything
    # to do with it means reading what was sent beside what came back. Split
    # them out if a second request-side fact ever needs recording; one does
    # not earn a class.
    #
    # Optional for the same reason every field above is: a provider that
    # does not report them gives None, and None is not "the schema had no
    # nullable fields" — which is a real and different answer, spelled `()`.
    response_schema_sha256: str | None = None
    nullable_fields: tuple[str, ...] | None = None


AfterResponse = Callable[[ResponseMeta], None]
"""Called once per successful `generate()`, after the body parses.

Optional, like `after_attempt`. Fires ONCE per generate rather than once per
HTTP attempt: a retried call has several attempts and one response, and it is
the response that produced the forecast.

Not called when `generate()` raises. A run that aborted has no forecast to
explain, and the exception already carries the reason — including, since item
100, the finish reason itself when that is what caused the abort.
"""
