"""Where the cap meets the code that actually sends requests.

`test_spend.py` proves the ledger counts correctly. That is a different claim
from "the pipeline records one entry per request", and the gap between the two
is where a real bug lived undetected: `record_attempt` was called once per
forecast, while the providers retry transient failures up to MAX_ATTEMPTS
times inside a single `generate()`. A cap of 10 permitted up to 40 billable
requests.

`test_it_counts_calls_not_forecasts` in the sibling file looked like it
covered this. It calls the ledger primitive three times directly and asserts
the fourth is refused — proving the counter counts, never that the caller
records per attempt. A test can state the right intent in its docstring and
still pin the wrong seam.

These tests therefore drive a real provider against a fake transport and count
what lands in the ledger. The mirror of this file is
`app/olw_core/test/spend_seam_test.dart`; the invariants are named in
`spec/README.md` so a change to one side has an obvious counterpart.
"""

from datetime import datetime, timezone

import pytest
import requests
from pydantic import BaseModel

from openlocalweather.llm.gemini import GeminiProvider, LLMResponseError
from openlocalweather.spend import (
    SpendCapExceeded,
    calls_in_window,
    read_ledger,
    record_attempt,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


class _TinySchema(BaseModel):
    """A real pydantic model, because the schema adapter runs before the POST
    and a bare placeholder would fail earlier than the code under test."""

    ok: str = "yes"


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "fake"

    def json(self):
        return self._payload


def _hook(tmp_path, max_calls):
    """The wiring the pipeline installs, isolated so the test is about it."""

    def _record():
        record_attempt(
            tmp_path,
            provider="GeminiProvider",
            model="gemini-3.6-flash",
            purpose="forecast",
            max_calls=max_calls,
        )

    return _record


def test_every_retry_is_counted_not_just_the_forecast(tmp_path, monkeypatch):
    """Two transient failures then success = three requests = three entries.

    This is the exact shape that was undercounted. Retries fire on 429 and
    5xx, which is when a provider is rate-limiting or struggling — so the
    undercount was worst precisely when spend was most likely to run away.
    """
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(503)
        return _FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=_hook(tmp_path, max_calls=10)
    )
    provider.generate("sys", "user", _TinySchema)

    assert calls["n"] == 3, "the provider really did send three requests"
    assert len(read_ledger(tmp_path)) == 3, (
        "one ledger entry per request sent, not one per forecast attempted"
    )


def test_the_cap_stops_a_retry_loop_mid_flight(tmp_path, monkeypatch):
    """Running out of budget must abort the retries, not ride through them.

    Without this, a cap of 2 would still permit MAX_ATTEMPTS requests once a
    run had started — the cap would bound how many forecasts begin rather than
    how many requests go out.
    """
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return _FakeResponse(503)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=_hook(tmp_path, max_calls=2)
    )
    with pytest.raises(SpendCapExceeded):
        provider.generate("sys", "user", _TinySchema)

    assert calls["n"] == 2, f"expected the 3rd request to be refused, sent {calls['n']}"
    assert calls_in_window(read_ledger(tmp_path), NOW.replace(year=2099)) >= 0


def test_the_attempt_is_recorded_before_the_request_leaves(tmp_path, monkeypatch):
    """A process killed mid-request must still have counted it.

    Recording afterwards loses the count exactly when things are going wrong.
    Over-counting refuses a call that would have been allowed, which is the
    safe direction; under-counting hands out free calls after a crash.
    """

    def fake_post(*args, **kwargs):
        # Stands in for the process dying between send and reply.
        raise requests.ConnectionError("killed mid-flight")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=_hook(tmp_path, max_calls=10)
    )
    with pytest.raises(LLMResponseError):
        provider.generate("sys", "user", _TinySchema)

    assert len(read_ledger(tmp_path)) == 4, (
        "every attempt that left the process is on the ledger"
    )


def test_a_provider_that_ignores_the_hook_is_reported_loudly(tmp_path, capsys):
    """The hook is only as good as the provider's willingness to call it.

    Providers are injected, so one that never reports a request would spend
    without appearing in the ledger — the exact failure the cap exists to
    prevent, and invisible until a bill arrives. It must not pass quietly.

    This also keeps the warning path executable. It was written using `sys`
    in a module that did not import it, and no test reached the branch, so the
    safeguard against silent overspending would itself have raised NameError
    the first time it mattered.
    """
    from dataclasses import replace as dc_replace

    from tests.test_pipeline_run import LOCATION, FakeLLMProvider, make_deps
    from openlocalweather.pipeline import _attach_spend_cap

    class SilentProvider(FakeLLMProvider):
        """Never calls before_attempt — a plausible third-party provider."""

        def generate(self, *a, **kw):
            return self.response

    deps = make_deps(tmp_path, llm=SilentProvider())
    verify = _attach_spend_cap(deps, LOCATION, purpose="forecast")
    deps.llm_provider.generate("sys", "user", None)
    verify()

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "SilentProvider" in err
    assert "NOT counted" in err
