"""The doorbell on ROADMAP item 2's gate.

The item is gated on one event: the KMD CAP feed, silent since May 2026,
starting to carry alerts again. Nothing was going to report it. QUIET is
green and FRESH is green, so the transition produced a line in a weekly
Actions log and nothing else — a real event, correctly detected, announced
where nobody looks. That is item 66's shape one layer along.

These pin the two properties that make it a usable signal: it fires on the
change, and it fires ONCE.
"""

from openlocalweather.health_check import (
    CAP_STATUS_KEY,
    CapFeedStatus,
    cap_feed_woke_up,
)
from openlocalweather.store.health_status import (
    read_health_status,
    write_health_status,
)


def test_a_sleeping_feed_that_starts_issuing_rings_the_bell():
    assert cap_feed_woke_up(CapFeedStatus.QUIET.value, CapFeedStatus.FRESH)


def test_an_empty_feed_that_fills_rings_it_too():
    """EMPTY is kept a separate STATUS so a working URL is never debugged by
    mistake. As a starting point for a transition it is the same thing as
    QUIET: nothing there, then something there."""
    assert cap_feed_woke_up(CapFeedStatus.EMPTY.value, CapFeedStatus.FRESH)


def test_a_feed_that_was_already_fresh_rings_nothing():
    """The property that keeps this a signal rather than noise. Kenya's short
    rains run roughly October to December, so a feed that goes FRESH stays
    FRESH for months — and an alarm repeating weekly through a whole season
    is an alarm that gets muted."""
    assert not cap_feed_woke_up(CapFeedStatus.FRESH.value, CapFeedStatus.FRESH)


def test_a_feed_going_quiet_is_not_a_wake_up():
    for current in (CapFeedStatus.QUIET, CapFeedStatus.EMPTY, CapFeedStatus.UNREACHABLE):
        assert not cap_feed_woke_up(CapFeedStatus.FRESH.value, current), current


def test_the_first_run_ever_records_and_reports_nothing():
    """The three-valued case, and the one worth getting right.

    None means no run has recorded a status yet. A transition nobody was
    present for is not a transition anyone can report — and firing here would
    mean any fork whose national feed happens to be live opens with an alarm
    about news that is not new.
    """
    assert not cap_feed_woke_up(None, CapFeedStatus.FRESH)
    assert not cap_feed_woke_up(None, CapFeedStatus.QUIET)


def test_an_unreachable_feed_coming_back_is_not_a_wake_up():
    """A feed that was UNREACHABLE and is now FRESH means the ENDPOINT
    recovered, not that the service started warning. That is already covered
    by UNREACHABLE failing the check on its own, and conflating the two would
    report an outage's end as a reason to start building."""
    assert not cap_feed_woke_up(CapFeedStatus.UNREACHABLE.value, CapFeedStatus.FRESH)


def test_a_missing_file_reads_as_never_observed(tmp_path):
    assert read_health_status(tmp_path) == {}


def test_the_status_survives_a_write_and_read(tmp_path):
    write_health_status(tmp_path, {CAP_STATUS_KEY: CapFeedStatus.QUIET.value})

    assert read_health_status(tmp_path) == {CAP_STATUS_KEY: "quiet"}


def test_the_bell_rings_once_across_consecutive_runs(tmp_path):
    """The whole mechanism, end to end, over four weekly runs.

    Written as a sequence rather than as separate cases because the failure
    worth catching is not "the comparison is wrong" — it is "the new status
    was never recorded", which only shows up on the run AFTER the one that
    fired.
    """
    rings: list[bool] = []

    for observed in (
        CapFeedStatus.QUIET,
        CapFeedStatus.QUIET,
        CapFeedStatus.FRESH,
        CapFeedStatus.FRESH,
    ):
        previous = read_health_status(tmp_path).get(CAP_STATUS_KEY)
        rings.append(cap_feed_woke_up(previous, observed))
        write_health_status(tmp_path, {CAP_STATUS_KEY: observed.value})

    assert rings == [False, False, True, False], "exactly one run announces it"


# ---------------------------------------------------------------------------
# Through the real command. The logic above is arithmetic on an enum; what
# these catch is the wiring — a status read from the wrong directory, or the
# write skipped on the failing path, neither of which the unit tests can see.
# ---------------------------------------------------------------------------


def _feed_with(pub_date: str) -> str:
    return f"<rss><channel><item><pubDate>{pub_date}</pubDate></item></channel></rss>"


def _run_health(monkeypatch, tmp_path, feed_xml):
    """check-health with everything but the CAP probe stubbed out."""
    from datetime import timedelta

    from openlocalweather import cli
    from openlocalweather.cycle import aligned_cycle_at
    from openlocalweather.fetch import model_run as model_run_fetch
    from openlocalweather.fetch.model_run import OBSERVED_MODEL, ModelRun
    from tests.test_cli_helpers import _health_argv, _stub_the_other_health_checks

    _stub_the_other_health_checks(monkeypatch)
    monkeypatch.setattr(
        model_run_fetch,
        "fetch_settled_run",
        lambda now: ModelRun(
            model=OBSERVED_MODEL,
            initialised_at=aligned_cycle_at(now).initialised_at,
            available_at=now,
        ),
    )

    class FakeResponse:
        status_code = 200
        text = feed_xml

    monkeypatch.setattr(cli.requests, "get", lambda *a, **k: FakeResponse())

    return cli.main(_health_argv(tmp_path))


def test_the_first_run_records_the_feed_and_stays_green(monkeypatch, capsys, tmp_path):
    from datetime import datetime, timedelta, timezone

    old = datetime.now(timezone.utc) - timedelta(days=200)
    code = _run_health(monkeypatch, tmp_path, _feed_with(old.strftime("%a, %d %b %Y %H:%M:%S +0000")))
    capsys.readouterr()

    assert code == 0
    assert read_health_status(tmp_path)[CAP_STATUS_KEY] == "quiet"


def test_a_feed_waking_up_turns_the_job_red_exactly_once(monkeypatch, capsys, tmp_path):
    """The event ROADMAP item 2 waits for, driven through the real command.

    A red job carrying good news, which is the point: this deployment has one
    channel that reaches a person, and the alternative was a line in a weekly
    log next to four months of identical lines.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    quiet = _feed_with((now - timedelta(days=200)).strftime("%a, %d %b %Y %H:%M:%S +0000"))
    fresh = _feed_with((now - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000"))

    assert _run_health(monkeypatch, tmp_path, quiet) == 0
    capsys.readouterr()

    assert _run_health(monkeypatch, tmp_path, fresh) == 1, "the wake-up must be seen"
    assert "WAKE-UP" in capsys.readouterr().out

    # And the state that stops it repeating every Monday for a whole season.
    assert read_health_status(tmp_path)[CAP_STATUS_KEY] == "fresh"
    assert _run_health(monkeypatch, tmp_path, fresh) == 0, "announced once, not weekly"
