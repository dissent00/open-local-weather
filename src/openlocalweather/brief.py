"""The brief: the writer's input — ROADMAP item 191.

WHY A BRIEF. The narrative call sends ~122K characters (~49K tokens) and
asks for thousands of words, and that shape is what every free route
refuses: Gemini sheds it, OpenRouter's free models finish it 1 time in 5,
Groq's 8K-token-per-minute ceiling cannot admit it. Once the served call is
code's (item 189) the writer never needs a raw array: everything the
write-up quotes is a composed phrase or a stored figure. This renders
those, and only those, in about a tenth of the characters.

PARSED FROM THE ARCHIVED USER PROMPT, not rebuilt from the run. The user
prompt is the one artefact the run archives and hashes, so a write-up asked
at any later hour, on any route, reads the same input — the guarantee item
186 wanted — and the measurement over the archive runs the same parser as
production. The entry supplies what the prompt does not carry: the served
call, the day table and the record's lead rankings.

TWO TIERS. The full brief for the pipeline's routes; the mini brief for
on-device models, sized for a 4,096-token budget shared with instructions
and output. SECTIONS CHOOSE BLOCKS: the brief carries only the blocks the
enabled sections need, so a deployment that opts out of the discussion
sends no synoptic ring.

MODEL NAMES ARE THE PAGE'S. Review claims arrive naming `gfs_seamless`;
the brief says GFS, so the prose never carries an id.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from openlocalweather.llm.prompt_size import prompt_block_sizes
from openlocalweather.outlook import MODEL_SHORT_NAMES, short_model_name
from openlocalweather.tiles import KMH_PER_KNOT

# The sections a write-up can hold, in the page's order. `severe` is rendered
# only while the convective flag or a warning is live; `secondary` only where
# a secondary point exists. Synoptic and confidence are opt-in per
# deployment (item 191's decision 2).
SECTION_TODAY = "today"
SECTION_EXTENDED = "extended"
SECTION_SEVERE = "severe"
SECTION_SECONDARY = "secondary"
SECTION_SYNOPTIC = "synoptic"
SECTION_CONFIDENCE = "confidence"
SECTIONS = (
    SECTION_TODAY, SECTION_EXTENDED, SECTION_SEVERE, SECTION_SECONDARY, SECTION_SYNOPTIC, SECTION_CONFIDENCE,
)
DEFAULT_SECTIONS = (SECTION_TODAY, SECTION_EXTENDED, SECTION_SEVERE, SECTION_SECONDARY)

TIER_FULL = "full"
TIER_MINI = "mini"

# Which blocks each section reads. A block is rendered when any enabled
# section names it.
BLOCKS_BY_SECTION: dict[str, tuple[str, ...]] = {
    SECTION_TODAY: (
        "issued", "windows", "call", "day_over_day", "thunder", "sky", "wind", "uv", "observed",
        "footnotes", "air", "bulletin",
    ),
    SECTION_EXTENDED: ("calendar", "next_three_days", "sky_by_day", "days_ahead", "models_ahead", "record"),
    SECTION_SEVERE: ("thunder", "cape", "call", "bulletin"),
    SECTION_SECONDARY: ("secondary_wind", "thunder"),
    SECTION_SYNOPTIC: ("synoptic", "basin", "recency", "call"),
    SECTION_CONFIDENCE: ("models_today", "review", "record", "sufficiency", "bulletin", "recency"),
}
# Item 191's mini cut: the full tier without observed-so-far, the secondary
# point, the last-known AQI, the sky by day, UV, recency and the ring;
# fewer findings; the windows and instability reduced to their timing.
MINI_DROPPED_BLOCKS = frozenset({
    "observed", "footnotes", "secondary_wind", "sky_by_day", "uv", "recency", "synoptic", "calendar", "windows",
})
REVIEW_FINDINGS_FULL = 12
REVIEW_FINDINGS_MINI = 3
# The bulletin's text is the one free-form block; a long national outlook
# would swamp the brief.
BULLETIN_MAX_CHARS = 700
# The basin's three-day pressure change is "near-steady" inside this, the
# ring's own threshold (synoptic.py).
BASIN_STEADY_HPA = 1.5
ESTABLISHED = "established"
UNAVAILABLE = "Unavailable"


@dataclass
class BriefInputs:
    """Everything the brief says, as plain values; `to_json`/`from_json`
    round-trip so a vector case or a stored copy renders the same text."""

    issued: str | None = None
    calendar: list[dict] = field(default_factory=list)
    windows: list[str] = field(default_factory=list)
    recency: dict | None = None
    instability: dict | None = None
    day_over_day: str | None = None
    next_three_days: str | None = None
    sky_anchors: dict | None = None
    sky_by_day: list[dict] = field(default_factory=list)
    wind_directions: dict | None = None
    wind_shift: str | None = None
    secondary_wind: dict | None = None
    peak_uv: dict | None = None
    calibrated_gust_kmh: float | None = None
    observed_so_far: str | None = None
    footnotes: list[str] = field(default_factory=list)
    ground_aqi_stations: list[dict] = field(default_factory=list)
    ground_aqi_summary: dict | None = None
    ground_aqi_last_known: dict | None = None
    local_bulletin: str | None = None
    predictions: list[dict] = field(default_factory=list)
    synoptic_statements: list[str] = field(default_factory=list)
    basin_pressure: dict | None = None
    review_findings: list[dict] = field(default_factory=list)
    data_sufficiency: str | None = None
    track_record: list[dict] = field(default_factory=list)
    served_call: dict | None = None
    temp_display: str | None = None
    extended_days: list[dict] = field(default_factory=list)
    secondary_name: str | None = None
    met_service_name: str | None = None
    # The model id the met service's own forecast is filed under in the
    # tables (`local_bulletin_model_id`), so it reads as the service's name.
    met_service_model_id: str | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> "BriefInputs":
        return cls(**raw)

    @classmethod
    def from_user_prompt(
        cls, user_prompt: str, entry: Any, *, secondary_name: str | None, met_service_name: str | None,
        met_service_model_id: str | None = None,
    ) -> "BriefInputs":
        """The archived user prompt and the stored day, read together."""
        b = _blocks(user_prompt)
        guidance = _json_body(b.get("TODAY'S MULTI-MODEL GUIDANCE", "")) or {}
        synoptic = guidance.get("synoptic_scale_pressure") or {}
        review = _json_body(b.get("LONG-RUN REVIEW", "")) or {}
        served = _field(entry, "served_call")
        return cls(
            issued=_issued(b.get("PREAMBLE", "")),
            calendar=_table_rows(b.get("CALENDAR")),
            windows=[line[2:] for line in _phrase_lines(b.get("FORECAST WINDOWS")) if line.startswith("- ")],
            recency=_json_body(b.get("GUIDANCE RECENCY", "")),
            instability=_json_body(b.get("CONVECTIVE INSTABILITY", "")),
            day_over_day=_field(entry, "overview_comparison"),
            next_three_days=_phrase(b.get("NEXT THREE DAYS")),
            sky_anchors=_json_body(b.get("SKY AT EACH ANCHOR", "")),
            sky_by_day=_table_rows(b.get("SKY BY DAY")),
            wind_directions=_json_body(b.get("WIND DIRECTION", "")),
            wind_shift=_phrase(b.get("WIND SHIFT")),
            secondary_wind=_json_body(b.get("SECONDARY POINT WIND", "")),
            peak_uv=_json_body(b.get("PEAK UV INDEX", "")),
            calibrated_gust_kmh=_leading_number(_phrase(b.get("CALIBRATED PEAK GUST"))),
            observed_so_far=_phrase(b.get("OBSERVED SO FAR TODAY")),
            footnotes=[
                *_phrase_lines(b.get("OVERNIGHT LOW FOOTNOTE")),
                *_phrase_lines(b.get("OBSERVATION FOOTNOTES")),
            ],
            ground_aqi_stations=_table_rows(b.get("GROUND AQI STATIONS")),
            ground_aqi_summary=_json_body(b.get("GROUND AQI SUMMARY", "")),
            ground_aqi_last_known=_json_body(b.get("GROUND AQI LAST KNOWN", "")),
            local_bulletin=_phrase(b.get("LOCAL BULLETIN"), keep_unavailable=True),
            predictions=_table_rows(b.get("EXTRACTED PER-MODEL PREDICTIONS")),
            synoptic_statements=list(synoptic.get("statements") or []),
            basin_pressure=basin_pressure(guidance.get("regional_pressure")),
            review_findings=[
                {"kind": f.get("kind"), "checks": f.get("checks"), "claim": f.get("claim")}
                for f in review.get("findings") or []
                if f.get("confidence") == ESTABLISHED
            ],
            data_sufficiency=review.get("data_sufficiency"),
            track_record=[
                {k: row.get(k) for k in ("model", "lead_time_days", "rolling_30_rain_pct", "all_time_checks")}
                for row in _table_rows(b.get("MODEL TRACK RECORD"))
            ],
            served_call=served,
            temp_display=_field(entry, "temp_high_low_display"),
            extended_days=list(_field(entry, "extended_days") or []),
            secondary_name=secondary_name,
            met_service_name=met_service_name,
            met_service_model_id=met_service_model_id,
        )


def basin_pressure(points: list[dict] | None) -> dict | None:
    """The regional pressure points reduced to one line's worth: today's
    range across the basin and the mean three-day change. The ring in
    `synoptic.py` is the large scale; this is the basin the ring surrounds,
    which the old prompt sent as 4K characters of arrays."""
    today: list[float] = []
    changes: list[float] = []
    for point in points or []:
        series = ((point.get("daily") or {}).get("pressure_msl_mean")) or []
        values = [v for v in series[:3] if isinstance(v, (int, float))]
        if len(series) >= 1 and isinstance(series[0], (int, float)):
            today.append(float(series[0]))
        if len(values) == 3 and len(series) >= 3:
            changes.append(float(series[2]) - float(series[0]))
    if not today:
        return None

    return {
        "points": len(today),
        "today_min_hpa": round(min(today), 1),
        "today_max_hpa": round(max(today), 1),
        "change_72h_hpa": round(sum(changes) / len(changes), 1) if changes else None,
    }


# --- the renderer -----------------------------------------------------------


def render_brief(
    inputs: BriefInputs, *, tier: str = TIER_FULL, sections: tuple[str, ...] | list[str] = DEFAULT_SECTIONS
) -> str:
    """The brief's text for the enabled sections at the given tier."""
    if tier not in (TIER_FULL, TIER_MINI):
        raise ValueError(f"unknown brief tier {tier!r}")
    wanted: list[str] = []
    for section in SECTIONS:
        if section not in sections:
            continue
        for block in BLOCKS_BY_SECTION[section]:
            if block not in wanted:
                wanted.append(block)
    if tier == TIER_MINI:
        wanted = [block for block in wanted if block not in MINI_DROPPED_BLOCKS]

    renderers = {
        "issued": _issued_block, "windows": _windows_block, "calendar": _calendar_block,
        "recency": _recency_block, "call": _call_block, "day_over_day": _day_over_day_block,
        "thunder": _thunder_block, "cape": _cape_block, "sky": _sky_block, "wind": _wind_block,
        "secondary_wind": _secondary_wind_block, "uv": _uv_block, "observed": _observed_block,
        "footnotes": _footnotes_block, "air": _air_block, "bulletin": _bulletin_block,
        "next_three_days": _next_three_days_block, "sky_by_day": _sky_by_day_block,
        "days_ahead": _days_ahead_block, "models_today": _models_today_block,
        "models_ahead": _models_ahead_block, "synoptic": _synoptic_block, "basin": _basin_block,
        "record": _record_block, "review": _review_block, "sufficiency": _sufficiency_block,
    }
    parts = []
    for block in _ORDER:
        if block not in wanted:
            continue
        text = renderers[block](inputs, tier)
        if text:
            parts.append(text)
    return "\n".join(parts) + "\n"


# The page's reading order: when, the call, today, the days ahead, the
# models, the large scale, the record.
_ORDER = (
    "issued", "windows", "calendar", "recency", "call", "day_over_day", "thunder", "cape", "sky", "wind",
    "secondary_wind", "uv", "observed", "footnotes", "air", "bulletin", "next_three_days", "sky_by_day",
    "days_ahead", "models_today", "models_ahead", "synoptic", "basin", "record", "review", "sufficiency",
)


def _issued_block(i: BriefInputs, tier: str) -> str | None:
    return f"ISSUED: {i.issued}" if i.issued else None


def _windows_block(i: BriefInputs, tier: str) -> str | None:
    if not i.windows:
        return None
    return "WINDOWS (the periods this forecast covers, with their hours): " + "; ".join(i.windows)


def _calendar_block(i: BriefInputs, tier: str) -> str | None:
    if not i.calendar:
        return None
    days = []
    for row in i.calendar:
        label = f"{row.get('day_name')} {row.get('date')}"
        lead = _int(row.get("lead_time_days"))
        days.append(label + (" (today)" if lead == 0 else ""))
    return "CALENDAR (use these day names and dates as given; derive no others): " + "; ".join(days)


def _recency_block(i: BriefInputs, tier: str) -> str | None:
    r = i.recency
    if not r:
        return None
    hours = r.get("hours_old")
    at = str(r.get("models_last_aligned_at") or "")[:16].replace("T", " ")
    return f"GUIDANCE: the models' cycle of {at} UTC, {hours} h old at issue."


def _call_block(i: BriefInputs, tier: str) -> str | None:
    c = (i.served_call or {}).get("today_properties") or {}
    if not c:
        return None
    lines = ["THE CALL (code's, already served to the reader; state these figures as given):"]
    rain = c.get("rain")
    rain_words = c.get("rain_expected")
    onset = c.get("onset_window") or c.get("onset_hour")
    amount = c.get("precip_mm")
    prob = c.get("rain_probability_pct")
    rain_line = f"  rain: {'yes' if rain else 'no' if rain is False else 'unknown'}"
    if rain_words:
        rain_line += f", {rain_words}"
    if rain and onset:
        rain_line += f"; onset {onset}"
    if rain and amount is not None:
        rain_line += f"; {_num(amount)} mm"
    if prob is not None:
        rain_line += f"; {_num(prob)}%"
    lines.append(rain_line)
    if i.temp_display:
        lines.append(f"  temperature: {i.temp_display}")
    gust = c.get("peak_wind_primary_kmh")
    if gust is not None:
        gust_line = f"  peak gust: {_kmh_and_kt(gust)} ashore"
        secondary_gust = c.get("peak_wind_secondary_kmh")
        if secondary_gust is not None and i.secondary_name:
            gust_line += f"; {_kmh_and_kt(secondary_gust)} on {i.secondary_name}"
        lines.append(gust_line)
    trend = c.get("mslp_trend_24h")
    pattern = c.get("synoptic_pattern")
    if trend or pattern:
        lines.append("  pressure: " + "; ".join(p for p in (f"trend {trend}" if trend else None, pattern) if p))
    aqi = c.get("air_quality_aqi")
    if aqi is not None:
        lines.append(f"  air quality: AQI {_num(aqi)} (model estimate)")
    for lead in (i.served_call or {}).get("extended_properties") or []:
        day = _day_name(i, _int(lead.get("lead_time_days")))
        words = "rain" if lead.get("rain") else "dry" if lead.get("rain") is False else "no call"
        p = lead.get("rain_probability_pct")
        lines.append(
            f"  Day+{lead.get('lead_time_days')}{f' ({day})' if day else ''}: {words}"
            + (f", {_num(p)}%" if p is not None else "")
        )
    return "\n".join(lines)


def _day_over_day_block(i: BriefInputs, tier: str) -> str | None:
    return f"SINCE YESTERDAY (verbatim): {i.day_over_day}" if i.day_over_day else None


def _thunder_block(i: BriefInputs, tier: str) -> str | None:
    inst = i.instability
    if not inst:
        return None
    convective = inst.get("convective")
    timing = inst.get("timing")
    if not convective:
        return "THUNDER: no model's instability supports thunderstorms; say nothing about thunder."
    if timing:
        return f"THUNDER (verbatim, capitalised, with a full stop): {timing}"
    return "THUNDER: possible; the models do not agree on when, so name no hour."


def _cape_block(i: BriefInputs, tier: str) -> str | None:
    inst = i.instability
    by_model = (inst or {}).get("peak_cape_by_model") or {}
    if not by_model:
        return None
    parts = [f"{short_model_name(m)} {_num(v, 0)}" for m, v in by_model.items() if v is not None]
    above = [short_model_name(m) for m in inst.get("models_above_threshold") or []]
    line = "PEAK CAPE TODAY (J/kg, per model): " + "; ".join(parts)
    if above:
        line += f". Above the thunderstorm threshold: {', '.join(above)}"
    peak_hour = inst.get("peak_hour")
    if peak_hour:
        line += f"; peak at {peak_hour}"
    return line + "."


def _sky_block(i: BriefInputs, tier: str) -> str | None:
    if not i.sky_anchors:
        return None
    parts = [f"{when} {word}" for when, word in i.sky_anchors.items() if word]
    return "SKY (the tile's word at each anchor; use these words for these hours): " + "; ".join(parts) if parts else None


def _wind_block(i: BriefInputs, tier: str) -> str | None:
    lines = []
    if i.wind_shift:
        lines.append(f"WIND (verbatim): {i.wind_shift}")
    if i.wind_directions:
        parts = [f"{when} {point if point else 'no agreed bearing'}" for when, point in i.wind_directions.items()]
        lines.append("WIND AT EACH ANCHOR (name a bearing only at an anchor that has one): " + "; ".join(parts))
    return "\n".join(lines) if lines else None


def _secondary_wind_block(i: BriefInputs, tier: str) -> str | None:
    w = i.secondary_wind
    if not w or not i.secondary_name:
        return None
    line = f"{i.secondary_name.upper()} WIND (verbatim timeline): {w.get('timeline')}"
    gust = w.get("consensus_gust_kmh")
    if gust is not None:
        line += f"; consensus gust {_kmh_and_kt(gust)}"
    return line


def _uv_block(i: BriefInputs, tier: str) -> str | None:
    uv = i.peak_uv
    if not uv or uv.get("index") is None:
        return None
    day = _day_for_date(i, str(uv.get("date"))) or str(uv.get("date"))
    return f"UV: peak index {_num(uv['index'])} {day}, from {short_model_name(str(uv.get('source')))}."


def _observed_block(i: BriefInputs, tier: str) -> str | None:
    return f"OBSERVED SO FAR (verbatim; measured, not forecast): {i.observed_so_far}" if i.observed_so_far else None


def _footnotes_block(i: BriefInputs, tier: str) -> str | None:
    if not i.footnotes:
        return None
    return "FOOTNOTES (verbatim or not at all):\n" + "\n".join(f"  - {f}" for f in i.footnotes)


def _air_block(i: BriefInputs, tier: str) -> str | None:
    lines = []
    if i.ground_aqi_stations:
        rows = []
        for s in i.ground_aqi_stations:
            if s.get("aqi") is None:
                continue
            pm = ", ".join(
                f"{label} {s[key]}" for label, key in (("PM2.5", "pm25"), ("PM10", "pm10")) if s.get(key) is not None
            )
            stale = " (stale)" if str(s.get("stale")).lower() == "true" else ""
            rows.append(f"{s.get('name')} AQI {s['aqi']}{f' ({pm})' if pm else ''}{stale}")
        if rows:
            lines.append("AIR QUALITY, ground stations: " + "; ".join(rows))
    summary = i.ground_aqi_summary
    if summary and summary.get("highest_station_name"):
        lines.append(
            f"  highest station: {summary['highest_station_name']} at AQI {summary.get('aqi_max')}, "
            f"{summary.get('stations_with_aqi')} of {summary.get('stations_total')} reporting"
        )
    last = i.ground_aqi_last_known
    if tier == TIER_FULL and last and last.get("aqi") is not None and not i.ground_aqi_stations:
        lines.append(f"  last known reading: {last.get('station_name')} AQI {last['aqi']}, {last.get('hours_old')} h old")
    return "\n".join(lines) if lines else None


def _bulletin_block(i: BriefInputs, tier: str) -> str | None:
    if not i.met_service_name:
        return None
    text = (i.local_bulletin or "").strip()
    if not text or text.startswith(UNAVAILABLE):
        return f"LOCAL MET SERVICE ({i.met_service_name}): no bulletin this run; say so in one clause."
    if len(text) > BULLETIN_MAX_CHARS:
        text = text[:BULLETIN_MAX_CHARS].rstrip() + " …"
    return f"LOCAL MET SERVICE ({i.met_service_name}), its own forecast:\n" + "\n".join(f"  {l}" for l in text.splitlines() if l.strip())


def _next_three_days_block(i: BriefInputs, tier: str) -> str | None:
    return f"NEXT THREE DAYS (verbatim): {i.next_three_days}" if i.next_three_days else None


def _sky_by_day_block(i: BriefInputs, tier: str) -> str | None:
    if not i.sky_by_day:
        return None
    parts = [f"{row.get('day_name')} {row.get('sky')}" for row in i.sky_by_day if row.get("sky")]
    return "SKY BY DAY (one word per day, as given): " + "; ".join(parts) if parts else None


def _days_ahead_block(i: BriefInputs, tier: str) -> str | None:
    if not i.extended_days:
        return None
    lines = ["DAYS AHEAD (the models together, per day):"]
    for d in i.extended_days:
        bits = []
        models = d.get("models") or []
        if models:
            bits.append(f"{d.get('wet_votes', 0)} of {len(models)} models wet")
        amounts = [v for v in (d.get("precip_by_model") or {}).values() if v is not None]
        if amounts:
            lo, hi = min(amounts), max(amounts)
            bits.append(f"rain {_num(lo, 1)}–{_num(hi, 1)} mm by model" if lo != hi else f"rain {_num(lo, 1)} mm")
        if d.get("high_min_c") is not None and d.get("high_max_c") is not None:
            lo, hi = d["high_min_c"], d["high_max_c"]
            bits.append(f"high {_num(lo, 0)}–{_num(hi, 0)} °C" if _num(lo, 0) != _num(hi, 0) else f"high {_num(lo, 0)} °C")
        if d.get("low_c") is not None:
            bits.append(f"low {_num(d['low_c'], 0)} °C")
        if d.get("wind_kmh") is not None:
            bits.append(f"gusts to {_kmh_and_kt(d['wind_kmh'])}")
        if d.get("thunder"):
            bits.append(f"thunder {d['thunder']}")
        if d.get("sky"):
            bits.append(str(d["sky"]).lower())
        lines.append(f"  {d.get('day_name')} (Day+{d.get('lead_time_days')}): " + "; ".join(bits))
    return "\n".join(lines)


_MODEL_COLUMNS_TODAY = (
    ("rain", "rain"), ("onset", "onset"), ("precip_mm", "mm"), ("rain_probability_pct", "%"),
    ("wind_kmh", "gust km/h"), ("high_c", "high"), ("low_c", "low"), ("peak_cape_jkg", "CAPE"),
)
_MODEL_COLUMNS_AHEAD = (("rain", "rain"), ("precip_mm", "mm"), ("rain_probability_pct", "%"), ("high_c", "high"), ("low_c", "low"))


def _models_today_block(i: BriefInputs, tier: str) -> str | None:
    return _models_table("MODELS TODAY (each model's own Day+0 call; these exact values are scored)", i, "day0", _MODEL_COLUMNS_TODAY)


def _models_ahead_block(i: BriefInputs, tier: str) -> str | None:
    parts = [
        _models_table(f"MODELS AT DAY+{lead}", i, f"day{lead}", _MODEL_COLUMNS_AHEAD) for lead in (3, 7)
    ]
    return "\n".join(p for p in parts if p) or None


def _models_table(title: str, i: BriefInputs, lead: str, columns: tuple[tuple[str, str], ...]) -> str | None:
    rows = [r for r in i.predictions if r.get("lead") == lead]
    if not rows:
        return None
    header = "model\t" + "\t".join(label for _, label in columns)
    lines = [f"{title}:", header]
    for r in rows:
        cells = [_cell(r.get(key)) for key, _ in columns]
        lines.append(_name(i, str(r.get("model"))) + "\t" + "\t".join(cells))
    return "\n".join(lines)


def _synoptic_block(i: BriefInputs, tier: str) -> str | None:
    if not i.synoptic_statements:
        return "LARGE SCALE: the pressure ring could not be assessed this run; say so rather than substituting the local gradient."
    return "LARGE SCALE (verbatim statements, from a nine-point ring about 2,600 km across):\n" + "\n".join(
        f"  - {s}" for s in i.synoptic_statements
    )


def _basin_block(i: BriefInputs, tier: str) -> str | None:
    b = i.basin_pressure
    if not b:
        return None
    change = b.get("change_72h_hpa")
    if change is None:
        tendency = "no three-day tendency available"
    elif abs(change) < BASIN_STEADY_HPA:
        tendency = f"near-steady over three days ({change:+.1f} hPa)"
    else:
        tendency = f"{'rising' if change > 0 else 'falling'} by {abs(change):.1f} hPa over three days"
    return (
        f"BASIN PRESSURE: across {b.get('points')} points, today's mean sea-level pressure sits between "
        f"{b.get('today_min_hpa')} and {b.get('today_max_hpa')} hPa; {tendency}."
    )


def _record_block(i: BriefInputs, tier: str) -> str | None:
    if not i.track_record:
        return None
    lines = []
    for lead in (0, 3, 7):
        rows = [r for r in i.track_record if _int(r.get("lead_time_days")) == lead and _int(r.get("all_time_checks")) is not None]
        rows = [r for r in rows if r.get("model") not in _HIDDEN_MODELS]
        if not rows:
            continue
        checks = max(_int(r.get("all_time_checks")) or 0 for r in rows)
        scored = [r for r in rows if (_int(r.get("all_time_checks")) or 0) >= 10 and _float(r.get("rolling_30_rain_pct")) is not None]
        if scored:
            best = max(scored, key=lambda r: _float(r.get("rolling_30_rain_pct")) or 0.0)
            lines.append(
                f"  Day+{lead}: best rain record {_name(i, str(best['model']))}, right "
                f"{_num(best['rolling_30_rain_pct'], 0)}% of the last 30 checks; {checks} checks all time"
            )
        else:
            lines.append(f"  Day+{lead}: too few checks to rank any model yet ({checks} all time)")
    return "RECORD (this place's own verification; the only rankings you may use beyond REVIEW):\n" + "\n".join(lines) if lines else None


# Rows the forecaster never sees as peers — the blend's own record stays out
# of its prompt (the standing rule), and the code blend is the served call.
_HIDDEN_MODELS = frozenset({"olw_blend", "olw_code_blend", "olw_lite"})


def _review_block(i: BriefInputs, tier: str) -> str | None:
    if not i.review_findings:
        return None
    limit = REVIEW_FINDINGS_MINI if tier == TIER_MINI else REVIEW_FINDINGS_FULL
    ranked = sorted(
        i.review_findings, key=lambda f: (0 if f.get("kind") == "ranking" else 1, str(f.get("claim")))
    )[:limit]
    return (
        "REVIEW (established findings only; the only biases and rankings you may state):\n"
        + "\n".join(f"  - {_short_names(i, str(f.get('claim')))} ({f.get('checks')} checks)" for f in ranked)
    )


def _sufficiency_block(i: BriefInputs, tier: str) -> str | None:
    if tier == TIER_MINI or not i.data_sufficiency:
        return None
    return f"DATA SUFFICIENCY: {_short_names(i, i.data_sufficiency)}"


# --- parsing the archived prompt --------------------------------------------


def _blocks(user_prompt: str) -> dict[str, str]:
    """Each top-level block's text below its header line, keyed by the
    header's name as `prompt_size` names it."""
    out: dict[str, str] = {}
    offset = 0
    for name, size in prompt_block_sizes(user_prompt).items():
        if "/" in name:
            continue
        text = user_prompt[offset:offset + size]
        offset += size
        body = text.split("\n", 1)[1] if "\n" in text else ""
        out[name] = body
    return out


def _field(entry: Any, name: str) -> Any:
    """A stored day's field, off the entry object or its JSON."""
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _issued(preamble: str) -> str | None:
    for line in preamble.splitlines():
        if line.startswith("ISSUED: "):
            return line[len("ISSUED: "):].strip()
    return None


def _json_body(body: str) -> Any:
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(body[start:end + 1])
    except ValueError:
        return None


def _table_rows(body: str | None) -> list[dict]:
    """A tab-separated table with a header row, "-" read as None; the
    cells stay the strings the prompt showed."""
    lines = [l for l in (body or "").strip("\n").splitlines() if l.strip()]
    if len(lines) < 2 or "\t" not in lines[0]:
        return []
    columns = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) != len(columns):
            continue
        rows.append({c: (None if v == "-" else v) for c, v in zip(columns, cells)})
    return rows


def _phrase(body: str | None, *, keep_unavailable: bool = False) -> str | None:
    text = (body or "").strip()
    if not text or (text.startswith(UNAVAILABLE) and not keep_unavailable):
        return None
    return text


def _phrase_lines(body: str | None) -> list[str]:
    text = _phrase(body)
    return [l.strip() for l in text.splitlines() if l.strip()] if text else []


def _leading_number(text: str | None) -> float | None:
    if not text:
        return None
    head = text.split(" ", 1)[0]
    try:
        return float(head)
    except ValueError:
        return None


# --- small helpers ----------------------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _num(value: Any, decimals: int | None = None) -> str:
    """A number as the page shows it: integers without a point, otherwise
    at the given precision, else as given."""
    f = _float(value)
    if f is None:
        return str(value)
    if decimals is None:
        return str(int(f)) if f == int(f) else str(value)
    return f"{f:.{decimals}f}"


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and value in ("true", "false"):
        return "yes" if value == "true" else "no"
    return _num(value)


def _kmh_and_kt(kmh: float) -> str:
    # floor.py's own formatting, kept private there; the same two numbers.
    return f"{float(kmh):.0f} km/h ({float(kmh) / KMH_PER_KNOT:.0f} kt)"


def _day_for_date(i: BriefInputs, date: str) -> str | None:
    for row in i.calendar:
        if row.get("date") == date:
            return "today" if _int(row.get("lead_time_days")) == 0 else str(row.get("day_name"))
    return None


def _day_name(i: BriefInputs, lead: int | None) -> str | None:
    for row in i.calendar:
        if _int(row.get("lead_time_days")) == lead:
            return str(row.get("day_name"))
    return None


def _name(i: BriefInputs, model: str) -> str:
    if i.met_service_model_id and model == i.met_service_model_id and i.met_service_name:
        return i.met_service_name
    return short_model_name(model)


def _short_names(i: BriefInputs, text: str) -> str:
    for model_id, short in sorted(MODEL_SHORT_NAMES.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(model_id, short)
    if i.met_service_model_id and i.met_service_name:
        text = text.replace(i.met_service_model_id, i.met_service_name)
    return text
