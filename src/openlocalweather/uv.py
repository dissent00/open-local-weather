"""The day's UV index: which day it describes, and which source said so.

ROADMAP item 161. The field was the model's to write and it was copying a
number: measured on 2026-09-22 across 28 archived issuances, only
`gfs_seamless` serves a UV index and `best_match` duplicates it value for
value on every one, while ECMWF, ICON and UKMO serve none at all. The
prompt's "this is your synthesized BLENDED call across all models" was never
true of this field — there is one source under two names.

WHAT MADE IT WORTH COMPUTING WAS NOT THE COPYING. Measured against the run
that wrote each entry, the published figure matched the source exactly on 12
of 18 days and the six differences were rounding; the BAND a reader sees
never once differed. On that evidence alone this is tidiness.

THE REASON IS THE DAY IT DESCRIBES. `daypart._horizon_for` decides what a
reader at this hour is waiting for, and today drops out of it at dusk. The
18:01 run classifies as dusk and its own prompt says "WHAT MATTERS NOW:
tonight, then tomorrow" — while the UV field went on reporting a peak that
happened around midday, six hours earlier. Over the 11 archived evening runs
the number changes on 6 under this rule and the BAND changes on 2, once from
High to Very high, which is the direction that understates a sun risk.

The operator's words for the rule: "what we're noting is just the max for
today, or the max for tomorrow if the sun has set and we're forecasting the
next day."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from openlocalweather.daypart import REST_OF_TODAY, TODAY

# WHICH SOURCE ANSWERS, IN ORDER, AND IT IS NAMED RATHER THAN DISCOVERED.
#
# `best_match` is second and not first even though it always agrees: it is
# Open-Meteo's own blend and on this quantity it is GFS under another name, so
# preferring the model that actually computed the figure keeps the record
# honest about where the number came from.
#
# A NATIONAL MET SERVICE WOULD OUTRANK BOTH — it is the body issuing the
# public sun-safety advice — and item 167 is what accepting one would take.
# This is a tuple rather than a config field on purpose: a setting with one
# possible value is speculative, and item 11's source ladder should decide
# that shape rather than this module guessing it.
UV_SOURCE_PREFERENCE = ("gfs_seamless", "best_match")


@dataclass(frozen=True)
class DayUVIndex:
    """The index, the day it describes, and who said so."""

    index: float
    target_date: date
    source: str


def _values(daily: dict, model: str) -> list:
    block = daily.get("daily") or {}
    return block.get(f"uv_index_max_{model}") or block.get("uv_index_max") or []


def day_uv_index(
    daily: dict,
    *,
    horizon: tuple[str, ...],
    today: date,
    sources: tuple[str, ...] = UV_SOURCE_PREFERENCE,
) -> DayUVIndex | None:
    """The UV index for the day the horizon is pointed at, or None.

    None rather than a guess when no configured source serves one. Measured
    over 28 archived issuances the arrays were never null and never short, so
    the absence path here is reasoned rather than observed — which is the
    argument for returning nothing instead of reaching for a neighbour.

    THE INDEX INTO THE DAILY BLOCK IS 0 OR 1 and nothing else. Index 0 is the
    issuance day, verified against the block's own `time` array rather than
    assumed; a horizon that no longer holds today means tomorrow, and there is
    no third case, because no horizon this project builds skips a day.
    """
    day_index = 0 if any(period in (TODAY, REST_OF_TODAY) for period in horizon) else 1
    target = today + timedelta(days=day_index)

    for model in sources:
        values = _values(daily, model)
        if day_index >= len(values):
            continue

        value = values[day_index]
        if value is None:
            continue

        return DayUVIndex(index=float(value), target_date=target, source=model)

    return None
