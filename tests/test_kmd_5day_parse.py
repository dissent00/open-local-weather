"""The five-day grid, tested against a real captured bulletin."""

import json
from datetime import date
from pathlib import Path

from openlocalweather.fetch.bulletin.kmd_5day_parse import (
    outlook_for_date,
    outlook_to_prediction,
    parse_county_days,
    parse_header_dates,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "kmd_5day_2026-08-20.json").read_text())
TABLES = FIXTURE["tables"]


def test_reads_five_consecutive_dates_from_the_headers_own_labels():
    """Read from the bulletin, not counted forward from an assumed start, so
    a skipped or reordered day can't shift every column by one."""
    days = parse_county_days(TABLES, "Kisumu")
    assert [d.target_date for d in days] == [
        date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22),
        date(2026, 8, 23), date(2026, 8, 24),
    ]


def test_recovers_temperatures_from_a_block_split_across_a_page_break():
    """The trap in this document. Kisumu's Morning/Afternoon/Night rows end
    one page and its Maximum/Minimum rows begin the next, under a repeated
    header. Parsing each page's table independently yields no temperatures
    for Kisumu at all — or silently attaches them to whichever county starts
    the next page."""
    days = parse_county_days(TABLES, "Kisumu")
    assert [d.high_c for d in days] == [29.0, 31.0, 29.0, 29.0, 30.0]
    assert [d.low_c for d in days] == [18.0, 18.0, 17.0, 18.0, 18.0]


def test_collects_all_three_daily_periods_not_just_the_first():
    """A regression: empty PDF cells arrive as None, and str(None) is
    "None" — truthy, and enough to read a continuation row as a new county
    and truncate the block after its first row."""
    day = outlook_for_date(TABLES, "Kisumu", date(2026, 8, 20))
    assert len(day.periods) == 3
    assert day.periods == ["Sunny intervals.", "Light showers.", "Partly cloudy."]


def test_rain_is_decoded_per_day_not_per_block():
    """Consecutive days in one block differ, so a rule that leaked across
    columns would show up as every day sharing an answer."""
    days = {d.target_date: d.rain for d in parse_county_days(TABLES, "Kisumu")}
    assert days[date(2026, 8, 20)] is True    # "Light showers."
    assert days[date(2026, 8, 21)] is False   # "Sunny intervals." throughout
    assert days[date(2026, 8, 23)] is True    # "Moderate showers."


def test_a_block_stops_at_the_next_county():
    """Siaya sits immediately above Kisumu with a different forecast."""
    siaya = outlook_for_date(TABLES, "Siaya", date(2026, 8, 23))
    kisumu = outlook_for_date(TABLES, "Kisumu", date(2026, 8, 23))
    assert "Light rains." in siaya.periods
    assert "Light rains." not in kisumu.periods
    assert siaya.high_c == 28.0 and kisumu.high_c == 29.0


def test_an_unlisted_county_yields_nothing_rather_than_a_dry_forecast():
    assert parse_county_days(TABLES, "Atlantis") == []
    assert outlook_for_date(TABLES, "Atlantis", date(2026, 8, 20)) is None


def test_a_date_outside_the_bulletin_is_a_miss():
    assert outlook_for_date(TABLES, "Kisumu", date(2026, 9, 1)) is None


def test_prediction_carries_no_onset_at_extended_range():
    """Day+3 has no onset anywhere in this project — the numerical models
    are only fetched at daily resolution that far out. Giving the met
    service one would put it in a column nothing else populates."""
    day = outlook_for_date(TABLES, "Kisumu", date(2026, 8, 23))
    pred = outlook_to_prediction(day, "kenya_met")
    assert pred.onset is None
    assert pred.wind_kmh is None
    assert pred.rain is True
    assert pred.high_c == 29.0


def test_header_date_parsing_ignores_non_date_columns():
    dates = parse_header_dates(["COUNTY", "Time/\nTemperature", "", "", "THURSDAY\n20 AUGUST 2026"])
    assert dates == {4: date(2026, 8, 20)}
