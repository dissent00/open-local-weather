"""Shared test fixtures.

Keeps the suite fast and deterministic by removing real waiting. Retry delays
are correct behaviour in production and pure cost in a test: after adding
Open-Meteo retries, two existing failure-path tests silently started sleeping
4.5 seconds each, taking the whole suite from about one second to ten.

A slow suite gets run less, which is how regressions reach production — so
this is a correctness concern rather than a convenience.
"""

import pytest

from openlocalweather import dates, pipeline
from openlocalweather.fetch import metar, open_meteo

#: The hour every test runs at unless it says otherwise.
#:
#: Morning, because that is the shape most assertions were written against:
#: a run before noon has TODAY in its horizon, a day-over-day comparison to
#: make, and a UV index for today rather than tomorrow.
SUITE_HOUR = 8
SUITE_MINUTE = 30


@pytest.fixture(autouse=True)
def _a_fixed_hour(monkeypatch):
    """Pins the TIME OF DAY for every test. The date is left alone.

    THE SUITE USED TO DEPEND ON WHEN IT WAS RUN, and it went red twice a day
    for it. Measured 2026-09-22: five tests in `test_pipeline_run.py` assert
    morning-shaped output while taking the run's clock from the wall — one
    fails from 12:00 local onwards, because `comparison_subject` returns None
    once the hour reaches COMPARISON_MORNING_ENDS_HOUR, and four more fail
    after sunset, when the horizon no longer contains TODAY.

    It cost a full investigation to find: CI was green at 10:45Z and red at
    12:23Z on a commit whose only change was a markdown file, which reads
    exactly like a flaky suite and is not — the behaviour is deliberate and
    the tests simply never said what hour they meant.

    THE DATE IS DELIBERATELY NOT PINNED. Tests that reason about "today" use
    the real one, and freezing it would change what they are about. Only the
    hour moves, which is the axis that was silently load-bearing.

    A test that is ABOUT another hour sets its own clock, as
    `test_the_comparison_modifiers_reach_the_day_record` does.
    """
    real = dates.now_in_tz

    def at_a_fixed_hour(tz_name: str):
        return real(tz_name).replace(
            hour=SUITE_HOUR, minute=SUITE_MINUTE, second=0, microsecond=0
        )

    monkeypatch.setattr(dates, "now_in_tz", at_a_fixed_hour)
    # The pipeline imported the name directly, so patching the module it came
    # from is not enough.
    monkeypatch.setattr(pipeline, "now_in_tz", at_a_fixed_hour)


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
