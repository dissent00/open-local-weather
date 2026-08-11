"""GeminiForecastResponse: the single source of truth for what the LLM must
return, mirroring the original pipeline's Gemini `responseSchema` field for
field. Provider adapters (to_gemini_schema below; future Groq/Cerebras/
OpenRouter adapters alongside their own provider classes) convert this into
whatever dialect a given provider's structured-output feature expects — the
pipeline and prompt-building code only ever deal with this pydantic model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VerificationNote(BaseModel):
    lead_time_days: int
    note: str


class SkillProfileSummaryItem(BaseModel):
    model: str
    lead_time_days: int
    summary: str


class TodayProperties(BaseModel):
    """The LLM's synthesized, BLENDED call across all models — genuine
    reasoning, not any one model's raw number. Only rain_expected,
    temp_high_c, temp_low_c, and temp_high_low are required; the original
    schema's `required` list is preserved exactly."""

    rain_expected: str
    onset_window: str | None = None  # Day+0 only
    peak_wind_kmh: float | None = None  # secondary point, if configured
    temp_high_c: float
    temp_low_c: float
    temp_high_low: str
    mslp_trend_24h: str | None = None
    synoptic_pattern: str | None = None
    uv_index_max: str | None = None
    air_quality_aqi: str | None = None


class GeminiForecastResponse(BaseModel):
    yesterday_verification: str
    verification_notes: list[VerificationNote] = Field(default_factory=list)
    skill_profile_summaries: list[SkillProfileSummaryItem] = Field(default_factory=list)
    today_properties: TodayProperties
    today_narrative: str
    whatsapp_summary: str | None = None


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
