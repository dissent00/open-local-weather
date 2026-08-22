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

import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from openlocalweather import __version__
from openlocalweather.aqi import GroundAQISummary, hours_old, is_stale, summarize_ground_aqi
from openlocalweather.comparison import compute_day_over_day
from openlocalweather.daypart import (
    DayPart,
    daypart_without_sun,
    forward_hours,
    reconcile_now,
    summarize_daypart,
)
from openlocalweather.config import LocationConfig
from openlocalweather.dates import add_days, format_date, now_in_tz, today_in_tz
from openlocalweather.defaults import (
    ACTUALS_BATCH_LOOKBACK_DAYS,
    HISTORICAL_LOOKBACK_DAYS,
    MODELS,
    WEEKLY_BATCH_WEEKDAY,
    scored_models,
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
from openlocalweather.review import WeeklyReview, build_weekly_review
from openlocalweather.spend import assert_capacity, record_attempt
from openlocalweather.synoptic import summarize_synoptic
from openlocalweather.llm.provider import LLMProvider
from openlocalweather.llm.schema import GeminiForecastResponse
from openlocalweather.models import (
    LocalBulletinRecord,
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
    # github.event_name when running under Actions; empty locally.
    trigger_source: str = ""
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
    synoptic: object | None = None
    # Structured half of the same bulletin fetch, when the source supports
    # it (see fetch/bulletin/kenya_kmd_daily). None for a met service whose
    # bulletin can't be decoded, which must leave scoring untouched rather
    # than inserting a blank model into the record.
    met_service_prediction: ModelPrediction | None = None
    met_service_valid_for: date | None = None
    met_service_prediction_day3: ModelPrediction | None = None
    met_service_day3_valid_for: date | None = None
    # Where this run sits in the day, and the hours still ahead of it. Both
    # live here rather than being computed per call site so the first run of
    # the day and the fourth cannot disagree about what time it is.
    issuance: DayPart | None = None
    forward_hourly: dict | None = None



def _attach_spend_cap(deps: PipelineDeps, location, *, purpose: str):
    """Make the cap count HTTP requests, which is what actually costs money.

    Recording once before generate() undercounted by up to a factor of
    MAX_ATTEMPTS: the providers retry transient failures inside a single
    generate(), so one recorded call could be four billable requests. That is
    the opposite of what spend.py promises — "calls, not forecasts" — and it
    went wrong in exactly the conditions the cap exists for, since retries fire
    on 429 and 5xx: when a provider is already rate-limiting or struggling.

    The cap is the operator's number, not ours. Honouring it means counting the
    thing they are billed for, so the hook fires once per request, and raising
    from it aborts the retry loop mid-flight rather than after the damage.
    """
    # Fail closed before anything starts. The hook below is the real
    # enforcement, but the provider is an injected dependency: one that ignores
    # the hook would sail straight past the cap, and a guard that a substituted
    # object can switch off is not a guard. This runs on the pipeline's own
    # path, where nothing can opt out.
    assert_capacity(deps.data_dir, max_calls=location.max_llm_calls_per_24h)

    recorded: list[int] = []

    def _record() -> None:
        used = record_attempt(
            deps.data_dir,
            provider=type(deps.llm_provider).__name__,
            model=getattr(deps.llm_provider, "model", "unknown"),
            purpose=purpose,
            max_calls=location.max_llm_calls_per_24h,
        )
        recorded.append(used)
        print(f"LLM call {used}/{location.max_llm_calls_per_24h} in the last 24h")

    # Set rather than passed to the constructor: the provider is built in
    # cli.py, which has no reason to know where the ledger lives.
    deps.llm_provider.before_attempt = _record

    def _verify_recorded() -> None:
        """Complain if the provider went and called a model without saying so.

        The hook is only as good as the provider's willingness to call it, and
        the provider is injected. Tolerance requires vigilance: a run that
        spends without appearing in the ledger is the exact failure the cap
        exists to prevent, so it gets said out loud rather than discovered on
        a bill. Not fatal — the forecast itself is fine, and throwing it away
        would punish the user for someone else's provider.
        """
        if not recorded:
            print(
                f"WARNING: {type(deps.llm_provider).__name__} completed a "
                f"{purpose} without reporting any request to the spend cap. "
                f"Its usage is NOT counted and the cap cannot bound it. A "
                f"provider must call before_attempt() before every request.",
                file=sys.stderr,
            )

    return _verify_recorded


def _earlier_issuances(entry) -> list[dict]:
    """Today's already-published narratives, oldest first.

    The log currently stores one narrative per day plus an optional refreshed
    one, so this returns at most two. It returns a LIST regardless, because
    the number of runs a day is an operator's choice and the prompt should not
    have to change when someone schedules a third.
    """
    out = []
    morning = getattr(entry, "narrative_markdown", None)
    if morning:
        issued = getattr(entry.meta, "generated_at", None) if hasattr(entry, "meta") else None
        out.append({"time": _clock(issued) or "earlier today", "narrative": morning})
    return out


def _clock(value) -> str | None:
    """HH:MM from whatever the log stored, or None if it cannot be read.

    Deliberately forgiving: a timestamp that will not parse is a reason to
    say "earlier today" rather than to fail a run.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def _sun_context(location, now_local: datetime) -> tuple[DayPart | None, datetime]:
    """Sunrise/sunset for today and tomorrow, reduced to the issuance moment.

    Open-Meteo returns these as naive local strings when `timezone=` is set,
    which is why `now_in_tz` is naive too — see its docstring on why mixing
    the two would be worse than either.
    """
    sun = open_meteo.fetch_sun_times(
        location.primary_point.lat, location.primary_point.lon, location.timezone
    )

    # An independent check on this machine's clock, from a response already
    # fetched. See daypart.reconcile_now — the host's own timezone setting is
    # irrelevant, but its clock being wrong is silent and would produce a
    # forecast written confidently for the wrong part of the day.
    now_local, skew_warning = reconcile_now(
        now_local, (sun or {}).get("_server_date"), (sun or {}).get("utc_offset_seconds")
    )
    if skew_warning:
        print(f"WARNING: {skew_warning}", file=sys.stderr)

    daily = (sun or {}).get("daily") or {}
    rises, sets = daily.get("sunrise") or [], daily.get("sunset") or []
    if not rises or not sets:
        # Polar night returns no sunrise or sunset at all. Not an error, and
        # not something to fail a forecast over — the reader simply gets no
        # sun times, which at that latitude is the correct answer.
        return None, now_local

    sunrise = datetime.fromisoformat(rises[0])
    sunset = datetime.fromisoformat(sets[0])
    next_sunrise = datetime.fromisoformat(rises[1]) if len(rises) > 1 else None
    return summarize_daypart(now_local, sunrise, sunset, next_sunrise), now_local


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
    # Synoptic-scale pressure ring. One request, ~3 KB — see synoptic.py for
    # why the near-field region_points cannot answer this. Optional: losing it
    # costs a paragraph of context, not the forecast.
    try:
        synoptic = summarize_synoptic(
            open_meteo.fetch_synoptic_pressure(
                location.primary_point.lat, location.primary_point.lon, location.timezone
            )
        )
    except Exception:
        synoptic = None

    ground_aqi_readings = waqi_fetch.fetch_ground_aqi_stations(location.waqi_stations, deps.waqi_token)
    ground_aqi_summary = summarize_ground_aqi(ground_aqi_readings, now=aqi_fetch_time)
    # A fetcher that can also yield a structured prediction exposes
    # fetch_forecast(); the plain BulletinFetcher protocol does not. Duck-typed
    # rather than added to the Protocol so existing fork implementations keep
    # working untouched — a met service whose bulletin can't be decoded simply
    # contributes narrative text, exactly as before.
    met_prediction = None
    met_valid_for = None
    met_day3 = None
    met_day3_valid_for = None
    fetch_forecast = getattr(deps.bulletin_fetcher, "fetch_forecast", None)
    if callable(fetch_forecast):
        met_forecast = fetch_forecast()
        bulletin_text = met_forecast.text
        met_prediction = met_forecast.prediction
        met_valid_for = met_forecast.valid_for
        met_day3 = getattr(met_forecast, "prediction_day3", None)
        met_day3_valid_for = getattr(met_forecast, "five_day_valid_for", None)
    else:
        bulletin_text = deps.bulletin_fetcher.fetch()

    # Where this run sits in the day, and the hours still ahead of it.
    #
    # Both are best-effort: a forecast without them is worse but still a
    # forecast, whereas a run aborted because an astronomical lookup failed
    # would turn a nice-to-have into a single point of failure. The prompt
    # states plainly when they are missing rather than guessing.
    # The clock, the sun, and the hours ahead are three separate things, and
    # they fail separately.
    #
    # now_in_tz reads the system clock and cannot fail over the network;
    # sunrise/sunset and the forward window both can. An earlier version put
    # all three in one try, so a failed astronomical lookup also skipped the
    # forward window AND threw away the local time — leaving the prompt to say
    # "time of day unavailable" for a run that knew perfectly well it was
    # 18:15. Knowing the time is most of the value; knowing where the sun is
    # only refines it.
    now_local = now_in_tz(location.timezone)
    try:
        # Also returns the reconciled clock: if this host's time disagrees with
        # the server's, the corrected value must reach the forward-window trim
        # below too, or the two halves of the prompt would describe different
        # moments.
        sun_part, now_local = _sun_context(location, now_local)
        issuance = sun_part or daypart_without_sun(now_local)
    except Exception as e:  # noqa: BLE001 - never fatal; the time still stands
        print(f"Sun times unavailable ({e}); using the clock alone.", file=sys.stderr)
        issuance = daypart_without_sun(now_local)

    forward_hourly = None
    try:
        forward_hourly = forward_hours(
            open_meteo.fetch_forecast_hourly_forward(
                location.primary_point.lat,
                location.primary_point.lon,
                MODELS,
                location.timezone,
            ),
            now_local,
        )
    except Exception as e:  # noqa: BLE001 - the calendar day is still supplied
        print(f"Forward hourly window unavailable ({e}); continuing without it.", file=sys.stderr)

    return ForwardGuidance(
        issuance=issuance,
        forward_hourly=forward_hourly,
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
        synoptic=synoptic,
        met_service_prediction=met_prediction,
        met_service_valid_for=met_valid_for,
        met_service_prediction_day3=met_day3,
        met_service_day3_valid_for=met_day3_valid_for,
    )


def _review_prompt_payload(review: WeeklyReview) -> dict[str, Any]:
    """The parts of a review the LLM should reason from — and no more.

    Deliberately omits the per-cell skill table. The prompt already carries
    rolling stats in MODEL TRACK RECORD; handing over a second table of raw
    per-model percentages would invite precisely the by-eye comparison the
    findings gate exists to prevent. The findings ARE the cross-model
    conclusions, already gated on sample size, and their absence is
    meaningful information rather than an omission.
    """
    return {
        "period_start": format_date(review.period_start),
        "period_end": format_date(review.period_end),
        "days_with_predictions": review.days_with_predictions,
        "days_verified": review.days_verified,
        "data_sufficiency": review.data_sufficiency,
        "findings": [asdict(f) for f in review.findings],
    }


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
        # Full span, not a fixed 40 days: Open-Meteo revises recent
        # observations, so a bounded re-fetch would leave older revisions
        # permanently unapplied to the all-time figures now derived from
        # them. Still one archive call regardless of range.
        _batch_log_dates = log_store.list_log_dates(deps.data_dir)
        batch_start = min(
            add_days(today, -ACTUALS_BATCH_LOOKBACK_DAYS),
            min(_batch_log_dates) if _batch_log_dates else add_days(today, -ACTUALS_BATCH_LOOKBACK_DAYS),
        )
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

    # Retention follows the LOG history, not a fixed window. All-time is now
    # re-derived by walking every stored prediction, so an actuals cache that
    # falls behind the log would silently shrink the headline number rather
    # than fail. Keeps at least the old 45-day window, and more once the log
    # is older than that. Cost is ~400 bytes/day (~146 KB/year).
    log_dates_for_retention = log_store.list_log_dates(deps.data_dir)
    fixed_window_cutoff = add_days(today, -(ACTUALS_BATCH_LOOKBACK_DAYS + 5))
    prune_cutoff = (
        min(fixed_window_cutoff, min(log_dates_for_retention))
        if log_dates_for_retention
        else fixed_window_cutoff
    )
    actuals_cache_store.prune_older_than(cache.primary, prune_cutoff)
    actuals_cache_store.prune_older_than(cache.secondary, prune_cutoff)
    actuals_primary = actuals_cache_store.as_date_dict(cache.primary)

    # --- Step 3: deterministic verification + rolling stats ---
    log_lookup = log_store.make_log_lookup(deps.data_dir)
    prior_track_record = track_record_store.read_track_record(deps.data_dir)
    verification_result = run_deterministic_verification_and_scoring(
        log_lookup=log_lookup,
        prior_track_record=prior_track_record,
        earliest_log_date=min(log_dates_for_retention) if log_dates_for_retention else None,
        actuals_primary=actuals_primary,
        today=today,
        yesterday=yesterday,
        models=scored_models(location.local_bulletin_model_id),
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

    # Deterministic day-over-day comparison for the Overview — in code, not
    # the LLM. A live run asked to compare 29.6°C against 29.5°C described it
    # as "about 1°C cooler": a ten-fold overstatement of the one sentence
    # most readers actually act on. See comparison.py.
    day_over_day = compute_day_over_day(actuals_primary.get(yesterday), day0_predictions)
    # The local met service's own forecast, scored as another model. Its
    # prediction comes from the same bulletin fetch that already happened for
    # the narrative, so this costs no additional request — and it is decoded
    # in code, so it costs no LLM call either. Only accepted when the
    # bulletin says it is valid for TODAY: KMD issues at ~3pm for the
    # following day, so a run finding yesterday's bulletin still current
    # would otherwise score it against the wrong day's weather.
    met_prediction = getattr(guidance, "met_service_prediction", None)
    met_valid_for = getattr(guidance, "met_service_valid_for", None)
    if met_prediction is not None and met_valid_for == today:
        day0_predictions = [*day0_predictions, met_prediction]

    day3_predictions = extract_day_n_predictions_from_daily(primary_daily, 3, MODELS)
    # Same guard as Day+0, for the same reason: the five-day bulletin is
    # accepted only if it actually covers today+3. A stale issue whose range
    # has rolled past that date must contribute nothing rather than be
    # scored against a day it never forecast.
    met_day3 = getattr(guidance, "met_service_prediction_day3", None)
    if met_day3 is not None and getattr(guidance, "met_service_day3_valid_for", None) == add_days(today, 3):
        day3_predictions = [*day3_predictions, met_day3]
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
    # Long-run review findings, recomputed from the raw record every run
    # rather than stored — same reasoning as every other statistic here: a
    # figure that can only be re-derived is a figure that can be checked,
    # and one that's carried forward is one that can silently go stale.
    # Pure computation over data already in memory, so it costs no API call.
    review_context = _review_prompt_payload(
        build_weekly_review(
            log_lookup=log_lookup,
            actuals=actuals_primary,
            all_log_dates=log_store.list_log_dates(deps.data_dir),
            today=today,
            models=scored_models(location.local_bulletin_model_id),
        )
    )
    # The same extracted values that get scored, handed to the LLM so its
    # narrative and the accuracy record describe one set of numbers rather
    # than two. This is also where the met service becomes visible as a peer
    # of the numerical models rather than only as prose.
    model_predictions_context = {
        "day0": [p.model_dump() for p in day0_predictions],
        "day3": [p.model_dump() for p in day3_predictions],
        "day7": [p.model_dump() for p in day7_predictions],
    }
    user_prompt = build_user_prompt(
        today=today,
        yesterday=yesterday,
        public_webpage_url=deps.public_webpage_url,
        verification_context=verification_context,
        model_predictions_context=model_predictions_context,
        track_record_context=track_record_context,
        historical_logs=historical_logs,
        ground_aqi_readings=_ground_aqi_prompt_payload(guidance),
        ground_aqi_summary=asdict(guidance.ground_aqi_summary) if guidance.ground_aqi_summary is not None else None,
        # What actually HAPPENED yesterday, so the Overview can open with a
        # real day-over-day comparison. Distinct from verification_context,
        # which is how yesterday's predictions SCORED. Free: this is the same
        # cache the verification pass already read.
        yesterday_actual=asdict(day_over_day) if day_over_day is not None else None,
        review_context=review_context,
        today_weather_data={
            "primary_today_hourly": primary_hourly,
            "primary_extended_daily": primary_daily,
            "secondary_today_hourly": guidance.secondary_hourly,
            "secondary_extended_daily": guidance.secondary_daily,
            "regional_pressure": guidance.regional_pressure,
            "synoptic_scale_pressure": asdict(guidance.synoptic) if guidance.synoptic is not None else None,
            "air_quality": guidance.air_quality,
            "airport_metar": guidance.airport_metar,
        },
        local_bulletin_source_name=location.local_bulletin_source_name,
        local_bulletin_text=guidance.bulletin_text,
        issuance=guidance.issuance,
        forward_hourly=guidance.forward_hourly,
    )
    # Route EVERY request the provider makes through the cap — retries
    # included. Raises SpendCapExceeded, deliberately NOT caught here: the
    # run must fail loudly rather than quietly produce no forecast.
    _verify_spend = _attach_spend_cap(deps, location, purpose="forecast")
    llm_response: GeminiForecastResponse = deps.llm_provider.generate(
        system_prompt, user_prompt, GeminiForecastResponse
    )
    _verify_spend()

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
        # From code, not from the narrative. Empty strings mean the sun times
        # were unavailable — see daypart_without_sun — and are stored as None
        # so an absent value is never rendered as an empty clock.
        sunrise=(guidance.issuance.sunrise or None) if guidance.issuance else None,
        sunset=(guidance.issuance.sunset or None) if guidance.issuance else None,
        model_predictions=ModelPredictionsByLead(
            day0=day0_predictions, day3=day3_predictions, day7=day7_predictions
        ),
        # Stored verbatim, and stored even when it says "unavailable" — see
        # LocalBulletinRecord. A met service's forecast cannot be re-fetched
        # for a past day once its weekly bulletin is replaced, so a run that
        # doesn't write this down destroys the only copy there will ever be.
        local_bulletin=LocalBulletinRecord(
            source_name=location.local_bulletin_source_name,
            text=guidance.bulletin_text,
            fetched_at_utc=datetime.now(timezone.utc),
        )
        if location.local_bulletin_source_name
        else None,
        yesterday_verification_summary=llm_response.yesterday_verification,
        narrative_markdown=llm_response.today_narrative,
        whatsapp_summary=llm_response.whatsapp_summary,
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider=type(deps.llm_provider).__name__,
            llm_model=getattr(deps.llm_provider, "model", "unknown"),
            pipeline_version=deps.pipeline_version,
            trigger_source=deps.trigger_source or None,
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
    # The evening run does no verification, so it never needed the actuals
    # cache before. It reads it now purely for the Overview's day-over-day
    # comparison — a local file read, no extra API call.
    _refresh_actuals = actuals_cache_store.as_date_dict(
        actuals_cache_store.read_actuals_cache(deps.data_dir).primary
    )
    _refresh_yesterday = add_days(today, -1)
    # The evening refresh deliberately keeps the morning's model_predictions,
    # so it compares against those — the numbers actually published today.
    _refresh_comparison = compute_day_over_day(
        _refresh_actuals.get(_refresh_yesterday), existing_entry.model_predictions.day0
    )
    refresh_yesterday_actual = asdict(_refresh_comparison) if _refresh_comparison is not None else None
    track_record_context = [e.model_dump() for e in track_record_store.read_track_record(deps.data_dir).entries]

    # The refresh does no verification, but the long-run findings still
    # describe which models have earned trust here — relevant to the
    # narrative even when nothing new has been scored today.
    refresh_review_context = _review_prompt_payload(
        build_weekly_review(
            log_lookup=log_store.make_log_lookup(deps.data_dir),
            actuals=_refresh_actuals,
            all_log_dates=log_store.list_log_dates(deps.data_dir),
            today=today,
        )
    )

    # The morning's stored predictions, deliberately not re-extracted: a
    # refresh keeps the numbers actually published today, and those are what
    # tomorrow's verification will score. Re-deriving them from the fresher
    # cycle would leave the narrative describing values the record doesn't
    # contain.
    refresh_predictions_context = {
        "day0": [p.model_dump() for p in existing_entry.model_predictions.day0],
        "day3": [p.model_dump() for p in existing_entry.model_predictions.day3],
        "day7": [p.model_dump() for p in existing_entry.model_predictions.day7],
    }

    system_prompt = build_system_prompt(location, is_reissue=True)
    user_prompt = build_user_prompt(
        today=today,
        yesterday=add_days(today, -1),
        model_predictions_context=refresh_predictions_context,
        public_webpage_url=deps.public_webpage_url,
        verification_context=verification_context,
        track_record_context=track_record_context,
        historical_logs=historical_logs,
        ground_aqi_readings=_ground_aqi_prompt_payload(guidance),
        ground_aqi_summary=asdict(guidance.ground_aqi_summary) if guidance.ground_aqi_summary is not None else None,
        yesterday_actual=refresh_yesterday_actual,
        review_context=refresh_review_context,
        today_weather_data={
            "primary_today_hourly": guidance.primary_hourly,
            "primary_extended_daily": guidance.primary_daily,
            "secondary_today_hourly": guidance.secondary_hourly,
            "secondary_extended_daily": guidance.secondary_daily,
            "regional_pressure": guidance.regional_pressure,
            "synoptic_scale_pressure": asdict(guidance.synoptic) if guidance.synoptic is not None else None,
            "air_quality": guidance.air_quality,
            "airport_metar": guidance.airport_metar,
        },
        local_bulletin_source_name=location.local_bulletin_source_name,
        local_bulletin_text=guidance.bulletin_text,
        issuance=guidance.issuance,
        forward_hourly=guidance.forward_hourly,
        # Every issuance already published today, in order. Was a single
        # `morning_narrative`, which assumed the day has exactly two runs; an
        # operator may schedule two or five, and each one after the first
        # needs to know what its readers have already been told.
        earlier_today=_earlier_issuances(existing_entry),
    )
    # Route EVERY request the provider makes through the cap — retries
    # included. Raises SpendCapExceeded, deliberately NOT caught here: the
    # run must fail loudly rather than quietly produce no forecast.
    _verify_spend = _attach_spend_cap(deps, location, purpose="refresh")
    llm_response: GeminiForecastResponse = deps.llm_provider.generate(
        system_prompt, user_prompt, GeminiForecastResponse
    )
    _verify_spend()

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
            # Set here too, not only on the day's first run.
            #
            # Missed when sunrise/sunset were added: only run_daily_pipeline
            # set them, so any day whose entry was refreshed carried nulls
            # from that point on — the site simply stopped showing sun times
            # after the evening run, which nothing would have flagged.
            #
            # Falls back to what is already stored rather than overwriting
            # with None: a failed sun fetch on a re-issue must not erase a
            # good value the morning run captured.
            "sunrise": (guidance.issuance.sunrise or None if guidance.issuance else None)
            or existing_entry.sunrise,
            "sunset": (guidance.issuance.sunset or None if guidance.issuance else None)
            or existing_entry.sunset,
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
