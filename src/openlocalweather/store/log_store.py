"""Read/write daily log entries: data/log/YYYY-MM-DD.json.

One file per day, filename = the date the entry is FOR (i.e. the date it was
generated on, same convention as the Apps Script log's Date column). Kept as
many small files rather than one growing array specifically so each day's
commit touches exactly one new/changed file — small, reviewable diffs, and
no "rewrite the whole history file" cost or merge-conflict risk as the log
grows.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openlocalweather.dates import format_date, parse_date
from openlocalweather.models import DailyLogEntry


def log_path(data_dir: str | Path, d: date) -> Path:
    return Path(data_dir) / "log" / f"{format_date(d)}.json"


def read_log_entry(data_dir: str | Path, d: date) -> DailyLogEntry | None:
    """Returns None if no entry exists for that date — a missing day is a
    normal, expected condition (gaps in the log, not-yet-run days), not an
    error. Malformed JSON or a schema mismatch on an EXISTING file still
    raises, since that's a real data problem worth surfacing loudly.
    """
    path = log_path(data_dir, d)
    if not path.exists():
        return None
    return DailyLogEntry.model_validate_json(path.read_text())


def write_log_entry(data_dir: str | Path, entry: DailyLogEntry) -> Path:
    path = log_path(data_dir, entry.date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry.model_dump_json(indent=2, exclude_none=False) + "\n")
    return path


def list_log_dates(data_dir: str | Path) -> list[date]:
    """All dates with a log entry present, sorted ascending."""
    log_dir = Path(data_dir) / "log"
    if not log_dir.exists():
        return []
    dates = []
    for p in log_dir.glob("*.json"):
        try:
            dates.append(parse_date(p.stem))
        except ValueError:
            continue  # ignore any non-date-named file that ends up in here
    return sorted(dates)


def make_log_lookup(data_dir: str | Path):
    """Returns a `date -> DailyLogEntry | None` callable, the shape
    verify/scoring.py and verify/pipeline.py expect — keeps those functions
    decoupled from the filesystem so they're testable with an in-memory dict
    instead.
    """

    def _lookup(d: date) -> DailyLogEntry | None:
        return read_log_entry(data_dir, d)

    return _lookup
