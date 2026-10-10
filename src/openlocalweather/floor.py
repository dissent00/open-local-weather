"""The floor: the write-up code writes — ROADMAP item 190.

WHY A FLOOR. From 2026-09-24 the page showed a 385-character "Write-up
unavailable" notice under tiles that already said most of what a bulletin
would, on every day the model refused. Of the 61 sentences in the best
write-up on the record (2026-09-23), 25 restate a composed phrase or a
stored field and 32 follow from stored fields by a rule. This writes those
sentences in fixed frames, so no day shows a notice where a forecast
should be and a deployment with no model has prose.

A PURE FUNCTION OVER NAMED INPUTS, mirrored in `olw_core` and held to this
implementation by `spec/vectors/floor.json`. Every input is a value the run
already holds and the entry already stores, so `FloorInputs.from_entry`
renders for a stored day exactly what the run published — which is what
`olw floor` prints for the reading gate — and the app composes the same
text from the same values at the end of its own run.

ONLY PHRASES THAT EXIST VERBATIM, joined by fixed frames, every sentence
through `phrase_defect` (item 158). A sentence that fails the shape check
is dropped, as the pipeline drops a malformed phrase: publishing nothing
beats publishing punctuation.

WHAT IT CANNOT SAY: why the models disagree. That is item 195's job.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from openlocalweather.code_blend import blend_inputs
from openlocalweather.comparison import describe_day_rain
from openlocalweather.daypart import onset_word
from openlocalweather.defaults import BLEND_MODEL_ID, CODE_BLEND_MODEL_ID
from openlocalweather.disagreement import (
    DISAGREEMENT_GUST_EXCEEDED,
    DISAGREEMENT_HIGH_EXCEEDED,
    DISAGREEMENT_ONSET_ALREADY_PASSED,
    DISAGREEMENT_RAIN_WHILE_DRY,
    StandingCall,
    notable_disagreements,
)
from openlocalweather.instability import convective_tier
from openlocalweather.models import DailyLogEntry, DeviationBands, ModelPrediction, format_temp_c
from openlocalweather.observed import describe_observed_so_far
from openlocalweather.phrasing import phrase_defect
from openlocalweather.scales import aqi_band, uv_band
from openlocalweather.tiles import KMH_PER_KNOT, SKY_SOURCE_STATION
from openlocalweather.verify.scoring import mean, scored_predictions

# Who wrote `narrative_markdown` — stored as `narrative_source` so the record
# never mistakes code text for model text. None on entries written before
# the field existed, when the narrative was the model's or the placeholder.
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


@dataclass
class FloorInputs:
    """Everything the floor says, as plain values — the vector's input shape.

    `extended_calls` are the served leads already resolved (the stored call,
    else the code blend's row, else the model's, else the inputs' vote), so
    the composer never reads a record; `highs_by_lead` and
    `day0_peak_cape_jkg` are the inputs' own figures for the range and the
    thunder tier.
    """

    date: str
    temp_high_low_display: str
    issued_local_time: str | None = None
    sunrise: str | None = None
    sunset: str | None = None
    overview_comparison: str | None = None
    cloud_anchors: list[dict] = field(default_factory=list)
    wind_anchors: list[dict] = field(default_factory=list)
    peak_wind_primary_kmh: float | None = None
    peak_wind_secondary_kmh: float | None = None
    uv_index: float | None = None
    air_quality_index: int | None = None
    ground_aqi: list[dict] = field(default_factory=list)
    served_today: dict = field(default_factory=dict)
    extended_calls: list[dict] = field(default_factory=list)
    highs_by_lead: dict[str, list[float]] = field(default_factory=dict)
    day0_peak_cape_jkg: list[float | None] = field(default_factory=list)
    extended_trend: str | None = None
    # The two paragraphs of item 190 step 3, which replace the trend clause
    # and the lead lines in the Extended Outlook where the entry has them.
    extended_outlook: str | None = None
    secondary_name: str | None = None
    model_configured: bool = True

    # THE RIGHT-NOW RULE — 2026-10-10, the operator's: "when a local source is
    # available for right now and the models disagree, we always have to
    # trust the local right now." The station's line closes Today's
    # Forecast, and a reading that contradicts the served call is said right
    # after the opener, from `station_codes` — `notable_disagreements`
    # judged against the SERVED call, not the previous issuance's, because
    # the page is read against what it shows. The scored row stays as issued
    # (item 140: an observation scored as a forecast would enter as skill).
    observed_line: str | None = None
    observed: dict = field(default_factory=dict)
    station_name: str | None = None
    station_codes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> "FloorInputs":
        return cls(**raw)

    @classmethod
    def from_entry(
        cls,
        entry: DailyLogEntry,
        *,
        secondary_name: str | None = None,
        model_configured: bool = True,
        station_name: str | None = None,
        bands: DeviationBands | None = None,
    ) -> "FloorInputs":
        """The inputs as a stored entry holds them."""
        rows = entry.prediction_rows[0] if entry.prediction_rows else None
        comparison = rows.day_over_day if rows is not None else None
        scored = scored_predictions(entry)
        served = (entry.served_call or {}).get("today_properties") or {}
        observed = entry.observed_so_far
        standing = StandingCall(
            rain=served.get("rain"), temp_high_c=served.get("temp_high_c"), onset_hour=served.get("onset_hour"),
            temp_low_c=served.get("temp_low_c"), peak_gust_kmh=served.get("peak_wind_primary_kmh"),
        )
        return cls(
            date=entry.date.isoformat(),
            temp_high_low_display=entry.temp_high_low_display,
            issued_local_time=entry.meta.issued_local_time,
            sunrise=entry.sunrise,
            sunset=entry.sunset,
            overview_comparison=comparison.overview_comparison if comparison is not None else None,
            cloud_anchors=list(entry.cloud_anchors or []),
            wind_anchors=list(entry.wind_anchors or []),
            peak_wind_primary_kmh=entry.peak_wind_primary_kmh,
            peak_wind_secondary_kmh=entry.peak_wind_secondary_kmh,
            uv_index=entry.uv_index,
            air_quality_index=entry.air_quality_index,
            ground_aqi=[{"name": r.name, "aqi": r.aqi} for r in (entry.ground_aqi or []) if r.aqi is not None],
            served_today={
                **{k: served.get(k) for k in ("rain", "onset_hour", "precip_mm")},
                "high_c": served.get("temp_high_c"),
            },
            extended_calls=[c for c in (_lead_call(entry, scored.for_lead(lead), lead) for lead in EXTENDED_LEADS) if c],
            highs_by_lead={
                str(lead): [p.high_c for p in scored.for_lead(lead) if p.model in blend_inputs() and p.high_c is not None]
                for lead in EXTENDED_LEADS
            },
            day0_peak_cape_jkg=[p.peak_cape_jkg for p in scored.day0 if p.model in blend_inputs()],
            extended_trend=entry.extended_trend,
            extended_outlook=entry.extended_outlook,
            secondary_name=secondary_name,
            model_configured=model_configured,
            observed_line=describe_observed_so_far(observed, as_of=entry.meta.issued_local_time),
            observed=asdict(observed) if observed is not None else {},
            station_name=station_name,
            station_codes=(
                notable_disagreements(standing, observed, low_is_settled=None, bands=bands)
                if observed is not None and station_name
                else []
            ),
        )


def compose_floor(inputs: FloorInputs) -> str:
    """The floor as Markdown with the write-up's headings."""
    sections = [f"{heading}\n\n{text}" for heading, text in floor_sections(inputs)]
    sections.append(sign_off(inputs))
    return "\n\n".join(sections) + "\n"


# The section ids the writer composes by (brief.SECTIONS), for the sections
# code writes: the floor's three. Item 191 step (c) adds the rest.
FLOOR_SECTION_IDS = ("today", "extended", "secondary")


def floor_sections(inputs: FloorInputs) -> list[tuple[str, str]]:
    """(heading, text) for each section code writes, in the page's order;
    a section with nothing under it is absent, never a heading over
    nothing. `floor_section_texts` keys the same texts by section id for
    the writer's composition (item 191 step (b))."""
    out: list[tuple[str, str]] = []
    for section, heading, text in _floor_section_rows(inputs):
        if text:
            out.append((heading, text))
    return out


def floor_section_texts(inputs: FloorInputs) -> dict[str, str]:
    return {section: text for section, _, text in _floor_section_rows(inputs) if text}


def _floor_section_rows(inputs: FloorInputs) -> list[tuple[str, str, str]]:
    # The outlook composed in code (item 190 step 3) is already two checked
    # paragraphs; without it, the trend clause and a line per scored lead.
    extended = inputs.extended_outlook.strip() if inputs.extended_outlook else _sentences(_extended_parts(inputs))
    boaters = _sentences(_boaters_parts(inputs)) if inputs.secondary_name else ""
    return [
        ("today", TODAY_HEADING, _sentences(_today_parts(inputs))),
        ("extended", EXTENDED_HEADING, extended),
        ("secondary", BOATERS_HEADING.format(name=inputs.secondary_name), boaters),
    ]


def sign_off(inputs: FloorInputs) -> str:
    """The stamp and who wrote it — the floor's own closing line."""
    return _sentences(_sign_off_parts(inputs))


def compose_floor_for_entry(
    entry: DailyLogEntry,
    *,
    secondary_name: str | None = None,
    model_configured: bool = True,
    station_name: str | None = None,
    bands: DeviationBands | None = None,
) -> str:
    """The floor for one stored day — `olw floor`, and the run itself."""
    return compose_floor(
        FloorInputs.from_entry(
            entry, secondary_name=secondary_name, model_configured=model_configured,
            station_name=station_name, bands=bands,
        )
    )


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


def _today_parts(i: FloorInputs) -> list[str | None]:
    return [
        _opener(i),
        *_station_parts(i),
        _sentence(i.temp_high_low_display),
        _sky(i.cloud_anchors),
        _wind(i.wind_anchors, i.peak_wind_primary_kmh),
        _uv(i.uv_index),
        _air_quality(i),
        _sentence(i.observed_line),
    ]


def _station_parts(i: FloorInputs) -> list[str | None]:
    """One sentence per code the station's readings fired against the served
    call, in the codes' order: what was measured, beside what was called.
    A code whose numbers are missing on either side gets no sentence."""
    if not i.station_name or not i.station_codes:
        return []
    o = i.observed
    served = i.served_today
    parts: list[str | None] = []
    for code in i.station_codes:
        if code == DISAGREEMENT_ONSET_ALREADY_PASSED and o.get("precipitation_onset") and served.get("onset_hour"):
            parts.append(
                f"Rain began at {i.station_name} from {o['precipitation_onset']}, ahead of the "
                f"{served['onset_hour']} called."
            )
        elif code == DISAGREEMENT_RAIN_WHILE_DRY and o.get("precipitation"):
            parts.append(
                f"{i.station_name} has already reported rain today, against a dry call; the day is not dry."
            )
        elif code == DISAGREEMENT_HIGH_EXCEEDED and o.get("high_c") is not None and served.get("high_c") is not None:
            parts.append(
                f"{i.station_name} has already recorded {format_temp_c(o['high_c'], decimals=1)}, above the "
                f"{format_temp_c(served['high_c'], decimals=1)} called."
            )
        elif code == DISAGREEMENT_GUST_EXCEEDED and o.get("peak_gust_kmh") is not None and i.peak_wind_primary_kmh is not None:
            parts.append(
                f"{i.station_name} has already gusted to {_kmh_and_kt(o['peak_gust_kmh'])}, above the "
                f"{_kmh_and_kt(i.peak_wind_primary_kmh)} called."
            )
    return [_sentence(p) for p in parts]


def _opener(i: FloorInputs) -> str | None:
    """The day-over-day comparison, as the run composed it; or the rain
    character of the day from the served call when there was no yesterday
    to compare against — a fork's first day, the app, or a store that keeps
    no actuals."""
    if i.overview_comparison:
        return _sentence(i.overview_comparison)

    return _sentence(
        describe_day_rain(
            i.served_today.get("precip_mm"),
            i.served_today.get("onset_hour"),
            _thunder_tier(i) is not None,
            issued_hour=_issued_hour(i),
            onset_word=_onset_word(i, i.served_today.get("onset_hour")),
        )
    )


def _sky(anchors: list[dict]) -> str | None:
    """"Sky mostly cloudy early, partly cloudy at midday and mostly cloudy in
    the evening." One word for the whole day when every anchor agrees."""
    present = [
        (a.get("when"), a.get("cover"), a.get("source") == SKY_SOURCE_STATION)
        for a in anchors if a.get("when") in _ANCHOR_WHEN and a.get("cover")
    ]
    if not present:
        return None

    covers = {cover for _, cover, _ in present}
    if len(covers) == 1 and len(present) == len(_ANCHOR_WHEN) and not any(r for _, _, r in present):
        return f"Sky {present[0][1].lower()} through the day."

    # "as reported": the station's sky for an hour already lived (the
    # right-now rule), where the rest is the models' word.
    return "Sky " + _join([
        f"{cover.lower()} {_ANCHOR_WHEN[when]}{' as reported' if reported else ''}"
        for when, cover, reported in present
    ]) + "."


def _wind(anchors: list[dict], gust_kmh: float | None) -> str | None:
    """"Wind NNE early, SSW at midday and WSW in the evening; gusts to 33 km/h
    (18 kt)." The turn from the anchors, the peak from the served call."""
    present = [(a.get("when"), a.get("direction")) for a in anchors if a.get("when") in _ANCHOR_WHEN and a.get("direction")]
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


def _air_quality(i: FloorInputs) -> str | None:
    """The ground stations' worst reading where there are stations, the CAMS
    estimate where there are none — the same precedence the prompt gave."""
    readings = [r for r in i.ground_aqi if r.get("aqi") is not None]
    if readings:
        worst = max(readings, key=lambda r: r["aqi"])
        where = f"at {worst['name']}, the highest of {len(readings)} stations" if len(readings) > 1 else f"at {worst['name']}"
        return f"Air quality {worst['aqi']} ({aqi_band(worst['aqi']).lower()}) {where}."

    if i.air_quality_index is None:
        return None
    return f"Air quality {i.air_quality_index} ({aqi_band(i.air_quality_index).lower()}), by the CAMS model."


# --- Extended Outlook ---


def _extended_parts(i: FloorInputs) -> list[str | None]:
    return [_sentence(i.extended_trend), *(_lead_sentence(i, call) for call in i.extended_calls)]


def _lead_sentence(i: FloorInputs, call: dict) -> str | None:
    """"Saturday (Day+3): dry, 30% chance of rain; highs around 29 °C.\""""
    lead = int(call["lead_time_days"])
    rain = bool(call["rain"])
    probability = call.get("rain_probability_pct")

    weekday = (date.fromisoformat(i.date) + timedelta(days=lead)).strftime("%A")
    if rain:
        words = "rain likely" if probability is None else f"rain likely, {probability}% chance"
    else:
        words = "dry" if probability is None else f"dry, {probability}% chance of rain"

    highs = _highs(i.highs_by_lead.get(str(lead)) or [])
    return f"{weekday} (Day+{lead}): {words}{'; ' + highs if highs else ''}."


def _lead_call(entry: DailyLogEntry, rows: list[ModelPrediction], lead: int) -> dict | None:
    """The served call at a lead, from a stored entry: the stored call where
    the entry carries one (item 189), else the code blend's row, else the
    model's own row, else the inputs' equal-weight vote with a dry tie."""
    for prop in (entry.served_call or {}).get("extended_properties") or []:
        if prop.get("lead_time_days") == lead and prop.get("rain") is not None:
            return {"lead_time_days": lead, "rain": bool(prop["rain"]), "rain_probability_pct": prop.get("rain_probability_pct")}

    for model in (CODE_BLEND_MODEL_ID, BLEND_MODEL_ID):
        row = next((p for p in rows if p.model == model and p.rain is not None), None)
        if row is not None:
            return {"lead_time_days": lead, "rain": row.rain, "rain_probability_pct": row.rain_probability_pct}

    votes = [p for p in rows if p.model in blend_inputs() and p.rain is not None]
    if not votes:
        return None
    wet = sum(1 for p in votes if p.rain)
    return {"lead_time_days": lead, "rain": wet * 2 > len(votes), "rain_probability_pct": round(100 * wet / len(votes))}


def _highs(highs: list[float]) -> str | None:
    if not highs:
        return None
    if max(highs) - min(highs) > HIGH_RANGE_SPREAD_C:
        return f"highs {min(highs):.0f} to {max(highs):.0f} °C"
    return f"highs around {mean(highs):.0f} °C"


# --- Conditions for Boaters ---


def _boaters_parts(i: FloorInputs) -> list[str | None]:
    if i.peak_wind_secondary_kmh is None:
        return []
    return [
        f"Peak gust {_kmh_and_kt(i.peak_wind_secondary_kmh)}.",
        _STORM_GUSTS if _thunder_tier(i) is not None else None,
    ]


# --- The sign-off ---


def _sign_off_parts(i: FloorInputs) -> list[str | None]:
    return [
        f"Figures issued {i.issued_local_time}." if i.issued_local_time else None,
        SIGN_OFF_WITH_MODEL if i.model_configured else SIGN_OFF_WITHOUT_MODEL,
    ]


# --- Shared ---


def _thunder_tier(i: FloorInputs) -> str | None:
    """The thunder word from the Day+0 inputs' CAPE maxima, the pipeline's
    own rule (`convective_tier`)."""
    return convective_tier(i.day0_peak_cape_jkg)


def _issued_hour(i: FloorInputs) -> int | None:
    try:
        return int(i.issued_local_time.split(":")[0]) if i.issued_local_time else None
    except ValueError:
        return None


def _onset_word(i: FloorInputs, onset: str | None) -> str | None:
    """The onset placed by the sun, as `pipeline._onset_word_for` places it,
    from the sun times and the issue time the entry stores."""
    now = _clock(i, i.issued_local_time)
    sunrise = _clock(i, i.sunrise)
    sunset = _clock(i, i.sunset)
    if now is None or sunrise is None or sunset is None:
        return None
    return onset_word(onset, now=now, sunrise=sunrise, sunset=sunset)


def _clock(i: FloorInputs, hhmm: str | None) -> datetime | None:
    try:
        hour, minute = (int(part) for part in hhmm.split(":")[:2])
        day = date.fromisoformat(i.date)
    except (AttributeError, ValueError):
        return None
    return datetime(day.year, day.month, day.day, hour, minute)


def _kmh_and_kt(kmh: float) -> str:
    return f"{kmh:.0f} km/h ({kmh / KMH_PER_KNOT:.0f} kt)"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"
