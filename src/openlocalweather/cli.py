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
import sys
from pathlib import Path

from openlocalweather import __version__
from openlocalweather.config import load_location_config
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd import KenyaKMDBulletinFetcher
from openlocalweather.fetch.open_meteo import OpenMeteoFetchError
from openlocalweather.llm.gemini import GeminiProvider, LLMResponseError
from openlocalweather.pipeline import PipelineDeps, run_daily_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "location.yaml"
DEFAULT_DATA_DIR = REPO_ROOT / "data"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _build_bulletin_fetcher(local_bulletin_url: str) -> BulletinFetcher:
    if not local_bulletin_url:
        return NullBulletinFetcher()
    # This repo currently ships exactly one reference bulletin
    # implementation (Kenya Meteorological Department). A fork with a
    # different local met service should replace this wiring with its own
    # BulletinFetcher — see fetch/bulletin/__init__.py's module docstring
    # for why this isn't auto-detected from the URL.
    return KenyaKMDBulletinFetcher(local_bulletin_url)


def _build_pipeline_deps(config_path: str, data_dir: str, public_webpage_url: str) -> PipelineDeps:
    location = load_location_config(config_path)

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        raise SystemExit("GEMINI_API_KEY environment variable is required for run-daily.")
    gemini_model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    waqi_token = os.environ.get("WAQI_TOKEN", "")

    return PipelineDeps(
        location=location,
        data_dir=Path(data_dir),
        llm_provider=GeminiProvider(api_key=gemini_api_key, model=gemini_model),
        public_webpage_url=public_webpage_url,
        waqi_token=waqi_token,
        bulletin_fetcher=_build_bulletin_fetcher(location.local_bulletin_url),
        # publisher / email_sender stay unset until publish.pages /
        # publish.email_brevo land (later phases) — run_daily_pipeline
        # treats either being None as "skip that step", not an error.
    )


def _run_daily(args: argparse.Namespace) -> int:
    deps = _build_pipeline_deps(args.config, args.data_dir, args.public_url)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="olw", description="Open Local Weather")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    run_daily = sub.add_parser("run-daily", help="Run the daily forecast pipeline.")
    run_daily.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    run_daily.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    run_daily.add_argument("--public-url", default="", help="Public GitHub Pages URL, included in the LLM prompt")
    run_daily.add_argument(
        "--dry-run", action="store_true", help="Run fetch/verify/LLM for real but skip writes, publish, and email."
    )

    check_config = sub.add_parser("check-config", help="Load and validate a location.yaml, then exit.")
    check_config.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")

    args = parser.parse_args(argv)

    if args.command == "check-config":
        cfg = load_location_config(args.config)
        print(f"OK: {cfg.primary_place_name} ({cfg.region_name}), tz={cfg.timezone}")
        return 0

    if args.command == "run-daily":
        return _run_daily(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
