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
import time
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from openlocalweather.llm.schema import to_gemini_schema

T = TypeVar("T", bound=BaseModel)

GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
REQUEST_TIMEOUT_S = 60

# Transient, retryable HTTP statuses: 429 rate-limited, 500/502/503/504
# server-side or capacity errors. Observed in practice — a "This model is
# currently experiencing high demand" 503 aborted a real run during
# development. Without a retry, a demand spike coinciding with the daily
# cron silently costs a whole day's forecast (and, downstream, that day's
# verification check), so a few cheap retries are well worth it.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
RETRY_BASE_DELAY_S = 5  # exponential: 5s, 10s, 20s — ~35s worst case


class LLMResponseError(RuntimeError):
    """The LLM call failed outright (network/HTTP error, no candidates) or
    its response didn't validate against the requested schema. Either way
    the pipeline should abort loudly rather than publish a malformed or
    missing forecast — mirrors the original pipeline's "Critical Error:
    Pipeline aborted due to Gemini API failure" behavior.
    """


class GeminiProvider:
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GeminiProvider requires a non-empty api_key.")
        if not model:
            raise ValueError("GeminiProvider requires a non-empty model id.")
        self.api_key = api_key
        self.model = model

    def _post_with_retry(self, url: str, payload: dict) -> requests.Response:
        """POSTs with bounded exponential backoff on transient failures.

        Network errors and RETRYABLE_STATUS_CODES are retried; everything
        else (a bad API key, a deprecated model id, a malformed request)
        is returned immediately for the caller to raise on — retrying those
        just wastes time and quota, since they'll fail identically.
        """
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    url, params={"key": self.api_key}, json=payload, timeout=REQUEST_TIMEOUT_S
                )
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    return resp
                last_exc = LLMResponseError(f"Gemini returned HTTP {resp.status_code}")
            except requests.RequestException as e:
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
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(response_schema),
            },
        }

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

        try:
            text = candidates[0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMResponseError(f"Gemini response did not contain the expected JSON payload: {e}") from e

        try:
            return response_schema.model_validate(data)
        except ValidationError as e:
            raise LLMResponseError(f"Gemini response failed schema validation: {e}") from e
