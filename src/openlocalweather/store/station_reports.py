"""The station's reports as committed data: data/station/<ICAO>/<UTC day>.json.

ROADMAP item 151. Three readers of the ASOS archive — the same-day
snapshot, the day aggregates stamped onto the actuals, and the +24 h window
— fetched and forgot, so the window's scores were not recomputable from the
record (item 24's founding claim) and every reader depended on the archive
answering at that moment, which it did not on the evenings of 2026-09-16
and 09-17. The rows are stored exactly as the archive serves them, with its
`M` markers, so the readers derive from one committed series.

ONE FILE PER STATION PER UTC DAY, because that is what the archive serves;
bucketing to the location's calendar day happens at read time, as it always
did. MERGED, NEVER REPLACED: the 06:01 run stores the night, the 18:01 run
brings the day, the Monday refetch corrects, and a fetch that no longer
returns an early row leaves the stored one in place. A row is identified by
its time AND its text, because a SPECI can share a minute with the routine
report and is the one that carries the storm.

THE FILE NAMES ITS COLUMNS. The daily parse is positional (item 152), so a
row read back with a column too many would put a wind where a temperature
belongs. A merge refuses rows of the wrong width and a file whose columns
differ from the ones being written.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path

# The archive's row shape on the daily path: station, valid, then these.
# Named here rather than imported from fetch/metar.py so the store does not
# depend on the driver layer above it; tests pin the two agree.
COLUMNS = ("valid", "metar", "tmpf", "sknt")

_STATION_COLUMNS = 1  # the leading station id, dropped on write, restored on read
_VALID_DATE_CHARS = 10  # "YYYY-MM-DD" of "YYYY-MM-DD HH:MM"


def station_dir(data_dir: str | Path, icao: str) -> Path:
    return Path(data_dir) / "station" / icao


def report_path(data_dir: str | Path, icao: str, utc_day: date) -> Path:
    return station_dir(data_dir, icao) / f"{utc_day.isoformat()}.json"


def merge_rows(data_dir: str | Path, icao: str, rows: Iterable[Sequence[str]]) -> int:
    """Files `rows` (station, valid, *COLUMNS) by the UTC day of `valid`,
    adding to what each day already holds. Returns how many were new."""
    width = _STATION_COLUMNS + len(COLUMNS)
    by_day: dict[str, list[list[str]]] = {}
    for row in rows:
        if len(row) != width:
            raise ValueError(f"expected {width} columns (station, {', '.join(COLUMNS)}), got {len(row)}: {row!r}")
        by_day.setdefault(row[1][:_VALID_DATE_CHARS], []).append(list(row[_STATION_COLUMNS:]))

    added = 0
    for day, new_rows in by_day.items():
        path = report_path(data_dir, icao, date.fromisoformat(day))
        existing = _read_file(path)
        seen = {_key(r) for r in existing}
        for r in new_rows:
            if _key(r) in seen:
                continue
            existing.append(r)
            seen.add(_key(r))
            added += 1
        existing.sort(key=lambda r: r[0])
        _write_file(path, icao, existing)
    return added


def read_rows(data_dir: str | Path, icao: str, start_utc: date, end_utc: date) -> list[list[str]] | None:
    """The stored rows for the UTC days `start_utc..end_utc` inclusive, in
    the archive's own shape (station, valid, *COLUMNS) and time order. None
    when nothing is stored for the range — "nothing known", never "quiet"."""
    rows: list[list[str]] = []
    day = start_utc
    while day <= end_utc:
        for r in _read_file(report_path(data_dir, icao, day)):
            rows.append([icao, *r])
        day += timedelta(days=1)
    return rows or None


def _key(row: Sequence[str]) -> tuple[str, str]:
    return (row[0], row[1])


def _read_file(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    if tuple(doc.get("columns", ())) != COLUMNS:
        raise ValueError(f"{path} stores columns {doc.get('columns')}, this code writes {list(COLUMNS)}")
    return [list(r) for r in doc["rows"]]


def _write_file(path: Path, icao: str, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"station": icao, "columns": list(COLUMNS), "rows": rows}
    path.write_text(json.dumps(doc, indent=1) + "\n")
