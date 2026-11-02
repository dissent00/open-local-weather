"""The writer: the prompt, the audit and the composition — ROADMAP item 191,
step (b), 2026-10-10.

THE OPERATOR'S RULE: "if the LLM responds, we see that section, if not we
get it from code. For all sections." So the writer answers one Markdown
string per section (`WriteUpResponse`), each field is audited alone, and
the page is composed section by section: the model's where it answered and
passed, code's otherwise, with a sign-off saying which was which.

THE AUDIT IS STRICT, decided 2026-10-05: the only transformations it takes
are the ones code itself performs — unit conversions, the page's display
precision, times and day names. Any other figure is a rejection of that
section, which falls to code. Weather does not round itself. Measured
before trusting it: `tools/measure_audit.py` over the stored narratives
beside their briefs, and hand-mutated figures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openlocalweather.defaults import MM_PER_INCH
from openlocalweather.brief import (
    SECTION_CONFIDENCE,
    SECTION_EXTENDED,
    SECTION_SECONDARY,
    SECTION_SEVERE,
    SECTION_SYNOPTIC,
    SECTION_TODAY,
    SECTIONS,
    TIER_FULL,
    BriefInputs,
    render_brief,
)
from openlocalweather.floor import (
    BOATERS_HEADING,
    CONFIDENCE_HEADING,
    DISCUSSION_HEADING,
    EXTENDED_HEADING,
    SEVERE_HEADING,
    SYNOPTIC_HEADING,
    TODAY_HEADING,
)
from openlocalweather.llm.fallback import FallbackProvider
from openlocalweather.llm.schema import WriteUpResponse
from openlocalweather.models import fahrenheit_figure, inches_figure, knots_figure
from openlocalweather.outlook import MODEL_SHORT_NAMES
from openlocalweather.phrasing import phrase_defect
from openlocalweather.tiles import KMH_PER_KNOT

# Word caps per section, set near the record's 90th percentile over 46
# Gemini write-ups (2026-10-10: today p90 154, extended 141, severe 86,
# boaters 94, synoptic 141, confidence 207). A cap is a ceiling the audit
# enforces, so it refuses the tails: the 300-to-400-word confidence notes
# were the ones reciting raw figures.
#
# TODAY IS 125 SINCE 2026-10-29: the 155 held the observed line, 28 to 30
# words, which code now writes (`strip_observed_line`). Without it the
# model's own Today ran 66 to 104 words over the 7 that passed.
WORD_CAPS = {
    SECTION_TODAY: 125,
    SECTION_EXTENDED: 140,
    SECTION_SEVERE: 90,
    SECTION_SECONDARY: 95,
    SECTION_SYNOPTIC: 140,
    SECTION_CONFIDENCE: 200,
}

# The sections a reader acts on: no pipeline words there (the old prompt's
# rule, now checked), and no model names in the two that never carried them.
# The Extended Outlook names models by design: code's own outlook says which
# reach past Sunday and which run wetter (item 190 step 3).
READER_SECTIONS = frozenset({SECTION_TODAY, SECTION_EXTENDED, SECTION_SECONDARY})
NO_MODEL_NAME_SECTIONS = frozenset({SECTION_TODAY, SECTION_SECONDARY})
# What the Gulf timeline's legs are joined by (wind.py).
TIMELINE_JOIN = ", then "
PIPELINE_WORDS = ("calibrated", "consensus", "pre-computed", "precomputed", "blend", "guidance", "the brief")

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
# A figure with the unit it carries, as the page writes them. UNIT-AWARE on
# purpose: the first cut allowed every brief figure's every conversion, and
# with two hundred figures in a brief that let "35°C" through as some
# other number's knots — measured 2026-10-10, 15 of 29 mutated figures
# caught. A unit names which conversions apply; a bare figure matches only
# a bare figure or a rounding of one.
_UNIT_NUMBER = re.compile(
    r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(°\s?C|°\s?F|km/h|kt\b|mm\b|\bin\b|%|hPa|J/kg)?", re.I
)
_UNIT_KEYS = {"°c": "c", "° c": "c", "°f": "f", "° f": "f", "km/h": "kmh", "kt": "kt", "mm": "mm", "in": "in", "%": "pct", "hpa": "hpa", "j/kg": "jkg"}
_MODEL_IDS = tuple(MODEL_SHORT_NAMES) + ("kenya_met", "olw_blend", "olw_code_blend")
BEARING_SECTIONS = (SECTION_TODAY, SECTION_SECONDARY)
_BEARING = re.compile(r"\b(?:north|south|east|west)(?:-?(?:north|south|east|west)){0,2}(?:erly)?\b", re.I)


def _bearing_key(word: str) -> str:
    return word.lower().removesuffix("erly")


def sections_to_ask(enabled: list[str] | tuple[str, ...], inputs: BriefInputs) -> list[str]:
    """The enabled sections that apply today: `severe` only while the
    convective flag is live, `secondary` only where a point is named."""
    out = []
    for section in SECTIONS:
        if section not in enabled:
            continue
        if section == SECTION_SEVERE and not (inputs.instability or {}).get("convective"):
            continue
        if section == SECTION_SECONDARY and not inputs.secondary_name:
            continue
        out.append(section)
    return out


def section_heading(section: str, *, secondary_name: str | None) -> str:
    return {
        SECTION_TODAY: TODAY_HEADING,
        SECTION_EXTENDED: EXTENDED_HEADING,
        SECTION_SEVERE: SEVERE_HEADING,
        SECTION_SECONDARY: BOATERS_HEADING.format(name=secondary_name or ""),
        SECTION_SYNOPTIC: SYNOPTIC_HEADING,
        SECTION_CONFIDENCE: CONFIDENCE_HEADING,
    }[section]


# --- the prompt -------------------------------------------------------------


def build_writer_prompt(
    sections: list[str], *, place: str, secondary_name: str | None, met_service_name: str | None
) -> str:
    """The system prompt: the job, the output contract, the checked rules
    and one paragraph per section asked for. Short on purpose — the brief
    carries the facts, and every rule here is also a check in code."""
    fields = ", ".join(f'"{s}"' for s in sections)
    caps = "; ".join(f"{s} {WORD_CAPS[s]} words" for s in sections)
    met = met_service_name or "the national met service"
    guides = {
        SECTION_TODAY: (
            "TODAY'S FORECAST (\"today\"): open on a weather condition or a temperature, never on a time or the "
            "sun. What is still ahead, as the ISSUED line and WINDOWS say; the thunder phrase verbatim where "
            "one is given; the sky in SKY's words for those hours; the WIND phrase verbatim; gusts in km/h "
            "as the brief writes them; the UV peak; air quality from the ground "
            "stations where given, the model estimate otherwise. Nothing about what has already happened: "
            "code adds the OBSERVED SO FAR line after your text. Nothing about humidity, feels-like or "
            "visibility: nothing measures them."
        ),
        SECTION_EXTENDED: (
            "EXTENDED OUTLOOK (\"extended\"): the NEXT THREE DAYS phrase verbatim first; the sky in SKY BY "
            "DAY's words; the days from DAYS AHEAD, rain as the models' count (\"three of four models wet\") "
            "and thunder in the day's own word, possible staying possible; the Day+3 and Day+7 calls from "
            "THE CALL; highs as the range when the models spread; the RECORD line where given."
        ),
        SECTION_SEVERE: (
            "SEVERE WEATHER (\"severe\"): thunder ahead from PEAK CAPE AHEAD, per model, with the hazard any "
            "thunderstorm brings: sudden gusts well above the forecast wind. A warning from the met "
            "service's bulletin where it carries one; otherwise none."
        ),
        SECTION_SECONDARY: (
            f"{(secondary_name or 'THE SECONDARY POINT').upper()} (\"secondary\"): the wind timeline's bearings and figures verbatim, the "
            "peak gust, and the thunderstorm gust hazard where thunder is possible."
        ),
        SECTION_SYNOPTIC: (
            "SYNOPTIC OVERVIEW (\"synoptic\"): the LARGE SCALE statements as given, then the BASIN PRESSURE "
            "line, then the pressure trend from THE CALL. Lower pressure lies toward a direction; never a "
            "centre, a track, a speed of approach or a front."
        ),
        SECTION_CONFIDENCE: (
            "FORECASTER CONFIDENCE NOTES (\"confidence\"): what REVIEW and RECORD say about today's models, "
            "and where THE CALL's figures sit against MODELS TODAY; name a model sitting on the wrong side "
            f"of its own record; {met}'s own figures and whether they agree with the call, or that no "
            "bulletin came; DATA SUFFICIENCY in substance. Never how the call weighed the models, and "
            "never the first person."
        ),
    }
    paragraphs = "\n\n".join(guides[s] for s in sections)
    return f"""You write the daily forecast's prose for {place}. Everything you may say is in the brief that follows this message; the figures were decided in code and are already on the page. Write for a reader deciding what to do next.

RETURN JSON with one field per section asked for: {fields}. Each field is a short Markdown passage with no heading, since the page adds the headings. Leave a field null rather than pad it. Caps: {caps}.

RULES CHECKED IN CODE. A section that breaks one is dropped and the reader gets code's version of it.
1. Every number you write is in the brief, or is one of its figures converted (Celsius to Fahrenheit, km/h to knots, mm to inches) or rounded as the page shows it. Never a figure of your own: no averages, no "around 35", no chance the brief does not give.
2. A phrase marked verbatim is used whole or not at all.
3. Model names as the brief names them, never an id such as gfs_seamless; none at all in today or the secondary point's section.
4. No pipeline words in today, extended or the secondary point's section: calibrated, consensus, pre-computed, blend, guidance, the brief.
5. One value per quantity per section: the call's high is the day's high, said once.

{paragraphs}

Plain prose in complete sentences: no lists, no emojis, no headings. Temperatures in °C, wind in km/h, rain in mm: the page adds °F, knots and inches itself."""


# --- the audit --------------------------------------------------------------


def allowed_numbers(brief: str) -> dict[str, set[float]]:
    """The figures a section may write, by the unit it writes them with.

    Every unit's set holds every figure in the brief, with the roundings the
    page shows, because the brief's tables give temperatures, gusts and
    amounts bare under a unit-less header. What a unit adds is the
    conversions INTO it that code itself performs: Fahrenheit from any
    figure read as Celsius, knots from any read as km/h, inches from any
    read as millimetres. A conversion never lands in the source units, so
    "35°C" passes only if 35 is in the brief, never because some gust
    divides to 35 knots — the looseness the first cut had (15 of 29 mutated
    figures caught, 2026-10-10)."""
    bare: set[float] = set()
    for value, _ in _figures(brief):
        bare.update({value, float(round(value)), round(value, 1)})
    by_unit = {k: set(bare) for k in ("bare", "c", "f", "kmh", "kt", "mm", "in", "pct", "hpa", "jkg")}
    for value in bare:
        f = value * 9 / 5 + 32
        by_unit["f"].update({float(round(f)), round(f, 1)})
        kt = value / KMH_PER_KNOT
        by_unit["kt"].update({float(round(kt)), round(kt, 1)})
        inches = value / MM_PER_INCH
        by_unit["in"].update({round(inches, 1), round(inches, 2)})
    return by_unit


def _figures(text: str) -> list[tuple[float, str | None]]:
    out = []
    for token, unit in _UNIT_NUMBER.findall(text):
        value = _value(token)
        if value is None:
            continue
        out.append((value, _unit_key(token, unit)))
    return out


def _unit_key(token: str, unit: str) -> str | None:
    """The unit a figure is written with, or None when bare. "in" counts as
    inches only after a decimal — "0.09 in" — never in "2 in the afternoon"."""
    key = _UNIT_KEYS.get(unit.lower().replace(" ", "")) if unit else None
    if key == "in" and "." not in token:
        return None
    return key


# TYPOGRAPHY A MODEL WRITES THAT THE PAGE DOES NOT: gpt-oss on Groq
# (2026-10-12) put a non-breaking hyphen in every compound bearing and a
# narrow no-break space before every unit, and eight locked phrases were
# refused for the hyphen alone. Mapped to the plain forms before the audit
# and on the page; none of these changes a figure or a word.
_TYPOGRAPHY = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",
})


# AN EN DASH AS A MINUS SIGN: "Trend \u20131.6 hPa" (gpt-oss, probe of
# 2026-10-29) read as an unsigned 1.6. Not mapped whole above because it is
# mostly a range: 285 of 287 en dashes before a digit in the stored texts
# followed a digit, letter or degree sign ("29°C\u201331"); the 2 minus signs
# followed a space. Only that shape becomes "-".
# Before it, "\u20130.5 hPa" against a brief's +0.5 passed with the wrong sign.
# ASCII digits on purpose: Python's \d takes non-ASCII digits and Dart's
# does not, and 46 of 3,000 swept strings came out apart until both
# said [0-9].
_EN_DASH_MINUS = re.compile(r"(?<![^\s(])\u2013(?=[0-9])")


# A NARROW SPACE GROUPING THOUSANDS: "1\u202f710 J/kg" (gpt-oss, probe of
# 2026-10-29) became "1 710" above and read as 1 and 710, each checked alone
# — refusing a right figure, and able to pass a wrong one whose halves the
# brief carried. Removed before the map, so the figure reads as the brief
# writes it. Between a digit and exactly three; a unit after it is spacing.
_THOUSANDS_SPACE = re.compile(r"(?<=[0-9])\u202f(?=[0-9]{3}(?![0-9]))")


def normalise_prose(text: str) -> str:
    return _EN_DASH_MINUS.sub("-", _THOUSANDS_SPACE.sub("", text).translate(_TYPOGRAPHY))


# THE SECOND UNIT IS CODE'S, 2026-11-02. The model writes °C, km/h and mm;
# the page shows each with its conversion in brackets, as code's own text
# does. Measured before: since 10-11 the model wrote °C alone 121 times
# against 46 pairs, km/h alone 24, mm alone 40 against 1, though the prompt
# asked for both. The model's figure is kept as written; the bracket is
# code's, except where the model's own bracket is right.
#
# A MODEL'S PAIR STAYS WHEN ITS SECOND FIGURE IS WITHIN ONE DISPLAY STEP of
# code's conversion of the first. The brief's pairs convert from the TRUE
# value: "29 km/h (15 kt)" is between 28.5 and 28.7 km/h, and recomputing from 29
# printed 16 kt in the first cut (2026-10-30's Gulf section) — rounding
# twice, the bug `models.format_temp_high_low` exists to prevent. Rounding
# the first figure moves the second by less than one step, so further off
# is a mis-conversion and code's replaces it.
_FIG = r"-?\d+(?:\.\d+)?"
_JOIN = r"\s+to\s+|–|-"
_RANGE = rf"(?<![\w.:])(?P<a>{_FIG})(?:(?P<j>{_JOIN})(?P<b>{_FIG}))?"
_SECOND = rf"{_FIG}(?:\s*(?:{_JOIN})\s*{_FIG})?"
# The slash pair and the bracket pair each name their group, since a name
# may appear once per pattern and Dart has no conditional group.
_TEMP = re.compile(
    _RANGE + rf"\s?°\s?C(?:\s*/\s*(?P<slash>{_SECOND})\s?°\s?F|\s*\(\s*(?P<second>{_SECOND})\s?°\s?F\s*\))?"
)
_WIND = re.compile(_RANGE + rf"\s?km/h(?:\s*\(\s*(?P<second>{_SECOND})\s?(?:kt|knots?)\s*\))?")
_RAIN = re.compile(_RANGE + rf"\s?mm\b(?:\s*\(\s*(?P<second>{_SECOND})\s?(?:in|inch|inches)\s*\))?")


def complete_units(text: str) -> str:
    """The model's text with every temperature, wind speed and rain amount
    carrying its second unit — "30 °C" reads "30°C (86°F)"."""
    text = _TEMP.sub(lambda m: _completed(m, "°C", "°F", fahrenheit_figure), text)
    text = _WIND.sub(lambda m: _completed(m, " km/h", " kt", lambda v, d: knots_figure(v)), text)
    return _RAIN.sub(lambda m: _completed(m, " mm", " in", lambda v, d: inches_figure(v)), text)


def _completed(m: re.Match, unit: str, second_unit: str, convert) -> str:
    figures = [f for f in (m.group("a"), m.group("b")) if f is not None]
    joiner = " to " if m.group("j") and m.group("j").strip() == "to" else (m.group("j") or "")
    converted = [convert(float(f), _decimals(f)) for f in figures]
    pair = m.groupdict().get("slash") or m.group("second") or ""
    # Split on a joiner after a digit, so "29-31" is two figures, not 29 and -31.
    written = [w for w in re.split(rf"(?<=\d)\s*(?:{_JOIN})\s*", pair) if w]
    if len(written) == len(converted) and all(_within_a_step(w, c) for w, c in zip(written, converted)):
        converted = written

    # Inside a bracket already, the second unit takes square ones rather
    # than nest a round pair: "(0.8–6.1 mm [0.03–0.24 in])".
    before = m.string[:m.start()]
    opened, closed = ("[", "]") if before.count("(") > before.count(")") else ("(", ")")
    return f"{joiner.join(figures)}{unit} {opened}{joiner.join(converted)}{second_unit}{closed}"


def _decimals(figure: str) -> int:
    return len(figure.split(".")[1]) if "." in figure else 0


def _within_a_step(written: str, converted: str) -> bool:
    step = 10 ** -max(_decimals(written), _decimals(converted))
    return abs(float(written) - float(converted)) <= step + 1e-9


def strip_observed_line(text: str, line: str | None) -> str:
    """The model's text without an exact copy of the observed line, which
    code appends itself. A part of the line is left for the lock to refuse:
    stripping a rewording would hide a second, different account of the
    morning beside code's."""
    if not line or line not in text:
        return text
    return re.sub(r"[ \t]{2,}", " ", text.replace(line, "")).strip()


def audit_section(section: str, text: str, brief: str, inputs: BriefInputs) -> list[str]:
    """Why a section is not publishable, or [] when it is. Each reason is a
    short clause for the ledger; the first is enough to drop the section."""
    defects: list[str] = []
    body = normalise_prose(text).strip()
    if section == SECTION_TODAY:
        body = strip_observed_line(body, inputs.observed_so_far)
    if not body:
        return ["empty"]
    if re.search(r"^\s*#", body, flags=re.M):
        defects.append("carries a heading")

    words = len(body.split())
    if words > WORD_CAPS[section]:
        defects.append(f"{words} words, cap {WORD_CAPS[section]}")

    allowed = allowed_numbers(brief)
    strays = []
    for token, unit in _UNIT_NUMBER.findall(body):
        value = _value(token)
        key = _unit_key(token, unit)
        permitted = allowed[key] if key else allowed["bare"]
        if value is not None and value not in permitted:
            strays.append(f"{token}{unit}" if key else token)
    if strays:
        defects.append("figures not in the brief: " + ", ".join(sorted(set(strays))[:6]))

    for phrase in _locked_phrases(section, inputs):
        if _uses_part_of(body, phrase) and phrase.lower() not in body.lower():
            defects.append(f"phrase not verbatim: {phrase[:40]}")
    if section == SECTION_TODAY:
        defects.extend(_shift_defects(body, inputs.wind_shift))
    if section == SECTION_SECONDARY:
        defects.extend(_pair_defects(body, (inputs.secondary_wind or {}).get("timeline") or ""))

    lowered = body.lower()
    if any(model_id in lowered for model_id in _MODEL_IDS):
        defects.append("names a model id")
    if section in NO_MODEL_NAME_SECTIONS:
        names = [n for n in MODEL_SHORT_NAMES.values() if re.search(rf"\b{re.escape(n)}\b", body)]
        if names:
            defects.append("names a model in a reader's section: " + ", ".join(names))
    if section in READER_SECTIONS:
        words_found = [w for w in PIPELINE_WORDS if re.search(rf"\b{re.escape(w)}\b", lowered)]
        if words_found:
            defects.append("pipeline words: " + ", ".join(words_found))

    # A BEARING IS A FIGURE — 2026-10-13. A lock on a single word cannot
    # catch "south-southwesterly" written as "southwesterly", because a
    # phrase not used is not a phrase misused. Every compass word in a
    # wind section is checked against the brief's, as the numbers are;
    # "northerly" and "north" are one bearing.
    if section in BEARING_SECTIONS:
        allowed = {_bearing_key(b) for b in _BEARING.findall(brief)}
        strays = sorted({b for b in _BEARING.findall(body) if _bearing_key(b) not in allowed}, key=str.lower)
        if strays:
            defects.append("bearings not in the brief: " + ", ".join(strays))

    for paragraph in body.split("\n\n"):
        reason = phrase_defect(paragraph.strip())
        if reason:
            defects.append(f"shape: {reason}")
            break

    return defects


def _value(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _locked_phrases(section: str, i: BriefInputs) -> list[str]:
    """The composed phrases this section may quote, which it quotes whole."""
    # The Gulf timeline by its bearings and figure pairs — 2026-10-12 and
    # 13: Groq's gpt-oss quoted every figure and wrote the legs its own way
    # ("Overnight winds are 6 km/h (3 kt) gusting 8 km/h (5 kt)"), and a
    # leg locked whole refused 13 of 16 days. A leg's bearing and its
    # sustained-and-gust pair are what it means; the anchor wording is not.
    # Its figure pairs are checked by `_pair_defects` since 2026-11-02, its
    # bearings here. A leg not in wind.py's shape is no longer locked whole;
    # wind.py composes every leg in that shape.
    timeline = (i.secondary_wind or {}).get("timeline") or ""
    shift = i.wind_shift if i.wind_shift and not _SHIFT_PART.search(i.wind_shift) else None
    phrases = {
        SECTION_TODAY: [(i.instability or {}).get("timing"), shift, i.observed_so_far],
        SECTION_EXTENDED: [i.next_three_days],
        SECTION_SECONDARY: [m.group("bearing") for leg in timeline.split(TIMELINE_JOIN) if (m := _LEG.match(leg)) and m.group("bearing")],
    }.get(section, [])
    return [p for p in phrases if p]


# LOCKED BY MEANING, NOT BY CONNECTOR — 2026-11-02. Every Gulf and wind-shift
# refusal on record (2 live and 2 probe for the pairs, 1 and 1 for the
# shift) had every figure and bearing right and its own connector: "gusting
# to 12 km/h", "with gusts to 18 km/h", "shift from a north-northeasterly
# overnight to a south-southwest by midday". So a pair is checked as the
# sustained figure and its gust, and a shift as the bearing at each time:
# whatever the section writes in that shape must be the brief's. That also
# closes a hole: a pair written in km/h only matched no part of the old lock,
# so "12 km/h gusting 7 km/h" — swapped — passed.
_SHIFT_WHEN = r"overnight|by midday|into the evening"
_SHIFT_PART = re.compile(rf"(?P<bearing>[a-z][a-z-]*)\s+(?P<when>{_SHIFT_WHEN})\b", re.I)
_GUST_CONNECTOR = re.compile(r"\s*\b(?:with\s+)?gust(?:s|ing)?(?:\s+up)?(?:\s+to)?\b\s*", re.I)
_SECOND_UNIT = re.compile(r"\s*[(\[]\s*-?\d+(?:\.\d+)?\s?(?:kt|knots?)\s*[)\]]", re.I)
_WRITTEN_PAIR = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?km/h gusting (\d+(?:\.\d+)?)\s?km/h\b")


def _shift_defects(body: str, shift: str | None) -> list[str]:
    """Each bearing written at one of the shift's times must be the brief's
    bearing for that time; "northerly" and "north" are one bearing."""
    if not shift:
        return []
    brief = {m.group("when").lower(): _bearing_key(m.group("bearing")) for m in _SHIFT_PART.finditer(shift)}
    wrong = [
        f"{m.group('bearing')} {m.group('when')}" for m in _SHIFT_PART.finditer(body)
        if m.group("when").lower() in brief and _BEARING.fullmatch(m.group("bearing"))
        and _bearing_key(m.group("bearing")) != brief[m.group("when").lower()]
    ]
    return [f"wind shift not as the brief has it: {w}" for w in wrong]


def _pair_defects(body: str, timeline: str) -> list[str]:
    """Each "X km/h gusting Y km/h" written, knots aside and whatever the
    connector, must be one of the timeline's pairs."""
    if not timeline:
        return []
    brief = set(_WRITTEN_PAIR.findall(_canonical_pairs(timeline)))
    written = _WRITTEN_PAIR.findall(_canonical_pairs(body))
    return [f"gust pair not in the brief: {x} km/h gusting {y} km/h" for x, y in written if (x, y) not in brief]


def _canonical_pairs(text: str) -> str:
    return _GUST_CONNECTOR.sub(" gusting ", _SECOND_UNIT.sub("", text))


_LEG = re.compile(r"^(?:(?P<bearing>[a-z-]+) )?(?:overnight|by midday|into the evening) at (?P<figures>.+)$")


def _uses_part_of(text: str, phrase: str, window: int = 4) -> bool:
    words = phrase.lower().split()
    lowered = text.lower()
    return any(" ".join(words[k:k + window]) in lowered for k in range(0, max(1, len(words) - window + 1)))


# --- the composition --------------------------------------------------------


def compose_write_up(
    answers: dict[str, str | None],
    verdicts: dict[str, list[str]],
    code_sections: dict[str, str],
    enabled: list[str],
    *,
    secondary_name: str | None,
    model_name: str | None,
    sign_off_line: str,
    observed_line: str | None = None,
) -> tuple[str, dict[str, str]]:
    """The page's Markdown and who wrote each section: the model's text where
    it answered and the audit passed, code's otherwise, a section with
    neither absent. The model's Today ends with `observed_line`, written by
    code. The sign-off names the sections the model wrote."""
    parts: list[str] = []
    sources: dict[str, str] = {}
    discussion_open = False
    for section in SECTIONS:
        if section not in enabled:
            continue
        answer = normalise_prose(answers.get(section) or "").strip()
        if answer and not verdicts.get(section):
            text, source = answer, "llm"
            # Code's line, after the model's text, as the floor ends Today.
            text = complete_units(answer)
            if section == SECTION_TODAY and observed_line:
                text = f"{complete_units(strip_observed_line(answer, observed_line))} {observed_line}"
        elif code_sections.get(section):
            text, source = code_sections[section], "code"
        else:
            continue
        if section in (SECTION_SYNOPTIC, SECTION_CONFIDENCE) and not discussion_open:
            parts.append(DISCUSSION_HEADING)
            discussion_open = True
        parts.append(f"{section_heading(section, secondary_name=secondary_name)}\n\n{text}")
        sources[section] = source

    written = [section_heading(s, secondary_name=secondary_name).lstrip("# ") for s, src in sources.items() if src == "llm"]
    if written and model_name:
        credit = f"{_join(written)} by {model_name}; the rest written by code."
    else:
        credit = sign_off_line
    parts.append(credit)
    return "\n\n".join(parts) + "\n", sources


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


@dataclass(frozen=True)
class WriterAsk:
    """What one stored day puts to the writer: the sections to ask for,
    the brief, and the prompt that asks for them."""

    sections: list[str]
    brief: str
    system_prompt: str


def brief_inputs(location, entry, user_prompt: str) -> BriefInputs:
    """The brief's inputs for one stored day under a deployment's config —
    the archived user prompt parsed, the secondary point and the met
    service named as the config names them."""
    return BriefInputs.from_user_prompt(
        user_prompt, entry,
        secondary_name=_secondary_name(location),
        met_service_name=location.local_bulletin_source_name or None,
        met_service_model_id=location.local_bulletin_model_id or None,
    )


def writer_ask(location, inputs: BriefInputs) -> WriterAsk | None:
    """The ask for one day, or None when no enabled section is askable —
    shared by `olw write-up` and `tools/probe_writer.py`, so the probe
    measures the request production sends."""
    sections = sections_to_ask(location.write_up_sections, inputs)
    if not sections:
        return None
    return WriterAsk(
        sections=sections,
        brief=render_brief(inputs, tier=TIER_FULL, sections=sections),
        system_prompt=build_writer_prompt(
            sections, place=location.primary_place_name, secondary_name=_secondary_name(location),
            met_service_name=location.local_bulletin_source_name or None,
        ),
    )


def _secondary_name(location) -> str | None:
    secondary = location.secondary_point
    return secondary.name if secondary.enabled and secondary.name else None


def ask_writer(provider, system_prompt: str, user_prompt: str, accept) -> WriteUpResponse:
    """One accepted answer from the first link that gives one — the chain
    puts each link's answer to `accept` and moves on past a refusal; a
    single provider is asked once and its answer put to `accept` here."""
    if isinstance(provider, FallbackProvider):
        return provider.generate(system_prompt, user_prompt, WriteUpResponse, accept=accept)

    answer = provider.generate(system_prompt, user_prompt, WriteUpResponse)
    accept(answer)
    return answer


def model_display_name(model_id: str) -> str:
    """The sign-off's name for a model: "gemini-3.6-flash" reads "Gemini 3.6
    Flash", a gateway's "vendor/model:free" reads as its model."""
    name = model_id.split("/")[-1].split(":")[0]
    if name.startswith("gemini"):
        return " ".join(w.capitalize() if not w[0].isdigit() else w for w in name.split("-"))
    return name
