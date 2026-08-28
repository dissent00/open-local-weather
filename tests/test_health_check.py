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
