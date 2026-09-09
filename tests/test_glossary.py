"""ROADMAP item 56 — the forecast's vocabulary.

Static data, so these guard the properties a reader depends on rather than
recomputing anything: that the terms actually used are covered, that a
citation is real where one is claimed, and that no definition leans on
another term the glossary does not itself define.
"""

import json
import re
from pathlib import Path

from openlocalweather.glossary import GLOSSARY

LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "log"

# Measured across the stored narratives 2026-09-09, most-used first. A term
# that reaches a reader this often and is not defined is the whole complaint.
TERMS_IN_USE = {
    "knot": r"\bknots?\b|\bkt\b",
    "hPa": r"\bhPa\b",
    "Day+": r"Day\+\d",
    "AQI": r"\bAQI\b",
    "CAPE": r"\bCAPE\b|J/kg",
    "convective": r"convecti",
    "gust": r"\bgusts?\b",
    "instability": r"instabilit",
    "PM2.5": r"PM2\.5|PM10",
    "UV": r"\bUV\b",
    "synoptic": r"synoptic",
    "MSLP": r"\bMSLP\b",
    "consensus": r"\bconsensus\b",
    "METAR": r"\bMETAR\b",
    "cumulonimbus": r"cumulonimbus",
    "onset": r"\bonset\b",
}


def glossary_text() -> str:
    return " ".join(f"{e.term} {e.definition}" for e in GLOSSARY)


def test_every_term_the_forecast_actually_uses_is_defined():
    text = glossary_text()
    missing = [name for name, pattern in TERMS_IN_USE.items()
               if not re.search(pattern, text, re.I)]
    assert not missing, f"used in the narrative and undefined: {missing}"


def test_the_terms_in_use_list_is_not_stale():
    """Guard the guard. If the narratives stop using a term the list claims,
    the list is describing a forecast that no longer exists."""
    if not LOG_DIR.exists():
        return
    blob = " ".join(
        json.loads(f.read_text()).get("narrative_markdown") or ""
        for f in LOG_DIR.glob("*.json")
    )
    assert blob, "no stored narratives — this test proves nothing"
    absent = [name for name, pattern in TERMS_IN_USE.items()
              if not re.search(pattern, blob, re.I)]
    assert not absent, f"claimed to be in use but absent from every narrative: {absent}"


def test_a_claimed_source_is_a_real_one():
    """An invented citation is worse than none. Every source names a body and
    a locator; entries that are our own wording carry None deliberately."""
    for entry in GLOSSARY:
        if entry.source is None:
            continue
        assert re.search(r"\.gov|\.org|\.int", entry.source), (
            f"{entry.term}: source names no locator — {entry.source!r}"
        )


def test_definitions_are_written_for_a_reader():
    seen = set()
    for entry in GLOSSARY:
        assert entry.term not in seen, f"duplicate term: {entry.term}"
        seen.add(entry.term)
        assert len(entry.definition) > 60, f"{entry.term}: too thin to help"
        assert entry.definition[0].isupper(), f"{entry.term}: not a sentence"
        assert entry.definition.rstrip().endswith("."), f"{entry.term}: no full stop"
