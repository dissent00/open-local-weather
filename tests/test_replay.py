"""ROADMAP item 27, the half that gates prompt work.

Replay is not "is this model better" — that question needs weeks of scored
forecasts and the existing review gates. This is the other half: feed a fixed
set of inputs to the LLM, keep the outputs, and diff them across a change so
that "did this prompt edit move anything I did not intend" stops being a
matter of impression. Items 48 and 53.3 both changed the prompt with no way
to answer that.
"""

import json
from datetime import date

import pytest

from openlocalweather.llm.schema import GeminiForecastResponse, TodayProperties
from openlocalweather.replay import (
    ReplayResult,
    diff_replays,
    frozen_cases,
    run_replay,
)


def _response(narrative: str, rain: str = "Unlikely") -> GeminiForecastResponse:
    return GeminiForecastResponse(
        today_narrative=narrative,
        whatsapp_summary="short",
        yesterday_verification="ok",
        skill_profile_summaries=[],
        today_properties=TodayProperties(
            rain_expected=rain,
            temp_high_c=28.0,
            temp_low_c=18.0,
            mslp_trend_24h="steady",
            synoptic_pattern="ridge",
            rain=False,
        ),
    )


class _FakeProvider:
    """Deterministic, and records what it was asked."""

    model = "fake-1.0"

    def __init__(self, narrative="## Overview\n\nWarm."):
        self.calls = []
        self._narrative = narrative

    def generate(self, system_prompt, user_prompt, response_schema):
        self.calls.append((system_prompt, user_prompt))
        return _response(self._narrative)


def test_the_frozen_cases_come_from_the_committed_vectors():
    """Not a new corpus. The prompt vectors are already frozen inputs, already
    committed, and already maintained by anyone who changes the prompt — a
    second set would be a second thing to keep in step."""
    cases = frozen_cases()
    assert len(cases) >= 5
    for c in cases:
        assert c.name
        assert "FORECAST DATA" in c.user_prompt or len(c.user_prompt) > 200
        assert len(c.system_prompt) > 200


def test_a_replay_calls_once_per_case_and_keeps_the_output():
    provider = _FakeProvider()
    cases = frozen_cases()[:2]
    results, _ = run_replay(provider, cases)

    assert len(provider.calls) == 2
    assert [r.case for r in results] == [c.name for c in cases]
    assert all(r.response.today_narrative == "## Overview\n\nWarm." for r in results)


def test_it_records_what_the_change_might_be_traded_against():
    """"Slightly better and three times slower" is a real outcome and a
    legitimate reason to decline a change, so latency and the model that
    produced it travel with the output."""
    results, _ = run_replay(_FakeProvider(), frozen_cases()[:1])
    r = results[0]
    assert r.latency_s >= 0
    assert r.model == "fake-1.0"


def test_a_diff_of_identical_runs_is_empty():
    a, _ = run_replay(_FakeProvider(), frozen_cases()[:2])
    b, _ = run_replay(_FakeProvider(), frozen_cases()[:2])
    assert diff_replays(a, b) == []


def test_a_changed_narrative_shows_up_as_a_difference():
    a, _ = run_replay(_FakeProvider("## Overview\n\nWarm."), frozen_cases()[:1])
    b, _ = run_replay(_FakeProvider("## Overview\n\nWarm, and rain later."), frozen_cases()[:1])

    d = diff_replays(a, b)
    assert len(d) == 1
    assert d[0].case == a[0].case
    assert "today_narrative" in d[0].changed_fields


def test_a_diff_names_the_scored_fields_separately():
    """The point of item 27's "separate what the LLM controls" note. A prompt
    edit that only reworded the prose is a different event from one that moved
    the blended call, and the second is the one that changes the accuracy
    record."""
    a, _ = run_replay(_FakeProvider(), frozen_cases()[:1])
    b, _ = run_replay(_FakeProvider(), frozen_cases()[:1])
    b[0] = ReplayResult(
        case=b[0].case,
        model=b[0].model,
        latency_s=b[0].latency_s,
        response=_response("## Overview\n\nWarm.", rain="Likely"),
    )

    d = diff_replays(a, b)
    assert d[0].scored_changed is True
    assert "today_properties.rain_expected" in d[0].changed_fields


def test_prose_only_changes_are_marked_as_such():
    a, _ = run_replay(_FakeProvider("one"), frozen_cases()[:1])
    b, _ = run_replay(_FakeProvider("two"), frozen_cases()[:1])
    assert diff_replays(a, b)[0].scored_changed is False


def test_results_round_trip_through_disk(tmp_path):
    """A replay is worth nothing unless the BEFORE run survives long enough to
    be compared against the after."""
    from openlocalweather.replay import read_replay, write_replay

    results, _ = run_replay(_FakeProvider(), frozen_cases()[:2])
    write_replay(tmp_path / "before", results)
    assert diff_replays(results, read_replay(tmp_path / "before")) == []


def test_each_case_is_paired_with_its_own_system_prompt():
    """Two bugs of the same shape were caught here, both of which produced a
    harness that looked fine: matching the two vector files BY NAME (which
    never matched, so every case silently got the first system prompt), and
    then reading `historical_logs` as the re-issue marker (which is the
    multi-day history and is present on ordinary runs too).

    A mismatched pair does not fail — it quietly reports differences that are
    artefacts of the harness rather than of the change under test, which is
    worse than a harness that does not run."""
    by_name = {c.name: c for c in frozen_cases()}

    # The system prompt branches on configuration, so these must not be equal.
    assert (
        by_name["no ground stations configured — the blocks are absent"].system_prompt
        != by_name["fully populated"].system_prompt
    )
    assert (
        by_name["no local met service configured — the bulletin block is absent"].system_prompt
        != by_name["fully populated"].system_prompt
    )

    # A re-issue is told it is one; a first run is not.
    refresh = by_name["evening refresh carries the morning narrative"].system_prompt
    first = by_name["fully populated"].system_prompt
    assert refresh != first
    assert "LATER ISSUANCE" in refresh
    assert "LATER ISSUANCE" not in first


def test_a_failure_partway_through_does_not_discard_what_succeeded():
    """Measured 2026-09-03: a real replay completed case 1, failed four times
    on case 2, raised, and lost the completed one. Each case is a paid call —
    throwing away work that was already bought is the one thing this must not
    do, and a six-case run that dies on the fifth would otherwise cost
    everything and return nothing."""
    calls = {"n": 0}

    class _FlakyProvider:
        model = "fake-1.0"

        def generate(self, system_prompt, user_prompt, response_schema):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("Gemini request failed after 4 attempts")
            return _response("ok")

    results, failures = run_replay(_FlakyProvider(), frozen_cases()[:3])

    assert [r.case for r in results] == [
        frozen_cases()[0].name,
        frozen_cases()[2].name,
    ], "the cases that succeeded must survive"
    assert len(failures) == 1
    assert failures[0].case == frozen_cases()[1].name
    assert "4 attempts" in failures[0].error


def test_a_clean_run_reports_no_failures():
    results, failures = run_replay(_FakeProvider(), frozen_cases()[:2])
    assert len(results) == 2
    assert failures == []
