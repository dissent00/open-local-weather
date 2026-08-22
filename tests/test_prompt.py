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
        yesterday_actual=None,
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
        yesterday_actual=None,
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
        yesterday_actual=None,
        today_weather_data={"primary_today_hourly": {"hourly": {"time": ["2026-08-11T00:00"]}}},
        local_bulletin_source_name="KMD",
        local_bulletin_text="text",
    )
    assert "2026-08-11T00:00" in prompt


# ---------------------------------------------------------------------------
# Refresh mode (evening second run)
# ---------------------------------------------------------------------------


def test_system_prompt_refresh_block_absent_by_default():
    prompt = build_system_prompt(KISUMU)
    assert "LATER ISSUANCE" not in prompt


def test_system_prompt_reissue_block_present_when_requested():
    prompt = build_system_prompt(KISUMU, is_reissue=True)
    assert "LATER ISSUANCE" in prompt
    assert "EARLIER TODAY" in prompt
    assert "not a repeat" in prompt
    assert 'empty array for "skill_profile_summaries"' in prompt


def test_a_later_issuance_is_allowed_to_say_nothing_has_changed():
    """The most likely honest answer for a second run four hours later, and
    the one a model will avoid unless told it is acceptable — padding an
    update to look thorough is exactly how a forecast starts repeating itself."""
    prompt = build_system_prompt(KISUMU, is_reissue=True)
    assert "nothing material has changed" in prompt
    assert "Do not manufacture change" in prompt


def test_todays_forecast_covers_the_hours_ahead_not_the_calendar_day():
    """The gap the first live run exposed. The Overview became time-aware and
    this section did not, because its entire instruction was five words:
    "(temps, rain, wind, UV index, air quality)" — a whole-day checklist.

    Issued at 16:45 it produced "peak UV index will reach 9.0 around noon",
    which is wrong twice: noon had gone, and nothing could be done about it.
    """
    prompt = build_system_prompt(KISUMU)
    assert "next 12-18 hours" in prompt
    assert "HOURS AHEAD" in prompt
    assert "PAST TENSE" in prompt


def test_the_time_aware_section_must_not_change_what_gets_scored():
    """today_properties stays a whole-day call.

    Those values are scored against the day's observations and compared
    against every other day in the record. Narrowing temp_high_c to "the next
    12 hours" would leave the record internally inconsistent — and silently,
    since every individual entry would still look reasonable."""
    prompt = build_system_prompt(KISUMU)
    assert "today_properties stays your blended call for the WHOLE calendar day" in prompt
    assert "temp_high_c is the day's high whether or not it has already happened" in prompt


def test_a_later_issuance_may_be_brief_but_may_not_drop_content():
    """A real regression, caught on the live 18:07 run of 2026-08-22.

    The met service was named 1-3 times in each of the previous three days'
    refreshes and ZERO times in that one. Its data was present throughout —
    kenya_met sat in day0 and day3 model_predictions and the 629-character
    bulletin was stored — so nothing was lost upstream. The new LATER ISSUANCE
    block pushed brevity hard enough ("do not restate at length", "say so
    plainly and move on") that the model economised by dropping a peer model
    entirely rather than by shortening prose.

    Saying "nothing changed since this morning" IS a statement about the met
    service. Silence is not.
    """
    prompt = build_system_prompt(KISUMU, is_reissue=True)
    assert "BREVITY IS NOT OMISSION" in prompt
    assert "local met service is still a peer model" in prompt
    assert "every heading below appears on every issuance" in prompt


def test_every_run_is_told_to_lead_with_what_matters_now():
    """Present on EVERY run, not just later ones: a first run at 06:00 and a
    first run at 16:00 are both possible once an operator picks their own
    schedule, and neither should describe a day that has largely happened."""
    prompt = build_system_prompt(KISUMU)
    assert "ISSUANCE TIME" in prompt
    assert "WHAT MATTERS NOW" in prompt
    assert "dusk through to dawn" in prompt


def test_system_prompt_reissue_does_not_disturb_heading_order():
    # The reissue block is instructional text, not a narrative heading —
    # must not add or reorder the actual ## headings the LLM is told to use.
    prompt = build_system_prompt(KISUMU, is_reissue=True)
    top_level = [t for level, t in headings(prompt) if level == "##"]
    assert top_level == [
        "Overview",
        "Today's Forecast",
        "Extended Outlook",
        "Severe Weather / Hazard Potential",
        "Lake Victoria — Conditions for Boaters",
        "Detailed Discussion",
    ]


def test_user_prompt_omits_earlier_today_block_by_default():
    prompt = build_user_prompt(
        today=date(2026, 8, 11), yesterday=date(2026, 8, 10), public_webpage_url="https://example.org",
        verification_context={}, track_record_context=[], historical_logs=[],
        ground_aqi_readings=[], ground_aqi_summary=None, yesterday_actual=None, today_weather_data={},
        local_bulletin_source_name="KMD", local_bulletin_text="text",
    )
    assert "EARLIER TODAY" not in prompt


def test_user_prompt_lists_every_earlier_issuance_with_its_time():
    prompt = build_user_prompt(
        today=date(2026, 8, 11), yesterday=date(2026, 8, 10), public_webpage_url="https://example.org",
        verification_context={}, track_record_context=[], historical_logs=[],
        ground_aqi_readings=[], ground_aqi_summary=None, yesterday_actual=None, today_weather_data={},
        local_bulletin_source_name="KMD", local_bulletin_text="text",
        earlier_today=[
            {"time": "06:07", "narrative": "## Overview\nSunny and warm today."},
            {"time": "13:02", "narrative": "## Overview\nCloud building inland."},
        ],
    )
    # A list, not a single "morning narrative": the number of runs a day is
    # the operator's choice, and the third one needs to know about the second.
    assert "EARLIER TODAY" in prompt
    assert "Issued 06:07" in prompt and "Issued 13:02" in prompt
    assert "Sunny and warm today." in prompt
    assert "Cloud building inland." in prompt
    assert "not a repeat" in prompt


# ---------------------------------------------------------------------------
# Day-over-day comparison (YESTERDAY'S ACTUAL CONDITIONS)
# ---------------------------------------------------------------------------


def test_system_prompt_asks_for_a_concrete_comparison_against_observed_conditions():
    """The Overview used to be told to "compare to the previous day" while the
    user message never supplied yesterday's conditions — so the model was
    asked for a comparison it had no data for, inviting vagueness or
    invention. Guards the instruction now that the data exists."""
    prompt = build_system_prompt(KISUMU)
    assert "DAY-OVER-DAY COMPARISON" in prompt
    # The labels are computed in code; the LLM must not redo the subtraction.
    assert "do NOT subtract the temperatures yourself" in prompt
    # And must not invent a change when there genuinely isn't one.
    assert "do not manufacture a difference" in prompt
    # Must be anchored to observations, not to how the models scored.
    assert "never against yesterday's forecast or its verification scores" in prompt
    # And must degrade honestly when there's no record.
    assert "omit the comparison rather than guessing" in prompt


def test_user_prompt_includes_yesterdays_observed_conditions():
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.com/",
        verification_context={},
        track_record_context=[],
        historical_logs=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual={"rain": True, "high_c": 29.4, "low_c": 18.0},
        today_weather_data={},
        local_bulletin_source_name="",
        local_bulletin_text="",
    )
    assert "DAY-OVER-DAY COMPARISON" in prompt
    assert "29.4" in prompt
    # Framed as observed, so it can't be confused with the verification block.
    assert "yesterday's OBSERVED conditions" in prompt


def test_user_prompt_says_so_when_yesterday_is_unavailable():
    """A gap in the record must read as a gap, not silently look like a day
    with no notable weather."""
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.com/",
        verification_context={},
        track_record_context=[],
        historical_logs=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual=None,
        today_weather_data={},
        local_bulletin_source_name="",
        local_bulletin_text="",
    )
    assert "Unavailable — no observed record for yesterday" in prompt
    assert "omit the day-over-day comparison" in prompt


def _user_prompt_with_review(review_context):
    return build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.com/",
        verification_context={},
        track_record_context=[],
        historical_logs=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual=None,
        today_weather_data={},
        local_bulletin_source_name="",
        local_bulletin_text="",
        review_context=review_context,
    )


def test_system_prompt_forbids_deriving_a_ranking_the_gate_withheld():
    """The load-bearing instruction.

    MODEL TRACK RECORD hands the LLM raw per-model percentages. So an empty
    findings list is not self-enforcing: the model could read those numbers
    and announce a winner itself, which is exactly the small-sample claim the
    code-side gate declined to make. The prompt has to close that off in
    words, because nothing else can.
    """
    prompt = build_system_prompt(KISUMU)
    assert "LONG-RUN REVIEW FINDINGS" in prompt
    assert "IF NO RANKING FINDING IS PRESENT FOR A LEAD TIME" in prompt
    assert "do NOT construct your own ranking" in prompt
    assert "deliberately withheld" in prompt
    # Absence of a bias finding must be treated the same way.
    assert "do not assert one from the error numbers yourself" in prompt
    # And a stated confidence is a ceiling, not a starting point.
    assert "do not upgrade it" in prompt


def test_system_prompt_makes_admitting_thin_data_the_expected_outcome():
    """Left unsaid, "insufficient data" reads to a model as a failure to be
    written around. It has to be named as the correct answer."""
    prompt = build_system_prompt(KISUMU)
    assert "not a failure" in prompt
    assert "never claiming more than the record holds" in prompt


def test_user_prompt_carries_the_review_and_repeats_the_no_derivation_rule():
    prompt = _user_prompt_with_review(
        {
            "data_sufficiency": "Day+0: 8 check(s) per model — directional only.",
            "findings": [],
        }
    )
    assert "LONG-RUN REVIEW" in prompt
    assert "8 check(s) per model" in prompt
    # Restated at the point of use, not only in the system prompt.
    assert "if a ranking is absent the record does not support one" in prompt


def test_user_prompt_says_so_when_no_review_was_computed():
    """Same principle as the day-over-day gap: absent must read as absent."""
    prompt = _user_prompt_with_review(None)
    assert "LONG-RUN REVIEW" in prompt
    assert "Unavailable — no review computed this run." in prompt


def test_system_prompt_frames_the_met_service_as_a_peer_not_an_authority():
    """The failure mode is deference. A national met service reads as
    authoritative, and an LLM told about it without instruction will tend to
    defer to it — or, just as wrong, dismiss it as unscientific next to a
    numerical model. Both substitute a prior for the record."""
    prompt = build_system_prompt(KISUMU)
    assert "LOCAL MET SERVICE AS A MODEL" in prompt
    assert "not as a more authoritative source and not as a lesser one" in prompt
    assert "settled by the record rather than by deference" in prompt
    # And the sparse-field trap, which is the same one ModelPrediction.rain
    # exists to avoid.
    assert 'a null there means "not forecast", never "no rain" or "calm"' in prompt


def test_user_prompt_carries_the_extracted_predictions_that_get_scored():
    """The narrative and the accuracy record should describe one set of
    numbers, not two."""
    prompt = build_user_prompt(
        today=date(2026, 8, 19), yesterday=date(2026, 8, 18),
        public_webpage_url="https://example.com/",
        verification_context={}, track_record_context=[], historical_logs=[],
        ground_aqi_readings=[], ground_aqi_summary=None, yesterday_actual=None,
        today_weather_data={}, local_bulletin_source_name="", local_bulletin_text="",
        model_predictions_context={
            "day0": [{"model": "kenya_met", "rain": True, "high_c": 30.0, "wind_kmh": None}],
            "day3": [], "day7": [],
        },
    )
    assert "EXTRACTED PER-MODEL PREDICTIONS" in prompt
    assert "kenya_met" in prompt
    assert "these exact values get scored" in prompt
    assert 'never zero or "no"' in prompt


def test_user_prompt_forwards_every_weather_key_the_pipeline_sends():
    """A real bug this would have caught.

    build_user_prompt rebuilds the weather payload key-by-key so a stray key
    cannot silently ENLARGE the prompt. The cost is that a newly added key
    must be listed in two places — and when `synoptic_scale_pressure` was
    added, the pipeline passed it and this rebuild dropped it. The system
    prompt then instructed the model to use a key that never arrived, so the
    whole synoptic feature was inert while every test still passed.

    Rather than pin a hard-coded list here (which would have been written
    from the same mistaken assumption), this asserts against the keys
    pipeline.py actually populates.
    """
    import inspect

    from openlocalweather import pipeline

    source = inspect.getsource(pipeline)
    # Scoped to the today_weather_data literals specifically. A looser scan
    # over the whole module also matches response-schema fields such as
    # `synoptic_pattern`, which are not weather-payload keys at all.
    sent: set[str] = set()
    for block in re.findall(r"today_weather_data=\{(.*?)\n        \},", source, re.S):
        sent |= set(re.findall(r'"(\w+)":', block))
    assert sent, "fixture assumption: pipeline builds today_weather_data inline"
    assert "synoptic_scale_pressure" in sent, "fixture assumption: pipeline sends it"

    prompt = build_user_prompt(
        today=date(2026, 8, 19), yesterday=date(2026, 8, 18),
        public_webpage_url="https://example.com/",
        verification_context={}, track_record_context=[], historical_logs=[],
        ground_aqi_readings=[], ground_aqi_summary=None, yesterday_actual=None,
        today_weather_data={k: f"SENTINEL_{k}" for k in sent},
        local_bulletin_source_name="", local_bulletin_text="",
    )
    missing = sorted(k for k in sent if f"SENTINEL_{k}" not in prompt)
    assert not missing, (
        f"pipeline sends these weather keys but build_user_prompt drops them: {missing}"
    )
