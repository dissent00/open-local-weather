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
        "peak_wind_secondary_kmh": None,
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
