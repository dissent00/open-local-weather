import re
from datetime import date

from openlocalweather.config import LocationConfig, Point, RegionPoint, SecondaryPoint, WaqiStation
from openlocalweather.llm.prompt import (
    build_judgment_prompt,
    build_narrative_prompt,
    build_user_prompt,
)


def build_system_prompt(location, **kwargs) -> str:
    """Both prompts of the split, joined — ROADMAP item 59 step 3.

    THIS FILE ASKS WHETHER A RULE STILL EXISTS, not which call carries it.
    Those are different questions and splitting them keeps both answerable:
    34 assertions here would otherwise each have to guess a side, and a rule
    that legitimately moved between the two calls would fail as though it had
    been deleted. WHICH SIDE a rule belongs on is tests/test_prompt_seam.py's
    question, and it is the one that can actually be got wrong.
    """
    return f"{build_judgment_prompt(location, **kwargs)}\n{build_narrative_prompt(location, **kwargs)}"

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
        # NO "Overview" — item 159 step 5 retired it 2026-09-22. Today's
        # Forecast is the first section a reader meets, which is why the
        # instability mandate moved there.
        "Today's Forecast",
        "Extended Outlook",
        "Severe Weather / Hazard Potential",
        "Lake Victoria — Conditions for Boaters",
        "Detailed Discussion",
    ]
    sub_level = [t for level, t in headings(prompt) if level == "###"]
    # "WORKFLOW & INSTRUCTIONS:" twice because BOTH prompts carry it and this
    # helper joins them — the judgment call has a workflow too, it is just a
    # much shorter one. The narrative headings are what this test is about.
    assert sub_level == [
        "WORKFLOW & INSTRUCTIONS:",
        "WORKFLOW & INSTRUCTIONS:",
        "Synoptic Overview",
        "Forecaster Confidence Notes",
    ]


def test_secondary_section_omitted_when_disabled():
    prompt = build_system_prompt(NO_SECONDARY)
    top_level = [t for level, t in headings(prompt) if level == "##"]
    assert "Lake Victoria — Conditions for Boaters" not in top_level
    assert top_level == [
        # NO "Overview" — item 159 step 5 retired it 2026-09-22. Today's
        # Forecast is the first section a reader meets, which is why the
        # instability mandate moved there.
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
    # Formatting rules present. The bearing left this rule on 2026-09-10:
    # it is pre-computed, gated on model agreement, and absent more often
    # than not — see WIND DIRECTION in the user prompt.
    assert '"X km/h (Y kt)"' in prompt
    assert "SAY NOTHING ABOUT DIRECTION" in prompt
    assert "0°C / 32°F" in prompt
    assert "Plain text throughout. No emojis." in prompt


def test_system_prompt_interpolates_rolling_windows():
    # The lookback assertion went with ROADMAP item 147: HISTORICAL NOTES was
    # the only block whose length this configured, and the block is gone.
    prompt = build_system_prompt(KISUMU, rolling_window_short=7, rolling_window_long=21)
    assert "rolling 7-check/21-check/all-time" in prompt
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
    # `primary_extended_daily` rather than `primary_today_hourly` — ROADMAP
    # item 73's first cut removed the calendar day's hourly series from the
    # payload, so this asserts against a key that is still forwarded.
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.org",
        verification_context={},
        track_record_context=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual=None,
        today_weather_data={"primary_extended_daily": {"daily": {"time": ["2026-08-11"]}}},
        local_bulletin_source_name="KMD",
        local_bulletin_text="text",
    )
    assert "2026-08-11" in prompt


# ---------------------------------------------------------------------------
# Refresh mode (evening second run)
# ---------------------------------------------------------------------------


def test_system_prompt_refresh_block_absent_by_default():
    prompt = build_system_prompt(KISUMU)
    assert "LATER ISSUANCE" not in prompt


def test_the_verification_block_says_only_that_verification_is_written():
    """WHAT THIS BLOCK STOPPED BEING, 2026-09-16.

    It was four rules for a LATER ISSUANCE, from when OLW was a twice-a-day
    tool. Every run is now a fresh forecast and the reader does not care when
    the last one ran, so three of the four went with the concept — the UPDATE
    framing, "NO NEW GUIDANCE IS AN ANSWER" (unreachable: no new cycle means
    no call), and "BREVITY IS NOT OMISSION" (its substance is in the base
    prompt, checked before deleting).

    What is left is about VERIFICATION and nothing else, which is why it is
    keyed on verification rather than on which run this is.
    """
    prompt = build_system_prompt(KISUMU, verification_already_written=True)

    assert "VERIFICATION IS ALREADY WRITTEN" in prompt
    # PLURAL — the schema field is `skill_profile_summaries`, and the prompt
    # asked for a singular that does not exist. ROADMAP item 142, finding 1's
    # neighbour; `test_prompt_seam` now guards the class.
    assert 'empty array for "skill_profile_summaries"' in prompt
    # And the block must say it outranks WORKFLOW STEP 1, which asks
    # unconditionally for the very fields this one suppresses — finding 1.
    assert "OVERRIDES WORKFLOW STEP 1" in prompt

    # The retired concept must not come back by the side door.
    for gone in ("LATER ISSUANCE", "EARLIER TODAY", "not a repeat",
                 "NO NEW GUIDANCE IS AN ANSWER", "BREVITY IS NOT OMISSION"):
        assert gone not in prompt, f"{gone!r} is the retired reissue concept"


def test_the_base_prompt_still_carries_what_the_deleted_rule_protected():
    """"BREVITY IS NOT OMISSION" guarded the met service against being
    dropped as "nothing changed". It was safe to delete only because the base
    prompt says it anyway — this is that check, kept so the deletion stays
    safe."""
    prompt = build_system_prompt(KISUMU, verification_already_written=False)

    assert "peer model" in prompt
    assert "Forecaster Confidence Notes" in prompt


def test_instability_is_treated_as_a_disagreement_axis():
    """The miss that prompted this, 2026-08-22.

    The evening forecast said "no severe weather hazards" and "mostly dry"
    while it was thundering in Kisumu. Both statements were defensible on
    precipitation — every model had near zero. But CAPE at 19:00 was 70 J/kg
    in GFS and 780 in ICON, 960 in ECMWF: a sharp disagreement about
    instability, resolved silently toward the quiet answer.

    cape was already fetched and already in the prompt payload. Nothing told
    the model it mattered.
    """
    prompt = build_system_prompt(KISUMU)
    assert "INSTABILITY AND THUNDER" in prompt
    assert "1000 J/kg" in prompt, "thresholds, not adjectives"
    assert "THUNDER WITHOUT RAIN IS A REAL AND COMMON OUTCOME" in prompt
    assert "must never be used as such" in prompt, (
        "near-zero rainfall is not evidence against thunder — that inference "
        "is exactly what produced the miss"
    )


def test_instability_guidance_stays_location_agnostic():
    """An earlier draft named Lake Victoria, which an existing test caught.

    This prompt is shared by every fork. Naming the operator's own lake in
    guidance about convection would read as nonsense in Nairobi or Reykjavik,
    and the failure would be silent — the text is still grammatical.
    """
    prompt = build_system_prompt(NO_SECONDARY)
    assert "INSTABILITY AND THUNDER" in prompt
    assert "Lake Victoria" not in prompt


def test_the_forecast_does_not_state_the_unactionable():
    """"The UV index has dropped to zero following sunset" — true,
    unsurprising, and useless. The reader can see it is dark."""
    prompt = build_system_prompt(KISUMU)
    assert "Do not state the obvious or the unactionable" in prompt
    assert "OMIT it rather than reporting its null state" in prompt


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
    prompt = build_system_prompt(KISUMU, verification_already_written=True)
    top_level = [t for level, t in headings(prompt) if level == "##"]
    assert top_level == [
        # NO "Overview" — item 159 step 5 retired it 2026-09-22. Today's
        # Forecast is the first section a reader meets, which is why the
        # instability mandate moved there.
        "Today's Forecast",
        "Extended Outlook",
        "Severe Weather / Hazard Potential",
        "Lake Victoria — Conditions for Boaters",
        "Detailed Discussion",
    ]


def test_the_days_earlier_narratives_are_never_sent():
    """DELETED 2026-09-16 with the reissue concept — items 137 and 138.

    EARLIER TODAY carried every narrative already published today so a later
    run could write "an update to these, not a repeat of them". There is no
    update: every run is a fresh forecast, and a run with no new model data
    never reaches a model at all.

    It was NOT protecting the case it looked like it protected. Asked whether
    dropping it would let a 16:00 run repeat "dry until 18:00" while the
    station had seen rain since 14:00, the answer is no — it carried
    NARRATIVES, not readings. The reading arrives as OBSERVED SO FAR TODAY,
    and the contradiction is detected in code by
    DISAGREEMENT_ONSET_ALREADY_PASSED.
    """
    prompt = build_user_prompt(
        today=date(2026, 8, 11), yesterday=date(2026, 8, 10), public_webpage_url="https://example.org",
        verification_context={}, track_record_context=[],
        ground_aqi_readings=[], ground_aqi_summary=None, yesterday_actual=None, today_weather_data={},
        local_bulletin_source_name="KMD", local_bulletin_text="text",
    )

    assert "EARLIER TODAY" not in prompt
    assert "not a repeat" not in prompt
    assert "Issued " not in prompt


def test_the_narrative_no_longer_asks_for_a_day_over_day_sentence():
    """The comparison is a tile modifier now, not a sentence — item 159.

    THIS TEST IS THE INVERSE OF THE ONE IT REPLACES. That one guarded a long
    set of rules teaching the model how to open the Overview with the
    code-composed comparison: do not subtract the temperatures yourself, do
    not manufacture a difference, never compare against yesterday's scores.
    All of it existed because a sentence was being handed to a writer.

    Nothing hands it over any more. `comparison_modifiers` puts "3° cooler"
    inside the temperature tile and nothing inside a tile that did not move,
    which is the thing the sentence could never do: across 16 archived runs
    `overview_comparison` returned "nothing worth saying" ZERO times.

    What the model still gets is the three BOOLEANS for context —
    `yesterday_rain`, `yesterday_thunder`, `today_rain_expected` — and the
    caveat about the reanalysis cell and the airport station being different
    places, which is about `yesterday_rain` and outlives the sentence.
    """
    prompt = build_system_prompt(KISUMU)

    assert "Overview" not in [t for _, t in headings(prompt)]
    for gone in (
        "overview_comparison",
        "do not subtract the temperatures yourself",
        "do not manufacture a difference",
        "omit the comparison rather than guessing",
    ):
        assert gone not in prompt, f"the Overview's comparison rule survives: {gone!r}"

    # the booleans and their caveat stay, in the USER prompt where they live
    user = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.com/",
        verification_context={},
        track_record_context=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual={"yesterday_rain": True, "today_rain_expected": False},
        today_weather_data={},
        local_bulletin_source_name="",
        local_bulletin_text="",
    )
    assert "DAY-OVER-DAY COMPARISON" in user
    assert "IS NOT A CONTRADICTION" in user


def test_user_prompt_includes_yesterdays_observed_conditions():
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.com/",
        verification_context={},
        track_record_context=[],
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
        verification_context={}, track_record_context=[],
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
        verification_context={}, track_record_context=[],
        ground_aqi_readings=[], ground_aqi_summary=None, yesterday_actual=None,
        today_weather_data={k: f"SENTINEL_{k}" for k in sent},
        local_bulletin_source_name="", local_bulletin_text="",
    )
    missing = sorted(k for k in sent if f"SENTINEL_{k}" not in prompt)
    assert not missing, (
        f"pipeline sends these weather keys but build_user_prompt drops them: {missing}"
    )


def test_the_extended_outlook_says_when_its_guidance_never_arrived():
    """ROADMAP item 51. Degrading is only an improvement if the degraded run
    is HONEST. The seven-day fetch failing leaves `primary_extended_daily`
    empty and the Day+3/Day+7 prediction blocks empty, and until this the
    prompt still asked for an Extended Outlook paragraph "using the daily
    summary data" that was no longer there — a required section with nothing
    to fill it, which is the exact shape that produces invention.
    """
    available = build_system_prompt(KISUMU)
    missing = build_system_prompt(KISUMU, extended_outlook_available=False)

    # The heading stays on every issuance — the structure is a contract, and
    # a section that vanishes reads as a forecast that forgot rather than one
    # that could not.
    assert ("##", "Extended Outlook") in headings(available)
    assert ("##", "Extended Outlook") in headings(missing)

    # "using the daily summary data" is the phrase that POINTS AT A SOURCE.
    # The degraded text names the same data to say it is absent, so the bare
    # noun phrase is not the thing to test on.
    assert "using the daily summary data" in available
    assert "using the daily summary data" not in missing, (
        "the degraded prompt still points at data the run does not have"
    )
    assert "did not arrive" in missing

    # SCORED FIELDS TOO, not only the prose. "extended_properties" is graded
    # against what happens on those days beside every model in the record, so
    # a lead called from no guidance is scored exactly as confidently as one
    # that was.
    assert "extended_properties" in missing
    assert re.search(r"extended_properties[^\n]*empty|empty[^\n]*extended_properties", missing) or (
        "leave \"extended_properties\" empty" in missing
    ), "nothing tells the forecaster not to commit to Day+3/Day+7 blind"


def test_the_calendar_days_hourly_series_is_not_sent():
    """ROADMAP item 73's first cut, and the guard that keeps it cut.

    `primary_today_hourly` was 13.2% of the whole prompt — ten hourly
    variables across five models for the calendar day. Eighteen of its
    twenty-four hours were already in HOURS AHEAD and the other six had
    elapsed, so it carried nothing the forecaster could legitimately use.

    Asserted here rather than left to the size of the prompt, because a
    payload key that creeps back is exactly how the synoptic key went missing
    in the other direction and nothing noticed for weeks.
    """
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.org",
        verification_context={},
        track_record_context=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual=None,
        today_weather_data={
            "primary_today_hourly": {"hourly": {"time": ["2026-08-11T00:00"]}},
            "primary_extended_daily": {"daily": {"time": ["2026-08-11"]}},
        },
        local_bulletin_source_name="KMD",
        local_bulletin_text="text",
    )

    assert "primary_today_hourly" not in prompt
    assert "2026-08-11T00:00" not in prompt
    # And the rule that policed it is gone with it — item 73's first
    # category: the cheapest way to delete a rule is to delete the temptation.
    assert "as though it were all still ahead" not in prompt


# ---------------------------------------------------------------------------
# Item 73, category 4 — the payload's own precision.
# ---------------------------------------------------------------------------


def test_our_own_arithmetic_is_rounded_to_the_instrument():
    """The noise this pass exists to remove, taken from a real prompt.

    `avg_temp_high_error_c_10: -2.380000000000001` is a mean temperature error
    to sixteen significant figures, and the observation behind it is recorded
    to 0.1 C. The rounded value IS the value; nothing is lost.
    """
    from openlocalweather.llm.prompt import _round_for_prompt

    assert _round_for_prompt({"avg_temp_high_error_c_10": -2.380000000000001}) == {
        "avg_temp_high_error_c_10": -2.4
    }
    assert _round_for_prompt({"mslp_trend": 1.1999999999999318}) == {"mslp_trend": 1.2}
    assert _round_for_prompt({"all_time_rain_pct": 61.76470588235294}) == {
        "all_time_rain_pct": 61.8
    }


def test_a_brier_score_is_not_rounded_into_nothing():
    """0.0529 at one decimal place is 0.1 — a deletion, not a rounding.

    The exceptions were found by sweeping every numeric field in a real prompt
    for values living inside [0, 1], not by guessing which ones looked
    delicate.
    """
    from openlocalweather.llm.prompt import _round_for_prompt

    assert _round_for_prompt({"rain_brier": 0.05289999999999999}) == {"rain_brier": 0.0529}
    assert _round_for_prompt({"rain_brier_skill": -0.1234567}) == {"rain_brier_skill": -0.1235}


def test_a_coordinate_is_a_position_not_a_measurement():
    """One decimal place would move the location by kilometres."""
    from openlocalweather.llm.prompt import _round_for_prompt

    assert _round_for_prompt({"latitude": -0.05857086}) == {"latitude": -0.0586}


def test_counts_and_flags_are_not_numbers_to_round():
    """A bool rounded into 1 reads as a count, whatever isinstance(True, int)
    says; and a count is not a measurement with a precision."""
    from openlocalweather.llm.prompt import _round_for_prompt

    got = _round_for_prompt({"checks": 34, "thunder": True, "rain": False, "note": "steady"})

    assert got == {"checks": 34, "thunder": True, "rain": False, "note": "steady"}
    assert got["thunder"] is True and got["rain"] is False


def test_the_field_table_survives_a_list_of_dicts():
    """The worst offender in a real prompt is `regional_pressure`, which
    arrives as a list of dicts carrying seventeen decimal places."""
    from openlocalweather.llm.prompt import _round_for_prompt

    got = _round_for_prompt(
        {"regional_pressure": [{"latitude": -0.10544816, "pressure_msl_mean": 1014.6666666666666}]}
    )

    assert got == {"regional_pressure": [{"latitude": -0.1054, "pressure_msl_mean": 1014.7}]}


def test_a_first_issuance_is_never_told_verification_is_already_written():
    """THE FAILURE THIS PREVENTS DESTROYS A DAY'S SCORING, silently.

    The block tells the model to return a one-line PLACEHOLDER for
    yesterday_verification and an empty skill_profile_summary. On the day's
    first run that is the only chance to write the real thing — the actuals
    have just been fetched and scored — so emitting it unconditionally would
    replace a day's verification with the word "unchanged" and nothing would
    raise.

    Found by mutation on 2026-09-16: making the block unconditional passed
    every other test in this file.
    """
    prompt = build_system_prompt(KISUMU, verification_already_written=False)

    assert "VERIFICATION IS ALREADY WRITTEN" not in prompt
    assert "placeholder" not in prompt.lower()


def _minimal_user_prompt(**overrides):
    kwargs = dict(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.org",
        verification_context=[],
        track_record_context=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual=None,
        today_weather_data={},
        local_bulletin_source_name="",
        local_bulletin_text="",
    )
    kwargs.update(overrides)
    return build_user_prompt(**kwargs)


def test_user_prompt_says_when_the_record_blocks_were_not_supplied():
    # ensemble item 12: the app passed nothing for these and the prompt
    # rendered `[]`, which reads like a result rather than an absence. None
    # now says what it is. The pipeline never passes None — a fresh
    # deployment sends its (empty) lists — so the server's prompt is unmoved.
    prompt = _minimal_user_prompt(verification_context=None, track_record_context=None)
    assert "Unavailable — no verification results supplied this run." in prompt
    assert "Unavailable — no track record supplied this run." in prompt


def test_user_prompt_keeps_rendering_supplied_empty_record_blocks_as_lists():
    prompt = _minimal_user_prompt()
    assert "no verification results supplied" not in prompt
    assert "no track record supplied" not in prompt
    assert "MODEL TRACK RECORD (already computed rolling stats, per model per lead time):\n[]" in prompt


def _section(prompt: str, name: str) -> str:
    """The rules under one narrative heading, up to the next one."""
    start = prompt.index(f"## {name}")
    rest = prompt[start + 4:]
    end = rest.find("\n   ## ")
    return rest if end < 0 else rest[:end]


def test_the_instability_mandate_lives_in_the_first_section():
    """A convective flag the code sets MUST reach the reader, and early.

    THIS TEST DID NOT EXIST BEFORE 2026-09-22 and the rule it guards is four
    weeks old. Two mutations proved the gap at item 159 step 5: deleting the
    mandate outright, and loosening it from "THIS section" to "some section",
    both left the whole suite green except the vector exporter's byte-for-byte
    comparison of the entire prompt — which fires on any edit at all, so it is
    a tripwire and not a guard.

    The live case the rule was written against, 2026-08-26: afternoon CAPE
    peaked between 1100 and 2600 J/kg across models, and a real forecast
    opened "similar warmth, calmer winds, and dry again", discussing the
    instability only far below, where a reader who stopped early never saw it.

    IT MOVED HERE WHEN THE OVERVIEW WENT. The mandate used to be the
    Overview's, for the same reason it is now Today's Forecast's: that is the
    first section a reader meets. Asserting the SECTION and not just the
    presence of the words is the point — a warning that drifts to the bottom
    of the document has failed in the way this rule exists to prevent.
    """
    prompt = build_system_prompt(KISUMU)
    today = _section(prompt, "Today's Forecast")

    assert "THUNDER IS NOT OPTIONAL WHEN CODE SAYS IT IS THERE" in today
    assert "THIS section must say thunderstorms are possible" in today
    # the flag decides, and a dry day is not an exemption
    assert "The flag decides; you phrase it." in today
    assert "near-zero rainfall totals are NOT a reason to leave it out" in today
    # and the numbers stay out of the section a reader acts on
    assert "NO CAPE VALUES, NO J/kg AND NO MODEL NAMES HERE" in today

    # THE MANDATE MUST NOT ORDER A CLOCK IT CANNOT JUSTIFY. Its first draft
    # said "placed by the CLOCK from onset_at and peak_at", which collides
    # with Rule 4 — never claim more precision than the models agree on.
    # `summarize_instability` sets `onset_at` to the FIRST hour ANY model
    # crosses the threshold, explicitly "the earliest warning" and not a
    # consensus, so a named hour asserts an agreement that does not exist.
    # A cold reader caught it, and said it would have written the hour.
    assert "onset_at" in today and "not an hour they share" in today
    assert 'that block\'s "timing" places the thunder' in today

    # it is NOT in the later sections, which place the thunder their own way
    for later in ("Extended Outlook", "Severe Weather / Hazard Potential"):
        assert "THUNDER IS NOT OPTIONAL" not in _section(prompt, later)


def test_the_direction_block_speaks_per_anchor_not_per_day():
    """A day with one agreed bearing must be able to say so — item 160.

    THE CONTRADICTION THIS CLOSES, measured over the 19 archived issuances
    that carry both a direction block and hourly bearings:

        the day-level block had a bearing on          1 of 19
        the midday anchor had one on                 15 of 17
        the evening anchor had one on                 8 of 28

    The old block asked whether ONE bearing held for the whole day. At this
    station the day has two — a northerly land breeze and a southwesterly lake
    breeze — so the honest answer to that question is almost always no, and
    the block then ordered "Say nothing about direction". Meanwhile the tile,
    fed by `wind_anchors`, printed "midday SW" on the page and in the app. One
    record, two answers, and the prose was the one that had to stay quiet.

    Both now read the SAME `wind_anchors` call, which is what makes them
    unable to disagree.
    """
    hourly = {
        "hourly": {
            "time": [f"2026-08-11T{h:02d}:00" for h in range(24)],
            **{
                f"wind_direction_10m_{m}": [220.0] * 24
                for m in ("gfs_seamless", "ecmwf_ifs025", "icon_seamless")
            },
            **{
                f"wind_speed_10m_{m}": [12.0] * 24
                for m in ("gfs_seamless", "ecmwf_ifs025", "icon_seamless")
            },
        }
    }
    prompt = build_user_prompt(
        today=date(2026, 8, 11),
        yesterday=date(2026, 8, 10),
        public_webpage_url="https://example.com/",
        verification_context={},
        track_record_context=[],
        ground_aqi_readings=[],
        ground_aqi_summary=None,
        yesterday_actual=None,
        today_weather_data={},
        local_bulletin_source_name="",
        local_bulletin_text="",
        anchor_directions={"early": None, "midday": "SW", "evening": None},
    )

    # the agreed anchor is named, and named as ITS moment
    assert "midday" in prompt and "SW" in prompt
    # and the block no longer issues a blanket order the tile contradicts
    assert "Say nothing about direction." not in prompt

    # THE RULE THAT STOPS ONE ANCHOR BECOMING THE DAY. Added after a mutation
    # replacing it with "you may describe the day with it" survived the whole
    # suite: naming midday's bearing is the fix, and generalising it to a day
    # whose evening agrees on 8 of 28 is the failure the fix could cause.
    system = build_system_prompt(KISUMU)
    assert "must not carry it across the day" in system
    assert "SAY NOTHING ABOUT DIRECTION FOR THAT PART OF THE DAY" in system
    assert '"variable"' in system and '"shifting"' in system


def _aqi_reading(**over):
    """A station reading in the shape the archive holds."""
    base = {"name": "Kisumu Airport", "station_id": "A418534", "aqi": None,
            "pm25": 52.0, "pm10": 15.0,
            "measured_at": "2026-09-21 23:00:00+00:00"}
    return {**base, **over}


def _user_prompt_with(readings, last_known_absence=None):
    return build_user_prompt(
        today=date(2026, 9, 22), yesterday=date(2026, 9, 21),
        public_webpage_url="https://example.com/", verification_context={},
        track_record_context=[], ground_aqi_readings=readings,
        ground_aqi_summary=None, yesterday_actual=None, today_weather_data={},
        local_bulletin_source_name="", local_bulletin_text="",
        ground_aqi_last_known_absence=last_known_absence,
    )


def test_the_last_known_block_says_which_kind_of_nothing_it_is():
    """ROADMAP item 163. The block asserted one absence and there are three.

    `last_known_ground_aqi` returns None when no reading carries BOTH a
    numeric AQI and a timestamp, and the block said "no station has a
    timestamped reading at all" — which on the commonest of those cases is
    FALSE. Measured over the stored record: 11 of 43 days had no numeric AQI
    from any station, and on the two of those that fall inside the prompt
    archive every station carried `measured_at` and `hours_old` while the
    block denied it. Rule 1 orders the model to state it as given, and
    system-prompt rule 5 forbids presenting a measurement as absent, so
    obeying one breaks the other.

    THE INSTRUCTION RIDES IN THE BLOCK, NOT THE RULE. A deployment whose
    stations are reliable never reaches this branch, and a sentence in the
    system prompt would cost it characters on every run for a case it never
    hits. Blocks in this prompt already carry their own instructions — "state
    as given if present", "Use it VERBATIM or not at all" — so this one does
    too, and appears only on the days it applies.
    """
    reported_no_number = _user_prompt_with(
        [_aqi_reading(), _aqi_reading(name="Dunga Beach", station_id="A1")],
        last_known_absence=(
            "Unavailable — the stations are reporting but none of them carried a "
            "numeric AQI. Say that, and take the figure from the model guidance. "
            "They are NOT down and NOT absent: their own readings, with their "
            "timestamps and ages, are in GROUND AQI STATIONS above."
        ),
    )
    block = reported_no_number.split("GROUND AQI LAST KNOWN")[1].split("\n\n")[0]

    assert "reporting but none of them carried a numeric AQI" in block
    assert "NOT down and NOT absent" in block
    # the false claim is gone
    assert "no station has a timestamped reading at all" not in reported_no_number
