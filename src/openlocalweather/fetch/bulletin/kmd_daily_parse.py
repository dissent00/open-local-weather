"""Decoding Kenya Met's daily forecast into a scoreable prediction.

The point of this module is that it needs no LLM. KMD's daily bulletin
looks like prose, but it is written in a **controlled vocabulary that the
bulletin itself defines**, in a glossary table on its last page:

    Light / Moderate / Heavy / Very Heavy   <5mm / 5-20mm / 21-50mm / >50mm
    Few / Several / Most places             <33% / 33-66% / >66% of area
    Possible / Chance of / Likely /         10-30% / 31-50% / 51-75% /
      Expected / Very Likely / Certain      76-90% / 91-99% / 100%

So this is decoding a documented encoding, not guessing at natural
language, which is what makes a deterministic parser defensible here
rather than brittle. It also keeps the met service on the same footing as
every other model: its prediction is derived in code, and the project's
"all arithmetic in code, never the LLM" rule holds without an extra API
call.

The bulletin also carries a per-county table with numeric max/min
temperatures, so temperature is scored from published numbers rather than
inferred from words at all.

WHAT THIS DELIBERATELY DOES NOT DO: combine the probability and area terms
into a single point-probability. "Light rains expected over few places"
means high confidence of rain somewhere in under a third of a county —
which is not the same as the chance of rain at one airport, and no honest
arithmetic turns one into the other. The rule below uses the probability
term alone, at KMD's own "more probable than not" boundary, and records
the area term separately so the choice stays visible and revisable. If
that rule is wrong, the verification record will show it: this is exactly
the kind of question the accuracy loop exists to answer empirically rather
than by argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# KMD's probability ladder, from its own glossary. The cut is placed at
# "Likely" (51-75%) because that is where KMD itself puts "more probable
# than not" — a forecast the bulletin calls a "chance of" is, by its own
# published definition, under 50%, and scoring it as a rain call would
# systematically over-predict on the met service's behalf.
_PROBABILITY_TERMS: list[tuple[str, int]] = [
    ("very likely", 95),
    ("certain", 100),
    ("expected", 83),
    ("likely", 63),
    ("chance of", 40),
    ("may ", 40),
    ("possible", 20),
]
RAIN_PROBABILITY_CUTOFF_PCT = 50

_AREA_TERMS: list[tuple[str, int]] = [
    ("most places", 80),
    ("several places", 50),
    ("few places", 20),
]

_RAIN_WORDS = ("rain", "shower", "thunderstorm", "drizzle")


@dataclass(frozen=True)
class CountyOutlook:
    """One county's row, decoded but not yet collapsed to a prediction."""

    county: str
    high_c: float | None
    low_c: float | None
    periods: list[str]           # tonight / morning / afternoon, verbatim
    rain_probability_pct: int | None
    area_coverage_pct: int | None

    @property
    def rain(self) -> bool | None:
        """None when the row mentions no rain vocabulary at all — absence of
        evidence, not a confident dry call. Same distinction ModelPrediction
        draws, and for the same reason: a fabricated dry forecast would
        accrue a flattering fake score on a mostly-dry climate."""
        if self.rain_probability_pct is None:
            return False if self.periods else None
        return self.rain_probability_pct > RAIN_PROBABILITY_CUTOFF_PCT


def decode_rain_terms(phrases: list[str]) -> tuple[int | None, int | None]:
    """(probability_pct, area_pct) from any phrases that mention rain.

    Shared with the 5-day parser so both bulletins decode KMD's vocabulary
    through one implementation — two copies would drift, and the whole
    argument for parsing this deterministically is that the encoding is
    fixed and documented.

    Only rain-mentioning phrases contribute, so a confidence term sitting in
    an unrelated clause ("Sunny intervals") can't be misread as qualifying a
    rain call.
    """
    rain_phrases = [p for p in phrases if mentions_rain(p)]
    if not rain_phrases:
        return None, None
    joined = " ".join(rain_phrases)
    # An unqualified rain statement is a positive forecast, not an uncertain
    # one; KMD reserves its hedging vocabulary for when it means it.
    return (_first_term(joined, _PROBABILITY_TERMS) or 83), _first_term(joined, _AREA_TERMS)


def _first_term(text: str, terms: list[tuple[str, int]]) -> int | None:
    lowered = text.lower()
    best: int | None = None
    for term, value in terms:
        if term in lowered and (best is None or value > best):
            best = value
    return best


def mentions_rain(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in _RAIN_WORDS)


def _is_number(cell: str) -> bool:
    """Whole-cell numeric test. Deliberately not "contains a number": a
    forecast period may mention one, and mistaking prose for a temperature
    would be the same class of error this replaced."""
    try:
        float(cell.strip())
        return True
    except ValueError:
        return False


def _number(cell: str | None) -> float | None:
    if not cell:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", cell)
    return float(m.group()) if m else None


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def find_county_row(tables: list[list[list]], county: str) -> list | None:
    """The county table repeats across pages with the same header, so this
    scans every table rather than assuming which page holds a given county.
    Matched case-insensitively on the first cell."""
    # Whitespace is normalised on both sides because the PDF wraps longer
    # county names inside the cell — "Elgeyo\nMarakwet" is one real example
    # — and a fork's config will reasonably write them on one line.
    target = _normalise(county)
    for table in tables:
        for row in table:
            if row and row[0] and _normalise(str(row[0])) == target:
                return row
    return None


def parse_county_outlook(tables: list[list[list]], county: str) -> CountyOutlook | None:
    """Decodes one county's row. Returns None if that county isn't listed —
    which happens, and must read as "no forecast" rather than "no rain"."""
    row = find_county_row(tables, county)
    if row is None:
        return None

    # Read by KIND, not by position. The number of columns varies between
    # issues — the same Kisumu row came back as
    #   ['Kisumu', '30', '19', <tonight>, <morning>, <afternoon>]        (19 Aug)
    #   ['Kisumu', '30', None, None, '19', <tonight>, <morning>, ...]    (20 Aug)
    # because merged cells extract differently from one PDF to the next. Fixed
    # indices silently mistook the minimum temperature for the Tonight
    # forecast, losing the low and shifting every period by one, with no error
    # raised. Numbers are temperatures and prose is a forecast period; that
    # distinction survives a column shift.
    cells = [
        str(c).replace("\n", " ").strip()
        for c in row[1:]
        if c is not None and str(c).strip()
    ]
    numbers = [c for c in cells if _is_number(c)]
    periods = [c for c in cells if not _is_number(c)]
    # Header order is "Maximum" then "Minimum" throughout.
    high = float(numbers[0]) if numbers else None
    low = float(numbers[1]) if len(numbers) > 1 else None

    # Only phrases that actually mention rain contribute a probability —
    # otherwise "Sunny intervals" alongside a rainy period would pull the
    # confidence term off an unrelated clause.
    probability, area = decode_rain_terms(periods)

    return CountyOutlook(
        county=county,
        high_c=high,
        low_c=low,
        periods=periods,
        rain_probability_pct=probability,
        area_coverage_pct=area,
    )
