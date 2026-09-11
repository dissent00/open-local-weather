"""GeminiProvider: the first LLMProvider implementation, matching the
original pipeline's free-tier Gemini usage.

Uses Gemini's native structured-output support (responseMimeType=
application/json + responseSchema) rather than asking for JSON in the prompt
and hoping — same mechanism the original pipeline relied on. Note: at the
time this was first written, "gemini-3.6-flash" (the reference script's
CONFIG.GEMINI_MODEL value) didn't match any known Gemini model id and was
assumed to be a placeholder or typo — it has since turned out to be a real,
current model, and is this project's default (see cli.py's
DEFAULT_GEMINI_MODEL). Whatever id is used, Gemini's available models
change over time — if generate() starts returning 404s, check
`GET /v1beta/models?key=...` for the current lineup rather than assuming
the id is still wrong.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
import time
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from openlocalweather.llm.provider import (
    OUTCOME_ERROR,
    OUTCOME_TIMEOUT,
    AfterAttempt,
    AfterResponse,
    ResponseMeta,
    http_outcome,
    report_outcome,
)
from openlocalweather.llm.errors import LLMResponseError
from openlocalweather.llm.schema import gemini_schema_facts, to_gemini_schema

# Re-exported: it lived here until 2026-09-11 and cli.py, the other
# providers and several tests import it from this module.
__all__ = ["GeminiProvider", "LLMResponseError"]

T = TypeVar("T", bound=BaseModel)

GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# 90s, raised from 60s on 2026-09-04, and the evidence is the spend ledger.
#
# Every attempt is timestamped there before it is sent, so subtracting the
# known backoff from consecutive timestamps recovers how long each FAILED
# attempt ran. Across the scheduled runs on record (13 failed attempts;
# replay bursts excluded because consecutive entries there are separate
# cases, not retries) the distribution is bimodal and has no middle:
#
#     8 of 13 ran 60.1s   — exactly the ceiling
#     5 of 13 ran 1.6s to 29.5s
#
# Nothing has ever failed at 35s, or 45s. A cluster sitting precisely on the
# client's own deadline is the client giving up, not the server refusing.
#
# This corrects an earlier reading. A single probe on 2026-09-04 returned the
# real production prompt (33,777 char system, 4,590 char user) at
# thinking_level="high" in 32.9s, and that was written up as "generation is
# comfortably inside 60s, so the ceiling is not what fails". One success
# shows generation CAN finish in 33s. It says nothing about the tail, and the
# ledger's 8 hits on the deadline are the tail.
#
# What 90s does NOT settle: whether those attempts were slow generations that
# would have completed, or connections already hung. The two are
# indistinguishable from this side, and they predict different things —
# slow generations turn into successes at 90s, hung connections just fail 30s
# later for the same cost. Check the ledger again in a week: if 90.1s
# replaces 60.1s as the cluster, it was hangs and the timeout is not the fix.
#
# ANSWERED 2026-09-07, AND THE PREDICTION FAILED. 90.1s has replaced 60.1s as
# the cluster: three failed attempts since the change (2026-09-05 03:01:45,
# 2026-09-07 03:01:40, 2026-09-07 03:03:40) all ran 90.1s exactly, and NOT ONE
# has failed anywhere near 60s since. By the criterion written above, these
# are hangs and the timeout is not the fix — raising it bought a longer wait
# before giving up, not a completed generation.
#
# Two things that follow, both of which the count above cannot settle. The
# sample is three, and 2026-09-06/07 was a Google incident, so this is the
# behaviour under degradation and not necessarily under normal load.
#
# It does settle one question that was asked separately: whether 60s was a
# STANDARD HTTP timeout somewhere in the path rather than ours. It was not. A
# fixed 60s cut upstream would still be cutting at 60s; the cluster moved when
# WE moved, which makes it ours.
#
# Do not raise this to 120s on the strength of it. If the failure is a hung
# connection then no synchronous deadline is the right instrument, and the
# provider now offers an asynchronous one — see ROADMAP item 80.
REQUEST_TIMEOUT_S = 90

# Transient, retryable HTTP statuses: 429 rate-limited, 500/502/503/504
# server-side or capacity errors. Observed in practice — a "This model is
# currently experiencing high demand" 503 aborted a real run during
# development. Without a retry, a demand spike coinciding with the daily
# cron silently costs a whole day's forecast (and, downstream, that day's
# verification check), so a few cheap retries are well worth it.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
# 30s base: 30s, 60s, 120s — ~3.5 min of waiting across the four attempts.
#
# The old 5/10/20 schedule spent its whole budget inside ~35 seconds, which
# is the wrong shape for what actually goes wrong here. A 503 from this API
# means capacity, and capacity comes back on the scale of minutes, not
# seconds: four attempts crammed into half a minute all land inside the same
# bad minute and all fail together. The 09-03 forecast run is the clean
# example: four attempts, three of them dying on the 60s ceiling, the
# whole burst over in 215 seconds.
#
# Widening the schedule costs no extra billable requests — the count is
# still MAX_ATTEMPTS — and no time at all on a run that succeeds first try.
# It only spends wall-clock on runs that were failing anyway, and it is the
# one knob that turns "this minute is bad" into "these four minutes are bad"
# before giving up.
RETRY_BASE_DELAY_S = 30

# gemini-3.x's reasoning-effort control. REST field is nested and camelCase
# — generationConfig.thinkingConfig.thinkingLevel — confirmed empirically
# against the live API (Google's own docs summary showed the Python-SDK
# snake_case form, "thinking_level", which the REST endpoint actually
# rejects). Measured against this project's real production prompt:
# "low" -> 739 thinking tokens, "high" -> 4,235 — a real difference, and
# at ~45K tokens/call against a 250K-token/run free-tier limit there's
# ample headroom to default to "high" for the genuinely multi-step
# reasoning this pipeline asks for (reconciling disagreeing models,
# weighing recency against long-term track record).
VALID_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})


# The only stop reason that means "I finished the answer". Everything else —
# MAX_TOKENS, RECITATION, SAFETY, OTHER — means the text is not what was
# asked for, however well it parses.
FINISH_REASON_COMPLETE = "STOP"


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        thinking_level: str | None = None,
        before_attempt: Callable[[], None] | None = None,
        after_attempt: AfterAttempt | None = None,
        after_response: AfterResponse | None = None,
    ):
        if not api_key:
            raise ValueError("GeminiProvider requires a non-empty api_key.")
        if not model:
            raise ValueError("GeminiProvider requires a non-empty model id.")
        if thinking_level is not None and thinking_level not in VALID_THINKING_LEVELS:
            raise ValueError(
                f"thinking_level must be one of {sorted(VALID_THINKING_LEVELS)} or None, got {thinking_level!r}."
            )
        self.api_key = api_key
        self.model = model
        self.thinking_level = thinking_level
        # Called immediately before EACH HTTP request, including every retry.
        #
        # The spend cap lives here rather than around generate() because one
        # generate() can send up to MAX_ATTEMPTS requests. Counting once per
        # forecast let a flaky provider issue four billable requests against a
        # single recorded call — under a cap whose own documentation says it
        # counts calls, not forecasts. Raising from this hook aborts the retry
        # loop, which is the correct response to "you are out of budget".
        self.before_attempt = before_attempt
        # The other half: called after each request resolves, with what it
        # did and how long it took. See AfterAttempt in provider.py.
        self.after_attempt = after_attempt
        self.after_response = after_response

    def _post_with_retry(self, url: str, payload: dict) -> requests.Response:
        """POSTs with bounded exponential backoff on transient failures.

        Network errors and RETRYABLE_STATUS_CODES are retried; everything
        else (a bad API key, a deprecated model id, a malformed request)
        is returned immediately for the caller to raise on — retrying those
        just wastes time and quota, since they'll fail identically.
        """
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.before_attempt is not None:
                self.before_attempt()
            started = time.monotonic()
            try:
                resp = requests.post(
                    url, params={"key": self.api_key}, json=payload, timeout=REQUEST_TIMEOUT_S
                )
                report_outcome(self.after_attempt, http_outcome(resp.status_code), started)
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    return resp
                last_exc = LLMResponseError(f"Gemini returned HTTP {resp.status_code}")
            # Timeout before RequestException: it is a subclass, and it is the
            # one this measurement exists to separate from the rest.
            except requests.Timeout as e:
                report_outcome(self.after_attempt, OUTCOME_TIMEOUT, started)
                last_exc = e
            except requests.RequestException as e:
                report_outcome(self.after_attempt, OUTCOME_ERROR, started)
                last_exc = e

            if attempt < MAX_ATTEMPTS:
                delay = RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                print(
                    f"Gemini call failed ({last_exc}); retrying in {delay}s "
                    f"(attempt {attempt}/{MAX_ATTEMPTS}).",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise LLMResponseError(
            f"Gemini request failed after {MAX_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    def generate(self, system_prompt: str, user_prompt: str, response_schema: type[T]) -> T:
        url = GEMINI_API_URL_TEMPLATE.format(model=self.model)
        # Built once and held, so the facts reported below fingerprint the
        # schema this call actually sent rather than a second build of it.
        wire_schema = to_gemini_schema(response_schema)
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": wire_schema,
            },
        }
        if self.thinking_level is not None:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self.thinking_level}

        resp = self._post_with_retry(url, payload)

        try:
            body = resp.json()
        except ValueError as e:
            raise LLMResponseError(
                f"Gemini returned a non-JSON response (HTTP {resp.status_code}): {resp.text[:500]}"
            ) from e

        if resp.status_code != 200 or "error" in body:
            err = body.get("error", {})
            raise LLMResponseError(
                f"Gemini error ({err.get('code', resp.status_code)}): "
                f"{err.get('message', resp.text[:500])}"
            )

        candidates = body.get("candidates") or []
        if not candidates:
            raise LLMResponseError("Gemini returned no candidates.")

        # WHY THE STOP REASON IS CHECKED BEFORE THE CONTENT IS READ.
        #
        # On 2026-09-10 the evening run published a UV index of 15,930
        # characters: a plausible opening, then "Passtaken" some nine hundred
        # times, then several kilobytes of unrelated recited text. The JSON
        # parsed. It validated. It was stored, rendered onto the site and
        # mailed out, and every test in this suite stayed green, because
        # nothing here ever asked why the model stopped talking.
        #
        # A candidate that ran into the token ceiling mid-loop is, to code
        # that only parses `parts[0].text`, identical to one that finished its
        # sentence. So the reason is checked first: the content is not worth
        # reading until the model says it meant to stop.
        #
        # PERMISSIVE ABOUT ABSENCE, strict about a wrong value. The field is
        # not guaranteed on every response shape, and refusing a response for
        # a missing field would turn a provider change into a total outage.
        finish_reason = candidates[0].get("finishReason")
        if finish_reason is not None and finish_reason != FINISH_REASON_COMPLETE:
            raise LLMResponseError(
                f"Gemini stopped for {finish_reason}, not {FINISH_REASON_COMPLETE} — "
                "the response is incomplete or is not the answer that was asked for."
            )

        try:
            text = candidates[0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMResponseError(f"Gemini response did not contain the expected JSON payload: {e}") from e

        try:
            validated = response_schema.model_validate(data)
        except ValidationError as e:
            raise LLMResponseError(f"Gemini response failed schema validation: {e}") from e

        # AFTER validation, not before: a response that fails the schema
        # produced no forecast, and the exception already carries the reason.
        if self.after_response is not None:
            usage = body.get("usageMetadata") or {}
            schema_sha256, nullable_fields = gemini_schema_facts(wire_schema)
            self.after_response(
                ResponseMeta(
                    finish_reason=finish_reason,
                    input_tokens=usage.get("promptTokenCount"),
                    output_tokens=usage.get("candidatesTokenCount"),
                    response_schema_sha256=schema_sha256,
                    nullable_fields=nullable_fields,
                )
            )

        return validated
