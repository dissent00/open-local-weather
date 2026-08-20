"""Decoding Kenya Met's daily bulletin without an LLM.

Every test runs against a fixture captured from a real KMD PDF
(tests/fixtures/kmd_daily_2026-08-19.json), because the value of this
parser is entirely in whether it handles KMD's actual phrasing rather than
phrasing invented to suit the parser.
"""

import json
from pathlib import Path

import pytest

from openlocalweather.fetch.bulletin.kmd_daily_parse import (
    RAIN_PROBABILITY_CUTOFF_PCT,
    find_county_row,
    mentions_rain,
    parse_county_outlook,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "kmd_daily_2026-08-19.json").read_text())
TABLES = FIXTURE["tables"]


def test_reads_published_temperatures_rather_than_inferring_them():
    """The county table carries real numbers, so temperature never has to be
    guessed from words."""
    outlook = parse_county_outlook(TABLES, "Kisumu")
    assert outlook.high_c == 30.0
    assert outlook.low_c == 19.0


def test_decodes_kmds_own_probability_vocabulary():
    """'Expected' is 76-90% by KMD's published glossary — a rain call."""
    outlook = parse_county_outlook(TABLES, "Kisumu")
    assert outlook.rain_probability_pct == 83
    assert outlook.rain is True
    assert any("light rains expected" in p.lower() for p in outlook.periods)


def test_a_chance_of_rain_is_not_scored_as_a_rain_call():
    """KMD defines 'chance of' as 31-50%: by its own glossary, less likely
    than not. Scoring it as a rain call would over-predict on the met
    service's behalf and then blame it for the miss."""
    outlook = parse_county_outlook(TABLES, "Nyandarua")
    assert outlook.rain_probability_pct == 40
    assert outlook.rain_probability_pct < RAIN_PROBABILITY_CUTOFF_PCT
    assert outlook.rain is False


def test_likely_sits_on_the_other_side_of_the_same_boundary():
    outlook = parse_county_outlook(TABLES, "Bungoma")
    assert outlook.rain_probability_pct == 63
    assert outlook.rain is True


def test_no_rain_vocabulary_at_all_reads_as_a_dry_forecast():
    outlook = parse_county_outlook(TABLES, "Garissa")
    assert outlook.rain_probability_pct is None
    assert outlook.rain is False
    assert outlook.periods, "a dry forecast still has period text"


def test_an_unlisted_county_is_a_miss_not_a_dry_forecast():
    """The distinction ModelPrediction.rain draws, for the same reason: a
    fabricated dry call would earn a flattering fake score on a mostly-dry
    climate."""
    assert parse_county_outlook(TABLES, "Atlantis") is None


def test_county_names_match_across_the_pdfs_internal_line_wrapping():
    """Longer names wrap inside the cell — "Elgeyo\\nMarakwet" is real — and a
    fork's config will write them on one line."""
    assert find_county_row(TABLES, "Elgeyo Marakwet") is not None
    assert find_county_row(TABLES, "elgeyo   marakwet") is not None


def test_probability_is_read_only_from_clauses_that_mention_rain():
    """Rows pair a rainy period with a dry one. Pulling a confidence term off
    an unrelated clause would mis-score the day."""
    outlook = parse_county_outlook(TABLES, "Kisumu")
    dry_periods = [p for p in outlook.periods if not mentions_rain(p)]
    assert dry_periods, "fixture should contain at least one non-rain period"
    assert outlook.rain_probability_pct == 83, "taken from the rainy clause only"


@pytest.mark.parametrize("phrase,expected", [
    ("Sunny intervals.", False),
    ("Partly cloudy, chance of light showers", True),
    ("light rains expected over few places", True),
    ("Morning drizzle", True),
    ("Intermittent cool and cloudy conditions", False),
])
def test_rain_vocabulary_detection(phrase, expected):
    assert mentions_rain(phrase) is expected


def test_the_cutoff_actually_discriminates_across_the_whole_bulletin():
    """A rule that answered the same way for every county would pass all the
    tests above and still be useless."""
    counties = {
        str(r[0]).strip()
        for t in TABLES for r in t
        if r and r[0] and len(r) >= 6 and r[1] and str(r[1]).strip().isdigit()
    }
    outlooks = [parse_county_outlook(TABLES, c) for c in counties]
    wet = [o for o in outlooks if o and o.rain]
    dry = [o for o in outlooks if o and not o.rain]
    assert len(counties) > 40, "fixture should cover most of the country"
    assert wet and dry, "the rule must separate counties, not label them all alike"
    # And the separation must be driven by the threshold, not by rain
    # vocabulary alone — some counties mention rain below the cutoff.
    hedged = [o for o in outlooks if o and o.rain_probability_pct == 40]
    assert hedged, "fixture should contain sub-50% 'chance of' counties"
    assert all(not o.rain for o in hedged)


# The same Kisumu row, as KMD's PDF actually extracted on two consecutive
# days. Column count differs because merged cells extract differently from
# one issue to the next.
ROW_19_AUG = [[[
    "Kisumu", "30", "19",
    "Partly cloudy, chance of light showers\nand thunderstorms few places.",
    "Sunny intervals, light rains expected\nover few places.",
    "Sunny intervals.",
]]]
ROW_20_AUG = [[[
    "Kisumu", "30", None, None, "19",
    "Partly cloudy, light showers and\nthunderstorms few places.",
    "Sunny intervals",
    "Sunny intervals, light showers and\nthunderstorms over few places.",
]]]


def test_column_layout_varies_between_issues_and_both_must_parse():
    """A live regression, caught on the first production run.

    Fixed indices (row[1]=max, row[2]=min, row[3:]=periods) worked on the
    19 Aug bulletin and silently broke on the 20 Aug one: the minimum
    temperature landed in the Tonight slot, the low was lost, and every
    period shifted by one. No error was raised — the stored extract read
    "Min NoneC / Tonight: 19".
    """
    for label, tables in (("19 Aug", ROW_19_AUG), ("20 Aug", ROW_20_AUG)):
        outlook = parse_county_outlook(tables, "Kisumu")
        assert outlook.high_c == 30.0, label
        assert outlook.low_c == 19.0, label
        assert len(outlook.periods) == 3, label
        # No period is a bare number — that was the tell.
        assert not any(p.strip().isdigit() for p in outlook.periods), label


def test_temperatures_are_identified_by_kind_not_position():
    """Numbers are temperatures, prose is a forecast period. That holds
    whatever the column count, which fixed offsets do not."""
    shifted = [[["Kisumu", None, "28", None, None, None, "17", "Light showers."]]]
    outlook = parse_county_outlook(shifted, "Kisumu")
    assert outlook.high_c == 28.0
    assert outlook.low_c == 17.0
    assert outlook.periods == ["Light showers."]


def test_prose_mentioning_a_number_is_not_mistaken_for_a_temperature():
    """The complement. A whole-cell numeric test, not "contains a digit"."""
    tables = [[["Kisumu", "30", "19", "Winds gusting to 25 km/h, light showers."]]]
    outlook = parse_county_outlook(tables, "Kisumu")
    assert outlook.high_c == 30.0 and outlook.low_c == 19.0
    assert outlook.periods == ["Winds gusting to 25 km/h, light showers."]
    assert outlook.rain is True
