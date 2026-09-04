"""OpenAICompatProvider: one LLMProvider implementation covering every
service that speaks the OpenAI `/chat/completions` API.

That is deliberately a large family, and the reason this is the second
provider rather than a service-specific one: OpenAI, OpenRouter, Groq,
Cerebras, Together, DeepInfra, vLLM, LM Studio and Ollama all accept the
same request shape, so a single class plus a `base_url` gives a forker a
genuine choice of LLM without this project having to maintain one adapter
per vendor. Point it at whichever endpoint you have a key for:

    OpenAI       https://api.openai.com/v1
    OpenRouter   https://openrouter.ai/api/v1
    Groq         https://api.groq.com/openai/v1
    Together     https://api.together.xyz/v1
    Ollama       http://localhost:11434/v1     (local, no key needed)

See cli.py for the environment variables that select and configure this,
and QUICKSTART.md for the setup walkthrough.

STRUCTURED OUTPUT: defaults to `response_format.json_schema` with
strict: true — the modern standard, supported by OpenAI, OpenRouter,
Groq and vLLM, and the only mode that actually constrains generation to
the schema. Endpoints that don't implement it (some local runtimes, some
older models) can fall back to `json_object` mode, which only guarantees
*valid JSON*, not the right shape — so in that mode the schema is
injected into the system prompt instead. Either way the response is
validated against the pydantic model before being returned, so a
malformed response fails loudly rather than silently producing a broken
forecast.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from openlocalweather.llm.gemini import LLMResponseError
from openlocalweather.llm.schema import to_strict_json_schema

T = TypeVar("T", bound=BaseModel)

REQUEST_TIMEOUT_S = 120  # generous: some hosted models are slow to first token

# Same transient-failure handling as GeminiProvider (see gemini.py's
# comment for the incident that motivated it). Deliberately duplicated
# rather than shared: each provider owns its own HTTP quirks, per
# llm/provider.py's contract, and the two differ already — this one
# honors Retry-After, which Gemini's API doesn't send.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
# 30s, 60s, 120s. Widened with Gemini's on 2026-09-04 — see gemini.py for
# the reasoning and the measurement. No evidence from THIS provider drove
# it; the argument is about how provider capacity recovers, which is not
# a Gemini trait, and leaving two of the three on a schedule already
# shown to be too tight would only hide the next occurrence.
RETRY_BASE_DELAY_S = 30
RETRY_AFTER_MAX_S = RETRY_BASE_DELAY_S * 2 ** (MAX_ATTEMPTS - 2)  # the longest we impose ourselves

VALID_JSON_MODES = frozenset({"json_schema", "json_object"})

SCHEMA_PROMPT_TEMPLATE = (
    "\n\nReturn ONLY a JSON object conforming exactly to this JSON Schema. "
    "Do not wrap it in markdown fences or add commentary:\n{schema}"
)


class OpenAICompatProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        json_mode: str = "json_schema",
        temperature: float | None = None,
        before_attempt: Callable[[], None] | None = None,
    ):
        # api_key is intentionally NOT required to be non-empty: local
        # runtimes (Ollama, LM Studio) accept any value or none at all,
        # and refusing to start without one would block the fully-free
        # local-model path this class exists to enable.
        if not model:
            raise ValueError("OpenAICompatProvider requires a non-empty model id.")
        if not base_url:
            raise ValueError(
                "OpenAICompatProvider requires a base_url, e.g. https://openrouter.ai/api/v1"
            )
        if json_mode not in VALID_JSON_MODES:
            raise ValueError(
                f"json_mode must be one of {sorted(VALID_JSON_MODES)}, got {json_mode!r}."
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.json_mode = json_mode
        self.temperature = temperature
        # Called immediately before EACH HTTP request, including every retry.
        #
        # The spend cap lives here rather than around generate() because one
        # generate() can send up to MAX_ATTEMPTS requests. Counting once per
        # forecast let a flaky provider issue four billable requests against a
        # single recorded call — under a cap whose own documentation says it
        # counts calls, not forecasts. Raising from this hook aborts the retry
        # loop, which is the correct response to "you are out of budget".
        self.before_attempt = before_attempt

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _post_with_retry(self, payload: dict) -> requests.Response:
        """POSTs with bounded exponential backoff on transient failures,
        mirroring GeminiProvider._post_with_retry. A 4xx that isn't in
        RETRYABLE_STATUS_CODES (bad key, unknown model, unsupported
        response_format) returns immediately for the caller to raise on —
        retrying those just burns time and quota."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.before_attempt is not None:
                self.before_attempt()
            try:
                resp = requests.post(
                    self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_S
                )
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    return resp
                last_exc = LLMResponseError(f"{self.base_url} returned HTTP {resp.status_code}")
                delay = _retry_after_seconds(resp) or RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
            except requests.RequestException as e:
                last_exc = e
                delay = RETRY_BASE_DELAY_S * (2 ** (attempt - 1))

            if attempt < MAX_ATTEMPTS:
                print(
                    f"LLM call failed ({last_exc}); retrying in {delay}s "
                    f"(attempt {attempt}/{MAX_ATTEMPTS}).",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise LLMResponseError(
            f"LLM request to {self.endpoint} failed after {MAX_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    def generate(self, system_prompt: str, user_prompt: str, response_schema: type[T]) -> T:
        schema = to_strict_json_schema(response_schema)

        if self.json_mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            # json_object only promises syntactically valid JSON, so the
            # shape has to be described in the prompt instead.
            response_format = {"type": "json_object"}
            system_prompt = system_prompt + SCHEMA_PROMPT_TEMPLATE.format(
                schema=json.dumps(schema, indent=2)
            )

        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": response_format,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        resp = self._post_with_retry(payload)

        try:
            body = resp.json()
        except ValueError as e:
            raise LLMResponseError(
                f"LLM returned a non-JSON response (HTTP {resp.status_code}): {resp.text[:500]}"
            ) from e

        if resp.status_code != 200 or "error" in body:
            err = body.get("error", {})
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise LLMResponseError(
                f"LLM error (HTTP {resp.status_code}): {message or resp.text[:500]}"
            )

        choices = body.get("choices") or []
        if not choices:
            raise LLMResponseError("LLM returned no choices.")

        try:
            text = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMResponseError(f"LLM response had no message content: {e}") from e

        if text is None:
            # Seen when a model stops on a length/filter finish_reason —
            # surface that reason rather than a bare "None is not JSON".
            raise LLMResponseError(
                f"LLM returned empty content (finish_reason="
                f"{choices[0].get('finish_reason')!r})."
            )

        try:
            data = json.loads(_strip_code_fence(text))
        except ValueError as e:
            raise LLMResponseError(f"LLM response was not valid JSON: {text[:500]}") from e

        try:
            return response_schema.model_validate(data)
        except ValidationError as e:
            raise LLMResponseError(f"LLM response failed schema validation: {e}") from e


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """Honors a Retry-After header when the endpoint sends one (OpenAI and
    Groq both do on 429). Ignores the HTTP-date form and anything
    implausibly long — a provider asking us to wait ten minutes is better
    handled by failing the run and letting the next scheduled attempt pick
    it up than by blocking a CI job that long.

    The ceiling is RETRY_AFTER_MAX_S, and the rule setting it is: never sleep
    longer on a provider's say-so than we would sleep on our own. It tracks
    the longest delay this module imposes by itself, which the 2026-09-04
    widening moved from 20s to 120s. Left at its old 60s while our own
    schedule grew past it, the clamp would have refused a 90s Retry-After --
    an explicit, authoritative instruction from the provider -- and then
    waited 120 seconds anyway on a guess. That is strictly worse."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if 0 < seconds <= RETRY_AFTER_MAX_S else None


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in ```json fences despite being told not to —
    common enough in json_object mode (and with smaller local models) to
    be worth handling rather than failing the whole run over."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
