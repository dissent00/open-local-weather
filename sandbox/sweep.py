#!/usr/bin/env python3
"""Run the deterministic labels against a fleet, and report what fired.

See sandbox/README.md. No LLM, no key, nothing written.

WHAT THIS IS FOR. Every band and branch in comparison.py is exercised here
only by whatever Kisumu's weather happens to produce, which is mild and
consistent. Four defects this year survived a green suite for exactly that
reason. This asks a different question from a test: not "is the output what we
pinned" but "has this code path ever been reached at all".
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from locations import FLEET, SandboxLocation  # noqa: E402

from openlocalweather.comparison import (  # noqa: E402
    compute_day_over_day,
    describe_extended_trend,
    wind_warning,
)
from openlocalweather.dates import weekday_name  # noqa: E402
from openlocalweather.defaults import MODELS  # noqa: E402
from openlocalweather.extract import (  # noqa: E402
    extract_day0_predictions_from_hourly,
    extract_day_n_predictions_from_daily,
)
from openlocalweather.fetch.open_meteo import (  # noqa: E402
    fetch_archive_range,
    fetch_forecast_daily_extended,
    fetch_forecast_hourly_today,
)
from openlocalweather.fetch.open_meteo import bucket_hourly_by_date  # noqa: E402
from openlocalweather.instability import summarize_instability  # noqa: E402
from openlocalweather.verify.scoring import mean  # noqa: E402


def _mean(values):
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def observe(loc: SandboxLocation, today: date) -> dict:
    """Everything for one location. Raises nothing — a location that fails to
    fetch reports the failure and the sweep continues, because one bad
    endpoint must not cost the other eleven."""
    out: dict = {"location": loc, "error": None}
    try:
        hourly = fetch_forecast_hourly_today(loc.lat, loc.lon, MODELS, loc.timezone)
        daily = fetch_forecast_daily_extended(loc.lat, loc.lon, MODELS, loc.timezone)
        yesterday = today - timedelta(days=1)
        archive = fetch_archive_range(loc.lat, loc.lon, yesterday, yesterday, loc.timezone)
    except Exception as exc:  # noqa: BLE001 — reporting tool, not a library
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    day0 = extract_day0_predictions_from_hourly(hourly, MODELS)
    actuals = bucket_hourly_by_date(archive)
    out["yesterday"] = actuals.get(today - timedelta(days=1))
    out["instability"] = summarize_instability(hourly, MODELS)
    out["comparison"] = compute_day_over_day(
        out["yesterday"], day0,
        today_convective=out["instability"].convective if out["instability"] else None,
    )

    extended = [extract_day_n_predictions_from_daily(daily, n, MODELS) for n in (1, 2, 3)]
    out["trend"] = describe_extended_trend(
        _mean([p.high_c for p in day0]),
        [_mean([p.high_c for p in d]) for d in extended],
        [_mean([p.precip_mm for p in d]) for d in extended],
        weekday_name(today + timedelta(days=3)),
        today_wind_kmh=_mean([p.wind_kmh for p in day0]),
        day_winds_kmh=[_mean([p.wind_kmh for p in d]) for d in extended],
    )
    out["consensus_gust"] = _mean([p.wind_kmh for p in day0])
    out["high_delta"] = out["comparison"].high_delta_c if out["comparison"] else None
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locations", nargs="*", help="names to include; default all")
    args = parser.parse_args()

    fleet = FLEET
    if args.locations:
        wanted = {n.lower() for n in args.locations}
        fleet = tuple(loc for loc in FLEET if loc.name.lower() in wanted)

    today = date.today()
    fired: dict[str, list[str]] = defaultdict(list)
    print(f"Sweeping {len(fleet)} locations for {today}\n")

    for loc in fleet:
        row = observe(loc, today)
        if row["error"]:
            print(f"  {loc.name:14} FETCH FAILED — {row['error']}")
            continue
        cmp_ = row["comparison"]
        if cmp_ is None:
            print(f"  {loc.name:14} no observed record for yesterday")
            continue

        for label, value in (("high_label", cmp_.high_label), ("wind_label", cmp_.wind_label)):
            if value:
                fired[f"{label}: {value}"].append(loc.name)
        warn = wind_warning(row["consensus_gust"])
        fired[f"wind_warning: {warn or 'none'}"].append(loc.name)
        if row["instability"]:
            fired[f"convective: {row['instability'].convective}"].append(loc.name)
        if row["trend"]:
            fired["trend: " + row["trend"].split(" through ")[0]].append(loc.name)

        print(f"  {loc.name:14} {str(row['consensus_gust'] and round(row['consensus_gust'],1)):>6} km/h gust | "
              f"dT {str(row['high_delta']):>6} | {cmp_.overview_comparison}")

    print("\n--- which code paths fired, and where ---")
    for key in sorted(fired):
        names = fired[key]
        mark = "" if "Kisumu" in names else "   <- NEVER at the deployment"
        print(f"  {key:44} {len(names):>2}  {', '.join(names)}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
