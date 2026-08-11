"""Read/write the actuals cache: data/actuals_cache/actuals.json.

This is what makes the weekly-batch/daily-light-touch split work (see
CLAUDE_CODE_HANDOFF_BRIEF.md and defaults.WEEKLY_BATCH_WEEKDAY):

- On a normal day, the pipeline fetches ONLY yesterday's single-day actual
  and upserts it into this cache — cheap.
- On the weekly batch day, the pipeline does a full
  ACTUALS_BATCH_LOOKBACK_DAYS-day re-fetch and REPLACES the whole cache for
  that point — this is what delivers the ~7x API load cut relative to doing
  the full batch fetch every day, and it self-heals any drift from a missed
  daily upsert (a failed run, an API hiccup) once a week, preserving the
  original design's "stateless, self-correcting" property.

Keyed by ISO date string (not a real `date`) purely because JSON object keys
must be strings — see date_dict() / from_date_dict() for the date<->str
boundary.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from openlocalweather.dates import format_date, parse_date
from openlocalweather.models import DailyActual


class ActualsCache(BaseModel):
    generated_at_utc: datetime
    primary: dict[str, DailyActual] = Field(default_factory=dict)
    secondary: dict[str, DailyActual] = Field(default_factory=dict)


def actuals_cache_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "actuals_cache" / "actuals.json"


def empty_actuals_cache() -> ActualsCache:
    return ActualsCache(generated_at_utc=datetime.now(timezone.utc))


def read_actuals_cache(data_dir: str | Path) -> ActualsCache:
    path = actuals_cache_path(data_dir)
    if not path.exists():
        return empty_actuals_cache()
    return ActualsCache.model_validate_json(path.read_text())


def write_actuals_cache(data_dir: str | Path, cache: ActualsCache) -> Path:
    path = actuals_cache_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.model_dump_json(indent=2) + "\n")
    return path


def upsert_day(bucket: dict[str, DailyActual], d: date, actual: DailyActual) -> None:
    """Mutates `bucket` in place, adding/overwriting one day."""
    bucket[format_date(d)] = actual


def replace_all(bucket_owner: ActualsCache, which: str, new_bucket: dict[date, DailyActual]) -> None:
    """Wholesale-replaces `primary` or `secondary` with a freshly fetched
    batch — used by the weekly re-fetch, not the daily upsert.
    """
    setattr(bucket_owner, which, {format_date(d): v for d, v in new_bucket.items()})


def prune_older_than(bucket: dict[str, DailyActual], cutoff: date) -> None:
    """Mutates `bucket` in place, dropping any entry older than `cutoff`.
    Keeps the cache from growing unbounded as the daily upsert runs
    indefinitely between weekly full-refreshes.
    """
    stale = [k for k in bucket if parse_date(k) < cutoff]
    for k in stale:
        del bucket[k]


def as_date_dict(bucket: dict[str, DailyActual]) -> dict[date, DailyActual]:
    """Converts the JSON-safe str-keyed bucket into the date-keyed dict the
    verify/ module works with.
    """
    return {parse_date(k): v for k, v in bucket.items()}
