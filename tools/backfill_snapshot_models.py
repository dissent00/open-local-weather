"""Record which model made each earlier issuance — ROADMAP item 185.

WHY THIS EXISTS. An `IssuanceSnapshot` froze everything a later run
overwrites except the model, so an earlier issuance's page read the LATEST
run's `meta.llm_model`. The 09-25 and 09-26 morning pages credited
nex-n2.5-pro for forecasts Gemini made. Snapshots now carry `llm_model`;
this fills it in for the ones taken before.

THE SOURCE IS THE ENTRY AS THAT ISSUANCE'S OWN RUN COMMITTED IT. A snapshot's
`generated_at_utc` is the entry's `last_issued_at` at the moment it was
taken, so the commit of `data/log/<date>.json` whose `refreshed_at or
generated_at_utc` equals it holds the `meta.llm_model` the snapshot would
have copied. Where the prompt archive has the same issuance (from
2026-09-04), its `llm_model` is the same field copied at the same moment,
and is checked against it.

A snapshot is left alone when no commit has its time, when two commits with
its time disagree, or when the archive disagrees. Each is reported.

Edits the JSON in place, adding one key per snapshot, so the diff is only
what is new; every log file round-trips byte for byte through
`json.dumps(indent=2)`, checked before this was written.

Usage:
  python tools/backfill_snapshot_models.py          # report only
  python tools/backfill_snapshot_models.py --write  # and write
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


def _instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _models_by_issuance(path: Path) -> dict[datetime, set[str]]:
    """Every (last issued at -> meta.llm_model) this file has ever held."""
    rel = path.relative_to(ROOT).as_posix()
    out: dict[datetime, set[str]] = {}
    for sha in _git("log", "--format=%H", "--", rel).split():
        meta = json.loads(_git("show", f"{sha}:{rel}")).get("meta") or {}
        stamp = meta.get("refreshed_at") or meta.get("generated_at_utc")
        if stamp and meta.get("llm_model"):
            out.setdefault(_instant(stamp), set()).add(meta["llm_model"])

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
    problems = 0

    for path in sorted(LOG.glob("*.json")):
        text = path.read_text()
        entry = json.loads(text)
        # A day can hold its first issuance in BOTH stores; each copy is filled.
        snapshots = ([(entry[LEGACY_KEY], " (legacy copy)")] if entry.get(LEGACY_KEY) else []) + [
            (s, "") for s in entry.get(LIST_KEY) or []
        ]
        pending = [(s, where) for s, where in snapshots if "llm_model" not in s]
        if not pending:
            continue

        history = _models_by_issuance(path)
        archived = _archived(path.stem)
        day_model = (entry.get("meta") or {}).get("llm_model")

        for snap, where in pending:
            at = _instant(snap["generated_at_utc"])
            models = history.get(at, set())
            archive = _archive_model(archived, at)
            label = f"{path.stem} {at:%H:%M:%S}Z{where}"

            if len(models) != 1:
                print(f"SKIP {label}: committed models {sorted(models) or 'none'}")
                problems += 1
                continue

            model = next(iter(models))
            if archive is not None and archive != model:
                print(f"SKIP {label}: committed {model}, prompt archive {archive}")
                problems += 1
                continue

            flag = f"  <- the day's latest run is {day_model}" if model != day_model else ""
            print(f"{label}: {model} (archive {archive or '-'}){flag}")
            snap["llm_model"] = model

        if write and any("llm_model" in s for s, _ in pending):
            ensure_ascii = json.dumps(json.loads(text), indent=2, ensure_ascii=True) + "\n" == text
            path.write_text(json.dumps(entry, indent=2, ensure_ascii=ensure_ascii) + "\n")
            changed_files += 1

    print(f"\n{'wrote' if write else 'would write'} {changed_files if write else 'the above'}; {problems} skipped")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
