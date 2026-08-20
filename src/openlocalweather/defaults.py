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

# NOTE: MODELS is the list of OPEN-METEO models — it is what gets sent in
# the `models=` API parameter, so nothing may be added here that Open-Meteo
# cannot serve. The set of models that carry a TRACK RECORD is a superset:
# a local met service is scored alongside these but is fetched from its own
# bulletin, not from Open-Meteo. Use scored_models() wherever the question
# is "who has a skill record", and MODELS only where the question is "what
# do we ask Open-Meteo for".


def scored_models(local_bulletin_model_id: str = "") -> list[str]:
    """Every model with a tracked skill record, met service included.

    Kept a function rather than a second constant because whether a local
    met service participates is per-location config, not a global fact —
    and a fork with no parseable bulletin must produce exactly the previous
    list, so its track record and accuracy page are unchanged.
    """
    return [*MODELS, local_bulletin_model_id] if local_bulletin_model_id else list(MODELS)


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

# --- Rain-skill trend (recent vs. longer-term window) ---
#
# The LLM prompt already tells the model to weight recent (rolling_10)
# evidence over longer-term (rolling_30/all_time) evidence when they
# conflict — but "do they conflict" was left for the model to notice by
# eyeballing three separate numbers per (model, lead time) and subtracting
# them in its head. That's exactly the kind of small arithmetic this
# project's "all arithmetic in code, never the LLM" principle exists to
# rule out; see verify/scoring.compute_rain_pct_trend, which now does that
# subtraction deterministically and hands the model a ready-made label.
#
# Minimum sample sizes before a trend is even attempted — below these, the
# comparison is more likely to reflect small-sample noise than real skill
# drift, and TREND_MIN_CHECKS_LONG in particular exists because a 30-check
# window still short on data (early in a fork's life, or after a long gap)
# shouldn't be compared against a fuller 10-check window as if both were
# equally reliable.
TREND_MIN_CHECKS_SHORT = 5
TREND_MIN_CHECKS_LONG = 10

# Minimum |rolling_10 - rolling_30| percentage-point gap to call it a real
# trend rather than noise. For a binary hit/miss rate at n=10, binomial
# sampling noise alone gives a standard deviation of roughly 14-15
# percentage points around p=0.5-0.7 — so a 15-point threshold is set
# close to that noise floor, deliberately not more sensitive than the
# sample size can actually support.
TREND_THRESHOLD_PCT = 15.0

# --- Day-over-day comparison (see comparison.py) ---
#
# Bands for how a temperature change actually FEELS, rather than raw
# degrees. Ordered (upper_bound_exclusive, label); anything at or above the
# last bound gets that last label. Calibrated to human perception rather
# than instrument precision: roughly a degree is inside day-to-day noise
# and shouldn't be announced as a change at all, which is exactly the
# mistake a live run made when the LLM was left to do this subtraction
# itself (it called a 0.1°C difference "about 1°C cooler").
TEMP_CHANGE_BANDS_C = [
    (1.5, "about the same"),
    (3.0, "slightly"),
    (6.0, "noticeably"),
    (99.0, "much"),
]

# Gust change below this is not worth remarking on.
WIND_CHANGE_THRESHOLD_KMH = 8.0

# --- Weekly review (see review.py) ---
#
# How many verified checks a claim needs before it may be stated, and how
# strongly. Deliberately conservative: the README already warns that
# accuracy is meaningless below ~10 checks, and a review that spoke
# confidently off five days would be exactly the "unverified claim hardens
# into received wisdom" failure the review exists to prevent.
# Ordered (upper_bound_exclusive, label).
REVIEW_CONFIDENCE_BANDS = [
    (5, "insufficient"),
    (10, "provisional"),
    (30, "usable"),
    (10**9, "established"),
]

# A comparative claim ("model X beats model Y here") needs BOTH models at
# this many checks, and a gap wider than the sampling noise below.
REVIEW_MIN_CHECKS_FOR_COMPARISON = 10

# Minimum percentage-point gap between two models before the difference is
# worth asserting. Same reasoning as TREND_THRESHOLD_PCT: at n=10 a binary
# hit rate carries ~15 points of binomial noise on its own, so anything
# narrower is indistinguishable from chance.
REVIEW_COMPARISON_MIN_GAP_PCT = 15.0

# Mean signed error large enough to call a systematic bias rather than
# scatter. Roughly the point where a forecast user would notice.
REVIEW_TEMP_BIAS_THRESHOLD_C = 1.0
REVIEW_WIND_BIAS_THRESHOLD_KMH = 8.0


# --- Data-coverage watch (see coverage.py) ---
#
# How far back to look when deciding whether a variable has gone missing.
# Long enough to distinguish a genuine upstream change from a model that
# simply doesn't publish something.
COVERAGE_WINDOW_DAYS = 30

# Consecutive runs a previously-present variable must be missing before it
# is reported. More than one, because a single failed fetch is noise; low
# enough that a real rename surfaces in days rather than months — the
# ECMWF Day+0 wind gap went unnoticed for the life of the deployment
# precisely because absence was handled correctly and silently everywhere.
COVERAGE_ABSENT_RUNS = 3


# --- Synoptic-scale pressure (see synoptic.py) ---
#
# How a large-scale MSLP spread reads. Ordered (upper_bound_exclusive,
# label). Across ~2,600 km, a few hPa is an unremarkable field while 15+ is
# a strongly forced pattern.
SYNOPTIC_GRADIENT_BANDS_HPA = [
    (4.0, "weak"),
    (10.0, "moderate"),
    (18.0, "strong"),
    (999.0, "very strong"),
]

# 72-hour change below this is not worth calling a tendency.
SYNOPTIC_TENDENCY_THRESHOLD_HPA = 1.5
