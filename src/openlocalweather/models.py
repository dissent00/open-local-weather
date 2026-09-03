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


def format_temp_high_low(high_c: float, low_c: float) -> str:
    """The headline temperature line, in both units.

    Computed here rather than asked of the model. It used to be a string the
    LLM wrote, and it drifted in both of the ways an LLM-written number does.

    It drifted in VALUE: on 2026-08-27 a blended high of 33.5 °C was published
    as "34°C / 93°F". 33.5 °C is 92.3 °F — the model rounded to 34 first and
    converted that. The day's comparison label, computed in code, said the day
    was about the same as yesterday's observed 32.3 °C, and a reader looking at
    90 °F yesterday and 93 °F today reasonably disagreed. Roughly a third of
    that gap was invented in the rounding.

    And it drifted in FORM: the day before, the same field came out as
    "32°C / 90°F (High) | 18°C / 64°F (Low)". Two consecutive days, two
    formats, because nothing had ever fixed one.

    Each unit is rounded from the true Celsius value rather than one from the
    other, so both are the closest whole number to what was actually
    forecast. A consequence worth keeping rather than "fixing": 33.5 °C gives
    "34°C / 92°F", and 34 °C converts to 93.2 °F. The pair does not round-trip,
    because rounding twice is what caused this.

    `round` is Python's half-to-even, which is the convention this project
    already pins across the two implementations — see `_fmt0` in the Dart
    `synoptic.dart` for the matching half.
    """
    def both(celsius: float) -> str:
        return f"{round(celsius)}°C / {round(celsius * 9 / 5 + 32)}°F"

    return f"{both(high_c)} high, {both(low_c)} low"


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
    # Total precipitation for the day, millimetres. ADDITIVE and NOT SCORED —
    # `rain` above stays the boolean the accuracy record is built on, because
    # changing what that means would make every stored day incomparable with
    # every other.
    #
    # This exists because a boolean cannot tell 0.6 mm at 20:00 from 40 mm all
    # day, and the day-over-day summary was calling both "another wet day".
    # None on entries written before it was stored.
    precip_mm: float | None = None
    # The model's own chance-of-rain, percent — ROADMAP item 58, storage half.
    #
    # RECORDED BUT NOT YET SCORED, and stored ahead of anything reading it on
    # purpose. `rain` is a boolean, so a model that said "60% chance" and one
    # that said "certainly" score identically whichever way the day goes, and
    # the ledger cannot tell a confidently wrong forecast from an honestly
    # uncertain one — which is the distinction item 53's whole incident turns
    # on. Fixing that needs a proper scoring rule, and a proper scoring rule
    # needs history: it cannot be computed backwards over days whose
    # probabilities were fetched and thrown away, which is what has happened
    # on every run until now. So the clock starts here.
    #
    # None means the model gave no probability, NEVER zero — zero is a
    # confident claim that it will not rain, and the same distinction `rain`
    # above keeps for the same reason.
    rain_probability_pct: int | None = None
    wind_kmh: float | None = None
    high_c: float | None = None
    low_c: float | None = None
    mslp_trend: float | None = None


# WHICH INSTRUMENT SUPPLIED A VALUE — ROADMAP item 45, trap 2.
#
# Source identifiers, not display names: they are written into every stored
# day and a rename would make the archive incomparable with itself.
SOURCE_REANALYSIS = "era5_archive"
SOURCE_STATION = "metar_station"

# Item 45's confidence ladder. Declared, never learned — where a source sits
# follows from what the instrument physically is, which is knowable before any
# data arrives, and with no held-out truth there is nothing to fit against
# anyway.
#
# DERIVED FROM THE SOURCE, NOT STORED PER DAY. Confidence is a property of the
# instrument rather than of the weather, so storing it alongside every value
# would duplicate one fact across thousands of rows and invite the copies to
# disagree. The record stores which source answered; this says what that is
# worth.
#
# A station reporting an AMOUNT it measured would be "gold". Nothing here
# earns that yet: HKKI files 0.00 inches on every row of a 45-day sample,
# including an hour whose own report says -RA, so its amounts are a constant
# dressed as a measurement and only its present-weather groups are evidence.
_SOURCE_CONFIDENCE = {
    SOURCE_STATION: "reliable",
    SOURCE_REANALYSIS: "possible",
}


def confidence_of(source: str) -> str:
    """How much weight a value from `source` can carry.

    Unknown sources are "unknown" rather than defaulting to anything
    trustworthy: a fork adding its own sensor must not have it silently
    outrank the reanalysis, and an unrecognised id is more likely a typo than
    a gold-standard instrument.
    """
    return _SOURCE_CONFIDENCE.get(source, "unknown")


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
    # Total precipitation for the day, millimetres. ADDITIVE and NOT SCORED —
    # `rain` above stays the boolean the accuracy record is built on, because
    # changing what that means would make every stored day incomparable with
    # every other.
    #
    # This exists because a boolean cannot tell 0.6 mm at 20:00 from 40 mm all
    # day, and the day-over-day summary was calling both "another wet day".
    # None on entries written before it was stored.
    precip_mm: float | None = None
    # Did the airport observe thunder on this local day (fetch/metar.py)?
    #
    # THREE-VALUED, AND THE THIRD VALUE MATTERS. None means no observation was
    # available — no ICAO configured, the archive unreachable, or the station
    # filed nothing that day — and must never be read as "no thunder". False
    # means the station reported and saw none, which is real evidence a dry
    # call can be scored against.
    #
    # This is not a decoration on `rain`. It CHANGES what a rain forecast is
    # scored against, via observed_convection() below. Measured on 2026-08-26
    # across the 42 days then stored: 5 had an observed thunderstorm that the
    # reanalysis recorded as a dry day, and every model that called those days
    # correctly had been marked wrong for it.
    thunder: bool | None = None
    # Did the airport observe PRECIPITATION on this local day (fetch/metar.py)?
    #
    # THREE-VALUED for the same reason `thunder` is, and read the same way:
    # None is "no observation", never "it stayed dry".
    #
    # Separate from `thunder` because the two fail separately. Adding it was
    # item 53: on 2026-08-29 the station reported -RA and RERA under
    # cumulonimbus with no TS group at all, the reanalysis recorded 0.0 mm,
    # and the day scored DRY — so every model that had called it dry was
    # credited for a day it rained. `thunder` alone could not catch that.
    #
    # Measured over the 45 days then stored: precipitation observed on 9, of
    # which 2 had been scored dry by both the reanalysis and the thunder check
    # (2026-07-21, 2026-08-29). Every model's all-time Day+0 rain accuracy
    # fell about five points once they were counted.
    precipitation: bool | None = None
    # LOCAL "HH:MM" the airport first observed precipitation, or None. Kept
    # SEPARATE from `onset_hour` rather than filling it in, because
    # `onset_hour` is SCORED — verify/scoring.py measures onset error against
    # it — and quietly swapping a reanalysis quantity for a station one would
    # change what every stored onset error means. This field only ever feeds
    # the day-over-day description, via observed_onset().
    precipitation_onset: str | None = None

    # Which source supplied which value, for THIS day — ROADMAP item 45,
    # trap 2. Keys are DailyActual field names, values are SOURCE_* ids.
    #
    # THREE-VALUED, like `thunder` and `degradations` before it. `None` means
    # the day predates provenance recording and was never asked. An empty dict
    # would claim we looked and found no sources, which is never true of a
    # stored day — every one has at least a reanalysis `rain`.
    #
    # WHY IT IS THE PREREQUISITE for the rest of item 45. The station is truth
    # for most days and down for a few, and those few are scored against a
    # coarser instrument. That is acceptable only if it is visible: without
    # this, an unexplained dip in the accuracy record cannot be told apart
    # from the models getting worse. Item 53.1 moved every model about five
    # points in a day purely by adding a source, which is exactly the kind of
    # movement this exists to explain.
    #
    # Not every field appears. A key is present when something supplied a
    # value; a field the day has no observation for is simply absent, rather
    # than being stamped with a source that reported nothing.
    provenance: dict[str, str] | None = None

    def observed_onset(self) -> str | None:
        """The onset a day's CHARACTER should be described from.

        The reanalysis onset when there is one, the station's when there is
        not. A day the reanalysis recorded as 0.0 mm has no onset by
        construction, so a shower it missed entirely had no time to be
        described at — which is how 2026-08-29 reached readers as "dry"
        after 53.1 had already scored it as a wet day.

        NOT what onset error is scored against; see `precipitation_onset`.
        """
        return self.onset_hour or self.precipitation_onset

    def observed_convection(self) -> bool:
        """What a rain forecast is actually scored against.

        Reanalysis precipitation OR anything the airport actually saw fall or
        heard. A day with a thunderstorm over the city and 0.5 mm in a 25 km
        grid cell is a day the convective models called correctly, and scoring
        it as dry punishes exactly the models most worth trusting here — over
        a lake basin whose storms global models already under-resolve.

        THE NAME IS NARROWER THAN THE BEHAVIOUR, and deliberately kept:
        drizzle from stratus is not convection, but it is still rain the
        reader stood in, and it is still what a dry call should be scored
        against. Renaming would churn scoring.py, cli.py and two ROADMAP items
        to no benefit — the docstring is the definition, not the identifier.

        Both observations being None leaves this as plain `rain`, so a
        deployment with no METAR station scores exactly as it did before.
        """
        return self.rain or bool(self.thunder) or bool(self.precipitation)


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


# Stable identifiers for the degradations a run can record. Constants rather
# than literals at each site because three surfaces read them — the record,
# the page and check-health — and a typo in any one of them would silently
# stop matching rather than fail.
DEGRADATION_HOURS_AHEAD_NARROWED = "hours_ahead_narrowed"
DEGRADATION_SUN_TIMES = "sun_times_unavailable"
DEGRADATION_METAR = "metar_unavailable"
DEGRADATION_SYNOPTIC = "synoptic_unavailable"


class RunDegradation(BaseModel):
    """One block the prompt expects that arrived absent, narrowed or unread.

    ROADMAP item 53.4. On 2026-08-29 the forward hourly fetch timed out on
    three consecutive runs, and the only trace was a line on stderr inside a
    GitHub Actions log. The committed entry for a degraded run was
    byte-for-byte the same SHAPE as a clean one, so nothing downstream — the
    page, the reader, or an investigation opened a day later — could tell
    that the day's hazard block had been built on less than usual. The gap
    surfaced because a reader was rained on.

    WHAT COUNTS. A block the prompt normally receives that did not arrive, or
    arrived narrower than usual. Not: a source this deployment never
    configured. A location with no METAR station is running as configured,
    not running degraded, and recording that as a degradation would make the
    field mean nothing within a week — the same reasoning that keeps
    "no met service configured" separate from "the met service did not
    answer" in the bulletin block.

    TWO TEXTS, FOR TWO PLACES. `summary` is what a reader is shown at the top
    of the forecast: plain, no jargon, and it says what the gap MEANS rather
    than which fetch failed. `detail` is the technical account and belongs in
    the notes at the end, with whatever timing or identifiers are worth having
    there. Splitting them is not decoration — the top of a forecast is where
    somebody decides whether to go outside, and "the forward hourly window
    (forecast_days=2) did not arrive" tells that person nothing they can use.

    `code` is matched on; both texts are written for people and may be
    reworded freely.
    """

    code: str
    summary: str
    detail: str


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
    # WHICH trigger produced this run — GitHub's `github.event_name`
    # ("schedule", "workflow_dispatch"), or empty when run outside Actions.
    #
    # Recorded because the failure it detects is otherwise invisible. This
    # deployment fires from unreliable cron slots AND a dispatch call from an
    # external host, the latter existing precisely because the former missed
    # issuances. If the dispatch path dies — expired or revoked token, lost
    # token file, decommissioned host — the cron slots keep firing, commits
    # keep appearing, and the repo-staleness check (50 days with NO commit)
    # never trips. The system silently reverts to the exact unreliable mode
    # the dispatch trigger was added to fix.
    #
    # Storing it makes "the external trigger hasn't fired in N days" a query
    # against the committed record instead of an investigation. Optional, so
    # entries written before it existed still load.
    trigger_source: str | None = None

    # What this run did NOT have.
    #
    # THREE-VALUED, and the middle value is the whole point. `[]` means this
    # run looked and found nothing missing. `None` means the run was never
    # asked — every entry committed before item 53.4 loads that way, and
    # collapsing the two would make the check announce that the 2026-08-29
    # incident runs "had the data they expect". Caught by running the health
    # check against the real record rather than by a test: absence of a
    # record is not a record of absence, which is the same rule the CONVECTIVE
    # INSTABILITY block and `thunder` already follow.
    #
    # On the ISSUANCE this describes, not the day. A re-issue whose fetches
    # all succeeded is a clean issuance even when the morning's were not, so
    # the outgoing issuance's own list is snapshotted into
    # IssuanceSnapshot.degradations rather than merged into this one.
    degradations: list[RunDegradation] | None = None


class IssuanceSnapshot(BaseModel):
    """A frozen copy of DailyLogEntry's public-facing fields exactly as
    they stood right before a later run overwrote them in place.

    Real bug this fixes: run_refresh_pipeline merges a later run's fresh
    narrative_markdown/today_properties/ground_aqi/whatsapp_summary
    directly into the existing entry, so an earlier issuance's actual
    published text was silently gone from both the committed JSON and the
    rendered archive page the moment a later run landed — recoverable only
    by digging through git history for the pre-overwrite commit, not from
    anything the site or data file exposed. model_predictions/verification
    were never affected (those already survive a refresh untouched — see
    LogEntryMeta) and are deliberately NOT duplicated here; this only
    covers the fields a later run actually overwrites.

    Named for what it captures, not when: this used to hold at most one of
    these, as DailyLogEntry.morning_issuance, back when a day held at most
    two issuances — so the only snapshot ever taken was the morning's. A
    day can now hold any number, so DailyLogEntry.earlier_issuances holds
    one of these per issuance before the current one; see its doc comment,
    and DailyLogEntry.issuance_log() for the accessor that reads both.
    """

    rain_expected: str
    onset_window: str | None = None
    peak_wind_kmh: float | None = None
    temp_high_c: float
    temp_low_c: float
    temp_high_low_display: str
    mslp_trend_24h: str
    synoptic_pattern: str
    uv_index_max: str | None = None
    air_quality_aqi: str | None = None
    ground_aqi: list[GroundAQIReading] = Field(default_factory=list)
    narrative_markdown: str
    whatsapp_summary: str | None = None
    generated_at_utc: datetime

    # How old the guidance behind THIS issuance was — see DailyLogEntry's
    # fields of the same name for why these exist and what "observed" vs
    # "derived" means. None for every entry committed before this existed.
    guidance_initialised_at: datetime | None = None
    guidance_age_hours: float | None = None
    guidance_source: str | None = None

    # What the run that produced THIS issuance did not have — see
    # RunDegradation, and LogEntryMeta.degradations for why None and [] are
    # different answers. Snapshotted for the same reason the guidance fields
    # above are: a re-issue overwrites the narrative in place, and a reader
    # asking why the morning's hazard block was thin needs the morning's own
    # answer, not the evening's.
    degradations: list[RunDegradation] | None = None


class LocalBulletinRecord(BaseModel):
    """The local met service's own bulletin, stored verbatim as fetched.

    Kept for two reasons. First, auditability: this text materially shapes
    the day's synthesis, and a record that omits one of its inputs can't
    honestly claim to be reconstructible.

    Second, and the reason it can't wait: a met service's forecast is a
    PREDICTION, and predictions are not recoverable after the fact. Actuals
    can always be re-fetched from the archive, but nobody publishes what
    Kenya Met said last Tuesday once the week rolls over — their weekly PDF
    is replaced, not archived per-day. Every run that discards this text
    destroys the only chance to ever score that forecast. Storing it now
    means a scoring pass added later can be backfilled across the whole
    record instead of starting from zero on the day it ships.

    `text` is stored exactly as the fetcher returned it, including the
    explanatory "unavailable" strings — deliberately NOT normalised or
    filtered here, because whether a given bulletin is usable is a judgment
    for the extraction step, and a heuristic applied at write time would
    silently discard the evidence needed to revisit it.
    """

    source_name: str
    text: str
    fetched_at_utc: datetime


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

    # Sunrise and sunset, local, as HH:MM. Set by CODE from the day's
    # astronomical data, never by the model — they are facts, and asking a
    # language model to restate a fact is how a wrong one gets published.
    #
    # None for entries written before this was stored, and legitimately None
    # in polar night, where there is no sunrise to report. A reader at that
    # latitude is better served by the field being absent than by a fabricated
    # time.
    sunrise: str | None = None
    sunset: str | None = None

    model_predictions: ModelPredictionsByLead = Field(default_factory=ModelPredictionsByLead)
    verification: VerificationByLead = Field(default_factory=VerificationByLead)

    # The local met service's own words for this day, verbatim. None for
    # entries written before this was stored, and for locations with no
    # bulletin source configured.
    local_bulletin: LocalBulletinRecord | None = None

    yesterday_verification_summary: str | None = None
    narrative_markdown: str
    whatsapp_summary: str | None = None

    # Every issuance BEFORE the current one, oldest first. The current
    # issuance is never duplicated in here — it stays in the top-level
    # fields above, exactly as it always has. Absent/empty on every entry
    # committed before this field existed; see issuance_log() below for the
    # accessor that reads both shapes.
    earlier_issuances: list[IssuanceSnapshot] = Field(default_factory=list)

    # Set only when a later run has overwritten the fields above — the
    # pre-refresh (morning) issuance, preserved so it stays readable and
    # archived rather than silently lost. None for an entry that's never
    # been refreshed. See IssuanceSnapshot's doc comment.
    #
    # Redundant with earlier_issuances[0] once that list is non-empty, and
    # deliberately kept anyway: data/log/*.json is this project's public
    # archive, and publish/pages.py (plus any external reader) keys off
    # this field by name to build archive/<date>-morning.html. That schema
    # is allowed to grow but must never change under a reader — removing
    # this field would be exactly that change.
    morning_issuance: IssuanceSnapshot | None = None

    # How old the guidance behind THIS issuance was, resolved by
    # pipeline._resolve_guidance_cycle at the moment this issuance was
    # generated — never recomputed by a later reader, because "how old was
    # the guidance when this issuance went out" is a fact about that
    # moment, not something derivable from today's clock. guidance_source
    # says which of cycle.py's two answers produced it: "observed" (Open-
    # Meteo's own ecmwf_ifs025 meta.json — see fetch/model_run.py — once it
    # has settled) or "derived" (cycle.aligned_cycle_at's inferred floor,
    # used whenever the observation is unavailable or has not yet settled).
    # A later run archives the issuance it is about to overwrite into
    # earlier_issuances via to_issuance_snapshot(), which copies these three
    # fields too — so a re-issue keeps the FIRST issuance's own recency
    # rather than letting the later run's fresher cycle overwrite it. None
    # for every entry committed before this existed; no migration ever
    # backfills data/log/*.json to add it — same reasoning as
    # earlier_issuances above.
    guidance_initialised_at: datetime | None = None
    guidance_age_hours: float | None = None
    guidance_source: str | None = None

    meta: LogEntryMeta

    @property
    def last_issued_at(self) -> datetime:
        """When this entry last SAID something.

        Not meta.generated_at_utc, which is deliberately frozen at the day's
        first run — that field is the audit trail for when the entry came
        into being, and a later run must not move it. refreshed_at is when
        the current narrative went out; it is None until something re-issues.
        """
        return self.meta.refreshed_at or self.meta.generated_at_utc

    def to_issuance_snapshot(self) -> IssuanceSnapshot:
        """This entry's current top-level fields, frozen as an
        IssuanceSnapshot — what a later run must capture before it
        overwrites them. Moved here from pipeline.py's `_morning_snapshot`:
        it is model knowledge (which fields make up one issuance), and
        issuance_log() below needs it too.

        Stamped with last_issued_at, not generated_at_utc. The predecessor
        of this method only ever captured the day's FIRST issuance, where
        the two are the same value; generalising it to any issuance made
        that equivalence false, and stamping a 22:00 update with the 06:07
        clock is a confident wrong answer where the old code had an
        "earlier today" shrug.
        """
        return IssuanceSnapshot(
            rain_expected=self.rain_expected,
            onset_window=self.onset_window,
            peak_wind_kmh=self.peak_wind_kmh,
            temp_high_c=self.temp_high_c,
            temp_low_c=self.temp_low_c,
            temp_high_low_display=self.temp_high_low_display,
            mslp_trend_24h=self.mslp_trend_24h,
            synoptic_pattern=self.synoptic_pattern,
            uv_index_max=self.uv_index_max,
            air_quality_aqi=self.air_quality_aqi,
            ground_aqi=self.ground_aqi,
            narrative_markdown=self.narrative_markdown,
            whatsapp_summary=self.whatsapp_summary,
            generated_at_utc=self.last_issued_at,
            guidance_initialised_at=self.guidance_initialised_at,
            guidance_age_hours=self.guidance_age_hours,
            guidance_source=self.guidance_source,
            degradations=self.meta.degradations,
        )

    def issuance_log(self) -> list[IssuanceSnapshot]:
        """Every issuance of the day, oldest first, the CURRENT one last.

        Reads two shapes on purpose and never rewrites either into the
        other. An entry written after earlier_issuances existed carries it
        directly; every entry committed before that change (everything in
        data/log/ as of this change) has none, so this falls back to
        [morning_issuance] when that is set, or to nothing when the day was
        never re-issued. Either way, the current top-level fields are
        appended last.

        The archive in data/log/ is this project's record. Migrating old
        entries to carry earlier_issuances would edit history to look like
        it always had this field — which destroys the very thing an
        archive is for: a true account of what was actually stored at the
        time. Reading both shapes forever costs one small function; the
        alternative costs the archive's own honesty.
        """
        current = self.to_issuance_snapshot()
        if self.earlier_issuances:
            return [*self.earlier_issuances, current]
        if self.morning_issuance is not None:
            return [self.morning_issuance, current]
        return [current]


# ---------------------------------------------------------------------------
# Model track record — data/track_record.json
# ---------------------------------------------------------------------------


class TrackRecordEntry(BaseModel):
    """One (model, lead_time) pair's accuracy record.

    Fully recomputed and rewritten every run — including all_time_checks /
    all_time_correct, which are re-derived by walking the entire stored
    record rather than carried forward.

    They were incremental until it was shown that Open-Meteo revises recent
    observations (a day served as "rain, 29.6C" at 06:07 came back as "no
    rain, 30.5C" hours later), which meant an incremental counter kept the
    provisional verdict permanently. The original objection to deriving —
    recovering history beyond the retention window — turned out not to
    apply: LOG_RETENTION_DAYS is documented but deliberately unimplemented,
    so nothing is ever pruned from data/log/, and pipeline.py now ties the
    actuals cache's retention to the log history so the walk always has
    observations to score against.

    Nothing in this record is carried forward any more. Every field is
    derivable from the committed logs plus fetched actuals, which is what
    makes the git-as-auditable-database claim actually true.
    """

    model: str
    lead_time_days: LeadTime
    rolling_10_rain_pct: float | None = None
    rolling_30_rain_pct: float | None = None
    # Deterministic comparison of rolling_10 against rolling_30 — see
    # verify/scoring.compute_rain_pct_trend. Computed in code so the LLM is
    # handed a ready-made "is recent skill diverging from the longer-term
    # baseline" signal instead of inferring it itself from the raw numbers.
    # None/None means either window doesn't yet have enough checks for the
    # comparison to be meaningful (see defaults.TREND_MIN_CHECKS_*).
    rain_pct_trend: str | None = None  # "improving" | "declining" | "stable" | None
    rain_pct_trend_delta: float | None = None  # rolling_10_rain_pct - rolling_30_rain_pct
    all_time_checks: int = 0
    all_time_correct: int = 0
    all_time_rain_pct: float | None = None
    # How far back the all-time re-derivation actually reached. Recorded so
    # coverage is auditable from the committed record rather than assumed:
    # "80% correct" over six scattered days is not the claim it looks like
    # next to the same figure over three hundred.
    all_time_earliest_target_date: date | None = None
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
