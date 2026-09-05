import subprocess

import pytest

from openlocalweather.cli import _github_repo_slug


def test_github_repo_slug_prefers_env_var(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "dissent00/open-local-weather")
    assert _github_repo_slug() == "dissent00/open-local-weather"


def test_github_repo_slug_falls_back_to_git_remote_https(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/dissent00/open-local-weather.git\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == "dissent00/open-local-weather"


def test_github_repo_slug_falls_back_to_git_remote_ssh(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="git@github.com:dissent00/open-local-weather.git\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == "dissent00/open-local-weather"


def test_github_repo_slug_returns_empty_on_non_github_remote(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://gitlab.com/someone/somewhere.git\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == ""


def test_github_repo_slug_returns_empty_when_git_fails(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _github_repo_slug() == ""


# ---------------------------------------------------------------------------
# _build_llm_provider — LLM_PROVIDER selection
# ---------------------------------------------------------------------------


import pytest

from openlocalweather.cli import _build_llm_provider
from openlocalweather.llm.anthropic import AnthropicProvider
from openlocalweather.llm.gemini import GeminiProvider
from openlocalweather.llm.openai_compat import OpenAICompatProvider

LLM_ENV_VARS = (
    "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
    "LLM_JSON_MODE", "LLM_MAX_TOKENS",
    "GEMINI_API_KEY", "GEMINI_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    """Every selection test starts from a blank slate — otherwise a real
    GEMINI_API_KEY in the developer's own shell silently changes results."""
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_gemini_with_no_llm_provider_set(monkeypatch):
    """Backward compatibility is the whole point: an existing deployment
    that only ever set GEMINI_API_KEY must keep working untouched."""
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    provider = _build_llm_provider(thinking_level="high")
    assert isinstance(provider, GeminiProvider)
    assert provider.api_key == "gem-key"
    assert provider.thinking_level == "high"


def test_gemini_missing_key_exits_with_clear_message():
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        _build_llm_provider()


def test_selects_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "ant-key")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    provider = _build_llm_provider(thinking_level="high")
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5"
    # Defaults to the real API when no gateway is configured.
    assert provider.endpoint == "https://api.anthropic.com/v1/messages"


def test_anthropic_respects_max_tokens_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "ant-key")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("LLM_MAX_TOKENS", "16384")
    assert _build_llm_provider().max_tokens == 16384


def test_anthropic_missing_config_exits(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "ant-key")  # no LLM_MODEL
    with pytest.raises(SystemExit, match="LLM_MODEL"):
        _build_llm_provider()


def test_selects_openai_compatible(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "some/model")
    provider = _build_llm_provider()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.endpoint == "https://openrouter.ai/api/v1/chat/completions"


def test_openai_allows_keyless_local_runtime(monkeypatch):
    """Ollama needs no API key — this path must not demand one."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    provider = _build_llm_provider()
    assert provider.api_key == ""


def test_openai_missing_base_url_exits(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "some/model")
    with pytest.raises(SystemExit, match="LLM_BASE_URL"):
        _build_llm_provider()


def test_unknown_provider_exits_listing_valid_options(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "hal9000")
    with pytest.raises(SystemExit, match="gemini"):
        _build_llm_provider()


def test_provider_name_is_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "  Anthropic  ")
    monkeypatch.setenv("LLM_API_KEY", "ant-key")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    assert isinstance(_build_llm_provider(), AnthropicProvider)


def test_empty_env_vars_fall_back_to_defaults(monkeypatch):
    """GitHub Actions sets `FOO: ${{ vars.FOO }}` to the EMPTY STRING when
    that repository variable isn't defined — not absent. Without treating
    empty as unset, every scheduled run would have built GeminiProvider
    with model="" (a hard crash) and thinking_level=None (silently losing
    the deliberately-measured "high" setting). Regression test for exactly
    that, since it only reproduces under Actions, never locally."""
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("GEMINI_MODEL", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")

    provider = _build_llm_provider(thinking_level="high")
    assert isinstance(provider, GeminiProvider), "empty LLM_PROVIDER must mean 'use the default'"
    assert provider.model == "gemini-3.6-flash", "empty GEMINI_MODEL must fall back, not be sent as ''"
    assert provider.thinking_level == "high"


def test_empty_max_tokens_falls_back_rather_than_crashing(monkeypatch):
    """int("") raises ValueError — an unset LLM_MAX_TOKENS repo variable
    must not take the whole run down."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "ant-key")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("LLM_MAX_TOKENS", "")
    monkeypatch.setenv("LLM_BASE_URL", "")

    provider = _build_llm_provider()
    assert provider.max_tokens == 8192
    assert provider.endpoint == "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# `olw forecast` — the one verb, and what it does with what comes back
# ---------------------------------------------------------------------------


def _forecast_argv(*extra):
    return ["forecast", "--config", "c.yaml", "--data-dir", "d", "--docs-dir", "docs", *extra]


def test_forecast_verb_reports_a_skip_as_success(monkeypatch, capsys):
    """A duplicate trigger is the system working — a backup slot firing behind
    a primary that already delivered. Colouring that run red trains an
    operator to ignore red runs."""
    from datetime import date

    from openlocalweather import cli
    from openlocalweather.pipeline import ForecastSkipped

    monkeypatch.setattr(cli, "_build_pipeline_deps", lambda *a, **k: object())
    monkeypatch.setattr(
        cli, "run_forecast", lambda deps, dry_run, force: ForecastSkipped(date(2026, 8, 11), "just ran")
    )

    assert cli.main(_forecast_argv()) == 0
    assert "just ran" in capsys.readouterr().out


def test_forecast_verb_passes_force_through(monkeypatch):
    from openlocalweather import cli

    seen = {}

    def fake(deps, dry_run, force):
        seen.update(dry_run=dry_run, force=force)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "_build_pipeline_deps", lambda *a, **k: object())
    monkeypatch.setattr(cli, "run_forecast", fake)

    with pytest.raises(SystemExit):
        cli.main(_forecast_argv("--force", "--dry-run"))
    assert seen == {"dry_run": True, "force": True}


# ---------------------------------------------------------------------------
# check-health: the aligned-window check's exit code. The point of running
# this comparison weekly is that a drifted table turns the job red, where
# the same comparison in a forecast run only reaches a log nobody reads.
# ---------------------------------------------------------------------------


def _health_argv(data_dir):
    from openlocalweather import cli

    return ["check-health", "--config", str(cli.DEFAULT_CONFIG_PATH), "--data-dir", str(data_dir)]


def _stub_the_other_health_checks(monkeypatch):
    """Everything check-health does apart from the aligned-window check.
    Stubbed so the exit code under test is that check's alone — the LLM
    call in particular must not be made."""
    from openlocalweather import cli
    from openlocalweather.health_check import ModelDeprecationCheck

    class FakeProvider:
        model = "gemini-3.6-flash"

    monkeypatch.setattr(cli, "_build_llm_provider", lambda **kwargs: FakeProvider())
    monkeypatch.setattr(
        cli,
        "check_model_deprecation",
        lambda llm, model_name: ModelDeprecationCheck(deprecated_or_scheduled=False, notes="Not listed."),
    )
    monkeypatch.setattr(cli, "detect_trigger_regression", lambda lookup, today: None)
    monkeypatch.setattr(cli, "detect_coverage", lambda *args: [])
    monkeypatch.setattr(cli, "_days_since_last_commit", lambda: 0)

    # The CAP probe is a live HTTP request to Kenya's met service whenever
    # config/location.yaml sets cap_feed_url, which it does. Left unstubbed it
    # made every caller of this helper depend on that host being up: on
    # 2026-09-05 the probe timed out mid-suite, check_cap_feed returned
    # UNREACHABLE, and the passes-when-it-matches test exited 1 — then passed
    # on a rerun and in isolation. A feed date fixed in the past reads QUIET
    # forever, which is what production has seen since May 2026 and leaves
    # `ok` true, so the exit code stays the check-under-test's alone.
    class QuietFeed:
        status_code = 200
        text = "<rss><channel><item><pubDate>Mon, 04 May 2026 09:00:00 +0000</pubDate></item></channel></rss>"

    monkeypatch.setattr(cli.requests, "get", lambda *args, **kwargs: QuietFeed())


def _observed_at(offset_hours):
    """A settled observation `offset_hours` from whatever cycle the table
    derives for the clock the CLI actually reads — computed from the `now`
    the CLI passes in, so the test cannot straddle a window boundary."""
    from datetime import timedelta

    from openlocalweather.cycle import aligned_cycle_at
    from openlocalweather.fetch.model_run import OBSERVED_MODEL, ModelRun

    def fake(now):
        initialised_at = aligned_cycle_at(now).initialised_at + timedelta(hours=offset_hours)
        return ModelRun(model=OBSERVED_MODEL, initialised_at=initialised_at, available_at=now)

    return fake


def test_check_health_fails_when_the_aligned_window_table_has_drifted(monkeypatch, capsys, tmp_path):
    from openlocalweather import cli
    from openlocalweather.fetch import model_run as model_run_fetch

    _stub_the_other_health_checks(monkeypatch)
    monkeypatch.setattr(model_run_fetch, "fetch_settled_run", _observed_at(-6))

    assert cli.main(_health_argv(tmp_path)) == 1
    assert "WARNING" in capsys.readouterr().out


def test_check_health_passes_when_the_observation_matches_the_table(monkeypatch, capsys, tmp_path):
    from openlocalweather import cli
    from openlocalweather.fetch import model_run as model_run_fetch

    _stub_the_other_health_checks(monkeypatch)
    monkeypatch.setattr(model_run_fetch, "fetch_settled_run", _observed_at(0))

    assert cli.main(_health_argv(tmp_path)) == 0


def test_check_health_does_not_fail_when_there_is_nothing_to_compare(monkeypatch, capsys, tmp_path):
    """A silent metadata endpoint is not evidence about the table, and a
    best-effort observation must never be the reason a check goes red."""
    from openlocalweather import cli
    from openlocalweather.fetch import model_run as model_run_fetch

    _stub_the_other_health_checks(monkeypatch)
    monkeypatch.setattr(model_run_fetch, "fetch_settled_run", lambda now: None)

    assert cli.main(_health_argv(tmp_path)) == 0
    assert "not checked" in capsys.readouterr().out
