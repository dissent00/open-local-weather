"""Weekday/date pairings the published narratives assert, checked against the
calendar — every stored entry, every issuance.

WHY THIS EXISTS. The model was doing calendar arithmetic and getting it wrong,
and nothing anywhere noticed: run from data/log/ on 2026-09-13 it found 4 false
pairings out of 24, on 2026-08-11 and 2026-08-12, each off by exactly one day.
`dates.forward_calendar` now hands the pairings over finished so the model has
no arithmetic left to do. This is how you check whether that worked — re-run it
after a few weeks of forecasts and the count should stay at those 4, which are
history and cannot be unpublished.

Read-only. Prints; writes nothing.

  python tools/scan_weekday_claims.py
"""
import json, re, glob, datetime, sys

WEEKDAYS = ("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")
MONTHS = {m: i for i, m in enumerate(
    ("January","February","March","April","May","June","July","August",
     "September","October","November","December"), start=1)}

# "Monday (16 September)", "Monday, 16 September", "Monday 16 September"
PAIR = re.compile(
    rf"\b({'|'.join(WEEKDAYS)})\b[\s,(]+(\d{{1,2}})\s+({'|'.join(MONTHS)})\b")
# "Monday (September 16)"
PAIR_US = re.compile(
    rf"\b({'|'.join(WEEKDAYS)})\b[\s,(]+({'|'.join(MONTHS)})\s+(\d{{1,2}})\b")

def narratives(entry):
    yield "current", entry.get("narrative_markdown") or ""
    for i, iss in enumerate(entry.get("earlier_issuances") or []):
        yield f"earlier[{i}]", (iss or {}).get("narrative_markdown") or ""

checked = wrong = 0
findings = []
for path in sorted(glob.glob("data/log/*.json")):
    entry = json.load(open(path))
    year = int(entry["date"][:4])
    for which, text in narratives(entry):
        for rx, order in ((PAIR, "dm"), (PAIR_US, "md")):
            for m in rx.finditer(text):
                name = m.group(1)
                day, month = (m.group(2), m.group(3)) if order == "dm" else (m.group(3), m.group(2))
                try:
                    d = datetime.date(year, MONTHS[month], int(day))
                except ValueError:
                    continue
                checked += 1
                actual = WEEKDAYS[d.weekday()]
                if actual != name:
                    wrong += 1
                    findings.append((entry["date"], which, m.group(0).strip(), actual))

print(f"entries scanned: {len(glob.glob('data/log/*.json'))}")
print(f"weekday/date pairings asserted: {checked}")
print(f"WRONG: {wrong}")
for date, which, text, actual in findings:
    print(f"  {date} {which:<12} {text!r} -> {date[:4]} says {actual}")
