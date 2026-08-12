import re
from datetime import date

from openlocalweather.config import LocationConfig, Point, RegionPoint, SecondaryPoint, WaqiStation
from openlocalweather.llm.prompt import build_system_prompt, build_user_prompt

KISUMU = LocationConfig(
    region_name="Nyanza Basin",
    primary_place_name="Kisumu, Kenya",
    timezone="Africa/Nairobi",
    primary_point=Point(lat=-0.0917, lon=34.768),
    secondary_point=SecondaryPoint(
        enabled=True, name="Lake Victoria", section_label="Conditions for Boaters", lat=-0.75, lon=33.15
    ),
    region_points=[RegionPoint(name="Siaya", lat=0.0607, lon=34.2881)],
    metar_station_icao="HKKI",
    waqi_stations=[WaqiStation(name="Kisumu Airport", station_id="A418534")],
)

NO_SECONDARY = KISUMU.model_copy(update={"secondary_point": SecondaryPoint()})


def headings(text: str) -> list[tuple[str, str]]:
    """Returns [(level, text), ...] for every '## '/'### ' heading line, in
    order — used to check the LLM-facing narrative-structure spec, not any
    actual generated output."""
    return re.findall(r"^\s*(#{2,3}) (.+)$", text, re.MULTILINE)


def test_heading_order_with_secondary_enabled():
    prompt = build_system_prompt(KISUMU)
    top_level = [t for level, t in headings(prompt) if level == "##"]
    assert top_level == [
        "Overview",
        "Today's Forecast",
        "Extended Outlook",
        "Severe Weather / Hazard Potential",
        "Lake Victoria — Conditions for Boaters",
        "Detailed Discussion",
    ]
    sub_level = [t for level, t in headings(prompt) if level == "###"]
    assert sub_level == ["WORKFLOW & INSTRUCTIONS:", "Synoptic Overview", "Forecaster Confidence Notes"]


def test_secondary_section_omitted_when_disabled():
    prompt = build_system_prompt(NO_SECONDARY)
    top_level = [t for level, t in headings(prompt) if level == "##"]
    assert "Lake Victoria — Conditions for Boaters" not in top_level
    assert top_level == [
        "Overview",
        "Today's Forecast",
        "Extended Outlook",
        "Severe Weather / Hazard Potential",
        "Detailed Discussion",
    ]


def test_system_prompt_mentions_key_design_principles():
    prompt = build_system_prompt(KISUMU)
    # Recency-weighting instruction present.
    assert "weight the recent evidence more heavily" in prompt
    # Lead-time-awareness instruction present.
    assert "not the Day+0 numbers" in prompt
    # Honesty rule present.
    assert "insufficient data yet" in prompt.lower()
    # METAR staleness caveat present.
    assert "do not treat it as live ground truth" in prompt
    # Day+3/+7 no-onset-timing prohibition present.
    assert "never state a specific onset time" in prompt.lower()
    # Formatting rules present.
    assert "km/h (Y kt) from [CARDINAL]" in prompt
    assert "0°C / 32°F" in prompt
    assert "Emojis ONLY in the whatsapp_summary field" in prompt


def test_system_prompt_interpolates_rolling_windows_and_lookback():
    prompt = build_system_prompt(KISUMU, historical_lookback_days=45, rolling_window_short=7, rolling_window_long=21)
    assert "rolling 7-check/21-check/all-time" in prompt
    assert "past 45 days" in prompt
    assert "last 7-check" in prompt
    assert "longer-term (21-check/all-time)" in prompt


def test_system_prompt_names_region_and_place():
    prompt = build_system_prompt(KISUMU)
    assert "Nyanza Basin" in prompt
    assert "Kisumu, Kenya" in prompt
    assert "and Lake Victoria" in prompt  # secondary point mentioned in guidance list


def test_system_prompt_omits_secondary_mention_when_disabled():
    prompt = build_system_prompt(NO_SECONDARY)
    assert "Lake Victoria" not in prompt


# ---------------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------------


def test_user_prompt_includes_dates_and_url():
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://dissent00.github.io/open-local-weather/",
        verification_context={"lead_time_results": []},
        track_record_context=[],
        historical_logs=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        today_weather_data={},
        local_bulletin_source_name="Kenya Meteorological Department (KMD)",
        local_bulletin_text="No bulletin available.",
    )
    assert "2026-08-11" in prompt
    assert "2026-08-10" in prompt
    assert "https://dissent00.github.io/open-local-weather/" in prompt
    assert "no ground station reported data" in prompt  # empty ground_aqi_readings path
    assert "Not applicable" in prompt  # ground_aqi_summary=None path
    assert "Kenya Meteorological Department (KMD)" in prompt
    assert "No bulletin available." in prompt


def test_user_prompt_serializes_ground_aqi_readings_and_summary_when_present():
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.org",
        verification_context={},
        track_record_context=[],
        historical_logs=[],
        ground_aqi_readings=[
            {"name": "Kisumu Airport", "station_id": "A418534", "aqi": 42, "pm25": 18.0, "pm10": 30.0},
            {"name": "Dunga Beach", "station_id": "A418504", "aqi": 90, "pm25": 40.0, "pm10": 12.0},
        ],
        ground_aqi_summary={
            "aqi_min": 42, "aqi_max": 90, "highest_station_name": "Dunga Beach",
            "stations_with_aqi": 2, "stations_total": 2,
        },
        today_weather_data={},
        local_bulletin_source_name="KMD",
        local_bulletin_text="text",
    )
    assert '"aqi": 42' in prompt
    assert "Kisumu Airport" in prompt
    assert '"highest_station_name": "Dunga Beach"' in prompt


def test_user_prompt_includes_weather_data_sections():
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.org",
        verification_context={},
        track_record_context=[],
        historical_logs=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        today_weather_data={"primary_today_hourly": {"hourly": {"time": ["2026-08-11T00:00"]}}},
        local_bulletin_source_name="KMD",
        local_bulletin_text="text",
    )
    assert "2026-08-11T00:00" in prompt
