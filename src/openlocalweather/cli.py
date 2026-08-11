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
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd import KenyaKMDBulletinFetcher
from openlocalweather.fetch.open_meteo import OpenMeteoFetchError
from openlocalweather.health_check import check_model_deprecation, check_repo_staleness
from openlocalweather.llm.gemini import GeminiProvider, LLMResponseError
from openlocalweather.pipeline import PipelineDeps, run_daily_pipeline
from openlocalweather.publish.pages import GitHubPagesPublisher
from openlocalweather.store.log_store import list_log_dates

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "location.yaml"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"


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


def _build_pipeline_deps(config_path: str, data_dir: str, docs_dir: str, public_webpage_url: str) -> PipelineDeps:
    location = load_location_config(config_path)
    data_path = Path(data_dir)

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        raise SystemExit("GEMINI_API_KEY environment variable is required for run-daily.")
    gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    waqi_token = os.environ.get("WAQI_TOKEN", "")

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
        )

    return PipelineDeps(
        location=location,
        data_dir=data_path,
        llm_provider=GeminiProvider(api_key=gemini_api_key, model=gemini_model),
        public_webpage_url=public_webpage_url,
        waqi_token=waqi_token,
        bulletin_fetcher=_build_bulletin_fetcher(location.local_bulletin_url),
        publisher=publisher,
        # email_sender stays unset until publish.email_brevo lands —
        # run_daily_pipeline treats it being None as "skip that step".
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
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        raise SystemExit("GEMINI_API_KEY environment variable is required for check-health.")
    gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    llm = GeminiProvider(api_key=gemini_api_key, model=gemini_model)

    ok = True

    print(f"Checking whether '{gemini_model}' is listed on Gemini's deprecations page...")
    try:
        result = check_model_deprecation(llm, gemini_model)
    except LLMResponseError as e:
        print(f"  Could not complete the model-deprecation check: {e}", file=sys.stderr)
        ok = False
    else:
        if result.deprecated_or_scheduled:
            print(f"  WARNING: '{gemini_model}' may be deprecated or scheduled for shutdown.")
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

    if args.command == "check-health":
        return _run_check_health(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
