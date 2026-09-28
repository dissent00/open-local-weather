"""Fill in what each earlier issuance's snapshot did not keep — ROADMAP items
185 and 123.

WHY THIS EXISTS. An `IssuanceSnapshot` froze what a later run overwrites,
but not everything. Item 185: not the model, so an earlier issuance's page
read the LATEST run's `meta.llm_model`, and the 09-25 and 09-26 morning pages
credited nex-n2.5-pro for forecasts Gemini made. Item 123: not the tile's sky
and wind at the anchors, so an 18:01 run, with every anchor behind it,
erased what the morning tile had told a reader. Snapshots now keep all
three; this fills them in for the ones taken before.

THE SOURCE IS THE ENTRY AS THAT ISSUANCE'S OWN RUN COMMITTED IT. A snapshot's
`generated_at_utc` is the entry's `last_issued_at` at the moment it was
taken, so the commit of `data/log/<date>.json` whose `refreshed_at or
generated_at_utc` equals it holds the values the snapshot would have copied.
Where the prompt archive has the same issuance (from 2026-09-04), its
`llm_model` is the same field copied at the same moment, and is checked
against it. Nothing else records the tile words, so for those git is the
only witness.

A field is left alone when no commit has the snapshot's time, when two
commits with that time disagree, or when the archive disagrees. A tile
field is also left alone where the committed entry has no such key: that run
predates the tiles, and None says so. Each skip is reported.

Edits the JSON in place, adding keys only, so the diff is only what is new;
every log file round-trips byte for byte through `json.dumps(indent=2)`,
checked before this was first written.

Usage:
  python tools/backfill_snapshots.py          # report only
  python tools/backfill_snapshots.py --write  # and write
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "log"
PROMPTS = ROOT / "data" / "prompts"

# Snapshot stores, oldest shape first: one legacy morning, then the list.
LEGACY_KEY = "morning_issuance"
LIST_KEY = "earlier_issuances"

MODEL = "llm_model"
TILE_FIELDS = ("cloud_anchors", "wind_anchors")


def _instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _committed(entry: dict) -> dict[str, str]:
    """The fields a snapshot keeps, as this committed entry held them, each
    JSON-encoded so values can be compared as a set. A tile field the entry
    has no key for is absent: that run predates the tiles."""
    meta = entry.get("meta") or {}
    out = {}
    if meta.get(MODEL):
        out[MODEL] = json.dumps(meta[MODEL])
    for field in TILE_FIELDS:
        if field in entry:
            out[field] = json.dumps(entry[field], sort_keys=True)
    return out


def _history(path: Path) -> dict[datetime, dict[str, set[str]]]:
    """Every value each field has held, by the issuance that held it."""
    rel = path.relative_to(ROOT).as_posix()
    out: dict[datetime, dict[str, set[str]]] = {}
    for sha in _git("log", "--format=%H", "--", rel).split():
        entry = json.loads(_git("show", f"{sha}:{rel}"))
        meta = entry.get("meta") or {}
        stamp = meta.get("refreshed_at") or meta.get("generated_at_utc")
        if not stamp:
            continue
        seen = out.setdefault(_instant(stamp), {})
        for field, value in _committed(entry).items():
            seen.setdefault(field, set()).add(value)

    return out


def _archived(day: str) -> dict[datetime, str]:
    path = PROMPTS / f"{day}.json"
    if not path.exists():
        return {}

    return {_instant(i["issued_at"]): i["llm_model"] for i in json.loads(path.read_text())["issuances"]}


def _archive_model(archived: dict[datetime, str], at: datetime) -> str | None:
    # The archive stamps the same instant a few microseconds apart.
    near = [m for t, m in archived.items() if abs((t - at).total_seconds()) < 1]
    return near[0] if near else None


def main(argv: list[str]) -> int:
    write = "--write" in argv
    changed_files = 0
    filled = 0
    problems = 0

    for path in sorted(LOG.glob("*.json")):
        text = path.read_text()
        entry = json.loads(text)
        # A day can hold its first issuance in BOTH stores; each copy is filled.
        snapshots = ([(entry[LEGACY_KEY], " (legacy copy)")] if entry.get(LEGACY_KEY) else []) + [
            (s, "") for s in entry.get(LIST_KEY) or []
        ]
        pending = [(s, where) for s, where in snapshots if any(f not in s for f in (MODEL, *TILE_FIELDS))]
        if not pending:
            continue

        history = _history(path)
        archived = _archived(path.stem)
        day_model = (entry.get("meta") or {}).get(MODEL)
        touched = False

        for snap, where in pending:
            at = _instant(snap["generated_at_utc"])
            held = history.get(at, {})
            label = f"{path.stem} {at:%H:%M:%S}Z{where}"

            for field in (MODEL, *TILE_FIELDS):
                if field in snap:
                    continue
                values = held.get(field, set())
                if field in TILE_FIELDS and not values:
                    continue  # the run predates the tiles; None says so
                if len(values) != 1:
                    print(f"SKIP {label} {field}: committed {sorted(values) or 'none'}")
                    problems += 1
                    continue

                value = json.loads(next(iter(values)))
                if field == MODEL:
                    archive = _archive_model(archived, at)
                    if archive is not None and archive != value:
                        print(f"SKIP {label} {field}: committed {value}, prompt archive {archive}")
                        problems += 1
                        continue
                    flag = f"  <- the day's latest run is {day_model}" if value != day_model else ""
                    print(f"{label} {field}: {value} (archive {archive or '-'}){flag}")
                else:
                    words = [a.get("cover") or a.get("direction") for a in value]
                    print(f"{label} {field}: {words}")

                snap[field] = value
                filled += 1
                touched = True

        if write and touched:
            ensure_ascii = json.dumps(json.loads(text), indent=2, ensure_ascii=True) + "\n" == text
            path.write_text(json.dumps(entry, indent=2, ensure_ascii=ensure_ascii) + "\n")
            changed_files += 1

    verb = "wrote" if write else "would write"
    print(f"\n{verb} {filled} fields{f' in {changed_files} files' if write else ''}; {problems} skipped")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
