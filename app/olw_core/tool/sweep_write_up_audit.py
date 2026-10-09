"""Cases and Python's answers for sweep_write_up_audit.dart — the write-up
gate, 2026-10-09.

    .venv/bin/python app/olw_core/tool/sweep_write_up_audit.py /tmp/cases.json /tmp/want.json
    (cd app/olw_core && dart run tool/sweep_write_up_audit.dart /tmp/cases.json /tmp/want.json)

Random texts over three heading sets: headings dropped, shuffled, repeated,
written inline or with the prompt's extra ones; bodies present, blank or
whitespace-only; every line separator Python's splitlines() knows and the
whitespace str.strip() knows at the ends of heading lines, with the BOM and
U+001F at the edges where Dart's own trim and LineSplitter would differ.
"""

import json
import random
import sys

from openlocalweather.write_up import audit_write_up

HEADINGS = [
    "## Today's Forecast", "## Extended Outlook", "## Severe Weather / Hazard Potential",
    "## Winam Gulf — Conditions for Boaters", "## Detailed Discussion",
    "### Synoptic Overview", "### Forecaster Confidence Notes",
]
SETS = [HEADINGS, [h for h in HEADINGS if "Boaters" not in h], [h.replace("Winam Gulf", "Lake Victoria") for h in HEADINGS]]
SEPARATORS = ["\n", "\n", "\n", "\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]
EDGES = ["", "", " ", "  ", "\t", "\xa0", "　", "\x1f", "﻿", " "]
EXTRA = ["## Overview", "#### Deep", "# Title", "##Nospace", "## Today's Forecast"]
BODIES = ["Written.", "Written.", "Two lines\nhere.", "", " ", "\xa0", "\x1f", "- a list", "## not a heading? yes it is"]
CASES = 3_000


def _clean(rng: random.Random, asked: list[str]) -> dict:
    """Every heading, in order, with a body; only the separators and the
    benign edges vary, so the pass path and the blank-body path are swept."""
    lines: list[str] = []
    for heading in asked:
        lines.append(rng.choice([" ", "\t", "\xa0", ""]) + heading + rng.choice(["", " ", "\u3000"]))
        lines.append(rng.choice(["Written.", "Written.", "Two lines\nhere.", "Written.", rng.choice([" ", "", "\x1f"])]))
    text = "".join(line + rng.choice(SEPARATORS) for line in lines)
    return {"markdown": text, "headings": asked}


def _case(rng: random.Random) -> dict:
    asked = rng.choice(SETS)
    if rng.random() < 0.4:
        return _clean(rng, asked)
    present = [h for h in asked if rng.random() < 0.85]
    if rng.random() < 0.3:
        rng.shuffle(present)
    lines: list[str] = []
    for heading in present:
        if rng.random() < 0.1:
            lines.append(heading + "n" + rng.choice(BODIES))
            continue
        lines.append(rng.choice(EDGES) + heading + rng.choice(EDGES))
        if rng.random() < 0.8:
            lines.append(rng.choice(BODIES))
        if rng.random() < 0.15:
            lines.insert(rng.randint(0, len(lines)), rng.choice(EXTRA))
    text = "".join(line + rng.choice(SEPARATORS) for line in lines)
    if rng.random() < 0.3:
        text = text.rstrip("\n")
    return {"markdown": text, "headings": asked}


def main() -> None:
    rng = random.Random(20261009)
    cases = [_case(rng) for _ in range(CASES)]
    want = [audit_write_up(c["markdown"], c["headings"]) for c in cases]
    with open(sys.argv[1], "w") as f:
        json.dump(cases, f, ensure_ascii=False)
    with open(sys.argv[2], "w") as f:
        json.dump(want, f, ensure_ascii=False)
    kinds = {k: sum(1 for w in want if any(d.startswith(k) for d in w)) for k in ("missing", "out of order", "empty")}
    print(f"wrote {len(cases)} cases; {sum(1 for w in want if not w)} pass; with a defect of each kind: {kinds}")


if __name__ == "__main__":
    main()
