import pytest
import requests_mock

from openlocalweather.llm.anthropic import (
    ANTHROPIC_VERSION,
    MAX_ATTEMPTS,
    TOOL_NAME,
    AnthropicProvider,
)
from openlocalweather.llm.gemini import LLMResponseError
from openlocalweather.llm.schema import GeminiForecastResponse

MODEL = "claude-test-model"
URL = "https://api.anthropic.com/v1/messages"

VALID_INPUT = {
    "yesterday_verification": "Rain call was accurate.",
    "verification_notes": [{"lead_time_days": 0, "note": "Spot on."}],
    "skill_profile_summaries": [{"model": "gfs_seamless", "lead_time_days": 0, "summary": "Reliable."}],
    "today_properties": {
        "rain_expected": "Likely",
        "temp_high_c": 26.0,
        "temp_low_c": 18.0,
        "temp_high_low": "26°C / 79°F",
    },
    "today_narrative": "## Overview\nRain expected.",
    "whatsapp_summary": None,
}


def envelope(blocks, stop_reason="tool_use") -> dict:
    return {"type": "message", "content": blocks, "stop_reason": stop_reason}


def valid_envelope() -> dict:
    return envelope([{"type": "tool_use", "name": TOOL_NAME, "input": VALID_INPUT}])


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    import openlocalweather.llm.anthropic as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def provider(**overrides) -> AnthropicProvider:
    kwargs = dict(api_key="key", model=MODEL)
    kwargs.update(overrides)
    return AnthropicProvider(**kwargs)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_api_key():
    with pytest.raises(ValueError):
        AnthropicProvider(api_key="", model=MODEL)


def test_requires_model():
    with pytest.raises(ValueError):
        AnthropicProvider(api_key="key", model="")


def test_default_endpoint_is_anthropic_api():
    assert provider().endpoint == URL


def test_custom_base_url_for_a_gateway():
    p = provider(base_url="https://gateway.internal/anthropic/")
    assert p.endpoint == "https://gateway.internal/anthropic/v1/messages"


# ---------------------------------------------------------------------------
# Request shape — the parts that differ from every other provider
# ---------------------------------------------------------------------------


def test_successful_generate_returns_validated_response():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        result = provider().generate("system", "user", GeminiForecastResponse)
        assert isinstance(result, GeminiForecastResponse)
        assert result.today_properties.temp_high_c == 26.0


def test_uses_x_api_key_and_version_headers_not_bearer():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider().generate("system", "user", GeminiForecastResponse)
        headers = m.request_history[0].headers
        assert headers["x-api-key"] == "key"
        assert headers["anthropic-version"] == ANTHROPIC_VERSION
        assert "Authorization" not in headers


def test_system_prompt_is_top_level_not_a_message():
    """Anthropic rejects role: "system" inside messages — the system prompt
    has to be its own top-level field."""
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider().generate("SYS", "USR", GeminiForecastResponse)
        body = m.request_history[0].json()

        assert body["system"] == "SYS"
        assert body["messages"] == [{"role": "user", "content": "USR"}]
        assert all(msg["role"] != "system" for msg in body["messages"])


def test_forces_tool_use_with_the_schema_as_input_schema():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider().generate("system", "user", GeminiForecastResponse)
        body = m.request_history[0].json()

        assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
        assert len(body["tools"]) == 1
        tool = body["tools"][0]
        assert tool["name"] == TOOL_NAME
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "today_narrative" in schema["properties"]


def test_max_tokens_is_always_sent():
    """Required by the API — a missing max_tokens is a hard 400."""
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider(max_tokens=1234).generate("system", "user", GeminiForecastResponse)
        assert m.request_history[0].json()["max_tokens"] == 1234


def test_temperature_only_sent_when_set():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider().generate("system", "user", GeminiForecastResponse)
        assert "temperature" not in m.request_history[0].json()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_error_body_raises_with_message():
    with requests_mock.Mocker() as m:
        m.post(
            URL,
            status_code=401,
            json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
        )
        with pytest.raises(LLMResponseError, match="invalid x-api-key"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_truncated_response_names_max_tokens_explicitly():
    """A model that ran out of tokens mid-tool-call should say so — this is
    the most likely real failure once multiple audience voices land."""
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope([{"type": "text", "text": "partial..."}], stop_reason="max_tokens"))
        with pytest.raises(LLMResponseError, match="truncated at max_tokens"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_prose_only_response_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope([{"type": "text", "text": "I think it will rain."}]))
        with pytest.raises(LLMResponseError, match="no tool_use block"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_schema_validation_failure_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope([{"type": "tool_use", "name": TOOL_NAME, "input": {"partial": True}}]))
        with pytest.raises(LLMResponseError, match="schema validation"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_non_json_response_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, text="<html>502 Bad Gateway</html>")
        with pytest.raises(LLMResponseError):
            provider().generate("system", "user", GeminiForecastResponse)


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


def test_retries_529_overloaded():
    """529 overloaded_error is Anthropic-specific and genuinely transient —
    exactly the class of failure that cost this project a whole run before
    retries existed."""
    with requests_mock.Mocker() as m:
        m.post(URL, [{"status_code": 529, "json": {}}, {"status_code": 200, "json": valid_envelope()}])
        result = provider().generate("system", "user", GeminiForecastResponse)
        assert result.today_properties.rain_expected == "Likely"
        assert len(m.request_history) == 2


def test_does_not_retry_non_retryable_status():
    with requests_mock.Mocker() as m:
        m.post(URL, status_code=400, json={"type": "error", "error": {"message": "bad request"}})
        with pytest.raises(LLMResponseError):
            provider().generate("system", "user", GeminiForecastResponse)
        assert len(m.request_history) == 1


def test_gives_up_after_max_attempts():
    with requests_mock.Mocker() as m:
        m.post(URL, status_code=529, json={})
        with pytest.raises(LLMResponseError, match=f"after {MAX_ATTEMPTS} attempts"):
            provider().generate("system", "user", GeminiForecastResponse)
        assert len(m.request_history) == MAX_ATTEMPTS


def test_retry_after_header_is_honored(monkeypatch):
    slept: list[float] = []
    import openlocalweather.llm.anthropic as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))

    with requests_mock.Mocker() as m:
        m.post(
            URL,
            [
                {"status_code": 429, "json": {}, "headers": {"retry-after": "2"}},
                {"status_code": 200, "json": valid_envelope()},
            ],
        )
        provider().generate("system", "user", GeminiForecastResponse)

    assert slept == [2.0]
