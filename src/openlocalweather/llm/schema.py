"""GeminiForecastResponse: the single source of truth for what the LLM must
return, mirroring the original pipeline's Gemini `responseSchema` field for
field. Provider adapters (to_gemini_schema below; future Groq/Cerebras/
OpenRouter adapters alongside their own provider classes) convert this into
whatever dialect a given provider's structured-output feature expects — the
pipeline and prompt-building code only ever deal with this pydantic model.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


class VerificationNote(BaseModel):
    lead_time_days: int
    note: str


class SkillProfileSummaryItem(BaseModel):
    model: str
    lead_time_days: int
    summary: str


# A generous ceiling on the SHORT display strings the forecaster writes.
#
# These are one-line values a reader sees beside a number: the morning run of
# 2026-09-10 produced "8.7 (Very High)" for UV and "-1.3 hPa (falling)" for
# pressure, fifteen and eighteen characters. That evening the same field came
# back at 15,930 — a repetition loop that parsed, validated, stored and
# published, because a string field with no bound accepts anything at all.
#
# SIZED AGAINST THE WHOLE STORED RECORD, and it was wrong first. The original
# 200 was set from two convenient samples — a replay's 22 characters and one
# morning's 49 — and described in a comment as "roughly four times the longest
# value ever observed". It was not. Measured properly across all 360 display
# strings ever written: the longest is a `synoptic_pattern` of 155 characters,
#
#   "Broad pressure fall across basin with strong NE-to-SE gradient; local
#    afternoon lake-breeze convergence supporting elevated..."
#
# which is an ordinary sentence about an ordinary day, and it sat 45 characters
# from aborting a forecast.
#
# THE COST IS NOT SYMMETRIC, which is what decides the number. Too tight and a
# wordy but correct forecast is refused and the day has none at all. Too loose
# and an odd 800-character value is published, which is ugly and nothing worse
# — the finish-reason check catches the truncation case and autoescape now
# catches the markup case. So this errs long: 6.5x the longest real value, and
# still 16x tighter than the 15,930 characters that caused it.
#
# Enforced on THIS side only: `_convert_node` emits type and description and
# nothing else, so the bound never reaches the provider's schema and cannot
# make a well-behaved response fail upstream. A value over it raises
# ValidationError, which the providers turn into LLMResponseError, which
# aborts the run.
#
# NOT applied to the narrative or the WhatsApp summary: those are long by
# design and the prompt already bounds the summary at 600 characters.
MAX_DISPLAY_STRING = 1000


class TodayProperties(BaseModel):
    """The LLM's synthesized, BLENDED call across all models — genuine
    reasoning, not any one model's raw number. Only rain_expected, rain,
    temp_high_c and temp_low_c are required.

    `temp_high_low` is deliberately absent. It was a display string the model
    wrote, and it drifted in both value and format; it is now computed from
    the two numbers here by `models.format_temp_high_low`. Asking a language
    model to convert units is asking it to do arithmetic, which this project
    does in code."""

    rain_expected: str = Field(max_length=MAX_DISPLAY_STRING)
    onset_window: str | None = Field(default=None, max_length=MAX_DISPLAY_STRING)  # Day+0 only
    peak_wind_kmh: float | None = None  # secondary point, if configured
    temp_high_c: float
    temp_low_c: float

    # The scored commitment.
    #
    # rain_expected and onset_window above are prose, written for a reader.
    # These are the same calls in the form the accuracy record can check, and
    # they are what the blend is scored on as a peer of the models it
    # synthesizes. Prose is what the forecast SAYS; these are what it COMMITS
    # to, and a forecast whose prose and commitment disagree is a bug that is
    # now visible instead of unfalsifiable.
    rain: bool
    # "HH:MM" local, Day+0 only. None means no rain expected, or expected
    # without resolvable timing — never midnight.
    onset_hour: str | None = Field(default=None, max_length=MAX_DISPLAY_STRING)
    precip_mm: float | None = None
    # The forecaster's OWN chance of rain, percent — ROADMAP item 58, and the
    # field where that item's argument actually lands.
    #
    # `rain` above is a boolean, so a confident wrong call and an honest hedge
    # score identically. System-prompt rule 7 therefore has to ask for
    # restraint in English that the ledger does not pay for. This is scored
    # with a proper scoring rule, under which claiming certainty you do not
    # have is the most expensive thing available — so the honesty rule 7 asks
    # for becomes the strategy that wins.
    #
    # OPTIONAL, deliberately. A stored response from before this field
    # existed, or a model that omits it, must not fail a run; it simply is not
    # Brier-scored, exactly like a numerical model that supplies no
    # probability. Absent is not 50.
    rain_probability_pct: int | None = None
    mslp_trend_24h: str | None = Field(default=None, max_length=MAX_DISPLAY_STRING)
    synoptic_pattern: str | None = Field(default=None, max_length=MAX_DISPLAY_STRING)
    uv_index_max: str | None = Field(default=None, max_length=MAX_DISPLAY_STRING)
    air_quality_aqi: str | None = Field(default=None, max_length=MAX_DISPLAY_STRING)


# ROADMAP item 72, minimal shape.
#
# THIS DOCSTRING IS SHIPPED TO THE PROVIDER. to_gemini_schema lifts a model's
# docstring into the response schema's `description`, so it is billed on every
# request and read by the forecaster. Reasoning for a future maintainer goes
# in comments like this one; the docstring says only what the model needs.
#
# RAIN ONLY, deliberately. The full item widens the schema to carry a forecast
# at every lead the record scores; this carries the one variable Brier can
# score and item 58 already built the machinery for. Shipped small and early
# because item 72 shares item 69's property — it changes what FUTURE days
# record and recovers nothing — so every day it waits is a Day+3 call
# permanently lost, while the design question about the remaining fields wants
# real data to answer.
#
# lead_time_days is 3 or 7 because those are the leads the record already
# scores. Days 1 and 2 have no row to land in, and inventing one to hold two
# fields would be the schema change this version exists to postpone.
class ExtendedDayProperties(BaseModel):
    """Your own rain call for one day beyond today. Scored against what
    happens. Omit a lead rather than guess at it."""

    lead_time_days: int
    rain: bool
    rain_probability_pct: int | None = None


class GeminiJudgmentResponse(BaseModel):
    """What the judgment call returns: the scored fields, and nothing else.

    ROADMAP item 59 step 3. This half of the split is the point of it — the
    forecaster deciding these numbers reads an 18,400-character prompt
    instead of a 47,054-character one, and every instruction in it governs a
    number.
    """

    today_properties: TodayProperties
    # ROADMAP item 72. Empty is a legitimate answer and the default:
    # a run that declines to commit at a lead scores nothing there,
    # which is honest, where a guessed boolean is scored wrong exactly
    # as confidently as a real one.
    extended_properties: list[ExtendedDayProperties] = Field(default_factory=list)


class GeminiNarrativeResponse(BaseModel):
    """What the rendering call returns: prose, and only prose.

    THE SEAM IS THIS CLASS. Nothing here is scored, and there is no field a
    scored value could be written into, so the rendering call cannot revise
    the forecast however its prompt is later edited. That separation used to
    be a property of where a paragraph sat inside one string — see
    tests/test_prompt_seam.py, which still checks the weaker claim because a
    prompt can still be edited and this cannot.
    """

    yesterday_verification: str
    verification_notes: list[VerificationNote] = Field(default_factory=list)
    skill_profile_summaries: list[SkillProfileSummaryItem] = Field(default_factory=list)
    today_narrative: str
    whatsapp_summary: str | None = None


# The two calls merged, and the shape everything downstream still reads.
#
# Kept deliberately: the pipeline, the store, the renderers and the Dart port
# all consume this, and the split has no business reaching them. See
# `merge_forecast_response`.
#
# A COMMENT RATHER THAN A DOCSTRING, unlike its two halves. Docstrings on
# these classes are LIFTED INTO THE WIRE SCHEMA and shipped to the provider —
# see the note above `to_gemini_schema`. No call ever sends this shape, so a
# description here would be instructions nobody reads, and adding one churned
# two pinned vectors in both languages before it was noticed.
class GeminiForecastResponse(BaseModel):
    yesterday_verification: str
    verification_notes: list[VerificationNote] = Field(default_factory=list)
    skill_profile_summaries: list[SkillProfileSummaryItem] = Field(default_factory=list)
    today_properties: TodayProperties
    # ROADMAP item 72. Empty is a legitimate answer and the default:
    # a run that declines to commit at a lead scores nothing there,
    # which is honest, where a guessed boolean is scored wrong exactly
    # as confidently as a real one.
    extended_properties: list[ExtendedDayProperties] = Field(default_factory=list)
    today_narrative: str
    whatsapp_summary: str | None = None


def merge_forecast_response(
    judgment: GeminiJudgmentResponse, narrative: GeminiNarrativeResponse
) -> GeminiForecastResponse:
    """Puts the two calls back together in the shape everything downstream
    already reads.

    The merge is total and mechanical — every field of the result comes from
    exactly one of the two inputs, and neither can supply a field the other
    owns. That is what makes the split invisible below this line.
    """
    return GeminiForecastResponse(
        yesterday_verification=narrative.yesterday_verification,
        verification_notes=narrative.verification_notes,
        skill_profile_summaries=narrative.skill_profile_summaries,
        today_properties=judgment.today_properties,
        extended_properties=judgment.extended_properties,
        today_narrative=narrative.today_narrative,
        whatsapp_summary=narrative.whatsapp_summary,
    )


# ---------------------------------------------------------------------------
# pydantic -> Gemini schema adapter
# ---------------------------------------------------------------------------

_JSON_TYPE_TO_GEMINI = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
}


def to_gemini_schema(model: type[BaseModel]) -> dict:
    """Converts a pydantic model's JSON schema into Gemini's `responseSchema`
    dialect (OBJECT/STRING/NUMBER/INTEGER/BOOLEAN/ARRAY, nullable flags).

    Pydantic v2's model_json_schema() emits standard JSON Schema, which uses
    $defs/$ref for nested models and anyOf for Optional[X] — Gemini's schema
    format understands neither, so this inlines both. Keep this adapter and
    its test (test_llm_schema.py) in sync with any schema changes; a
    provider addition later needs its own adapter, not a modification to
    this one.
    """
    json_schema = model.model_json_schema()
    defs = json_schema.get("$defs", {})
    return _convert_node(json_schema, defs)


def gemini_schema_facts(wire: dict) -> tuple[str, tuple[str, ...]]:
    """Fingerprints the schema as SENT, and names the fields it let the model
    skip — ROADMAP items 59 and 102.

    Takes the built wire dict rather than the model, so it describes the
    bytes that actually went out. Rebuilding from the model here would
    fingerprint what a rebuild produces, which is the same thing right up
    until the moment it is not — and that moment is exactly when this field
    is being read.

    WHY NULLABILITY IS WORTH STORING AT ALL. Measured 2026-09-11 over the
    record's first 32 runs: every field that has ever gone missing from a
    forecast is nullable here, and the non-nullable ones have never missed
    once in 111 field-instances. That is not established as CAUSAL — the
    non-nullable fields are also the four a forecaster would always write,
    and nothing in the record separates the two explanations. The point of
    recording it is that the next change to this schema makes the record
    able to answer the question, which item 102 found it could not.

    Returns the hash and the nullable paths separately because they answer
    different questions: the hash says "did the schema move", the paths say
    "which fields could be skipped on this run". A reader chasing a vanished
    field wants the second without decoding the first.

    Paths are `/a/b` for object properties and `/a[]/b` for array items —
    `extended_properties` is an array, and a walk that followed only
    `properties` would report this schema as half its real size.
    """
    nullable: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return

        for key, value in (node.get("properties") or {}).items():
            child = f"{path}/{key}"
            if isinstance(value, dict) and value.get("nullable"):
                nullable.append(child)
            walk(value, child)

        if "items" in node:
            walk(node["items"], f"{path}[]")

    walk(wire, "")

    # sort_keys because the hash must track the schema's CONTENT, not the
    # order a dict happened to be built in. Without it an unrelated edit
    # that reorders a field reads as a schema change, and the field stops
    # being believed the first time that happens.
    digest = hashlib.sha256(
        json.dumps(wire, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return digest, tuple(sorted(nullable))


def _convert_node(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        ref_name = node["$ref"].rsplit("/", 1)[-1]
        return _convert_node(defs[ref_name], defs)

    if "anyOf" in node:
        branches = node["anyOf"]
        non_null = [b for b in branches if b.get("type") != "null"]
        nullable = len(non_null) != len(branches)
        if len(non_null) == 1:
            converted = _convert_node(non_null[0], defs)
            if nullable:
                converted["nullable"] = True
            return converted
        # Gemini has no true union type; fall back to STRING as a safe
        # default for the rare case of a genuinely multi-type field.
        return {"type": "STRING", "nullable": nullable}

    json_type = node.get("type")

    if json_type == "object":
        properties = {
            key: _convert_node(value, defs) for key, value in node.get("properties", {}).items()
        }
        result: dict[str, Any] = {"type": "OBJECT", "properties": properties}
        if node.get("required"):
            result["required"] = node["required"]
        return _with_description(result, node)

    if json_type == "array":
        items_node = node.get("items", {})
        return _with_description({"type": "ARRAY", "items": _convert_node(items_node, defs)}, node)

    if json_type in _JSON_TYPE_TO_GEMINI:
        return _with_description({"type": _JSON_TYPE_TO_GEMINI[json_type]}, node)

    # Fallback for anything unrecognized (shouldn't normally be reached).
    return {"type": "STRING"}


def _with_description(result: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    if node.get("description"):
        result["description"] = node["description"]
    return result


# ---------------------------------------------------------------------------
# pydantic -> OpenAI-compatible JSON-schema adapter
# ---------------------------------------------------------------------------


def to_strict_json_schema(model: type[BaseModel]) -> dict:
    """Converts a pydantic model's JSON schema into the dialect OpenAI's
    Structured Outputs (`response_format.json_schema`) expects — the same
    format used by every OpenAI-compatible endpoint (OpenRouter, Groq,
    Together, vLLM, Ollama).

    This is a SEPARATE adapter from to_gemini_schema, not a generalization
    of it, because the two dialects disagree in ways that can't be papered
    over: Gemini wants uppercase type names and a `nullable` flag, while
    OpenAI wants standard lowercase JSON Schema with null expressed as a
    type union. Per llm/provider.py's contract, each provider owns its own
    adapter.

    OpenAI's *strict* mode adds three rules beyond plain JSON Schema, all
    handled here:
      1. every object must set additionalProperties: false
      2. every object's `required` must list ALL its properties — optional
         fields are expressed as nullable instead of omitted from required
      3. `default` is not allowed, so it's stripped

    $defs/$ref are inlined. OpenAI itself does support them, but several
    compatible endpoints don't, and inlining costs nothing at this schema's
    size.
    """
    json_schema = model.model_json_schema()
    defs = json_schema.get("$defs", {})
    return _convert_openai_node(json_schema, defs)


def _convert_openai_node(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        ref_name = node["$ref"].rsplit("/", 1)[-1]
        return _convert_openai_node(defs[ref_name], defs)

    if "anyOf" in node:
        branches = node["anyOf"]
        non_null = [b for b in branches if b.get("type") != "null"]
        nullable = len(non_null) != len(branches)
        if len(non_null) == 1:
            converted = _convert_openai_node(non_null[0], defs)
            if nullable:
                converted["type"] = [converted.get("type", "string"), "null"]
            return _with_description(converted, node)
        # A genuinely multi-type union; strict mode can't express it
        # usefully, so fall back to a nullable string the same way the
        # Gemini adapter falls back to STRING.
        return {"type": ["string", "null"] if nullable else "string"}

    json_type = node.get("type")

    if json_type == "object":
        properties = {
            key: _convert_openai_node(value, defs) for key, value in node.get("properties", {}).items()
        }
        return _with_description(
            {
                "type": "object",
                "properties": properties,
                # Rule 2: ALL properties required, not just node["required"].
                "required": list(properties.keys()),
                "additionalProperties": False,
            },
            node,
        )

    if json_type == "array":
        return _with_description(
            {"type": "array", "items": _convert_openai_node(node.get("items", {}), defs)}, node
        )

    if json_type in _JSON_TYPE_TO_GEMINI:  # same set of scalar types, lowercase here
        return _with_description({"type": json_type}, node)

    return {"type": "string"}
