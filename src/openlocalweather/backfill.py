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
from openlocalweather.code_blend import code_blend_predictions
from openlocalweather.defaults import CODE_BLEND_MODEL_ID, LEAD_TIMES_DAYS
from openlocalweather.models import DailyActual, DailyLogEntry
from openlocalweather.verify.scoring import LogLookup, resolve_prediction_rows

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
    rows = resolve_prediction_rows(entry)
    if not rows:
        return None

    if any(p.model in _BASELINE_IDS for p in rows[0].predictions.day0):
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

    # ROW 0, because the baselines belong beside the numbers that get
    # scored, and this is the one tool allowed to rewrite that row: it is a
    # deliberate historical repair, not a run. Later rows are untouched — a
    # baseline is a property of what could be seen at the DAY's first
    # issuance, so re-deriving it per issuance would be a different claim.
    #
    # Writing rows here also migrates an entry that predates contract item 4,
    # which is fine and is why the bridge is read rather than the field.
    predictions = rows[0].predictions
    return entry.model_copy(
        update={
            "model_predictions": None,
            "prediction_rows": [
                rows[0].model_copy(
                    update={
                        "predictions": predictions.model_copy(
                            update={
                                "day0": [*predictions.day0, *at_day0],
                                "day3": [*predictions.day3, *beyond],
                                "day7": [*predictions.day7, *beyond],
                            }
                        )
                    }
                ),
                *rows[1:],
            ],
        }
    )


def backfill_entry_code_blend(
    entry: DailyLogEntry,
    log_lookup: LogLookup,
    actuals: Mapping[date, DailyActual],
    inputs: list[str],
) -> DailyLogEntry | None:
    """`entry` with the code blend added to row 0, or None when there is
    nothing to add — ROADMAP item 173.

    The baselines' rules exactly: row 0 only, nothing else touched, None
    rather than an unchanged copy, and idempotent. Not hindsight for the
    reason the baselines are not: code_blend.windows_as_of reads nothing
    from the entry's own date on. What it reads is today's record of the
    days before — see code_blend.py on how that can differ from the morning's.
    """
    rows = resolve_prediction_rows(entry)
    if not rows:
        return None

    predictions = rows[0].predictions
    if any(p.model == CODE_BLEND_MODEL_ID for lead in LEAD_TIMES_DAYS for p in predictions.for_lead(lead)):
        return None

    blend = code_blend_predictions(predictions, entry.date, log_lookup, actuals, inputs)
    if not (blend.day0 or blend.day3 or blend.day7):
        return None

    return entry.model_copy(
        update={
            "model_predictions": None,
            "prediction_rows": [
                rows[0].model_copy(
                    update={
                        "predictions": predictions.model_copy(
                            update={
                                "day0": [*predictions.day0, *blend.day0],
                                "day3": [*predictions.day3, *blend.day3],
                                "day7": [*predictions.day7, *blend.day7],
                            }
                        )
                    }
                ),
                *rows[1:],
            ],
        }
    )
