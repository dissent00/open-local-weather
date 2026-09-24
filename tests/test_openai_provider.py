import json
import time

import pytest
import requests_mock

from openlocalweather.llm.errors import LLMResponseError, LLMUnavailableError
from openlocalweather.llm.openai_compat import (
    MAX_ATTEMPTS,
    PROVIDER_FAILURE_MAX_ATTEMPTS,
    OpenAICompatProvider,
)
from openlocalweather.llm.schema import GeminiForecastResponse

MODEL = "test-model"
BASE_URL = "https://example-llm.test/v1"
URL = f"{BASE_URL}/chat/completions"

VALID_PAYLOAD = {
    "yesterday_verification": "Rain call was accurate.",
    "verification_notes": [{"lead_time_days": 0, "note": "Spot on."}],
    "skill_profile_summaries": [{"model": "gfs_seamless", "lead_time_days": 0, "summary": "Reliable."}],
    "today_properties": {
        "rain_expected": "Likely", "rain": False,
        "temp_high_c": 26.0,
        "temp_low_c": 18.0,
        "temp_high_low": "26°C / 79°F",
    },
    "today_narrative": "## Overview\nRain expected.",
    "whatsapp_summary": None,
}


def envelope(content, finish_reason="stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def valid_envelope() -> dict:
    return envelope(json.dumps(VALID_PAYLOAD))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retry backoff must never actually sleep in tests — without this the
    retryable-status cases below take ~35s each."""
    import openlocalweather.llm.openai_compat as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def provider(**overrides) -> OpenAICompatProvider:
    kwargs = dict(api_key="key", model=MODEL, base_url=BASE_URL)
    kwargs.update(overrides)
    return OpenAICompatProvider(**kwargs)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_model():
    with pytest.raises(ValueError):
        OpenAICompatProvider(api_key="key", model="", base_url=BASE_URL)


def test_requires_base_url():
    with pytest.raises(ValueError):
        OpenAICompatProvider(api_key="key", model=MODEL, base_url="")


def test_rejects_unknown_json_mode():
    with pytest.raises(ValueError):
        OpenAICompatProvider(api_key="key", model=MODEL, base_url=BASE_URL, json_mode="yaml")


def test_allows_empty_api_key_for_local_runtimes():
    """Ollama/LM Studio need no key — refusing to construct without one
    would block the fully-free local-model path this provider exists for."""
    p = OpenAICompatProvider(api_key="", model=MODEL, base_url="http://localhost:11434/v1")
    assert p.endpoint == "http://localhost:11434/v1/chat/completions"


def test_trailing_slash_in_base_url_does_not_double_up():
    assert provider(base_url=BASE_URL + "/").endpoint == URL


# ---------------------------------------------------------------------------
# Successful generation
# ---------------------------------------------------------------------------


def test_successful_generate_returns_validated_response():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        result = provider().generate("system", "user", GeminiForecastResponse)
        assert isinstance(result, GeminiForecastResponse)
        assert result.today_properties.temp_high_c == 26.0
        assert result.yesterday_verification == "Rain call was accurate."


def test_request_shape_includes_strict_schema_model_and_auth():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider().generate("SYS", "USR", GeminiForecastResponse)
        req = m.request_history[0]
        body = req.json()

        assert req.headers["Authorization"] == "Bearer key"
        assert body["model"] == MODEL
        assert body["messages"] == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ]
        rf = body["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"]["additionalProperties"] is False
        # Strict mode requires every property listed as required.
        schema = rf["json_schema"]["schema"]
        assert set(schema["required"]) == set(schema["properties"].keys())


def test_no_auth_header_when_api_key_is_empty():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider(api_key="").generate("system", "user", GeminiForecastResponse)
        assert "Authorization" not in m.request_history[0].headers


def test_temperature_only_sent_when_set():
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider().generate("system", "user", GeminiForecastResponse)
        assert "temperature" not in m.request_history[0].json()

    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider(temperature=0.2).generate("system", "user", GeminiForecastResponse)
        assert m.request_history[0].json()["temperature"] == 0.2


def test_json_object_mode_uses_simpler_format_and_injects_schema_into_prompt():
    """Endpoints without json_schema support only guarantee valid JSON, so
    the shape has to travel in the prompt instead."""
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        provider(json_mode="json_object").generate("SYS", "user", GeminiForecastResponse)
        body = m.request_history[0].json()

        assert body["response_format"] == {"type": "json_object"}
        system_content = body["messages"][0]["content"]
        assert system_content.startswith("SYS")
        assert "JSON Schema" in system_content
        assert "today_narrative" in system_content


def test_strips_markdown_code_fence_around_json():
    """Smaller/local models wrap JSON in ```json fences despite being told
    not to — common enough to handle rather than fail a whole run over."""
    fenced = "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```"
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope(fenced))
        result = provider(json_mode="json_object").generate("system", "user", GeminiForecastResponse)
        assert result.today_properties.rain_expected == "Likely"


# ---------------------------------------------------------------------------
# Failure paths — every one must raise, never return a partial forecast
# ---------------------------------------------------------------------------


def test_non_json_http_response_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, text="<html>gateway error</html>")
        with pytest.raises(LLMResponseError):
            provider().generate("system", "user", GeminiForecastResponse)


def test_error_body_raises_with_message():
    with requests_mock.Mocker() as m:
        m.post(URL, status_code=401, json={"error": {"message": "Invalid API key"}})
        with pytest.raises(LLMResponseError, match="Invalid API key"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_no_choices_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json={"choices": []})
        with pytest.raises(LLMResponseError, match="no choices"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_null_content_reports_finish_reason():
    """A length/filter cutoff should say so, not fail with an opaque
    JSON-parse error on None."""
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope(None, finish_reason="length"))
        with pytest.raises(LLMResponseError, match="length"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_non_json_content_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope("I'm afraid I can't do that."))
        with pytest.raises(LLMResponseError, match="not valid JSON"):
            provider().generate("system", "user", GeminiForecastResponse)


def test_schema_validation_failure_raises():
    with requests_mock.Mocker() as m:
        m.post(URL, json=envelope(json.dumps({"yesterday_verification": "only this field"})))
        with pytest.raises(LLMResponseError, match="schema validation"):
            provider().generate("system", "user", GeminiForecastResponse)


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


def test_retries_transient_status_then_succeeds():
    with requests_mock.Mocker() as m:
        m.post(URL, [{"status_code": 503, "json": {}}, {"status_code": 200, "json": valid_envelope()}])
        result = provider().generate("system", "user", GeminiForecastResponse)
        assert result.today_properties.temp_high_c == 26.0
        assert len(m.request_history) == 2


def test_does_not_retry_non_retryable_status():
    """A bad key or unknown model fails identically every time — retrying
    just burns time and quota."""
    with requests_mock.Mocker() as m:
        m.post(URL, status_code=400, json={"error": {"message": "unknown model"}})
        with pytest.raises(LLMResponseError):
            provider().generate("system", "user", GeminiForecastResponse)
        assert len(m.request_history) == 1


def test_gives_up_after_max_attempts():
    with requests_mock.Mocker() as m:
        m.post(URL, status_code=429, json={})
        with pytest.raises(LLMResponseError, match=f"after {MAX_ATTEMPTS} attempts"):
            provider().generate("system", "user", GeminiForecastResponse)
        assert len(m.request_history) == MAX_ATTEMPTS


def test_retry_after_header_is_honored(monkeypatch):
    slept: list[float] = []
    import openlocalweather.llm.openai_compat as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))

    with requests_mock.Mocker() as m:
        m.post(
            URL,
            [
                {"status_code": 429, "json": {}, "headers": {"Retry-After": "3"}},
                {"status_code": 200, "json": valid_envelope()},
            ],
        )
        provider().generate("system", "user", GeminiForecastResponse)

    assert slept == [3.0], "should wait exactly as long as the endpoint asked"


def test_implausibly_long_retry_after_is_ignored(monkeypatch):
    """A ten-minute Retry-After is better handled by failing and letting the
    next scheduled run pick it up than by blocking a CI job that long.

    The margin narrowed on 2026-09-14. This ceiling was 120s when the test was
    written, so 600s was five times anything we would impose; the schedule now
    ends at 420s, so a ten-minute header is under half again as long. The rule
    is unchanged — never sleep longer on a provider's say-so than on our own
    guess — but it is no longer obviously true by inspection, and the next
    widening should re-read this rather than assume the gap is still wide."""
    slept: list[float] = []
    import openlocalweather.llm.openai_compat as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))

    with requests_mock.Mocker() as m:
        m.post(
            URL,
            [
                {"status_code": 429, "json": {}, "headers": {"Retry-After": "600"}},
                {"status_code": 200, "json": valid_envelope()},
            ],
        )
        provider().generate("system", "user", GeminiForecastResponse)

    assert slept == [mod.RETRY_DELAYS_S[0]], "should fall back to the normal backoff, not wait 600s"


def test_the_retry_after_ceiling_tracks_our_own_longest_wait():
    """Never sleep longer on a provider's say-so than we would sleep on our
    own guess.

    The two numbers were independent until 2026-09-04, when the backoff was
    widened from 5/10/20 to 30/60/120 and the 60s ceiling was left behind.
    That combination refuses an authoritative 90s Retry-After and then waits
    120s on a guess instead — strictly worse, and invisible, because both
    halves look reasonable read on their own.

    Pinned in both providers that honor the header at all.
    """
    import openlocalweather.llm.anthropic as anthropic_mod
    import openlocalweather.llm.openai_compat as openai_mod

    for mod in (openai_mod, anthropic_mod):
        longest_self_imposed = max(mod.RETRY_DELAYS_S)
        assert mod.RETRY_AFTER_MAX_S == longest_self_imposed, mod.__name__


# ---------------------------------------------------------------------------
# A provider that fails is not a model that answered badly — ROADMAP item 178
# ---------------------------------------------------------------------------


def test_finish_reason_error_is_unavailable_not_a_bad_answer():
    """An empty body with finish_reason "error" is the PROVIDER failing.

    Measured 2026-09-23 and again 2026-09-24: `nex-agi/nex-n2.5-pro:free` held
    the connection 1802.9s and 1801.8s — 1.2 seconds apart — then returned
    HTTP 200 with a null content and this reason. Both times the narrative was
    abandoned without a second attempt, because empty content raised
    `LLMResponseError`, which is deliberately neither retried nor fallen back.

    That rule is right for "length" and "content_filter", where a second
    attempt buys the same answer. It is wrong here: the same model answered
    successfully at 624s and 1625s on the days either side, so a retry had a
    real chance and was never taken.
    """
    with requests_mock.Mocker() as m:
        m.post(f"{BASE_URL}/chat/completions", json=envelope(None, finish_reason="error"))
        with pytest.raises(LLMUnavailableError):
            provider().generate("sys", "user", GeminiForecastResponse)
        # TWO, not four. The failing call takes ~1800s, so four attempts is
        # two hours for one narrative — see PROVIDER_FAILURE_MAX_ATTEMPTS.
        assert m.call_count == PROVIDER_FAILURE_MAX_ATTEMPTS


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_a_model_that_stopped_itself_is_still_not_retried(reason):
    """The other half of the rule, unchanged. These say the model produced
    what it was going to produce, and asking again pays twice for it."""
    with requests_mock.Mocker() as m:
        m.post(f"{BASE_URL}/chat/completions", json=envelope(None, finish_reason=reason))
        with pytest.raises(LLMResponseError):
            provider().generate("sys", "user", GeminiForecastResponse)
        assert m.call_count == 1, "a self-stopped model must not be retried"


def test_a_call_that_outlives_the_deadline_is_abandoned_and_retried_once(monkeypatch):
    """ROADMAP item 178's second half. REQUEST_TIMEOUT_S is 120 and a call
    still ran 1801.8s under it: `requests` applies it BETWEEN reads, so a
    provider that keeps the connection alive can hold it indefinitely.
    RESPONSE_DEADLINE_S bounds the whole exchange, and overrunning it is the
    provider failing — retried once, like an empty body, then given up."""
    import openlocalweather.llm.openai_compat as mod

    monkeypatch.setattr(mod, "RESPONSE_DEADLINE_S", -1)  # every read is late
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        with pytest.raises(LLMUnavailableError):
            provider().generate("sys", "user", GeminiForecastResponse)
        assert m.call_count == PROVIDER_FAILURE_MAX_ATTEMPTS


def test_the_deadline_is_recorded_as_its_own_outcome(monkeypatch):
    """So the ceiling can be MONITORED rather than guessed at again. The
    operator set 1700s on four samples, 2026-09-24, to be revisited: a
    distinct outcome in the ledger is how anyone will know how often it bites,
    and which calls it cost. Folding it into "timeout" would hide it among
    read timeouts, which are a different failure."""
    import openlocalweather.llm.openai_compat as mod

    monkeypatch.setattr(mod, "RESPONSE_DEADLINE_S", -1)
    seen: list[str] = []
    p = provider()
    p.after_attempt = lambda outcome, elapsed: seen.append(outcome)
    with requests_mock.Mocker() as m:
        m.post(URL, json=valid_envelope())
        with pytest.raises(LLMUnavailableError):
            p.generate("sys", "user", GeminiForecastResponse)

    assert seen == ["deadline"] * PROVIDER_FAILURE_MAX_ATTEMPTS


def test_the_deadline_is_enforced_on_a_real_socket_not_only_detected(monkeypatch):
    """A REAL SOCKET, because the mocks cannot see this. `requests_mock`
    hands the body over at once, so every test above passes whether the
    deadline aborts mid-stream or only notices once the body is complete.

    Measured 2026-09-24 against a local server sending headers at once and
    then a byte a second: with `chunk_size=65536` a 4s deadline did not abort
    until the whole body arrived at 10s. The overrun was DETECTED and never
    ENFORCED, which on production numbers is the difference between giving up
    at 1700s and waiting out the provider's own ~1800s. Scaled down here to
    run in about a second.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import openlocalweather.llm.openai_compat as mod

    trickle_s = 3.0

    class Trickle(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(valid_envelope()).encode()
            pad = int(trickle_s / 0.1)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(pad + len(body)))
            self.end_headers()
            try:
                for _ in range(pad):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    # NOT time.sleep: this file's autouse fixture patches it,
                    # and `time` is one module, so the server would stop
                    # trickling and the test would pass by delivering the
                    # whole body before the deadline could matter.
                    threading.Event().wait(0.1)
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client hung up on us: the behaviour under test

    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(mod, "RESPONSE_DEADLINE_S", 0.4)
        monkeypatch.setattr(mod, "REQUEST_TIMEOUT_S", 2)
        monkeypatch.setattr(mod, "_retry_delay", lambda attempt: 0)

        rows: list[tuple[str, float]] = []
        p = provider(base_url=f"http://127.0.0.1:{server.server_port}/v1")
        p.after_attempt = lambda outcome, elapsed: rows.append((outcome, elapsed))
        with pytest.raises(LLMUnavailableError):
            p.generate("sys", "user", GeminiForecastResponse)
    finally:
        server.shutdown()

    assert [outcome for outcome, _ in rows] == ["deadline"] * PROVIDER_FAILURE_MAX_ATTEMPTS
    # Aborted near the deadline, not after the 3s body: enforced, not detected.
    assert all(elapsed < 1.5 for _, elapsed in rows), rows
