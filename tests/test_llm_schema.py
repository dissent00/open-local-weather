from openlocalweather.llm.schema import GeminiForecastResponse, to_gemini_schema


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
    assert set(today_properties["required"]) == {"rain_expected", "temp_high_c", "temp_low_c"}
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
