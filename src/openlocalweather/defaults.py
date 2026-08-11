"""Pipeline-wide constants.

These are deliberately NOT part of the per-location config — the project's
location-agnostic design principle is that forking for a new place should
only ever require editing config/location.yaml. If you find yourself tuning
one of these per-fork, that's a sign it should move to location.yaml instead.

Ported from CONFIG in the original Apps Script pipeline
(KisumuForecastPipeline_v2.gs), same values, same rationale.
"""

from __future__ import annotations

# Open-Meteo model identifiers queried for every multi-model fetch.
MODELS: list[str] = [
    "gfs_seamless",
    "ecmwf_ifs025",
    "icon_seamless",
    "ukmo_seamless",
    "best_match",
]

# Which lead times get independently tracked and scored. Day+0 is verified
# against hourly data (has onset timing); Day+3/Day+7 are verified against
# daily aggregates only (no onset timing available at that range, by design,
# to control API cost).
LEAD_TIMES_DAYS: list[int] = [0, 3, 7]

# Rolling-window sizes, in *verified checks* (not calendar days) at a given
# lead time.
ROLLING_WINDOW_SHORT = 10  # "recent" window
ROLLING_WINDOW_LONG = 30  # "longer-term" window

# How far back the weekly actuals-batch re-fetch reaches. Must be >=
# ROLLING_WINDOW_LONG + max(LEAD_TIMES_DAYS) for the longest rolling window to
# ever fully populate: the oldest target date needed is (yesterday -
# ROLLING_WINDOW_LONG), and the row that MADE that prediction is dated
# (target - lead_time), so at defaults (30 + 7 = 37) this needs >= 37 days of
# history. Set generously above that floor.
ACTUALS_BATCH_LOOKBACK_DAYS = 40

# Day of week (Monday=0 ... Sunday=6) that triggers the full actuals-batch
# re-fetch and cache rebuild. Every other day only upserts yesterday's single
# actual into the cache. See store/actuals_cache.py.
WEEKLY_BATCH_WEEKDAY = 0  # Monday

# How many days of past narratives/notes get sent to the LLM as context.
HISTORICAL_LOOKBACK_DAYS = 30

# Daily log entries (data/log/*.json) older than this are considered eligible
# for archival. Git history already preserves everything indefinitely, so v1
# does not implement a separate archive-mover — see README for rationale.
LOG_RETENTION_DAYS = 180

# Rain threshold, mm/hour, used consistently for both "did it rain" scoring
# and onset-hour detection.
RAIN_THRESHOLD_MM = 0.5
