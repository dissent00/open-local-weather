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
    complete_attempt,
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
    # Imported WITHOUT a package prefix. "from tests.test_pipeline_run import"
    # works under `python -m pytest`, which puts the working directory on
    # sys.path, and fails under plain `pytest`, which does not — so it passed
    # locally and broke CI for ten consecutive pushes without either being
    # obviously wrong. There is no tests/__init__.py, so pytest puts this
    # directory on sys.path itself and the bare name resolves under both.
    from test_pipeline_run import LOCATION, FakeLLMProvider, make_deps

    from openlocalweather.pipeline import attach_spend_cap

    class SilentProvider(FakeLLMProvider):
        """Never calls before_attempt — a plausible third-party provider."""

        def generate(self, *a, **kw):
            return self.response

    deps = make_deps(tmp_path, llm=SilentProvider())
    verify, _ = attach_spend_cap(deps, LOCATION, purpose="forecast")
    deps.llm_provider.generate("sys", "user", None)
    verify()

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "SilentProvider" in err
    assert "NOT counted" in err


# --- The other half of the seam: what the attempt did ------------------------
#
# `before_attempt` proves a request left. These prove the provider comes back
# and says what happened to it, which is the measurement roadmap item 80 was
# approved for: outcome and elapsed time per attempt, so the next latency
# question is a query rather than an inference.


def _paired_hooks(tmp_path, max_calls=10):
    """Both halves of the wiring the pipeline installs, sharing one row."""
    pending = {"at": None}

    def _record():
        pending["at"] = None
        at = datetime.now(timezone.utc)
        record_attempt(
            tmp_path,
            provider="GeminiProvider",
            model="gemini-3.6-flash",
            purpose="forecast",
            max_calls=max_calls,
            now=at,
        )
        pending["at"] = at

    def _complete(outcome, elapsed_s):
        complete_attempt(
            tmp_path, at=pending["at"], outcome=outcome, elapsed_s=elapsed_s
        )

    return _record, _complete


def test_each_attempt_records_what_it_did_and_how_long_it_took(tmp_path, monkeypatch):
    """503, 503, 200 — three rows, and the ledger says which was which.

    This is the distribution item 80 could only infer. Failures track
    whatever ceiling exists; successes have never been observed directly,
    because the ledger recorded only the START of an attempt.
    """
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(503)
        return _FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    record, complete = _paired_hooks(tmp_path)
    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=record, after_attempt=complete
    )
    provider.generate("sys", "user", _TinySchema)

    rows = read_ledger(tmp_path)
    assert [r.outcome for r in rows] == ["http_503", "http_503", "http_200"]
    assert all(r.elapsed_s is not None and r.elapsed_s >= 0 for r in rows), (
        "every completed row carries a duration"
    )


def test_a_timeout_is_distinguishable_from_a_refusal(tmp_path, monkeypatch):
    """The distinction the whole item turns on.

    A hung connection cut at our own ceiling and a service refusing to accept
    work are different failures with different fixes — item 80's polling
    versus item 79's backoff. Both looked identical on the old ledger, which
    recorded a start time and nothing else.
    """
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("hung"))
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)

    record, complete = _paired_hooks(tmp_path)
    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=record, after_attempt=complete
    )
    with pytest.raises(LLMResponseError):
        provider.generate("sys", "user", _TinySchema)

    assert [r.outcome for r in read_ledger(tmp_path)] == ["timeout"] * 4


def test_a_connection_error_is_neither_a_timeout_nor_an_http_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("refused")),
    )
    monkeypatch.setattr("time.sleep", lambda *_: None)

    record, complete = _paired_hooks(tmp_path)
    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=record, after_attempt=complete
    )
    with pytest.raises(LLMResponseError):
        provider.generate("sys", "user", _TinySchema)

    assert [r.outcome for r in read_ledger(tmp_path)] == ["error"] * 4


def test_a_provider_that_never_finishes_the_row_still_counts_its_call(tmp_path):
    """The completing write is optional; the counting one is not.

    A third-party provider that only calls `before_attempt` stays correct
    under the cap and simply leaves open rows behind. That is why an
    unfinished row is not warned about the way an unrecorded call is — it is
    the expected shape for a provider that does not measure itself.
    """
    record, _ = _paired_hooks(tmp_path)
    record()

    row = read_ledger(tmp_path)[0]
    assert row.outcome is None
    assert len(read_ledger(tmp_path)) == 1


def test_the_duration_excludes_the_backoff_it_waited_afterwards(tmp_path, monkeypatch):
    """Elapsed is the request, not the retry schedule.

    Including the sleep would bake RETRY_BASE_DELAY_S into every failed
    attempt's latency and make the ledger agree with whatever backoff was
    configured — the same circularity that made the 60s reading wrong.
    """
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(503))
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    record, complete = _paired_hooks(tmp_path)
    provider = GeminiProvider(
        api_key="k", model="m", before_attempt=record, after_attempt=complete
    )
    with pytest.raises(LLMResponseError):
        provider.generate("sys", "user", _TinySchema)

    assert sum(slept) > 0, "the loop really did back off between attempts"
    assert all(r.elapsed_s < 1.0 for r in read_ledger(tmp_path)), (
        "a sub-second fake request is recorded as sub-second"
    )


def test_a_replay_is_counted_like_any_other_call(tmp_path, monkeypatch):
    """`olw replay` is the most expensive thing this tool does — one call per
    frozen case, six of them today — and it announced, in its own output and
    in its own docstring, that they were "counted against the spend cap".

    They were not. `_run_replay` built its deps through `_build_pipeline_deps`
    and then called `run_replay(deps.llm_provider, cases)` directly, so
    `attach_spend_cap` never ran: nothing was recorded, nothing was counted,
    and `assert_capacity` never asked whether there was budget left. Measured
    2026-09-10 — a six-case replay made eight requests (two 503s retried) and
    the ledger gained not one row.

    Worse than an uncounted call: a replay could run with the cap already
    exhausted and then leave the morning forecast to be refused.
    """
    from openlocalweather import replay as replay_mod
    from openlocalweather.cli import _run_replay

    calls: list[str] = []

    class Recording:
        model = "fake-model"
        before_attempt = None
        after_attempt = None
        after_response = None

        def generate(self, system_prompt, user_prompt, response_schema):
            if self.before_attempt is not None:
                self.before_attempt()
            calls.append(system_prompt)
            if self.after_attempt is not None:
                self.after_attempt("http_200", 0.1)
            from openlocalweather.llm.schema import (
                GeminiForecastResponse,
                TodayProperties,
            )

            return GeminiForecastResponse(
                yesterday_verification="ok",
                verification_notes=[],
                skill_profile_summaries=[],
                today_properties=TodayProperties(
                    rain=False,
                    rain_expected="Unlikely",
                    temp_high_c=26.0,
                    temp_low_c=18.0,
                    temp_high_low="26°C / 79°F",
                ),
                today_narrative="## Overview\nDry.",
                whatsapp_summary=None,
            )

    from argparse import Namespace
    from pathlib import Path

    from openlocalweather.config import LocationConfig, Point
    from openlocalweather.pipeline import PipelineDeps

    deps = PipelineDeps(
        location=LocationConfig(
            region_name="R",
            primary_place_name="P",
            timezone="UTC",
            primary_point=Point(lat=0.0, lon=0.0),
        ),
        data_dir=Path(tmp_path),
        llm_provider=Recording(),
        public_webpage_url="",
    )
    monkeypatch.setattr("openlocalweather.cli._build_pipeline_deps", lambda *a, **k: deps)
    monkeypatch.setattr(
        replay_mod,
        "frozen_cases",
        lambda: [
            replay_mod.ReplayCase(name="one", system_prompt="s1", user_prompt="u1"),
            replay_mod.ReplayCase(name="two", system_prompt="s2", user_prompt="u2"),
        ],
    )

    _run_replay(
        Namespace(
            config="config/location.yaml",
            data_dir=str(tmp_path),
            docs_dir=str(tmp_path / "docs"),
            public_url="",
            out=str(tmp_path / "out"),
            yes=True,
        )
    )

    assert len(calls) == 2, "the replay did not run"
    rows = read_ledger(tmp_path)
    assert len(rows) == 2, f"a replay spent {len(calls)} calls and recorded {len(rows)}"
    assert {r.purpose for r in rows} == {"replay"}, (
        "recorded under the wrong purpose — a ledger read later cannot tell a "
        "replay from the forecast it was meant to be compared against"
    )
