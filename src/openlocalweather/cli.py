"""Command-line entrypoint.

`olw run-daily` is what .github/workflows/daily.yml invokes. It reads
secrets from the environment (never from CLI args, so they don't end up in
shell history or process listings) and leaves git commit/push and any
required approvals to the caller — see pipeline.py's module docstring for
why.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from openlocalweather import __version__
from openlocalweather.config import load_location_config
from openlocalweather.dates import today_in_tz
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd import KenyaKMDBulletinFetcher
from openlocalweather.fetch.open_meteo import OpenMeteoFetchError
from openlocalweather.health_check import check_model_deprecation, check_repo_staleness
from openlocalweather.llm.anthropic import DEFAULT_BASE_URL as DEFAULT_ANTHROPIC_BASE_URL
from openlocalweather.llm.anthropic import DEFAULT_MAX_TOKENS as DEFAULT_ANTHROPIC_MAX_TOKENS
from openlocalweather.llm.anthropic import AnthropicProvider
from openlocalweather.llm.gemini import GeminiProvider, LLMResponseError
from openlocalweather.llm.openai_compat import OpenAICompatProvider
from openlocalweather.pipeline import (
    PipelineDeps,
    RefreshWithoutMorningRunError,
    run_daily_pipeline,
    run_refresh_pipeline,
)
from openlocalweather.publish.email_gmail import GmailSMTPSender, parse_recipient_list
from openlocalweather.publish.pages import GitHubPagesPublisher
from openlocalweather.review import build_weekly_review
from openlocalweather.store.actuals_cache import as_date_dict, read_actuals_cache
from openlocalweather.store.log_store import list_log_dates, make_log_lookup

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "location.yaml"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
# "high" by default for the actual forecast pipeline (run-daily,
# refresh-forecast) — measured against the real production prompt, "high"
# vs "low" is a real difference (4,235 vs 739 thinking tokens) and at
# ~45K tokens/call against a 250K-token/run free-tier limit there's ample
# headroom. check-health's model-deprecation check is a simple factual
# lookup, not multi-step reasoning, and intentionally does NOT set this —
# it uses Gemini's own default.
DEFAULT_GEMINI_THINKING_LEVEL = "high"

# Which LLMProvider implementation to build. "gemini" keeps this project's
# original free-tier path and is the default so existing deployments (and
# every doc written before multi-provider support) keep working with no
# environment changes at all. "openai" selects OpenAICompatProvider, which
# covers OpenAI, OpenRouter, Groq, Together, vLLM and Ollama — see that
# module's docstring.
DEFAULT_LLM_PROVIDER = "gemini"
VALID_LLM_PROVIDERS = ("gemini", "anthropic", "openai")


def _github_repo_slug() -> str:
    """Best-effort "owner/repo" for building the site's "View source on
    GitHub" link. GITHUB_REPOSITORY is set automatically by GitHub Actions;
    the git-remote fallback covers local runs. Returns "" (a harmless,
    non-fatal broken link) if neither source is available."""
    env_value = os.environ.get("GITHUB_REPOSITORY", "")
    if env_value:
        return env_value
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True, cwd=REPO_ROOT
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    url = result.stdout.strip()
    # Handles both "git@github.com:owner/repo.git" and
    # "https://github.com/owner/repo.git" remote URL forms.
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git")
    return ""


def _build_bulletin_fetcher(local_bulletin_url: str) -> BulletinFetcher:
    if not local_bulletin_url:
        return NullBulletinFetcher()
    # This repo currently ships exactly one reference bulletin
    # implementation (Kenya Meteorological Department). A fork with a
    # different local met service should replace this wiring with its own
    # BulletinFetcher — see fetch/bulletin/__init__.py's module docstring
    # for why this isn't auto-detected from the URL.
    return KenyaKMDBulletinFetcher(local_bulletin_url)


def _env(name: str, default: str = "") -> str:
    """os.environ.get(), but treats an EMPTY value as absent.

    Load-bearing under GitHub Actions: `FOO: ${{ vars.FOO }}` sets FOO to
    the empty string when that repository variable isn't defined, rather
    than leaving it unset. A plain os.environ.get(name, default) therefore
    returns "" instead of the default — which would have set GEMINI_MODEL
    to "" (a hard crash: GeminiProvider rejects an empty model id) and
    silently downgraded GEMINI_THINKING_LEVEL from the deliberately-chosen
    "high" to None on every scheduled run.
    """
    return os.environ.get(name, "").strip() or default


def _build_llm_provider(*, thinking_level: str | None = None):
    """Builds the configured LLMProvider from the environment.

    Secrets and endpoints come from env vars, never CLI args, for the same
    reason as everything else here — they'd otherwise land in shell history
    and process listings. `thinking_level` is Gemini-specific and simply
    ignored by other providers; check-health passes None because a factual
    model-lookup doesn't need extended reasoning.
    """
    provider_name = _env("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).lower()

    if provider_name == "gemini":
        api_key = _env("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("GEMINI_API_KEY environment variable is required (LLM_PROVIDER=gemini).")
        return GeminiProvider(
            api_key=api_key,
            model=_env("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            thinking_level=thinking_level,
        )

    if provider_name == "anthropic":
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL")
        if not api_key or not model:
            raise SystemExit(
                "LLM_PROVIDER=anthropic requires LLM_API_KEY and LLM_MODEL to be set. "
                "See QUICKSTART.md for recommended model ids."
            )
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            # Only needed for a proxy/gateway; defaults to api.anthropic.com.
            base_url=_env("LLM_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL),
            max_tokens=int(_env("LLM_MAX_TOKENS", str(DEFAULT_ANTHROPIC_MAX_TOKENS))),
        )

    if provider_name == "openai":
        base_url = _env("LLM_BASE_URL")
        model = _env("LLM_MODEL")
        if not base_url or not model:
            raise SystemExit(
                "LLM_PROVIDER=openai requires LLM_BASE_URL and LLM_MODEL to be set "
                "(plus LLM_API_KEY for any hosted endpoint). "
                "See QUICKSTART.md for per-service values."
            )
        return OpenAICompatProvider(
            # Empty is legitimate here: local runtimes like Ollama don't
            # need a key. Hosted endpoints will fail loudly on the first
            # call, which is clearer than guessing at intent up front.
            api_key=_env("LLM_API_KEY"),
            model=model,
            base_url=base_url,
            json_mode=_env("LLM_JSON_MODE", "json_schema"),
        )

    raise SystemExit(
        f"Unknown LLM_PROVIDER {provider_name!r} — expected one of {', '.join(VALID_LLM_PROVIDERS)}."
    )


def _build_pipeline_deps(config_path: str, data_dir: str, docs_dir: str, public_webpage_url: str) -> PipelineDeps:
    location = load_location_config(config_path)
    data_path = Path(data_dir)

    gemini_thinking_level = _env("GEMINI_THINKING_LEVEL", DEFAULT_GEMINI_THINKING_LEVEL) or None
    llm_provider = _build_llm_provider(thinking_level=gemini_thinking_level)
    waqi_token = _env("WAQI_TOKEN")

    # Publisher needs an absolute base URL to build sane nav links (see
    # publish/pages.py's module docstring) — skip it gracefully rather than
    # publish broken-relative-link pages if none was given, same "None =
    # skip" pattern pipeline.py already uses for publisher/email_sender.
    publisher = None
    if public_webpage_url:
        publisher = GitHubPagesPublisher(
            docs_dir=Path(docs_dir),
            location=location,
            base_url=public_webpage_url,
            github_repo=_github_repo_slug(),
            all_dates_provider=lambda: list_log_dates(data_path),
            entry_provider=make_log_lookup(data_path),
            review_provider=lambda: build_weekly_review(
                log_lookup=make_log_lookup(data_path),
                actuals=as_date_dict(read_actuals_cache(data_path).primary),
                all_log_dates=list_log_dates(data_path),
                today=today_in_tz(location.timezone),
            ),
        )

    # Gmail SMTP direct-send — see publish/email_gmail.py's module
    # docstring for why this path was chosen over a third-party ESP.
    # Skipped gracefully (same "None = skip" pattern as publisher above)
    # unless both credentials AND at least one recipient are configured.
    email_sender = None
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipients = parse_recipient_list(os.environ.get("SUBSCRIBER_EMAILS", ""))
    if gmail_address and gmail_app_password and recipients:
        email_sender = GmailSMTPSender(
            gmail_address=gmail_address,
            gmail_app_password=gmail_app_password,
            recipients=recipients,
            location_name=location.primary_place_name,
        )

    return PipelineDeps(
        location=location,
        data_dir=data_path,
        llm_provider=llm_provider,
        public_webpage_url=public_webpage_url,
        waqi_token=waqi_token,
        bulletin_fetcher=_build_bulletin_fetcher(location.local_bulletin_url),
        publisher=publisher,
        email_sender=email_sender,
    )


def _run_daily(args: argparse.Namespace) -> int:
    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)
    try:
        result = run_daily_pipeline(deps, dry_run=args.dry_run)
    except OpenMeteoFetchError as e:
        print(f"Critical Error: pipeline aborted, a required weather fetch failed: {e}", file=sys.stderr)
        return 1
    except LLMResponseError as e:
        print(f"Critical Error: pipeline aborted, the LLM call failed: {e}", file=sys.stderr)
        return 1

    entry = result.log_entry
    print(f"Pipeline run complete for {result.today} (dry_run={args.dry_run}).")
    print(f"  rain_expected:   {entry.rain_expected}")
    print(f"  temp:            {entry.temp_high_low_display}")
    print(f"  synoptic:        {entry.synoptic_pattern}")
    print(f"  newly_verified:  {result.newly_verified}")
    print(f"  published:       {result.published}")
    print(f"  emailed:         {result.emailed}")
    if args.dry_run:
        print("\n--- narrative preview (not written to data/, nothing published/emailed) ---\n")
        print(entry.narrative_markdown)
    return 0


def _run_refresh(args: argparse.Namespace) -> int:
    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)
    try:
        result = run_refresh_pipeline(deps, dry_run=args.dry_run)
    except RefreshWithoutMorningRunError as e:
        print(f"Critical Error: {e}", file=sys.stderr)
        return 1
    except OpenMeteoFetchError as e:
        print(f"Critical Error: refresh aborted, a required weather fetch failed: {e}", file=sys.stderr)
        return 1
    except LLMResponseError as e:
        print(f"Critical Error: refresh aborted, the LLM call failed: {e}", file=sys.stderr)
        return 1

    entry = result.log_entry
    print(f"Refresh run complete for {result.today} (dry_run={args.dry_run}).")
    print(f"  rain_expected:   {entry.rain_expected}")
    print(f"  temp:            {entry.temp_high_low_display}")
    print(f"  synoptic:        {entry.synoptic_pattern}")
    print(f"  refreshed_at:    {entry.meta.refreshed_at}")
    print(f"  published:       {result.published}")
    if args.dry_run:
        print("\n--- narrative preview (not written to data/, nothing published) ---\n")
        print(entry.narrative_markdown)
    return 0


def _days_since_last_commit() -> int:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    last_commit = datetime.fromtimestamp(int(result.stdout.strip()), tz=timezone.utc)
    return (datetime.now(timezone.utc) - last_commit).days


def _run_check_health(args: argparse.Namespace) -> int:
    # No thinking_level: the deprecation check is a factual lookup, not the
    # multi-step reasoning the forecast pipeline asks for.
    llm = _build_llm_provider(thinking_level=None)
    model_name = llm.model

    ok = True

    print(f"Checking whether '{model_name}' is listed as deprecated...")
    try:
        result = check_model_deprecation(llm, model_name)
    except LLMResponseError as e:
        print(f"  Could not complete the model-deprecation check: {e}", file=sys.stderr)
        ok = False
    else:
        if result.deprecated_or_scheduled:
            print(f"  WARNING: '{model_name}' may be deprecated or scheduled for shutdown.")
            print(f"  {result.notes}")
            ok = False
        else:
            print(f"  OK — {result.notes}")

    days = _days_since_last_commit()
    print(f"Days since last commit: {days}")
    if check_repo_staleness(days):
        print(
            f"  WARNING: last commit was {days} days ago. GitHub auto-disables scheduled "
            "workflows after 60 days of repo inactivity — investigate why daily.yml isn't "
            "running or isn't pushing."
        )
        ok = False
    else:
        print("  OK.")

    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="olw", description="Open Local Weather")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    run_daily = sub.add_parser("run-daily", help="Run the daily forecast pipeline.")
    run_daily.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    run_daily.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    run_daily.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR), help="Path to the docs/ (GitHub Pages) directory")
    run_daily.add_argument(
        "--public-url",
        default="",
        help="Public GitHub Pages URL. Included in the LLM prompt; also enables GitHub Pages publishing if set.",
    )
    run_daily.add_argument(
        "--dry-run", action="store_true", help="Run fetch/verify/LLM for real but skip writes, publish, and email."
    )

    refresh = sub.add_parser(
        "refresh-forecast",
        help="Optional same-day evening refresh — fresher narrative, morning's model_predictions preserved.",
    )
    refresh.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    refresh.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    refresh.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR), help="Path to the docs/ (GitHub Pages) directory")
    refresh.add_argument(
        "--public-url",
        default="",
        help="Public GitHub Pages URL. Included in the LLM prompt; also enables GitHub Pages publishing if set.",
    )
    refresh.add_argument(
        "--dry-run", action="store_true", help="Run fetch/LLM for real but skip writes and publish."
    )

    check_config = sub.add_parser("check-config", help="Load and validate a location.yaml, then exit.")
    check_config.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")

    sub.add_parser(
        "check-health",
        help="Weekly health checks: Gemini model deprecation status + repo staleness.",
    )

    args = parser.parse_args(argv)

    if args.command == "check-config":
        cfg = load_location_config(args.config)
        print(f"OK: {cfg.primary_place_name} ({cfg.region_name}), tz={cfg.timezone}")
        return 0

    if args.command == "run-daily":
        return _run_daily(args)

    if args.command == "refresh-forecast":
        return _run_refresh(args)

    if args.command == "check-health":
        return _run_check_health(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
