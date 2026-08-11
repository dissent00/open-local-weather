import requests_mock

from openlocalweather.health_check import (
    DEFAULT_STALENESS_WARNING_DAYS,
    DEPRECATIONS_PAGE_URL,
    ModelDeprecationCheck,
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
