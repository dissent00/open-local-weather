"""The floor, item 190: code's write-up from the stored entry."""

from pathlib import Path

import pytest

from openlocalweather.floor import (
    SIGN_OFF_WITH_MODEL,
    SIGN_OFF_WITHOUT_MODEL,
    FloorInputs,
    compose_floor,
    compose_floor_for_entry,
)
from openlocalweather.models import DailyLogEntry
from openlocalweather.phrasing import phrase_defect

FIXTURES = Path(__file__).parent / "fixtures"


def _entry(day: str) -> DailyLogEntry:
    """A stored day, frozen: 09-30 is a placeholder day, 09-23 a served one."""
    return DailyLogEntry.model_validate_json((FIXTURES / f"floor_entry_{day}.json").read_text())


def _sentences(text: str) -> list[str]:
    return [
        s.strip() + "."
        for line in text.splitlines()
        if line and not line.startswith("#")
        for s in line.split(". ")
        if s.strip()
    ]


@pytest.mark.parametrize("day", ["2026-09-30", "2026-09-23"])
def test_every_sentence_passes_the_shape_check(day):
    text = compose_floor_for_entry(_entry(day), secondary_name="Winam Gulf")

    for sentence in _sentences(text):
        assert phrase_defect(sentence) is None, sentence


def test_the_floor_is_the_write_up_shape_from_stored_fields():
    text = compose_floor_for_entry(_entry("2026-09-30"), secondary_name="Winam Gulf")

    assert text.startswith("## Today's Forecast\n\n")
    # The day-over-day comparison opens, as the run composed it.
    assert "Slightly warmer than yesterday; winds and cloud little changed." in text
    assert "Dry by day, with thunder possible, peaking this evening again." in text
    assert "32°C / 90°F high, 19°C / 66°F low." in text
    assert "Sky mostly cloudy early, partly cloudy at midday and mostly cloudy in the evening." in text
    # The turn from the anchors, the peak from the served gust.
    assert "Wind NNE early, SSW at midday and WSW in the evening; gusts to 33 km/h (18 kt)." in text
    assert "UV index 9.2 (very high)." in text
    assert "Air quality 103 (unhealthy for sensitive groups) at Kisumu Airport, the highest of 3 stations." in text
    assert "## Extended Outlook\n\nSaturday (Day+3): dry, 28% chance of rain; highs 31 to 34 °C." in text
    assert "Wednesday (Day+7): rain likely, 82% chance; highs 29 to 35 °C." in text
    assert "## Winam Gulf — Conditions for Boaters\n\nPeak gust 30 km/h (16 kt). Any thunderstorm" in text
    assert text.endswith(f"Figures issued 06:01. {SIGN_OFF_WITH_MODEL}\n")
    assert 600 <= len(text) <= 1500


def test_no_secondary_point_means_no_boaters_section():
    text = compose_floor_for_entry(_entry("2026-09-30"), secondary_name=None)

    assert "Conditions for Boaters" not in text
    assert "Peak gust 30" not in text


def test_a_deployment_with_no_model_promises_nothing():
    text = compose_floor_for_entry(_entry("2026-09-30"), model_configured=False)

    assert text.endswith(f"Figures issued 06:01. {SIGN_OFF_WITHOUT_MODEL}\n")
    assert "a discussion follows" not in text


def test_without_a_comparison_the_opener_is_the_served_calls_rain_character():
    """A fork's first day, or a store without actuals: the day-over-day block
    is absent and the rain sentence comes from the served call instead."""
    entry = _entry("2026-09-30").model_copy(update={
        "prediction_rows": [],
        "served_call": {
            # 8 mm: the showery band, which carries the onset; under 5 mm the
            # composer says "largely dry" and names no hour.
            "today_properties": {"rain": True, "onset_hour": "15:00", "precip_mm": 8.0, "rain_expected": "x",
                                 "temp_high_c": 32.0, "temp_low_c": 19.0},
            "extended_properties": [],
        },
    })

    text = compose_floor_for_entry(entry)

    assert "yesterday" not in text
    assert text.startswith("## Today's Forecast\n\nShowery from the afternoon.")
    # The served onset, placed by the sun the entry stores.
    assert "afternoon" in text.split("\n")[2]


def test_a_stored_served_call_decides_the_extended_leads():
    entry = _entry("2026-09-30").model_copy(update={
        "served_call": {
            "today_properties": {},
            "extended_properties": [
                {"lead_time_days": 3, "rain": True, "rain_probability_pct": 55},
                {"lead_time_days": 7, "rain": False, "rain_probability_pct": None},
            ],
        },
    })

    text = compose_floor_for_entry(entry)

    assert "Saturday (Day+3): rain likely, 55% chance; highs 31 to 34 °C." in text
    assert "Wednesday (Day+7): dry; highs 29 to 35 °C." in text


def test_a_thin_day_still_signs_off():
    """Nothing but the temperatures and the stamp: the floor is short, not
    broken, and never a section with nothing under it."""
    entry = _entry("2026-09-30").model_copy(update={
        "prediction_rows": [], "served_call": None, "cloud_anchors": None, "wind_anchors": None,
        "uv_index": None, "air_quality_index": None, "ground_aqi": [], "peak_wind_primary_kmh": None,
        "peak_wind_secondary_kmh": None, "observed_so_far": None,
    })

    text = compose_floor_for_entry(entry, secondary_name="Winam Gulf")

    # No rows at all, so no extended leads either: the section is absent,
    # never a heading over nothing.
    assert text == (
        "## Today's Forecast\n\n32°C / 90°F high, 19°C / 66°F low.\n\n"
        f"Figures issued 06:01. {SIGN_OFF_WITH_MODEL}\n"
    )


def test_the_stored_trend_opens_the_extended_outlook():
    """The run stores the three-day clause (item 190), and the floor puts it
    before the leads; a day without one starts at Day+3."""
    entry = _entry("2026-09-30").model_copy(
        update={"extended_trend": "Warming through Saturday, with showers returning by Monday"}
    )

    text = compose_floor_for_entry(entry)

    assert "## Extended Outlook\n\nWarming through Saturday, with showers returning by Monday. Saturday (Day+3)" in text


def test_the_inputs_round_trip_as_json():
    """The vector's input shape: what `from_entry` reads is what
    `compose_floor` is handed, in both languages."""
    inputs = FloorInputs.from_entry(_entry("2026-09-30"), secondary_name="Winam Gulf")

    assert FloorInputs.from_json(inputs.to_json()) == inputs
    assert compose_floor(inputs) == compose_floor_for_entry(_entry("2026-09-30"), secondary_name="Winam Gulf")
    assert inputs.extended_calls == [
        {"lead_time_days": 3, "rain": False, "rain_probability_pct": 28},
        {"lead_time_days": 7, "rain": True, "rain_probability_pct": 82},
    ]


# ---------------------------------------------------------------------------
# The right-now rule — 2026-10-10, the operator's: when a local source has a
# reading for hours already lived and the models disagree, the page says what
# was measured. The scored row stays as issued (item 140).
# ---------------------------------------------------------------------------


def _station_inputs(**changes) -> FloorInputs:
    base = dict(
        date="2026-10-10", temp_high_low_display="30°C / 86°F high, 20°C / 68°F low",
        issued_local_time="06:01", sunrise="06:24", sunset="18:31",
        served_today={"rain": True, "onset_hour": "16:00", "precip_mm": 6.9, "high_c": 29.6},
        peak_wind_primary_kmh=38.9, station_name="Kisumu Airport", model_configured=False,
    )
    return FloorInputs(**{**base, **changes})


def _today(text: str) -> str:
    body = text.split("## Today's Forecast\n", 1)[1].split("\n## ", 1)[0]
    return body.split("\n\nFigures issued", 1)[0].strip()


def test_the_stations_line_closes_todays_forecast():
    line = "As of 06:01, reports through 05:00: no rain; no thunder; sky 2/8."
    text = compose_floor(_station_inputs(observed_line=line))

    assert _today(text).endswith(line)
    assert "As of" not in _today(compose_floor(_station_inputs()))


@pytest.mark.parametrize("code, observed, sentence", [
    ("onset_already_passed", {"precipitation": True, "precipitation_onset": "14:00"},
     "Rain began at Kisumu Airport from 14:00, ahead of the 16:00 called."),
    ("rain_observed_while_dry_called", {"precipitation": True},
     "Kisumu Airport has already reported rain today, against a dry call; the day is not dry."),
    ("high_already_exceeded", {"high_c": 31.4},
     "Kisumu Airport has already recorded 31.4°C / 88.5°F, above the 29.6°C / 85.3°F called."),
    ("gust_already_exceeded", {"peak_gust_kmh": 46.3},
     "Kisumu Airport has already gusted to 46 km/h (25 kt), above the 39 km/h (21 kt) called."),
])
def test_a_reading_that_contradicts_the_call_is_said_right_after_the_opener(code, observed, sentence):
    served = {"rain": False, "onset_hour": None, "precip_mm": 0.0, "high_c": 29.6} if code == "rain_observed_while_dry_called" else None
    inputs = _station_inputs(station_codes=[code], observed=observed, **({"served_today": served} if served else {}))
    today = _today(compose_floor(inputs))

    assert sentence in today
    assert today.index(sentence) < today.index("30°C / 86°F high"), "before the figures, right after the opener"
    assert phrase_defect(sentence) is None


def test_a_code_without_its_numbers_gets_no_sentence():
    today = _today(compose_floor(_station_inputs(station_codes=["high_already_exceeded"], observed={})))

    assert "already" not in today


def test_an_anchor_the_station_reported_says_so():
    anchors = [
        {"when": "early", "cover": "Mostly clear", "source": "station"},
        {"when": "midday", "cover": "Partly cloudy"},
        {"when": "evening", "cover": "Mostly cloudy"},
    ]
    today = _today(compose_floor(_station_inputs(cloud_anchors=anchors)))

    assert "Sky mostly clear early as reported, partly cloudy at midday and mostly cloudy in the evening." in today


def test_the_station_inputs_round_trip_as_json():
    inputs = _station_inputs(observed={"precipitation": True, "precipitation_onset": "14:00"}, station_codes=["onset_already_passed"], observed_line="So far today: rain.")

    assert FloorInputs.from_json(inputs.to_json()) == inputs


# ---------------------------------------------------------------------------
# The other three sections, in code — item 191 step (c), 2026-10-10.
# ---------------------------------------------------------------------------

ALL = ["today", "extended", "severe", "secondary", "synoptic", "confidence"]


def _six_inputs(**changes) -> FloorInputs:
    base = dict(
        date="2026-10-10", temp_high_low_display="30°C / 86°F high, 20°C / 68°F low", issued_local_time="06:01",
        served_today={"rain": True, "onset_hour": "16:00", "precip_mm": 6.9, "high_c": 31.1},
        peak_wind_primary_kmh=38.9, model_configured=False, enabled_sections=ALL,
        day0_peak_cape_jkg=[320.0, 2030.0, 1950.0, 3440.0],
        models_today=[
            {"model": "GFS", "high_c": 33.5, "rain": False, "wind_kmh": 21.6, "peak_cape_jkg": 320.0},
            {"model": "ECMWF", "high_c": 29.1, "rain": True, "wind_kmh": 27.7, "peak_cape_jkg": 2030.0},
            {"model": "ICON", "high_c": 29.6, "rain": True, "wind_kmh": 34.6, "peak_cape_jkg": 1950.0},
            {"model": "UKMO", "high_c": 31.9, "rain": True, "wind_kmh": 31.0, "peak_cape_jkg": 3440.0},
        ],
        met_service_name="Kenya Met", met_service_call={"high_c": 32.0, "low_c": 20.0, "rain": True},
        synoptic_statements=[
            "Across roughly 2,600 km, pressure is lowest toward the north (1009 hPa) and highest toward the southeast (1016 hPa) — an 8 hPa spread, a moderate large-scale gradient.",
            "Pressure overhead is near-steady.",
            "Sampling is a nine-point ring at 12-degree spacing, so this locates a direction, not a centre or a front.",
        ],
        basin_pressure={"points": 5, "today_min_hpa": 1012.8, "today_max_hpa": 1015.0, "change_72h_hpa": 0.1},
        mslp_trend_24h="+0.5 hPa",
        review_findings=[
            {"kind": "bias", "checks": 56, "claim": "At Day+0, gfs_seamless systematically over-forecasts daytime highs here."},
            {"kind": "ranking", "checks": 45, "claim": "At Day+0, no model is meaningfully better than the others here yet."},
            {"kind": "bias", "checks": 56, "claim": "At Day+0, ukmo_seamless systematically under-forecasts overnight lows here."},
            {"kind": "bias", "checks": 56, "claim": "At Day+0, icon_seamless slightly under-forecasts daytime highs here."},
        ],
        lead_records=[
            {"lead_time_days": 0, "best_model": "ecmwf_ifs025", "rain_pct": 83.3, "checks": 56},
            {"lead_time_days": 3, "best_model": "ecmwf_ifs025", "rain_pct": 76.9, "checks": 53},
            {"lead_time_days": 7, "best_model": None, "rain_pct": None, "checks": 4},
        ],
    )
    return FloorInputs(**{**base, **changes})


def _section(text: str, heading: str) -> str:
    body = text.split(heading + "\n\n", 1)[1]
    return body.split("\n\n## ", 1)[0].split("\n\n### ", 1)[0].split("\n\nFigures issued", 1)[0].strip()


def test_severe_weather_names_each_models_instability_and_the_gust_hazard():
    text = compose_floor(_six_inputs())
    severe = _section(text, "## Severe Weather / Hazard Potential")

    assert severe == (
        "Convective instability today: UKMO 3440 J/kg, ECMWF 2030 J/kg, ICON 1950 J/kg and GFS 320 J/kg. "
        "Thunder likely; any thunderstorm brings sudden gusts well above the 39 km/h (21 kt) forecast."
    )
    for sentence in severe.split(". "):
        assert phrase_defect(sentence.rstrip(".") + ".") is None


def test_severe_weather_is_absent_while_no_model_supports_thunder():
    text = compose_floor(_six_inputs(day0_peak_cape_jkg=[100.0, 200.0], models_today=[]))

    assert "Severe Weather" not in text


def test_the_synoptic_overview_is_the_ring_the_basin_and_the_trend():
    text = compose_floor(_six_inputs())
    synoptic = _section(text, "### Synoptic Overview")

    assert synoptic.startswith("Across roughly 2,600 km, pressure is lowest toward the north")
    assert "Sampling is a nine-point ring at 12-degree spacing, so this locates a direction, not a centre or a front." in synoptic
    assert "Across the basin's 5 points, pressure today sits between 1012.8 and 1015.0 hPa, near-steady over three days (+0.1 hPa)." in synoptic
    assert synoptic.endswith("Pressure here over the last 24 hours: +0.5 hPa.")
    assert "## Detailed Discussion\n\n### Synoptic Overview" in text and text.count("## Detailed Discussion") == 1


def test_without_a_ring_the_overview_says_so():
    text = compose_floor(_six_inputs(synoptic_statements=[], basin_pressure={"points": 5, "today_min_hpa": 1012.8, "today_max_hpa": 1015.0, "change_72h_hpa": -2.3}))
    synoptic = _section(text, "### Synoptic Overview")

    assert synoptic.startswith("The large-scale pressure ring could not be assessed this run.")
    assert "falling by 2.3 hPa over three days" in synoptic


def test_the_confidence_notes_read_the_record_and_todays_models():
    text = compose_floor(_six_inputs())
    notes = _section(text, "### Forecaster Confidence Notes")

    assert notes.startswith(
        "On rain the record ranks ECMWF first at Day+0, right 83% of the last 30 checks and ECMWF first at Day+3, right 77% of the last 30 checks."
    )
    assert "On today's high GFS is the warmest model at 33.5 °C and ECMWF the coolest at 29.1 °C; the call's 31.1 °C sits between them." in notes
    assert "Kenya Met calls a high of 32.0 °C and rain, agreeing with the call on rain." in notes
    # Three findings, rankings first, names as the page's.
    assert "At Day+0, no model is meaningfully better than the others here yet." in notes
    assert "At Day+0, GFS systematically over-forecasts daytime highs here." in notes
    assert "gfs_seamless" not in notes and notes.count("At Day+0,") == 3
    assert "UKMO systematically under-forecasts" not in notes, "the fourth finding is cut"


def test_a_thin_record_says_so_and_a_section_with_nothing_is_absent():
    text = compose_floor(_six_inputs(lead_records=[{"lead_time_days": 0, "best_model": None, "rain_pct": None, "checks": 4}], models_today=[], met_service_call=None, review_findings=[]))
    notes = _section(text, "### Forecaster Confidence Notes")

    assert notes == "The record is too thin to rank the models on rain yet."
    bare = compose_floor(_six_inputs(lead_records=[], models_today=[], met_service_call=None, review_findings=[], synoptic_statements=[], basin_pressure=None, mslp_trend_24h=None))
    assert "Forecaster Confidence Notes" not in bare and "Detailed Discussion" in bare, "the overview still says the ring was unavailable"


def test_only_enabled_sections_are_shown_but_every_text_is_offered_to_the_writer():
    from openlocalweather.floor import floor_section_texts

    inputs = _six_inputs(enabled_sections=["today", "extended"])
    text = compose_floor(inputs)

    assert "Severe Weather" not in text and "Detailed Discussion" not in text
    assert set(floor_section_texts(inputs)) >= {"today", "severe", "synoptic", "confidence"}


def test_the_six_inputs_round_trip_as_json():
    i = _six_inputs()
    assert FloorInputs.from_json(i.to_json()) == i
