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
