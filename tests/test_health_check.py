from datetime import datetime, timedelta, timezone

import requests_mock

from openlocalweather.fetch.model_run import OBSERVED_MODEL, ModelRun
from openlocalweather.health_check import (
    DEFAULT_STALENESS_WARNING_DAYS,
    DEPRECATIONS_PAGE_URL,
    AlignedWindowStatus,
    ModelDeprecationCheck,
    check_aligned_window,
    check_model_deprecation,
    check_repo_staleness,
    fetch_deprecations_page_text,
)


class FakeLLMProvider:
    def __init__(self, response: ModelDeprecationCheck):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt, user_prompt, response_schema):
        self.calls.append((system_prompt, user_prompt))
        return self.response


# ---------------------------------------------------------------------------
# check_repo_staleness
# ---------------------------------------------------------------------------


def test_repo_staleness_below_threshold_is_fine():
    assert check_repo_staleness(0) is False
    assert check_repo_staleness(DEFAULT_STALENESS_WARNING_DAYS - 1) is False


def test_repo_staleness_at_or_above_threshold_warns():
    assert check_repo_staleness(DEFAULT_STALENESS_WARNING_DAYS) is True
    assert check_repo_staleness(DEFAULT_STALENESS_WARNING_DAYS + 10) is True


def test_repo_staleness_custom_threshold():
    assert check_repo_staleness(10, warning_threshold_days=5) is True
    assert check_repo_staleness(4, warning_threshold_days=5) is False


# ---------------------------------------------------------------------------
# check_model_deprecation
# ---------------------------------------------------------------------------


def test_check_model_deprecation_passes_model_id_and_page_text_to_llm():
    llm = FakeLLMProvider(ModelDeprecationCheck(deprecated_or_scheduled=False, notes="Not listed."))
    check_model_deprecation(llm, "gemini-3.6-flash", page_text="some page content mentioning other models")

    assert len(llm.calls) == 1
    system_prompt, user_prompt = llm.calls[0]
    assert "gemini-3.6-flash" in user_prompt
    assert "some page content mentioning other models" in user_prompt
    assert "conservative" in system_prompt.lower()


def test_check_model_deprecation_returns_llm_verdict():
    llm = FakeLLMProvider(
        ModelDeprecationCheck(deprecated_or_scheduled=True, notes="Shutdown date 2026-11-01, replacement: foo.")
    )
    result = check_model_deprecation(llm, "some-old-model", page_text="...")
    assert result.deprecated_or_scheduled is True
    assert "2026-11-01" in result.notes


def test_check_model_deprecation_fetches_live_page_when_no_text_given():
    with requests_mock.Mocker() as m:
        m.get(DEPRECATIONS_PAGE_URL, text="<html>deprecations table</html>")
        llm = FakeLLMProvider(ModelDeprecationCheck(deprecated_or_scheduled=False, notes="ok"))
        check_model_deprecation(llm, "gemini-3.6-flash")

        assert "deprecations table" in llm.calls[0][1]


def test_fetch_deprecations_page_text():
    with requests_mock.Mocker() as m:
        m.get(DEPRECATIONS_PAGE_URL, text="page body")
        assert fetch_deprecations_page_text() == "page body"


# ---------------------------------------------------------------------------
# check_aligned_window — is the hand-measured table in docs-internal/
# ROADMAP.md still what the provider actually does? See cycle.py for the
# table itself and what its answer means.
# ---------------------------------------------------------------------------

# The weekly slot: 04:17 UTC, inside the window that opened at 02:00 UTC
# carrying the previous day's 18z cycle.
CHECK_TIME = datetime(2026, 8, 28, 4, 17, tzinfo=timezone.utc)
DERIVED_CYCLE = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)


def observed_run(initialised_at: datetime) -> ModelRun:
    return ModelRun(
        model=OBSERVED_MODEL,
        initialised_at=initialised_at,
        available_at=initialised_at + timedelta(hours=8),
    )


def test_aligned_window_agrees_when_the_observed_cycle_is_the_derived_one():
    result = check_aligned_window(CHECK_TIME, observed_run(DERIVED_CYCLE))

    assert result.status is AlignedWindowStatus.AGREES
    assert DERIVED_CYCLE.isoformat() in result.message


def test_aligned_window_drift_names_both_cycles_and_the_manual_remeasure():
    """The check exists to prompt a re-measurement, which is a manual act —
    so the message has to carry both figures and where to go, not just
    "drift detected"."""
    observed = observed_run(DERIVED_CYCLE - timedelta(hours=6))
    result = check_aligned_window(CHECK_TIME, observed)

    assert result.status is AlignedWindowStatus.DRIFTED
    assert observed.initialised_at.isoformat() in result.message
    assert DERIVED_CYCLE.isoformat() in result.message
    assert "meta.json" in result.message


def test_an_observation_older_than_derived_is_reported_as_the_dangerous_direction():
    """The table claiming alignment before the slowest model has the cycle
    means the derived floor understates the age of the guidance — the one
    thing a floor must never do."""
    result = check_aligned_window(CHECK_TIME, observed_run(DERIVED_CYCLE - timedelta(hours=6)))

    assert result.status is AlignedWindowStatus.DRIFTED
    assert "understating" in result.message


def test_an_observation_newer_than_derived_is_reported_as_merely_pessimistic():
    result = check_aligned_window(CHECK_TIME, observed_run(DERIVED_CYCLE + timedelta(hours=6)))

    assert result.status is AlignedWindowStatus.DRIFTED
    assert "pessimistic" in result.message


def test_no_settled_observation_is_not_agreement():
    """A silent endpoint says nothing about whether the table is right, and
    reporting it as agreement would let the table rot unwatched behind a
    green check."""
    result = check_aligned_window(CHECK_TIME, None)

    assert result.status is AlignedWindowStatus.NOT_CHECKED
    assert "not checked" in result.message


def test_drift_reported_well_inside_a_window_carries_no_boundary_caveat():
    result = check_aligned_window(CHECK_TIME, observed_run(DERIVED_CYCLE - timedelta(hours=6)))

    assert "window boundary" not in result.message


def test_drift_just_after_a_window_opens_says_so():
    """The availability delays behind the table vary by more than the hour
    the windows are rounded to, so a disagreement here is not evidence about
    the table — and re-measuring it by hand is real work to send someone on
    for nothing."""
    just_after = datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc)
    observed = observed_run(datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc))

    result = check_aligned_window(just_after, observed)

    assert result.status is AlignedWindowStatus.DRIFTED
    assert "window boundary" in result.message


def test_drift_shortly_before_a_window_opens_says_so():
    shortly_before = datetime(2026, 8, 28, 7, 30, tzinfo=timezone.utc)
    observed = observed_run(datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc))

    result = check_aligned_window(shortly_before, observed)

    assert result.status is AlignedWindowStatus.DRIFTED
    assert "window boundary" in result.message


# ---------------------------------------------------------------------------
# ROADMAP item 53.4 — the third surface: somebody is told
# ---------------------------------------------------------------------------

from openlocalweather.health_check import (
    DegradationCheck,
    DegradationStatus,
    check_recent_degradations,
)
from openlocalweather.models import RunDegradation


def _deg(code: str) -> RunDegradation:
    return RunDegradation(code=code, summary=f"{code} in plain words", detail=f"{code} happened")


def test_no_degradations_is_clean():
    result = check_recent_degradations([[], [], []])
    assert result.status is DegradationStatus.CLEAN


def test_one_isolated_degradation_is_reported_but_does_not_fail():
    """A single transient gap is now visible in the record and on the page.
    Failing the weekly job for it would make a red job routine, and a red job
    that is routine is a green job."""
    result = check_recent_degradations([[], [_deg("metar_unavailable")], []])
    assert result.status is DegradationStatus.ISOLATED
    assert "metar_unavailable" in result.message


def test_the_same_gap_twice_is_a_pattern_and_fails():
    """The 2026-08-29 incident: three consecutive runs lost the same block.
    A repeat is not bad luck, it is something broken that nobody was told
    about."""
    result = check_recent_degradations(
        [[_deg("hours_ahead_narrowed")], [], [_deg("hours_ahead_narrowed")]]
    )
    assert result.status is DegradationStatus.REPEATED
    assert "hours_ahead_narrowed" in result.message


def test_two_different_gaps_once_each_is_not_a_pattern():
    """Two unrelated one-off failures are two one-off failures. Counting them
    together would fire on exactly the noise this is meant to see past."""
    result = check_recent_degradations([[_deg("metar_unavailable")], [_deg("sun_times_unavailable")]])
    assert result.status is DegradationStatus.ISOLATED


def test_no_issuances_at_all_is_not_checked():
    """Distinct from clean, for the same reason check_aligned_window separates
    NOT CHECKED from AGREES: nothing to read is not evidence of health."""
    result = check_recent_degradations([])
    assert result.status is DegradationStatus.NOT_CHECKED


def test_an_entry_that_predates_recording_is_not_reported_as_clean():
    """The bug this exists to prevent, caught by running the check against
    the real committed record: every entry written before item 53.4 loads
    with no degradation list, and an empty list read as "nothing was missing"
    made the check announce that the last 20 issuances "had the data they
    expect" — including the three 2026-08-29 runs that did not, which are the
    reason this item exists.

    None means the run was never asked. It is not evidence of health, and
    this project's whole complaint about the incident is a gap being read as
    an all-clear."""
    result = check_recent_degradations([None, None])
    assert result.status is DegradationStatus.NOT_CHECKED
    # The wording is free to change; what must not is the claim. Nothing here
    # may read as "those runs were fine".
    assert "recorded" in result.message


def test_a_mix_of_recorded_and_unrecorded_says_how_many_it_could_read():
    result = check_recent_degradations([[], [], None])
    assert result.status is DegradationStatus.CLEAN
    assert "2" in result.message
    assert "1" in result.message


# ---------------------------------------------------------------------------
# ROADMAP item 2 — is the CAP warning feed alive?
# ---------------------------------------------------------------------------

from openlocalweather.health_check import (
    CapFeedStatus,
    check_cap_feed,
    newest_cap_item_age,
)


def _feed(dates: list[str]) -> str:
    items = "".join(f"<item><title>x</title><pubDate>{d}</pubDate></item>" for d in dates)
    return f"<rss version='2.0'><channel><title>t</title>{items}</channel></rss>"


def test_a_recent_alert_is_fresh():
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    r = check_cap_feed(_feed(["Thu, 07 May 2026 14:45:00 +0000"]), now=now)
    assert r.status is CapFeedStatus.FRESH
    # 07 May 14:45 to 10 May 00:00 is two whole days, not three. Whole days
    # elapsed, never calendar days crossed — the first draft of this test got
    # it the other way and the code was right.
    assert r.days_since_newest == 2


def test_a_long_quiet_spell_is_reported_but_does_not_fail():
    """Silence is not failure. Alerts are episodic — Kenya's feed carried
    nothing between May and September 2026, and the long rains had ended.
    A check that went red on a quiet season would be red most of the year and
    would teach everyone to ignore it."""
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    r = check_cap_feed(_feed(["Thu, 07 May 2026 14:45:00 +0000"]), now=now)
    assert r.status is CapFeedStatus.QUIET
    assert r.days_since_newest == 118
    assert "118" in r.message


def test_an_unreachable_feed_IS_a_failure():
    """The distinction that makes this check worth having. A quiet feed may be
    correct; a feed that stopped answering has moved or died, and that is
    actionable — the endpoint was only found because somebody probed a
    namespace nobody had thought to try."""
    r = check_cap_feed(None, now=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert r.status is CapFeedStatus.UNREACHABLE


def test_a_feed_with_no_items_is_not_the_same_as_no_feed():
    """An empty channel means the service is up and has published nothing.
    Reading it as unreachable would send someone to debug a working URL."""
    r = check_cap_feed(_feed([]), now=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert r.status is CapFeedStatus.EMPTY


def test_the_newest_item_wins_regardless_of_feed_order():
    """RSS order is a convention, not a guarantee, and the age of the feed is
    the age of its newest item."""
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    age = newest_cap_item_age(
        _feed([
            "Tue, 21 Apr 2026 14:13:00 +0000",
            "Thu, 07 May 2026 14:45:00 +0000",
            "Fri, 24 Apr 2026 16:27:00 +0000",
        ]),
        now=now,
    )
    assert age == 2


def test_an_unparseable_date_is_skipped_not_guessed():
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    assert newest_cap_item_age(_feed(["not a date"]), now=now) is None


def test_no_feed_configured_is_a_state_not_a_problem():
    """A fork whose met service publishes no CAP is not broken."""
    r = check_cap_feed("", now=datetime(2026, 9, 3, tzinfo=timezone.utc), configured=False)
    assert r.status is CapFeedStatus.NOT_CONFIGURED
