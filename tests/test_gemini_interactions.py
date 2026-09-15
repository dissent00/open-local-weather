"""The Interactions provider — ROADMAP items 80, 132.

Every shape asserted here was MEASURED from a real interaction on 2026-09-15
with `tools/probe_background_submit.py`, not read off documentation: the docs
are SDK-first and do not say where generated text lands.
"""

import pytest
import requests
import requests_mock
from pydantic import BaseModel

from openlocalweather.llm.errors import LLMResponseError
from openlocalweather.llm.gemini_interactions import (
    INTERACTION_URL,
    INTERACTIONS_URL,
    GeminiInteractionsProvider,
)


class Answer(BaseModel):
    verdict: str


def completed(text: str = '{"verdict": "dry"}', status: str = "completed") -> dict:
    """A completed interaction, shaped as the real one was."""
    return {
        "id": "v1_abc",
        "status": status,
        "object": "interaction",
        "model": "gemini-3.6-flash",
        "usage": {"total_input_tokens": 35631, "total_output_tokens": 1024},
        "steps": [
            {"type": "user_input", "content": [{"text": "the prompt", "type": "text"}]},
            {"type": "thought", "signature": "x" * 11292},
            {"type": "model_output", "content": [{"text": text, "type": "text"}]},
        ],
    }


def provider(**kw):
    return GeminiInteractionsProvider(api_key="k", model="gemini-3.6-flash", **kw)


def test_a_submit_that_is_already_terminal_spends_no_poll():
    """The API may answer immediately, and paying a request to re-read what we
    were just handed is waste — against a ceiling of 20 a day."""
    polls = []
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed())
        got = provider(on_poll=lambda: polls.append(1)).generate("sys", "user", Answer)

    assert got.verdict == "dry"
    assert polls == [], "a terminal submit must not be polled"


def test_the_answer_comes_from_the_model_output_step(monkeypatch):
    """Measured shape: the echoed input, a thought holding an opaque
    signature, then the model_output. A thought appended to a forecast would
    be eleven thousand characters of noise that still parses as a string."""
    monkeypatch.setattr("openlocalweather.llm.gemini_interactions.time.sleep", lambda s: None)
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json={"id": "v1_abc", "status": "in_progress"})
        m.get(INTERACTION_URL.format(id="v1_abc"), json=completed())
        got = provider().generate("sys", "user", Answer)

    assert got.verdict == "dry"


def test_an_incomplete_interaction_is_refused_before_its_content_is_read(monkeypatch):
    """THE STOP-REASON GUARD, moved rather than dropped.

    gemini.py refuses a candidate whose finishReason is not STOP because on
    2026-09-10 a 15,930-character UV index was published, rendered and mailed:
    it parsed, and it validated. There is no finish_reason on a step here, so
    the interaction STATUS carries it — `incomplete` is where a run into the
    token ceiling lands.
    """
    monkeypatch.setattr("openlocalweather.llm.gemini_interactions.time.sleep", lambda s: None)
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json={"id": "v1_abc", "status": "in_progress"})
        # Valid JSON that would validate cleanly if anything read it.
        m.get(INTERACTION_URL.format(id="v1_abc"), json=completed(status="incomplete"))
        with pytest.raises(LLMResponseError, match="incomplete"):
            provider().generate("sys", "user", Answer)


def test_tokens_reach_the_response_hook(monkeypatch):
    """Item 80's accounting has to survive the move, or the record loses the
    instrument that diagnosed the whole 503 episode."""
    monkeypatch.setattr("openlocalweather.llm.gemini_interactions.time.sleep", lambda s: None)
    seen = []
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed())
        provider(after_response=seen.append).generate("sys", "user", Answer)

    assert seen[0].input_tokens == 35631
    assert seen[0].output_tokens == 1024


def test_polls_are_reported_separately_from_attempts(monkeypatch):
    """Polls count against the provider's daily limit but are NOT retries, so
    they must never consume MAX_ATTEMPTS. Item 80 raised this; item 81 settled
    it; separate hooks are what keep the two from being conflated."""
    monkeypatch.setattr("openlocalweather.llm.gemini_interactions.time.sleep", lambda s: None)
    attempts, polls = [], []
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json={"id": "v1_abc", "status": "in_progress"})
        m.get(INTERACTION_URL.format(id="v1_abc"), [
            {"json": {"id": "v1_abc", "status": "in_progress"}},
            {"json": completed()},
        ])
        provider(before_attempt=lambda: attempts.append(1),
                 on_poll=lambda: polls.append(1)).generate("sys", "user", Answer)

    assert attempts == [1], "one submit, one attempt"
    assert len(polls) == 2, "both polls reported, neither counted as an attempt"


def test_a_failed_poll_does_not_abandon_a_paid_generation(monkeypatch):
    """The work may well be running; only the question about it failed."""
    monkeypatch.setattr("openlocalweather.llm.gemini_interactions.time.sleep", lambda s: None)
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json={"id": "v1_abc", "status": "in_progress"})
        m.get(INTERACTION_URL.format(id="v1_abc"), [
            {"exc": requests.exceptions.ConnectTimeout},
            {"json": completed()},
        ])
        assert provider().generate("sys", "user", Answer).verdict == "dry"


def test_a_completed_interaction_with_no_output_names_what_it_found(monkeypatch):
    monkeypatch.setattr("openlocalweather.llm.gemini_interactions.time.sleep", lambda s: None)
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json={
            "id": "v1_abc", "status": "completed",
            "steps": [{"type": "user_input", "content": []}, {"type": "thought"}],
        })
        with pytest.raises(LLMResponseError, match="user_input.*thought"):
            provider().generate("sys", "user", Answer)


def test_an_error_returned_as_a_LIST_is_still_reported():
    """MEASURED 2026-09-15, and it cost a request to find.

    A rejected submit came back as `[{"error": {...}}]` — Google wraps some
    errors as a list — and code that went straight to `body.get("error")`
    raised AttributeError instead of reporting the message. The request was
    spent and its reason was lost, which is the worst outcome available: a
    failure that costs and teaches nothing.
    """
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, status_code=400, json=[
            {"error": {"code": 400, "message": "Unknown name response_format",
                       "status": "INVALID_ARGUMENT"}}
        ])
        with pytest.raises(LLMResponseError, match="Unknown name response_format"):
            provider().generate("sys", "user", Answer)


def test_the_raw_body_is_carried_even_when_the_shape_is_unrecognised():
    """A message Google wrote and a body nobody expected are different
    evidence, and whoever debugs this next wants both."""
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, status_code=400, json=["something unforeseen"])
        with pytest.raises(LLMResponseError, match="something unforeseen"):
            provider().generate("sys", "user", Answer)


def test_response_format_is_the_schema_itself_not_a_wrapper():
    """MEASURED 2026-09-15, after guessing an OpenAI envelope and being told:

        The value 'json_schema' is not supported for 'type' at
        'response_format'. Supported values: ... 'object', 'integer', ...

    So `response_format` IS a JSON Schema and its `type` is a schema type.
    Asserted on the sent payload, because this is the one field whose shape
    cost a request to learn and nothing else would notice it regressing.
    """
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed())
        provider().generate("sys", "user", Answer)
        sent = m.request_history[0].json()

    assert sent["response_format"]["type"] == "object", "a schema, not a wrapper"
    assert "json_schema" not in sent["response_format"]
    assert "verdict" in sent["response_format"]["properties"]
    # Lowercase throughout: to_gemini_schema emits OBJECT/ARRAY and would be
    # refused by the same endpoint for a second reason.
    assert sent["response_format"]["properties"]["verdict"]["type"] == "string"


# --- Direct answers are the default (MEASURED 2026-09-15) -----------------


def test_background_is_not_sent_by_default():
    """THE REQUEST ARITHMETIC IS THE POINT, not a stylistic preference.

    Backgrounded, one call is a submit plus N polls — the first live run spent
    roughly 3 requests where a direct call spends 1. Two issuances of two
    calls is ~12 a day against a limit of 20, versus ~4 direct. This endpoint
    was being evaluated to solve the daily-limit problem, so a default that
    trebles the request count would have made it worse while looking like a
    fix.

    Measured, not assumed: asked WITHOUT the flag the endpoint returned HTTP
    200 in 19.982s with status "completed" and the model_output inline.
    """
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed())
        provider().generate("sys", "user", Answer)
        sent = m.request_history[0].json()

    assert "background" not in sent, "omitted, not sent as false — untested combination"


def test_background_is_sent_only_when_asked_for():
    """Kept rather than deleted: it is the right shape for a job slower than a
    client wants to hold a connection for."""
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed(status="queued"))
        m.get(INTERACTION_URL.format(id="v1_abc"), json=completed())
        provider(background=True).generate("sys", "user", Answer)
        sent = m.request_history[0].json()

    assert sent["background"] is True


def test_the_default_path_makes_exactly_one_request():
    """The claim in one assertion. A direct call is one HTTP request, so the
    ledger row and the provider's spend agree without a poll to reconcile."""
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed())
        provider().generate("sys", "user", Answer)

    assert len(m.request_history) == 1


# --- What the record keeps, when production switches endpoints ------------


def test_thinking_effort_is_recorded():
    """THE SECOND HALF OF A TWO-PART CHANGE, and the only visible half.

    Switching production here also drops `thinkingLevel: "high"`, because this
    endpoint is sent no equivalent. Without this number the record shows the
    endpoint moving and not the thinking, and any later accuracy movement is
    attributable to either one forever.
    """
    seen = []
    body = completed()
    body["usage"]["total_thought_tokens"] = 4096
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=body)
        provider(after_response=seen.append).generate("sys", "user", Answer)

    assert seen[0].thought_tokens == 4096


def test_the_schema_facts_survive_the_switch():
    """Item 102's instrument must not go dark on the run that changes the
    endpoint. A gap here would read as "the schema stopped being recorded"
    when the truth is "the provider forgot" — and the schema did not change."""
    seen = []
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed())
        provider(after_response=seen.append).generate("sys", "user", Answer)

    assert seen[0].response_schema_sha256, "the schema fingerprint is recorded"
    assert seen[0].nullable_fields is not None, "() means none, None means not said"


def test_a_response_that_fails_the_schema_records_nothing():
    """Fires AFTER validation, like generateContent. It fired before, which
    was a divergence rather than a choice: a failed response produced no
    forecast, and recording meta for it on one path and not the other would
    put a seam in the record exactly where the endpoint changed."""
    seen = []
    with requests_mock.Mocker() as m:
        m.post(INTERACTIONS_URL, json=completed(text='{"wrong_field": 1}'))
        with pytest.raises(LLMResponseError):
            provider(after_response=seen.append).generate("sys", "user", Answer)

    assert seen == [], "no forecast, no row"
