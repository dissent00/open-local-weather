"""The brief — ROADMAP item 191. Parsed from the archived user prompt and
the stored day, rendered in two tiers for the sections a deployment
writes. The fixtures are 2026-10-07's prompt cut to the blocks the brief
reads, and that day's entry cut to the fields it takes."""

import json
from pathlib import Path

import pytest

from openlocalweather.brief import (
    DEFAULT_SECTIONS, SECTION_CONFIDENCE, SECTION_EXTENDED, SECTION_SYNOPTIC, SECTION_TODAY, SECTIONS,
    TIER_MINI, BriefInputs, basin_pressure, render_brief,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _inputs(**overrides) -> BriefInputs:
    prompt = (FIXTURES / "user_prompt_2026-10-07.txt").read_text()
    entry = json.loads((FIXTURES / "brief_entry_2026-10-07.json").read_text())
    inputs = BriefInputs.from_user_prompt(
        prompt, entry, secondary_name="Winam Gulf", met_service_name="Kenya Met", met_service_model_id="kenya_met",
    )
    for key, value in overrides.items():
        setattr(inputs, key, value)
    return inputs


def test_the_parser_reads_every_block_the_brief_carries():
    i = _inputs()

    assert i.issued.startswith("It is 06:01.")
    assert i.calendar[0] == {"lead_time_days": "0", "date": "2026-10-07", "day_name": "Wednesday"}
    assert i.windows[0] == "today (06:01-17:02)"
    assert i.recency["hours_old"] == 9.0
    assert i.instability["convective"] is True and i.instability["timing"].startswith("thunder possible")
    assert i.next_three_days.startswith("temperatures and winds much the same through Saturday")
    assert i.sky_anchors == {"early": "Mostly cloudy", "midday": "Mostly cloudy", "evening": "Mostly cloudy"}
    assert i.wind_directions == {"early": "NNE", "midday": "SW", "evening": "S"}
    assert i.wind_shift.startswith("north-northeasterly overnight")
    assert i.secondary_wind["consensus_gust_kmh"] == 30.9
    assert i.peak_uv == {"index": 9.4, "date": "2026-10-07", "source": "gfs_seamless"}
    assert i.calibrated_gust_kmh == 39.3
    assert i.observed_so_far.startswith("As of 06:01, reports through 04:00")
    assert [s["name"] for s in i.ground_aqi_stations] == ["Kisumu Airport", "Ochieng' Avenue, Kisumu Central", "Dunga Beach"]
    assert i.ground_aqi_summary["aqi_max"] == 42 and i.ground_aqi_last_known["aqi"] == 42
    assert i.local_bulletin.startswith("Kenya Meteorological Department (KMD) — forecast for Kisumu")
    # 6 at Day+0 (the met service among them), 5 at Day+3, 5 at Day+7 and
    # the secondary point's 5 at Day+0.
    assert len(i.predictions) == 21 and i.predictions[0]["model"] == "gfs_seamless"
    assert len(i.synoptic_statements) == 3
    assert i.basin_pressure == {"points": 5, "today_min_hpa": 1012.8, "today_max_hpa": 1015.0, "change_72h_hpa": 0.1}
    assert len(i.review_findings) == 14, "the established findings only"
    assert i.data_sufficiency.startswith("Reviewed 56 day(s)")
    assert {r["lead_time_days"] for r in i.track_record} >= {"0", "3", "7"}
    assert i.served_call["today_properties"]["temp_high_c"] == 31.1
    assert i.temp_display == "31°C / 88°F high, 19°C / 67°F low"
    assert len(i.extended_days) == 7


def test_the_inputs_round_trip_through_json():
    i = _inputs()

    assert BriefInputs.from_json(json.loads(json.dumps(i.to_json()))) == i


def test_the_full_brief_fits_the_targets_with_every_section():
    """Item 191's go: p95 at most 4.5K tokens with the writer prompt, at
    the measured 2.5 characters per token. The mini tier is sized for a
    4,096-token on-device budget shared with instructions and output."""
    i = _inputs()
    full = render_brief(i, sections=SECTIONS)
    mini = render_brief(i, tier=TIER_MINI, sections=SECTIONS)

    assert 6_000 < len(full) < 10_000, len(full)
    assert len(mini) < 6_000, len(mini)


def test_sections_choose_blocks():
    i = _inputs()
    today = render_brief(i, sections=(SECTION_TODAY,))
    extended = render_brief(i, sections=(SECTION_EXTENDED,))
    discussion = render_brief(i, sections=(SECTION_SYNOPTIC, SECTION_CONFIDENCE))

    assert "THE CALL" in today and "OBSERVED SO FAR" in today
    assert "MODELS AT DAY+3" not in today and "LARGE SCALE" not in today
    assert "DAYS AHEAD" in extended and "NEXT THREE DAYS" in extended and "RECORD" in extended
    assert "OBSERVED SO FAR" not in extended and "THE CALL" not in extended
    assert "LARGE SCALE" in discussion and "BASIN PRESSURE" in discussion and "REVIEW" in discussion
    assert "MODELS TODAY" in discussion and "DAYS AHEAD" not in discussion


def test_the_mini_tier_drops_what_item_191_said():
    i = _inputs()
    mini = render_brief(i, tier=TIER_MINI, sections=SECTIONS)

    for gone in ("OBSERVED SO FAR", "WINAM GULF WIND", "SKY BY DAY", "UV:", "GUIDANCE:", "LARGE SCALE", "CALENDAR", "WINDOWS"):
        assert gone not in mini, gone
    assert mini.count("\n  - At Day+") == 3, "three findings"
    assert "DATA SUFFICIENCY" not in mini
    assert "THE CALL" in mini and "THUNDER" in mini


def test_model_ids_never_reach_the_brief():
    full = render_brief(_inputs(), sections=SECTIONS)

    for model_id in ("gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless", "best_match", "kenya_met"):
        assert model_id not in full, model_id
    assert "Kenya Met\tyes" in full, "the met service's row carries its name"
    assert "Kenya Met does not forecast at Day+3" in full


def test_whole_numbers_show_without_a_point():
    full = render_brief(_inputs(), sections=SECTIONS)

    assert "320.0" not in full and "\t320\n" in full
    assert "33.5" in full, "a decimal stays a decimal"


def test_the_days_ahead_read_the_day_table():
    full = render_brief(_inputs(), sections=(SECTION_EXTENDED,))

    assert "Thursday (Day+1): 3 of 4 models wet; rain 0.0–4.9 mm by model; high 30–34 °C; low 20 °C; gusts to 31 km/h (17 kt); thunder likely; mostly cloudy" in full
    assert "Day+3 (Saturday): rain, 100%" not in full, "the served leads belong to the call block"


def test_a_missing_block_renders_as_its_absence():
    assert "THUNDER" not in render_brief(_inputs(instability=None), sections=(SECTION_TODAY,))
    quiet = render_brief(_inputs(instability={"convective": False}), sections=(SECTION_TODAY,))
    assert "say nothing about thunder" in quiet
    no_ring = render_brief(_inputs(synoptic_statements=[]), sections=(SECTION_SYNOPTIC,))
    assert "could not be assessed" in no_ring
    no_gulf = render_brief(_inputs(secondary_name=None), sections=SECTIONS)
    assert "WINAM GULF" not in no_gulf and "on Winam Gulf" not in no_gulf
    no_bulletin = render_brief(_inputs(local_bulletin="Unavailable — nothing fetched"), sections=(SECTION_TODAY,))
    assert "no bulletin this run" in no_bulletin


def test_a_long_bulletin_is_cut():
    full = render_brief(_inputs(local_bulletin="x " * 800), sections=(SECTION_TODAY,))

    assert "…" in full and full.count("x") <= 701, "cut near BULLETIN_MAX_CHARS"


@pytest.mark.parametrize("points, expected", [
    ([], None),
    ([{"daily": {"pressure_msl_mean": [1012.8, 1013.5, 1012.0]}}, {"daily": {"pressure_msl_mean": [1015.0, 1014.0, 1013.0]}}],
     {"points": 2, "today_min_hpa": 1012.8, "today_max_hpa": 1015.0, "change_72h_hpa": -1.4}),
    # A series too short for a tendency still places today.
    ([{"daily": {"pressure_msl_mean": [1012.8]}}], {"points": 1, "today_min_hpa": 1012.8, "today_max_hpa": 1012.8, "change_72h_hpa": None}),
])
def test_the_basin_reduces_to_a_range_and_a_tendency(points, expected):
    assert basin_pressure(points) == expected


def test_the_basin_line_names_the_tendency():
    steady = render_brief(_inputs(basin_pressure={"points": 5, "today_min_hpa": 1012.8, "today_max_hpa": 1015.0, "change_72h_hpa": 0.1}), sections=(SECTION_SYNOPTIC,))
    falling = render_brief(_inputs(basin_pressure={"points": 5, "today_min_hpa": 1012.8, "today_max_hpa": 1015.0, "change_72h_hpa": -2.3}), sections=(SECTION_SYNOPTIC,))

    assert "near-steady over three days (+0.1 hPa)" in steady
    assert "falling by 2.3 hPa over three days" in falling


def test_the_default_sections_are_the_floors():
    assert DEFAULT_SECTIONS == ("today", "extended", "severe", "secondary")


def test_an_unknown_tier_is_refused():
    with pytest.raises(ValueError, match="tier"):
        render_brief(_inputs(), tier="huge")
