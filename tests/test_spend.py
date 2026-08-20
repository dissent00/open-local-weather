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
