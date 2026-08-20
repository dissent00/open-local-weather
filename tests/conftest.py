"""Shared test fixtures.

Keeps the suite fast and deterministic by removing real waiting. Retry delays
are correct behaviour in production and pure cost in a test: after adding
Open-Meteo retries, two existing failure-path tests silently started sleeping
4.5 seconds each, taking the whole suite from about one second to ten.

A slow suite gets run less, which is how regressions reach production — so
this is a correctness concern rather than a convenience.
"""

import pytest

from openlocalweather.fetch import open_meteo


@pytest.fixture(autouse=True)
def _no_retry_sleeping(monkeypatch):
    """Zero every retry backoff, everywhere, for every test.

    Autouse so a future module that adds retries cannot quietly reintroduce
    the same slowdown — the alternative is remembering to patch it at each new
    call site, which is exactly the kind of discipline that lapses.
    """
    monkeypatch.setattr(open_meteo, "RETRY_BASE_DELAY_S", 0)
    monkeypatch.setattr(open_meteo.time, "sleep", lambda _: None)
