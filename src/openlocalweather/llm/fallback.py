"""One provider made of several, tried in order — ROADMAP item 81.

WHY THIS EXISTS, measured. Over 2026-08-28 to 09-21 the evening run failed on
Gemini 5xx five times in 29 runs and the morning run never did in 25
(one-sided Fisher p = 0.038). Every one was a 500 or a 503 after four
attempts across roughly eight and a half minutes, so the vendor was shedding
load for longer than any retry schedule this project would accept. No amount
of waiting fixes that; another vendor does.

THE SEAM WAS ALREADY CHOSEN. `provider.py`'s docstring says a new provider is
a new class implementing the same Protocol and never a change to pipeline.py,
and `config.py` has carried `llm_providers` as a LIST since 2026-09-15 with a
validator that says out loud that only the first entry is used and names this
item as the reason. So this is the thing that list was shaped for, and
nothing above it changes: the pipeline, the two-call sequence and the replay
all still see one object with one `generate`.

WHAT IT DOES NOT DO. It does not retry — each provider owns its own schedule,
and this runs after one has given up. It does not fall back on a model that
answered badly; see `LLMUnavailableError` for that distinction, which is the
whole of the branching logic here.
"""

from __future__ import annotations

import sys
from typing import TypeVar

from pydantic import BaseModel

from openlocalweather.llm.errors import LLMUnavailableError

T = TypeVar("T", bound=BaseModel)


class FallbackProvider:
    """Tries each provider in order until one answers.

    PER CALL, NOT PER RUN, and that is the larger half of the gain. A forecast
    is two calls: the judgment, whose failure aborts the run, and the
    narrative, whose failure currently degrades the issuance to a placeholder
    under DEGRADATION_NARRATIVE. Falling back per call means the narrative
    tries the next vendor BEFORE it degrades, so the placeholder becomes the
    third outcome rather than the second.
    """

    def __init__(self, providers: list):
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")

        self._providers = list(providers)
        # Which child is serving the current request, read by
        # `provider_identity` so the spend ledger keeps naming the vendor that
        # actually spent. None between calls rather than left on the last one:
        # a stale value would attribute the next caller's request to whoever
        # happened to answer the previous one.
        self.active_provider = None
        # WHICH CHILD LAST ANSWERED, and deliberately NOT cleared between
        # calls — item 171. `active_provider` answers "who is spending right
        # now" and must go stale-proof; this answers "who produced the thing
        # being recorded", which is asked after the call has returned and the
        # first attribute is already None. A forecast makes two calls and the
        # pipeline snapshots this after each, so the two can differ — on
        # 2026-09-23 Gemini took the judgment call and an OpenRouter model
        # wrote the narrative.
        self.last_served = None
        self._before_attempt = None
        self._after_attempt = None
        self._after_response = None

    @property
    def served_provider(self):
        """The child a record about the last output should credit.

        `last_served` once anything has answered, and the entry that WOULD be
        tried first before that — an idle chain has produced nothing to
        credit, and "what will this deployment use" is the only honest answer
        to the question at that point. Keeps `_providers` private; the
        `model` property below reads it the same way and for the same reason.
        """
        return self.last_served or self._providers[0]

    @property
    def model(self) -> str:
        """The first entry's model, for a caller that asks between calls.

        `provider_identity` resolves through `active_provider` during a call
        and never reaches this, so this answers the idle question — "what will
        this deployment use" — with the entry that will be tried first.
        """
        return getattr(self._providers[0], "model", "unknown")

    # THE HOOKS ARE SET ON THE OBJECT, NOT PASSED TO IT. `attach_spend_cap`
    # assigns them after construction, by design — cli.py builds the provider
    # and has no reason to know where the ledger lives. A wrapper therefore has
    # to forward an assignment down, or the children would make counted
    # requests while reporting nothing, which is the one failure the cap
    # exists to prevent and which `tests/test_spend_coverage.py` was written
    # after three separate occurrences of.
    def _fan_out(self, name: str, value) -> None:
        for provider in self._providers:
            setattr(provider, name, value)

    @property
    def before_attempt(self):
        return self._before_attempt

    @before_attempt.setter
    def before_attempt(self, value) -> None:
        self._before_attempt = value
        self._fan_out("before_attempt", value)

    @property
    def after_attempt(self):
        return self._after_attempt

    @after_attempt.setter
    def after_attempt(self, value) -> None:
        self._after_attempt = value
        self._fan_out("after_attempt", value)

    @property
    def after_response(self):
        return self._after_response

    @after_response.setter
    def after_response(self, value) -> None:
        self._after_response = value
        self._fan_out("after_response", value)

    @property
    def on_poll(self):
        return getattr(self, "_on_poll", None)

    @on_poll.setter
    def on_poll(self, value) -> None:
        # Only to children that have one. Polling is one provider's
        # implementation detail — see attach_spend_cap — and giving the others
        # an attribute they never call would make `hasattr` lie about them.
        self._on_poll = value
        for provider in self._providers:
            if hasattr(provider, "on_poll"):
                provider.on_poll = value

    def generate(self, system_prompt: str, user_prompt: str, response_schema: type[T]) -> T:
        last: LLMUnavailableError | None = None

        for position, provider in enumerate(self._providers, start=1):
            name, model = type(provider).__name__, getattr(provider, "model", "unknown")
            self.active_provider = provider
            try:
                answer = provider.generate(system_prompt, user_prompt, response_schema)
                # WHO ANSWERED, KEPT PAST THE `finally` BELOW — item 171.
                # `active_provider` is cleared the moment this returns, so
                # anything asking afterwards — and the log entry is written
                # afterwards — resolves back to this wrapper and reads
                # `.model`, which is the FIRST link whoever served. That is
                # how a forecast written end to end by a fallback was filed
                # under `gemini-3.6-flash`.
                self.last_served = provider
                return answer
            except LLMUnavailableError as e:
                # LOUD, because a silent fallback is the failure `config.py`'s
                # validator already warns about: a deployment quietly served by
                # its second choice looks exactly like one served by its first,
                # and the difference is the whole reliability question.
                last = e
                remaining = len(self._providers) - position
                print(
                    f"{name} ({model}) is unavailable: {e}. "
                    + (
                        f"Falling back, {remaining} provider(s) left."
                        if remaining
                        else "No providers left."
                    ),
                    file=sys.stderr,
                )
            finally:
                self.active_provider = None

        # The LAST failure, not a summary of all of them. Every one was printed
        # above as it happened, and the caller's handling — abort the run, or
        # degrade the issuance — turns on the kind of error rather than on its
        # text. Raising the real exception keeps that kind intact.
        raise last  # type: ignore[misc]
