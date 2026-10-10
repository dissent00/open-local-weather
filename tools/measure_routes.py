"""What the ledger says about each route — ROADMAP item 191 step (d).

    .venv/bin/python tools/measure_routes.py [--since YYYY-MM-DD] [--data-dir data]

Two readings, no calls spent. First, per purpose and model: how many
requests left, how many came back served, what the audit said of them, and
how long they took. Second, the SIZE-BLINDNESS question item 191 asked:
whether a small call lowers a vendor's own refusals or only opens other
routes. Since item 189 every morning sends the same model two requests
minutes apart on one key — the 56K-token judgment, then the brief (about
6K tokens with the writer prompt, since 2026-10-10) — so each morning is a
paired observation and the pairs answer it with nothing to build. Days
before 2026-10-10 sent the 122K-character prompt as the write-up and are
the baseline, not the measurement.
"""

import argparse
import collections
import statistics
from datetime import date, timedelta

from openlocalweather.spend import read_ledger

SERVED = "http_200"
BRIEF_FROM = date(2026, 10, 10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(days=14))
    args = parser.parse_args()
    rows = [r for r in read_ledger(args.data_dir) if r.at.date() >= args.since]
    if not rows:
        print(f"No ledger rows since {args.since}.")
        return 0
    print(f"{len(rows)} requests from {rows[0].at.date()} to {rows[-1].at.date()}\n")

    print("PER PURPOSE AND MODEL")
    by_key = collections.defaultdict(list)
    for r in rows:
        by_key[(r.purpose, r.model)].append(r)
    for (purpose, model), group in sorted(by_key.items()):
        served = [r for r in group if r.outcome == SERVED]
        outcomes = collections.Counter(r.outcome or "no reply" for r in group if r.outcome != SERVED)
        audits = collections.Counter(
            "passed" if (r.audit or "").startswith("passed") else "refused" for r in served if r.audit
        )
        elapsed = [r.elapsed_s for r in served if r.elapsed_s is not None]
        line = f"  {purpose:<9} {model:<34} asked {len(group):>3}  served {len(served):>3}"
        if audits:
            line += f"  audit passed {audits['passed']} refused {audits['refused']}"
        if elapsed:
            line += f"  median {statistics.median(elapsed):.0f} s"
        if outcomes:
            line += "  " + ", ".join(f"{k} ×{v}" for k, v in sorted(outcomes.items()))
        print(line)

    print("\nSIZE-BLINDNESS: per model per day, the judgment (one request per link) against")
    print("  the write-up (served if any of the day's requests was; the count follows)")
    print("  (the write-up is the brief from 2026-10-10; earlier days are the baseline)")
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[(r.at.date(), r.model, r.purpose)].append(r)
    pairs = collections.Counter()
    for d in sorted({d for d, _, _ in by_day}):
        for model in sorted({m for dd, m, _ in by_day if dd == d}):
            judgment = by_day.get((d, model, "forecast"))
            write_up = by_day.get((d, model, "write-up"))
            if not judgment or not write_up:
                continue
            period = "brief" if d >= BRIEF_FROM else "baseline"
            j = "served" if any(r.outcome == SERVED for r in judgment) else "refused"
            w = "served" if any(r.outcome == SERVED for r in write_up) else "refused"
            pairs[(period, model, j, w)] += 1
            print(f"  {d} {model:<26} judgment {j:<7} write-up {w:<7} in {len(write_up)} ({period})")
    if not pairs:
        print("  no day with both requests on one model yet")
        return 0
    print("\n  period    model                       judgment  write-up  days")
    for (period, model, j, w), n in sorted(pairs.items()):
        print(f"  {period:<9} {model:<26} {j:<9} {w:<9} {n}")
    print("\n  Read: write-up served where the judgment was refused, counted over the brief period,\n"
          "  is the small call's own gain; the baseline says what the big write-up did on the same mornings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
