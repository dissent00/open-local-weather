import json

import pytest
import requests
import requests_mock

from openlocalweather.llm.gemini import GEMINI_API_URL_TEMPLATE, GeminiProvider, LLMResponseError
from openlocalweather.llm.schema import GeminiForecastResponse

MODEL = "gemini-test-model"
URL = GEMINI_API_URL_TEMPLATE.format(model=MODEL)

VALID_PAYLOAD = {
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


def gemini_envelope(payload: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """No test should ever actually sleep through the retry backoff —
    without this, the retryable-status tests below take ~35s each."""
    import openlocalweather.llm.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod.time, "sleep", lambda s: None)


def test_requires_api_key():
    with pytest.raises(ValueError):
        GeminiProvider(api_key="", model=MODEL)


def test_requires_model():
    with pytest.raises(ValueError):
        GeminiProvider(api_key="key", model="")


def test_successful_generate_returns_validated_response():
    with requests_mock.Mocker() as m:
        m.post(URL, json=gemini_envelope(VALID_PAYLOAD))
        provider = GeminiProvider(api_key="key", model=MODEL)
        result = provider.generate("system", "user", GeminiForecastResponse)
        assert isinstance(result, GeminiForecastResponse)
        assert result.today_properties.temp_high_c == 26.0
        assert result.yesterday_verification == "Rain call was accurate."


def test_request_includes_schema_and_api_key():
    with requests_mock.Mocker() as m:
        m.post(URL, json=gemini_envelope(VALID_PAYLOAD))
        provider = GeminiProvider(api_key="secret-key", model=MODEL)
        provider.generate("sys", "usr", GeminiForecastResponse)

        req = m.last_request
        assert req.qs["key"] == ["secret-key"]
        body = req.json()
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseSchema"]["type"] == "OBJECT"
        assert body["system_instruction"]["parts"][0]["text"] == "sys"
        assert body["contents"][0]["parts"][0]["text"] == "usr"


def test_http_error_status_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, status_code=500, json={"error": {"code": 500, "message": "boom"}})
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


def test_network_error_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, exc=requests.exceptions.ConnectionError("boom"))
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


def test_no_candidates_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json={"candidates": []})
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


def test_malformed_envelope_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json={"candidates": [{"content": {}}]})
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


def test_non_json_body_text_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, text="not json at all")
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


def test_candidate_text_not_valid_json_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json={"candidates": [{"content": {"parts": [{"text": "{not valid json"}]}}]})
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


def test_response_failing_schema_validation_raises():
    with requests_mock.Mocker() as m:
        bad_payload = {"yesterday_verification": "ok"}  # missing required today_properties/today_narrative
        m.post(URL, json=gemini_envelope(bad_payload))
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError):
            provider.generate("s", "u", GeminiForecastResponse)


# ---------------------------------------------------------------------------
# Transient-failure retry (regression: a real 503 aborted a live run)
# ---------------------------------------------------------------------------


def test_retries_transient_503_then_succeeds():
    import openlocalweather.llm.gemini as gemini_mod

    with requests_mock.Mocker() as m:
        m.post(URL, [
            {"status_code": 503, "json": {"error": {"code": 503, "message": "high demand"}}},
            {"status_code": 503, "json": {"error": {"code": 503, "message": "high demand"}}},
            {"json": gemini_envelope(VALID_PAYLOAD)},
        ])
        provider = GeminiProvider(api_key="key", model=MODEL)
        result = provider.generate("s", "u", GeminiForecastResponse)

    assert result.today_properties.temp_high_c == 26.0
    assert m.call_count == 3


def test_retries_exhausted_raises():
    import openlocalweather.llm.gemini as gemini_mod

    with requests_mock.Mocker() as m:
        m.post(URL, status_code=503, json={"error": {"code": 503, "message": "high demand"}})
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError, match="after 4 attempts"):
            provider.generate("s", "u", GeminiForecastResponse)
    assert m.call_count == gemini_mod.MAX_ATTEMPTS


def test_non_retryable_error_fails_fast_without_retrying():
    # identically on retry — burning quota and time for nothing.
    import openlocalweather.llm.gemini as gemini_mod

    with requests_mock.Mocker() as m:
        m.post(URL, status_code=404, json={"error": {"code": 404, "message": "model not found"}})
        provider = GeminiProvider(api_key="key", model=MODEL)
        with pytest.raises(LLMResponseError, match="404"):
            provider.generate("s", "u", GeminiForecastResponse)
    assert m.call_count == 1


def test_retries_on_network_error_then_succeeds():
    import openlocalweather.llm.gemini as gemini_mod

    with requests_mock.Mocker() as m:
        m.post(URL, [
            {"exc": requests.exceptions.ConnectionError("boom")},
            {"json": gemini_envelope(VALID_PAYLOAD)},
        ])
        provider = GeminiProvider(api_key="key", model=MODEL)
        result = provider.generate("s", "u", GeminiForecastResponse)
    assert result.yesterday_verification == "Rain call was accurate."
