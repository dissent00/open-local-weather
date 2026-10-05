"""The Extended Outlook, composed in code — ROADMAP item 190, step 3.

THE OPERATOR'S ASK, 2026-10-05: not a line per day but the trends — "rain
likely through Thursday, increasing chance, windier on Wednesday", then the
longer term — the way the model's outlook read when it read well. The data
was fetched and extracted all along: the daily arrays hold eight days per
model, the trend clause already reads days one to three, the record scores
Day+3 and Day+7. What was missing was the prose, so this puts it in code.

TWO SPANS, ONE VOCABULARY. The near term (Day+1 to Day+3) opens with the
trend clause the prompt and the floor already carry (`describe_extended_
trend`), then says the sky, how rain's support among the models moves, a
day that stands out for wind, Thursday's scored call and what the record
says about this lead. The far term (Day+4 to Day+7) is the same trend
clause measured from Day+3 rather than today, then which models still
reach, which are the wetter solutions, the Day+7 call and its record.

HONEST ABOUT CHANCE. The models' stated daily probabilities did not sort
outcomes at Days+1 to +3 on the record (item 158 step 2), so "the chance of
rain rising" is never a probability here: it is the count of models
calling rain, which is a fact about the guidance. The one probability
printed is the served call's, the number tomorrow scores.

Pure over `OutlookInputs`, mirrored in `olw_core` and pinned by
`spec/vectors/extended_outlook.json`; `extended_days` builds the inputs'
day table from the daily arrays and is pinned by `extended_days.json`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from openlocalweather.comparison import WIND_CHANGE_BANDS_KMH, describe_extended_trend
from openlocalweather.dates import forward_calendar
from openlocalweather.defaults import BEST_MATCH_MODEL_ID
from openlocalweather.extract import extract_day_n_predictions_from_daily
from openlocalweather.instability import convective_tier
from openlocalweather.phrasing import phrase_defect
from openlocalweather.tiles import sky_by_day
from openlocalweather.verify.scoring import mean

# The leads each span covers. The near span is the trend clause's; the far
# span runs to the last day the daily arrays hold.
NEAR_LEADS = (1, 2, 3)
FAR_LEADS = (4, 5, 6, 7)
# The leads the record scores beyond today (item 72).
SCORED_LEADS = (3, 7)

# A record shorter than this ranks nobody — the review's own ten-check
# floor (item 12).
THIN_RECORD_CHECKS = 10
# Rain support moving by fewer votes than this is noise among four models.
SUPPORT_MOVE_VOTES = 2
# Models whose far-span totals sit this far apart are different solutions:
# the width of the showery band (comparison.DAY_RAIN_BANDS_MM).
PRECIP_DISAGREEMENT_MM = 5.0

# How prose names a model. The ids are the record's; a reader knows these.
MODEL_SHORT_NAMES = {
    "gfs_seamless": "GFS",
    "ecmwf_ifs025": "ECMWF",
    "icon_seamless": "ICON",
    "ukmo_seamless": "UKMO",
    "jma_seamless": "JMA",
    BEST_MATCH_MODEL_ID: "Best Match",
}


def short_model_name(model: str) -> str:
    return MODEL_SHORT_NAMES.get(model, model)


@dataclass
class ExtendedDay:
    """One day beyond today, as the models see it together."""

    lead_time_days: int
    date: str
    day_name: str
    # The models that reach this day with a temperature, in the inputs' order.
    models: list[str] = field(default_factory=list)
    wet_votes: int = 0
    precip_mm: float | None = None
    precip_by_model: dict[str, float] = field(default_factory=dict)
    high_c: float | None = None
    high_min_c: float | None = None
    high_max_c: float | None = None
    low_c: float | None = None
    wind_kmh: float | None = None
    thunder: str | None = None
    sky: str | None = None


@dataclass
class LeadRecord:
    """What the record says about a scored lead: the best visible model and
    its rolling rain accuracy, or no ranking on a thin record."""

    lead_time_days: int
    best_model: str | None = None
    rain_pct: float | None = None
    checks: int = 0


@dataclass
class OutlookInputs:
    days: list[ExtendedDay] = field(default_factory=list)
    today_high_c: float | None = None
    today_wind_kmh: float | None = None
    # The served call per scored lead, keyed by the lead as a string.
    served: dict[str, dict] = field(default_factory=dict)
    records: list[LeadRecord] = field(default_factory=list)
    met_service_name: str | None = None
    met_service_day3_rain: bool | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> "OutlookInputs":
        return cls(
            days=[ExtendedDay(**d) for d in raw.get("days", [])],
            today_high_c=raw.get("today_high_c"),
            today_wind_kmh=raw.get("today_wind_kmh"),
            served={str(k): v for k, v in (raw.get("served") or {}).items()},
            records=[LeadRecord(**r) for r in raw.get("records", [])],
            met_service_name=raw.get("met_service_name"),
            met_service_day3_rain=raw.get("met_service_day3_rain"),
        )


def extended_days(daily: dict | None, models: list[str], today: date) -> list[ExtendedDay]:
    """The day table for Day+1 to Day+7 from the daily arrays — the same
    extraction the scored leads use, so the outlook and the record read one
    set of numbers. A day no model reaches is left out."""
    times = ((daily or {}).get("daily") or {}).get("time") or []
    skies = {row["lead_time_days"]: row["sky"] for row in sky_by_day(daily or {}, models, today=today)}
    out: list[ExtendedDay] = []
    for row in forward_calendar(today):
        lead = row["lead_time_days"]
        if lead not in NEAR_LEADS + FAR_LEADS or row["date"] not in times:
            continue
        predictions = [
            p for p in extract_day_n_predictions_from_daily(daily, times.index(row["date"]), models)
            if p.high_c is not None
        ]
        if not predictions:
            continue
        highs = [p.high_c for p in predictions]
        out.append(
            ExtendedDay(
                lead_time_days=lead,
                date=row["date"],
                day_name=row["day_name"],
                models=[p.model for p in predictions],
                wet_votes=sum(1 for p in predictions if p.rain),
                precip_mm=_round1(mean([p.precip_mm for p in predictions])),
                precip_by_model={p.model: round(p.precip_mm, 1) for p in predictions if p.precip_mm is not None},
                high_c=_round1(mean(highs)),
                high_min_c=min(highs),
                high_max_c=max(highs),
                low_c=_round1(mean([p.low_c for p in predictions])),
                wind_kmh=_round1(mean([p.wind_kmh for p in predictions])),
                thunder=convective_tier([p.peak_cape_jkg for p in predictions if p.model != BEST_MATCH_MODEL_ID]),
                sky=skies.get(lead),
            )
        )
    return out


def lead_records(entries, *, visible_models: list[str], leads=SCORED_LEADS) -> list[LeadRecord]:
    """The best visible model at each scored lead by rolling 30-check rain
    accuracy, over models with at least THIN_RECORD_CHECKS checks."""
    out = []
    for lead in leads:
        candidates = [
            e for e in entries
            if e.lead_time_days == lead and e.model in visible_models
            and e.rolling_30_rain_pct is not None and (e.all_time_checks or 0) >= THIN_RECORD_CHECKS
        ]
        if not candidates:
            out.append(LeadRecord(lead_time_days=lead))
            continue
        best = max(candidates, key=lambda e: (e.rolling_30_rain_pct, e.all_time_checks))
        out.append(LeadRecord(lead, best.model, round(best.rolling_30_rain_pct, 1), best.all_time_checks))
    return out


def describe_extended_outlook(i: OutlookInputs) -> str | None:
    """The outlook as two paragraphs, or None with no day to speak about."""
    by_lead = {d.lead_time_days: d for d in i.days}
    near = [by_lead[n] for n in NEAR_LEADS if n in by_lead]
    far = [by_lead[n] for n in FAR_LEADS if n in by_lead]
    if not near:
        return None

    paragraphs = [_paragraph(_near_parts(i, near, far)), _paragraph(_far_parts(i, near, far))]
    text = "\n\n".join(p for p in paragraphs if p)
    return text or None


# --- The near term ---


def _near_parts(i: OutlookInputs, near: list[ExtendedDay], far: list[ExtendedDay]) -> list[str | None]:
    trend = describe_extended_trend(
        i.today_high_c,
        [d.high_c for d in near],
        [d.precip_mm for d in near],
        [d.day_name for d in near],
        i.today_wind_kmh,
        [d.wind_kmh for d in near],
        day_thunder=[d.thunder for d in near],
        day_after_precip_mm=far[0].precip_mm if far else None,
    )
    last = near[-1]
    return [
        _sentence(trend),
        _sky(near),
        _support(near),
        _windier(near, trend),
        _call(i, last),
        _record(i, last.lead_time_days),
    ]


def _sky(days: list[ExtendedDay]) -> str | None:
    """"Skies partly cloudy Tuesday, then mostly cloudy Wednesday and
    Thursday." Runs of one word merge; one word for the span says so."""
    words = [(d.day_name, d.sky) for d in days if d.sky]
    if not words:
        return None
    if len({w for _, w in words}) == 1:
        if len(words) == len(days) and len(days) > 1:
            return f"Skies {words[0][1].lower()} throughout."
        return f"Skies {words[0][1].lower()} {_name_run([n for n, _ in words])}."

    runs: list[tuple[str, list[str]]] = []
    for name, word in words:
        if runs and runs[-1][0] == word:
            runs[-1][1].append(name)
        else:
            runs.append((word, [name]))
    clauses = [f"{word.lower()} {_name_run(names)}" for word, names in runs]
    return f"Skies {clauses[0]}, then {_join(clauses[1:])}."


def _support(days: list[ExtendedDay]) -> str | None:
    """Rain's support among the models, moving one way across the span —
    the honest form of "increasing chance"."""
    votes = [(d.day_name, d.wet_votes, len(d.models)) for d in days if d.models]
    if len(votes) < 2:
        return None
    counts = [v for _, v, _ in votes]
    rising = all(b >= a for a, b in zip(counts, counts[1:]))
    falling = all(b <= a for a, b in zip(counts, counts[1:]))
    if abs(counts[-1] - counts[0]) < SUPPORT_MOVE_VOTES or rising == falling:
        return None
    first_name, first_votes, first_n = votes[0]
    last_name, last_votes, last_n = votes[-1]
    verb = "gains" if rising else "loses"
    return (
        f"Rain {verb} support through {last_name}, from {_votes(first_votes, first_n)} "
        f"{first_name} to {_votes(last_votes, last_n)} {last_name}."
    )


def _windier(days: list[ExtendedDay], trend: str | None) -> str | None:
    """A day that stands out for wind against its neighbours, by the
    day-over-day noise band; silent when the trend clause already says the
    wind is moving."""
    if trend and ("windier" in trend or "calmer" in trend):
        return None
    winds = [(d.day_name, d.wind_kmh) for d in days if d.wind_kmh is not None]
    if len(winds) < 2:
        return None
    band = WIND_CHANGE_BANDS_KMH[0][0]
    for name, wind in winds:
        others = [w for n, w in winds if n != name]
        if wind - max(others) >= band:
            return f"Windier on {name}, gusts to {wind:.0f} km/h."
    return None


def _call(i: OutlookInputs, day: ExtendedDay) -> str | None:
    """"Thursday's call: rain, 68%, with two of four models wet; highs 29 to
    34 °C." The served probability is the number tomorrow scores; the votes
    say how the models stood behind it."""
    served = i.served.get(str(day.lead_time_days))
    if not served or served.get("rain") is None:
        return None
    words = "rain" if served["rain"] else "dry"
    probability = served.get("rain_probability_pct")
    if probability is not None:
        words += f", {probability}%"
    if day.models:
        words += f", with {_votes(day.wet_votes, len(day.models))} wet"
    highs = _highs(day)
    sentence = f"{day.day_name}'s call: {words}{'; ' + highs if highs else ''}."

    if day.lead_time_days == 3 and i.met_service_day3_rain is not None and i.met_service_name:
        sentence += f" {i.met_service_name} calls it {'wet' if i.met_service_day3_rain else 'dry'}."
    return sentence


def _record(i: OutlookInputs, lead: int) -> str | None:
    record = next((r for r in i.records if r.lead_time_days == lead), None)
    if record is None:
        return None
    if record.best_model is None or record.rain_pct is None:
        return "The record at this lead is too short to rank the models."
    return (
        f"At this lead {short_model_name(record.best_model)} has the best record, "
        f"right {record.rain_pct:.0f}% of the time over {record.checks} checks."
    )


# --- The far term ---


def _far_parts(i: OutlookInputs, near: list[ExtendedDay], far: list[ExtendedDay]) -> list[str | None]:
    if not far:
        return []
    base = near[-1]
    trend = describe_extended_trend(
        base.high_c,
        [d.high_c for d in far],
        [d.precip_mm for d in far],
        [d.day_name for d in far],
        base.wind_kmh,
        [d.wind_kmh for d in far],
        day_thunder=[d.thunder for d in far],
        day_after_precip_mm=None,
    )
    last = far[-1]
    return [
        _sentence(f"further out, {trend}") if trend else None,
        _reach(near, far),
        _solutions(far),
        _call(i, last),
        _record(i, last.lead_time_days),
    ]


def _reach(near: list[ExtendedDay], far: list[ExtendedDay]) -> str | None:
    """Which models still reach the far days, when some have dropped out."""
    full = near[0].models
    for d in far:
        if len(d.models) < len(full):
            kept = [short_model_name(m) for m in d.models]
            if not kept:
                return None
            previous = next((x for x in [*near, *far] if x.lead_time_days == d.lead_time_days - 1), None)
            after = previous.day_name if previous else d.day_name
            return f"Only {_join(kept)} {'reaches' if len(kept) == 1 else 'reach'} past {after}."
    return None


def _solutions(far: list[ExtendedDay]) -> str | None:
    """"ECMWF and ICON are the wetter solutions from Friday to Monday; GFS
    keeps it drier." Named only when the models' totals sit a band apart."""
    totals: dict[str, float] = {}
    for d in far:
        for model, mm in d.precip_by_model.items():
            totals[model] = totals.get(model, 0.0) + mm
    if len(totals) < 2:
        return None
    lo, hi = min(totals.values()), max(totals.values())
    if hi - lo < PRECIP_DISAGREEMENT_MM:
        return None
    midpoint = (hi + lo) / 2
    wetter = [short_model_name(m) for m, t in totals.items() if t >= midpoint]
    drier = [short_model_name(m) for m, t in totals.items() if t < midpoint]
    span = f"from {far[0].day_name} to {far[-1].day_name}" if len(far) > 1 else far[0].day_name
    return (
        f"{_join(wetter)} {'is' if len(wetter) == 1 else 'are'} the wetter "
        f"{'solution' if len(wetter) == 1 else 'solutions'} {span}; "
        f"{_join(drier)} {'keeps' if len(drier) == 1 else 'keep'} it drier."
    )


# --- Shared ---


def _highs(day: ExtendedDay) -> str | None:
    if day.high_min_c is None or day.high_max_c is None:
        return None
    if day.high_max_c - day.high_min_c > 2.0:
        return f"highs {day.high_min_c:.0f} to {day.high_max_c:.0f} °C"
    return f"highs around {day.high_c:.0f} °C"


def _votes(wet: int, total: int) -> str:
    """"two of four models", "both models", "all four models", "no model"."""
    if total == 1:
        return "the one model that reaches it" if wet else "no model"
    if wet == total:
        return "both models" if total == 2 else f"all {_number(total)} models"
    if wet == 0:
        return "no model"
    return f"{_number(wet)} of {_number(total)} models"


_NUMBERS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _number(n: int) -> str:
    return _NUMBERS.get(n, str(n))


def _name_run(names: list[str]) -> str:
    if len(names) <= 2:
        return _join(names)
    return f"{names[0]} to {names[-1]}"


def _paragraph(parts: list[str | None]) -> str:
    return " ".join(p for p in parts if p and phrase_defect(p) is None)


def _sentence(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    text = text[0].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def _round1(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"
