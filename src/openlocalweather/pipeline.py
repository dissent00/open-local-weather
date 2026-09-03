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
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from openlocalweather import __version__
from openlocalweather.aqi import (
    GroundAQILastKnown,
    GroundAQISummary,
    hours_old,
    is_stale,
    last_known_ground_aqi,
    merge_ground_aqi,
    summarize_ground_aqi,
)
from openlocalweather.instability import InstabilityOutlook, summarize_instability
from openlocalweather.comparison import compute_day_over_day
from openlocalweather.daypart import (
    DayPart,
    daypart_without_sun,
    forward_hours,
    reconcile_now,
    summarize_daypart,
)
from openlocalweather.config import LocationConfig
from zoneinfo import ZoneInfo

from openlocalweather.baselines import (
    climatology_prediction,
    persistence_prediction,
)
from openlocalweather.cycle import (
    aligned_cycle_at,
    next_aligned_window,
    round_hours_to_tenths,
)
from openlocalweather.dates import (
    add_days,
    format_date,
    now_in_tz,
    today_in_tz,
    utc_offset_seconds,
)
from openlocalweather.defaults import (
    ACTUALS_BATCH_LOOKBACK_DAYS,
    BASELINE_MODEL_IDS,
    BLEND_MODEL_ID,
    HISTORICAL_LOOKBACK_DAYS,
    MODELS,
    WEEKLY_BATCH_WEEKDAY,
    models_visible_to_the_forecaster,
    scored_models,
)
from openlocalweather.extract import (
    extract_day0_predictions_from_hourly,
    extract_day_n_predictions_from_daily,
)
from openlocalweather.fetch import metar as metar_fetch
from openlocalweather.fetch import model_run as model_run_fetch
from openlocalweather.fetch import open_meteo
from openlocalweather.fetch import waqi as waqi_fetch
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.llm.prompt import build_system_prompt, build_user_prompt
from openlocalweather.review import WeeklyReview, build_weekly_review
from openlocalweather import solar
from openlocalweather.spend import assert_capacity, record_attempt
from openlocalweather.synoptic import summarize_synoptic
from openlocalweather.llm.provider import LLMProvider
from openlocalweather.llm.schema import GeminiForecastResponse, TodayProperties
from openlocalweather.models import (
    DEGRADATION_HOURS_AHEAD_NARROWED,
    DEGRADATION_METAR,
    DEGRADATION_SYNOPTIC,
    SOURCE_STATION,
    DEGRADATION_SUN_TIMES,
    LocalBulletinRecord,
    DailyActual,
    DailyLogEntry,
    GroundAQIReading,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
    RunDegradation,
    TrackRecord,
    format_temp_high_low,
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


@dataclass
class ForecastSkipped:
    """Returned when a trigger repeats one that just ran — see run_forecast.

    A skip is a success, not a failure: a duplicate trigger is the system
    working (a backup schedule slot firing behind a primary that already
    delivered), and it must not colour a workflow run red.
    """

    today: date
    reason: str


# How recently a run has to have happened for the next trigger to be a
# repeat of it rather than a new issuance.
#
# The backup schedule slots sit +15/+30/+45 minutes behind each primary, and
# an operator's crontab may aim at the same minute as GitHub's own scheduler,
# so anything under an hour is a duplicate of the run before it. The cost of
# the bound is that runs scheduled less than an hour apart are refused; four
# runs a day, the most anyone has wanted, is six hours apart.
MIN_REISSUE_INTERVAL_MINUTES = 60


class RefreshWithoutMorningRunError(RuntimeError):
    """Raised when run_refresh_pipeline() is called for a date with no
    existing log entry — there is nothing to refresh, and silently creating
    a "morning" entry from an evening run would mean model_predictions were
    extracted from evening-cycle data, not what was actually true at 6 AM
    when a run_daily_pipeline() call would normally have captured them.
    """


@dataclass
class ResolvedGuidanceCycle:
    """The model cycle actually behind this run's guidance, resolved once so
    every reader — the log entry, the snapshot archived on a re-issue —
    derives its three stored values from one place. Either OBSERVED (Open-
    Meteo's own record of the last ecmwf_ifs025 run, once it has settled —
    fetch/model_run.py's fetch_settled_run) or DERIVED (cycle.
    aligned_cycle_at's inferred floor, used whenever the observation is
    unavailable or has not yet settled). See _resolve_guidance_cycle below.
    """

    initialised_at: datetime
    age_hours: float
    source: str  # "observed" or "derived"


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
    # What to say when nothing is fresh, and whether the afternoon is
    # unstable enough to belong in the Overview. Both live here rather than
    # in each run's own code so the morning and the refresh cannot drift.
    ground_aqi_last_known: GroundAQILastKnown | None
    instability: InstabilityOutlook | None

    # True when the forward fetch failed and the window came from the day-0
    # fetch instead — the rest of today, with nothing past midnight.
    forward_window_narrowed: bool

    # What this run did not have, in the form the record stores. See
    # models.RunDegradation and ROADMAP item 53.4: the stderr lines each of
    # these mirrors were the ONLY trace of three degraded runs, and stderr is
    # not somewhere anyone looks until after a reader has been rained on.
    degradations: list[RunDegradation]

    aqi_fetch_time: datetime
    bulletin_text: str
    guidance_cycle: ResolvedGuidanceCycle
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


def _issuances_for_prompt(entry: DailyLogEntry) -> list[dict]:
    """Today's already-published narratives, oldest first.

    One dict per issuance in entry.issuance_log() — now the model's concept,
    not this module's. Used to return only entry.narrative_markdown, one
    element, so a third run was shown the second issuance and had no idea
    the first one ever existed even though morning_issuance still held it.
    """
    return [
        {"time": _clock(issuance.generated_at_utc) or "earlier today", "narrative": issuance.narrative_markdown}
        for issuance in entry.issuance_log()
    ]


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


def _sun_context(location, now_local: datetime, clock_reference: dict) -> tuple[DayPart, datetime]:
    """Sunrise/sunset for today and tomorrow, reduced to the issuance moment.

    COMPUTED, not fetched — see `solar` for the six days of null sun times
    that motivated the change, and for how far the computation can be trusted.

    `clock_reference` is any Open-Meteo response already fetched this run. It
    is read for two things only: the server's `Date` header and the location's
    UTC offset, which together are an independent check on this machine's
    clock. That check used to ride on the sun fetch; with the sun fetch gone
    it rides on a mandatory one instead, which makes it strictly harder to
    lose. See `daypart.reconcile_now` — the host's own timezone setting is
    irrelevant, but its clock being wrong is silent, and would produce a
    forecast written confidently for the wrong part of the day.

    Naive local throughout, because `now_in_tz` and Open-Meteo's hourly
    timestamps both are; see its docstring on why mixing the two would be
    worse than either.
    """
    now_local, skew_warning = reconcile_now(
        now_local,
        (clock_reference or {}).get("_server_date"),
        (clock_reference or {}).get("utc_offset_seconds"),
    )
    if skew_warning:
        print(f"WARNING: {skew_warning}", file=sys.stderr)

    lat, lon = location.primary_point.lat, location.primary_point.lon
    today = now_local.date()
    tomorrow = add_days(today, 1)

    # A separate offset per date rather than one for both, so a run on a
    # daylight-saving changeover does not report tomorrow's sunrise an hour
    # out. The location's zone, never this host's — see dates.utc_offset_seconds.
    sun = solar.sun_times(lat, lon, today, utc_offset_seconds(location.timezone, today))
    next_sun = solar.sun_times(lat, lon, tomorrow, utc_offset_seconds(location.timezone, tomorrow))

    return summarize_daypart(now_local, sun.sunrise, sun.sunset, next_sun.sunrise), now_local


def apply_station_readings(actual: DailyActual, measured) -> None:
    """Store what the station MEASURED on one day, beside the reanalysis.

    Shared by the daily pipeline and `rebuild-record` so the two cannot drift:
    a rebuild that dropped these would silently erase weeks of the
    accumulation item 45's sequencing depends on, and it would look like the
    station having said nothing.

    STORED, NOT SCORED. Nothing downstream reads these yet, and that is the
    point — cross-check before replacement. `None` values are skipped rather
    than written, because a stamp asserts an observation was made.
    """
    if measured is None:
        return

    for field, value in (
        ("station_high_c", measured.high_c),
        ("station_low_c", measured.low_c),
        ("station_peak_wind_kmh", measured.peak_wind_kmh),
    ):
        if value is None:
            continue
        setattr(actual, field, value)
        actual.provenance = {**(actual.provenance or {}), field: SOURCE_STATION}


def _apply_station_observations(
    actuals: dict[date, DailyActual], location: LocationConfig
) -> None:
    """Stamps what the airport observed — thunder and precipitation — onto the
    days just bucketed from the reanalysis archive.

    PRIMARY POINT ONLY. The METAR station sits at the primary place; the
    secondary point is a lake position that can be a hundred kilometres away,
    and convection there is genuinely a different event. Copying one onto the
    other would invent an observation.

    Silent no-op when no ICAO is configured or the archive is unreachable,
    which leaves every flag at None — "not observed", never "nothing
    happened". Runs in both the daily and the weekly-batch branch, so a
    re-fetch reapplies it rather than quietly dropping it.
    """
    if not actuals:
        return

    # ONE FETCH for both what the station SAW and what it MEASURED — the
    # archive request is the slowest call in the verification pass.
    weather_by_date, readings_by_date = metar_fetch.observed_station_data(
        location.metar_station_icao, min(actuals), max(actuals), location.timezone
    )
    if weather_by_date is None:
        return

    for day, actual in actuals.items():
        if day not in weather_by_date:
            continue

        observed = weather_by_date[day]
        actual.thunder = observed.thunder
        actual.precipitation = observed.precipitation
        # ROADMAP item 45, trap 2: stamp the station's fields on the days it
        # actually covered, and only those. The `continue` above means an
        # uncovered day keeps whatever the archive stamped and gains nothing,
        # which is the distinction the whole trap is about — the station is
        # truth for most days and down for a few, and those few have to be
        # identifiable afterwards rather than guessed at.
        #
        # Merged into the existing dict rather than replacing it: the archive
        # supplied this day's temperature and wind and still did.
        actual.provenance = {
            **(actual.provenance or {}),
            "thunder": SOURCE_STATION,
            "precipitation": SOURCE_STATION,
        }
        if observed.precipitation_onset is not None:
            actual.provenance["precipitation_onset"] = SOURCE_STATION

        apply_station_readings(actual, (readings_by_date or {}).get(day))
        # All THREE fields, not the two booleans. Storing the flag without the
        # onset leaves the day-over-day description with no timing to reach
        # the dry band's shower phrases with, so a corrected day goes on being
        # called "dry" — see DailyActual.observed_onset and ROADMAP 53.1a.
        actual.precipitation_onset = observed.precipitation_onset


def _resolve_guidance_cycle(now: datetime) -> ResolvedGuidanceCycle:
    """Which model cycle is behind the guidance fetched at `now`: OBSERVED
    when Open-Meteo's own record of the last ecmwf_ifs025 run is in hand and
    settled, DERIVED otherwise.

    A disagreement between the two, when the observation is used, is
    printed rather than silently resolved — the derived table
    (docs-internal/ROADMAP.md) is a one-time hand measurement, and this
    comparison is the only thing that would ever say it had drifted. The
    run is not affected either way: the observation is still used, since it
    is the more trustworthy of the two answers.

    This line goes into a run log, which is a poor place to notice
    something that rots over months, so check-health runs the same
    comparison weekly and fails on it — health_check.check_aligned_window.
    Both surfaces, not one: this one records the disagreement at the moment
    it actually affected a forecast.
    """
    derived = aligned_cycle_at(now)
    observed = model_run_fetch.fetch_settled_run(now)

    if observed is not None:
        if observed.initialised_at != derived.initialised_at:
            print(
                f"WARNING: observed {observed.model} run ({observed.initialised_at.isoformat()}) "
                f"disagrees with the derived aligned cycle ({derived.initialised_at.isoformat()}) "
                f"at {now.isoformat()} — docs-internal/ROADMAP.md's measured aligned-window table "
                "may need re-measuring. Using the observation.",
                file=sys.stderr,
            )
        initialised_at, source = observed.initialised_at, "observed"
    else:
        initialised_at, source = derived.initialised_at, "derived"

    age_hours = (now - initialised_at).total_seconds() / 3600
    return ResolvedGuidanceCycle(initialised_at=initialised_at, age_hours=age_hours, source=source)


def _next_guidance_sentence(tz_name: str, now: datetime | None = None) -> str:
    """When new model guidance is next due, in the reader's own local time.

    HEDGED ON PURPOSE. The aligned-window table is a hand measurement, and
    item 50 measured ECMWF's availability varying from ~7.1 h to 8h25m — more
    than the whole hour the windows are rounded to. So "usually in by about
    17:00" is what the table supports and "at 17:00" is not. A notice that
    names an exact time and is wrong twice teaches the reader to ignore every
    notice, which costs more than saying nothing at all.

    Written as advice about WAITING, never as an instruction to regenerate:
    on the app a regeneration spends the reader's own cap (item 26), and this
    text is shared with the surface that has no way to know what that would
    cost them.
    """
    moment = now or datetime.now(timezone.utc)
    window = next_aligned_window(moment)
    local = window.opens_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
    return (
        f"New model guidance is usually in by about {local.strftime('%H:%M')} local; "
        "a forecast made after that would normally have the full window."
    )


def _fetch_forward_guidance(deps: PipelineDeps) -> ForwardGuidance:
    location = deps.location
    # Positions in the run's request sequence are what item 53's diagnostics
    # report, so they have to be per-run rather than per-process.
    open_meteo.reset_request_counter()

    degradations: list[RunDegradation] = []
    primary_hourly = open_meteo.fetch_forecast_hourly_today(
        location.primary_point.lat, location.primary_point.lon, MODELS, location.timezone
    )
    # THE FORWARD WINDOW IS FETCHED HERE, EARLY, AND THAT IS AN EXPERIMENT.
    #
    # It failed on 8 of the last 9 real runs while sitting 7th in this
    # function's sequence of /v1/forecast requests, and the call that used to
    # sit 7th (fetch_sun_times) failed identically until it was deleted — the
    # failure stayed at the POSITION rather than following the request shape.
    # See ROADMAP item 53, "It is the seventh request, not the second day".
    #
    # Moving it early is what separates the two explanations. If it succeeds
    # here, the request shape is exonerated and the fix is pacing, session
    # reuse or fewer calls; if it still fails, forecast_days=2 is the cause
    # after all. RECORD THE ANSWER IN ITEM 53 AND REVISIT THIS PLACEMENT —
    # an experiment left in place and forgotten reads as a design decision.
    #
    # It has to follow the day-0 fetch either way: the reconciled clock that
    # trims the window comes from that response.
    # Where this run sits in the day, and the hours still ahead of it.
    #
    # The clock, the sun, and the hours ahead are three separate things, and
    # they still fail separately. An earlier version put all three in one try,
    # so a failed astronomical lookup also skipped the forward window AND
    # threw away the local time — leaving the prompt to say "time of day
    # unavailable" for a run that knew perfectly well it was 18:15. Knowing
    # the time is most of the value; knowing where the sun is only refines it.
    #
    # The sun no longer fails over the network — it is computed. What the try
    # now guards is a defect in that arithmetic, and the guarantee it keeps is
    # unchanged: a run does not abort because it could not place itself in the
    # day, and a lost sun does not cost the clock as well.
    now_local = now_in_tz(location.timezone)
    try:
        # Also returns the reconciled clock: if this host's time disagrees with
        # the server's, the corrected value must reach the forward-window trim
        # below too, or the two halves of the prompt would describe different
        # moments.
        issuance, now_local = _sun_context(location, now_local, primary_hourly)
    except Exception as e:  # noqa: BLE001 - never fatal; the time still stands
        print(f"Sun times unavailable ({e}); using the clock alone.", file=sys.stderr)
        issuance = daypart_without_sun(now_local)
        degradations.append(
            RunDegradation(
                code=DEGRADATION_SUN_TIMES,
                summary=(
                    "Sunrise and sunset could not be worked out for today, so this "
                    "forecast knows the time but not where the sun is in the day."
                ),
                detail=(
                    f"Sun times could not be computed ({e}); the part-of-day context "
                    "was derived from the clock alone."
                ),
            )
        )

    forward_hourly = None
    forward_window_narrowed = False
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

    if forward_hourly is None:
        # THE DAY-0 FETCH ALREADY HAS THIS DATA. `fetch_forecast_hourly_today`
        # asks the same host and the same endpoint for the same
        # HOURLY_FORECAST_VARS — cape included — differing only in
        # forecast_days=1, and it is fetched unguarded above, so reaching this
        # line at all means it succeeded.
        #
        # Measured 2026-08-29 and 08-30: the forward call read-timed out on
        # three consecutive runs while the day-0 call succeeded in every one,
        # and the convective outlook was published as "unavailable" with a
        # 1830 J/kg UKMO peak sitting in memory. A reader was rained on that
        # evening. See ROADMAP item 53.
        #
        # WHAT THE FALLBACK DOES NOT COVER: forecast_days=1 stops at 23:00
        # local, so an evening run sees this evening and nothing past
        # midnight. That is the peak worth warning about and it is NOT the
        # whole window the prompt normally gets, which is why the narrowing
        # is flagged rather than passed off as a full window.
        print("Falling back to the day-0 hourly window (rest of today only).", file=sys.stderr)
        forward_hourly = forward_hours(primary_hourly, now_local)
        forward_window_narrowed = True
        degradations.append(
            RunDegradation(
                code=DEGRADATION_HOURS_AHEAD_NARROWED,
                summary=(
                    "Part of tonight's data did not arrive. This forecast covers the "
                    "rest of today only — where it says nothing about later tonight, "
                    "that is missing information, not a quiet night."
                ),
                detail=(
                    "The forward hourly window did not arrive, so the hours-ahead "
                    "guidance and the convective outlook were trimmed from the day-0 "
                    "fetch instead, which stops at 23:00 local. "
                    + _next_guidance_sentence(location.timezone)
                ),
            )
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
    # fetch_metar returns None for every failure path and airport_metar is
    # passed to the prompt but never persisted, so until now the record could
    # not answer "was the station consulted on that run?" — a question item 53
    # had to leave under "Not established" about its own incident.
    #
    # Only when a station IS configured. No station is a configuration, not a
    # degradation.
    if location.metar_station_icao and airport_metar is None:
        print(
            f"METAR unavailable for {location.metar_station_icao}; continuing without it.",
            file=sys.stderr,
        )
        degradations.append(
            RunDegradation(
                code=DEGRADATION_METAR,
                summary=(
                    "The nearest airport weather report was not available, so this "
                    "forecast had no live local observation to check the models against."
                ),
                detail=(
                    f"No current METAR from {location.metar_station_icao} this run. "
                    "fetch_metar returns None on every failure path, so the cause — "
                    "network, upstream outage, or a station that stopped reporting — "
                    "is not distinguished here."
                ),
            )
        )

    aqi_fetch_time = datetime.now(timezone.utc)
    # Reuses aqi_fetch_time as "now" rather than a second datetime.now()
    # call, for the same reason ground_aqi_summary/ground_aqi_last_known do
    # below: one clock read per run, so every "how old" figure this run
    # produces agrees with every other.
    guidance_cycle = _resolve_guidance_cycle(aqi_fetch_time)
    # Synoptic-scale pressure ring. One request, ~3 KB — see synoptic.py for
    # why the near-field region_points cannot answer this. Optional: losing it
    # costs a paragraph of context, not the forecast.
    try:
        synoptic = summarize_synoptic(
            open_meteo.fetch_synoptic_pressure(
                location.primary_point.lat, location.primary_point.lon, location.timezone
            )
        )
    except Exception as e:  # noqa: BLE001 - optional; costs context, not the run
        # It used to be `except Exception: synoptic = None` with no log at
        # all, so a failure here was invisible in every surface — the exact
        # shape of this project's 2026-08-29 incident, one layer along. It
        # also sits where the read timeouts have been landing (ROADMAP item
        # 53), so a silent failure here would waste the run that is meant to
        # answer them.
        print(f"Synoptic pressure ring unavailable ({e}); continuing without it.", file=sys.stderr)
        synoptic = None
        degradations.append(
            RunDegradation(
                code=DEGRADATION_SYNOPTIC,
                summary=(
                    "The wider pressure picture did not arrive, so this forecast "
                    "describes the local pattern without the regional one around it."
                ),
                detail=(
                    f"The synoptic-scale pressure ring did not arrive ({e}). The "
                    "narrative's large-scale paragraph falls back to local gradients, "
                    "which cannot locate a system's direction."
                ),
            )
        )

    ground_aqi_readings = waqi_fetch.fetch_ground_aqi_stations(location.waqi_stations, deps.waqi_token)
    ground_aqi_summary = summarize_ground_aqi(ground_aqi_readings, now=aqi_fetch_time)
    ground_aqi_last_known = last_known_ground_aqi(ground_aqi_readings, now=aqi_fetch_time)
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

    # A SECOND, SPACED ATTEMPT — ROADMAP item 53.
    #
    # `_get` already retries three times, and on all eight failing runs all
    # three timed out inside one 94.5-second burst. More of the same shape
    # buys nothing; what has never been tried is waiting for the rest of the
    # run to happen and asking again. That is the only variable the surviving
    # "something trips inside a short burst" hypothesis says matters.
    #
    # The day-0 fallback above stays the floor, so this can only improve on
    # it: a second failure lands exactly where one used to. The degradation is
    # recorded only if BOTH attempts fail, because a run that got its full
    # window in the end was not degraded and the record must not say it was.
    if forward_window_narrowed:
        try:
            retried = forward_hours(
                open_meteo.fetch_forecast_hourly_forward(
                    location.primary_point.lat,
                    location.primary_point.lon,
                    MODELS,
                    location.timezone,
                ),
                now_local,
            )
        except Exception as e:  # noqa: BLE001 - the fallback already stands
            print(f"Second forward attempt also failed ({e}); keeping the day-0 window.", file=sys.stderr)
        else:
            print("Second forward attempt succeeded; full window restored.", file=sys.stderr)
            forward_hourly = retried
            forward_window_narrowed = False
            degradations = [
                d for d in degradations if d.code != DEGRADATION_HOURS_AHEAD_NARROWED
            ]

    # From the trimmed forward window, never the calendar day: a CAPE peak
    # that already passed this morning is not a reason to warn about tonight.
    instability = summarize_instability(forward_hourly or {}, MODELS)

    return ForwardGuidance(
        issuance=issuance,
        forward_hourly=forward_hourly,
        forward_window_narrowed=forward_window_narrowed,
        degradations=degradations,
        ground_aqi_last_known=ground_aqi_last_known,
        instability=instability,
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
        guidance_cycle=guidance_cycle,
        synoptic=synoptic,
        met_service_prediction=met_prediction,
        met_service_valid_for=met_valid_for,
        met_service_prediction_day3=met_day3,
        met_service_day3_valid_for=met_day3_valid_for,
    )


def _blend_prediction(tp: TodayProperties) -> ModelPrediction:
    """The forecaster's own Day+0 call, in the form the record can score.

    Every INPUT to a forecast was scored and the OUTPUT was not: the blended
    call a reader actually reads had no accuracy record, while `best_match` —
    Open-Meteo's own blend — did. So the record could say which model was best
    and never whether synthesizing them helped.

    Built from the STRUCTURED fields rather than parsed back out of the prose,
    so what is scored is what the forecaster committed to rather than what a
    regex could recover from a sentence.
    """
    return ModelPrediction(
        model=BLEND_MODEL_ID,
        rain=tp.rain,
        onset=tp.onset_hour,
        high_c=tp.temp_high_c,
        low_c=tp.temp_low_c,
        precip_mm=tp.precip_mm,
        # The forecaster's own probability, carried into the scored prediction
        # so it is Brier-scored as a peer of the models it synthesizes —
        # ROADMAP item 58. Without this line the field would be collected and
        # never checked, which is the one outcome worse than not asking.
        rain_probability_pct=tp.rain_probability_pct,
        # Deliberately absent, not zero. peak_wind_kmh in today_properties is
        # the SECONDARY point's, and mslp_trend_24h is prose; scoring either
        # against the primary point's observations would be comparing two
        # different things. A null reads as "not forecast" everywhere in this
        # record, which is the truthful answer until both are structured.
        wind_kmh=None,
        mslp_trend=None,
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


def _guidance_recency_payload(guidance: ForwardGuidance, previous: DailyLogEntry | None) -> dict | None:
    """How old the guidance behind this run is, as the prompt sees it.

    `newer_than_previous_issuance` is the field that matters on a re-issue,
    and it is computed here rather than left to the model because the model
    cannot subtract two timestamps it was never given. None on a day's first
    run, and on a re-issue of an entry written before this was recorded —
    both mean "no basis for the comparison", which is different from false.

    The key is named for the FLOOR it describes, not for a cycle, so that a
    reader of the prompt cannot mistake it for a claim about every model.
    See cycle.py's docstring.
    """
    cycle = guidance.guidance_cycle
    # A negative age means the cycle behind our guidance initialised in the
    # future, which is not a thing that happens — it means this machine's
    # clock is wrong, or the provider reported something impossible. Either
    # way we do not know how old the data is, so say that rather than hand
    # the model a confident negative number to narrate. The stored value
    # keeps whatever was computed; the RECORD should show the anomaly even
    # though the prompt cannot use it. This project already treats the system
    # clock as worth a second opinion — see daypart.reconcile_now.
    if cycle.age_hours < 0:
        return None

    newer = None
    if previous is not None and previous.guidance_initialised_at is not None:
        if cycle.initialised_at > previous.guidance_initialised_at:
            newer = True
        elif cycle.initialised_at == previous.guidance_initialised_at:
            newer = False
        else:
            # OLDER than what the previous issuance recorded, which is not a
            # thing the world does — cycles only move forward. It means this
            # run fell back to the derived floor while the last one had a real
            # observation, so we know LESS than the run before us did. That is
            # not "no new guidance has landed" (which licenses a short, quiet
            # update), it is no basis for the comparison at all. None says so.
            newer = None

    return {
        "models_last_aligned_at": cycle.initialised_at.isoformat(),
        "hours_old": round_hours_to_tenths(cycle.age_hours),
        "source": cycle.source,
        "newer_than_previous_issuance": newer,
    }


def _predictions_already_recorded(entry: DailyLogEntry | None) -> ModelPredictionsByLead | None:
    """The scored predictions this date already holds, or None if it holds
    none.

    Empty lists count as none: an entry carrying no predictions has nothing
    to protect, and refusing to write would leave that date permanently
    unscoreable.
    """
    if entry is None:
        return None

    stored = entry.model_predictions
    if not (stored.day0 or stored.day3 or stored.day7):
        return None

    return stored


def _model_predictions_prompt_payload(
    day0: list[ModelPrediction], day3: list[ModelPrediction], day7: list[ModelPrediction]
) -> dict:
    """The extracted predictions as the forecaster is allowed to see them —
    its own blend and the two baselines removed, at every lead.

    All three are stored, scored and published as peer models, and withheld
    from the prompt permanently: see models_visible_to_the_forecaster for the
    blend's reasoning and the baselines' adjacent one. This is the third block
    they can leak through, after the track record and the review findings, and
    the only one carrying predictions rather than scores.

    The blend leaks here only on a re-issue, which is handed the day's STORED
    predictions — and the stored Day+0 list has the blend in it. The baselines
    leak on EVERY run and at every lead, because unlike the blend they are
    built before the prompt rather than after it, so the filter cannot be
    Day+0 only.
    """
    hidden = {BLEND_MODEL_ID, *BASELINE_MODEL_IDS}
    return {
        "day0": [p.model_dump() for p in day0 if p.model not in hidden],
        "day3": [p.model_dump() for p in day3 if p.model not in hidden],
        "day7": [p.model_dump() for p in day7 if p.model not in hidden],
    }


def _with_merged_ground_aqi(
    guidance: ForwardGuidance, stored: list[GroundAQIReading]
) -> ForwardGuidance:
    """A re-issue's guidance, with its ground AQI merged against what the
    day's entry already holds — see aqi.merge_ground_aqi for why a fresher
    null must not overwrite an older real reading.

    The summary and the last-known reading are recomputed from the merged
    list rather than carried over, so the prompt, the stored entry and the
    published page all describe one set of readings. Recomputing is also
    what lets the narrative quote a kept reading with its true age instead
    of reporting nothing.
    """
    merged = merge_ground_aqi(stored, guidance.ground_aqi_readings)
    return replace(
        guidance,
        ground_aqi_readings=merged,
        ground_aqi_summary=summarize_ground_aqi(merged, now=guidance.aqi_fetch_time),
        ground_aqi_last_known=last_known_ground_aqi(merged, now=guidance.aqi_fetch_time),
    )


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
        primary_actuals = open_meteo.bucket_hourly_by_date(primary_archive)
        _apply_station_observations(primary_actuals, location)
        actuals_cache_store.replace_all(cache, "primary", primary_actuals)
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
        primary_actuals = open_meteo.bucket_hourly_by_date(primary_archive)
        _apply_station_observations(primary_actuals, location)
        for d, actual in primary_actuals.items():
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

    # The yardsticks — ROADMAP item 57. Built from the stored record rather
    # than fetched, so they cost nothing and cannot fail a run.
    #
    # BOTH READ ONLY WHAT THE FORECAST COULD SEE. Persistence repeats the last
    # observation available at issuance, which for a run on day D is D-1 at
    # EVERY lead time — the lead is a property of the target, not of what the
    # forecaster could see when it issued. Climatology reads the record
    # strictly before D. A baseline that could read the day it is forecasting
    # would score near-perfectly and make every real model look hopeless, and
    # nothing about the page would look broken.
    baselines = [
        p
        for p in (
            persistence_prediction(actuals_primary.get(yesterday), include_onset=True),
            climatology_prediction(actuals_primary, before=today),
        )
        if p is not None
    ]
    day0_predictions = [*day0_predictions, *baselines]

    # Onset is dropped beyond Day+0 because the real models have none there —
    # extract_day_n_predictions_from_daily cannot produce one — and scoring a
    # baseline on a field its competitors cannot answer is not measuring the
    # same thing they are.
    baselines_no_onset = [p.model_copy(update={"onset": None}) for p in baselines]
    day3_predictions = [*day3_predictions, *baselines_no_onset]
    day7_predictions = [*day7_predictions, *baselines_no_onset]

    # The day's predictions are written once, and the first write wins.
    #
    # A second full run re-extracts them from a later model cycle, and
    # tomorrow scores those as though they had been issued at 06:00 —
    # every model's Day+0 accuracy improves and nothing in the record shows
    # why. The guards that used to prevent it both live OUTSIDE this
    # pipeline: daily.yml's already_done condition, and a crontab on a
    # machine this repo cannot see or test. Neither survives someone ticking
    # `force` on a manual dispatch.
    #
    # THE TRIGGER IS UNTRUSTED INPUT. A caller must be able to invoke this
    # with any combination of flags and be unable to corrupt the record; the
    # worst it should achieve is a wasted API call. Same rule and same
    # reasoning as HistoryStore.savePredictions in the app.
    #
    # `force` still forces the NARRATIVE, which is the only thing it was
    # ever wanted for. The blend is dropped from the day0 list here because
    # everything downstream of this point treats day0_predictions as the
    # models' own calls — the stored value below keeps it.
    #
    # day_over_day above is deliberately left reasoning from the fresh
    # cycle: it frames today against yesterday for the prose and is not part
    # of the scored record.
    existing_entry = log_lookup(today)
    recorded_predictions = _predictions_already_recorded(existing_entry)
    if recorded_predictions is not None:
        # ONLY the blend is stripped here, and deliberately not the baselines,
        # even though both are hidden from the forecaster. This list is
        # re-stored below with `_blend_prediction(tp)` appended, so dropping
        # the blend prevents a duplicate; dropping the baselines would delete
        # them from the day's record on every re-run. The prompt's own copy is
        # filtered separately, in _model_predictions_prompt_payload.
        day0_predictions = [p for p in recorded_predictions.day0 if p.model != BLEND_MODEL_ID]
        day3_predictions = recorded_predictions.day3
        day7_predictions = recorded_predictions.day7

    # --- Step 6: call the LLM ---
    # A location with no WAQI stations gets a prompt with no ground-station
    # guidance and no GROUND AQI blocks at all, rather than a daily note that
    # no station reported — nothing reported because nothing was configured.
    ground_stations_configured = bool(location.waqi_stations)
    # Same distinction for the met service: a location with none wired is a
    # state, not a fetch that came back empty. LocalBulletinRecord already
    # keys off this same field for whether to store a bulletin at all.
    local_bulletin_configured = bool(location.local_bulletin_source_name)
    # A run on a day that already has an entry is a later issuance, whatever
    # verb was typed. Told otherwise it writes a fresh morning-style forecast
    # over one the readers have already had, and emails it as the day's first.
    system_prompt = build_system_prompt(
        location,
        is_reissue=existing_entry is not None,
        ground_stations_configured=ground_stations_configured,
        local_bulletin_configured=local_bulletin_configured,
    )
    verification_context = [
        {
            "lead_time_days": r.lead_time_days,
            "target_date_verified": format_date(r.target_date_verified) if r.target_date_verified else None,
            "per_model_scores": {model: score.model_dump() for model, score in r.per_model_scores.items()},
        }
        for r in verification_result.lead_time_results
    ]
    # The forecaster's own blend and the two baselines are scored and
    # published, and withheld from its context — see
    # models_visible_to_the_forecaster for why each is a standing rule rather
    # than a temporary omission.
    #
    # Tested against that list rather than against a hand-written exclusion.
    # This filter and the two others like it each used to name BLEND_MODEL_ID
    # directly, so adding a second hidden model meant remembering three
    # places; the baselines leaked through exactly this block on the first
    # attempt, in a prompt nobody would have read closely.
    forecaster_models = models_visible_to_the_forecaster(location.local_bulletin_model_id)
    track_record_context = [
        e.model_dump()
        for e in verification_result.updated_track_record.entries
        if e.model in forecaster_models
    ]
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
            models=forecaster_models,
        )
    )
    # The same extracted values that get scored, handed to the LLM so its
    # narrative and the accuracy record describe one set of numbers rather
    # than two. This is also where the met service becomes visible as a peer
    # of the numerical models rather than only as prose.
    model_predictions_context = _model_predictions_prompt_payload(
        day0_predictions, day3_predictions, day7_predictions
    )
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
        ground_aqi_last_known=asdict(guidance.ground_aqi_last_known) if guidance.ground_aqi_last_known is not None else None,
        ground_stations_configured=ground_stations_configured,
        local_bulletin_configured=local_bulletin_configured,
        instability=asdict(guidance.instability) if guidance.instability is not None else None,
        guidance_recency=_guidance_recency_payload(guidance, existing_entry),
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
        forward_window_narrowed=guidance.forward_window_narrowed,
        earlier_today=_issuances_for_prompt(existing_entry) if existing_entry is not None else None,
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
        temp_high_low_display=format_temp_high_low(tp.temp_high_c, tp.temp_low_c),
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
        # Whatever this date already holds, byte-for-byte, or the freshly
        # extracted set on the day's first run — see the write-once note in
        # Step 5. This run's own blend is not appended to a kept set either:
        # it is a prediction like any other and the day's belongs to the run
        # that made it first.
        model_predictions=recorded_predictions
        or ModelPredictionsByLead(
            # The blend joins Day+0 as a peer of the models it synthesizes, so
            # tomorrow scores the forecast this run actually published and not
            # only the guidance that fed it. Day+0 only: today_properties is a
            # call about today, and there is no extended-range equivalent to
            # score until the outlook carries structured numbers too.
            day0=[*day0_predictions, _blend_prediction(tp)],
            day3=day3_predictions,
            day7=day7_predictions,
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
        guidance_initialised_at=guidance.guidance_cycle.initialised_at,
        guidance_age_hours=guidance.guidance_cycle.age_hours,
        guidance_source=guidance.guidance_cycle.source,
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider=type(deps.llm_provider).__name__,
            llm_model=getattr(deps.llm_provider, "model", "unknown"),
            pipeline_version=deps.pipeline_version,
            trigger_source=deps.trigger_source or None,
            degradations=guidance.degradations,
        ),
    )

    # A later run of the day rewrites the narrative. It must not rewrite the
    # day's own history with it.
    #
    # Traced on a real sequence — morning run, evening refresh, forced
    # run-daily: the third run built a brand-new entry, which wiped
    # morning_issuance (the only copy of what was published this morning),
    # reset generated_at_utc so the entry claimed to have been created hours
    # after it was, and cleared refreshed_at — which re-opened
    # evening_refresh's gate, so the NEXT refresh would snapshot the forced
    # narrative as that day's morning issuance.
    #
    # yesterday_verification_summary and verification are carried for a
    # related reason: this run is now told it is a later issuance, so the
    # model returns a PLACEHOLDER for the verification fields (by design —
    # see the LATER ISSUANCE block). Storing that would overwrite the real
    # verification the day's first run wrote.
    if existing_entry is not None:
        current_snapshot = existing_entry.to_issuance_snapshot()
        log_entry = log_entry.model_copy(
            update={
                "morning_issuance": existing_entry.morning_issuance or current_snapshot,
                "earlier_issuances": [*existing_entry.earlier_issuances, current_snapshot],
                "verification": existing_entry.verification,
                "yesterday_verification_summary": existing_entry.yesterday_verification_summary,
                "meta": log_entry.meta.model_copy(
                    update={
                        "generated_at_utc": existing_entry.meta.generated_at_utc,
                        "refreshed_at": datetime.now(timezone.utc),
                    }
                ),
            }
        )

    published = False
    emailed = False

    # --- Step 8: write files + patch historical rows/track record ---
    if not dry_run:
        log_store.write_log_entry(deps.data_dir, log_entry)
        actuals_cache_store.write_actuals_cache(deps.data_dir, cache)

        # A later issuance does no verification, and returns a placeholder for
        # these fields by design. The row it would land on was scored by the
        # day's first run, and the note there is the record of what was
        # actually checked — re-running the scoring pass is idempotent, but
        # overwriting its note with "no new verification this run" is not.
        notes_by_lead = (
            {}
            if existing_entry is not None
            else {n.lead_time_days: n.note for n in llm_response.verification_notes}
        )
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


def run_forecast(
    deps: PipelineDeps,
    today: date | None = None,
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> PipelineRunResult | RefreshRunResult | ForecastSkipped:
    """The day's forecast, whichever run of the day this is.

    One verb, because the operator was picking between two by time of day and
    that is the wrong axis. The real distinction has nothing to do with the
    clock:

      - The FIRST run of a day owns verification and the day's
        model_predictions — the numbers tomorrow scores.
      - EVERY later run is an update: narrative only, predictions preserved.

    A dispatcher, not a rewrite. run_daily_pipeline and run_refresh_pipeline
    keep their own bodies, because the two have genuinely different
    responsibilities and merging them would lose the invariant that makes the
    accuracy record trustworthy.

    Repeat triggers are skipped here rather than in a workflow condition. A
    YAML `if:` and a crontab line are not code this repo can test, and both
    are the operator's to get wrong; a caller must be able to invoke this
    thing with any combination of flags and be unable to corrupt the record —
    the worst it should achieve is a wasted API call, and the skip means it
    does not even achieve that. `force` overrides the skip and nothing else:
    since the write-once guard it cannot reach the scored numbers.
    """
    location = deps.location
    today = today or today_in_tz(location.timezone)
    existing_entry = log_store.read_log_entry(deps.data_dir, today)

    if existing_entry is None:
        return run_daily_pipeline(deps, today=today, dry_run=dry_run)

    now = now or datetime.now(timezone.utc)
    age_minutes = (now - existing_entry.last_issued_at).total_seconds() / 60
    if not force and age_minutes < MIN_REISSUE_INTERVAL_MINUTES:
        return ForecastSkipped(
            today=today,
            reason=(
                f"a forecast for {today} was issued {age_minutes:.0f} minute(s) ago; "
                f"this trigger repeats it. Re-issues are {MIN_REISSUE_INTERVAL_MINUTES} "
                "minutes apart at the closest — pass force to override."
            ),
        )

    return run_refresh_pipeline(deps, today=today, dry_run=dry_run)


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
    guidance = _with_merged_ground_aqi(guidance, existing_entry.ground_aqi)

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
    # Same filter as the morning run, for the same standing reason: the blend
    # and the baselines are scored and published, and never shown to the
    # forecaster — see models_visible_to_the_forecaster.
    _refresh_forecaster_models = models_visible_to_the_forecaster(
        deps.location.local_bulletin_model_id
    )
    track_record_context = [
        e.model_dump()
        for e in track_record_store.read_track_record(deps.data_dir).entries
        if e.model in _refresh_forecaster_models
    ]

    # The refresh does no verification, but the long-run findings still
    # describe which models have earned trust here — relevant to the
    # narrative even when nothing new has been scored today.
    refresh_review_context = _review_prompt_payload(
        build_weekly_review(
            log_lookup=log_store.make_log_lookup(deps.data_dir),
            actuals=_refresh_actuals,
            all_log_dates=log_store.list_log_dates(deps.data_dir),
            today=today,
            models=models_visible_to_the_forecaster(location.local_bulletin_model_id),
        )
    )

    # The morning's stored predictions, deliberately not re-extracted: a
    # refresh keeps the numbers actually published today, and those are what
    # tomorrow's verification will score. Re-deriving them from the fresher
    # cycle would leave the narrative describing values the record doesn't
    # contain.
    refresh_predictions_context = _model_predictions_prompt_payload(
        existing_entry.model_predictions.day0,
        existing_entry.model_predictions.day3,
        existing_entry.model_predictions.day7,
    )

    ground_stations_configured = bool(location.waqi_stations)
    local_bulletin_configured = bool(location.local_bulletin_source_name)
    system_prompt = build_system_prompt(
        location,
        is_reissue=True,
        ground_stations_configured=ground_stations_configured,
        local_bulletin_configured=local_bulletin_configured,
    )
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
        ground_aqi_last_known=asdict(guidance.ground_aqi_last_known) if guidance.ground_aqi_last_known is not None else None,
        ground_stations_configured=ground_stations_configured,
        local_bulletin_configured=local_bulletin_configured,
        instability=asdict(guidance.instability) if guidance.instability is not None else None,
        guidance_recency=_guidance_recency_payload(guidance, existing_entry),
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
        forward_window_narrowed=guidance.forward_window_narrowed,
        # Every issuance already published today, in order. Was a single
        # `morning_narrative`, which assumed the day has exactly two runs; an
        # operator may schedule two or five, and each one after the first
        # needs to know what its readers have already been told.
        earlier_today=_issuances_for_prompt(existing_entry),
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
    # Before overwriting them, snapshot the existing entry's own version of
    # those same fields — current_snapshot always gets appended below to
    # earlier_issuances, since it is by definition an issuance that
    # happened before the one this run is about to write.
    #
    # morning_issuance is different: it must be snapshotted exactly ONCE,
    # by whichever run is first to find it unset, so it keeps the day's
    # TRUE morning content. Re-snapshotting on a later refresh would
    # silently replace it with an already-refreshed version — hence the
    # `or`, which only reaches current_snapshot the first time. (A second
    # same-day refresh finding morning_issuance already set shouldn't
    # normally happen — evening_refresh.yml's `check` job gates on
    # meta.refreshed_at already being set — but the guard costs nothing and
    # matches this project's existing belt-and-suspenders idempotency
    # style, e.g. last_verified_target_date in verify/pipeline.py.)
    current_snapshot = existing_entry.to_issuance_snapshot()
    morning_snapshot = existing_entry.morning_issuance or current_snapshot

    tp = llm_response.today_properties
    updated_entry = existing_entry.model_copy(
        update={
            "rain_expected": tp.rain_expected,
            "onset_window": tp.onset_window,
            "peak_wind_kmh": tp.peak_wind_kmh,
            "temp_high_c": tp.temp_high_c,
            "temp_low_c": tp.temp_low_c,
            "temp_high_low_display": format_temp_high_low(tp.temp_high_c, tp.temp_low_c),
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
            # This issuance's own recency, not the morning's — current_snapshot
            # above (built from existing_entry, before this overwrite) is what
            # carries the morning's guidance_* values into earlier_issuances.
            "guidance_initialised_at": guidance.guidance_cycle.initialised_at,
            "guidance_age_hours": guidance.guidance_cycle.age_hours,
            "guidance_source": guidance.guidance_cycle.source,
            "morning_issuance": morning_snapshot,
            "earlier_issuances": [*existing_entry.earlier_issuances, current_snapshot],
            # THIS issuance's gaps, not the morning's — the same split as
            # guidance_* above. current_snapshot was built from
            # existing_entry before this overwrite, so the morning's own list
            # has already been carried into earlier_issuances; leaving the
            # morning's here as well would report a clean evening as degraded
            # for ever.
            "meta": existing_entry.meta.model_copy(
                update={
                    "refreshed_at": datetime.now(timezone.utc),
                    "degradations": guidance.degradations,
                }
            ),
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
