"""What the at-a-glance tiles carry — ROADMAP item 159, `ensemble` item 23.

THE OVERVIEW'S JOB, MOVED. The forecast's Overview is being retired because
most of it was a second telling: its rain sentence was already in the Rain and
Onset tiles and more precisely, and its three-day sentence duplicated the
Extended Outlook below it. The one thing with no other home was the
day-over-day comparison, and this is where it goes.

WHY THE SENTENCE FAILED, and the reason this is not a sentence. Across the 16
comparisons in the prompt archive `overview_comparison` returned "nothing
worth saying" ZERO times. It spoke every day, so on a quiet day it filled with
"about the same", "similar winds", "winds and cloud little changed" — a report
that there is nothing to report. A modifier that belongs to one tile has no
joining to do and can simply be absent.
"""

from __future__ import annotations

from openlocalweather.comparison import _band_label
from openlocalweather.defaults import (
    CLOUD_CHANGE_BANDS_PCT,
    TEMP_CHANGE_BANDS_C,
    WIND_CHANGE_BANDS_KMH,
)

# The dimensions a tile can carry a comparison for, and the words for each
# direction. Rain is deliberately absent — see `comparison_modifiers`.
DIMENSION_BANDS = {
    "temp": (TEMP_CHANGE_BANDS_C, "warmer", "cooler"),
    "wind": (WIND_CHANGE_BANDS_KMH, "windier", "calmer"),
    "cloud": (CLOUD_CHANGE_BANDS_PCT, "cloudier", "clearer"),
}

# How many day-to-day pairs the record must hold before a percentile means
# anything.
#
# ITEM 100's RULE, APPLIED TO ITSELF. At 30 pairs the top decile has three
# observations above it, which is thin; below that it is noise wearing a
# number. A deployment with less history says nothing rather than inventing a
# threshold, which is the same answer every other absent input here gets.
MIN_PAIRS_FOR_NOTABLE = 30

# The percentile a move must reach to be worth a tile's second line.
#
# MEASURED, 2026-09-21, over the 40 day-pairs the reference record holds.
# Three candidate rules were compared:
#
#   - the band tables' own first threshold ("is this perceptible"): something
#     spoke on 35 of 40 days, 1.6 dimensions each. Barely quieter than always.
#   - the top quartile: 24 of 40.
#   - the top decile: 11 of 40, usually one word.
#
# The bands are standards-derived and right about perceptibility — the cloud
# floor is one okta, the NWS resolution — but at this station a perceptible
# change happens most days, so perceptibility is the wrong question for a
# tile. "Is this unusual HERE" is the right one, and a percentile of the
# station's own history asks it without a number per climate: the same code
# gives a 2.2 °C bar here and a much wider one where seasons move.
NOTABLE_PERCENTILE = 0.9


def notable_moves(
    history: list[dict | object],
    *,
    percentile: float = NOTABLE_PERCENTILE,
    minimum_pairs: int = MIN_PAIRS_FOR_NOTABLE,
) -> dict[str, float]:
    """The size a day-to-day move must reach, per dimension, to be worth
    saying — read off this station's own record.

    `history` is the observed days in date order, each carrying `high_c`,
    `peak_wind_kmh` and `cloud_cover_pct`. A dimension with too few pairs is
    ABSENT from the result, and an absent gate means that dimension never
    speaks: a threshold from six days would fire on the instrument.
    """
    fields = {"temp": "high_c", "wind": "peak_wind_kmh", "cloud": "cloud_cover_pct"}
    out: dict[str, float] = {}

    for dimension, field in fields.items():
        values = [_read(day, field) for day in history]
        moves = [
            abs(b - a)
            for a, b in zip(values, values[1:])
            if a is not None and b is not None
        ]
        if len(moves) < minimum_pairs:
            continue

        moves.sort()
        out[dimension] = moves[min(int(len(moves) * percentile), len(moves) - 1)]

    return out


def _read(day, field: str) -> float | None:
    value = day.get(field) if isinstance(day, dict) else getattr(day, field, None)
    return float(value) if isinstance(value, (int, float)) else None


def comparison_modifiers(
    deltas: dict[str, float | None],
    notable: dict[str, float],
) -> dict[str, str]:
    """One short modifier per dimension that moved enough to be worth it.

    A dimension is absent from the result when it did not move, when the
    record cannot yet say what "enough" is, or when it was not measured. The
    tile renders nothing for it, which is the whole point — see this module's
    docstring for what the sentence did instead.

    THE GATE IS LOCAL AND THE WORD IS NOT. Whether to speak comes from this
    station's own distribution; the word comes from the same band tables the
    prose has always used, which are derived from standards rather than from
    a sample — the cloud boundaries are one and three oktas.

    TEMPERATURE IS A NUMBER, NOT AN ADJECTIVE, and that is a mismatch the
    combination forces. The bands call 2.2 °C "slightly", because they are
    built for a climate where six degrees is ordinary; here 2.2 is the top
    decile. "Slightly cooler" on a day the gate has just called unusual
    undercuts itself, so temperature says how many degrees and lets the
    reader judge.
    """
    said: dict[str, str] = {}

    for dimension, (bands, up, down) in DIMENSION_BANDS.items():
        delta = deltas.get(dimension)
        gate = notable.get(dimension)
        if delta is None or gate is None or abs(delta) < gate:
            continue

        if dimension == "temp":
            said[dimension] = f"{round(abs(delta))}° {up if delta > 0 else down}"
            continue

        label = _band_label(delta, bands, up, down)
        if label is not None:
            said[dimension] = label

    return said


# THE SKY, IN THE STANDARD'S OWN CATEGORIES.
#
# NWS sky condition is reported in eighths: clear at 0, few at 1-2, scattered
# at 3-4, broken at 5-7, overcast at 8. The boundaries below are the midpoints
# between those categories converted to percent — 0.5, 2.5, 4.5 and 7.5 oktas.
#
# NOTHING INVENTED AND NO LOCAL MEASUREMENT, which is the same discipline
# CLOUD_CHANGE_BANDS_PCT records: the one-okta resolution there and the
# category boundaries here come from the same standard. Item 95 is why —
# a Kisumu gust factor once got baked into a published scale.
#
# The WORDS are plain rather than the aviation abbreviations. A tile is read
# by someone deciding whether to hang washing out, not by a pilot, and "Few"
# means something different to each of them.
SKY_COVER_BANDS_PCT = (
    (6.25, "Clear"),
    (31.25, "Mostly clear"),
    (56.25, "Partly cloudy"),
    (93.75, "Mostly cloudy"),
)
OVERCAST_LABEL = "Overcast"

# What a tile calls each anchor hour, positionally paired with
# `wind.SHIFT_ANCHORS`. Separate from that tuple's own labels because those
# are prose for a clause — "overnight", "by midday" — and a tile has room for
# a word. The HOURS are shared, which is the part that matters: the sky and
# the wind must describe the same three moments or a reader comparing two
# tiles is comparing different times of day.
TILE_ANCHOR_WORDS = ("early", "midday", "evening")


def sky_word(cover_pct: float | None) -> str | None:
    """The plain word for a sky cover percentage, or None."""
    if cover_pct is None:
        return None

    for threshold, word in SKY_COVER_BANDS_PCT:
        if cover_pct < threshold:
            return word

    return OVERCAST_LABEL


def cloud_anchors(
    hourly: dict,
    models: list[str],
    *,
    issued_hour: int,
) -> list[dict[str, str]]:
    """The sky at each anchor hour, as a tile's lines, in time order.

    A DAY'S SHAPE, NOT ITS MEAN, and 2026-09-21 is the argument. The models'
    Day+0 mean that day was 45% with a 14-to-64 spread, while the day ran
    clear in the morning to overcast under afternoon convection — which is
    what the forecast's own prose said. One number for that day is true and
    useless, and it is the number a naive cloud tile would have shown.

    READ FROM THE HOURLY SERIES the narrative already reasons from, not from
    the daily block. `cloud_cover` per model is in HOURS AHEAD and is where
    that sentence came from.

    EMPTY WHEN EVERY ANCHOR IS BEHIND THE READER — ROADMAP item 118, the same
    rule `describe_wind_shift` follows and for the same reason: a tile whose
    every hour has gone describes a day the reader has finished. The test is
    "is any of it still ahead", not "drop what has passed", because the first
    anchor is behind a 06:00 run too and a morning reader still wants to know
    the day started clear.
    """
    from openlocalweather.wind import SHIFT_ANCHORS, values_at

    out: list[dict[str, str]] = []
    hours: list[int] = []

    for (hour, _), word in zip(SHIFT_ANCHORS, TILE_ANCHOR_WORDS):
        covers = values_at(hourly, models, hour, "cloud_cover")
        if not covers:
            continue

        # The models' MEAN, matching every other consensus in this project.
        # A spread is a real fact about a sky and belongs in the discussion,
        # where there is room to name which model said what.
        label = sky_word(sum(covers) / len(covers))
        if label is not None:
            out.append({"when": word, "cover": label})
            hours.append(hour)

    if not out or all(hour <= issued_hour for hour in hours):
        return []

    return out
