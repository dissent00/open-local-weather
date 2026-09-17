"""Does what we can SEE contradict what we already said?

ROADMAP item 104, C2's third trigger. A judgment call is made when the
information moved, and the operator's correction to that rule was that
"information" is not only a new model cycle: observations are truth where
models are opinion, and local sensors update on their own schedule.

WHY THIS HAS TO BE COMPUTABLE IN CODE. Judging whether an observation
contradicts a forecast is itself judgment, and judgment is the expensive call
being decided on. A test that needed the model to run would be circular, so
everything here is arithmetic over values the pipeline already holds.

THE ASYMMETRY THAT GOVERNS EVERY TEST HERE. A mid-day observation can only
ever prove a forecast too LOW, never too high:

- rain that has fallen has fallen, but a day that has not rained YET may
  still — so observed rain contradicts a dry call, and observed dryness
  contradicts nothing;
- a maximum only rises, so an observed high above the call settles it, while
  one below it merely means the day is not over.

Treating the symmetric case as a contradiction would re-forecast every dry
morning of every wet day, which is the opposite of what C2 is for.

WHAT IS DELIBERATELY ABSENT, AND WHY. There is still no wind test, and
ROADMAP item 144 CHANGED THE REASON without changing the answer.

It used to be a question of PLACE: there was one wind field, it was the
secondary point's, and the METAR station sits at the primary, so comparing
them would have reported a disagreement between two places as a disagreement
between forecast and reality. Item 144 split the field, so
`peak_wind_primary_kmh` now describes the station's own place and that
objection is gone.

What remains is QUANTITY, and it is the harder one. `observed.py` records it:
the station side is `sknt`, the max SUSTAINED wind, because METAR files a
gust group only when a gust occurs and none appeared on any of 932 rows in a
45-day sample. The forecast side is a GUST. Pairing them would read the gust
factor as weather, which is the error item 126 records withdrawing a whole
plan over. This deployment cannot observe a gust at all.

So the test is absent for the reason that a deployment whose station DOES
file gust groups would not share — see item 144 on why that is a
generalizable upgrade rather than a Kisumu workaround.
"""

from __future__ import annotations

from dataclasses import dataclass

from openlocalweather.models import (
    DeviationBands,
    LowDivergence,
    ModelPrediction,
    ObservedSoFar,
    SustainedWindGap,
)
from openlocalweather.verify.scoring import mean

# What the forecast already committed to, from the standing issuance.
#
# `rain` is the SCORED boolean, taken from the blend's own Day+0 row rather
# than from the prose — the prose may hedge and the record does not.


@dataclass(frozen=True)
class StandingCall:
    rain: bool | None = None
    temp_high_c: float | None = None
    # "HH:MM", the hour the standing call put the rain's arrival at. Separate
    # from `rain` because a call can be right about the DAY and wrong about
    # the HOUR, which is the whole case item 138 was raised on.
    onset_hour: str | None = None
    # The called overnight minimum — ROADMAP item 143. Read from the standing
    # issuance like the rest of this, and compared against what the station
    # actually recorded rather than against a later model run.
    temp_low_c: float | None = None


DISAGREEMENT_RAIN_WHILE_DRY = "rain_observed_while_dry_called"
DISAGREEMENT_HIGH_EXCEEDED = "high_already_exceeded"
DISAGREEMENT_ONSET_ALREADY_PASSED = "onset_already_passed"
DISAGREEMENT_LOW_DIVERGES = "observed_low_diverges"

# How far above the standing high an observation must sit before it counts.
#
# SIZED AGAINST TWO MEASURED QUANTITIES, not picked for roundness. The station
# reads +0.43 C against the reanalysis on average (item 44, quoted in
# fetch/metar.py), and the blend's Day+0 high error runs a few tenths. A
# margin at or below either would fire on the instrument rather than on the
# weather, and every spurious firing spends an LLM call.
#
# CONSERVATIVE AND NOT YET MEASURED. The honest threshold needs observed
# station highs through the day set against the standing call, which the
# record cannot supply until later issuances exist — the same bind C4's floor
# is in. Revisit against the record rather than tuning it against a
# convenient sample; item 100 records what that mistake cost last time.
TEMP_CONTRADICTION_MARGIN_C = 2.0

# How much earlier the observed onset must be before it counts — ROADMAP
# item 138.
#
# SIZED TO THE FORECAST'S OWN RESOLUTION, which is the one defensible basis
# available without a record to measure against. `onset_hour` is a POINT
# taken from `onset_window`, a multi-hour band; a station catching rain
# twenty minutes before the named hour is inside the window the forecast
# actually claimed, and calling that a contradiction would fire on the
# instrument rather than the weather — the same mistake
# TEMP_CONTRADICTION_MARGIN_C is written to avoid.
#
# UNMEASURED, and deliberately said so. The honest threshold needs observed
# onsets set against standing calls across the record, and item 122 records
# that the station's onset is not yet scored at all, so the data does not
# exist. One hour is conservative: it will miss a call that is 45 minutes
# late, and missing one is cheaper than re-forecasting every drizzle.
ONSET_CONTRADICTION_MARGIN_MIN = 60

# How far the station's overnight low must sit from the called one before it
# is worth a reader's attention — ROADMAP item 143.
#
# TWO WIDTHS, BECAUSE ONE NUMBER CANNOT BE RIGHT. Two degrees is nothing at
# 20 C and decisive at 2 C, where it is the difference between ice and no ice.
# A single delta would either shout on every ordinary morning or stay silent
# on the one morning the reader needed it.
#
# STEPPED, NOT INTERPOLATED, and that is a claim about what is known. A smooth
# taper would imply the shape of the relationship is understood; it is not.
# The step says only "colder than this, be more sensitive", which is the whole
# of what the operator's ice/no-ice case establishes.
#
# WHAT IS MEASURED HERE, AND WHAT IS NOT — the distinction matters, because
# the neighbouring margins in this module are unmeasured in BOTH senses and
# this one is not.
#
# Measured: the instrument floor. `observed.py` records station-minus-
# reanalysis over 40 days at -0.05 C on the LOW, against a 1.0 C band — the
# low is very nearly unbiased here, better than the +0.49 C on the high. So
# both widths clear the instrument comfortably and neither can fire on the
# thermometer rather than on the weather, which is the failure
# TEMP_CONTRADICTION_MARGIN_C is written to avoid.
#
# Not measured: how often a real gap of a given size appears, and at what
# width a reader starts being told something useful.
#
# AND THAT SECOND ONE IS NOT A NUMBER ANYONE HERE CAN FIND — ITEM 145. It
# depends on who is reading: a frost-sensitive grower and someone walking to
# work do not share a threshold, and picking one for both is what produced a
# band twelve times the expected gap. The direction is now a TUNABLE SETTING
# in `config/location.yaml`, surfaced in the app's advanced settings, not a
# better guess here. Read item 145 before retuning this constant — and note
# the hazard it turns on: this one is safe to expose because it only decides
# what is SAID, while a threshold that reaches `observation_disagreements`
# decides what is SPENT. That needs observed
# station lows set against standing calls across the record, which is exactly
# what `low_divergence` stores on every run whether or not it fires. Revisit
# the widths against that record, never against a convenient sample; item 100
# has the cost of the alternative.
# The shipped defaults now live on `models.DeviationBands`, which is what a
# deployment configures and a reader tunes — ROADMAP item 145. These names are
# kept because the vectors and several tests refer to them, and because a
# reader of this module should meet the numbers here rather than two files
# away.
LOW_DIVERGENCE_MARGIN_C = DeviationBands().low_c
LOW_DIVERGENCE_FREEZING_MARGIN_C = DeviationBands().low_freezing_c

# THE SPENDING BAND, AND IT IS NOT IN DeviationBands ON PURPOSE — item 145.
#
# `decisive` used to be `notable and near_freezing`, which meant the reporting
# band reached the spending decision: a reader tightening what they wanted to
# be told about would have started buying LLM calls, with nothing on the
# screen connecting the two. Decoupled here. This constant is the ONLY thing
# that widens or narrows spending, it is not configurable, and
# `test_tuning_the_reporting_band_can_never_change_what_is_spent` sweeps the
# reporting bands across their range to prove nothing here moves.
#
# Set to the value `decisive` effectively had before the split, so the change
# is a decoupling and not a retune. Moving it is a spending decision and
# belongs in the same review as `max_llm_calls_per_24h`.
LOW_DIVERGENCE_SPEND_MARGIN_C = 1.0

# At or below this, the tight margin applies and the divergence is treated as
# decision-grade. 4 C rather than 0 because ground frost forms while the air
# is still above freezing, so a reader deciding about ice is already exposed
# before the air reading reaches zero.
NEAR_FREEZING_C = 4.0


def low_divergence(
    standing: StandingCall,
    observed: ObservedSoFar,
    *,
    low_is_settled: bool | None,
    bands: DeviationBands | None = None,
) -> LowDivergence | None:
    """The gap between the station's overnight low and the standing call, or
    None when there is no settled comparison to make.

    THE ASYMMETRY, POINTED THE OTHER WAY. This module's header records that a
    maximum only rises, so an observed high ABOVE the call settles it. A
    minimum only FALLS, so the mirror holds: a station already BELOW the
    called low has proved the call too high at any hour, while one sitting
    ABOVE it has proved nothing until the night is over — the night can still
    get colder. `low_is_settled` is that gate and only the warmer case needs
    it.

    THREE-VALUED, and None is not False. `low_is_settled` is None when the sun
    times were unavailable, which means the caller does not KNOW whether the
    night is over. Unknown resolves to silence for the warmer case: claiming a
    divergence on a night that may still be running is the one error that puts
    a wrong number in front of a reader.
    """
    called = standing.temp_low_c
    seen = observed.low_c
    if called is None or seen is None:
        return None

    delta = seen - called

    # Colder than called is settled on its own. Warmer needs the night behind
    # it, and `low_is_settled is True` rather than truthiness because None
    # must not pass.
    if delta > 0 and low_is_settled is not True:
        return None

    bands = bands or DeviationBands()
    near_freezing = min(called, seen) <= NEAR_FREEZING_C
    band = bands.low_freezing_c if near_freezing else bands.low_c

    # TWO INDEPENDENT TESTS AGAINST TWO INDEPENDENT BANDS — item 145.
    #
    # `notable` answers "tell the reader?" and reads the configured band.
    # `decisive` answers "buy a call?" and reads a constant no configuration
    # touches. They were one expression until 2026-09-16, and that is the
    # coupling the split exists to break: `decisive` must not be able to move
    # because somebody changed what they wanted to be TOLD about.
    #
    # THE ASYMMETRIC CASE IS NOT A BUG. A reader who loosens the band far
    # enough gets `notable=False` and `decisive=True` near freezing: no
    # footnote, and the call is still bought. That is right. Re-forecasting
    # near freezing is about the forecast being wrong in a range where being
    # wrong matters, which is a fact about the WEATHER; the band is a
    # preference about being TOLD. The spend is not wasted — it buys a
    # corrected forecast rather than a sentence about an uncorrected one.
    notable = abs(delta) >= band
    decisive = near_freezing and abs(delta) >= LOW_DIVERGENCE_SPEND_MARGIN_C

    return LowDivergence(
        forecast_c=called,
        observed_c=seen,
        delta_c=delta,
        margin_c=band,
        notable=notable,
        decisive=decisive,
    )


def sustained_wind_gap(
    observed: ObservedSoFar, day0_predictions: list[ModelPrediction]
) -> SustainedWindGap | None:
    """The station's sustained maximum so far minus the models' Day+0
    sustained consensus, or None when either side is missing.

    NOT A TEST IN THIS MODULE'S SENSE. Everything else here asks whether an
    observation contradicts the standing call; this asks nothing and decides
    nothing. It exists because the pair was measured before a check was
    built on it (item 146's sweep) and the measurement said the check would
    fire on the instrument, not the weather — so the honest thing to store
    is the offset itself, until the record can say what it is.

    The consensus is the mean over the models that have a sustained value,
    the way `mean` treats every absent value in the record: not in the
    denominator. `ObservedSoFar.peak_wind_kmh` is the station's `sknt`
    maximum, which was always sustained — item 144.
    """
    observed_kmh = observed.peak_wind_kmh
    if observed_kmh is None:
        return None

    present = [p.sustained_wind_kmh for p in day0_predictions if p.sustained_wind_kmh is not None]
    consensus = mean(present)
    if consensus is None:
        return None

    return SustainedWindGap(
        consensus_kmh=consensus,
        observed_kmh=observed_kmh,
        delta_kmh=observed_kmh - consensus,
        model_count=len(present),
    )


def _minutes(hhmm: str | None) -> int | None:
    """"HH:MM" as minutes past midnight, or None if it is not that.

    PARSED RATHER THAN COMPARED AS TEXT. Both values are "HH:MM" by
    convention and string order would usually agree — but "9:00" sorts after
    "18:00", and one unpadded hour from either side would invert the test
    silently and in the direction that suppresses a real contradiction.
    """
    if not hhmm:
        return None
    parts = hhmm.split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def observation_disagreements(
    standing: StandingCall,
    observed: ObservedSoFar,
    *,
    temp_margin_c: float = TEMP_CONTRADICTION_MARGIN_C,
    onset_margin_min: int = ONSET_CONTRADICTION_MARGIN_MIN,
    low_is_settled: bool | None = None,
    bands: DeviationBands | None = None,
) -> list[str]:
    """Codes for every way the observation settles against the standing call.

    Empty means "nothing seen contradicts what we said", which is NOT the
    same as "the forecast is right" — most of the day is usually still ahead.

    Order is stable because the result is stored and compared across two
    languages, which makes it part of the contract rather than an
    implementation detail.
    """
    found: list[str] = []

    if standing.rain is False and observed.precipitation is True:
        found.append(DISAGREEMENT_RAIN_WHILE_DRY)

    if (
        standing.temp_high_c is not None
        and observed.high_c is not None
        and observed.high_c >= standing.temp_high_c + temp_margin_c
    ):
        found.append(DISAGREEMENT_HIGH_EXCEEDED)

    # THE CALL IS RIGHT ABOUT THE DAY AND WRONG ABOUT THE HOUR — item 138.
    #
    # `RAIN_WHILE_DRY` above cannot see this: it needs `rain is False`, and
    # here the forecast agreed rain was coming and put it too late. Onset is
    # scored at Day+0, so a run that ignores this is graded on the wrong
    # number AND prints "dry until 18:00" beside "rain from 14:00".
    #
    # ONE-DIRECTIONAL, like every other test here. Rain that arrived EARLIER
    # than called is settled and contradicts the hour; rain that has not
    # arrived by the called hour proves nothing, because the day is not over.
    called = _minutes(standing.onset_hour)
    seen = _minutes(observed.precipitation_onset)
    if called is not None and seen is not None and seen <= called - onset_margin_min:
        found.append(DISAGREEMENT_ONSET_ALREADY_PASSED)

    # ONLY THE DECISIVE ONES REACH THIS LIST — ROADMAP item 143.
    #
    # `reasoning.llm_should_reason` treats any code here as grounds to buy a
    # judgment call and a narrative, so membership is a SPENDING decision and
    # not a reporting one. The operator's framing splits exactly there: an
    # ordinary divergence "isn't a key item to read about in the morning
    # before going to work", while one near freezing "could mean ice or not".
    # So the gap is computed and stored on every run, and only the cold case
    # is a contradiction.
    #
    # In a deployment that never approaches freezing this can never fire, and
    # that is the correct behaviour rather than a gap: there, the divergence
    # is a footnote and footnotes do not re-forecast a day.
    # `bands` is threaded through and is DELIBERATELY UNABLE to change the
    # result — see LOW_DIVERGENCE_SPEND_MARGIN_C. It is passed only so one
    # call can serve both questions; the swept test proves it cannot move
    # this list.
    divergence = low_divergence(
        standing, observed, low_is_settled=low_is_settled, bands=bands
    )
    if divergence is not None and divergence.decisive:
        found.append(DISAGREEMENT_LOW_DIVERGES)

    return found
