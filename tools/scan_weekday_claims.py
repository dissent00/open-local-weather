#!/usr/bin/env python3
"""Weekday/date pairings the published narratives assert, checked against the
calendar — every stored entry, every issuance.

WHY THIS EXISTS. The model was doing calendar arithmetic and getting it wrong,
and nothing anywhere noticed. `dates.forward_calendar` now hands the pairings
over finished so there is no arithmetic left to do, and `claims` checks each
run before it publishes. This is how you check whether that worked: re-run it
after a few weeks and the count should stay where it is, because the entries
below are history and cannot be unpublished.

IT SHARES THE CHECKER RATHER THAN CARRYING ITS OWN PATTERN, and that is not
tidiness. The first version of this file had its own regex, and it missed an
ordinal suffix — "Sunday, September 7th" — so it reported 4 false pairings when
there were 7, and undercounted the days affected from four to two. A second
implementation of a check is a second answer to the same question.

Read-only. Prints; writes nothing.

  python tools/scan_weekday_claims.py
"""

from __future__ import annotations

import glob
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather import claims  # noqa: E402


def _narratives(entry: dict):
    yield "current", entry.get("narrative_markdown") or ""
    for index, issuance in enumerate(entry.get("earlier_issuances") or []):
        yield f"earlier[{index}]", (issuance or {}).get("narrative_markdown") or ""


def _pairings(text: str) -> int:
    """How many pairings were asserted at all — the denominator."""
    return sum(
        len(pattern.findall(text))
        for pattern in (claims._DAY_FIRST, claims._MONTH_FIRST, claims._ISO)
    )


def main() -> int:
    asserted = 0
    findings: list[tuple[str, str, dict]] = []
    paths = sorted(glob.glob(str(ROOT / "data" / "log" / "*.json")))

    for path in paths:
        entry = json.loads(Path(path).read_text())
        today = date.fromisoformat(entry["date"])
        for which, text in _narratives(entry):
            asserted += _pairings(text)
            for finding in claims.false_weekday_claims(text, today):
                findings.append((entry["date"], which, finding))

    print(f"entries scanned: {len(paths)}")
    print(f"weekday/date pairings asserted: {asserted}")
    share = f" ({len(findings) / asserted * 100:.0f}%)" if asserted else ""
    print(f"FALSE: {len(findings)}{share}")
    for day, which, finding in findings:
        print(f"  {day}  {which:<12} {finding['quote']!r} — {finding['detail']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
