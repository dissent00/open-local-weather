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

from openlocalweather.brief import (
    SECTION_CONFIDENCE,
    SECTION_EXTENDED,
    SECTION_SECONDARY,
    SECTION_SEVERE,
    SECTION_SYNOPTIC,
    SECTION_TODAY,
    SECTIONS,
    BriefInputs,
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
from openlocalweather.outlook import MODEL_SHORT_NAMES
from openlocalweather.phrasing import phrase_defect
from openlocalweather.tiles import KMH_PER_KNOT

# Word caps per section, set near the record's 90th percentile over 46
# Gemini write-ups (2026-10-10: today p90 154, extended 141, severe 86,
# boaters 94, synoptic 141, confidence 207). A cap is a ceiling the audit
# enforces, so it refuses the tails: the 300-to-400-word confidence notes
# were the ones reciting raw figures.
WORD_CAPS = {
    SECTION_TODAY: 155,
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
PIPELINE_WORDS = ("calibrated", "consensus", "pre-computed", "precomputed", "blend", "guidance", "the brief")
MM_PER_INCH = 25.4

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
            "one is given; the sky in SKY's words for those hours; the WIND phrase verbatim; gusts as "
            "\"39 km/h (21 kt)\"; the UV peak; air quality from the ground stations where given, the model "
            "estimate otherwise; the OBSERVED SO FAR line verbatim at the end, the one place the past "
            "tense is allowed. Nothing about humidity, feels-like or visibility: nothing measures them."
        ),
        SECTION_EXTENDED: (
            "EXTENDED OUTLOOK (\"extended\"): the NEXT THREE DAYS phrase verbatim first; the sky in SKY BY "
            "DAY's words; the days from DAYS AHEAD, rain as the models' count (\"three of four models wet\") "
            "and thunder in the day's own word, possible staying possible; the Day+3 and Day+7 calls from "
            "THE CALL; highs as the range when the models spread; the RECORD line where given."
        ),
        SECTION_SEVERE: (
            "SEVERE WEATHER (\"severe\"): thunder today from PEAK CAPE TODAY, per model, with the hazard any "
            "thunderstorm brings: sudden gusts well above the forecast wind. A warning from the met "
            "service's bulletin where it carries one; otherwise none."
        ),
        SECTION_SECONDARY: (
            f"{(secondary_name or 'THE SECONDARY POINT').upper()} (\"secondary\"): the wind timeline verbatim, the "
            "consensus gust, and the thunderstorm gust hazard where thunder is possible."
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

Plain prose in complete sentences: no lists, no emojis, no headings. Both units for temperatures and rain where the brief gives both; wind as km/h with knots in brackets."""


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


def audit_section(section: str, text: str, brief: str, inputs: BriefInputs) -> list[str]:
    """Why a section is not publishable, or [] when it is. Each reason is a
    short clause for the ledger; the first is enough to drop the section."""
    defects: list[str] = []
    body = text.strip()
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
    phrases = {
        SECTION_TODAY: [(i.instability or {}).get("timing"), i.wind_shift, i.observed_so_far],
        SECTION_EXTENDED: [i.next_three_days],
        SECTION_SECONDARY: [(i.secondary_wind or {}).get("timeline")],
    }.get(section, [])
    return [p for p in phrases if p]


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
) -> tuple[str, dict[str, str]]:
    """The page's Markdown and who wrote each section: the model's text where
    it answered and the audit passed, code's otherwise, a section with
    neither absent. The sign-off names the sections the model wrote."""
    parts: list[str] = []
    sources: dict[str, str] = {}
    discussion_open = False
    for section in SECTIONS:
        if section not in enabled:
            continue
        answer = (answers.get(section) or "").strip()
        if answer and not verdicts.get(section):
            text, source = answer, "llm"
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
