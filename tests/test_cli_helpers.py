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
