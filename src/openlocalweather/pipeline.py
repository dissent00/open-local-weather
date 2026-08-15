"""Daily pipeline orchestration — mirrors runDailyForecastPipeline() from
KisumuForecastPipeline_v2.gs step-for-step, adapted to git-as-database.

git commit/push is deliberately NOT done here — that's the GitHub Actions
workflow's job (see .github/workflows/daily.yml and evening_refresh.yml)
after these functions return, keeping this module free of any git
dependency and testable purely as "run this, inspect the files/return
value."

Two entry points:

run_daily_pipeline() — the morning run. Step order:
  1. Fetch today's forward-looking multi-model guidance + optional sources
     (METAR, ground AQI, local bulletin).
  2. Fetch/refresh yesterday's actual into the actuals cache — a cheap
     single-day upsert on a normal day, a full batch re-fetch on
     defaults.WEEKLY_BATCH_WEEKDAY (see store/actuals_cache.py).
  3. Deterministic verification + rolling stats (verify/pipeline.py) — no
     LLM involved.
  4. Historical notes context for the LLM.
  5. Extract today's raw per-model predictions (extract.py) — code, not LLM.
  6. Call the LLM for narrative + qualitative notes + blended today_properties.
  7. Build today's DailyLogEntry.
  8. Write today's entry, write the LLM's qualitative notes back onto the
     TRACK RECORD (skill summaries) and the ORIGINAL historical log rows
     that just got verified (verification notes) — not today's row.
  9. Publish (GitHub Pages / email) via injected Publisher/EmailSender, if
     configured — both are optional hooks so this module doesn't need to
     know about either concrete implementation.

run_refresh_pipeline() — an optional same-day evening run (see
docs-internal/ROADMAP.md's "Second daily forecast run" for the full design
rationale). Re-fetches forward guidance on a fresher model cycle and
re-synthesizes the narrative, but deliberately does NOT touch the accuracy
loop: no verification runs (yesterday's actuals don't change during the
day), and critically, the morning run's stored model_predictions are
preserved byte-for-byte. Those are what tomorrow's verification scores, and
they must reflect what was actually published at 6 AM, not silently get
overwritten by evening data.

--dry-run (see cli.py) skips both functions' file-write/publish/email
steps — the fetch/LLM steps still run for real, so a maintainer can see the
pipeline actually working without polluting committed data or emailing real
subscribers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from openlocalweather import __version__
from openlocalweather.aqi import GroundAQISummary, hours_old, is_stale, summarize_ground_aqi
from openlocalweather.config import LocationConfig
from openlocalweather.dates import add_days, format_date, today_in_tz
from openlocalweather.defaults import (
    ACTUALS_BATCH_LOOKBACK_DAYS,
    HISTORICAL_LOOKBACK_DAYS,
    MODELS,
    WEEKLY_BATCH_WEEKDAY,
)
from openlocalweather.extract import (
    extract_day0_predictions_from_hourly,
    extract_day_n_predictions_from_daily,
)
from openlocalweather.fetch import metar as metar_fetch
from openlocalweather.fetch import open_meteo
from openlocalweather.fetch import waqi as waqi_fetch
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.llm.prompt import build_system_prompt, build_user_prompt
from openlocalweather.llm.provider import LLMProvider
from openlocalweather.llm.schema import GeminiForecastResponse
from openlocalweather.models import (
    DailyLogEntry,
    GroundAQIReading,
    LogEntryMeta,
    ModelPredictionsByLead,
    MorningIssuanceSnapshot,
    TrackRecord,
)
from openlocalweather.store import actuals_cache as actuals_cache_store
from openlocalweather.store import log_store
from openlocalweather.store import track_record as track_record_store
from openlocalweather.verify.pipeline import run_deterministic_verification_and_scoring


class Publisher(Protocol):
    def publish(self, entry: DailyLogEntry) -> None: ...


class EmailSender(Protocol):
    def send(self, entry: DailyLogEntry) -> None: ...


@dataclass
class PipelineDeps:
    location: LocationConfig
    data_dir: Path
    llm_provider: LLMProvider
    public_webpage_url: str
    waqi_token: str = ""
    bulletin_fetcher: BulletinFetcher = field(default_factory=NullBulletinFetcher)
    publisher: Publisher | None = None
    email_sender: EmailSender | None = None
    pipeline_version: str = __version__


@dataclass
class PipelineRunResult:
    today: date
    log_entry: DailyLogEntry
    updated_track_record: TrackRecord
    newly_verified: list[tuple[date, int]]
    published: bool
    emailed: bool


@dataclass
class RefreshRunResult:
    today: date
    log_entry: DailyLogEntry
    published: bool


class RefreshWithoutMorningRunError(RuntimeError):
    """Raised when run_refresh_pipeline() is called for a date with no
    existing log entry — there is nothing to refresh, and silently creating
    a "morning" entry from an evening run would mean model_predictions were
    extracted from evening-cycle data, not what was actually true at 6 AM
    when a run_daily_pipeline() call would normally have captured them.
    """


@dataclass
class ForwardGuidance:
    """Everything Step 1 fetches — shared verbatim between the morning and
    evening-refresh runs so the two can never silently drift apart on what
    "today's forward-looking guidance" means.
    """

    primary_hourly: dict
    primary_daily: dict
    regional_pressure: dict
    air_quality: dict
    secondary_hourly: dict | None
    secondary_daily: dict | None
    airport_metar: list[dict] | None
    ground_aqi_readings: list[GroundAQIReading]
    ground_aqi_summary: GroundAQISummary | None
    aqi_fetch_time: datetime
    bulletin_text: str


def _fetch_forward_guidance(deps: PipelineDeps) -> ForwardGuidance:
    location = deps.location
    primary_hourly = open_meteo.fetch_forecast_hourly_today(
        location.primary_point.lat, location.primary_point.lon, MODELS, location.timezone
    )
    primary_daily = open_meteo.fetch_forecast_daily_extended(
        location.primary_point.lat, location.primary_point.lon, MODELS, location.timezone
    )
    region_points = [(p.lat, p.lon) for p in location.region_points]
    regional_pressure = open_meteo.fetch_regional_pressure(
        (location.primary_point.lat, location.primary_point.lon), region_points, location.timezone
    )
    air_quality = open_meteo.fetch_air_quality(
        location.primary_point.lat, location.primary_point.lon, location.timezone
    )

    secondary_hourly = None
    secondary_daily = None
    if location.secondary_point.enabled:
        secondary_hourly = open_meteo.fetch_forecast_hourly_today(
            location.secondary_point.lat, location.secondary_point.lon, MODELS, location.timezone
        )
        secondary_daily = open_meteo.fetch_forecast_daily_extended(
            location.secondary_point.lat, location.secondary_point.lon, MODELS, location.timezone
        )

    airport_metar = metar_fetch.fetch_metar(location.metar_station_icao)
    aqi_fetch_time = datetime.now(timezone.utc)
    ground_aqi_readings = waqi_fetch.fetch_ground_aqi_stations(location.waqi_stations, deps.waqi_token)
    ground_aqi_summary = summarize_ground_aqi(ground_aqi_readings, now=aqi_fetch_time)
    bulletin_text = deps.bulletin_fetcher.fetch()

    return ForwardGuidance(
        primary_hourly=primary_hourly,
        primary_daily=primary_daily,
        regional_pressure=regional_pressure,
        air_quality=air_quality,
        secondary_hourly=secondary_hourly,
        secondary_daily=secondary_daily,
        airport_metar=airport_metar,
        ground_aqi_readings=ground_aqi_readings,
        ground_aqi_summary=ground_aqi_summary,
        aqi_fetch_time=aqi_fetch_time,
        bulletin_text=bulletin_text,
    )


def _ground_aqi_prompt_payload(guidance: ForwardGuidance) -> list[dict]:
    """The per-station reading list as sent to the LLM: pre-computed
    hours_old/stale flags attached, never left for the LLM to derive from a
    raw timestamp — same rule as everywhere else in this prompt."""
    return [
        {
            **r.model_dump(),
            "hours_old": round(h, 1) if (h := hours_old(r, guidance.aqi_fetch_time)) is not None else None,
            "stale": is_stale(r, guidance.aqi_fetch_time),
        }
        for r in guidance.ground_aqi_readings
    ]


def run_daily_pipeline(
    deps: PipelineDeps, today: date | None = None, dry_run: bool = False
) -> PipelineRunResult:
    location = deps.location
    today = today or today_in_tz(location.timezone)
    yesterday = add_days(today, -1)

    # --- Step 1: today's forward-looking guidance + optional sources ---
    guidance = _fetch_forward_guidance(deps)
    primary_hourly = guidance.primary_hourly
    primary_daily = guidance.primary_daily

    # --- Step 2: actuals cache (daily upsert, or weekly full re-fetch) ---
    cache = actuals_cache_store.read_actuals_cache(deps.data_dir)
    if today.weekday() == WEEKLY_BATCH_WEEKDAY:
        batch_start = add_days(today, -ACTUALS_BATCH_LOOKBACK_DAYS)
        primary_archive = open_meteo.fetch_archive_range(
            location.primary_point.lat, location.primary_point.lon, batch_start, yesterday, location.timezone
        )
        actuals_cache_store.replace_all(cache, "primary", open_meteo.bucket_hourly_by_date(primary_archive))
        if location.secondary_point.enabled:
            secondary_archive = open_meteo.fetch_archive_range(
                location.secondary_point.lat,
                location.secondary_point.lon,
                batch_start,
                yesterday,
                location.timezone,
            )
            actuals_cache_store.replace_all(
                cache, "secondary", open_meteo.bucket_hourly_by_date(secondary_archive)
            )
    else:
        primary_archive = open_meteo.fetch_archive_single_day(
            location.primary_point.lat, location.primary_point.lon, yesterday, location.timezone
        )
        for d, actual in open_meteo.bucket_hourly_by_date(primary_archive).items():
            actuals_cache_store.upsert_day(cache.primary, d, actual)
        if location.secondary_point.enabled:
            secondary_archive = open_meteo.fetch_archive_single_day(
                location.secondary_point.lat, location.secondary_point.lon, yesterday, location.timezone
            )
            for d, actual in open_meteo.bucket_hourly_by_date(secondary_archive).items():
                actuals_cache_store.upsert_day(cache.secondary, d, actual)

    prune_cutoff = add_days(today, -(ACTUALS_BATCH_LOOKBACK_DAYS + 5))
    actuals_cache_store.prune_older_than(cache.primary, prune_cutoff)
    actuals_cache_store.prune_older_than(cache.secondary, prune_cutoff)
    actuals_primary = actuals_cache_store.as_date_dict(cache.primary)

    # --- Step 3: deterministic verification + rolling stats ---
    log_lookup = log_store.make_log_lookup(deps.data_dir)
    prior_track_record = track_record_store.read_track_record(deps.data_dir)
    verification_result = run_deterministic_verification_and_scoring(
        log_lookup=log_lookup,
        prior_track_record=prior_track_record,
        actuals_primary=actuals_primary,
        today=today,
        yesterday=yesterday,
    )

    # --- Step 4: historical notes context for the LLM ---
    historical_logs = []
    lookback_start = add_days(today, -HISTORICAL_LOOKBACK_DAYS)
    for d in log_store.list_log_dates(deps.data_dir):
        if lookback_start <= d < today:
            entry = log_lookup(d)
            if entry is not None:
                historical_logs.append(
                    {
                        "date": format_date(d),
                        "rain_expected": entry.rain_expected,
                        "day0_verified": entry.verification.day0.verified,
                        "day0_note": entry.verification.day0.note,
                        "day3_verified": entry.verification.day3.verified,
                        "day3_note": entry.verification.day3.note,
                        "day7_verified": entry.verification.day7.verified,
                        "day7_note": entry.verification.day7.note,
                    }
                )

    # --- Step 5: extract today's raw per-model predictions (code, not LLM) ---
    day0_predictions = extract_day0_predictions_from_hourly(primary_hourly, MODELS)
    day3_predictions = extract_day_n_predictions_from_daily(primary_daily, 3, MODELS)
    day7_predictions = extract_day_n_predictions_from_daily(primary_daily, 7, MODELS)

    # --- Step 6: call the LLM ---
    system_prompt = build_system_prompt(location)
    verification_context = [
        {
            "lead_time_days": r.lead_time_days,
            "target_date_verified": format_date(r.target_date_verified) if r.target_date_verified else None,
            "per_model_scores": {model: score.model_dump() for model, score in r.per_model_scores.items()},
        }
        for r in verification_result.lead_time_results
    ]
    track_record_context = [e.model_dump() for e in verification_result.updated_track_record.entries]
    user_prompt = build_user_prompt(
        today=today,
        yesterday=yesterday,
        public_webpage_url=deps.public_webpage_url,
        verification_context=verification_context,
        track_record_context=track_record_context,
        historical_logs=historical_logs,
        ground_aqi_readings=_ground_aqi_prompt_payload(guidance),
        ground_aqi_summary=asdict(guidance.ground_aqi_summary) if guidance.ground_aqi_summary is not None else None,
        today_weather_data={
            "primary_today_hourly": primary_hourly,
            "primary_extended_daily": primary_daily,
            "secondary_today_hourly": guidance.secondary_hourly,
            "secondary_extended_daily": guidance.secondary_daily,
            "regional_pressure": guidance.regional_pressure,
            "air_quality": guidance.air_quality,
            "airport_metar": guidance.airport_metar,
        },
        local_bulletin_source_name=location.local_bulletin_source_name,
        local_bulletin_text=guidance.bulletin_text,
    )
    llm_response: GeminiForecastResponse = deps.llm_provider.generate(
        system_prompt, user_prompt, GeminiForecastResponse
    )

    # --- Step 7: build today's log entry ---
    tp = llm_response.today_properties
    log_entry = DailyLogEntry(
        date=today,
        rain_expected=tp.rain_expected,
        onset_window=tp.onset_window,
        peak_wind_kmh=tp.peak_wind_kmh,
        temp_high_c=tp.temp_high_c,
        temp_low_c=tp.temp_low_c,
        temp_high_low_display=tp.temp_high_low,
        mslp_trend_24h=tp.mslp_trend_24h or "",
        synoptic_pattern=tp.synoptic_pattern or "",
        uv_index_max=tp.uv_index_max,
        air_quality_aqi=tp.air_quality_aqi,
        ground_aqi=guidance.ground_aqi_readings,
        model_predictions=ModelPredictionsByLead(
            day0=day0_predictions, day3=day3_predictions, day7=day7_predictions
        ),
        yesterday_verification_summary=llm_response.yesterday_verification,
        narrative_markdown=llm_response.today_narrative,
        whatsapp_summary=llm_response.whatsapp_summary,
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider=type(deps.llm_provider).__name__,
            llm_model=getattr(deps.llm_provider, "model", "unknown"),
            pipeline_version=deps.pipeline_version,
        ),
    )

    published = False
    emailed = False

    # --- Step 8: write files + patch historical rows/track record ---
    if not dry_run:
        log_store.write_log_entry(deps.data_dir, log_entry)
        actuals_cache_store.write_actuals_cache(deps.data_dir, cache)

        notes_by_lead = {n.lead_time_days: n.note for n in llm_response.verification_notes}
        for row_date, lead_time_days in verification_result.newly_verified:
            historical_entry = log_lookup(row_date)
            if historical_entry is None:
                continue
            verification_field = historical_entry.verification.for_lead(lead_time_days)
            verification_field.verified = True
            note = notes_by_lead.get(lead_time_days)
            if note:
                verification_field.note = note
            log_store.write_log_entry(deps.data_dir, historical_entry)

        summaries_by_key = {
            (s.model, s.lead_time_days): s.summary for s in llm_response.skill_profile_summaries
        }
        for entry in verification_result.updated_track_record.entries:
            summary = summaries_by_key.get((entry.model, entry.lead_time_days))
            if summary:
                entry.skill_profile_summary = summary
        track_record_store.write_track_record(deps.data_dir, verification_result.updated_track_record)

        # --- Step 9: publish (optional hooks) ---
        if deps.publisher is not None:
            deps.publisher.publish(log_entry)
            published = True
        if deps.email_sender is not None:
            deps.email_sender.send(log_entry)
            emailed = True

    return PipelineRunResult(
        today=today,
        log_entry=log_entry,
        updated_track_record=verification_result.updated_track_record,
        newly_verified=verification_result.newly_verified,
        published=published,
        emailed=emailed,
    )


def run_refresh_pipeline(
    deps: PipelineDeps, today: date | None = None, dry_run: bool = False
) -> RefreshRunResult:
    """The optional evening refresh — see this module's docstring and
    docs-internal/ROADMAP.md for the full rationale. Requires today's entry
    to already exist (written by a prior run_daily_pipeline() call);
    raises RefreshWithoutMorningRunError otherwise, since there is nothing
    to refresh and fabricating one here would mean model_predictions came
    from evening-cycle data instead of what was true at 6 AM.
    """
    location = deps.location
    today = today or today_in_tz(location.timezone)

    existing_entry = log_store.read_log_entry(deps.data_dir, today)
    if existing_entry is None:
        raise RefreshWithoutMorningRunError(
            f"No forecast entry found for {today} to refresh — run-daily must complete "
            "successfully first (see run_daily_pipeline)."
        )

    # --- Step 1: fresh forward-looking guidance (later model cycle) ---
    guidance = _fetch_forward_guidance(deps)

    # --- Step 2: historical notes context, same as the morning run ---
    historical_logs = []
    lookback_start = add_days(today, -HISTORICAL_LOOKBACK_DAYS)
    log_lookup = log_store.make_log_lookup(deps.data_dir)
    for d in log_store.list_log_dates(deps.data_dir):
        if lookback_start <= d < today:
            entry = log_lookup(d)
            if entry is not None:
                historical_logs.append(
                    {
                        "date": format_date(d),
                        "rain_expected": entry.rain_expected,
                        "day0_verified": entry.verification.day0.verified,
                        "day0_note": entry.verification.day0.note,
                        "day3_verified": entry.verification.day3.verified,
                        "day3_note": entry.verification.day3.note,
                        "day7_verified": entry.verification.day7.verified,
                        "day7_note": entry.verification.day7.note,
                    }
                )

    # --- Step 3: call the LLM in refresh mode ---
    # No new verification happened (yesterday's actuals don't change during
    # the day), so this is a placeholder explaining that, not a recomputed
    # result — the LLM is told in-prompt not to fabricate new verification
    # content, and the response's verification_notes/yesterday_verification
    # are simply not used when merging back into the entry below.
    verification_context = {"note": "No new verification this run — same-day refresh; see the morning issuance."}
    track_record_context = [e.model_dump() for e in track_record_store.read_track_record(deps.data_dir).entries]

    system_prompt = build_system_prompt(location, is_refresh=True)
    user_prompt = build_user_prompt(
        today=today,
        yesterday=add_days(today, -1),
        public_webpage_url=deps.public_webpage_url,
        verification_context=verification_context,
        track_record_context=track_record_context,
        historical_logs=historical_logs,
        ground_aqi_readings=_ground_aqi_prompt_payload(guidance),
        ground_aqi_summary=asdict(guidance.ground_aqi_summary) if guidance.ground_aqi_summary is not None else None,
        today_weather_data={
            "primary_today_hourly": guidance.primary_hourly,
            "primary_extended_daily": guidance.primary_daily,
            "secondary_today_hourly": guidance.secondary_hourly,
            "secondary_extended_daily": guidance.secondary_daily,
            "regional_pressure": guidance.regional_pressure,
            "air_quality": guidance.air_quality,
            "airport_metar": guidance.airport_metar,
        },
        local_bulletin_source_name=location.local_bulletin_source_name,
        local_bulletin_text=guidance.bulletin_text,
        morning_narrative=existing_entry.narrative_markdown,
    )
    llm_response: GeminiForecastResponse = deps.llm_provider.generate(
        system_prompt, user_prompt, GeminiForecastResponse
    )

    # --- Step 4: merge into the EXISTING entry — everything the accuracy
    # loop depends on (model_predictions, verification, meta.generated_at_utc,
    # yesterday_verification_summary) is preserved untouched. Only the
    # narrative/today_properties/ground_aqi/whatsapp_summary and a new
    # refreshed_at timestamp are updated.
    #
    # Before overwriting them, snapshot the morning issuance's own version
    # of those same fields into morning_issuance — but ONLY if one isn't
    # already captured. A day's true morning issuance must be snapshotted
    # exactly once; re-snapshotting on a hypothetical second same-day
    # refresh would silently replace it with an already-refreshed version,
    # defeating the whole point. (In practice this shouldn't happen —
    # evening_refresh.yml's `check` job gates on meta.refreshed_at already
    # being set — but the guard costs nothing and matches this project's
    # existing belt-and-suspenders idempotency style, e.g.
    # last_verified_target_date in verify/pipeline.py.) ---
    morning_snapshot = existing_entry.morning_issuance or MorningIssuanceSnapshot(
        rain_expected=existing_entry.rain_expected,
        onset_window=existing_entry.onset_window,
        peak_wind_kmh=existing_entry.peak_wind_kmh,
        temp_high_c=existing_entry.temp_high_c,
        temp_low_c=existing_entry.temp_low_c,
        temp_high_low_display=existing_entry.temp_high_low_display,
        mslp_trend_24h=existing_entry.mslp_trend_24h,
        synoptic_pattern=existing_entry.synoptic_pattern,
        uv_index_max=existing_entry.uv_index_max,
        air_quality_aqi=existing_entry.air_quality_aqi,
        ground_aqi=existing_entry.ground_aqi,
        narrative_markdown=existing_entry.narrative_markdown,
        whatsapp_summary=existing_entry.whatsapp_summary,
        generated_at_utc=existing_entry.meta.generated_at_utc,
    )

    tp = llm_response.today_properties
    updated_entry = existing_entry.model_copy(
        update={
            "rain_expected": tp.rain_expected,
            "onset_window": tp.onset_window,
            "peak_wind_kmh": tp.peak_wind_kmh,
            "temp_high_c": tp.temp_high_c,
            "temp_low_c": tp.temp_low_c,
            "temp_high_low_display": tp.temp_high_low,
            "mslp_trend_24h": tp.mslp_trend_24h or "",
            "synoptic_pattern": tp.synoptic_pattern or "",
            "uv_index_max": tp.uv_index_max,
            "air_quality_aqi": tp.air_quality_aqi,
            "ground_aqi": guidance.ground_aqi_readings,
            "narrative_markdown": llm_response.today_narrative,
            "whatsapp_summary": llm_response.whatsapp_summary,
            "morning_issuance": morning_snapshot,
            "meta": existing_entry.meta.model_copy(update={"refreshed_at": datetime.now(timezone.utc)}),
        }
    )

    published = False
    if not dry_run:
        log_store.write_log_entry(deps.data_dir, updated_entry)
        if deps.publisher is not None:
            deps.publisher.publish(updated_entry)
            published = True
        # No email_sender call here by design — the evening refresh is
        # web-only in this first version (see ROADMAP.md's open task on
        # whether it should also email); the standalone Apps Script mailer
        # is unaffected either way since it runs on its own trigger and
        # just reads whatever is currently committed.

    return RefreshRunResult(today=today, log_entry=updated_entry, published=published)
