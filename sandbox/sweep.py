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
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, is_dataclass
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


DATA_DIR = Path(__file__).resolve().parent / "data"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _plain(value):
    """JSON-able, without importing a serialiser for three shapes."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if hasattr(value, "model_dump"):
        return {k: _plain(v) for k, v in value.model_dump().items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return str(value)


def store(row: dict) -> Path:
    """One file per location per day.

    THE PREDICTIONS ARE THE POINT, not the labels. Labels can be recomputed
    from the record at any time and are written only so that a change to them
    is visible as a diff; the per-model predictions and the observations
    cannot be recovered later at all, because Open-Meteo's forecast endpoint
    only ever answers about now. A day not stored is a day gone.

    Deliberately NOT data/: that directory is the production record, and a
    sandbox location appearing in it would put twelve places' weather into
    one location's accuracy page.
    """
    loc = row["location"]
    today = row["local_today"]
    out = DATA_DIR / slug(loc.name) / f"{today.isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "date": today.isoformat(),
        "location": {"name": loc.name, "lat": loc.lat, "lon": loc.lon,
                     "timezone": loc.timezone, "icao": loc.icao},
        "predictions": {k: _plain(row.get(k)) for k in ("day0", "day3", "day7")},
        "yesterday_observed": _plain(row.get("yesterday")),
        "labels": {
            "comparison": _plain(row.get("comparison")),
            "extended_trend": row.get("trend"),
            "instability": _plain(row.get("instability")),
            "wind_warning": row.get("wind_warning"),
        },
    }, indent=2, sort_keys=True) + "\n")

    return out


def _mean(values):
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def observe(loc: SandboxLocation) -> dict:
    """Everything for one location. Raises nothing — a location that fails to
    fetch reports the failure and the sweep continues, because one bad
    endpoint must not cost the other eleven.

    THE DATE COMES FROM THE DATA, NOT THE CLOCK. Open-Meteo is asked in the
    location's own timezone and answers about the location's own today, so a
    sweep run at 22:00 UTC gets Wellington's TOMORROW while the runner still
    says today. Naming the stored file from `date.today()` would file that
    forecast under the wrong day, and three days later it would be scored
    against the wrong observations — a corruption invisible until the
    accuracy figures were already wrong. Latent rather than theoretical: the
    two agreed at 08:15 UTC and diverge either side of it.
    """
    out: dict = {"location": loc, "error": None}
    try:
        hourly = fetch_forecast_hourly_today(loc.lat, loc.lon, MODELS, loc.timezone)
        daily = fetch_forecast_daily_extended(loc.lat, loc.lon, MODELS, loc.timezone)
        local_today = date.fromisoformat(hourly["hourly"]["time"][0][:10])
        yesterday = local_today - timedelta(days=1)
        archive = fetch_archive_range(loc.lat, loc.lon, yesterday, yesterday, loc.timezone)
    except Exception as exc:  # noqa: BLE001 — reporting tool, not a library
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["local_today"] = local_today

    day0 = extract_day0_predictions_from_hourly(hourly, MODELS)
    actuals = bucket_hourly_by_date(archive)
    out["yesterday"] = actuals.get(yesterday)
    out["instability"] = summarize_instability(hourly, MODELS)
    out["comparison"] = compute_day_over_day(
        out["yesterday"], day0,
        today_convective=out["instability"].convective if out["instability"] else None,
    )

    extended = [extract_day_n_predictions_from_daily(daily, n, MODELS) for n in (1, 2, 3)]
    # Day+3 and Day+7 are stored so that in three and seven days there is
    # something to score. They are not used by any label today.
    out["day0"] = day0
    out["day3"] = extract_day_n_predictions_from_daily(daily, 3, MODELS)
    out["day7"] = extract_day_n_predictions_from_daily(daily, 7, MODELS)
    out["trend"] = describe_extended_trend(
        _mean([p.high_c for p in day0]),
        [_mean([p.high_c for p in d]) for d in extended],
        [_mean([p.precip_mm for p in d]) for d in extended],
        weekday_name(local_today + timedelta(days=3)),
        today_wind_kmh=_mean([p.wind_kmh for p in day0]),
        day_winds_kmh=[_mean([p.wind_kmh for p in d]) for d in extended],
    )
    out["consensus_gust"] = _mean([p.wind_kmh for p in day0])
    out["wind_warning"] = wind_warning(out["consensus_gust"])
    out["high_delta"] = out["comparison"].high_delta_c if out["comparison"] else None
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locations", nargs="*", help="names to include; default all")
    parser.add_argument("--store", action="store_true",
                        help="write each location's predictions and observations under sandbox/data/")
    args = parser.parse_args()

    fleet = FLEET
    if args.locations:
        wanted = {n.lower() for n in args.locations}
        fleet = tuple(loc for loc in FLEET if loc.name.lower() in wanted)

    fired: dict[str, list[str]] = defaultdict(list)
    print(f"Sweeping {len(fleet)} locations; each date is the LOCATION's, not the runner's\n")

    for loc in fleet:
        row = observe(loc)
        if row["error"]:
            print(f"  {loc.name:14} FETCH FAILED — {row['error']}")
            continue
        if args.store:
            store(row)
        cmp_ = row["comparison"]
        if cmp_ is None:
            print(f"  {loc.name:14} no observed record for yesterday")
            continue

        for label, value in (("high_label", cmp_.high_label), ("wind_label", cmp_.wind_label)):
            if value:
                fired[f"{label}: {value}"].append(loc.name)
        fired[f"wind_warning: {row['wind_warning'] or 'none'}"].append(loc.name)
        if row["instability"]:
            fired[f"convective: {row['instability'].convective}"].append(loc.name)
        if row["trend"]:
            fired["trend: " + row["trend"].split(" through ")[0]].append(loc.name)

        print(f"  {loc.name:14} {row['local_today']} {str(row['consensus_gust'] and round(row['consensus_gust'],1)):>6} km/h | "
              f"dT {str(row['high_delta']):>6} | {cmp_.overview_comparison}")

    print("\n--- which code paths fired, and where ---")
    for key in sorted(fired):
        names = fired[key]
        mark = "" if "Kisumu" in names else "   <- NEVER at the deployment"
        print(f"  {key:44} {len(names):>2}  {', '.join(names)}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
