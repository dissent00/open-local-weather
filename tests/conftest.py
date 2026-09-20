"""Shared test fixtures.

Keeps the suite fast and deterministic by removing real waiting. Retry delays
are correct behaviour in production and pure cost in a test: after adding
Open-Meteo retries, two existing failure-path tests silently started sleeping
4.5 seconds each, taking the whole suite from about one second to ten.

A slow suite gets run less, which is how regressions reach production — so
this is a correctness concern rather than a convenience.
"""

import pytest

from openlocalweather.fetch import metar, open_meteo


@pytest.fixture(autouse=True)
def _no_retry_sleeping(monkeypatch):
    """Zero every retry backoff, everywhere, for every test.

    Autouse so a future module that adds retries cannot quietly reintroduce
    the same slowdown — the alternative is remembering to patch it at each new
    call site, which is exactly the kind of discipline that lapses.
    """
    monkeypatch.setattr(open_meteo, "RETRY_BASE_DELAY_S", 0)
    # Item 79's longer wait for a timed-out request, zeroed for the same
    # reason. A test that is ABOUT the delay values sets them back itself.
    monkeypatch.setattr(open_meteo, "TIMEOUT_RETRY_DELAY_S", 0)
    monkeypatch.setattr(open_meteo.time, "sleep", lambda _: None)
    # The station archive's retry, item 151 (2026-09-20), zeroed the same way.
    monkeypatch.setattr(metar, "ARCHIVE_RETRY_DELAYS_S", (0, 0))
    monkeypatch.setattr(metar.time, "sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _no_station_prefetch(monkeypatch):
    """The run's one archive request, item 151 (2026-09-20), is a network
    call at the top of run_forecast that the pipeline tests never mocked —
    they stub the readers below it. A no-op here keeps every existing test
    off the network; the prefetch's own tests import the function directly
    at collection time and so get the real one."""
    monkeypatch.setattr(metar, "prefetch_station_rows", lambda *a, **k: None)
