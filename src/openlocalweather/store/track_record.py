"""Read/write the model track record: data/track_record.json.

Unlike the daily log, this is a single file, fully rewritten every run — it
is a CACHE (fully re-derivable from the log + fresh actuals, except for the
two all-time counter fields — see models.TrackRecordEntry), not a ledger.
Its git diffs are still useful as a human-readable audit trail of skill
evolution over time, which is why it's committed at all rather than treated
as pure scratch state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from openlocalweather.defaults import LEAD_TIMES_DAYS, MODELS
from openlocalweather.models import TrackRecord, TrackRecordEntry


def track_record_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "track_record.json"


def empty_track_record() -> TrackRecord:
    """A fresh 15-entry (5 models x 3 lead times) track record with all
    stats blank — what a brand-new fork starts from before its first run.
    Mirrors the Apps Script version seeding the Model Track Record sheet
    with one row per (model, lead time) pair.
    """
    entries = [
        TrackRecordEntry(model=model, lead_time_days=lead)
        for model in MODELS
        for lead in LEAD_TIMES_DAYS
    ]
    return TrackRecord(generated_at_utc=datetime.now(timezone.utc), entries=entries)


def read_track_record(data_dir: str | Path) -> TrackRecord:
    """Returns an empty (seeded) track record if no file exists yet — same
    cold-start behavior as the Apps Script version auto-creating missing
    (model, lead time) rows on first run.
    """
    path = track_record_path(data_dir)
    if not path.exists():
        return empty_track_record()
    return TrackRecord.model_validate_json(path.read_text())


def write_track_record(data_dir: str | Path, record: TrackRecord) -> Path:
    path = track_record_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2) + "\n")
    return path
