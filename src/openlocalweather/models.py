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

import re

from datetime import date, datetime

from dataclasses import dataclass

from pydantic import BaseModel, Field

LeadTime = int  # one of 0, 3, 7 — not a real enum, kept as int to match defaults.LEAD_TIMES_DAYS


@dataclass(frozen=True)
class ObservedSoFar:
    """What the station has actually reported TODAY, so far.

    Every field is three-valued and absence means absence: a station that
    reported nothing is not a station reporting agreement. That is the same
    rule every other absent input in this project follows, and the one whose
    violation cost a published forecast on 2026-08-29.

    WITHIN a populated record the distinction sharpens, and it is worth
    stating because the two look alike in JSON. `thunder=False` means the
    station reported and saw none, which is information. `thunder=None` means
    nothing was measured, which is not.

    SIX DIMENSIONS, WHICH ARE ROADMAP ITEM 104'S C9 TABLE — high and low,
    peak wind, sky, thunder, and rain with its onset. It carried two until
    2026-09-13 because it existed only to feed the contradiction check; item
    121 reports these to a reader directly, in code, so the set is now the
    one C9 specified rather than the one that check happened to need.

    PRECIPITATION AMOUNT IS ABSENT ON PURPOSE and is the one dimension C9
    withholds: a METAR reports that rain fell, never how much, and ERA5's
    same-day archive is model output rather than observation — proven in
    Ensemble's item 14 finding 6 by hours that had not happened yet. So for
    today's elapsed hours there is no instrument for it, and the honest move
    is to withhold the dimension rather than substitute one.

    MOVED HERE 2026-09-13 from `disagreement.py`, where it lived only because
    C2's contradiction check was the first thing to need it. Item 121 stores
    it on the entry, and this module is the single source of truth for the
    shape of everything committed to git as JSON — so a record that is now
    committed belongs here, and `disagreement` imports it like any other
    consumer.
    """

    precipitation: bool | None = None
    high_c: float | None = None
    low_c: float | None = None
    peak_wind_kmh: float | None = None
    # Mean cover in eighths across the day's reports so far.
    cloud_oktas: float | None = None
    thunder: bool | None = None
    # Local "HH:MM" of the first report that saw precipitation.
    precipitation_onset: str | None = None


# MOVED HERE 2026-09-14 from `comparison.py` — ROADMAP item 127, and the same
# rule that brought `ObservedSoFar` here: this module is the single source of
# truth for the shape of everything committed to git as JSON, and this
# structure is now committed.
#
# THE IMPORT DIRECTION ALSO FORCES IT. `comparison.py` imports DailyActual and
# ModelPrediction from here, so a field on `IssuancePredictions` typed as this
# class could not live there without a cycle. That is the move being made for
# a structural reason rather than a tidy one.
@dataclass
class DayOverDayComparison:
    """Pre-computed comparison of today's consensus against yesterday's
    observations. Every field is derived in code; the LLM's job is to phrase
    `summary`, not to recompute it."""

    yesterday_high_c: float | None
    yesterday_low_c: float | None
    yesterday_rain: bool | None
    # Surfaced beside the label for the same reason today_rain_expected is:
    # a raw observation in the payload is much harder for the LLM to misread
    # than a phrase alone. None means no station observation, not "no thunder".
    yesterday_thunder: bool | None
    yesterday_peak_wind_kmh: float | None
    # Exposed alongside the derived label, not just folded into it. A live
    # run read "rain again, as yesterday" and wrote "wetter conditions",
    # dropping the comparison — having both raw booleans visible makes that
    # much harder to misread than a lone phrase.
    today_rain_expected: bool | None
    today_consensus_high_c: float | None
    today_consensus_low_c: float | None
    today_consensus_peak_wind_kmh: float | None
    # The gust operand the LABEL was actually banded from — calibration.py,
    # and the reason the raw consensus above is kept beside it. The two differ
    # by the models' measured bias, and a stored comparison has to be
    # re-derivable from its own fields: a delta computed from one number and
    # stored beside another cannot be checked afterwards, which is most of
    # what the record is for. None means no model had enough verified checks
    # and the raw consensus was used.
    today_calibrated_peak_wind_kmh: float | None
    high_delta_c: float | None
    low_delta_c: float | None
    wind_delta_kmh: float | None
    high_label: str | None
    wind_label: str | None
    # Items 87, 65 and 83. The fourth measurement, and the one the operator's
    # founding objection was about: "a cloudy/rainy day with the same temps,
    # wind speed, and AQI is not 'much the same' even though 3/4 vectors may
    # be the same."
    cloud_label: str | None
    rain_contrast: str | None
    # The three labels above, composed into finished sentences — item 83.
    # This is what the PROMPT is given.
    #
    # THE SECOND HALF OF THIS COMMENT WAS FALSE and said "the labels
    # themselves stay in the record because that is what is stored and
    # scored". Checked 2026-09-14: nothing writes this dataclass to the entry
    # and no stored log carries a label. The labels stay HERE, on a structure
    # that lives for the length of a run. See PROMPT_COMPARISON_FIELDS.
    overview_comparison: str | None
    # Where yesterday's observed values were taken — item 98. Carried through
    # so comparison_for_prompt can name the source beside each boolean; the
    # comparison itself never reads it.
    provenance: dict[str, str] | None = None


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
    return f"{format_temp_c(high_c)} high, {format_temp_c(low_c)} low"


def format_temp_c(celsius: float) -> str:
    """One temperature, in both units — the half of `format_temp_high_low`
    that is about a single number.

    EXTRACTED RATHER THAN COPIED, 2026-09-13. It was a nested `both()` and
    item 121 needs the same rendering for observed temperatures. A second
    spelling of it would be a second rounding site, and rounding is this
    project's most-bitten cross-language divergence — `_roundHalfEven` in
    models.dart, `_fmt0` in synoptic.dart and the whole of rounding.dart
    exist for it, and item 88 found ten.

    The rounding reasoning belongs to the caller above and is not repeated.
    """
    return f"{round(celsius)}°C / {round(celsius * 9 / 5 + 32)}°F"


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
    # WHAT THIS PREDICTION IS ABOUT — ROADMAP item 104, C1.
    #
    # The target was implicit until 2026-09-12: a prediction sat on the
    # issuance's row and the lead time said how far forward it pointed, so
    # `target = row_date + lead`. That arithmetic is correct and stays
    # correct — but only while a day holds exactly ONE issuance, which is the
    # assumption item 104 removes. Two issuances on one day would put two
    # predictions on one row with no way to say which targeted what.
    #
    # Three-valued, like every other field added to this record: None means
    # the row predates the field, NOT that it targets nothing. Every entry
    # committed before 2026-09-12 loads that way, and `resolve_target_date`
    # is the single place that bridges the two — so the fallback can be
    # deleted in one edit once no unmarked rows remain.
    target_date: date | None = None
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
    # Day MEAN cloud cover, 0-100. Fetched in HOURLY_FORECAST_VARS since
    # before this field existed and discarded at extraction, which is the
    # third place cloud was being paid for and thrown away — items 87 and 65.
    #
    # A MEAN where wind is a max and temperature is an extreme, because the
    # question is what kind of day it was rather than what the worst hour did.
    # It is also what the observed side is, so the two compare like for like.
    cloud_cover_pct: float | None = None
    # The day's HIGHEST hourly CAPE, J/kg — items 35 and 87. A PEAK where
    # cloud is a mean, and deliberately: an afternoon that touches 2000 J/kg
    # for one hour is convective, and a mean against a calm morning hides the
    # one hour that matters. Same quantity summarize_instability shows the
    # forecaster, so the record and the prompt cannot describe different air.
    #
    # WHOLE-DAY, where summarize_instability trims to the hours ahead. Every
    # other Day+0 field is taken over the whole day, and a morning run's
    # record has to be comparable with an evening one's.
    #
    # Day+0 ONLY. CAPE is hourly and the extended leads come from the daily
    # endpoint, exactly like `onset` — a null here beyond Day+0 is "not
    # fetched", never "stable".
    #
    # Fetched in HOURLY_FORECAST_VARS from the beginning and recomputed every
    # run into a value nothing stored, so no model could ever be checked
    # against whether a storm actually arrived.
    peak_cape_jkg: float | None = None
    # The compass bearing, degrees, AT THIS MODEL'S OWN PEAK-GUST HOUR —
    # ROADMAP item 59. Paired with wind_kmh, which is that same model's own
    # day-maximum gust: a speed from one hour beside a bearing from another
    # describes a wind that never blew.
    #
    # NOT SCORED, and not averaged by any caller. Bearings are circular and an
    # arithmetic mean of them is meaningless — see wind.vector_mean, which is
    # the only thing allowed to combine these.
    #
    # None, never 0.0, when the series is absent: due north is a confident
    # bearing and no data is not.
    wind_direction_deg: float | None = None
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

    # WHAT THE STATION MEASURED, stored beside the reanalysis values and NOT
    # scored against anything — ROADMAP item 45's sequencing, which is
    # cross-check before replacement: stamp provenance, store the extra
    # readings, change nothing that is scored, and let divergence accumulate
    # for a few weeks before deciding what precedence would earn.
    #
    # SEPARATE FIELDS, never overwriting `high_c` and friends. The rule from
    # `precipitation_onset`: those are SCORED, and quietly swapping a
    # reanalysis quantity for a station one changes what every stored error
    # in the record means. Item 44 measured the two agreeing on temperature to
    # +0.43 °C mean, so for that variable precedence may well earn nothing —
    # which is exactly the kind of thing worth knowing before building it.
    #
    # None means the station filed nothing usable that day, never zero.
    #
    # NO STATION PRECIPITATION FIELD, deliberately. HKKI files p01i as 0.00 on
    # every row of a 45-day sample including an hour whose own report says
    # -RA. Storing that would put a confident "no rain" beside days it rained,
    # and a later precedence rule would have no way to tell it from a real
    # measurement. Occurrence comes from the present-weather groups instead —
    # see `precipitation` above.
    station_high_c: float | None = None
    station_low_c: float | None = None
    station_peak_wind_kmh: float | None = None

    # TWO CLOUD OBSERVATIONS, IN DIFFERENT UNITS, AND NEITHER IS THE OTHER —
    # ROADMAP items 87 and 65, which recorded "the forecast predicts
    # cloud_cover; nothing observes it" while both of these were being
    # fetched and discarded.
    #
    # cloud_cover_pct is the reanalysis daily MEAN, 0-100, and has been in
    # ARCHIVE_HOURLY_VARS all along. station_cloud_oktas is the airport's
    # daily mean in EIGHTHS, 0-8, parsed from the sky groups metar.py used to
    # throw away.
    #
    # NOT MERGED INTO ONE FIELD, unlike high_c and station_high_c: those are
    # the same quantity in the same unit from two sources, so a ladder can
    # pick between them. Percent and eighths are not, and converting one to
    # the other needs the NWS band table on both sides — that belongs with
    # the label work, not here. Storing both unmerged is the honest state:
    # two observations exist, and nothing yet claims which is right.
    cloud_cover_pct: float | None = None
    station_cloud_oktas: float | None = None

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
    # Did anything DETECT lightning on this local day — ROADMAP item 65.
    #
    # THREE-VALUED, like `thunder` and `precipitation` before it, and read the
    # same way: None means nothing was asked, False means something looked and
    # detected none. Every stored day is None today and will stay None until a
    # detection source exists, which is the honest state rather than a gap to
    # be filled with False.
    #
    # DELIBERATELY NOT IN observed_convection(). That method is an OR, so
    # every term added to it can only create wet days and can only move the
    # rain rate — item 53.1 moved every model about five points in a day by
    # adding one source. Lightning is a DIFFERENT QUESTION: "did it storm" and
    # "did it rain" have different answers, and the ledger has had one column
    # for both. Scored separately, a model that predicted thunder and got a
    # dry storm is right about thunder and wrong about rain, which is more
    # information than either verdict alone, and nobody has to rule on whether
    # a dry thunderstorm is "a wet day".
    #
    # This is also why lightning is the right variable to add first: a new
    # variable is not an OR term, so it cannot make the models look worse for
    # instrumentation reasons and needs no divergence numbers first. Every
    # other source discussed so far fails that test.
    #
    # SCORED AGAINST NOTHING, for now. Nothing forecasts lightning as a
    # committed value — CAPE is an instability index, not a call — so this
    # ships as an observation exactly as the station readings did, and earns
    # a prediction to be scored against later or never.
    lightning: bool | None = None

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

        `lightning` IS DELIBERATELY ABSENT from this OR — see its field
        comment, and ROADMAP item 65. A test asserts the omission, because
        adding it here would read as an obvious completion to anyone who
        found the field and not the reasoning.
        """
        return self.rain or bool(self.thunder) or bool(self.precipitation)


class VerificationScore(BaseModel):
    """The result of scoring one ModelPrediction against one DailyActual."""

    rain_correct: bool
    # The squared error of the model's own probability — ROADMAP item 58, and
    # LOWER IS BETTER unlike every other figure here. None when the model
    # supplied no probability, which is most stored days: the field started
    # being recorded 2026-09-03. Never a default of 0.5, which would invent a
    # hedge nobody made. Scored against the same observed_convection() truth
    # as `rain_correct`, because two columns scored against two truths would
    # not be comparable.
    rain_brier: float | None = None
    onset_error_hrs: float | None = None  # Day+0 only, only when both predicted and actual rain
    wind_error_kmh: float | None = None  # actual - predicted
    high_error_c: float | None = None  # actual - predicted
    low_error_c: float | None = None  # actual - predicted
    mslp_error_hpa: float | None = None  # actual - predicted
    # actual - predicted, in percentage points of sky covered.
    #
    # ADDED 2026-09-10 so that the sky can earn a track record. cloud_cover_pct
    # had been stored on both sides for a day — forecast and reanalysis — and
    # scored against nothing, so no model could gain or lose standing on it
    # however wrong it was. The operator asked for the Overview to come to
    # trust the better models on cloud the way it already weighs them on rain;
    # this is the row that has to exist first.
    #
    # None on most stored days, and that is the honest value: the field did
    # not exist before 2026-09-09. Zero would be a claim of perfect skill.
    cloud_error_pct: float | None = None
    # Did this model's INSTABILITY call match whether it actually thundered?
    # ROADMAP item 35. True/False/None, and three-valued for two separate
    # reasons: a model with no CAPE series made no call, and a day with no
    # station report settled nothing.
    #
    # AGAINST THUNDER, NOT RAIN. CAPE predicts thunderstorms; a day of steady
    # frontal rain with no lightning is not a hit for a model that called
    # high instability. That is why this is its own column rather than a
    # second input to `rain_correct`, which scores against
    # observed_convection() and would credit exactly that case.
    #
    # A boolean against a boolean because there is no observed CAPE to
    # subtract from. ERA5 carries a CAPE field, but agreeing with a
    # reanalysis is not skill at anticipating storms.
    #
    # Day+0 only — CAPE is hourly and the extended leads have none.
    convective_correct: bool | None = None


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
    # When this note's error-SIGN direction words were mechanically corrected,
    # and nothing else about it — ROADMAP item 92, and `tools/fix_note_signs.py`
    # for the rules.
    #
    # THE LEDGER SAYS IT WAS TOUCHED. The convention is observed minus
    # forecast, so a positive error is a model that came in UNDER; the prompt
    # says so now and did not when these were written, and 26 stored notes
    # described the direction backwards — "GFS overpredicted surface wind
    # speeds by 38.9 km/h" on a day GFS forecast 12.2 and 51.1 blew. The
    # magnitude was always right and was never altered; only the word for its
    # direction was, and only where the stored prediction and the stored
    # observation for that exact date, model, lead and field said so.
    #
    # A corrected note is still an LLM's prose about numbers that are in the
    # record anyway. This field exists so that a reader can tell a note that
    # was edited from one that was written correctly, rather than the fix
    # quietly making the record look like it was always clean.
    note_sign_corrected_on: date | None = None


class ModelPredictionsByLead(BaseModel):
    day0: list[ModelPrediction] = Field(default_factory=list)
    day3: list[ModelPrediction] = Field(default_factory=list)
    day7: list[ModelPrediction] = Field(default_factory=list)

    def for_lead(self, lead_time_days: int) -> list[ModelPrediction]:
        return {0: self.day0, 3: self.day3, 7: self.day7}[lead_time_days]


class IssuancePredictions(BaseModel):
    """One issuance's predictions, stamped with the moment that made them.

    ROADMAP item 104, contract item 4 — one row per issuance. Until this
    existed, a DAY held one set of predictions: `model_predictions` was
    written by the first run and every later issuance's numbers were
    discarded, so the record could not say what a 22:00 call had been.

    THE STAMP IS THE ISSUANCE INSTANT, NOT AN HOURS COUNT — operator's
    decision 2026-09-13. Hours-to-target is derived at read time from this
    and the prediction's `target_date`, because storing it would bake in a
    convention (hours to the target's start, its midpoint, or its end?)
    before anyone knows which an analysis wants, and because a figure that
    can only be re-derived is a figure that can be checked.

    ROWS ARE APPEND-ONLY AND ROW 0 IS IMMUTABLE. That is the write-once rule
    the accuracy record rests on, generalised: the numbers tomorrow scores
    are the ones the day's first issuance committed, and a later issuance
    adds a row rather than editing one.
    """

    issued_at: datetime
    predictions: ModelPredictionsByLead = Field(default_factory=ModelPredictionsByLead)

    # THIS ISSUANCE'S CLAIM ABOUT THE NEXT 24 HOURS — ROADMAP item 104,
    # contract item 2. Day+0 above is a CALENDAR-DAY claim, which at 06:00 is
    # already a quarter hindcast and at 22:00 nine tenths of one; this is the
    # same models over the window that actually begins when the issuance does,
    # so a 06:00 row and a 22:00 row make the same KIND of claim.
    #
    # STORED AND NOT YET SCORED, deliberately. `verify.scoring` still names
    # row 0's `predictions` as the set tomorrow scores, and nothing reads this
    # field. It accumulates first so that switching the record over is a
    # decision taken against measured days rather than against the reframe's
    # argument — ROADMAP item 100 is why that sentence is here. What it is NOT
    # is a second opinion to average with Day+0: it is the replacement, parked
    # until there is enough of it to replace anything.
    #
    # EMPTY MEANS THE WINDOW COULD NOT BE FILLED, never a quiet forecast. A
    # run whose two-day fetch failed holds only today, which at 18:00 is six
    # hours, and `extract.extract_window_predictions` declines rather than
    # publishing six hours dressed as twenty-four.
    window_predictions: list[ModelPrediction] = Field(default_factory=list)

    # THE LOCAL INSTANT THE WINDOW OPENED, floored to the hour — the exact
    # value `daypart.forward_hours` sliced the forecast at.
    #
    # Stored rather than re-derived from `issued_at`, which is UTC. Converting
    # back would need the zone AND would assume `reconcile_now` never
    # overrode the clock, and the two sides of a window score must cover the
    # same hours or the score is wrong in a way nothing reports. Recorded at
    # the source, they cannot disagree. Same reasoning as
    # `IssuanceSnapshot.issued_local_time`.
    window_opened_local: datetime | None = None

    # WHAT THE WINDOW'S CLAIM TURNED OUT TO BE WORTH, per model.
    #
    # Empty until scored, and `window_verified_at` is what says which: {} with
    # a null timestamp is "not yet", {} with a timestamp is "scored and there
    # was nothing to score" — the archive could not cover it, or the run made
    # no window claim. A row carrying scores asserts a comparison happened.
    window_scores: dict[str, VerificationScore] = Field(default_factory=dict)

    # WHEN the window was scored. None means it has not been, which for a
    # fresh row is the normal condition — the observation is not available
    # until every hour the window touches lies on a finished day, roughly 48
    # hours after a morning issuance. See verify.scoring.window_is_scorable
    # for why it is the calendar rather than the 24-hour clock.
    window_verified_at: datetime | None = None

    # WHAT THE OVERVIEW WAS HANDED — ROADMAP item 127.
    #
    # Computed every run since item 23 and, until now, thrown away: the only
    # consumer was `comparison_for_prompt`, which narrows it to four fields on
    # the way to the forecaster. Two comments in `comparison.py` said the full
    # structure was "stored and scored"; both were false and were corrected
    # when this was found.
    #
    # WHY IT IS WORTH KEEPING. Nothing could measure whether the Overview used
    # the sentence it was ordered to use verbatim, because the sentence was
    # nowhere in the record to compare the published prose against. Item 126
    # was only measurable because the published gust IS stored — that is the
    # same question one field over, and it took a live run and a spare
    # afternoon to answer it. It is also the only home for the calibration
    # audit: the raw and the calibrated gust are both computed here and both
    # discarded.
    #
    # ON THE ROW, NOT THE DAY, because the comparison is a property of the
    # ISSUANCE. Contract item 8's gate makes a 06:00 run say "today", an 18:00
    # run say nothing at all and a 20:00 run say "tomorrow", from the same
    # weather — so a per-day field would record one of three answers and lose
    # which run gave it. Being on the row also inherits append-only and row 0
    # immutable for free, which is the right rule here too.
    #
    # A SIBLING OF `predictions`, NOT A MEMBER OF IT. `verify.scoring` names
    # `row.predictions` as the set tomorrow scores; this is prose-facing and
    # must never drift into that. The firewall is the nesting.
    day_over_day: DayOverDayComparison | None = None


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
# ROADMAP items 51 and 79. The seven-day outlook failing used to abort the
# whole run: on 2026-09-09 `forecast_days=8` read-timed out three times and
# there was no forecast at all, though today's hourly guidance had already
# arrived and nothing about today was in doubt.
#
# A forecast for today without a seven-day outlook beats no forecast. TODAY'S
# HOURLY GUIDANCE IS STILL FATAL — it IS the forecast, and a run without it
# has nothing to say.
#
# THE CODE IS THE POINT, not the prose beside it. Item 51's sequence is
# reason, then count, then report: a degradation nothing can count is an
# accumulating miss nobody sees, which is the failure mode graceful
# degradation creates. The counting half already exists —
# check_recent_degradations is generic over codes, so one lost outlook prints
# and passes and the same code twice in the last 20 issuances turns
# check-health red.
DEGRADATION_EXTENDED_OUTLOOK = "extended_outlook_unavailable"
# SEPARATE FROM THE PRIMARY'S, though it is the same endpoint and the same
# outage. The prompt's "the extended guidance did not arrive" switch is
# derived from the primary code, and under a shared code the lake's outlook
# failing would suppress a perfectly good seven-day outlook for the town.
# Splitting them also lets step two threshold each source on its own.
DEGRADATION_SECONDARY_EXTENDED_OUTLOOK = "secondary_extended_outlook_unavailable"
# THE WRITE-UP FAILED AND THE FORECAST DID NOT — ROADMAP item 59 step 3.
#
# The only degradation here that is not a missing INPUT. Everything above
# names a block the prompt expected and did not get; this names the second of
# the two LLM calls failing after the first had already succeeded.
#
# It exists because the alternative is losing the scored call. The judgment
# call decides the numbers the record verifies against observations; the
# rendering call only writes them up. Merging after both returned meant a
# provider blip in the ~60s between them discarded a complete, paid-for
# forecast and left the day unscored — and a hole in the accuracy record is
# worse than a page with no prose, because the record is what every published
# figure rests on.
#
# ONE-SIDED ON PURPOSE. The judgment call failing still aborts the run:
# prose around numbers that were never decided is not a degraded forecast,
# it is an invented one.
DEGRADATION_NARRATIVE = "narrative_unavailable"


class NarrativeFinding(BaseModel):
    """Something a machine could check in the published prose, and found wrong.

    DELIBERATELY NOT A RunDegradation. That class is for a block the prompt
    expects and did not get, and its own docstring warns that widening it
    would make the field mean nothing within a week. A degradation says the
    run had less to work with; this says the run had everything and the answer
    was still false. Two different facts, and the record keeps them apart —
    the same separation "no met service configured" and "the met service did
    not answer" already get.

    THE RUN STILL PUBLISHES. Operator's call, 2026-09-13: discarding a whole
    narrative over one wrong weekday costs the reader more than the error
    does. So this is a record, not a gate, and its count is the evidence for
    whether anything stronger is ever worth buying.
    """

    # Which check found it — see claims.CLAIM_*.
    kind: str
    # The text as written, so a reader of the record can find it in the prose.
    quote: str
    # What is actually true, in a sentence.
    detail: str


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


class InformationMoved(BaseModel):
    """Whether this issuance had anything new to say — ROADMAP item 104, C2.

    RECORDED BEFORE IT IS ACTED ON, deliberately. The contract says a judgment
    call is made when the information moved, and stage 2b writes the three
    triggers down without letting them decide anything. That order exists so
    the record can show how often each fires against real weather BEFORE a
    rule spends money on them — this project has sized a threshold from
    convenient samples once (item 100) and does not intend to again.

    Three-valued throughout, and the middle value is the point: `None` means
    there was no BASIS for the comparison, which is different from a
    comparison that came back negative.
    """

    # The day's first run is itself a trigger, so the other two have nothing
    # to compare against on it.
    first_issuance_of_day: bool

    # Whether a new guidance cycle has landed since the previous issuance.
    # `None` on a first run, and on a re-issue of an entry written before this
    # was recorded — both mean "no basis", never False. Computed by
    # `_guidance_recency_payload`, which has fed the prompt with it since long
    # before it was stored.
    guidance_is_newer: bool | None = None

    # Codes from `disagreement.observation_disagreements` — what the station
    # has already seen that contradicts the standing call. Empty means nothing
    # seen contradicts it; `None` means nothing was looked at, which happens
    # when the station did not report or the lookup failed.
    observation_disagreements: list[str] | None = None


class LogEntryMeta(BaseModel):
    generated_at_utc: datetime
    llm_provider: str
    llm_model: str
    pipeline_version: str
    # WHICH system prompt produced this entry — ROADMAP item 70.
    #
    # The forecaster is (model + prompt + input set), and until this field
    # existed only the model was recorded. `pipeline_version` is the string
    # "0.1.0" and has never changed, while the prompt was edited twice on
    # 2026-09-04 alone; prompt changes are more frequent than model changes
    # and at least as capable of moving the output, so a record partitioned on
    # `llm_model` alone partitions on the slower axis.
    #
    # Three-valued on purpose, like `degradations`: None means the entry was
    # written before the field existed, and its prompt is NOT recoverable —
    # only the deploy timing in git says which one ran. A default of "" would
    # claim an identity those runs never had.
    system_prompt_sha256: str | None = None
    # HOW THE CALL ENDED, and what it spent — ROADMAP item 100.
    #
    # `meta` already answers "what produced this entry" for everything except
    # the generation itself. On 2026-09-10 a run returned HTTP 200 in 54.5s
    # and published a UV Index of 15,930 characters, and when the question
    # came — token ceiling, or a sampler that collapsed well inside it? — the
    # record could not answer. The spend ledger had the status and the
    # elapsed time, which is a different question, and the provider had
    # discarded the rest.
    #
    # Kept here rather than in the ledger because the ledger is about spend
    # and this is about the forecast: it belongs with the prompt hash, beside
    # the other half of what a later reader needs to reconstruct a run.
    #
    # All three are None on an entry written before the field existed, and
    # None also when the provider simply did not say. Neither is zero, and
    # `finish_reason` is deliberately the provider's own word rather than a
    # normalised one — "STOP", "tool_use" and "stop" mean the same thing to
    # three different APIs, and flattening them would destroy the only
    # evidence of which API answered.
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # WHICH RESPONSE SCHEMA produced this entry, and what it let the model
    # skip — ROADMAP items 59 and 102.
    #
    # `system_prompt_sha256` above records the instructions; this records the
    # shape the answer had to fit. Both move, and until now only one was
    # written down, so a field that stopped arriving could not be checked
    # against the schema that permitted it to stop. Item 102 ran into exactly
    # that and had to close the question from the operator's memory of the
    # deployment instead of from the record — which it recorded as its own
    # finding.
    #
    # `nullable_fields` is stored as well as the hash because the two answer
    # different questions and the hash alone answers neither without the
    # code that produced it. Item 77 is the precedent: recovering which
    # PROMPT ran meant sweeping flag combinations until a hash matched,
    # which worked and should not have been necessary.
    #
    # Three-valued like `degradations`: None means the entry predates the
    # field or the provider does not report one, and `[]` means the schema
    # genuinely marked nothing nullable. Those are different facts and the
    # record keeps them apart.
    #
    # Provider-specific by nature, and correctly empty for some. The Gemini
    # dialect carries an explicit `nullable` flag; the OpenAI adapter spells
    # the same thing as a type union, so a provider that has not been taught
    # to report these leaves both None rather than guessing.
    response_schema_sha256: str | None = None
    nullable_fields: list[str] | None = None
    # What the narrative asserted that a machine could check and found false —
    # see claims.py. Three-valued like `degradations` and `nullable_fields`:
    # None means the entry predates the check, and `[]` means it ran and found
    # nothing. An entry from before 2026-09-13 saying None is not a clean run;
    # it is an unchecked one, and seven false pairings are already in the
    # record behind it.
    narrative_findings: list[NarrativeFinding] | None = None
    # Whether this issuance had anything new to say — ROADMAP item 104, C2.
    # None on entries written before 2026-09-12; see InformationMoved.
    information_moved: InformationMoved | None = None
    # WHEN THE NARRATIVE WAS LAST REWRITTEN. Set by any issuance after the
    # day's first, at whatever hour it happens — `generated_at_utc` stays the
    # ORIGINAL creation time, so the audit trail keeps showing when this entry
    # first existed and this records the most recent rewrite on top of it.
    # `model_predictions`/`verification` are never touched by a later
    # issuance, only this field and the narrative/today_properties fields.
    #
    # This comment used to read "set only by an evening refresh run (see
    # pipeline.run_refresh_pipeline)". Both halves stopped being true with
    # ROADMAP item 104: that function no longer exists, and there is no
    # "evening refresh" — a run is whatever the day's business makes it, at
    # any frequency.
    #
    # NOT SET BY AN OBSERVATION-ONLY REFRESH — item 121. That path rewrites no
    # narrative, and setting this there would re-open the morning-snapshot
    # gate; see pipeline._refresh_observations_only.
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

    # WHEN THIS ISSUANCE WENT OUT, as a local "HH:MM" — ROADMAP item 121.
    #
    # Stored rather than re-derived from `last_issued_at` and the configured
    # zone, because those are not the same number. `daypart.reconcile_now`
    # OVERRIDES the system clock when the server's Date header disagrees with
    # it, so on a machine whose clock is wrong the forecaster is given the
    # server's time and `generated_at_utc` carries the machine's. Re-deriving
    # would then print one time on the page and another in the prompt, from
    # one issuance, with only a stderr line recording why.
    #
    # It sits beside `sunrise` and `sunset`, which are already persisted off
    # this same DayPart for the same reason: they are what the run actually
    # used, not what a later reader would recompute.
    #
    # None for entries written before this existed, and for a run that could
    # not read its own clock — never a guess.
    issued_local_time: str | None = None

    # WHEN THE STORED `observed_so_far` WAS READ, as a local "HH:MM".
    #
    # SEPARATE FROM `issued_local_time` BECAUSE THE TWO COME APART — ROADMAP
    # item 121's no-LLM refresh path. An observation-only update refreshes
    # what the station has seen and deliberately leaves the forecast alone,
    # so an entry can hold a narrative reasoned at 06:00 beside observations
    # read at 14:00. That is the design, not a fault: the prose is the prose
    # of the last real forecast and the observed block carries what has
    # happened since.
    #
    # A reader has to be told both, or the page reads as one moment. The page
    # stamps the observed block with THIS and the forecast with
    # `issued_local_time`; when they are equal, which is every full run, a
    # reader sees one time twice and nothing is lost.
    #
    # None for entries written before this existed and for a run that could
    # not read its own clock. `publish.pages` falls back to
    # `issued_local_time` there, which is what those entries meant.
    observations_local_time: str | None = None

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
    # THE LOCAL CLOCK THIS ISSUANCE WENT OUT AT, as "HH:MM" — the same value
    # `LogEntryMeta.issued_local_time` holds, captured here when the snapshot
    # is taken rather than derived from `generated_at_utc` later.
    #
    # Derived-later was the alternative and it is a guess: converting a stored
    # UTC instant with TODAY's configured zone assumes the deployment has
    # never moved, and `daypart.reconcile_now` can override the system clock
    # so the two are not even the same instant. Recorded at the source, they
    # cannot disagree.
    #
    # WHY IT IS NEEDED AT ALL: the archive index prints one label per issuance,
    # and without this the first issuance's label was the only one still in
    # UTC while every other reader-facing clock on the site is local. Two
    # times in two zones, side by side, cannot be ordered by a reader — the
    # same defect item 121 had to fix on the forecast page.
    #
    # None for every snapshot taken before this existed; `publish.pages` falls
    # back to the UTC stamp with its suffix, which is what those meant.
    issued_local_time: str | None = None

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

    # ONE ROW PER ISSUANCE — ROADMAP item 104, contract item 4. Append-only;
    # see IssuancePredictions. Read it through
    # `verify.scoring.resolve_prediction_rows`, never directly, so entries
    # written before this existed are handled in one place.
    prediction_rows: list[IssuancePredictions] = Field(default_factory=list)

    # WHAT THE STATION HAD SEEN AT THIS ISSUANCE — ROADMAP item 121.
    #
    # THE RECORD, NOT THE SENTENCE. Storing the composed string would put the
    # wording in the file and make it unfixable for every day already written;
    # storing the reading lets the page, the app and the prompt all render it
    # through `observed.describe_observed_so_far`, which is the one place it
    # is worded. Re-derivable is also checkable — the same reason the review
    # findings are recomputed every run rather than carried forward.
    #
    # None means the station said nothing, or this entry predates the field.
    # It is NOT a quiet day: see ObservedSoFar, and the published forecast
    # that cost on 2026-08-29.
    observed_so_far: ObservedSoFar | None = None

    # SUPERSEDED by prediction_rows, and None on every entry written since.
    #
    # None rather than an empty ModelPredictionsByLead on purpose. A reader
    # that has not been moved to `resolve_prediction_rows` fails loudly here
    # instead of quietly seeing a day with no predictions in it — which is
    # the failure mode this whole item exists to stop, and the one an empty
    # default would have reintroduced at the exact moment the field stopped
    # being written.
    #
    # Deletable, with the fallback in `resolve_prediction_rows`, once no
    # unmarked entry remains in the window any verification pass reads.
    model_predictions: ModelPredictionsByLead | None = None
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
            issued_local_time=self.meta.issued_local_time,
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

    # THE SKY — ROADMAP items 87 and 123, added 2026-09-14.
    #
    # Cloud has been scored per model since 2026-09-10 and the number reached
    # nothing that lasts: `verify.scoring` computed `cloud_err` and
    # `cloud_checks` per window, `review.py` recomputed them weekly, and this
    # class had twenty fields and not one of them was cloud. This class is
    # what `pipeline._track_record_payload` dumps into the PROMPT, so the
    # forecaster was told which model to believe about rain, wind,
    # temperature and pressure — and nothing about the sky it is asked to
    # describe. Measured the day this was added: GFS and ICON over-forecast
    # cloud by about 30 points while ECMWF sits at +3.3, so the disagreement
    # is large, one-directional per model, and exactly the kind of thing a
    # forecaster can act on.
    #
    # `actual - predicted`, like every other error here: NEGATIVE means the
    # model painted more cloud than there was.
    #
    # DAY+0 ONLY, and None at the longer leads rather than 0.0.
    # DAILY_FORECAST_VARS carries no cloud, so Day+3 and Day+7 have no sky to
    # score — the same shape as `avg_onset_error_hrs_10`, and 0.0 there would
    # read as a model that forecast the sky perfectly.
    avg_cloud_error_pct_10: float | None = None

    # KEPT APART FROM `checks_in_window_10` ON PURPOSE, and `verify.scoring`
    # already states why: cloud_cover_pct is null on every row written before
    # item 65, so a window can hold ten scored days and one with a sky. One
    # count for both would report a bias drawn from a single check as though
    # ten days agreed with it — which, while the record is this young, is
    # precisely the mistake available.
    cloud_checks_in_window_10: int = 0
    last_updated: date | None = None
    # The TARGET date whose result was last counted into all_time_checks /
    # all_time_correct. Guards those two incremental fields against being
    # counted more than once for the same real check — which happens
    # whenever the pipeline runs more than once against the same
    # yesterday: a manual workflow_dispatch, a retry after a partial
    # failure, or a second scheduled run later the same day. Distinct from
    # last_updated, which is just "when did any run last touch this row".
    last_verified_target_date: date | None = None
    # LLM-written and QUALITATIVE, which is load-bearing rather than
    # descriptive — see summary_carries_a_figure below for why a number in
    # here cannot be right for long.
    skill_profile_summary: str | None = None
    notes: str = ""


# A lead time is not a measurement. "At Day+0" is the only digit a summary is
# allowed, because it names which row it is about rather than reporting one of
# its values.
_LEAD_TIME_REFERENCE = re.compile(r"Day\+\d")


def summary_carries_a_figure(summary: str | None) -> bool:
    """Whether a stored skill summary quotes a number, and so cannot be fed
    back.

    ROADMAP item 91. THE SUMMARY'S ONLY CONSUMER IS THE NEXT RUN'S PROMPT.
    Verification runs before the LLM call, so the model sees current counts
    and writes prose that matches them — and then it is stored, the next run
    advances the counts by one cycle, and the model is shown yesterday's
    figure beside today's. It is stale by construction and always by exactly
    one day.

    Measured across the real 2026-09-08 payload: of twelve stored figures,
    eleven matched n-1 exactly and none matched the current count.
    `gfs_seamless` Day+0 read "63% all-time" against a stored 17/28, and
    17/27 is 63%.

    A cold reader could not tell which side was right and dropped every
    percentage rather than choose — the same forced private judgement item 83
    exists to remove. The qualitative half does not go stale: "highs run
    consistently too warm" is as true tomorrow as today. Only the numbers do,
    and the computed fields beside them are fresh every run.
    """
    if not summary:
        return False

    return any(ch.isdigit() for ch in _LEAD_TIME_REFERENCE.sub("", summary))


class TrackRecord(BaseModel):
    generated_at_utc: datetime
    entries: list[TrackRecordEntry] = Field(default_factory=list)

    def get(self, model: str, lead_time_days: int) -> TrackRecordEntry | None:
        for e in self.entries:
            if e.model == model and e.lead_time_days == lead_time_days:
                return e
        return None
