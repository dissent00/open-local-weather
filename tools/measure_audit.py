"""The audit against the record — ROADMAP item 191 step (b)'s measurement.

    .venv/bin/python tools/measure_audit.py

For every stored Gemini write-up with an archived prompt: split it into the
page's sections, render that day's brief, audit each section as the writer
will be audited, and count the rejections and their reasons. Those texts
were written from the old 122K-character prompt, so a figure the brief does
not carry is a rejection here and an intended one: the point of the brief is
that the writer sees only what the page shows. Then 60 hand-mutated figures
over the passing sections: the false-accept rate the number check must hold
at zero.
"""

import collections
import json
import random
import re
from datetime import date
from pathlib import Path

from openlocalweather.brief import SECTIONS, BriefInputs, render_brief
from openlocalweather.config import load_location_config
from openlocalweather.store.log_store import read_log_entry
from openlocalweather.writer import audit_section

ROOT = Path(__file__).resolve().parent.parent
HEADINGS = {
    "## Today's Forecast": "today", "## Extended Outlook": "extended",
    "## Severe Weather / Hazard Potential": "severe", "## Winam Gulf — Conditions for Boaters": "secondary",
    "## Lake Victoria — Conditions for Boaters": "secondary", "### Synoptic Overview": "synoptic",
    "### Forecaster Confidence Notes": "confidence",
}
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _sections(markdown: str) -> dict[str, str]:
    out, current = {}, None
    for line in markdown.splitlines():
        if line.strip() in HEADINGS:
            current = HEADINGS[line.strip()]
            out[current] = []
        elif line.startswith("#"):
            current = None
        elif current:
            out[current].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items() if "\n".join(v).strip()}


def main() -> int:
    location = load_location_config(ROOT / "config" / "location.yaml")
    secondary = location.secondary_point
    totals = collections.Counter()
    rejected = collections.Counter()
    reasons = collections.Counter()
    passing: list[tuple[str, str, str, BriefInputs]] = []
    days = 0
    for archive in sorted((ROOT / "data" / "prompts").glob("*.json")):
        day = date.fromisoformat(archive.stem)
        entry = read_log_entry(ROOT / "data", day)
        if entry is None or "gemini" not in str(entry.meta.narrative_llm_model or entry.meta.llm_model or ""):
            continue
        if entry.narrative_source == "code" or len(entry.narrative_markdown or "") < 1500:
            continue
        issuance = json.loads(archive.read_text())["issuances"][-1]
        inputs = BriefInputs.from_user_prompt(
            issuance["user_prompt"], entry,
            secondary_name=secondary.name if secondary.enabled and secondary.name else None,
            met_service_name=location.local_bulletin_source_name or None,
            met_service_model_id=location.local_bulletin_model_id or None,
        )
        brief = render_brief(inputs, sections=SECTIONS)
        days += 1
        for section, text in _sections(entry.narrative_markdown).items():
            totals[section] += 1
            defects = audit_section(section, text, brief, inputs)
            if defects:
                rejected[section] += 1
                for d in defects:
                    reasons[(section, d.split(":")[0])] += 1
            else:
                passing.append((str(day), section, text, inputs))

    print(f"{days} Gemini days with an archived prompt")
    print("section      audited  rejected")
    for s in SECTIONS:
        if totals[s]:
            print(f"  {s:11s} {totals[s]:6d}  {rejected[s]:6d}")
    print("reasons (section, kind): count")
    for (s, kind), n in sorted(reasons.items(), key=lambda kv: -kv[1])[:14]:
        print(f"  {s:11s} {kind:40s} {n}")

    # Hand-mutated figures over passing sections: change one number by a
    # unit or a tenth and see that the audit catches it.
    rng = random.Random(191)
    caught = tried = 0
    for day, section, text, inputs in passing:
        # Real figures only: not the digits of a time or a date, and not a
        # single digit, which is in every brief somewhere.
        numbers = [
            m.group(0) for m in _NUMBER.finditer(text)
            if float(m.group(0).replace(",", "")) >= 10
            and text[max(0, m.start() - 1):m.start()] not in (":", "-")
            and text[m.end():m.end() + 1] not in (":", "-")
        ]
        if not numbers or tried >= 60:
            continue
        token = rng.choice(numbers)
        value = float(token.replace(",", ""))
        bumped = value + (0.1 if "." in token else 1)
        mutated = text.replace(token, f"{bumped:.1f}" if "." in token else str(int(bumped)), 1)
        brief = render_brief(inputs, sections=SECTIONS)
        tried += 1
        if any(d.startswith("figures not in the brief") for d in audit_section(section, mutated, brief, inputs)):
            caught += 1
    print(f"mutated figures caught: {caught} of {tried}")
    print("three rejected sections, the stray figures named:")
    shown = 0
    for archive in sorted((ROOT / "data" / "prompts").glob("*.json"))[-4:]:
        day = date.fromisoformat(archive.stem)
        entry = read_log_entry(ROOT / "data", day)
        if entry is None or entry.narrative_source == "code" or len(entry.narrative_markdown or "") < 1500:
            continue
        issuance = json.loads(archive.read_text())["issuances"][-1]
        inputs = BriefInputs.from_user_prompt(issuance["user_prompt"], entry, secondary_name=secondary.name, met_service_name=location.local_bulletin_source_name or None, met_service_model_id=location.local_bulletin_model_id or None)
        brief = render_brief(inputs, sections=SECTIONS)
        for section, text in _sections(entry.narrative_markdown).items():
            defects = [d for d in audit_section(section, text, brief, inputs) if d.startswith("figures")]
            if defects and shown < 3:
                shown += 1
                print(f"  {day} {section}: {defects[0][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
