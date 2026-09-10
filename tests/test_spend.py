"""The hard cap on LLM calls.

The bar is not "does it count" — it is whether the cap can be exceeded. Most
of these assert on the failure directions specifically, because a cap that
usually holds is not a cap.
"""

from datetime import datetime, timedelta, timezone

import pytest

from openlocalweather.spend import (
    DEFAULT_MAX_LLM_CALLS_PER_24H,
    SpendCapExceeded,
    SpendRecord,
    calls_in_window,
    complete_attempt,
    ledger_path,
    read_ledger,
    record_attempt,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _attempt(tmp_path, now, max_calls=3):
    return record_attempt(
        tmp_path,
        provider="GeminiProvider",
        model="gemini-3.6-flash",
        purpose="forecast",
        max_calls=max_calls,
        now=now,
    )


def test_the_cap_cannot_be_exceeded(tmp_path):
    for i in range(3):
        assert _attempt(tmp_path, NOW + timedelta(minutes=i)) == i + 1
    with pytest.raises(SpendCapExceeded):
        _attempt(tmp_path, NOW + timedelta(minutes=3))


def test_the_attempt_is_recorded_BEFORE_the_call_would_happen(tmp_path):
    """The crux.

    Recording after a call returns means a crash, timeout or kill between
    sending and recording loses the count and silently permits an overrun —
    exactly when things are already going wrong. So the ledger must already
    contain the attempt by the time record_attempt returns, with the caller
    yet to make the request.
    """
    _attempt(tmp_path, NOW)
    assert len(read_ledger(tmp_path)) == 1, (
        "the attempt must be durable before the caller makes its request"
    )


def test_it_counts_calls_not_forecasts(tmp_path):
    """One forecast can cost several calls when a provider is flaky, so a cap
    on successful runs would not be a cap on spend."""
    for i in range(3):
        _attempt(tmp_path, NOW + timedelta(seconds=i))
    # Three retries of ONE forecast have consumed the whole budget.
    with pytest.raises(SpendCapExceeded):
        _attempt(tmp_path, NOW + timedelta(seconds=4))


def test_the_window_rolls_rather_than_resetting_at_midnight(tmp_path):
    """A calendar-day reset would permit a full budget either side of it, so
    a cap of 3 would allow 6 within a few hours."""
    midnight = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        _attempt(tmp_path, midnight - timedelta(minutes=10 + i))
    # Ten minutes later, on the next calendar day, still refused.
    with pytest.raises(SpendCapExceeded):
        _attempt(tmp_path, midnight + timedelta(minutes=1))


def test_capacity_returns_only_once_calls_actually_age_out(tmp_path):
    for i in range(3):
        _attempt(tmp_path, NOW + timedelta(minutes=i))
    with pytest.raises(SpendCapExceeded):
        _attempt(tmp_path, NOW + timedelta(hours=23, minutes=59))
    # Exactly one slot frees: at NOW+24h+30s the cutoff is NOW+30s, so only
    # the first call (at NOW) has aged out and two remain in the window.
    assert _attempt(tmp_path, NOW + timedelta(hours=24, seconds=30)) == 3


def test_an_unreadable_ledger_fails_closed(tmp_path):
    """Everywhere else in this project an unreadable file degrades
    gracefully, because losing history beats refusing to run. Here the
    opposite holds: reading a corrupt ledger as "no calls yet" would disable
    the cap at the exact moment something is already wrong."""
    ledger_path(tmp_path).write_text("{ this is not json")
    with pytest.raises(Exception):
        _attempt(tmp_path, NOW)


def test_the_refusal_says_when_capacity_returns(tmp_path):
    """A bare "limit reached" leaves the operator guessing whether to wait
    ten minutes or raise the cap."""
    for i in range(3):
        _attempt(tmp_path, NOW + timedelta(minutes=i))
    with pytest.raises(SpendCapExceeded) as e:
        _attempt(tmp_path, NOW + timedelta(minutes=5))
    message = str(e.value)
    assert "3 of 3" in message
    assert "ages out at" in message
    assert "config/location.yaml" in message, "says how to change it"


def test_the_window_total_is_recomputed_not_stored(tmp_path):
    """Consistent with the project's standing rule: raw records are kept,
    derived figures are recomputed. A stored total could drift from the
    entries it claims to summarise."""
    _attempt(tmp_path, NOW)
    import json as _json

    stored = _json.loads(ledger_path(tmp_path).read_text())
    assert "calls" in stored
    # No derived total is persisted — checked against the KEYS rather than
    # the file text, since the explanatory note legitimately uses the word.
    assert not {"total", "count", "used", "calls_today"} & set(stored)

    records = read_ledger(tmp_path)
    assert calls_in_window(records, NOW) == 1
    assert calls_in_window(records, NOW + timedelta(hours=25)) == 0


def test_old_entries_are_pruned_but_a_week_is_kept(tmp_path):
    """Keeping more than the window costs nothing and makes "what did it
    spend last Tuesday" answerable, which matters the first time a bill looks
    wrong."""
    _attempt(tmp_path, NOW - timedelta(days=30))
    _attempt(tmp_path, NOW - timedelta(days=2))
    _attempt(tmp_path, NOW)
    ats = [r.at for r in read_ledger(tmp_path)]
    assert NOW - timedelta(days=30) not in ats, "far-old entry pruned"
    assert NOW - timedelta(days=2) in ats, "recent history retained"


def test_the_default_is_not_unlimited():
    """A cap only protects operators who have one. Defaulting to unlimited
    would protect nobody, and the pipeline's honest worst case is eight calls
    a day with retries."""
    assert DEFAULT_MAX_LLM_CALLS_PER_24H == 10
    assert DEFAULT_MAX_LLM_CALLS_PER_24H >= 8


def test_records_carry_enough_to_audit_a_bill(tmp_path):
    _attempt(tmp_path, NOW)
    r = read_ledger(tmp_path)[0]
    assert isinstance(r, SpendRecord)
    assert r.provider and r.model and r.purpose


def test_the_health_check_records_its_llm_call(monkeypatch, tmp_path):
    """Found 2026-09-03 while reconciling our ledger against the provider's
    own request count. The pipeline attaches a spend hook to the provider it
    is handed; `check-health` built one and attached nothing, so the weekly
    model-deprecation call — up to MAX_ATTEMPTS billable requests — was spent
    and never recorded.

    The cap cannot bound what it cannot see, and an unlogged call is worse
    than an uncapped one: it also makes the ledger disagree with the bill for
    reasons nobody can reconstruct.

    Asserts the BEHAVIOUR rather than a constructor signature — the hook is
    deliberately set after construction, because cli.py builds the provider
    and pipeline.py owns the ledger.
    """
    from openlocalweather import cli

    class _Spy:
        model = "gemini-3.6-flash"
        before_attempt = None

        def generate(self, system_prompt, user_prompt, response_schema):
            # A real provider calls this before every HTTP request.
            if self.before_attempt is not None:
                self.before_attempt()
            raise RuntimeError("stop here; the hook is what is under test")

    spy = _Spy()
    cli.attach_spend_cap(spy, tmp_path, max_calls=10, purpose="health-check")
    assert spy.before_attempt is not None, "check-health must record what it spends"

    spy.before_attempt()
    from datetime import datetime, timezone

    from openlocalweather.spend import calls_in_window, read_ledger

    assert calls_in_window(read_ledger(tmp_path), datetime.now(timezone.utc)) == 1


# --- The completing write: outcome and duration per attempt -----------------
#
# The count is still written BEFORE the call; these cover the SECOND write
# that fills in what the call did. Roadmap item 80 is the why: every latency
# question this project has asked was answered by subtracting something
# unmeasured from something else, and got item 66's timeout reading wrong
# doing it.


def test_completing_a_row_does_not_change_what_the_cap_counts(tmp_path):
    """The diagnostic must not be able to alter the guard it rides on.

    The completing write is a read-modify-write of the same file the cap is
    computed from. If it could drop, duplicate or re-time a row it would move
    the count — turning a measurement into a way to spend more.
    """
    _attempt(tmp_path, NOW, max_calls=3)
    before = read_ledger(tmp_path)

    complete_attempt(tmp_path, at=before[0].at, outcome="http_200", elapsed_s=1.5)

    after = read_ledger(tmp_path)
    assert len(after) == len(before) == 1
    assert after[0].at == before[0].at
    assert calls_in_window(after, NOW) == calls_in_window(before, NOW)


def test_the_completing_write_fills_the_row_it_opened(tmp_path):
    _attempt(tmp_path, NOW, max_calls=3)
    at = read_ledger(tmp_path)[0].at

    complete_attempt(tmp_path, at=at, outcome="timeout", elapsed_s=90.1)

    row = read_ledger(tmp_path)[0]
    assert row.outcome == "timeout"
    assert row.elapsed_s == 90.1


def test_it_completes_only_the_row_it_was_given(tmp_path):
    """Three attempts, one completed — the other two stay open."""
    for minute in range(3):
        _attempt(tmp_path, NOW + timedelta(minutes=minute), max_calls=10)
    rows = read_ledger(tmp_path)

    complete_attempt(tmp_path, at=rows[1].at, outcome="http_503", elapsed_s=0.4)

    outcomes = [r.outcome for r in read_ledger(tmp_path)]
    assert outcomes == [None, "http_503", None]


def test_an_incomplete_row_is_the_signal_that_the_process_died(tmp_path):
    """No outcome means the second write never happened.

    That is the whole point of writing in two halves rather than once at the
    end: a row with a start and no finish says the attempt left and nothing
    came back, which a single write after the fact could not distinguish from
    an attempt that was never made.
    """
    _attempt(tmp_path, NOW, max_calls=3)

    row = read_ledger(tmp_path)[0]
    assert row.outcome is None
    assert row.elapsed_s is None


def test_a_row_that_was_never_opened_is_reported_not_invented(tmp_path, capsys):
    """Completing a row that is not there must not append one.

    An appended row would be a call that was never made, counted against the
    cap. Refusing silently would hide a wiring bug, so it says so instead.
    """
    _attempt(tmp_path, NOW, max_calls=3)
    before = ledger_path(tmp_path).read_text()

    complete_attempt(
        tmp_path, at=NOW - timedelta(days=400), outcome="http_200", elapsed_s=1.0
    )

    assert ledger_path(tmp_path).read_text() == before, "the ledger is untouched"
    assert "WARNING" in capsys.readouterr().err


def test_rows_written_before_this_existed_still_read(tmp_path):
    """Every row on the real ledger predates these two fields."""
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"calls": [{"at": "2026-08-21T11:00:00+00:00", "provider": "p",'
        ' "model": "m", "purpose": "forecast"}]}'
    )

    row = read_ledger(tmp_path)[0]
    assert row.outcome is None
    assert row.elapsed_s is None


def test_an_open_row_serialises_without_the_fields_at_all(tmp_path):
    """Absent, not null — because every write rewrites the whole file.

    `record_attempt` prunes and rewrites all of it, so emitting explicit
    nulls would add two lines to every historical row the next time the
    pipeline runs. The ledger is committed, so that is a diff on hundreds of
    lines that means nothing.
    """
    assert SpendRecord(
        at=NOW, provider="p", model="m", purpose="forecast"
    ).to_json() == {
        "at": NOW.isoformat(),
        "provider": "p",
        "model": "m",
        "purpose": "forecast",
    }
