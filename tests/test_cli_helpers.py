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
    "LLM_JSON_MODE", "LLM_MAX_TOKENS", "LLM_FALLBACK_MODELS",
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
    # 2026-09-05 the passes-when-it-matches test exited 1 mid-suite and then
    # passed both in isolation and on a rerun, which is the shape of a feed
    # that did not answer — UNREACHABLE sets `ok` false — and not of anything
    # the test is about. A feed date fixed in the past reads QUIET
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


# --- Provider selection moved to config, 2026-09-15 (ROADMAP items 81, 132) --


def test_the_config_names_the_provider(monkeypatch):
    """THE POINT OF THE MOVE. It used to come from a code constant that
    nothing set, so the live deployment ran on the default and switching meant
    editing code or defining a repository variable in a web UI — which leaves
    no diff, no commit message and no author."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    built = _build_llm_provider(providers=["gemini-interactions"])

    assert type(built).__name__ == "GeminiInteractionsProvider"


def test_the_environment_still_overrides_the_config(monkeypatch):
    """Kept because it costs nothing and is how this endpoint was first
    driven: LLM_PROVIDER=... tools/rerender_narrative.py runs a one-off
    against a different provider without editing a committed file."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    built = _build_llm_provider(providers=["gemini-interactions"])

    assert type(built).__name__ == "GeminiProvider"


def test_no_config_and_no_env_still_builds_the_default(monkeypatch):
    """A deployment whose config predates the field keeps working."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    assert type(_build_llm_provider()).__name__ == "GeminiProvider"


def test_the_live_config_is_what_we_think_it_is():
    """Reads the deployment's OWN config, not a fixture.

    The change this pins is a one-line edit to a YAML file, and every other
    test here would pass just as happily with that line absent or misspelt.
    """
    from openlocalweather.config import load_location_config

    live = load_location_config("config/location.yaml")
    # THE ORDER IS THE CHANGE — ROADMAP item 81, 2026-09-21. Gemini answers
    # when it can and the gateway takes what it sheds.
    assert live.llm_providers == ["gemini-interactions", "openai"]
    # The three the operator chose on 2026-09-21, from OpenRouter's live free
    # list. Pinned by NAME because a typo in a model id is a run that fails at
    # the gateway, on the day the primary was already down.
    # THE SECOND AND THIRD TRIES. The first is `LLM_MODEL`, which the request
    # prepends — naming it here too would send it twice. Both of these carry
    # `structured_outputs` and `response_format` on OpenRouter's list, which
    # is what `require_parameters` restricts routing to; `gemma-4-31b` was
    # dropped 2026-09-22 for advertising only the second.
    assert live.llm_fallback_models == [
        "dots-studio/dots-3-note-preview:free",
        "nex-agi/nex-n2.5-pro:free",
    ]
    # FREE TIERS ONLY — the operator's constraint, so that anyone can run
    # this configuration without an account that bills. A paid id landing
    # here should be a deliberate decision, not a drift.
    assert all(m.endswith(":free") for m in live.llm_fallback_models)


# ---------------------------------------------------------------------------
# The chain — ROADMAP item 81
# ---------------------------------------------------------------------------


def test_a_list_of_providers_builds_a_chain(monkeypatch):
    from openlocalweather.llm.fallback import FallbackProvider

    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

    provider = _build_llm_provider(providers=["gemini", "openai"])
    assert isinstance(provider, FallbackProvider)
    assert provider.model == "gemini-3.6-flash"


def test_a_provider_whose_key_is_absent_is_dropped_loudly(monkeypatch, capsys):
    """The operator's own condition — "if the user has the right api keys".
    A missing second key is not an error, it is a deployment that holds one
    vendor. It still gets SAID: a chain silently shorter than its config is
    the failure `config.py`'s validator was written against."""
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")  # and no LLM_* at all

    provider = _build_llm_provider(providers=["gemini", "openai"])
    assert isinstance(provider, GeminiProvider), "the chain kept an unbuildable entry"

    err = capsys.readouterr().err
    assert "unavailable and was dropped" in err
    assert "openai" in err


def test_a_chain_with_nothing_buildable_exits_naming_every_reason(monkeypatch):
    with pytest.raises(SystemExit, match="No LLM provider could be built"):
        _build_llm_provider(providers=["gemini", "openai"])


def test_one_provider_missing_its_key_still_exits_as_it_always_did(monkeypatch):
    """Most deployments name one provider. Nothing about them changes."""
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        _build_llm_provider(providers=["gemini"])


def test_the_environment_override_selects_exactly_one(monkeypatch):
    """`LLM_PROVIDER=... tools/rerender_narrative.py` is a one-off against a
    named endpoint, and a one-off that fell through to a second vendor would
    report the wrong thing about the first."""
    from openlocalweather.llm.fallback import FallbackProvider

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "some/model")

    provider = _build_llm_provider(providers=["gemini", "openai"])
    assert not isinstance(provider, FallbackProvider)
    assert isinstance(provider, GeminiProvider)


def test_the_gateway_is_handed_its_own_model_order(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

    provider = _build_llm_provider(
        fallback_models=["dots-studio/dots-3-note-preview:free", "google/gemma-4-31b-it:free"]
    )
    assert provider.fallback_models == [
        "dots-studio/dots-3-note-preview:free",
        "google/gemma-4-31b-it:free",
    ]
    # Travels with the list: a model order is only as good as the routing
    # behind it, and without this the gateway may serve one through an
    # upstream that treats the JSON schema as a hint.
    assert provider.require_parameters is True


def test_no_model_order_means_no_openrouter_extensions(monkeypatch):
    """`models` and `provider` are OpenRouter fields. OpenAI itself rejects
    unknown top-level keys, and this class also covers Groq, Together, vLLM
    and Ollama."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-5-mini")

    provider = _build_llm_provider()
    assert provider.fallback_models == []
    assert provider.require_parameters is False


def test_a_misspelt_provider_in_a_chain_is_fatal_not_dropped(monkeypatch):
    """The trap the diff review caught. A chain treats SystemExit as "this
    deployment does not hold that vendor", which is right for a missing key
    and wrong for a typo: the run would quietly use a shorter chain than its
    config names, on the day the primary was already down."""
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    with pytest.raises(SystemExit, match="Unknown LLM provider"):
        _build_llm_provider(providers=["gemini", "openai-compatible"])


# ---------------------------------------------------------------------------
# Per-entry credentials — ROADMAP item 81, 2026-09-22
#
# The chain built on 2026-09-21 walks a list of VENDOR NAMES, and each name
# is wired to one fixed set of environment variables. That is what stops the
# list being extended the way the operator asked for on 2026-09-22: "gemini
# first, then openrouter, then another and another as long as I have api keys
# and providers". Two gateways cannot both be `openai`, because they would
# read the same LLM_API_KEY, LLM_BASE_URL and LLM_MODEL.
# ---------------------------------------------------------------------------


# These read `FallbackProvider._providers` directly. It is private and stays
# private — the chain's ORDER is what these tests are about, and there is no
# public way to see it. Widening the class for a test would be the tail
# wagging the dog; CLAUDE.md asks before widening visibility, so this does
# not.
def _clear_llm_env(monkeypatch):
    for var in (
        "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "LLM_FALLBACK_MODELS", "GEMINI_API_KEY", "GEMINI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_two_gateways_each_read_their_own_credentials(monkeypatch):
    """The ask itself: a chain of two OpenAI-compatible endpoints.

    Before this, the second entry was unreachable — both would read
    LLM_BASE_URL and LLM_MODEL, so the chain would hold two providers
    pointing at the same endpoint with the same model.
    """
    from openlocalweather.llm.fallback import FallbackProvider

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_MODEL", "or-model")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("GROQ_MODEL", "groq-model")

    built = _build_llm_provider(
        providers=[
            {"kind": "openai", "name": "openrouter", "env_prefix": "OPENROUTER"},
            {"kind": "openai", "name": "groq", "env_prefix": "GROQ"},
        ]
    )

    assert isinstance(built, FallbackProvider)
    first, second = built._providers
    assert (first.model, first.base_url) == ("or-model", "https://openrouter.ai/api/v1")
    assert (second.model, second.base_url) == ("groq-model", "https://api.groq.com/openai/v1")
    assert first.api_key != second.api_key


def test_a_bare_string_entry_keeps_the_old_variables(monkeypatch):
    """No config file has to change. The 2026-09-15 discipline, again."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "legacy-model")

    built = _build_llm_provider(providers=["openai"])

    assert built.model == "legacy-model"
    assert built.base_url == "https://example.test/v1"


def test_entries_sharing_an_env_prefix_are_fatal(monkeypatch):
    """Always a config mistake, and silent before this.

    `anthropic` and `openai` both read LLM_API_KEY and LLM_MODEL, so a chain
    holding both BUILDS and then hands one of them the other's credentials —
    failing at call time, after spending an attempt and a ledger row.
    """
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")

    with pytest.raises(SystemExit) as e:
        _build_llm_provider(providers=["anthropic", "openai"])

    assert "LLM" in str(e.value)
    assert "anthropic" in str(e.value) and "openai" in str(e.value)


def test_a_named_entry_whose_key_is_absent_is_dropped_by_its_name(monkeypatch, capsys):
    """The warning must name the ENTRY, not the vendor.

    With two `openai` entries, "openai was dropped" does not say which.
    """
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_MODEL", "or-model")
    monkeypatch.setenv("GEMINI_API_KEY", "g")

    built = _build_llm_provider(
        providers=[
            "gemini",
            {"kind": "openai", "name": "groq", "env_prefix": "GROQ"},
            {"kind": "openai", "name": "openrouter", "env_prefix": "OPENROUTER"},
        ]
    )

    err = capsys.readouterr().err
    # THE WARNING'S OWN SUBJECT, not merely the word somewhere in the line.
    # A mutation replacing the label with the vendor survived the looser
    # check on 2026-09-22, because the SystemExit message quoted underneath
    # names the entry too — so "groq" appeared either way.
    assert "groq:" in err
    assert "openai:" not in err
    assert type(built).__name__ == "FallbackProvider"
    assert len(built._providers) == 2


def test_fallback_models_belong_to_the_entry_that_uses_them(monkeypatch):
    """Top-level `llm_fallback_models` cannot say WHICH gateway it means."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_MODEL", "or-model")
    monkeypatch.setenv("GROQ_API_KEY", "k2")
    monkeypatch.setenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("GROQ_MODEL", "groq-model")

    built = _build_llm_provider(
        providers=[
            {
                "kind": "openai", "name": "openrouter", "env_prefix": "OPENROUTER",
                "fallback_models": ["a:free", "b:free"],
            },
            {"kind": "openai", "name": "groq", "env_prefix": "GROQ"},
        ]
    )

    assert built._providers[0].fallback_models == ["a:free", "b:free"]
    assert built._providers[1].fallback_models == []


def test_one_vendor_two_apis_share_the_one_key(monkeypatch):
    """The chain `CREDENTIAL_FAMILIES` exists to PERMIT.

    `gemini` and `gemini-interactions` are one account reaching two APIs, so
    they share GEMINI_API_KEY by design — trying the newer API and falling
    back to the older one is the intended use. A rule that simply forbade a
    shared prefix would ban it, and nothing caught that until this test: the
    mutation putting `gemini-interactions` in its own family survived a full
    suite on 2026-09-22.
    """
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "one-key")

    built = _build_llm_provider(providers=["gemini-interactions", "gemini"])

    first, second = built._providers
    assert type(first).__name__ == "GeminiInteractionsProvider"
    assert type(second).__name__ == "GeminiProvider"
    assert first.api_key == second.api_key == "one-key"


def test_a_named_entry_does_not_inherit_the_top_level_model_order(monkeypatch):
    """Found by DRIVING the real config, not by the tests above — 2026-09-22.

    `llm_fallback_models` is OpenRouter's in-request `models` array, holding
    OpenRouter model ids. A five-link chain built from the live config handed
    all three of them to the `groq` entry, which would ask Groq for
    "google/gemma-4-31b-it:free".

    The top-level field exists for the one deployment shape it was written
    for: a single bare `openai` entry. A BARE STRING still inherits it, so
    nothing in an existing config changes. A MAPPING ENTRY names its own
    gateway, so it gets only what it declares — which is the whole reason the
    field moved onto the entry.
    """
    _clear_llm_env(monkeypatch)
    for prefix in ("OPENROUTER", "GROQ"):
        monkeypatch.setenv(f"{prefix}_API_KEY", "k")
        monkeypatch.setenv(f"{prefix}_BASE_URL", f"https://{prefix.lower()}.test/v1")
        monkeypatch.setenv(f"{prefix}_MODEL", f"{prefix.lower()}-model")

    built = _build_llm_provider(
        providers=[
            {"kind": "openai", "name": "openrouter", "env_prefix": "OPENROUTER"},
            {"kind": "openai", "name": "groq", "env_prefix": "GROQ"},
        ],
        fallback_models=["nvidia/nemotron:free", "google/gemma:free"],
    )

    openrouter, groq = built._providers
    assert openrouter.fallback_models == []
    assert groq.fallback_models == []


def test_a_bare_openai_entry_still_inherits_the_top_level_model_order(monkeypatch):
    """The other half, and the reason the field still exists at top level."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron:free")

    built = _build_llm_provider(
        providers=["openai"], fallback_models=["nvidia/nemotron:free", "google/gemma:free"]
    )

    assert built.fallback_models == ["nvidia/nemotron:free", "google/gemma:free"]
