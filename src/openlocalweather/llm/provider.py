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
