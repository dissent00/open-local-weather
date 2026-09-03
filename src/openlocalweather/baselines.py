"""What a real model has to beat.

ROADMAP item 57. This project publishes figures like "ECMWF 75% rolling Day+0
rain verification" and feeds them to the forecaster as evidence about whom to
trust. Nothing beside those numbers said what they would have to beat to be
worth anything, so 75% could not be read as good, bad, or noise.

MEASURED 2026-08-31, over the 20 scored Day+0 checks then stored: persistence
scored 65% and always-dry 55%, against gfs_seamless and ukmo_seamless on 60%.
Two of the five models lost to a rule with no inputs. The lead times invert,
too — persistence falls to 35% at Day+3 and 30% at Day+7, so its INVERSE
scores 65% and 70% there. Whether that is a real oscillation in the wet/dry
sequence at this location or an artefact of twenty checks is not established,
and it is exactly the kind of local structure a flat scoreboard cannot show.

THESE ARE YARDSTICKS, NOT GUIDANCE. They are scored, stored and published
beside the numerical models, and they are withheld from the forecaster's own
context — see defaults.models_visible_to_the_forecaster, which excludes them
for a reason adjacent to the blend's. A forecaster shown "persistence: 65%"
alongside five real models would reasonably read it as a sixth opinion about
tomorrow, and it is not an opinion about anything; it is the floor the other
five have to clear.

NEITHER MAY EVER SEE THE DAY IT IS FORECASTING. Persistence reads the last
observation available when the forecast was issued, and climatology reads the
record strictly before the issuance date. A baseline that could peek would not
look broken — it would look like the models being worse than they are.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from openlocalweather.models import DailyActual, ModelPrediction

# Named like the numerical models because they sit in the same ledger, under
# the same scoring code, on the same page.
PERSISTENCE_MODEL_ID = "persistence"
CLIMATOLOGY_MODEL_ID = "climatology"


def persistence_prediction(
    last_observed: DailyActual | None, *, include_onset: bool = False
) -> ModelPrediction | None:
    """"Tomorrow will be like the last day we actually saw."

    The classical meteorological baseline, and the one that is genuinely hard
    to beat at short range. `last_observed` is the most recent observation
    available AT ISSUANCE — for a forecast issued on day D that is D-1, at
    every lead time, because the lead is a property of the target rather than
    of what the forecaster could see.

    None when there is no observation to repeat. Deliberately not a dry call:
    ModelPrediction.rain's docstring explains why manufacturing a confident
    dry prediction out of missing data accrues a flattering fake score, and
    this is the yardstick, so the distortion would move every comparison the
    page makes rather than one row of it.

    `include_onset` is False by default because the real models have no onset
    at Day+3 or Day+7 — `extract_day_n_predictions_from_daily` cannot produce
    one — and a baseline scored on a field its competitors cannot answer is
    not measuring the same thing they are.
    """
    if last_observed is None:
        return None

    return ModelPrediction(
        model=PERSISTENCE_MODEL_ID,
        rain=last_observed.rain,
        onset=last_observed.onset_hour if include_onset else None,
        precip_mm=last_observed.precip_mm,
        wind_kmh=last_observed.peak_wind_kmh,
        high_c=last_observed.high_c,
        low_c=last_observed.low_c,
        mslp_trend=last_observed.mslp_trend,
    )


def climatology_prediction(
    actuals: Mapping[date, DailyActual], *, before: date
) -> ModelPrediction | None:
    """"The usual weather here, so far as this record knows."

    The trailing base rate over every stored observation strictly before
    `before`, rather than a thirty-year normal — this deployment does not have
    one, and a fork in a new location will not either. It is honest about
    being thin and it improves on its own as the record grows, which is the
    right shape for a project whose whole premise is accumulating a local
    record.

    A TIE BREAKS DRY. At exactly 50% the call has to go somewhere, and dry is
    the direction that does not manufacture a rain expectation out of a coin
    flip. Recorded here rather than left for someone to discover in the
    arithmetic.

    Averages skip missing values instead of counting them as zero: a day whose
    high was never recorded is a day with no high, and averaging it in as 0 °C
    would drag the baseline down and flatter every model against it.

    None on an empty record, for the same reason persistence returns None.
    """
    in_scope = [a for d, a in actuals.items() if d < before]
    if not in_scope:
        return None

    wet = sum(1 for a in in_scope if a.rain)

    return ModelPrediction(
        model=CLIMATOLOGY_MODEL_ID,
        # Strictly greater than half: see the tie rule above.
        rain=wet * 2 > len(in_scope),
        # THE BASE RATE AS A PROBABILITY — ROADMAP item 58. The boolean above
        # throws away exactly the information a proper scoring rule needs, and
        # as a probability this becomes the canonical REFERENCE forecast: "the
        # usual chance of rain here", which is what a Brier skill score is
        # measured against.
        #
        # The two can disagree and that is correct. At a 40% base rate the
        # boolean says dry and this says 40 — two honest answers to two
        # different questions.
        #
        # Persistence deliberately has no equivalent. It repeats an
        # observation, and an observation is certain about the day it
        # describes while saying nothing about the chance of another one;
        # inventing 100 or 0 there would claim a confidence it never expressed
        # and would score badly under Brier for a reason that is an artefact.
        rain_probability_pct=round(100 * wet / len(in_scope)),
        # No view about WHEN, ever. Scoring it on onset would compare it
        # against the models on a question it does not answer.
        onset=None,
        precip_mm=_mean(a.precip_mm for a in in_scope),
        wind_kmh=_mean(a.peak_wind_kmh for a in in_scope),
        high_c=_mean(a.high_c for a in in_scope),
        low_c=_mean(a.low_c for a in in_scope),
        mslp_trend=None,
    )


def _mean(values) -> float | None:
    """The mean of the values that exist, or None when none do."""
    present = [v for v in values if v is not None]
    if not present:
        return None

    return sum(present) / len(present)
