"""Putting the yardsticks into days already stored.

ROADMAP item 57. Baselines are built at forecast time, so only runs from the
day they shipped carry them — and `rebuild-record` cannot help, because it
re-derives from STORED predictions and cannot invent one that was never made.
Without this, the comparison the whole item exists for becomes legible about
ten days after it ships.

THIS IS NOT HINDSIGHT, and the distinction is the reason it is allowed at
all. Both baselines are deterministic functions of data that existed at each
issuance: persistence repeats the observation for the day before the entry's
own date, climatology reads the record strictly before it. Computing them now
yields exactly what they would have produced then. Nothing here reads the day
being forecast, and a version that did would score near-perfectly and make
every real model look hopeless without anything on the page looking broken.

WHAT MAKES IT DELICATE IS NOT THE ARITHMETIC. This writes into the permanent
archive, so the rule is that it adds prediction rows and touches nothing else
— not the narrative, not the verification, not the meta, not the models' own
numbers. It is also idempotent: a second pass reports nothing to do rather
than doubling its own rows, which would corrupt every figure derived from
them while looking like unusually good baseline coverage.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

from openlocalweather.baselines import (
    CLIMATOLOGY_MODEL_ID,
    PERSISTENCE_MODEL_ID,
    climatology_prediction,
    persistence_prediction,
)
from openlocalweather.models import DailyActual, DailyLogEntry

_BASELINE_IDS = {PERSISTENCE_MODEL_ID, CLIMATOLOGY_MODEL_ID}


def backfill_entry_baselines(
    entry: DailyLogEntry, actuals: Mapping[date, DailyActual]
) -> DailyLogEntry | None:
    """`entry` with baseline predictions added at every lead, or None when
    there is nothing to add.

    None rather than an unchanged copy, so a caller can tell "already done"
    and "no observations to build from" apart from "written" without
    comparing two entries field by field. Both of those cases must leave the
    stored file untouched: rewriting a JSON file to identical content still
    churns the archive's git history for nothing.
    """
    if any(p.model in _BASELINE_IDS for p in entry.model_predictions.day0):
        return None

    issued = entry.date
    persistence = persistence_prediction(
        actuals.get(issued - timedelta(days=1)), include_onset=True
    )
    climatology = climatology_prediction(actuals, before=issued)

    at_day0 = [p for p in (persistence, climatology) if p is not None]
    if not at_day0:
        return None

    # Onset is dropped beyond Day+0 because the real models have none there,
    # and a baseline scored on a field its competitors cannot answer is not
    # measuring the same thing they are. Same rule as the live pipeline.
    beyond = [p.model_copy(update={"onset": None}) for p in at_day0]

    predictions = entry.model_predictions
    return entry.model_copy(
        update={
            "model_predictions": predictions.model_copy(
                update={
                    "day0": [*predictions.day0, *at_day0],
                    "day3": [*predictions.day3, *beyond],
                    "day7": [*predictions.day7, *beyond],
                }
            )
        }
    )
