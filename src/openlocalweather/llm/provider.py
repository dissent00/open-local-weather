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

T = TypeVar("T", bound=BaseModel)


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
