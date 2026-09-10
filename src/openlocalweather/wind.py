"""Wind direction, which is the one quantity here that cannot be averaged.

ROADMAP items 59 and 95. Every other figure this project blends — the day's
high, the peak gust, cloud cover, CAPE — lives on a number line, and a mean
of them means something. A compass bearing does not: 350 and 10 degrees are
twenty degrees apart and both nearly north, and their arithmetic mean is 180,
due SOUTH of both. Raised by the operator on 2026-09-10 in exactly those
terms — "what I don't want is a north wind in model A averaged with a south
wind in model B to become east or west or some nonsense".

So directions are combined as UNIT VECTORS. The resultant's angle is the
consensus bearing, and its LENGTH is how much the models agree: 1.0 is
identical, 0.0 is a set that cancels out and genuinely has no mean direction.
That length is not a bolt-on confidence score — it falls out of the same
arithmetic, and it is the reason this approach cannot produce the operator's
nonsense case. Two opposing winds sum to nothing, and nothing has no bearing.
"""

from __future__ import annotations

import math

from openlocalweather.defaults import (
    WIND_DIRECTION_AGREEMENT_GATE,
    WIND_DIRECTION_MIN_MODELS,
)

# The 16-point rose, in order from north. Chosen over the 8-point compass the
# prompt's formatting rule names because the models routinely sit one point
# apart — a 2026-09-10 midday consensus of 214 degrees is SW on this rose and
# would round to either S or SW on an 8-point one, which is a coarser answer
# than the agreement supports.
COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)
_POINT_WIDTH_DEG = 360.0 / len(COMPASS_POINTS)


def compass_point(degrees: float) -> str:
    """The rose point a bearing falls in. 0 is north, 90 east."""
    return COMPASS_POINTS[int(degrees % 360 / _POINT_WIDTH_DEG + 0.5) % len(COMPASS_POINTS)]


def vector_mean(degrees: list[float]) -> tuple[float, float] | None:
    """(bearing, agreement) for a set of compass directions, or None if empty.

    Agreement is the resultant length, 0.0 to 1.0. When it is near zero the
    bearing returned is arbitrary — atan2 of two near-zero components — which
    is precisely why callers must gate on the agreement rather than trust the
    angle. `consensus_direction` is that gate; prefer it.
    """
    if not degrees:
        return None

    x = sum(math.cos(math.radians(d)) for d in degrees) / len(degrees)
    y = sum(math.sin(math.radians(d)) for d in degrees) / len(degrees)

    # NORMALISED TO [0, 360), and the guard is not decorative. For a due-north
    # set atan2 returns a hair BELOW zero, and a tiny negative modulo 360 is
    # 360.0 exactly once the subtraction rounds — so the bearing for north
    # came back as 360.0 rather than 0.0. Both are north and compass_point
    # takes either, but a raw bearing outside its own stated range is a trap
    # for the next caller, and for the Dart port which must round identically.
    bearing = math.degrees(math.atan2(y, x)) % 360.0
    if bearing >= 360.0:
        bearing = 0.0

    return bearing, math.hypot(x, y)


def consensus_direction(
    degrees: list[float],
    gate: float = WIND_DIRECTION_AGREEMENT_GATE,
) -> str | None:
    """One rose point the models actually share, or None.

    None is the common and correct answer in the evening here: measured over
    seven days of archived guidance on 2026-09-10, agreement runs 0.95 at
    midday while the lake breeze is driven and 0.48 at 19:00 as it collapses.
    A cardinal named at 19:00 is a bearing no model holds, stated in the
    section a reader acts on.
    """
    if len(degrees) < WIND_DIRECTION_MIN_MODELS:
        return None

    mean = vector_mean(degrees)
    if mean is None:
        return None

    bearing, agreement = mean
    return compass_point(bearing) if agreement >= gate else None


# The hours the day's shape is sampled at, and the words for them.
#
# THREE POINTS, NOT TWENTY-FOUR. A phrase naming every hour is a table, and
# the reader wants the shape. These three were chosen from the measurement on
# 2026-09-10: 03:00 and 12:00 are the two hours the models agree on most
# strongly here (0.90 and 0.95), and they sit either side of the turn, while
# 18:00 catches the decay when it is agreed and drops out when it is not.
#
# 09:00 is deliberately NOT sampled. It is the mid-morning transition, where
# agreement falls to 0.71 because the models are placing the same turn at
# different hours — so the one hour guaranteed to be contested is the one
# hour not asked about.
SHIFT_ANCHORS = ((3, "overnight"), (12, "by midday"), (18, "into the evening"))

# "northeasterly", not "NE", for the opening clause. The prompt's formatting
# rule wants a cardinal beside a speed; this is prose describing a day, and
# "northeasterly overnight" reads where "NE overnight" is a data point.
_ADJECTIVE = {
    "N": "northerly", "NNE": "north-northeasterly", "NE": "northeasterly",
    "ENE": "east-northeasterly", "E": "easterly", "ESE": "east-southeasterly",
    "SE": "southeasterly", "SSE": "south-southeasterly", "S": "southerly",
    "SSW": "south-southwesterly", "SW": "southwesterly", "WSW": "west-southwesterly",
    "W": "westerly", "WNW": "west-northwesterly", "NW": "northwesterly",
    "NNW": "north-northwesterly",
}
_PLAIN = {
    "N": "north", "NNE": "north-northeast", "NE": "northeast", "ENE": "east-northeast",
    "E": "east", "ESE": "east-southeast", "SE": "southeast", "SSE": "south-southeast",
    "S": "south", "SSW": "south-southwest", "SW": "southwest", "WSW": "west-southwest",
    "W": "west", "WNW": "west-northwest", "NW": "northwest", "NNW": "north-northwest",
}


def _directions_at(hourly: dict, models: list[str], hour: int) -> list[float]:
    hours = hourly.get("hourly") or {}
    times = hours.get("time") or []
    idx = next((i for i, t in enumerate(times) if _hour_of(t) == hour), None)
    if idx is None:
        return []

    out = []
    for model in models:
        series = hours.get(f"wind_direction_10m_{model}") or hours.get("wind_direction_10m") or []
        if idx < len(series) and series[idx] is not None:
            out.append(float(series[idx]))
    return out


def _hour_of(stamp: str) -> int | None:
    # "2026-09-10T15:00" — the same shape every other reader of this payload
    # assumes, and None rather than a raise if the API ever changes it.
    try:
        return int(stamp.split("T")[1][:2])
    except (IndexError, ValueError):
        return None


def describe_wind_shift(hourly: dict, models: list[str]) -> str | None:
    """One finished clause for how the wind turns through the day, or None.

    ROADMAP item 59, and the operator's question on 2026-09-10: "winds shift
    during the day, is this all accounted for?" It was not — nothing extracted
    direction at all — and the shift turned out to be the best-supported wind
    fact this location has.

    Measured over seven days of archived guidance: the models' agreement on a
    single bearing swings from 0.95 at midday to 0.48 at 19:00, but the DAYS
    agree with each other at 0.98-0.99. Lake Victoria runs a land breeze
    overnight and a lake breeze from midday, and that is the same day every
    day. Asked for one direction the models argue; asked which way it turns,
    they do not.

    A FINISHED CLAUSE, lowercase and unpunctuated, for the same reason
    describe_extended_trend ships one: the prompt is told to use it verbatim,
    so anything left for the model to phrase is something it can get wrong.

    None when fewer than two anchors clear the agreement gate — one bearing
    is not a shift, and none is not a calm day.
    """
    named: list[tuple[str, str]] = []
    for hour, when in SHIFT_ANCHORS:
        point = consensus_direction(_directions_at(hourly, models, hour))
        if point is not None:
            named.append((point, when))

    if len(named) < 2:
        return None

    # Nothing turned. Said rather than skipped: a steady wind all day is a
    # real planning answer, exactly as a steady temperature spell is.
    if len({point for point, _ in named}) == 1:
        return f"{_ADJECTIVE[named[0][0]]} throughout"

    # EVERY AGREED ANCHOR IS NAMED, not just the first and last. An earlier
    # version reported the ends and dropped the middle, which on this
    # location's commonest day threw away midday — the hour the models agree
    # on most strongly (0.95) and the one a boater on the Gulf is asking
    # about. The ends of a rotation are not the useful part of it.
    first, rest = named[0], named[1:]
    turns = " and ".join(f"{_PLAIN[point]} {when}" for point, when in rest)
    return f"{_ADJECTIVE[first[0]]} {first[1]}, turning {turns}"
