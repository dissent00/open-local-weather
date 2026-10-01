"""The floor: the write-up code writes — ROADMAP item 190.

WHY A FLOOR. From 2026-09-24 the page showed a 385-character "Write-up
unavailable" notice under tiles that already said most of what a bulletin
would, on every day the model refused. Of the 61 sentences in the best
write-up on the record (2026-09-23), 25 restate a composed phrase or a
stored field and 32 follow from stored fields by a rule. This writes those
sentences from the stored entry, in fixed frames, so no day shows a notice
where a forecast should be and a deployment with no model has prose.

FROM THE STORED ENTRY, AND NOTHING ELSE. Every input is a field the run
already writes, so `olw floor --date` renders for a stored day exactly what
the run would have published — which is what the reading gate reads — and
the app can render the same text from the same fields. A phrase the run
does not store is not in the floor: the three-day trend sentence waits for
the run to store it.

ONLY PHRASES THAT EXIST VERBATIM, joined by fixed frames, every sentence
through `phrase_defect` (item 158). A sentence that fails the shape check
is dropped, as the pipeline drops a malformed phrase: publishing nothing
beats publishing punctuation.

WHAT IT CANNOT SAY: why the models disagree. That is item 195's job.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from openlocalweather.code_blend import blend_inputs
from openlocalweather.comparison import describe_day_rain
from openlocalweather.daypart import onset_word
from openlocalweather.defaults import BLEND_MODEL_ID, CODE_BLEND_MODEL_ID
from openlocalweather.instability import convective_tier
from openlocalweather.models import DailyLogEntry, ModelPrediction
from openlocalweather.phrasing import phrase_defect
from openlocalweather.scales import aqi_band, uv_band
from openlocalweather.tiles import KMH_PER_KNOT
from openlocalweather.verify.scoring import mean, scored_predictions

# Who wrote `narrative_markdown` — stored as `narrative_source` so the record
# never mistakes code text for model text.
NARRATIVE_SOURCE_CODE = "code"
NARRATIVE_SOURCE_LLM = "llm"

# The sign-off says what the reader is looking at and whether more is
# coming. On a deployment with no model nothing follows, and saying so would
# be a promise.
SIGN_OFF_WITH_MODEL = "Written by code; a discussion follows when a model answers."
SIGN_OFF_WITHOUT_MODEL = "Written by code."

TODAY_HEADING = "## Today's Forecast"
EXTENDED_HEADING = "## Extended Outlook"
BOATERS_HEADING = "## {name} — Conditions for Boaters"

# The leads the record scores beyond today (item 72's minimal shape).
EXTENDED_LEADS = (3, 7)
# Highs are shown as a range when the models spread more than this.
HIGH_RANGE_SPREAD_C = 2.0

# The anchors' words, as the tiles name them, in the positions a sentence
# puts them.
_ANCHOR_WHEN = {"early": "early", "midday": "at midday", "evening": "in the evening"}
_STORM_GUSTS = (
    "Any thunderstorm brings sudden gusts well above this figure; "
    "the water is unsafe while one is nearby."
)


def compose_floor(
    entry: DailyLogEntry,
    *,
    secondary_name: str | None = None,
    model_configured: bool = True,
) -> str:
    """The floor for one stored day, as Markdown with the write-up's headings.

    `secondary_name` names the second point's section and withholds it when
    None; `model_configured` picks the sign-off.
    """
    sections = []

    today = _sentences(_today_parts(entry))
    if today:
        sections.append(f"{TODAY_HEADING}\n\n{today}")

    extended = _sentences(_extended_parts(entry))
    if extended:
        sections.append(f"{EXTENDED_HEADING}\n\n{extended}")

    boaters = _sentences(_boaters_parts(entry)) if secondary_name else ""
    if boaters:
        sections.append(f"{BOATERS_HEADING.format(name=secondary_name)}\n\n{boaters}")

    sections.append(_sentences(_sign_off_parts(entry, model_configured)))

    return "\n\n".join(sections) + "\n"


def _sentences(parts: list[str | None]) -> str:
    """The present, well-shaped parts as one paragraph."""
    return " ".join(p for p in parts if p is not None and phrase_defect(p) is None)


def _sentence(text: str | None) -> str | None:
    """A composed phrase as a sentence: capitalised, full stop."""
    if not text:
        return None
    text = text.strip()
    text = text[0].upper() + text[1:]
    return text if text.endswith(".") else text + "."


# --- Today's Forecast ---


def _today_parts(entry: DailyLogEntry) -> list[str | None]:
    return [
        _opener(entry),
        _sentence(entry.temp_high_low_display),
        _sky(entry.cloud_anchors),
        _wind(entry.wind_anchors, entry.peak_wind_primary_kmh),
        _uv(entry.uv_index),
        _air_quality(entry),
    ]


def _opener(entry: DailyLogEntry) -> str | None:
    """The day-over-day comparison, as the run composed it; or the rain
    character of the day from the served call when there was no yesterday
    to compare against — a fork's first day, or a store that keeps no
    actuals."""
    rows = entry.prediction_rows[0] if entry.prediction_rows else None
    comparison = rows.day_over_day if rows is not None else None
    if comparison is not None and comparison.overview_comparison:
        return _sentence(comparison.overview_comparison)

    served = _served_today(entry)
    return _sentence(
        describe_day_rain(
            served.get("precip_mm"),
            served.get("onset_hour"),
            _thunder_tier(entry) is not None,
            issued_hour=_issued_hour(entry),
            onset_word=_onset_word(entry, served.get("onset_hour")),
        )
    )


def _sky(anchors: list[dict] | None) -> str | None:
    """"Sky mostly cloudy early, partly cloudy at midday and mostly cloudy in
    the evening." One word for the whole day when every anchor agrees."""
    present = [(a.get("when"), a.get("cover")) for a in (anchors or []) if a.get("when") in _ANCHOR_WHEN and a.get("cover")]
    if not present:
        return None

    covers = {cover for _, cover in present}
    if len(covers) == 1 and len(present) == len(_ANCHOR_WHEN):
        return f"Sky {present[0][1].lower()} through the day."

    return f"Sky {_join([f'{cover.lower()} {_ANCHOR_WHEN[when]}' for when, cover in present])}."


def _wind(anchors: list[dict] | None, gust_kmh: float | None) -> str | None:
    """"Wind NNE early, SSW at midday and WSW in the evening; gusts to 33 km/h
    (18 kt)." The turn from the anchors, the peak from the served call."""
    present = [(a.get("when"), a.get("direction")) for a in (anchors or []) if a.get("when") in _ANCHOR_WHEN and a.get("direction")]
    gusts = None if gust_kmh is None else f"gusts to {_kmh_and_kt(gust_kmh)}"

    if not present:
        return None if gusts is None else _sentence(gusts)

    directions = {d for _, d in present}
    if len(directions) == 1 and len(present) == len(_ANCHOR_WHEN):
        turn = f"Wind {present[0][1]} through the day"
    else:
        turn = f"Wind {_join([f'{direction} {_ANCHOR_WHEN[when]}' for when, direction in present])}"

    return f"{turn}; {gusts}." if gusts else f"{turn}."


def _uv(index: float | None) -> str | None:
    if index is None:
        return None
    return f"UV index {index:.1f} ({uv_band(index).lower()})."


def _air_quality(entry: DailyLogEntry) -> str | None:
    """The ground stations' worst reading where there are stations, the CAMS
    estimate where there are none — the same precedence the prompt gave."""
    readings = [r for r in (entry.ground_aqi or []) if r.aqi is not None]
    if readings:
        worst = max(readings, key=lambda r: r.aqi)
        where = f"at {worst.name}, the highest of {len(readings)} stations" if len(readings) > 1 else f"at {worst.name}"
        return f"Air quality {worst.aqi} ({aqi_band(worst.aqi).lower()}) {where}."

    index = entry.air_quality_index
    if index is None:
        return None
    return f"Air quality {index} ({aqi_band(index).lower()}), by the CAMS model."


# --- Extended Outlook ---


def _extended_parts(entry: DailyLogEntry) -> list[str | None]:
    return [_lead_sentence(entry, lead) for lead in EXTENDED_LEADS]


def _lead_sentence(entry: DailyLogEntry, lead: int) -> str | None:
    """"Saturday (Day+3): dry, 30% chance of rain; highs around 29 °C.\""""
    call = _lead_call(entry, lead)
    if call is None:
        return None
    rain, probability = call

    weekday = (entry.date + timedelta(days=lead)).strftime("%A")
    if rain:
        words = "rain likely" if probability is None else f"rain likely, {probability}% chance"
    else:
        words = "dry" if probability is None else f"dry, {probability}% chance of rain"

    highs = _highs(scored_predictions(entry).for_lead(lead))
    return f"{weekday} (Day+{lead}): {words}{'; ' + highs if highs else ''}."


def _lead_call(entry: DailyLogEntry, lead: int) -> tuple[bool, int | None] | None:
    """The served call at a lead: the stored call where the entry carries
    one (item 189), else the code blend's row, else the model's own row,
    else the inputs' equal-weight vote with a dry tie."""
    for prop in (entry.served_call or {}).get("extended_properties") or []:
        if prop.get("lead_time_days") == lead and prop.get("rain") is not None:
            return bool(prop["rain"]), prop.get("rain_probability_pct")

    rows = scored_predictions(entry).for_lead(lead)
    for model in (CODE_BLEND_MODEL_ID, BLEND_MODEL_ID):
        row = next((p for p in rows if p.model == model and p.rain is not None), None)
        if row is not None:
            return row.rain, row.rain_probability_pct

    votes = [p for p in rows if p.model in blend_inputs() and p.rain is not None]
    if not votes:
        return None
    wet = sum(1 for p in votes if p.rain)
    return wet * 2 > len(votes), round(100 * wet / len(votes))


def _highs(rows: list[ModelPrediction]) -> str | None:
    highs = [p.high_c for p in rows if p.model in blend_inputs() and p.high_c is not None]
    if not highs:
        return None
    if max(highs) - min(highs) > HIGH_RANGE_SPREAD_C:
        return f"highs {min(highs):.0f} to {max(highs):.0f} °C"
    return f"highs around {mean(highs):.0f} °C"


# --- Conditions for Boaters ---


def _boaters_parts(entry: DailyLogEntry) -> list[str | None]:
    gust = entry.peak_wind_secondary_kmh
    if gust is None:
        return []
    return [
        f"Peak gust {_kmh_and_kt(gust)}.",
        _STORM_GUSTS if _thunder_tier(entry) is not None else None,
    ]


# --- The sign-off ---


def _sign_off_parts(entry: DailyLogEntry, model_configured: bool) -> list[str | None]:
    issued = entry.meta.issued_local_time
    return [
        f"Figures issued {issued}." if issued else None,
        SIGN_OFF_WITH_MODEL if model_configured else SIGN_OFF_WITHOUT_MODEL,
    ]


# --- Shared ---


def _served_today(entry: DailyLogEntry) -> dict:
    return (entry.served_call or {}).get("today_properties") or {}


def _thunder_tier(entry: DailyLogEntry) -> str | None:
    """The thunder word from the Day+0 rows' CAPE maxima, the pipeline's own
    rule (`convective_tier`) over the models it measured it on."""
    rows = scored_predictions(entry).day0
    return convective_tier([p.peak_cape_jkg for p in rows if p.model in blend_inputs()])


def _issued_hour(entry: DailyLogEntry) -> int | None:
    issued = entry.meta.issued_local_time
    try:
        return int(issued.split(":")[0]) if issued else None
    except ValueError:
        return None


def _onset_word(entry: DailyLogEntry, onset: str | None) -> str | None:
    """The onset placed by the sun, as `pipeline._onset_word_for` places it,
    from the sun times and the issue time the entry stores."""
    now = _clock(entry, entry.meta.issued_local_time)
    sunrise = _clock(entry, entry.sunrise)
    sunset = _clock(entry, entry.sunset)
    if now is None or sunrise is None or sunset is None:
        return None
    return onset_word(onset, now=now, sunrise=sunrise, sunset=sunset)


def _clock(entry: DailyLogEntry, hhmm: str | None) -> datetime | None:
    try:
        hour, minute = (int(part) for part in hhmm.split(":")[:2])
    except (AttributeError, ValueError):
        return None
    return datetime(entry.date.year, entry.date.month, entry.date.day, hour, minute)


def _kmh_and_kt(kmh: float) -> str:
    return f"{kmh:.0f} km/h ({kmh / KMH_PER_KNOT:.0f} kt)"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"
