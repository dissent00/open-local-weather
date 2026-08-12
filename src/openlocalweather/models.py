"""Core data schemas.

These are the single source of truth for the shape of everything that gets
committed to git as JSON (see store/) and everything passed between the
verify/, fetch/, and llm/ modules. Pydantic gives us fail-fast validation on
both read and write — a malformed historical log file (hand-edited, or from
a future schema change) raises immediately rather than silently propagating
a None or a wrong-typed value into the scoring math.

Field names intentionally match the original Apps Script pipeline's
vocabulary (rain, onset, wind_kmh, mslp_trend, ...) so the port stays
auditable against KisumuForecastPipeline_v2.gs.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

LeadTime = int  # one of 0, 3, 7 — not a real enum, kept as int to match defaults.LEAD_TIMES_DAYS


# ---------------------------------------------------------------------------
# Predictions and actuals
# ---------------------------------------------------------------------------


class ModelPrediction(BaseModel):
    """One model's prediction for one target date at one lead time.

    Structured, unlike the Apps Script version's pipe-delimited
    `"gfs_seamless: rain=true onset=14:00 wind_kmh=23 ..."` string that had to
    be hand-parsed back out (`parseModelPredictionsRaw`) every time it was
    read. That round-trip is the one GAS-era workaround this rebuild drops
    rather than preserves.
    """

    model: str
    # None means "this model had no data at this lead time" — NOT "no rain".
    # The distinction is load-bearing: not every model reaches Day+7 (UKMO
    # tops out around 7.2 days, so it has no Day+7 value at all). Recording
    # that absence as rain=False would manufacture a confident dry
    # prediction out of missing data, and since dry days outnumber wet ones
    # it would accrue a flattering, entirely fake accuracy score — which the
    # prompt then instructs the LLM to trust when weighting the extended
    # outlook. score_prediction() refuses to score a None.
    rain: bool | None = None
    onset: str | None = None  # "HH:MM", Day+0 only — no onset data at Day+3/+7
    wind_kmh: float | None = None
    high_c: float | None = None
    low_c: float | None = None
    mslp_trend: float | None = None


class DailyActual(BaseModel):
    """One day's actual/reanalysis observation, bucketed from hourly data.

    Ported from bucketHourlyByDate()'s per-day aggregation: rain is "any hour
    saw >= RAIN_THRESHOLD_MM", peak_wind_kmh is the max hourly gust,
    mslp_trend is last-hour-minus-first-hour pressure, onset_hour is the
    first hour that crossed the rain threshold.
    """

    rain: bool
    high_c: float | None = None
    low_c: float | None = None
    peak_wind_kmh: float | None = None
    mslp_trend: float | None = None
    onset_hour: str | None = None  # "HH:MM"


class VerificationScore(BaseModel):
    """The result of scoring one ModelPrediction against one DailyActual."""

    rain_correct: bool
    onset_error_hrs: float | None = None  # Day+0 only, only when both predicted and actual rain
    wind_error_kmh: float | None = None  # actual - predicted
    high_error_c: float | None = None  # actual - predicted
    low_error_c: float | None = None  # actual - predicted
    mslp_error_hpa: float | None = None  # actual - predicted


# ---------------------------------------------------------------------------
# Daily log entry — data/log/YYYY-MM-DD.json
# ---------------------------------------------------------------------------


class LeadTimeVerification(BaseModel):
    """Verification status for one lead time on one log entry.

    `note` starts empty and is patched in on a LATER run, once the target
    date this entry's prediction was aiming at has actually arrived and been
    scored — see verify/pipeline.py. This is the one place a daily run
    writes back into a *past* day's file rather than only creating today's.
    """

    verified: bool = False
    note: str | None = None


class ModelPredictionsByLead(BaseModel):
    day0: list[ModelPrediction] = Field(default_factory=list)
    day3: list[ModelPrediction] = Field(default_factory=list)
    day7: list[ModelPrediction] = Field(default_factory=list)

    def for_lead(self, lead_time_days: int) -> list[ModelPrediction]:
        return {0: self.day0, 3: self.day3, 7: self.day7}[lead_time_days]


class VerificationByLead(BaseModel):
    day0: LeadTimeVerification = Field(default_factory=LeadTimeVerification)
    day3: LeadTimeVerification = Field(default_factory=LeadTimeVerification)
    day7: LeadTimeVerification = Field(default_factory=LeadTimeVerification)

    def for_lead(self, lead_time_days: int) -> LeadTimeVerification:
        return {0: self.day0, 3: self.day3, 7: self.day7}[lead_time_days]


class GroundAQIReading(BaseModel):
    """One ground-truth AQI station's reading. `name` is OUR configured
    display name (config.WaqiStation.name), not WAQI's own city.name — kept
    consistent everywhere a station gets named.

    `measured_at` is WHEN this reading was actually taken, not when we
    fetched it — WAQI (and the low-cost sensor networks it aggregates, e.g.
    AirQo) can and do serve hours-old readings without any obvious signal
    on their own site, which just shows the last known value with a quiet
    "updated Xh ago" caption. Confirmed live: all three of this project's
    configured stations were serving readings 7.2 hours old at once. Never
    assume a reading is current without checking this field — see
    aqi.hours_old() and the staleness handling in aqi.summarize_ground_aqi().
    """

    name: str
    station_id: str
    aqi: int | None = None
    pm25: float | None = None
    pm10: float | None = None
    measured_at: datetime | None = None


class LogEntryMeta(BaseModel):
    generated_at_utc: datetime
    llm_provider: str
    llm_model: str
    pipeline_version: str
    # Set only by an evening refresh run (see pipeline.run_refresh_pipeline)
    # — generated_at_utc stays the ORIGINAL morning creation time even after
    # a refresh, so the audit trail keeps showing when this entry first
    # existed; refreshed_at records the most recent narrative refresh on
    # top of it. model_predictions/verification are never touched by a
    # refresh, only this field and the narrative/today_properties fields.
    refreshed_at: datetime | None = None


class DailyLogEntry(BaseModel):
    """One day's full forecast record — the git-committed equivalent of one
    row in the Apps Script "Forecast Log" sheet, but with model_predictions
    and verification as structured sub-objects instead of flat columns.
    """

    date: date

    # Blended ("today_properties") synthesis — genuine cross-model reasoning,
    # not any single model's raw number.
    rain_expected: str
    onset_window: str | None = None  # Day+0 only
    peak_wind_kmh: float | None = None  # secondary point, if configured
    temp_high_c: float
    temp_low_c: float
    temp_high_low_display: str
    mslp_trend_24h: str
    synoptic_pattern: str
    uv_index_max: str | None = None
    air_quality_aqi: str | None = None
    # Raw per-station readings only — the range/highest-station summary
    # used in the narrative and on the site is deterministically recomputed
    # from this on demand (see aqi.summarize_ground_aqi), not persisted
    # redundantly, matching the project's "recompute, don't accumulate"
    # rolling-stats philosophy.
    ground_aqi: list[GroundAQIReading] = Field(default_factory=list)

    model_predictions: ModelPredictionsByLead = Field(default_factory=ModelPredictionsByLead)
    verification: VerificationByLead = Field(default_factory=VerificationByLead)

    yesterday_verification_summary: str | None = None
    narrative_markdown: str
    whatsapp_summary: str | None = None

    meta: LogEntryMeta


# ---------------------------------------------------------------------------
# Model track record — data/track_record.json
# ---------------------------------------------------------------------------


class TrackRecordEntry(BaseModel):
    """One (model, lead_time) pair's accuracy record.

    Fully recomputed and rewritten every run EXCEPT all_time_checks /
    all_time_correct, which are incremented by at most 1 per run (for
    yesterday's newly-scored check, if any) — the one piece of state that is
    genuinely carried forward rather than re-derived. See
    verify/pipeline.py for where that increment happens, and don't
    "simplify" it into a full re-derivation without also solving how to
    recover pre-retention-window history.
    """

    model: str
    lead_time_days: LeadTime
    rolling_10_rain_pct: float | None = None
    rolling_30_rain_pct: float | None = None
    all_time_checks: int = 0
    all_time_correct: int = 0
    all_time_rain_pct: float | None = None
    avg_onset_error_hrs_10: float | None = None  # Day+0 only
    avg_wind_error_kmh_10: float | None = None
    avg_temp_high_error_c_10: float | None = None
    avg_temp_low_error_c_10: float | None = None
    avg_mslp_trend_error_hpa_10: float | None = None
    checks_in_window_10: int = 0  # how many of the last 10 actually had data (cold-start visibility)
    last_updated: date | None = None
    # The TARGET date whose result was last counted into all_time_checks /
    # all_time_correct. Guards those two incremental fields against being
    # counted more than once for the same real check — which happens
    # whenever the pipeline runs more than once against the same
    # yesterday: a manual workflow_dispatch, a retry after a partial
    # failure, or a second scheduled run later the same day. Distinct from
    # last_updated, which is just "when did any run last touch this row".
    last_verified_target_date: date | None = None
    skill_profile_summary: str | None = None  # LLM-written, qualitative
    notes: str = ""


class TrackRecord(BaseModel):
    generated_at_utc: datetime
    entries: list[TrackRecordEntry] = Field(default_factory=list)

    def get(self, model: str, lead_time_days: int) -> TrackRecordEntry | None:
        for e in self.entries:
            if e.model == model and e.lead_time_days == lead_time_days:
                return e
        return None
