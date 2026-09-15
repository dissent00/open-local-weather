"""Gemini via the Interactions API, submitted as a background job.

ROADMAP items 80, 132 and 28. `generateContent` is labelled legacy and the
getting-started guide is now written around this API, so a move is a question
of when. What made it urgent is different: the free tier began refusing
synchronous generations, and a submit that only ENQUEUES work may be
acceptable when a generation is not.

MEASURED 2026-09-15 with `tools/probe_background_submit.py`, against the real
35,631-token forecast prompt:

    submit accepted           HTTP 200 in 2.823s, status "in_progress"
    completed by              the first poll, +20s
    our synchronous calls     median 44.8s, p95 70.5s

A submit is an order of magnitude cheaper to accept than a generation is to
complete, which is the asymmetry the hypothesis needs. Whether it actually
survives an episode is still unproven — that is the paired run, and it needs
an outage to happen while somebody is watching.

### What this costs, and it is not free

**At least TWO requests where `generateContent` needs one** — a submit and a
poll — against a free-tier ceiling of 20 a day. A forecast is two calls, so a
clean day goes from 4 requests to 8. Whether a poll counts toward that ceiling
is unknown and is answerable from the provider's dashboard after one real run;
if it does, this trade is reliability for headroom and the arithmetic has to
be redone against item 132's numbers.

The poll schedule is therefore sized so the TYPICAL case is one poll: the
probe's job was done inside 20 seconds.

### The envelope, measured rather than assumed

    steps[0]  type "user_input"     the prompt, echoed back
    steps[1]  type "thought"        a signature, no readable content
    steps[2]  type "model_output"   content[].text — the answer

`usage` carries `total_input_tokens` and `total_output_tokens`, so item 80's
token accounting survives the move unchanged.

### The stop-reason guard moves, it does not disappear

`gemini.py` refuses a candidate whose `finishReason` is not STOP, and the
comment beside it is the reason this file has the same check: on 2026-09-10 a
UV index of 15,930 characters was published, rendered and mailed because
nothing asked why the model stopped talking. The JSON parsed and it validated.

There is no `finish_reason` on a step here. The equivalent is the INTERACTION
STATUS: `completed` is the only acceptable terminal state, and `incomplete` is
where a run into the token ceiling surfaces. Anything else is refused by name.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Callable, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from openlocalweather.llm.errors import LLMResponseError
from openlocalweather.llm.gemini import MAX_ATTEMPTS, RETRY_DELAYS_S, RETRYABLE_STATUS_CODES
from openlocalweather.llm.provider import (
    AfterAttempt,
    AfterResponse,
    OUTCOME_ERROR,
    OUTCOME_TIMEOUT,
    ResponseMeta,
    http_outcome,
    report_outcome,
)
from openlocalweather.llm.schema import gemini_schema_facts, to_strict_json_schema

T = TypeVar("T", bound=BaseModel)

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
INTERACTION_URL = INTERACTIONS_URL + "/{id}"

REQUEST_TIMEOUT_S = 90

# The only terminal state that means "this is the answer that was asked for".
STATUS_COMPLETE = "completed"

# Every state that means the job has stopped. `incomplete` is the one that
# matters most: it is where a run into the token ceiling lands, and it is the
# analogue of the MAX_TOKENS finish reason that gemini.py refuses.
TERMINAL_STATUSES = frozenset(
    {STATUS_COMPLETE, "failed", "cancelled", "incomplete", "budget_exceeded"}
)

# Sized so the typical case is ONE poll — the probe's job finished inside 20
# seconds. Every entry after the first is a request the typical forecast never
# spends, and the tail exists for a queue that is genuinely slow rather than
# for the normal case.
POLL_DELAYS_S = (15, 15, 30, 60, 60)

# The step that carries the answer. `thought` steps hold an opaque signature
# and no readable content; `user_input` is the prompt coming back.
STEP_MODEL_OUTPUT = "model_output"


def _error_message(resp: requests.Response, body) -> str | None:
    """The error in a response, or None if there is not one.

    A JSON BODY IS NOT ALWAYS AN OBJECT. Measured 2026-09-15: a rejected
    Interactions submit came back as a LIST — Google wraps some errors as
    `[{"error": {...}}]` — and code that went straight to `body.get("error")`
    raised AttributeError instead of reporting the message. The request was
    spent and its reason was lost, which is the worst outcome available: a
    failure that costs and teaches nothing.

    So every shape is handled, and the raw text is carried whatever happens.
    An error nobody can read is barely better than no error at all.
    """
    payload = body
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), {})
    if not isinstance(payload, dict):
        payload = {}

    err = payload.get("error")
    if resp.status_code == 200 and not err:
        return None

    if isinstance(err, dict):
        code = err.get("code", resp.status_code)
        message = err.get("message") or ""
        status = err.get("status")
        detail = " ".join(part for part in (message, f"({status})" if status else "") if part)
    else:
        code, detail = resp.status_code, ""

    # The raw body ALWAYS, not only when the parse found nothing. A message
    # Google wrote and a body nobody expected are different evidence, and the
    # next person debugging this wants both.
    return f"Interactions error ({code}): {detail or '(no message)'} — body: {resp.text[:600]}"

class GeminiInteractionsProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        before_attempt: Callable[[], None] | None = None,
        after_attempt: AfterAttempt | None = None,
        after_response: AfterResponse | None = None,
        on_poll: Callable[[], None] | None = None,
        background: bool = False,
    ):
        if not api_key:
            raise ValueError("GeminiInteractionsProvider requires a non-empty api_key.")
        if not model:
            raise ValueError("GeminiInteractionsProvider requires a non-empty model id.")
        self.api_key = api_key
        self.model = model
        self.before_attempt = before_attempt
        self.after_attempt = after_attempt
        self.after_response = after_response
        # POLLS ARE REQUESTS AND ARE NOT ATTEMPTS — item 80's open question,
        # settled in item 81. They count against the provider's daily limit, so
        # a caller that tracks spend needs to see them; they are not retries, so
        # they must never consume MAX_ATTEMPTS. A separate hook keeps those two
        # facts from being conflated by whoever wires this up.
        self.on_poll = on_poll
        # DEFAULT FALSE, MEASURED 2026-09-15 — and this is the whole reason
        # the queue question went away.
        #
        # Every sync-vs-async comparison before that date compared
        # `generateContent` against this endpoint WITH `background`, which
        # changes two things at once. Asked without it, the endpoint simply
        # answers: HTTP 200 in 19.982s, `status: "completed"`, the
        # `model_output` step present inline, 4,296 characters of narrative.
        # One request, no polls.
        #
        # THE ARITHMETIC IS WHY IT MATTERS. A background call is 1 submit plus
        # N polls — the first live run spent roughly 3 requests where the
        # synchronous path spends 1. Against a daily limit of 20, two
        # issuances of two calls is ~12 a day backgrounded against ~4 direct,
        # and the entire problem this endpoint was being evaluated to solve IS
        # the daily limit. Backgrounding by default would have made it worse
        # while looking like a fix.
        #
        # `background=True` is kept, not deleted: it is the right shape for a
        # job genuinely slower than a client wants to hold a connection for,
        # and `_poll_to_terminal` already returns a submit that arrives
        # terminal without spending a poll, so both modes run the same path.
        self.background = background

    def generate(self, system_prompt: str, user_prompt: str, response_schema: type[T]) -> T:
        schema = to_strict_json_schema(response_schema)
        payload = {
            "model": self.model,
            "input": user_prompt,
            "system_instruction": system_prompt,
            # THE SCHEMA ITSELF, with no envelope around it — measured, after
            # guessing wrong. An OpenAI-shaped `{"type": "json_schema",
            # "json_schema": {...}}` was refused on 2026-09-15 with a message
            # that named the whole contract:
            #
            #   The value 'json_schema' is not supported for 'type' at
            #   'response_format'. Supported values: 'image', 'array', 'audio',
            #   'text', 'string', 'number', 'video', 'object', 'integer',
            #   'boolean'.
            #
            # So `response_format` IS a JSON Schema, and its `type` is a schema
            # type rather than a wrapper discriminator.
            #
            # THE CONVERTER CHOICE WAS RIGHT AND IS WORTH KEEPING FOR THE
            # REASON, not the luck. `to_strict_json_schema` emits lowercase
            # `object`/`array`/`integer`/`string`, which is exactly the list
            # above; `to_gemini_schema`, sitting in the same module and
            # belonging to the same vendor, emits `OBJECT`/`ARRAY` and would
            # have been refused for a second reason. The field name and the
            # `"object": "interaction"` envelope pointed at the OpenAI
            # conventions, and on the types they were right.
            "response_format": schema,
        }

        # Omitted rather than sent as false: `store=false` is incompatible
        # with `background=true` (item 80), so this API does read these flags
        # in combination, and sending a default we have not measured is how a
        # run looks configured and behaves otherwise.
        if self.background:
            payload["background"] = True

        interaction = self._submit_with_retry(payload)
        interaction_id = interaction.get("id")
        if not interaction_id:
            raise LLMResponseError(
                f"Interactions submit returned no id: {json.dumps(interaction)[:500]}"
            )

        final = self._poll_to_terminal(interaction_id, interaction)
        status = final.get("status")

        # CHECKED BEFORE THE CONTENT IS READ, for the reason gemini.py records
        # at length: on 2026-09-10 a 15,930-character UV index was published,
        # rendered and mailed because nothing asked why the model stopped. It
        # parsed and it validated.
        if status != STATUS_COMPLETE:
            raise LLMResponseError(
                f"Interaction finished as {status!r}, not {STATUS_COMPLETE!r} — "
                "the response is incomplete or is not the answer that was asked for."
            )

        text = _model_output_text(final)
        try:
            validated = response_schema.model_validate_json(text)
        except ValidationError as e:
            raise LLMResponseError(f"Interactions response failed schema validation: {e}") from e

        # AFTER VALIDATION, matching `generateContent` — it fired before, here,
        # and that was a divergence rather than a choice. A response that fails
        # the schema produced no forecast, and recording meta for it would put
        # a row in the record for a run that published nothing, on one path
        # and not the other. Switching production between two providers that
        # disagree about when this fires would put a seam in the record
        # exactly where the endpoint changed.
        if self.after_response is not None:
            usage = final.get("usage") or {}
            schema_sha256, nullable_fields = gemini_schema_facts(schema)
            self.after_response(
                ResponseMeta(
                    finish_reason=status,
                    input_tokens=usage.get("total_input_tokens"),
                    output_tokens=usage.get("total_output_tokens"),
                    # `total_thought_tokens` — measured in the probe envelope
                    # 2026-09-15. The direct call DID think (an 8,160-character
                    # thought signature came back); what is unknown is at what
                    # level, which is what this number is here to show.
                    thought_tokens=usage.get("total_thought_tokens"),
                    # CARRIED ACROSS, or item 102's instrument goes dark on the
                    # exact run that changes the endpoint. These describe the
                    # schema as sent, and the switch does not change the
                    # schema — so a gap here would read as "the schema stopped
                    # being recorded" when the truth is "the provider forgot".
                    response_schema_sha256=schema_sha256,
                    nullable_fields=nullable_fields,
                )
            )

        return validated

    def _submit_with_retry(self, payload: dict) -> dict:
        """Submits, retrying on the same statuses and the same schedule as
        `generateContent` — the argument is about how provider capacity
        recovers, which is not a property of one endpoint."""
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.before_attempt is not None:
                self.before_attempt()
            started = time.monotonic()
            try:
                resp = requests.post(
                    INTERACTIONS_URL,
                    params={"key": self.api_key},
                    json=payload,
                    timeout=REQUEST_TIMEOUT_S,
                )
                report_outcome(self.after_attempt, http_outcome(resp.status_code), started)
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    try:
                        body = resp.json()
                    except ValueError as e:
                        raise LLMResponseError(
                            f"Interactions returned a non-JSON response "
                            f"(HTTP {resp.status_code}): {resp.text[:500]}"
                        ) from e
                    failure = _error_message(resp, body)
                    if failure is not None:
                        raise LLMResponseError(failure)
                    return body
                last_exc = LLMResponseError(f"Gemini returned HTTP {resp.status_code}")
            except requests.Timeout as e:
                report_outcome(self.after_attempt, OUTCOME_TIMEOUT, started)
                last_exc = e
            except requests.RequestException as e:
                report_outcome(self.after_attempt, OUTCOME_ERROR, started)
                last_exc = e

            if attempt < MAX_ATTEMPTS:
                delay = RETRY_DELAYS_S[attempt - 1]
                print(
                    f"Interactions submit failed ({last_exc}); retrying in {delay}s "
                    f"(attempt {attempt}/{MAX_ATTEMPTS}).",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise LLMResponseError(
            f"Interactions submit failed after {MAX_ATTEMPTS} attempts: {last_exc}"
        )

    def _poll_to_terminal(self, interaction_id: str, submitted: dict) -> dict:
        """Follows a job to a terminal state.

        A submit that came back already terminal is returned without spending a
        poll — the API is free to answer immediately and paying a request to
        re-read what we were just handed would be waste.
        """
        if submitted.get("status") in TERMINAL_STATUSES:
            return submitted

        last = submitted
        for delay in POLL_DELAYS_S:
            time.sleep(delay)
            if self.on_poll is not None:
                self.on_poll()
            try:
                resp = requests.get(
                    INTERACTION_URL.format(id=interaction_id),
                    params={"key": self.api_key},
                    timeout=REQUEST_TIMEOUT_S,
                )
                last = resp.json()
            except (requests.RequestException, ValueError) as e:
                # A FAILED POLL IS NOT A FAILED JOB. The work may well be
                # running; only the question about it failed, so this keeps
                # asking rather than abandoning a generation already paid for.
                print(f"Interaction poll failed ({e}); still waiting.", file=sys.stderr)
                continue
            if last.get("status") in TERMINAL_STATUSES:
                return last

        raise LLMResponseError(
            f"Interaction {interaction_id} did not reach a terminal state within "
            f"{sum(POLL_DELAYS_S)}s (last status {last.get('status')!r})."
        )


def _model_output_text(interaction: dict) -> str:
    """The answer, from the step that carries it.

    MEASURED, not guessed: a completed interaction's `steps` are the echoed
    input, a `thought` holding an opaque signature, and a `model_output` whose
    `content` carries the text. Anything else is skipped rather than
    concatenated — a thought signature appended to a forecast would be 11,000
    characters of noise that still parses as a string.
    """
    for step in interaction.get("steps") or []:
        if step.get("type") != STEP_MODEL_OUTPUT:
            continue
        parts = [
            c.get("text", "")
            for c in (step.get("content") or [])
            if c.get("type") == "text" and c.get("text")
        ]
        if parts:
            return "".join(parts)

    raise LLMResponseError(
        "Interaction completed with no model_output text: "
        f"steps were {[s.get('type') for s in interaction.get('steps') or []]}"
    )
