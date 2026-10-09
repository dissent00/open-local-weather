"""The brief's size over the archive — ROADMAP item 191's first measurement.

    .venv/bin/python tools/measure_brief.py [--out DIR]

For every archived day that also has a stored entry: parse the brief as
`olw write-up` will, render the full and mini tiers for the deployment's
sections, and report characters and tokens (at the measured 2.5
characters per token) at p50, p95 and max. With --out, write each render
beside its date for reading.
"""

import argparse
import json
import statistics
from datetime import date
from pathlib import Path

from openlocalweather.brief import TIER_FULL, TIER_MINI, BriefInputs, render_brief
from openlocalweather.config import load_location_config
from openlocalweather.store.log_store import read_log_entry

ROOT = Path(__file__).resolve().parent.parent
CHARS_PER_TOKEN = 2.5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    location = load_location_config(ROOT / "config" / "location.yaml")
    secondary = location.secondary_point
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    sizes = {TIER_FULL: [], TIER_MINI: []}
    days = 0
    for archive in sorted((ROOT / "data" / "prompts").glob("*.json")):
        day = date.fromisoformat(archive.stem)
        entry = read_log_entry(ROOT / "data", day)
        if entry is None:
            continue
        issuance = json.loads(archive.read_text())["issuances"][-1]
        inputs = BriefInputs.from_user_prompt(
            issuance["user_prompt"], entry,
            secondary_name=secondary.name if secondary.enabled and secondary.name else None,
            met_service_name=location.local_bulletin_source_name or None,
            met_service_model_id=location.local_bulletin_model_id or None,
        )
        days += 1
        for tier in (TIER_FULL, TIER_MINI):
            text = render_brief(inputs, tier=tier, sections=location.write_up_sections)
            sizes[tier].append(len(text))
            if out:
                (out / f"{day}.{tier}.txt").write_text(text)

    print(f"{days} days, sections {location.write_up_sections}")
    for tier, values in sizes.items():
        values = sorted(values)
        p50 = statistics.median(values)
        p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
        print(
            f"  {tier}: p50 {p50:,.0f} chars (~{p50 / CHARS_PER_TOKEN:,.0f} tokens), "
            f"p95 {p95:,} (~{p95 / CHARS_PER_TOKEN:,.0f}), max {values[-1]:,} (~{values[-1] / CHARS_PER_TOKEN:,.0f})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
