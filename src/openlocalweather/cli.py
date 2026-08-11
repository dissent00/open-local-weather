"""Command-line entrypoint.

This is a scaffolding stub — `run-daily` will be wired up to the full
pipeline in a later phase (see pipeline.py). For now it supports enough to
smoke-test the scaffolding: loading and validating the location config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openlocalweather import __version__
from openlocalweather.config import load_location_config

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "location.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="olw", description="Open Local Weather")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    run_daily = sub.add_parser("run-daily", help="Run the daily forecast pipeline (not yet implemented).")
    run_daily.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    run_daily.add_argument("--dry-run", action="store_true", help="Skip git commit/push and email-send.")

    check_config = sub.add_parser("check-config", help="Load and validate a location.yaml, then exit.")
    check_config.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")

    args = parser.parse_args(argv)

    if args.command == "check-config":
        cfg = load_location_config(args.config)
        print(f"OK: {cfg.primary_place_name} ({cfg.region_name}), tz={cfg.timezone}")
        return 0

    if args.command == "run-daily":
        print(
            "run-daily is not implemented yet — this is repo scaffolding. "
            "See pipeline.py once the verification/fetch/LLM modules land.",
            file=sys.stderr,
        )
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
