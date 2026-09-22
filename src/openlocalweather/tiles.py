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


def wind_anchors(
    hourly: dict,
    models: list[str],
    *,
    issued_hour: int,
) -> list[dict[str, object]]:
    """The wind at each anchor hour, as a tile's lines, in time order.

    THE SAME ANCHORS AND THE SAME BLOCK AS THE SKY, which is the point: two
    tiles side by side must describe the same three moments or a reader
    comparing them is comparing different times of day.

    VALUES, NOT A SENTENCE. `describe_wind_shift` and
    `describe_wind_timeline` already compose prose from these hours; a tile
    needs the numbers, and re-deriving them here from a parsed clause would be
    the mistake `ensemble` item 23 names — splitting a sentence in a renderer
    is the wrong side of the seam.

    SPEEDS STAY IN KM/H whatever the reader's unit. The unit lives in the
    tile's header and the value is converted at render, which is what makes
    the setting a one-label change rather than a rebuild of every string.

    `direction` IS ABSENT MORE OFTEN THAN PRESENT and the tile drops the
    letters rather than apologising in words: measured over the prompt
    archive, a single agreed bearing existed on 3 of 18 runs. A bearing cannot
    be averaged, so this is `consensus_direction`'s gated answer and nothing
    else — see `wind.vector_mean`.

    EMPTY WHEN EVERY ANCHOR IS BEHIND THE READER — ROADMAP item 118, the rule
    both prose composers follow.
    """
    from openlocalweather.wind import (
        SHIFT_ANCHORS,
        directions_at,
        consensus_direction,
        values_at,
    )

    out: list[dict[str, object]] = []
    hours: list[int] = []

    for (hour, _), word in zip(SHIFT_ANCHORS, TILE_ANCHOR_WORDS):
        speeds = values_at(hourly, models, hour, "wind_speed_10m")
        gusts = values_at(hourly, models, hour, "wind_gusts_10m")
        if not speeds and not gusts:
            continue

        anchor: dict[str, object] = {"when": word}
        point = consensus_direction(directions_at(hourly, models, hour))
        if point is not None:
            anchor["direction"] = point
        if speeds:
            anchor["sustained_kmh"] = round(sum(speeds) / len(speeds), 1)
        if gusts:
            anchor["gust_kmh"] = round(sum(gusts) / len(gusts), 1)

        out.append(anchor)
        hours.append(hour)

    if not out or all(hour <= issued_hour for hour in hours):
        return []

    return out


# The reader's units. THE HEADER CARRIES THE UNIT, never the value, which is
# what makes this switchable: changing it rewrites one label per tile and no
# value string at all.
KMH_PER_KNOT = 1.852

# Two lines of wind, not three. `wind_anchors` samples three hours; a tile
# shows the TURN, which is the pair the shift clause itself names.
WIND_TILE_ANCHORS = 2
CLOUD_TILE_ANCHORS = 2


def _num(properties: dict, key: str) -> float | None:
    value = properties.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _str(properties: dict, key: str) -> str | None:
    value = properties.get(key)
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _anchor_list(properties: dict, key: str) -> list[dict]:
    """The anchors under `key`, or nothing.

    Anything that is not a list of maps is treated as ABSENT rather than
    coerced: a malformed block should render no tile, not half of one.
    """
    value = properties.get(key)
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, dict)]


def _line(text: str, *, primary: bool = False) -> dict:
    return {"text": text, "primary": primary}


def _wind_line(anchor: dict, *, metric: bool) -> str:
    """"early  NNE  9G15" — one anchor, in the reader's unit.

    THE DIRECTION IS DROPPED when the models share no bearing, and the speeds
    stand on their own rather than the tile apologising for what is missing.
    Measured over the prompt archive, a single agreed bearing existed on 3 of
    18 runs, so the short form is the normal one.
    """
    def shown(kmh: float) -> str:
        return str(round(kmh if metric else kmh / KMH_PER_KNOT))

    sustained = anchor.get("sustained_kmh")
    gust = anchor.get("gust_kmh")
    if sustained is not None and gust is not None:
        speed = f"{shown(sustained)}G{shown(gust)}"
    elif gust is not None:
        speed = f"G{shown(gust)}"
    elif sustained is not None:
        speed = shown(sustained)
    else:
        speed = ""

    parts = [str(anchor.get(key, "")).strip() for key in ("when", "direction")]

    # ONE SPACE, NOT TWO, and `phrase_defect` is what forced it. The app used
    # a double space as a column separator and it read well there, because
    # Flutter renders the string literally — but HTML COLLAPSES IT, so the
    # page and the email would have shown a different string from the app's,
    # out of one composer whose whole purpose is that they cannot. Visual
    # separation is the renderer's job and belongs in layout, not in
    # characters that survive on one surface out of three.
    return " ".join([p for p in parts if p] + ([speed] if speed else []))


def compose_tiles(properties: dict, *, metric: bool = True) -> list[dict]:
    """The at-a-glance tiles this record can fill, in reading order.

    ONE COMPOSER FOR THREE SURFACES — ROADMAP item 159 step 6. The app, the
    GitHub Pages forecast and the email all show these tiles, and this is the
    only place that decides which tiles exist, what order they come in and
    what each line says. Before this the app composed them in Dart, the page
    listed seven ungrouped stats in a Jinja template, and the email showed
    none at all; three surfaces, three answers, and the page's had never been
    grouped the way the operator's design asks.

    A TILE WITH NOTHING TO SAY IS ABSENT rather than showing a dash. An
    em-dash in a stat tile reads as a measured nothing, which is the mistake
    `DailyActual`'s three-valued fields exist to avoid.

    NO PRESSURE TILE — the operator's call on 2026-09-21. A 24-hour pressure
    trend is a forecaster's input, not an at-a-glance fact, and it belongs in
    the discussion the narrative already carries.

    `primary` IS THE ONLY STYLING THIS DECIDES, and it means "a reading"
    rather than "important". Paired data stays the same size: sunrise and
    sunset, UV and air quality, and the two anchors of a wind shift are two
    readings rather than a reading with a footnote. The day-over-day modifier
    is the one supporting line, and it drops.
    """
    from openlocalweather.scales import aqi_band, uv_band

    comparison = properties.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}

    def modifier(dimension: str) -> str | None:
        """The stored modifier, or nothing.

        THE STORED VALUE IS METRIC, like every number in the record. Wind and
        cloud say "windier" and "much cloudier", which carry no unit and read
        the same either way. TEMPERATURE SAYS A NUMBER OF DEGREES — "3°
        cooler" — and those are Celsius degrees, so an imperial reader would
        be shown a magnitude wrong by a factor of 1.8.

        IT IS DROPPED RATHER THAN CONVERTED because converting needs the
        delta and only the word is stored. Absent is the honest answer and
        the one the rest of this composer gives; showing it would be a
        measured claim that is false. Storing the delta beside the word, so
        an imperial reader gets "5° cooler", is item 165.
        """
        if dimension == "temp" and not metric:
            return None

        text = comparison.get(dimension)
        return str(text).strip() or None if text else None

    tiles: list[dict] = []

    high, low = _num(properties, "temp_high_c"), _num(properties, "temp_low_c")
    if high is not None and low is not None:
        def degrees(celsius: float) -> int:
            return round(celsius if metric else celsius * 9 / 5 + 32)

        tiles.append({
            "label": "High / Low",
            "unit": "°C" if metric else "°F",
            "lines": [_line(f"{degrees(high)}° / {degrees(low)}°", primary=True)]
            + ([_line(modifier("temp"))] if modifier("temp") else []),
        })

    rain = _str(properties, "rain_expected")
    if rain is not None:
        onset = _str(properties, "onset_window")
        tiles.append({
            "label": "Rain",
            "unit": None,
            "lines": [_line(rain, primary=True)]
            + ([_line(onset, primary=True)] if onset else []),
        })

    wind = _anchor_list(properties, "wind_anchors")
    if wind:
        tiles.append({
            "label": "Wind",
            "unit": "km/h" if metric else "kt",
            "lines": [
                _line(_wind_line(a, metric=metric), primary=True)
                for a in wind[:WIND_TILE_ANCHORS]
            ] + ([_line(modifier("wind"))] if modifier("wind") else []),
        })
    else:
        # THE FALLBACK IS A REAL CASE, not defensive padding: every entry
        # written before 2026-09-21 carries a peak gust and no anchors, and
        # every surface reads whatever was last published.
        gust = _num(properties, "peak_wind_primary_kmh")
        if gust is not None:
            shown = round(gust if metric else gust / KMH_PER_KNOT)
            tiles.append({
                "label": "Wind gust",
                "unit": "km/h" if metric else "kt",
                "lines": [_line(str(shown), primary=True)]
                + ([_line(modifier("wind"))] if modifier("wind") else []),
            })

    sky = _anchor_list(properties, "cloud_anchors")
    if sky:
        tiles.append({
            "label": "Cloud",
            "unit": None,
            "lines": [
                _line(f"{a.get('when', '')} {a.get('cover', '')}".strip(), primary=True)
                for a in sky[:CLOUD_TILE_ANCHORS]
            ] + ([_line(modifier("cloud"))] if modifier("cloud") else []),
        })

    uv, aqi = _num(properties, "uv_index"), _num(properties, "air_quality_index")
    if uv is not None or aqi is not None:
        from openlocalweather.models import format_index_and_band

        numbers = " / ".join(
            format_index_and_band(v, None)
            for v in (uv, aqi) if v is not None
        )
        words = " / ".join(
            w for w in (uv_band(uv) if uv is not None else None,
                        aqi_band(round(aqi)) if aqi is not None else None) if w
        )
        tiles.append({
            "label": "UV / AQI",
            "unit": None,
            "lines": [_line(numbers, primary=True)] + ([_line(words)] if words else []),
        })
    else:
        # the same fallback, for entries written before the split
        pair = [_str(properties, k) for k in ("uv_index_max", "air_quality_aqi")]
        if any(pair):
            tiles.append({
                "label": "UV / AQI",
                "unit": None,
                "lines": [_line(v, primary=True) for v in pair if v],
            })

    # BOTH OR NEITHER. In polar night there is no sunrise to report, and half
    # a pair reads as a rendering fault rather than as the honest answer.
    sunrise, sunset = _str(properties, "sunrise"), _str(properties, "sunset")
    if sunrise and sunset:
        tiles.append({
            "label": "Sun",
            "unit": None,
            "lines": [_line(sunrise, primary=True), _line(sunset, primary=True)],
        })

    return tiles
