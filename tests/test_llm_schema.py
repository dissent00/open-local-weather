import copy
import json

import pytest

from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    gemini_schema_facts,
    to_gemini_schema,
)


def test_top_level_is_object_with_required_fields():
    schema = to_gemini_schema(GeminiForecastResponse)
    assert schema["type"] == "OBJECT"
    assert set(schema["required"]) == {"yesterday_verification", "today_properties", "today_narrative"}


def test_simple_string_field():
    schema = to_gemini_schema(GeminiForecastResponse)
    assert schema["properties"]["yesterday_verification"] == {"type": "STRING"}


def test_optional_field_is_nullable_string():
    schema = to_gemini_schema(GeminiForecastResponse)
    prop = schema["properties"]["whatsapp_summary"]
    assert prop["type"] == "STRING"
    assert prop["nullable"] is True


def test_array_of_nested_objects_resolves_ref():
    schema = to_gemini_schema(GeminiForecastResponse)
    notes = schema["properties"]["verification_notes"]
    assert notes["type"] == "ARRAY"
    item = notes["items"]
    assert item["type"] == "OBJECT"
    assert item["properties"]["lead_time_days"] == {"type": "INTEGER"}
    assert item["properties"]["note"] == {"type": "STRING"}
    assert set(item["required"]) == {"lead_time_days", "note"}


def test_nested_object_with_required_and_optional_fields():
    schema = to_gemini_schema(GeminiForecastResponse)
    today_properties = schema["properties"]["today_properties"]
    assert today_properties["type"] == "OBJECT"
    assert set(today_properties["required"]) == {
        "rain_expected",
        "rain",
        "temp_high_c",
        "temp_low_c",
    }
    # temp_high_low is NOT asked of the model: it is computed from the two
    # numbers above by models.format_temp_high_low. A model that returns it
    # anyway is returning a field nothing reads.
    assert "temp_high_low" not in today_properties["properties"]
    # required numeric field
    assert today_properties["properties"]["temp_high_c"] == {"type": "NUMBER"}
    # optional field, nullable
    onset = today_properties["properties"]["onset_window"]
    assert onset["type"] == "STRING"
    assert onset["nullable"] is True


def test_no_unresolved_refs_anywhere_in_output():
    schema = to_gemini_schema(GeminiForecastResponse)

    def _walk(node):
        assert "$ref" not in node
        assert "anyOf" not in node
        if node.get("type") == "OBJECT":
            for v in node.get("properties", {}).values():
                _walk(v)
        if node.get("type") == "ARRAY":
            _walk(node["items"])

    _walk(schema)


def test_the_display_bound_is_enforced_here_and_never_sent_to_the_provider():
    """`MAX_DISPLAY_STRING` exists to catch a runaway generation on our side.
    It must not appear in either provider dialect: a constraint in the
    request is the provider's to enforce or ignore, and a model that trips it
    upstream would fail in a way this code cannot see or report.

    The schema vectors already pin both dialects byte-for-byte, so this
    would show up there too — but as an unexplained diff rather than as the
    rule it is.
    """
    from openlocalweather.llm.schema import (
        GeminiForecastResponse,
        to_gemini_schema,
        to_strict_json_schema,
    )

    for dialect in (to_gemini_schema(GeminiForecastResponse), to_strict_json_schema(GeminiForecastResponse)):
        rendered = json.dumps(dialect)
        assert "maxLength" not in rendered
        assert "max_length" not in rendered

    # And the bound really is live on this side.
    from pydantic import ValidationError
    from openlocalweather.llm.schema import MAX_DISPLAY_STRING, TodayProperties

    with pytest.raises(ValidationError):
        TodayProperties(
            rain_expected="Likely",
            rain=False,
            temp_high_c=26.0,
            temp_low_c=18.0,
            uv_index_max="x" * (MAX_DISPLAY_STRING + 1),
        )


# --- gemini_schema_facts — ROADMAP item 59/102 -------------------------------


def test_schema_facts_names_every_nullable_field_by_path():
    wire = to_gemini_schema(GeminiForecastResponse)
    _, nullable = gemini_schema_facts(wire)

    # The three fields that went quiet 2026-09-09..11 are all nullable, and
    # the four that never dropped are not. That pairing is the whole reason
    # this is recorded; see ROADMAP item 59's "what does NOT separate the six".
    assert "/today_properties/mslp_trend_24h" in nullable
    assert "/today_properties/peak_wind_kmh" in nullable
    assert "/today_properties/air_quality_aqi" in nullable
    assert "/today_properties/rain_expected" not in nullable
    assert "/today_properties/temp_high_c" not in nullable


def test_schema_facts_descends_into_array_items():
    """`extended_properties` is an ARRAY, and a walk that only followed
    `properties` would report it as having no fields at all — silently
    recording a schema half its real size.
    """
    wire = to_gemini_schema(GeminiForecastResponse)
    _, nullable = gemini_schema_facts(wire)

    assert any(p.startswith("/extended_properties[]/") for p in nullable), nullable


def test_schema_facts_hash_moves_when_nullability_moves():
    """The fingerprint has to notice the one change it exists to detect:
    flipping a field non-nullable. A hash over the model NAME, or over a
    dict whose key order wanders, would not.
    """
    wire = to_gemini_schema(GeminiForecastResponse)
    before, _ = gemini_schema_facts(wire)

    flipped = copy.deepcopy(wire)
    del flipped["properties"]["today_properties"]["properties"]["mslp_trend_24h"]["nullable"]
    after, after_nullable = gemini_schema_facts(flipped)

    assert before != after
    assert "/today_properties/mslp_trend_24h" not in after_nullable


def test_schema_facts_hash_is_stable_across_rebuilds():
    """Two builds of the same model must fingerprint identically, or every
    run looks like a schema change and the field is worthless.
    """
    a, _ = gemini_schema_facts(to_gemini_schema(GeminiForecastResponse))
    b, _ = gemini_schema_facts(to_gemini_schema(GeminiForecastResponse))

    assert a == b
