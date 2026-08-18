"""AnthropicProvider: LLMProvider implementation for Claude, via the
Anthropic Messages API.

Kept separate from OpenAICompatProvider rather than folded into it,
because Anthropic's API differs in every way that matters to this class:

  - auth is `x-api-key`, not `Authorization: Bearer`
  - a version header (`anthropic-version`) is mandatory
  - the system prompt is a TOP-LEVEL `system` field, not a message with
    role "system"
  - `max_tokens` is required, not optional
  - structured output is done with FORCED TOOL USE, not `response_format`

That last one is the substantive difference. Anthropic's reliable way to
get a guaranteed-shape object is to declare a tool whose `input_schema`
is the JSON Schema you want, then force it with
`tool_choice: {"type": "tool", ...}` — the model's `tool_use` block then
carries the structured object directly in `input`, already parsed, with
no JSON-in-a-string step and no markdown-fence stripping needed. It is
the sturdiest of the three providers' structured-output paths for that
reason.

Set LLM_PROVIDER=anthropic plus LLM_API_KEY and LLM_MODEL; see cli.py and
QUICKSTART.md. LLM_BASE_URL is optional and only needed for a proxy or
gateway.
"""

from __future__ import annotations

import sys
import time
from typing import Any, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from openlocalweather.llm.gemini import LLMResponseError
from openlocalweather.llm.schema import to_strict_json_schema

T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URL = "https://api.anthropic.com"
# Required by the API on every request. Pinned deliberately: Anthropic
# versions its API by date and an unpinned client would silently change
# behavior under us.
ANTHROPIC_VERSION = "2023-06-01"

REQUEST_TIMEOUT_S = 120

# 529 ("overloaded_error") is Anthropic-specific and genuinely transient —
# the exact class of failure that cost this project a whole run before
# GeminiProvider grew retries, so it belongs here from the start.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 4
RETRY_BASE_DELAY_S = 5  # exponential: 5s, 10s, 20s

# Generous but bounded. The real production forecast measured ~2,350
# output tokens, and roadmap item 14 (multiple audience voices) will grow
# that, so a low ceiling would truncate mid-narrative — which surfaces as
# stop_reason "max_tokens" and is reported explicitly below rather than
# silently returning a half-written forecast.
DEFAULT_MAX_TOKENS = 8192

TOOL_NAME = "structured_response"


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ):
        if not api_key:
            raise ValueError("AnthropicProvider requires a non-empty api_key.")
        if not model:
            raise ValueError("AnthropicProvider requires a non-empty model id.")
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _post_with_retry(self, payload: dict) -> requests.Response:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    self.endpoint, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_S
                )
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    return resp
                last_exc = LLMResponseError(f"Anthropic returned HTTP {resp.status_code}")
                delay = _retry_after_seconds(resp) or RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
            except requests.RequestException as e:
                last_exc = e
                delay = RETRY_BASE_DELAY_S * (2 ** (attempt - 1))

            if attempt < MAX_ATTEMPTS:
                print(
                    f"Anthropic call failed ({last_exc}); retrying in {delay}s "
                    f"(attempt {attempt}/{MAX_ATTEMPTS}).",
                    file=sys.stderr,
                )
                time.sleep(delay)

        raise LLMResponseError(
            f"Anthropic request failed after {MAX_ATTEMPTS} attempts: {last_exc}"
        ) from last_exc

    def generate(self, system_prompt: str, user_prompt: str, response_schema: type[T]) -> T:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            # Top-level, not a "system" role message — Anthropic rejects
            # role: "system" inside `messages`.
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": (
                        f"Return the complete {response_schema.__name__} object. "
                        "This is the only way to answer; do not reply with prose."
                    ),
                    "input_schema": to_strict_json_schema(response_schema),
                }
            ],
            # Forces the model to call the tool rather than deciding for
            # itself whether to — without this, a model may answer in
            # prose and produce no structured output at all.
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        resp = self._post_with_retry(payload)

        try:
            body = resp.json()
        except ValueError as e:
            raise LLMResponseError(
                f"Anthropic returned a non-JSON response (HTTP {resp.status_code}): {resp.text[:500]}"
            ) from e

        if resp.status_code != 200 or body.get("type") == "error":
            err = body.get("error", {})
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise LLMResponseError(
                f"Anthropic error (HTTP {resp.status_code}): {message or resp.text[:500]}"
            )

        content_blocks = body.get("content") or []
        tool_input = None
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_input = block.get("input")
                break

        if tool_input is None:
            # Most often a truncated response: the model started the tool
            # call but ran out of tokens before completing it. Say which,
            # rather than a bare "no tool_use block".
            stop_reason = body.get("stop_reason")
            if stop_reason == "max_tokens":
                raise LLMResponseError(
                    f"Anthropic response was truncated at max_tokens={self.max_tokens} "
                    "before completing the structured response — raise LLM_MAX_TOKENS."
                )
            raise LLMResponseError(
                f"Anthropic returned no tool_use block (stop_reason={stop_reason!r})."
            )

        try:
            return response_schema.model_validate(tool_input)
        except ValidationError as e:
            raise LLMResponseError(f"Anthropic response failed schema validation: {e}") from e


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """Honors Retry-After when Anthropic sends one on a 429. Ignores the
    HTTP-date form and anything implausibly long — better to fail the run
    and let the next scheduled attempt pick it up than block a CI job for
    minutes."""
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if 0 < seconds <= 60 else None
