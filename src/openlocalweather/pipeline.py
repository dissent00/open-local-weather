"""Forecast pipeline orchestration, adapted to git-as-database.

git commit/push is deliberately NOT done here — that's the GitHub Actions
workflow's job (see .github/workflows/forecast.yml) after these functions
return, keeping this module free of any git dependency and testable purely
as "run this, inspect the files/return value."

ONE ENTRY POINT, since ROADMAP item 104 step 4.

run_forecast() reads the day, skips a trigger that merely repeats one that
just ran, and hands the rest to _issue_forecast(). There used to be two
pipelines here — a morning run and an evening "refresh" — and the item's
finding was that this was never two pipelines: it was one pipeline and a
subset of it, and the subset drifted, repeatedly and in both directions,
because nothing made the two agree. A run can happen at any time, and the
forecast is a look at the upcoming 24 hours and the next three days, not a
review of the day.

_issue_forecast() branches once, on whether the day already holds an entry.
Step order:
  1. Fetch forward-looking multi-model guidance + optional sources (METAR,
     ground AQI, local bulletin). A later issuance merges the ground AQI
     against what the day already holds.
  2. FIRST ISSUANCE ONLY — fetch/refresh yesterday's actuals into the cache,
     a cheap single-day upsert on a normal day and a full batch re-fetch on
     defaults.WEEKLY_BATCH_WEEKDAY (see store/actuals_cache.py). This is the
     only part of a run that makes archive requests.
  3. FIRST ISSUANCE ONLY — deterministic verification + rolling stats
     (verify/pipeline.py), no LLM involved. Yesterday's actuals do not change
     during the day, so a later issuance would rewrite the record's learning
     loop against unchanged inputs.
  4. Extract the raw per-model predictions (extract.py) — code, not LLM. A
     later issuance keeps what the day already stored.
  5. Call the LLM for narrative + qualitative notes + blended
     today_properties.
  6. Build the entry — _compose_log_entry, which is also where the rules a
     later issuance may not rewrite live.
  7. Write the entry and archive the prompt. FIRST ISSUANCE ONLY: write the
     actuals cache, the track record's skill summaries, and the verification
     notes back onto the historical rows that just got verified.
  8. Publish via the injected Publisher, and — FIRST ISSUANCE ONLY — email
     via the injected EmailSender. Both are optional hooks so this module
     doesn't need to know about either concrete implementation. Note the
     standalone Apps Script mailer is unrelated to EmailSender: it polls the
     published data on its own trigger and mails every issuance.

--dry-run (see cli.py) skips the file-write/publish/email steps — the
fetch/LLM steps still run for real, so a maintainer can see the pipeline
actually working without polluting committed data or emailing real
subscribers.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

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
from openlocalweather.wind import consensus_direction, describe_wind_shift
from openlocalweather.calibration import calibrated_gust_consensus, gust_corrections
from openlocalweather.comparison import (
    comparison_for_prompt,
    compute_day_over_day,
    describe_extended_trend,
)
from openlocalweather.verify.scoring import mean as _mean_of
from openlocalweather.verify.scoring import resolve_prediction_rows, scored_predictions
from openlocalweather.daypart import (
    DayPart,
    daypart_without_sun,
    forecast_windows,
    forward_hours,
    reconcile_now,
    summarize_daypart,
)
from openlocalweather.config import LocationConfig, deviation_bands
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
    forward_calendar,
    now_in_tz,
    today_in_tz,
    utc_offset_seconds,
    weekday_name,
)
from openlocalweather.defaults import (
    WINDOW_VERIFY_LOOKBACK_DAYS,
    ACTUALS_BATCH_LOOKBACK_DAYS,
    BASELINE_MODEL_IDS,
    BLEND_MODEL_ID,
    MODELS,
    WEEKLY_BATCH_WEEKDAY,
    models_visible_to_the_forecaster,
    note_names_a_hidden_model,
    scored_models,
)
from openlocalweather.extract import (
    extract_window_predictions,
    extract_day0_predictions_from_hourly,
    extract_day_n_predictions_from_daily,
)
from openlocalweather.fetch import metar as metar_fetch
from openlocalweather.fetch import model_run as model_run_fetch
from openlocalweather.fetch import open_meteo
from openlocalweather.fetch import waqi as waqi_fetch
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.llm import forecast_call
from openlocalweather.llm.forecast_call import generate_forecast
from openlocalweather.llm.prompt_size import measure_prompt
from openlocalweather.extract import forecast_horizon_days
from openlocalweather.llm.prompt import (
    build_judgment_prompt,
    build_narrative_prompt,
    build_narrative_user_prompt,
    build_user_prompt,
)
from openlocalweather.store import prompt_archive
from openlocalweather.review import WeeklyReview, build_weekly_review
from openlocalweather import solar
from openlocalweather.spend import (
    LLM_CALLS_PER_FORECAST,
    assert_capacity,
    complete_attempt,
    record_attempt,
    record_poll,
)
from openlocalweather.synoptic import summarize_synoptic
from openlocalweather.llm.provider import LLMProvider, ResponseMeta
from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    GeminiJudgmentResponse,
    GeminiNarrativeResponse,
    TodayProperties,
    merge_forecast_response,
)
from openlocalweather.observed import (
    describe_low_divergence,
    describe_observed_so_far,
    observed_baseline,
)
from openlocalweather.reasoning import llm_should_reason
from openlocalweather.verify.scoring import verify_closed_windows
from openlocalweather.disagreement import (
    StandingCall,
    low_divergence,
    observation_disagreements,
)
from openlocalweather.claims import false_weekday_claims
from openlocalweather.models import (
    DeviationBands,
    LowDivergence,
    DayOverDayComparison,
    IssuancePredictions,
    ObservedSoFar,
    InformationMoved,
    DEGRADATION_NARRATIVE,
    summary_carries_a_figure,
    summary_contradicts_its_row,
    DEGRADATION_HOURS_AHEAD_NARROWED,
    DEGRADATION_METAR,
    DEGRADATION_STATION_READINGS,
    DEGRADATION_SYNOPTIC,
    SOURCE_STATION,
    DEGRADATION_SUN_TIMES,
    DEGRADATION_EXTENDED_OUTLOOK,
    DEGRADATION_DAILY_GUIDANCE_UNREADABLE,
    DEGRADATION_SECONDARY_EXTENDED_OUTLOOK,
    LocalBulletinRecord,
    DailyActual,
    DailyLogEntry,
    GroundAQIReading,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
    NarrativeFinding,
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
class ForecastRunResult:
    """What a run of the day's forecast produced, whichever run it was.

    ONE TYPE, BECAUSE THERE WERE TWO. PipelineRunResult and RefreshRunResult
    made "was this the day's first run?" a question about the result's CLASS,
    so a caller answered it with isinstance — which only works if the caller
    already knows which function was dispatched to, and run_forecast exists
    precisely so callers do not. It is a property of the RUN, and
    `first_issuance` is that property. ROADMAP item 104, step 2.

    It is the same question the entry already answers as
    `InformationMoved.first_issuance_of_day`, and the narrative prompt as
    `verification_already_written`.
    All three read `existing_entry`, which `run_forecast` resolves once and
    passes down. Do not compute a fourth.

    `updated_track_record` and `newly_verified` are the first run's work and
    are None on a later issuance, meaning THIS RUN DID NOT DO IT. An empty
    `newly_verified` would mean it verified and found nothing, which is a
    different fact. The opposite choice is what item 104 is cleaning up: for
    weeks three prompt blocks an unwired path never passed rendered
    identically to a genuine absence, and nothing reported it.
    """

    today: date
    log_entry: DailyLogEntry
    published: bool
    first_issuance: bool
    updated_track_record: TrackRecord | None = None
    newly_verified: list[tuple[date, int]] | None = None
    # False on a later issuance because one genuinely sends no email — see
    # _issue_forecast's publish step. Not an absence, so not None.
    emailed: bool = False


@dataclass
class ForecastSkipped:
    """Returned when a trigger repeats one that just ran — see run_forecast.

    A skip is a success, not a failure: a duplicate trigger is the system
    working (a backup schedule slot firing behind a primary that already
    delivered), and it must not colour a workflow run red.
    """

    today: date
    reason: str


@dataclass
class ObservationsRefreshed:
    """Returned when a run refreshed what the station has seen and reasoned
    nothing — ROADMAP item 121.

    A THIRD OUTCOME, not a kind of skip and not a kind of forecast. A skip
    does nothing at all; a forecast buys a judgment and a narrative. This
    fetched, composed what the station has seen since the standing forecast,
    wrote it, and re-rendered the page — for no LLM spend. It is the outcome
    an hourly cron should mostly produce.

    `log_entry` is the day's entry as it now stands: the standing forecast
    untouched, its observations current.
    """

    today: date
    log_entry: DailyLogEntry
    published: bool


# How recently a run has to have happened for the next trigger to be a
# repeat of it rather than a new issuance.
#
# The backup schedule slots sit +15/+30/+45 minutes behind each primary, and
# an operator's crontab may aim at the same minute as GitHub's own scheduler,
# so anything under an hour is a duplicate of the run before it. The cost of
# the bound is that runs scheduled less than an hour apart are refused; four
# runs a day, the most anyone has wanted, is six hours apart.
MIN_REISSUE_INTERVAL_MINUTES = 60


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
    # THE RECONCILED LOCAL INSTANT this run was issued at, carried whole.
    #
    # `issuance.local_time` is the same moment as an "HH:MM" string, and that
    # is enough for a sentence but not for slicing an hourly series: the
    # window needs a date as well as a clock, and re-deriving one by pairing
    # the string with `today` would be a second derivation of a number
    # `reconcile_now` may already have overridden. Carried rather than
    # recomputed, for the same reason `issuance` itself is.
    issued_at_local: datetime | None = None



def _response_meta(holder: dict) -> ResponseMeta:
    """Whatever the provider reported about the last call, or an empty record.

    Empty rather than None so the three call sites read one way: a provider
    that reports nothing gives three Nones, exactly like a provider that was
    never asked. The distinction does not exist downstream and inventing it
    here would only make every reader handle it.
    """
    return holder.get("meta") or ResponseMeta()


def _nullable_fields(holder: dict) -> list[str] | None:
    """The provider's tuple as a list, or None when it did not report one.

    The conversion is NOT redundant with pydantic's. The constructor coerces
    a tuple to `list[str]`; `model_copy(update=...)` does not, and the
    refresh path is built on `model_copy`. Checked rather than assumed: the
    tuple survives into the model and pydantic then warns at serialization
    time — `PydanticSerializationUnexpectedValue ... expected list[str]`.
    The JSON happens to come out identical today, which is what would make
    this rot quietly.

    None is preserved rather than flattened to `[]` because the two mean
    different things — see LogEntryMeta.nullable_fields.
    """
    reported = _response_meta(holder).nullable_fields
    if reported is None:
        return None

    return list(reported)


def _combined_meta(judgment: ResponseMeta, narrative: ResponseMeta) -> ResponseMeta:
    """One record for a forecast that now takes two calls — ROADMAP item 59.3.

    `nullable_fields` is the UNION of what either call was allowed to omit,
    which keeps the field answering the question it was added for: which
    values was the model permitted not to give. The two schemas share no
    field names — one holds the scored call, the other only prose — so the
    union loses nothing and needs no disambiguation.

    `response_schema_sha256` hashes the two schema hashes together, in call
    order, so a change to EITHER schema moves it. Hashing only the judgment
    call's would leave the narrative schema unrecorded, and it is the one
    that makes the seam structural.

    Tokens are summed because they are spend and the run spent both.
    `finish_reason` is the narrative call's: a judgment call that ended any
    other way raises inside the provider (see gemini.py, where the stop
    reason is checked before the content is read), so a stored entry cannot
    carry an abnormal one from the first call — it would have no entry.
    """
    both = [f for f in (judgment.nullable_fields, narrative.nullable_fields) if f is not None]
    nullable = tuple(sorted({path for fields in both for path in fields})) if both else None

    hashes = [judgment.response_schema_sha256, narrative.response_schema_sha256]
    if any(h is None for h in hashes):
        combined_sha = None
    else:
        combined_sha = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()

    def _sum(a: int | None, b: int | None) -> int | None:
        if a is None and b is None:
            return None
        return (a or 0) + (b or 0)

    return ResponseMeta(
        finish_reason=narrative.finish_reason,
        input_tokens=_sum(judgment.input_tokens, narrative.input_tokens),
        output_tokens=_sum(judgment.output_tokens, narrative.output_tokens),
        thought_tokens=_sum(judgment.thought_tokens, narrative.thought_tokens),
        response_schema_sha256=combined_sha,
        nullable_fields=nullable,
    )


def _narrative_findings(llm_response, today: date) -> list[NarrativeFinding]:
    """What the published prose asserts that a machine could check and found
    false — see claims.py.

    RECORDED, NOT ENFORCED. The run publishes either way: the operator's call
    on 2026-09-13 was that discarding a narrative over one wrong weekday costs
    the reader more than the error does. So the value of this field is the
    COUNT over time — it says whether `forward_calendar` closed the gap, and
    whether anything stronger than recording is ever worth buying.

    `[]` is a real answer and is stored as one. An entry whose findings are
    None was written before the check existed, and is unchecked rather than
    clean.
    """
    return [
        NarrativeFinding(**finding)
        for finding in false_weekday_claims(llm_response.today_narrative or "", today)
    ]


def _generate_forecast(
    provider, judgment_prompt: str, narrative_prompt: str, user_prompt: str, holder: dict
) -> tuple[forecast_call.ForecastCall, ResponseMeta]:
    """The two-call forecast, plus one meta describing both calls.

    The call ORDER lives in llm/forecast_call.py, shared with replay. What
    this adds is the snapshot: `holder` holds only the LAST response, so each
    call's report has to be taken before the next one overwrites it.
    """
    metas: dict[str, ResponseMeta] = {}

    def _snapshot(name: str) -> None:
        metas[name] = _response_meta(holder)

    call = generate_forecast(
        provider, judgment_prompt, narrative_prompt, user_prompt, on_call=_snapshot
    )

    return call, _combined_meta(
        metas.get(forecast_call.JUDGMENT, ResponseMeta()),
        metas.get(forecast_call.NARRATIVE, ResponseMeta()),
    )


def attach_spend_cap(
    provider, data_dir: Path, *, max_calls: int, purpose: str, calls_needed: int = 1
):
    """Make the cap count HTTP requests, which is what actually costs money.

    PUBLIC, AND THE ONLY ONE. The pipeline is not the only thing that spends,
    and every time a new caller appeared the rule was re-implemented locally
    instead: `check-health` got its own near-copy in cli.py that omitted the
    fail-closed check, the shout when a provider ignores the hook, and later
    the response capture. `olw replay` got nothing at all and went uncounted
    until 2026-09-10, while printing that it was counted.

    Three callers, three outcomes, one rule. So this takes a provider and a
    data directory rather than PipelineDeps: the pipeline is a caller here,
    not the owner. `tests/test_spend_coverage.py` fails if a `.generate(`
    appears anywhere that does not come through this.

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
    #
    # `calls_needed` is what the caller's whole job costs, so a budget that
    # cannot cover it refuses BEFORE the first call rather than partway
    # through — see assert_capacity, and ROADMAP item 59 step 3 for the
    # half-a-forecast failure that prompted it.
    assert_capacity(data_dir, max_calls=max_calls, calls_needed=calls_needed)

    recorded: list[int] = []
    # The row _record opened and _complete is owed. Held here rather than
    # returned through the hook so `before_attempt` keeps the signature every
    # third-party provider already implements.
    pending: dict[str, datetime | None] = {"at": None}

    def _record() -> None:
        # Cleared first, so a refusal below cannot leave the PREVIOUS row
        # eligible to be completed with this attempt's outcome.
        pending["at"] = None
        at = datetime.now(timezone.utc)
        used = record_attempt(
            data_dir,
            provider=type(provider).__name__,
            model=getattr(provider, "model", "unknown"),
            purpose=purpose,
            max_calls=max_calls,
            now=at,
        )
        pending["at"] = at
        recorded.append(used)
        print(f"LLM call {used}/{max_calls} in the last 24h")

    def _complete(outcome: str, elapsed_s: float) -> None:
        complete_attempt(
            data_dir, at=pending["at"], outcome=outcome, elapsed_s=elapsed_s
        )

    # HOW THE CALL ENDED, kept for the log entry rather than the ledger —
    # ROADMAP item 100. Wired HERE, in the one function both pipelines already
    # go through, because the alternative is two wiring sites that are free to
    # drift; this file has been bitten by exactly that twice, most recently
    # over the historical-notes projection.
    #
    # Last write wins, and a forecast run makes one generate() call. A run
    # that somehow made two would keep the second, which is the one whose
    # response was published.
    last_response: dict[str, ResponseMeta | None] = {"meta": None}

    def _record_response(meta: ResponseMeta) -> None:
        last_response["meta"] = meta

    # Set rather than passed to the constructor: the provider is built in
    # cli.py, which has no reason to know where the ledger lives.
    provider.before_attempt = _record
    provider.after_attempt = _complete
    # Optional on the Protocol, so a provider that never reports one leaves
    # the fields None — which reads as "did not say", not as zero.
    provider.after_response = _record_response

    # POLLS, for providers that have them. `hasattr` rather than a try/except
    # or a Protocol member because polling is one provider's implementation
    # detail and the other three have no business growing a no-op hook for it.
    #
    # WIRED HERE FOR THE REASON IN THIS FUNCTION'S DOCSTRING: three callers
    # previously re-implemented the rule locally and got three different
    # subsets of it. `on_poll` existed on GeminiInteractionsProvider from the
    # day it was written and NOTHING supplied it, so the first live background
    # run on 2026-09-15 made a submit and roughly two polls and recorded one
    # row of the three. Attaching it at the single seam is what stops that
    # being rediscovered per caller.
    if hasattr(provider, "on_poll"):
        provider.on_poll = lambda: record_poll(
            data_dir,
            provider=type(provider).__name__,
            model=getattr(provider, "model", "unknown"),
            purpose=purpose,
        )

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
                f"WARNING: {type(provider).__name__} completed a "
                f"{purpose} without reporting any request to the spend cap. "
                f"Its usage is NOT counted and the cap cannot bound it. A "
                f"provider must call before_attempt() before every request.",
                file=sys.stderr,
            )

    return _verify_recorded, last_response


def _issued_hour(issuance: DayPart | None) -> int:
    """The local hour a run went out — ROADMAP item 118.

    24 WHEN THE MOMENT COULD NOT BE ESTABLISHED, which is later than any
    onset and therefore suppresses every timing qualifier. Absence is not
    permission: a phrase saying a day was dry until the evening is a claim
    about elapsed hours, and a run that cannot say what hour it is has no
    business making it. In practice `issuance` is always present — the
    pipeline falls back to daypart_without_sun — so this is the guard for a
    path that does not exist rather than one that does.
    """
    if issuance is None:
        return 24

    try:
        return int(issuance.local_time.split(":")[0])
    except (AttributeError, ValueError, IndexError):
        return 24


def _sunset_hour(issuance: DayPart | None) -> int | None:
    """The local hour the sun sets, or None when it could not be established.

    None RATHER THAN A DEFAULT, and the asymmetry with `_issued_hour`'s 24 is
    deliberate. That returns a value later than any onset so an unknown clock
    SUPPRESSES a timing phrase; this returns None so an unknown sunset
    suppresses the evening subject — `comparison_subject` never promotes an
    afternoon into a comparison without a boundary to place the pivot on.
    Both choices resolve toward saying less.
    """
    if issuance is None:
        return None

    try:
        return int(issuance.sunset.split(":")[0])
    except (AttributeError, ValueError, IndexError):
        return None


def _issuance_windows(issuance: DayPart | None, today: date) -> tuple:
    """The issuance's named periods with explicit clock bounds.

    Empty when the moment could not be established, for the same reason
    `_issued_hour` returns 24 there: a run that cannot say what hour it is has
    no business telling a reader which hours a period covers. The prompt
    renders that as unavailable and asks for no hours at all, rather than
    offering bounds computed from a clock nobody trusts.

    The DayPart carries clock times and no date, so `today` supplies it — and
    it must be the LOCATION's date, which is what the pipeline means by
    `today`, never the runner's. A run at 03:02 UTC is already tomorrow in
    Kisumu, so a date taken from the host clock would put every window on the
    wrong day and name the wrong weekday with it. Same rule the sandbox sweep
    states for its filenames, one layer down.
    """
    if issuance is None:
        return ()

    now = _issuance_moment(issuance, today)
    if now is None:
        return ()

    sunrise = _clock_on(now, getattr(issuance, "sunrise", None))
    sunset = _clock_on(now, getattr(issuance, "sunset", None))
    next_sunrise = None if sunrise is None else sunrise + timedelta(days=1)

    return forecast_windows(now, sunrise, sunset, issuance.horizon, next_sunrise)


def _issuance_moment(issuance: DayPart, today: date) -> datetime | None:
    """The issuance as a datetime on the location's own date, or None when its
    clock cannot be read."""
    try:
        hour, minute = (int(part) for part in issuance.local_time.split(":")[:2])
    except (AttributeError, ValueError, IndexError):
        return None

    return datetime(today.year, today.month, today.day, hour, minute)


def _clock_on(day: datetime, hhmm: str | None) -> datetime | None:
    if not hhmm:
        return None
    try:
        hour, minute = (int(part) for part in hhmm.split(":")[:2])
    except (ValueError, IndexError):
        return None

    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _track_record_payload(entries, models: set | list) -> list[dict]:
    """The rolling stats the forecaster is shown, with unsafe summaries hidden.

    ONE IMPLEMENTATION, BECAUSE THERE WERE TWO. run_daily_pipeline had a nested
    `visible_summary` and run_refresh_pipeline had the same rule written out
    inline — one reading `skill_profile_summary` off the dumped dict and the
    other off the model. They agreed, and nothing made them agree; this is the
    class of divergence ROADMAP item 104 exists to close.

    `entries` differs by design and is the caller's to supply: the first run of
    a day passes the record it has just updated, and a later one passes what is
    stored, because it did no verification and has nothing fresher. Item 91 is
    why that matters — the refresh is the WORSE case, since every summary it
    reads was written by an earlier run.
    """
    payload = []
    for entry in entries:
        if entry.model not in models:
            continue

        row = entry.model_dump()
        # NOT SENT YET — ROADMAP item 150, step 3 decides how the forecaster
        # is told a lead is beyond a source's reach. Until then the stored
        # horizon stays out of the prompt: found by driving the real CLI
        # before and after step 2, where the archived prompt grew by 36
        # nulls nobody had decided to send.
        row.pop("forecast_horizon_days", None)
        if summary_carries_a_figure(entry.skill_profile_summary) or (
            # ROADMAP item 142, finding 3. A summary whose direction words
            # contradict its own row's measured error is a false claim the
            # forecaster is told to reason from, beside a LONG-RUN REVIEW
            # finding that says the opposite. Withheld on the same seam and
            # for the same reason as a quoted figure: the row is still worth
            # showing, and the summary is the part that cannot be trusted.
            summary_contradicts_its_row(
                entry.skill_profile_summary,
                low_error_c=getattr(entry, "avg_temp_low_error_c_10", None),
                wind_error_kmh=getattr(entry, "avg_wind_error_kmh_10", None),
            )
        ):
            # The figure is withheld rather than the row: the stats are still
            # worth showing, and the summary is the part the sample cannot
            # support.
            row = dict(row, skill_profile_summary=None)
        payload.append(row)

    return payload


def _build_forecast_prompt(
    deps: PipelineDeps,
    guidance: ForwardGuidance,
    existing_entry: DailyLogEntry | None,
    today: date,
    *,
    day0_predictions: list,
    observed_so_far: ObservedSoFar | None,
    verification_context: Any,
    model_predictions_context: Any,
    track_record_context: Any,
    review_context: Any,
    yesterday_actual: Any,
    calibrated_wind_kmh: float | None,
    extended_days: list[list],
    overnight_low_divergence: LowDivergence | None,
) -> str:
    """The user prompt, built in the one place it is built.

    ROADMAP item 104, and this is the structural half of it. Two call sites
    meant a field could be wired into one and not the other, and in a single
    day that happened five times: `extended_trend`, `wind_direction` and
    `wind_shift` had been missing from every evening run for weeks;
    `narrative_findings` would have been missing from the refresh's own meta;
    and `historical_logs`, the track-record shaping and `visible_note` were all
    carried as second copies that agreed only by luck.

    None of those was a hard failure. Each rendered as "Unavailable", which is
    also what a real gap looks like, so the record could not tell an unwired
    block from an absent one and neither could a reader.

    WITH ONE CALL SITE THE CLASS IS CLOSED, not merely fixed: a new field added
    to `build_user_prompt` has exactly one place to be wired, and both kinds of
    run get it or neither does.

    EVERYTHING A PATH GENUINELY DECIDES IS A REQUIRED KEYWORD ARGUMENT. Five
    values differ by design — a first run verifies and scores, a later one
    reuses what the first stored — and `required` is what stops a caller
    keeping an old default by saying nothing. Item 118 used the same technique
    for `issued_hour`, and it is what caught `sandbox/sweep.py` when the two
    fell out of step.
    """
    return build_user_prompt(
        today=today,
        yesterday=add_days(today, -1),
        public_webpage_url=deps.public_webpage_url,
        verification_context=verification_context,
        model_predictions_context=model_predictions_context,
        track_record_context=track_record_context,
        ground_aqi_readings=_ground_aqi_prompt_payload(guidance),
        ground_aqi_summary=(
            asdict(guidance.ground_aqi_summary)
            if guidance.ground_aqi_summary is not None
            else None
        ),
        ground_aqi_last_known=(
            asdict(guidance.ground_aqi_last_known)
            if guidance.ground_aqi_last_known is not None
            else None
        ),
        ground_stations_configured=bool(deps.location.waqi_stations),
        local_bulletin_configured=bool(deps.location.local_bulletin_source_name),
        instability=(
            asdict(guidance.instability) if guidance.instability is not None else None
        ),
        guidance_recency=_guidance_recency_payload(guidance, existing_entry),
        yesterday_actual=yesterday_actual,
        **_locked_blocks(
            guidance, day0_predictions, today, observed_so_far, calibrated_wind_kmh,
            extended_days, overnight_low_divergence, deps.location,
        ),
        review_context=review_context,
        today_weather_data={
            # `primary_today_hourly` IS NOT SENT — ROADMAP item 73's first
            # cut. Eighteen of its twenty-four hours were already in HOURS
            # AHEAD and the other six had elapsed. `guidance.primary_hourly`
            # is still fetched and still read by code — the wind shift, the
            # Day+0 extraction and the instability summary all reason from
            # it — so what changed is what the FORECASTER is handed, not what
            # the pipeline knows. See llm/prompt.py for the measurement.
            "primary_extended_daily": guidance.primary_daily,
            "secondary_today_hourly": guidance.secondary_hourly,
            "secondary_extended_daily": guidance.secondary_daily,
            "regional_pressure": guidance.regional_pressure,
            "air_quality": guidance.air_quality,
            "airport_metar": guidance.airport_metar,
            "synoptic_scale_pressure": (
                asdict(guidance.synoptic) if guidance.synoptic is not None else None
            ),
        },
        local_bulletin_source_name=deps.location.local_bulletin_source_name,
        local_bulletin_text=guidance.bulletin_text,
        issuance=guidance.issuance,
        forward_hourly=guidance.forward_hourly,
        forward_window_narrowed=guidance.forward_window_narrowed,
    )


def _locked_blocks(
    guidance: ForwardGuidance,
    day0_predictions: list,
    today: date,
    observed: ObservedSoFar | None,
    calibrated_wind_kmh: float | None,
    extended_days: list[list],
    overnight_low_divergence: LowDivergence | None,
    location: LocationConfig,
) -> dict:
    """The pre-computed blocks the prompt locks, composed once for every run.

    ROADMAP item 104. THESE THREE WERE COMPOSED ON ONE PATH AND NOT THE OTHER,
    and the reader could not tell. `build_user_prompt` takes `extended_trend`,
    `wind_direction` and `wind_shift` as keyword arguments defaulting to None,
    run_daily_pipeline passed all three and run_refresh_pipeline passed none,
    so every evening run told its reader the models had no three-day trend and
    nothing could be said about the wind turning. Measured across the prompt
    archive: real at the first issuance and Unavailable at the refresh on
    2026-09-07, 09-10 and 09-11, and the same for the wind shift on 09-11.

    Nothing marked it, because `None` is also what a legitimate absence looks
    like — the models genuinely do disagree about a bearing most evenings — so
    an unwired block and a true gap render as the same sentence.

    THE INPUTS WERE NEVER THE PROBLEM. `ForwardGuidance` carries
    `primary_hourly` and `primary_daily`, `_fetch_forward_guidance` is its only
    constructor and both paths call it, and run_daily_pipeline's own
    `primary_hourly` is that same object. The refresh could have published all
    three at no extra request; it simply never asked.

    So this exists to make asking the only option. A shared INPUT type was
    already tried — ForwardGuidance's docstring promises the two runs "can
    never silently drift apart" and it kept that promise, because they drifted
    one layer up, on what each composed from identical inputs. A shared struct
    cannot constrain two bodies of code. One function can, and every caller
    that wants these blocks now gets all of them or none.

    `day0_predictions` is the one input the callers must supply rather than
    read off `guidance`: the first run of a day extracts them fresh, and a
    later one reuses what the first stored, because those are the numbers the
    record scores.
    """
    issued_hour = _issued_hour(guidance.issuance)

    return {
        # What the station has already measured today — ROADMAP item 121.
        #
        # HERE, WITH THE OTHER LOCKED BLOCKS, for the reason this function
        # exists: a block composed on one path and not the other renders as a
        # legitimate absence and nobody can tell. It is also what makes C2's
        # third trigger worth acting on — that trigger fires when an
        # observation contradicts the standing call, and until now the run it
        # caused was never shown the observation that caused it.
        "observed_so_far": describe_observed_so_far(
            observed,
            as_of=guidance.issuance.local_time if guidance.issuance else None,
        ),
        # ROADMAP item 143, part 3. None on most runs and that is the design:
        # the block disappears entirely rather than printing an "Unavailable"
        # line, because nothing is unavailable — there is simply nothing worth
        # a reader's attention. NAMED, never "the station": a footnote that
        # does not say which place it describes reads as a claim about the
        # whole area, which is the one thing it must not say.
        "low_divergence_note": describe_low_divergence(
            overnight_low_divergence,
            location.metar_station_name or location.metar_station_icao,
        ),
        # The periods this issuance covers, each with the hours it means —
        # item 104. Derived from the issuance's own horizon, so the prompt
        # cannot name a period the phase did not call for.
        "forecast_windows": [w.to_json() for w in _issuance_windows(guidance.issuance, today)],
        # Every day this forecast can speak about, with its day name already
        # attached. The model was deriving these and getting them wrong — see
        # dates.forward_calendar for the count.
        "forward_calendar": forward_calendar(today),
        "extended_trend": describe_extended_trend(
            today_high_c=_mean_of([p.high_c for p in day0_predictions]),
            day_highs_c=[_mean_of([p.high_c for p in day]) for day in extended_days],
            day_precip_mm=[_mean_of([p.precip_mm for p in day]) for day in extended_days],
            last_day_name=weekday_name(add_days(today, 3)),
            # Wind is present at these leads and was being discarded, so a
            # three-day build in gusts under a flat temperature read as "much
            # the same". It is also what lets the clause say "conditions".
            today_wind_kmh=_mean_of([p.wind_kmh for p in day0_predictions]),
            day_winds_kmh=[_mean_of([p.wind_kmh for p in day]) for day in extended_days],
        ),
        # ROADMAP item 59. TWO WIND FACTS, and the second is the better one.
        # A bearing cannot be averaged — see wind.vector_mean — so the
        # direction is a gated vector consensus and is absent whenever the
        # models do not share one, which measured here is most of the evening.
        # The SHIFT is what survives: the models argue about a single daily
        # bearing and agree about which way it turns, so that is the fact
        # worth publishing.
        "wind_direction": consensus_direction(
            [p.wind_direction_deg for p in day0_predictions if p.wind_direction_deg is not None]
        ),
        # ROADMAP item 118: the anchors are hours of the day, so a clause with
        # none of them still ahead describes a day the reader has finished.
        "wind_shift": describe_wind_shift(
            guidance.primary_hourly, MODELS, issued_hour=issued_hour
        ),
        # The gust the record says to expect — calibration.py. Here with the
        # other locked blocks for this function's founding reason: a block
        # composed on one path and not the other renders as a legitimate
        # absence and nobody can tell.
        "calibrated_gust_kmh": (
            round(calibrated_wind_kmh, 1) if calibrated_wind_kmh is not None else None
        ),
    }


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

        # The sky the station actually saw — items 87 and 65. Stored beside
        # the archive's own figure rather than merged with it: percent and
        # eighths are not the same quantity, and picking between them needs
        # the band table on both sides. Unstamped when absent, because a
        # stamp asserts an observation was made.
        if observed.cloud_oktas is not None:
            actual.station_cloud_oktas = observed.cloud_oktas
            actual.provenance["station_cloud_oktas"] = SOURCE_STATION

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

    # BEST-EFFORT, unlike today's hourly guidance above. See
    # DEGRADATION_EXTENDED_OUTLOOK: losing the seven-day outlook costs the
    # Extended Outlook section and the day-over-day trend clause; losing today
    # costs the forecast.
    try:
        primary_daily = open_meteo.fetch_forecast_daily_extended(
            location.primary_point.lat, location.primary_point.lon, MODELS, location.timezone
        )
    except open_meteo.OpenMeteoFetchError as e:
        primary_daily = {}
        degradations.append(
            RunDegradation(
                code=DEGRADATION_EXTENDED_OUTLOOK,
                summary=(
                    "The seven-day outlook did not arrive. Today and tonight are "
                    "unaffected; where this forecast says nothing about the days "
                    "ahead, that is missing data rather than a settled week."
                ),
                detail=(
                    f"The extended daily fetch failed and the run continued without "
                    f"it: {e}. Day+3 and Day+7 predictions are absent from the "
                    "record for this run, so nothing is scored at those leads, and "
                    "the Overview's closing trend clause is omitted rather than "
                    "guessed."
                ),
            )
        )
    # THE SCHEMA EVENT — ROADMAP item 150. A 200 with no readable precipitation
    # array for ANY model is what a provider rename looks like, and read
    # naively it says every model forecasts nothing. Recorded as its own
    # degradation so the reach observer records nothing and the weekly
    # degradation watcher sees it recur. A fetch that FAILED is the branch
    # above and already carries its own code.
    if primary_daily and all(forecast_horizon_days(primary_daily, m) is None for m in MODELS):
        degradations.append(
            RunDegradation(
                code=DEGRADATION_DAILY_GUIDANCE_UNREADABLE,
                summary=(
                    "The seven-day outlook arrived but none of it could be read. "
                    "Where this forecast says nothing about the days ahead, that "
                    "is missing data rather than a settled week."
                ),
                detail=(
                    "The extended daily response carried no non-null precipitation "
                    "array for any model. That is the shape of a variable rename "
                    "upstream, not of short forecasts, so no forecast reach was "
                    "recorded from this run and Day+3 / Day+7 are absent."
                ),
            )
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
        # Same endpoint and the same outage, but a DIFFERENT code — see
        # DEGRADATION_SECONDARY_EXTENDED_OUTLOOK, which the prompt's switch
        # deliberately does not key on. None here is a state every consumer
        # already handles, because a location is allowed to have no secondary
        # point at all.
        try:
            secondary_daily = open_meteo.fetch_forecast_daily_extended(
                location.secondary_point.lat, location.secondary_point.lon, MODELS, location.timezone
            )
        except open_meteo.OpenMeteoFetchError as e:
            secondary_daily = None
            degradations.append(
                RunDegradation(
                    code=DEGRADATION_SECONDARY_EXTENDED_OUTLOOK,
                    summary=(
                        f"The seven-day outlook for {location.secondary_point.name} did "
                        "not arrive. Today and tonight are unaffected."
                    ),
                    detail=(
                        "The extended daily fetch for the secondary point failed and "
                        f"the run continued without it: {e}."
                    ),
                )
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
        issued_at_local=now_local,
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


def _extended_blend_predictions(
    extended: list[ExtendedDayProperties], lead_time_days: int
) -> list[ModelPrediction]:
    """The forecaster's own call at a lead beyond today — ROADMAP item 72.

    RAIN ONLY. The full item widens the schema at every lead the record
    scores; this carries the one variable item 58's Brier can already score.
    Shipped small and early because item 72 shares item 69's property — it
    changes what FUTURE days record and recovers nothing — so a day spent
    designing the rest is a Day+3 call permanently lost.

    Returns [] when the run committed to nothing at this lead, and that is a
    legitimate answer rather than a gap: a guessed boolean is scored wrong
    exactly as confidently as a real one, so declining to call is the honest
    move and the record should carry no row for it.

    WHY THE BLEND WAS UNSCORED HERE UNTIL NOW. `_blend_prediction` builds from
    `today_properties`, which is by definition today, so `olw_blend` had rows
    at Day+0 and nowhere else — and Day+0 is where the free NWP is already
    near its ceiling. The forecaster's most defensible job, reconciling models
    that disagree, happens at the leads where the spread is widest and nothing
    scored it there. See item 72 for the numbers.
    """
    return [
        ModelPrediction(
            model=BLEND_MODEL_ID,
            rain=e.rain,
            rain_probability_pct=e.rain_probability_pct,
            # Everything else absent rather than zero, exactly as the Day+0
            # blend treats peak wind: a field the forecaster was not asked to
            # commit to must not enter the record as a value it never gave.
            onset=None,
            precip_mm=None,
            wind_kmh=None,
            high_c=None,
            low_c=None,
            mslp_trend=None,
        )
        for e in extended
        if e.lead_time_days == lead_time_days
    ]


def _targeting(
    predictions: list[ModelPrediction], issued_for: date, lead_time_days: int
) -> list[ModelPrediction]:
    """Stamps each prediction with the date it is about — ROADMAP item 104, C1.

    `issued_for + lead` is exactly what `resolve_target_date` falls back to
    for older rows, so this changes no scoring today. What it changes is that
    the record stops DERIVING the target from where a prediction sits, which
    is the assumption that breaks once a day holds more than one issuance.

    Copies rather than mutates: these lists contain objects the caller also
    holds, and stamping in place would reach further than this function is
    allowed to.
    """
    target = add_days(issued_for, lead_time_days)

    return [p.model_copy(update={"target_date": target}) for p in predictions]


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
        # SCORED AT LAST — ROADMAP item 144. This was `None` for as long as
        # `today_properties` had one wind field, because that field was the
        # SECONDARY point's and scoring it against the primary point's
        # observations would have compared two different places. The split
        # gives the blend a wind call at the place the record observes, so the
        # null is no longer the truthful answer — it would now be discarding a
        # forecast that exists.
        #
        # THE QUANTITIES MATCH, checked 2026-09-16. The observation side is
        # ERA5 gust (`pick_series(h, "wind_gusts_10m", ...)`) and this is a
        # forecast gust, so it is gust against gust. The METAR station does not
        # enter it: `_apply_station_observations` stamps only thunder and
        # precipitation, and HKKI has never filed a gust group at all.
        #
        # The SECONDARY point's wind stays unscored, and `mslp_trend_24h`
        # stays prose, so both remain null here for the original reason.
        wind_kmh=tp.peak_wind_primary_kmh,
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


def _standing_call(entry: DailyLogEntry | None) -> StandingCall:
    """What the previous issuance committed to — ROADMAP item 104, C2.

    `rain` comes from the BLEND'S OWN Day+0 row rather than from the prose.
    `rain_expected` may hedge and the record does not: the boolean is what
    tomorrow scores, so it is the boolean an observation should be allowed to
    contradict.
    """
    if entry is None:
        return StandingCall()

    blend = next(
        (p for p in scored_predictions(entry).day0 if p.model == BLEND_MODEL_ID), None
    )

    return StandingCall(
        rain=blend.rain if blend is not None else None,
        temp_high_c=entry.temp_high_c,
        # FROM THE SCORED ROW, for the same reason `rain` is — ROADMAP item
        # 138. The entry carries `onset_window` as prose ("late afternoon or
        # early evening (16:00-18:00)"), which is what a reader sees; the
        # blend row carries the "HH:MM" the record is graded on, and grading
        # is what an observation should be allowed to contradict.
        onset_hour=blend.onset if blend is not None else None,
        # FROM THE ENTRY, not the blend row — ROADMAP item 143. The scored row
        # carries no low; `temp_low_c` on the entry IS the blended call and is
        # what tomorrow's verification grades, so it is the number the
        # station's reading should be allowed to diverge from.
        temp_low_c=entry.temp_low_c,
    )


def _overnight_low_is_settled(guidance: ForwardGuidance) -> bool | None:
    """Whether the night behind this issuance is over — ROADMAP item 143.

    A minimum only falls, so a station sitting ABOVE the called low proves
    nothing until the coldest part of the night has passed. Sunrise is that
    line: `observed.low_c` is the lowest reading of the calendar day so far,
    and after sunrise it is not going to be beaten.

    THREE-VALUED. None means the sun times were unavailable, so this run does
    not KNOW whether the night is over — see `daypart_without_sun`, which is
    the same absence. It is not False, because False would license the
    opposite claim on the same missing data.
    """
    issuance = getattr(guidance, "issuance", None)
    now_local = getattr(guidance, "issued_at_local", None)
    if issuance is None or now_local is None:
        return None

    sunrise = _clock_on(now_local, getattr(issuance, "sunrise", None))
    if sunrise is None:
        return None

    return now_local >= sunrise


def _observed_so_far(
    location: LocationConfig, today: date
) -> tuple[ObservedSoFar | None, RunDegradation | None]:
    """What the station has already reported today, and why not when it has not.

    BEST EFFORT, and None on every failure path. A station that did not answer
    is not a station reporting agreement — returning an empty ObservedSoFar
    would say "nothing contradicts the call" on the strength of having not
    looked, which is the error class that cost a published forecast on
    2026-08-29.

    IT NOW SAYS WHICH KIND OF NOTHING — ROADMAP item 151, and the second
    return value is the whole fix. This had four exits and only the exception
    printed; the other two returned None in silence. Measured 2026-09-16:
    `observed_so_far` was absent on 14 of the last 16 stored days with nothing
    anywhere recording it, while the same fetch by hand nineteen minutes after
    a run returned a full day. Three features were inert — the OBSERVED SO FAR
    TODAY block, C2's third trigger, and item 143's divergence, which shipped
    that morning and could not fire.

    THE TWO EMPTY EXITS MEAN DIFFERENT THINGS and are reported separately. No
    rows at all is a source or a network problem; rows that do not cover today
    is a lag, and a 06:00 run legitimately hits it where an 18:00 run should
    not. Collapsing them into one message would leave the record unable to
    say which, which is the defect this fixes rather than a refinement of it.

    NO STATION CONFIGURED IS NOT A DEGRADATION. RunDegradation's own docstring
    draws that line — a location running as configured is not running degraded
    — and blurring it makes the field mean nothing within a week.
    """
    if not location.metar_station_icao:
        return None, None

    icao = location.metar_station_icao

    def gap(detail: str) -> RunDegradation:
        return RunDegradation(
            code=DEGRADATION_STATION_READINGS,
            summary=(
                "The nearest airport's readings for today were not available, so "
                "this forecast could not be checked against what has already "
                "been measured locally."
            ),
            detail=detail,
        )

    try:
        weather, readings = metar_fetch.observed_station_data(
            icao, today, today, location.timezone
        )
    except Exception as e:  # noqa: BLE001 - never fatal; the forecast stands
        print(f"Station observations unavailable ({e}); no disagreement check.", file=sys.stderr)
        return None, gap(f"Fetching {icao}'s readings for {today} raised: {e}")

    # Returns a pair of Nones rather than raising when the station has no
    # data — a shape worth guarding explicitly, because the tuple unpacks
    # fine and only fails at the .get() two lines later.
    if weather is None and readings is None:
        print(f"Station {icao} returned no rows for {today}.", file=sys.stderr)
        return None, gap(
            f"{icao} returned no rows at all for {today}. The request "
            "succeeded and the response was empty."
        )

    seen = weather.get(today) if weather else None
    measured = readings.get(today) if readings else None
    if seen is None and measured is None:
        print(f"Station {icao} returned rows, none covering {today}.", file=sys.stderr)
        return None, gap(
            f"{icao} returned rows but none covering {today} itself — the "
            "archive had not reached today's date at the moment of this run."
        )

    return (
        ObservedSoFar(
            precipitation=seen.precipitation if seen is not None else None,
            precipitation_onset=seen.precipitation_onset if seen is not None else None,
            thunder=seen.thunder if seen is not None else None,
            cloud_oktas=seen.cloud_oktas if seen is not None else None,
            high_c=measured.high_c if measured is not None else None,
            low_c=measured.low_c if measured is not None else None,
            peak_wind_kmh=measured.peak_wind_kmh if measured is not None else None,
        ),
        None,
    )


def _information_moved(
    guidance: ForwardGuidance,
    existing_entry: DailyLogEntry | None,
    observed: ObservedSoFar | None,
    bands: DeviationBands | None = None,
) -> InformationMoved:
    """C2's three triggers, computed and recorded — and acted on by nothing.

    Stage 2b of item 104. The point of writing them down before wiring them to
    a spending decision is that the record then shows how often each fires
    against real weather, so the rule is sized against that rather than
    against the first few days anyone happens to look at.
    """
    recency = _guidance_recency_payload(guidance, existing_entry) or {}
    settled = _overnight_low_is_settled(guidance)

    return InformationMoved(
        first_issuance_of_day=existing_entry is None,
        guidance_is_newer=recency.get("newer_than_previous_issuance"),
        observation_disagreements=(
            None
            if observed is None
            else observation_disagreements(
                _standing_call(existing_entry),
                observed,
                low_is_settled=settled,
                bands=bands,
            )
        ),
        # STORED ON EVERY RUN, not only when it fires — ROADMAP item 143, part
        # 4. The question the operator wants answered is whether the station
        # runs warmer than the forecast or the forecast low is the problem,
        # and nothing can answer it until the per-day gap is on the record.
        # A divergence that was not worth printing is exactly the kind of row
        # that answers it.
        low_divergence=(
            None
            if observed is None
            else low_divergence(
                _standing_call(existing_entry),
                observed,
                low_is_settled=settled,
                bands=bands,
            )
        ),
    )


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


def observe_forecast_reach(
    primary_daily: dict,
    degradations: list[RunDegradation],
    *,
    met_service_present: bool,
    met_service_day3_present: bool,
    met_service_model_id: str,
) -> dict[str, int] | None:
    """How far each source forecast on this run — ROADMAP item 150, step 1.

    VOID ON A DEGRADED RUN, NOT ZERO. A fetch that failed or came back
    unreadable is indistinguishable, from the arrays alone, from a source
    that genuinely reaches that far; recording it would let one bad morning
    read as a shortened horizon. Absence is absence, the same three-valued
    discipline as everywhere else in this record.

    The met service is the reach of what THIS pipeline extracts from the
    bulletin — a Day+3 prediction when the daily bulletin parsed, Day+0
    otherwise — because that, not what the service publishes, is what can be
    scored. Absent when neither parsed.
    """
    codes = {d.code for d in degradations}
    if not primary_daily or codes & {DEGRADATION_EXTENDED_OUTLOOK, DEGRADATION_DAILY_GUIDANCE_UNREADABLE}:
        return None

    reach: dict[str, int] = {}
    for model in MODELS:
        horizon = forecast_horizon_days(primary_daily, model)
        if horizon is not None:
            reach[model] = horizon

    if met_service_day3_present:
        reach[met_service_model_id] = 3
    elif met_service_present:
        reach[met_service_model_id] = 0

    return reach


def _compose_log_entry(
    deps: PipelineDeps,
    guidance: ForwardGuidance,
    existing_entry: DailyLogEntry | None,
    today: date,
    llm_response: Any,
    *,
    observed_so_far: ObservedSoFar | None,
    information_moved: InformationMoved,
    window_predictions: list[ModelPrediction],
    fresh_predictions: ModelPredictionsByLead | None,
    day_over_day: DayOverDayComparison | None,
    judgment_prompt: str,
    narrative_prompt: str,
    user_prompt: str,
    last_response: Any,
) -> DailyLogEntry:
    """The day's entry, built in the one place it is built.

    ROADMAP item 104 step 3, and the half of it that matters. Two
    constructions meant a rule could be written into one and not the other,
    and measured 2026-09-13 by driving both re-issue paths against identical
    fixtures, SIX fields disagreed — each path holding a fix the other was
    missing:

      - `sunrise`/`sunset` fell back to the stored value on the refresh and
        were overwritten with null by a `run-daily` re-issue, so a run whose
        sun computation threw erased times the morning had captured. The
        refresh had the fallback and a comment explaining why; the other
        path had neither.
      - `meta.llm_model`, `meta.pipeline_version` and `meta.trigger_source`
        were THIS run's on a `run-daily` re-issue and the MORNING's on a
        refresh — so an evening narrative was stamped with the model that
        wrote the morning's, and `write_prompt_archive` copied that stamp
        onto the archived evening prompt, which exists to answer exactly
        that question.
      - `local_bulletin` was the morning's on a refresh and overwritten by a
        `run-daily` re-issue.

    None of them was a hard failure and the suite was green throughout, for
    the reason the whole item records: two bodies of code cannot be made to
    agree by anything except being one body.

    THE WRITE-ONCE RULES LIVE BELOW, AND ONLY BELOW. What a later issuance
    may not rewrite is one list in one place, so a new field is either in it
    or it is not, rather than being in it on one path.
    """
    location = deps.location
    tp = llm_response.today_properties
    response_meta = _response_meta(last_response)

    issuance = DailyLogEntry(
        date=today,
        rain_expected=tp.rain_expected,
        onset_window=tp.onset_window,
        peak_wind_primary_kmh=tp.peak_wind_primary_kmh,
        peak_wind_secondary_kmh=tp.peak_wind_secondary_kmh,
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
        # ONE ROW, stamped with this issuance. Contract item 4: a day holds a
        # row per issuance rather than one set, and the row a later issuance
        # adds is what IT had in hand — on a re-issue that is the day's stored
        # model numbers, which it deliberately does not re-extract, plus its
        # own blend from its own judgment call. That blend used to be computed
        # and thrown away.
        # This issuance's own reading — item 121. Stored so a reader can be
        # shown what the station had seen without another LLM call, and so a
        # later issuance's block describes ITS moment, not the morning's.
        observed_so_far=observed_so_far,
        prediction_rows=[
            IssuancePredictions(
                issued_at=datetime.now(timezone.utc),
                predictions=fresh_predictions or ModelPredictionsByLead(),
                window_predictions=window_predictions,
                # The exact instant the window was sliced at, floored the way
                # `forward_hours` floors it — see
                # IssuancePredictions.window_opened_local.
                window_opened_local=(
                    guidance.issued_at_local.replace(minute=0, second=0, microsecond=0)
                    if window_predictions and guidance.issued_at_local is not None
                    else None
                ),
                # ROADMAP item 127. What the Overview was handed, kept so the
                # published prose can be checked against it afterwards — the
                # prompt orders the sentence used VERBATIM and nothing could
                # verify that, because the sentence was nowhere in the record.
                day_over_day=day_over_day,
            )
        ],
        # Superseded by prediction_rows and deliberately not written — see
        # DailyLogEntry.model_predictions for why None rather than empty.
        model_predictions=None,
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
        guidance_initialised_at=guidance.guidance_cycle.initialised_at,
        guidance_age_hours=guidance.guidance_cycle.age_hours,
        guidance_source=guidance.guidance_cycle.source,
        forecast_reach=observe_forecast_reach(
            guidance.primary_daily,
            guidance.degradations,
            met_service_present=guidance.met_service_prediction is not None,
            met_service_day3_present=guidance.met_service_prediction_day3 is not None,
            met_service_model_id=deps.location.local_bulletin_model_id,
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider=type(deps.llm_provider).__name__,
            llm_model=getattr(deps.llm_provider, "model", "unknown"),
            pipeline_version=deps.pipeline_version,
            system_prompt_sha256=prompt_archive.combined_prompt_sha256(
                judgment_prompt, narrative_prompt
            ),
            finish_reason=response_meta.finish_reason,
            input_tokens=response_meta.input_tokens,
            output_tokens=response_meta.output_tokens,
            thought_tokens=response_meta.thought_tokens,
            # Sized here, from the same strings the archive hashes, so the
            # stored figure and the archived prompt describe one issuance.
            prompt_size=measure_prompt(judgment_prompt, narrative_prompt, user_prompt),
            response_schema_sha256=response_meta.response_schema_sha256,
            nullable_fields=_nullable_fields(last_response),
            narrative_findings=_narrative_findings(llm_response, today),
            # THE SAME READING THE PROMPT WAS BUILT FROM, passed in rather
            # than re-fetched. Two calls to the station would be two answers
            # on a day it changed between them, and the record would then
            # describe an observation the forecaster never saw.
            #
            # The SIGNALS are passed in for the same class of reason, since
            # item 121: they decide whether this run reaches the model at all,
            # so they are computed before that decision and recorded here
            # rather than computed twice and able to disagree.
            information_moved=information_moved,
            trigger_source=deps.trigger_source or None,
            # The clock the prompt was built with, so the page shows the same
            # one — see LogEntryMeta.issued_local_time.
            issued_local_time=guidance.issuance.local_time if guidance.issuance else None,
            # Equal to issued_local_time on every run that reaches here, which
            # is every run that reasons. They come apart only on the
            # observation-only path — see LogEntryMeta.observations_local_time.
            observations_local_time=guidance.issuance.local_time if guidance.issuance else None,
            degradations=guidance.degradations,
        ),
    )

    if existing_entry is None:
        return issuance

    # WHAT A LATER ISSUANCE MUST NOT REWRITE.
    #
    # Traced on a real sequence — morning run, evening refresh, forced
    # run-daily: the third run built a brand-new entry, which wiped
    # morning_issuance (the only copy of what was published this morning),
    # reset generated_at_utc so the entry claimed to have been created hours
    # after it was, and cleared refreshed_at — which re-opened the refresh
    # gate, so the NEXT refresh would snapshot the forced narrative as that
    # day's morning issuance.
    #
    # prediction_rows is the one that makes the accuracy record trustworthy:
    # the numbers tomorrow scores are row 0's, and row 0 is whatever the
    # day's first issuance committed. Appending rather than replacing is the
    # whole guard — there is no path here that rewrites an existing row.
    #
    # verification and yesterday_verification_summary are carried for a
    # related reason: this run is told it is a later issuance, so the model
    # returns a PLACEHOLDER for the verification fields (by design — see the
    # LATER ISSUANCE block). Storing that would overwrite the real
    # verification the day's first run wrote. `verification` itself is
    # written a day later, when the actuals exist, so losing it here loses
    # scores that cannot be recomputed from this entry.
    #
    # sunrise/sunset fall back rather than overwrite: a sun computation that
    # threw on a re-issue must not erase a good value the morning captured.
    #
    # local_bulletin keeps the morning's. A met service replaces its bulletin
    # and the stored copy is the only one there will ever be, so the rule is
    # not to lose one — but the entry holds a single record, so a bulletin
    # that genuinely changed mid-day loses the other copy whichever way this
    # goes. Kept as the refresh had it, which is what the production evening
    # run has always done; ROADMAP item 104 records it as the operator's to
    # settle.
    snapshot = existing_entry.to_issuance_snapshot()

    return issuance.model_copy(
        update={
            # APPEND-ONLY, and row 0 is never rewritten. This is the
            # write-once rule the accuracy record rests on, in its general
            # form: the numbers tomorrow scores are the ones the day's first
            # issuance committed, and a later issuance adds a row.
            #
            # `resolve_prediction_rows` rather than `.prediction_rows` so an
            # entry written before contract item 4 contributes its single
            # stored set as row 0 instead of being silently dropped.
            "prediction_rows": [
                *resolve_prediction_rows(existing_entry),
                *issuance.prediction_rows,
            ],
            "model_predictions": None,
            "verification": existing_entry.verification,
            "yesterday_verification_summary": existing_entry.yesterday_verification_summary,
            "sunrise": issuance.sunrise or existing_entry.sunrise,
            "sunset": issuance.sunset or existing_entry.sunset,
            "local_bulletin": existing_entry.local_bulletin or issuance.local_bulletin,
            # NO LONGER WRITTEN — ROADMAP item 137.
            #
            # `morning_issuance` held the day's first issuance back when a day
            # had at most two, and it kept being set long after
            # `earlier_issuances` superseded it: every later run stored the
            # SAME snapshot twice, once under a name from the dead
            # morning/evening model. `issuance_log()` has preferred
            # `earlier_issuances` throughout, so the duplicate was read by
            # nothing but `publish/pages.py`, which now goes through
            # `issuance_log()` as well.
            #
            # CARRIED FORWARD, NOT CLEARED. An entry that already has one
            # keeps it: this record is an archive, and blanking a field on
            # re-issue would edit what an earlier run actually stored. New
            # days simply never gain one — see `issuance_log`'s docstring on
            # why both shapes are read forever.
            "morning_issuance": existing_entry.morning_issuance,
            "earlier_issuances": [*existing_entry.earlier_issuances, snapshot],
            "meta": issuance.meta.model_copy(
                update={
                    "generated_at_utc": existing_entry.meta.generated_at_utc,
                    "refreshed_at": datetime.now(timezone.utc),
                }
            ),
        }
    )


def _verify_recent_windows(deps: PipelineDeps, today: date) -> list[date]:
    """Score every stored issuance window whose days have finished.

    ROADMAP item 104, contract item 2. Returns the dates whose entries changed.

    ITS OWN ARCHIVE FETCH, DELIBERATELY, and it never touches the actuals
    cache. The daily refresh fetches exactly yesterday and buckets it by
    calendar date; a window straddles midnight, so it needs two days at once,
    and widening the cached fetch to get them would put a PARTIAL today into
    the cache that tomorrow's verification would then score against. One extra
    archive request costs nothing and cannot corrupt the record.

    BEST EFFORT. A window left unscored is scored by a later run — the archive
    does not change for finished days — so a failure here must not take the
    forecast down with it.
    """
    location = deps.location
    start = add_days(today, -(WINDOW_VERIFY_LOOKBACK_DAYS + 1))
    end = add_days(today, -1)

    try:
        archive = open_meteo.fetch_archive_range(
            location.primary_point.lat, location.primary_point.lon, start, end, location.timezone
        )
    except Exception as e:  # noqa: BLE001 - never fatal; the forecast stands
        print(f"Window verification skipped ({e}); a later run will score them.", file=sys.stderr)
        return []

    # The airport over the same span, once, so each window is scored against
    # the evidence the calendar day already gets — ROADMAP item 139. Best
    # effort like the archive: no station, or a fetch that failed, scores the
    # window against the reanalysis alone, which is what happened before.
    station_reports = _station_reports(location, start, end)
    changed: list[date] = []
    for d in log_store.list_log_dates(deps.data_dir):
        if not (start <= d <= end):
            continue
        entry = log_store.read_log_entry(deps.data_dir, d)
        if entry is None:
            continue
        if verify_closed_windows(
            entry, archive, today=today,
            station_reports=station_reports, timezone_name=location.timezone,
        ):
            log_store.write_log_entry(deps.data_dir, entry)
            changed.append(d)

    return changed


def _station_reports(location: LocationConfig, start: date, end: date) -> list | None:
    """Raw airport reports over a span, padded a day either side so a window
    that opens late in `end` still sees its reports. None without a station
    or when the archive is unreachable."""
    if not location.metar_station_icao:
        return None
    try:
        return metar_fetch.fetch_metar_archive(
            location.metar_station_icao, add_days(start, -1), add_days(end, 2)
        )
    except Exception as e:  # noqa: BLE001 - never fatal; the reanalysis scores alone
        print(f"Station reports unavailable for window scoring ({e}).", file=sys.stderr)
        return None


def _run_actuals_refresh(
    deps: PipelineDeps,
    cache: Any,
    today: date,
    yesterday: date,
    log_dates_for_retention: list[date],
) -> None:
    """Bring the actuals cache up to date, in place.

    Lifted out of the pipeline body unchanged by ROADMAP item 104 step 4, so
    that "only the day's first issuance does this" is one `if` rather than
    forty indented lines. It is the only part of a run that makes archive
    requests, which is why it is the part a later issuance skips.
    """
    location = deps.location

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
    fixed_window_cutoff = add_days(today, -(ACTUALS_BATCH_LOOKBACK_DAYS + 5))
    prune_cutoff = (
        min(fixed_window_cutoff, min(log_dates_for_retention))
        if log_dates_for_retention
        else fixed_window_cutoff
    )
    actuals_cache_store.prune_older_than(cache.primary, prune_cutoff)
    actuals_cache_store.prune_older_than(cache.secondary, prune_cutoff)


def _write_back_verification(
    deps: PipelineDeps,
    verification_result: Any,
    llm_response: Any,
    log_lookup: Any,
) -> None:
    """Patch the rows this run verified, and store the track record.

    Lifted out of the pipeline body unchanged by ROADMAP item 104 step 4, so
    the rule "only the day's first issuance verifies" is one `if` around one
    call rather than a gate threaded through forty lines. The scoring pass
    itself is idempotent; the notes and summaries written here are not, which
    is why the whole block moves together.
    """
    # THE NOTE IS NO LONGER WRITTEN — ROADMAP item 147. What is still written
    # is `verified`, which is a FACT about whether the row was scored and has
    # nothing to do with the prose that used to accompany it. Entries from
    # before this change keep the notes they have; the archive is not migrated.
    for row_date, lead_time_days in verification_result.newly_verified:
        historical_entry = log_lookup(row_date)
        if historical_entry is None:
            continue
        historical_entry.verification.for_lead(lead_time_days).verified = True
        log_store.write_log_entry(deps.data_dir, historical_entry)

    summaries_by_key = {
        (s.model, s.lead_time_days): s.summary for s in llm_response.skill_profile_summaries
    }
    _merge_skill_summaries(verification_result.updated_track_record.entries, summaries_by_key)
    track_record_store.write_track_record(deps.data_dir, verification_result.updated_track_record)


def beyond_reach_summary(forecast_horizon_days: int) -> str:
    """The code-written skill summary for a lead past a source's reach.
    A lead is the one digit the summaries rule allows."""
    return f"Does not forecast at this lead; its guidance here reaches Day+{forecast_horizon_days}."


def _merge_skill_summaries(entries: list, summaries_by_key: dict[tuple[str, int], str]) -> None:
    """Writes this run's skill summaries onto the rows — ROADMAP item 150,
    step 3.

    A ROW BEYOND ITS SOURCE'S REACH IS LABELLED BY CODE, and the model's text
    for it is ignored. Item 149 rewrote the prompt so the model would stop
    saying "insufficient data yet" about a lead a source never reaches, and
    the three rows kept saying it anyway: the model is only asked for a
    summary on pairs "that has a result today", those pairs never have one,
    and a pair the model does not return keeps whatever it had. Measured
    2026-09-16, hours after that prompt change: icon, ukmo and kenya_met at
    Day+7 still stored the "yet". The horizon is a fact the record holds, so
    the record writes the label and keeps writing it.
    """
    for entry in entries:
        horizon = entry.forecast_horizon_days
        if horizon is not None and horizon < entry.lead_time_days:
            entry.skill_profile_summary = beyond_reach_summary(horizon)
            continue

        summary = summaries_by_key.get((entry.model, entry.lead_time_days))
        if summary:
            entry.skill_profile_summary = summary


def forecast_horizons_of(entries: list) -> dict[str, int]:
    """Each source's stored horizon, from its track record rows — the same
    value the row label is written from, so the review and the row agree."""
    horizons: dict[str, int] = {}
    for entry in entries:
        if entry.forecast_horizon_days is not None:
            horizons[entry.model] = max(horizons.get(entry.model, -1), entry.forecast_horizon_days)
    return horizons


def _refresh_observations_only(
    deps: PipelineDeps,
    today: date,
    existing_entry: DailyLogEntry,
    guidance: ForwardGuidance,
    *,
    observed_so_far: ObservedSoFar | None,
    dry_run: bool,
) -> ObservationsRefreshed:
    """Update what the station has seen, and touch nothing else.

    ROADMAP item 121's saving. The standing forecast keeps its narrative, its
    scored numbers and its verification; only the observed reading and the
    clock that reading was taken at move.

    BUILT BY COPYING THE STORED ENTRY RATHER THAN COMPOSING A NEW ONE, and
    that is the safety property rather than a shortcut. `_compose_log_entry`
    builds a fresh entry and then carries forward the fields a later issuance
    must not rewrite — a list that has to be right, and that was measured
    wrong in six places when there were two of it. Here there is no list:
    everything not named below is the same object it was, so a field added to
    the entry next year is preserved by default instead of by remembering.

    WHAT IS DELIBERATELY NOT DONE, each for its own reason:

      - NO PREDICTION ROW. Contract item 4 appends one row per issuance, and
        an issuance that made no judgment has no numbers to add. This path
        runs precisely when no new cycle has landed, so the row would hold
        the previous row's figures re-stamped, and every reader of the record
        would have to learn to skip it.
      - NO `refreshed_at`. That field means the NARRATIVE was refreshed, and
        it re-opens the morning-snapshot gate. Setting it here would let the
        next real re-issue snapshot an unchanged narrative as the day's
        morning issuance.
      - NO PROMPT ARCHIVE. The archive answers "what was this forecast built
        from", keyed by issuance instant. No prompt was built; an entry there
        would be an input set for a forecast that never happened.
      - NO `information_moved` REWRITE. The stored signals belong to the run
        that reasoned on them and are the record of why it spent. That this
        run found nothing moved is already said by the fact that it left the
        narrative alone, and is visible as `observations_local_time` running
        ahead of `issued_local_time`.
      - NO EMAIL. A later issuance has never emailed, and this one has even
        less to announce.

    It DOES publish. Re-rendering is the whole point — the observation has to
    reach a reader, and until it does the cost saving item 121 exists for is
    not realised. See ROADMAP item 121, "Publishing".
    """
    # The clock this reading was taken at, from the same reconciled issuance
    # the full path stamps with — never a fresh `datetime.now`, which would
    # disagree with it on a machine whose clock daypart.reconcile_now had to
    # override. See LogEntryMeta.issued_local_time.
    local_time = guidance.issuance.local_time if guidance.issuance else None

    entry = existing_entry.model_copy(
        update={
            "observed_so_far": observed_so_far,
            "meta": existing_entry.meta.model_copy(
                update={"observations_local_time": local_time}
            ),
        }
    )

    published = False
    if not dry_run:
        log_store.write_log_entry(deps.data_dir, entry)
        if deps.publisher is not None:
            deps.publisher.publish(entry)
            published = True

    return ObservationsRefreshed(today=today, log_entry=entry, published=published)


def _issue_forecast(
    deps: PipelineDeps,
    today: date,
    existing_entry: DailyLogEntry | None,
    dry_run: bool,
    *,
    # NO DEFAULT, deliberately. This decides whether item 121's gate can
    # decline to buy a narrative, and a call site that forgot it would get
    # the gate silently applied where the caller meant to override it. Two
    # call sites pass it today; a third has to say what it wants.
    force: bool,
) -> ForecastRunResult | ObservationsRefreshed:
    """One issuance of the day's forecast, whichever issuance it is.

    ROADMAP item 104 step 4. There were two bodies, and the difference
    between them was never what kind of run it was — it was whether the day
    already had an entry. `run_forecast` reads that one level up and this
    body branches on it once, as `first_issuance`.

    WHAT ONLY THE FIRST ISSUANCE OF A DAY DOES, and why each is not merely
    an optimisation:

      - Fetches yesterday's actuals and writes the cache. Real archive
        requests; a later issuance reads the same cache and adds nothing.
      - Verifies and scores, and writes the track record. Yesterday's
        observations do not change during the day, so re-verifying would
        rewrite the record's learning loop several times against unchanged
        inputs — and would overwrite the first run's note with a placeholder.
      - Emails. Preserved as it was; see ROADMAP item 104 on making it
        configurable, and note this is NOT the Apps Script mailer, which
        runs on its own trigger and already mails every issuance.

    Everything else runs on every issuance, and the values a later issuance
    must not rewrite are enforced in `_compose_log_entry`, not here.
    """
    location = deps.location
    yesterday = add_days(today, -1)
    # The one question this body branches on. It is a property of the DAY —
    # whether it already holds an entry — not of which verb was typed, which
    # is the whole of item 104.
    first_issuance = existing_entry is None

    # --- Step 1: today's forward-looking guidance + optional sources ---
    guidance = _fetch_forward_guidance(deps)
    if not first_issuance:
        # A re-fetch that comes back empty must not erase a real reading the
        # day's first run captured — see aqi.merge_ground_aqi. This was
        # applied only by the old refresh path, so a re-issue reached through
        # the other one could silently drop the day's ground AQI.
        guidance = _with_merged_ground_aqi(guidance, existing_entry.ground_aqi)
    primary_hourly = guidance.primary_hourly
    primary_daily = guidance.primary_daily

    # --- Step 2: actuals cache (daily upsert, or weekly full re-fetch) ---
    #
    # FIRST ISSUANCE ONLY, because this is where the archive requests are.
    # Yesterday's observations do not change during the day, so a later
    # issuance reads the cache the first one wrote and asks for nothing.
    cache = actuals_cache_store.read_actuals_cache(deps.data_dir)
    log_dates_for_retention = log_store.list_log_dates(deps.data_dir)
    if first_issuance:
        _run_actuals_refresh(deps, cache, today, yesterday, log_dates_for_retention)
        # Contract item 2. Separate from the refresh above and from the daily
        # verification below: this one is keyed on each ISSUANCE's own 24
        # hours rather than on a calendar day, so it re-reads recent entries
        # and scores whatever has become observable. First issuance only, for
        # the same reason the rest of verification is — the archive does not
        # change during a day.
        if not dry_run:
            _verify_recent_windows(deps, today)
    actuals_primary = actuals_cache_store.as_date_dict(cache.primary)

    # --- Step 3: deterministic verification + rolling stats ---
    #
    # FIRST ISSUANCE ONLY. Re-verifying against unchanged observations would
    # rewrite the record's learning loop several times a day, and the note a
    # later issuance returns is a placeholder by design.
    log_lookup = log_store.make_log_lookup(deps.data_dir)
    verification_result = (
        run_deterministic_verification_and_scoring(
            log_lookup=log_lookup,
            prior_track_record=track_record_store.read_track_record(deps.data_dir),
            earliest_log_date=min(log_dates_for_retention) if log_dates_for_retention else None,
            actuals_primary=actuals_primary,
            today=today,
            yesterday=yesterday,
            models=scored_models(location.local_bulletin_model_id),
        )
        if first_issuance
        else None
    )

    # WHICH TRACK RECORD THIS RUN READS, decided ONCE — the first run of a day
    # reads what it has just re-derived, a later one reads what that run
    # stored, because it verified nothing and has nothing fresher.
    #
    # Hoisted here because it now has two consumers rather than one: the
    # forecaster's MODEL TRACK RECORD block, and the gust calibration below.
    # The rule was previously written out at the payload's call site alone,
    # and a second copy of it is exactly the divergence item 104 exists to
    # close — the calibration and the block the forecaster reads must be
    # computed from the same record, or code corrects from one set of numbers
    # while the prompt argues from another.
    track_record_entries = (
        verification_result.updated_track_record.entries
        if first_issuance
        else track_record_store.read_track_record(deps.data_dir).entries
    )

    # --- Step 5: extract today's raw per-model predictions (code, not LLM) ---
    day0_predictions = extract_day0_predictions_from_hourly(primary_hourly, MODELS)

    # The same models over the window that starts when this issuance does —
    # ROADMAP item 104, contract item 2. Stored beside Day+0 and scored by
    # nothing yet; see IssuancePredictions.window_predictions for why it
    # accumulates before it replaces anything.
    #
    # FED FROM `forward_hourly`, WHICH IS THE TWO-DAY FETCH. That series is
    # the one `fetch_forecast_hourly_forward` keeps deliberately away from
    # scoring — "widening that fetch to two days would silently score 48 hours
    # as today". Crossing that fence is the whole of the reframe, so it is
    # crossed HERE, once, into a field nothing scores, rather than by widening
    # the fetch that Day+0 still reads. `primary_hourly` is untouched and
    # Day+0 above cannot be reached from this line.
    window_predictions = (
        extract_window_predictions(
            guidance.forward_hourly, MODELS, issued_local=guidance.issued_at_local
        )
        if guidance.forward_hourly and guidance.issued_at_local is not None
        else []
    )

    # Deterministic day-over-day comparison for the Overview — in code, not
    # the LLM. A live run asked to compare 29.6°C against 29.5°C described it
    # as "about 1°C cooler": a ten-fold overstatement of the one sentence
    # most readers actually act on. See comparison.py.
    #
    # today_convective is the OTHER half of the comparison's thunder
    # dimension. Without it today's side could never be thundery while
    # yesterday's always could, so a thundery yesterday manufactured a change
    # — see compute_day_over_day. The flag is already computed above, from
    # the hours ahead; it simply never reached here.
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

    # ROADMAP item 61 — where the Overview learns to look past today.
    #
    # Days 1-3 from the SAME daily source and the SAME model list as the
    # scored Day+3 row above, so the clause a reader acts on and the number
    # the record scores cannot describe different weather. Consensus across
    # models per day, then banded in comparison.py: the arithmetic lives in
    # code, and the prompt is handed a finished phrase.
    # Composed by _locked_blocks with every other block the prompt locks, so
    # this path and the refresh cannot compose different sets — item 104.

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

    # EVERY ISSUANCE EXTRACTS ITS OWN, and this replaced a swap that kept the
    # day's first set. ROADMAP item 104, contract items 3 and 4.
    #
    # The swap existed because the record held ONE set per day: re-deriving
    # from a fresher cycle "would leave the narrative describing values the
    # record doesn't contain". Contract item 4 gives every issuance its own
    # row, so the record now contains exactly what each issuance saw, and the
    # swap's premise is gone — the same way item 104's own C4 dissolved once
    # Day+0 stopped being a calendar day.
    #
    # WHAT THE SWAP WAS NOT PROTECTING is worth stating, because it reads
    # like the write-once guard and is not it. The numbers tomorrow scores
    # are row 0, and row 0 is protected by `_compose_log_entry` appending
    # rather than rewriting. A caller may still invoke this with any
    # combination of flags and be unable to reach the scored numbers; the
    # worst it achieves is a wasted API call.
    #
    # And it is what makes C3 possible. A row whose models came from the
    # morning's cycle and whose blend came from the evening's is not a paired
    # comparison — the blend saw guidance the models it sits beside did not.
    # Measured 2026-09-13: the 06:02 issuance read the 18Z cycle and the 18:02
    # issuance read the 06Z cycle, both 9.0 hours old.
    # Reasons from THIS issuance's extraction, like everything else it sends.
    # It read the day's stored numbers while the record held only those; now
    # that every issuance has its own row, the comparison an evening reader
    # sees is built from the cycle that evening actually read.
    #
    # today_convective is the OTHER half of the comparison's thunder
    # dimension, and is recomputed from THIS issuance's forward hours:
    # without it today's side could never be thundery while yesterday's
    # always could, so a thundery yesterday manufactured a change.
    #
    # issued_hour is why item 118 exists: an evening issuance reusing the
    # morning's onset would compose "dry until evening" after the evening
    # had begun.

    # DAYS 1-3, EXTRACTED ONCE, from the SAME daily source and the SAME model
    # list as the scored Day+3 row — so the clause a reader acts on and the
    # number the record scores cannot describe different weather.
    #
    # Hoisted out of `_locked_blocks` because contract item 8's evening
    # subject is a second consumer: after sunset the comparison is about
    # TOMORROW, and tomorrow is `extended_days[0]`. Extracting it twice would
    # be cheap and would also be exactly the drift `_locked_blocks` exists to
    # prevent — two computations of one quantity that agree only by luck.
    extended_days = [
        extract_day_n_predictions_from_daily(guidance.primary_daily, n, MODELS)
        for n in (1, 2, 3)
    ]

    # ONE READING OF THE STATION PER RUN, taken before the prompt because item
    # 121 puts it IN the prompt, shared with the record below so the two
    # cannot describe different observations, and now also the BASELINE an
    # evening comparison measures tomorrow against — see observed_baseline.
    observed_so_far, observed_gap = _observed_so_far(location, today)
    if observed_gap is not None:
        # Appended to the guidance's own list because that is what reaches
        # `meta.degradations` — see ROADMAP item 151. The station is read
        # AFTER guidance is built, so this is the one degradation that cannot
        # be raised where the others are.
        guidance.degradations.append(observed_gap)

    # THE GUST THE RECORD SAYS TO EXPECT — calibration.py, which carries the
    # out-of-sample validation and the reason this is not applied to the
    # scored rows. Computed here, before both consumers, so the number the
    # forecaster is handed and the number the comparison bands from are one
    # number and cannot drift.
    gust_bias = gust_corrections(track_record_entries)
    calibrated_wind_kmh = calibrated_gust_consensus(day0_predictions, gust_bias)

    day_over_day = compute_day_over_day(
        actuals_primary.get(yesterday),
        day0_predictions,
        today_convective=(
            guidance.instability.convective if guidance.instability is not None else None
        ),
        issued_hour=_issued_hour(guidance.issuance),
        calibrated_wind_kmh=calibrated_wind_kmh,
        # CONTRACT ITEM 8, STAGE 2 — the evening subject, wired. After sunset
        # the comparison is about tomorrow, measured against today's daytime,
        # and named for the days it means.
        #
        # WHAT THIS CHANGES IN PRACTICE IS SMALL AND WORTH STATING: this
        # deployment issues at 06:00 and 18:00, and 18:00 is BEFORE a sunset
        # that sits near 18:40 all year at this latitude, so the gate keeps
        # returning None there. The consumer that reaches it is the app, where
        # a forecast is issued whenever someone taps — including after dark.
        sunset_hour=_sunset_hour(guidance.issuance),
        tomorrow_predictions=extended_days[0],
        today_actual=observed_baseline(observed_so_far),
        today_name=weekday_name(today),
        tomorrow_name=weekday_name(add_days(today, 1)),
    )

    # C2's three triggers, computed ONCE and here — before the decision they
    # inform rather than inside the entry that records them. They were stored
    # and acted on by nothing from item 104 stage 2b until item 121; this is
    # where that changes.
    # ROADMAP item 145. The deployment's REPORTING bands, which reach the
    # footnote and cannot reach the spending decision.
    information_moved = _information_moved(
        guidance, existing_entry, observed_so_far, deviation_bands(deps.location)
    )

    # --- Step 5b: does this run earn an LLM call? ---
    #
    # ROADMAP item 121. Everything above this line is free of LLM spend:
    # fetches, extraction, the day-over-day comparison and the station
    # reading are all code. What follows is the only part that costs, and
    # when nothing has moved there is nothing for it to reason about.
    #
    # The observations are the point. They refresh on EVERY run either way —
    # that is what makes declining the call honest rather than a shortcut,
    # because the reader still learns that it started raining at 13:00.
    if not force and not llm_should_reason(information_moved, location.llm_refresh_policy):
        # Guaranteed by `llm_should_reason` itself: `first_issuance_of_day` is
        # `existing_entry is None`, and a first issuance always reasons. Stated
        # rather than assumed, because the guarantee lives in another module —
        # a future policy that let a first issuance decline would otherwise
        # surface here as an AttributeError on a half-written day.
        assert existing_entry is not None, "a day with no entry must never reach the cheap path"

        return _refresh_observations_only(
            deps,
            today,
            existing_entry,
            guidance,
            observed_so_far=observed_so_far,
            dry_run=dry_run,
        )

    # --- Step 6: call the LLM ---
    # A location with no WAQI stations gets a prompt with no ground-station
    # guidance and no GROUND AQI blocks at all, rather than a daily note that
    # no station reported — nothing reported because nothing was configured.
    ground_stations_configured = bool(location.waqi_stations)
    # Same distinction for the met service: a location with none wired is a
    # state, not a fetch that came back empty. LocalBulletinRecord already
    # keys off this same field for whether to store a bulletin at all.
    local_bulletin_configured = bool(location.local_bulletin_source_name)
    # ROADMAP item 51. DERIVED FROM THE RECORDED CODE, not from
    # `bool(primary_daily)`. An empty dict cannot tell a fetch that failed
    # from a fetch that was never wired up, and the degradation is the run's
    # own statement about which happened — so the prompt and the log entry
    # cannot disagree about whether the week is missing.
    extended_outlook_available = DEGRADATION_EXTENDED_OUTLOOK not in {
        d.code for d in guidance.degradations
    }
    # A run on a day that already has an entry is a later issuance, whatever
    # verb was typed. Told otherwise it writes a fresh morning-style forecast
    # over one the readers have already had, and emails it as the day's first.
    prompt_flags = dict(
        # KEYED ON VERIFICATION, NOT ON WHICH RUN THIS IS — 2026-09-16.
        # The block it gates says only "this day's verification is already
        # written"; that is true exactly when the day already has an entry,
        # so the value is unchanged and the MEANING is not. The old name
        # asserted a morning/evening distinction the system no longer draws.
        verification_already_written=not first_issuance,
        ground_stations_configured=ground_stations_configured,
        local_bulletin_configured=local_bulletin_configured,
        extended_outlook_available=extended_outlook_available,
    )
    judgment_prompt = build_judgment_prompt(location, **prompt_flags)
    narrative_prompt = build_narrative_prompt(location, **prompt_flags)
    # THE FOURTH PLACE THE STANDING RULE HAS TO BE APPLIED, and the one it
    # was missing. per_model_scores is scored for EVERY model, the blend and
    # the two baselines included, and it went to the prompt unfiltered while
    # the system prompt in the same call said "Your own accuracy record is
    # deliberately NOT in your context".
    #
    # It survived because the tests for this rule all run a FIRST day, which
    # has nothing to verify — so this block was empty in every one of them.
    # Found 2026-09-09 by the prompt harness reading the real 2026-09-08
    # payload, where olw_blend's Day+0 score and both baselines at all three
    # leads were sitting in it.
    forecaster_models = models_visible_to_the_forecaster(location.local_bulletin_model_id)
    # A later issuance verified nothing — yesterday's actuals do not change
    # during the day — so it says so rather than repeating the morning's
    # scores as though they were new. The response's verification fields are
    # read and discarded on that path; `_compose_log_entry` keeps the real
    # ones the first run stored.
    verification_context = [
        {
            "lead_time_days": r.lead_time_days,
            "target_date_verified": format_date(r.target_date_verified) if r.target_date_verified else None,
            "per_model_scores": {
                model: score.model_dump()
                for model, score in r.per_model_scores.items()
                if model in forecaster_models
            },
        }
        for r in verification_result.lead_time_results
    ] if first_issuance else {
        "note": "No new verification this run — same-day re-issue; see the first issuance."
    }
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
    # A STORED FIGURE IS ALWAYS A CYCLE OLD BY THE TIME IT IS READ — item 91.
    # Dropped whole rather than stripped: a summary with its number cut out
    # reads as a sentence missing a word, and the qualitative half is still
    # written fresh every run for every pair that verified.
    # `track_record_entries` was chosen once, up at the verification step —
    # a later issuance scored nothing, so the freshest figures available are
    # the stored ones the first run wrote. Item 91's warning applies harder
    # here: every summary it reads was written by an earlier run.
    track_record_context = _track_record_payload(track_record_entries, forecaster_models)
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
            forecast_horizons=forecast_horizons_of(track_record_entries),
        )
    )
    # The same extracted values that get scored, handed to the LLM so its
    # narrative and the accuracy record describe one set of numbers rather
    # than two. This is also where the met service becomes visible as a peer
    # of the numerical models rather than only as prose.
    model_predictions_context = _model_predictions_prompt_payload(
        day0_predictions, day3_predictions, day7_predictions
    )
    user_prompt = _build_forecast_prompt(
        deps,
        guidance,
        existing_entry,
        today,
        day0_predictions=day0_predictions,
        observed_so_far=observed_so_far,
        overnight_low_divergence=information_moved.low_divergence,
        verification_context=verification_context,
        model_predictions_context=model_predictions_context,
        track_record_context=track_record_context,
        review_context=review_context,
        yesterday_actual=comparison_for_prompt(
            asdict(day_over_day) if day_over_day is not None else None
        ),
        calibrated_wind_kmh=calibrated_wind_kmh,
        extended_days=extended_days,
    )
    # Route EVERY request the provider makes through the cap — retries
    # included. Raises SpendCapExceeded, deliberately NOT caught here: the
    # run must fail loudly rather than quietly produce no forecast.
    _verify_spend, _last_response = attach_spend_cap(
        deps.llm_provider,
        deps.data_dir,
        max_calls=location.max_llm_calls_per_24h,
        # ONE LABEL, BECAUSE EVERY RUN IS A FRESH FORECAST — ROADMAP item 137,
        # the operator's decision 2026-09-16.
        #
        # This field held three spellings for one activity. "refresh" came
        # from the twice-a-day tool item 104 removed and read as something
        # lesser — it was actively misleading on 2026-09-15, where four HTTP
        # 500s were filed under "refresh" for a run that had new guidance and
        # was doing the full job. It was renamed to "forecast-reissue" earlier
        # the same day, which was no better: it kept asserting a distinction
        # the system had stopped drawing.
        #
        # THE ARGUMENT FOR KEEPING IT DOES NOT SURVIVE INSPECTION. It was that
        # an operator reading the ledger wants to see which issuance of the
        # day a call belonged to — but every row carries `at`, so the ledger
        # already answers that, by counting rows within a date. The label was
        # a second, weaker copy of information the timestamp holds exactly.
        #
        # Other purposes stay distinct because they are different ACTIVITIES,
        # not different runs of the same one: "health-check" and "replay" buy
        # something that is not a forecast. Rows before this date still say
        # "refresh" and "forecast-reissue"; all three mean the same thing and
        # nothing re-writes history.
        purpose="forecast",
        calls_needed=LLM_CALLS_PER_FORECAST,
    )
    _call, _call_meta = _generate_forecast(
        deps.llm_provider, judgment_prompt, narrative_prompt, user_prompt, _last_response
    )
    _verify_spend()
    llm_response = _call.response

    # The write-up failed and the scored call did not — ROADMAP item 59 step
    # 3. Recorded rather than raised: the numbers below are real, and losing
    # them to publish nothing would put a hole in the accuracy record.
    if _call.narrative_error is not None:
        print(
            f"Narrative call failed ({_call.narrative_error}); publishing the "
            "scored forecast without its write-up.",
            file=sys.stderr,
        )
        guidance.degradations.append(
            RunDegradation(
                code=DEGRADATION_NARRATIVE,
                summary=(
                    "Today's figures are here, but the write-up that normally "
                    "explains them could not be produced this time. The numbers "
                    "are the same ones this forecast is scored on."
                ),
                detail=(
                    "The rendering call failed after its retries while the "
                    "judgment call had already succeeded, so the scored "
                    "prediction was published without a narrative: "
                    f"{_call.narrative_error}"
                ),
            )
        )

    # --- Step 7: build today's log entry ---
    tp = llm_response.today_properties
    log_entry = _compose_log_entry(
        deps,
        guidance,
        existing_entry,
        today,
        llm_response,
        observed_so_far=observed_so_far,
        information_moved=information_moved,
        window_predictions=window_predictions,
        # Item 127. The same object the prompt was built from, so the stored
        # copy and the sentence the forecaster read cannot be different ones.
        day_over_day=day_over_day,
        # The freshly extracted set, which the composer keeps only when this
        # date holds none. Built even on a re-issue and discarded there, as
        # it always was — the day's numbers belong to the run that made them
        # first.
        fresh_predictions=ModelPredictionsByLead(
            # The blend joins Day+0 as a peer of the models it synthesizes, so
            # tomorrow scores the forecast this run actually published and not
            # only the guidance that fed it. Day+3 and Day+7 carry the blend's
            # own rain call too since ROADMAP item 72's minimal shape — rain
            # and its probability, which is what Brier scores; the rest of the
            # extended schema waits for real data to design against.
            #
            # STAMPED WITH WHAT THEY TARGET — ROADMAP item 104, C1. Done here
            # at assembly rather than inside `extract`, deliberately: the
            # extractors are pinned byte-for-byte by spec/vectors, and the
            # target is a property of the ISSUANCE that collected them rather
            # than of the extraction. Stamping here keeps the vectors and
            # their Dart mirror untouched by a change that is about the
            # record's shape.
            day0=_targeting([*day0_predictions, _blend_prediction(tp)], today, 0),
            day3=_targeting(
                [
                    *day3_predictions,
                    *_extended_blend_predictions(llm_response.extended_properties, 3),
                ],
                today,
                3,
            ),
            day7=_targeting(
                [
                    *day7_predictions,
                    *_extended_blend_predictions(llm_response.extended_properties, 7),
                ],
                today,
                7,
            ),
        ),
        judgment_prompt=judgment_prompt,
        narrative_prompt=narrative_prompt,
        user_prompt=user_prompt,
        last_response=_last_response,
    )

    published = False
    emailed = False

    # --- Step 8: write files + patch historical rows/track record ---
    if not dry_run:
        log_store.write_log_entry(deps.data_dir, log_entry)
        if first_issuance:
            actuals_cache_store.write_actuals_cache(deps.data_dir, cache)

        # The inputs this forecast was built from — ROADMAP item 69. Written
        # beside the log entry rather than before the LLM call, deliberately:
        # an archived issuance with no entry in the log is a dangling input,
        # backtestable against nothing, because the pairing this exists to
        # enable needs the incumbent's own scored prediction on the other side.
        prompt_archive.write_prompt_archive(
            deps.data_dir,
            today,
            # THIS issuance's instant, not the day's first. The archive is
            # keyed on it, and a re-issue deliberately carries
            # generated_at_utc from the first run — so stamping with that
            # overwrote the morning's archived prompt at the same key and
            # destroyed the only copy of it. Measured 2026-09-13: two runs
            # through the then-separate `run-daily` verb, one archived
            # issuance.
            issued_at=log_entry.last_issued_at,
            judgment_prompt=judgment_prompt,
            narrative_prompt=narrative_prompt,
            user_prompt=user_prompt,
            llm_model=log_entry.meta.llm_model,
        )

        # A later issuance does no verification, so there is nothing to write
        # back. The rows it would land on were scored by the day's first run,
        # and the notes there are the record of what was actually checked —
        # re-running the scoring pass is idempotent, but overwriting a note
        # with "no new verification this run" is not.
        #
        # This used to be a `notes_by_lead` that emptied itself on a re-issue
        # while the write-back ran anyway. One gate says it once.
        if first_issuance:
            _write_back_verification(deps, verification_result, llm_response, log_lookup)

        # --- Step 9: publish (optional hooks) ---
        if deps.publisher is not None:
            deps.publisher.publish(log_entry)
            published = True
        # FIRST ISSUANCE ONLY, preserving what the two paths did: the old
        # refresh never emailed. This is the pipeline's own mail path, which
        # is not wired in production — forecast.yml sets no Gmail
        # credentials — and is NOT the Apps Script mailer, which polls the
        # published data on its own trigger and already mails every issuance.
        # ROADMAP item 104 carries making this configurable.
        if first_issuance and deps.email_sender is not None:
            deps.email_sender.send(log_entry)
            emailed = True

    return ForecastRunResult(
        today=today,
        log_entry=log_entry,
        published=published,
        first_issuance=first_issuance,
        # None on a later issuance, meaning this run did not do that work —
        # see ForecastRunResult on why that is not an empty list.
        updated_track_record=(
            verification_result.updated_track_record if first_issuance else None
        ),
        newly_verified=verification_result.newly_verified if first_issuance else None,
        emailed=emailed,
    )


def run_forecast(
    deps: PipelineDeps,
    today: date | None = None,
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> ForecastRunResult | ForecastSkipped | ObservationsRefreshed:
    """The day's forecast, whichever run of the day this is.

    One verb, because the operator was picking between two by time of day and
    that is the wrong axis. The real distinction has nothing to do with the
    clock:

      - The FIRST run of a day owns verification and the day's
        model_predictions — the numbers tomorrow scores.
      - EVERY later run is an update: narrative only, predictions preserved.

    THIS PARAGRAPH USED TO SAY THE OPPOSITE, and the correction is the
    finding. It read: "A dispatcher, not a rewrite. run_daily_pipeline and
    run_refresh_pipeline keep their own bodies, because the two have
    genuinely different responsibilities and merging them would lose the
    invariant that makes the accuracy record trustworthy."

    Merging them is what SECURED that invariant. The write-once rules now
    live in one list inside `_compose_log_entry` instead of being written
    twice and drifting — which they did, measurably, in both directions: six
    fields disagreed between the two re-issue paths, plus a ground-AQI merge
    one path skipped and an archive key that destroyed the morning's stored
    prompt. Two bodies of code cannot be held in step by intent.

    So this resolves the day once — is there an entry already? — and
    `_issue_forecast` branches on that answer, rather than on which function
    a caller happened to reach for.

    Repeat triggers are skipped here rather than in a workflow condition. A
    YAML `if:` and a crontab line are not code this repo can test, and both
    are the operator's to get wrong; a caller must be able to invoke this
    thing with any combination of flags and be unable to corrupt the record —
    the worst it should achieve is a wasted API call, and the skip means it
    does not even achieve that. `force` overrides the skip and item 121's
    reasoning gate, and nothing else: since the write-once guard it cannot
    reach the scored numbers.

    THAT IT OVERRIDES THE GATE IS THE FLAG'S DOCUMENTED PROMISE. Its help has
    always read "Forces the NARRATIVE only", and the gate can decline to
    write one. An operator who types --force and gets no narrative, because
    no new model cycle happens to have landed, has been told something untrue
    by the CLI — so the flag reaches past the policy the same way it reaches
    past the interval.
    """
    location = deps.location
    today = today or today_in_tz(location.timezone)
    existing_entry = log_store.read_log_entry(deps.data_dir, today)

    if existing_entry is None:
        return _issue_forecast(deps, today, None, dry_run, force=force)

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

    return _issue_forecast(deps, today, existing_entry, dry_run, force=force)
