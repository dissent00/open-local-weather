"""System and user prompt construction.

build_system_prompt() ports buildSystemPrompt() from
KisumuForecastPipeline_v2.gs — the instructional CONTENT is preserved
faithfully (recency-weighting, lead-time-awareness, the "insufficient data
yet" honesty rule, METAR/ground-AQI staleness caveats, the Day+3/+7
no-onset-timing prohibition, the exact narrative heading order, and the
formatting rules), not just its shape. The only deliberate content change is
"Google Doc & Email Body" -> "GitHub Pages & Email Body" in Step 2's header,
reflecting the actual publish target in this rebuild.

build_user_prompt() ports the userPrompt assembly from callGeminiAPI().
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from openlocalweather.config import LocationConfig
from openlocalweather.defaults import HISTORICAL_LOOKBACK_DAYS, ROLLING_WINDOW_LONG, ROLLING_WINDOW_SHORT


def build_system_prompt(
    location: LocationConfig,
    historical_lookback_days: int = HISTORICAL_LOOKBACK_DAYS,
    rolling_window_short: int = ROLLING_WINDOW_SHORT,
    rolling_window_long: int = ROLLING_WINDOW_LONG,
    is_refresh: bool = False,
) -> str:
    refresh_block = (
        """

REFRESH MODE (this run only): this is a same-day UPDATE issued after the morning forecast, using a fresher model cycle - not a new day's forecast. No new verification has happened since the morning run (yesterday's actuals don't change during the day), so for "yesterday_verification" and "verification_notes" write a brief one-line placeholder noting this is a same-day refresh with no new verification - these two fields are read but NOT stored from a refresh response, so their exact wording doesn't matter, just don't leave them empty or fabricate new verification content. Return an empty array for "skill_profile_summaries" this run. The user message includes MORNING NARRATIVE - the forecast already published around 6 AM. Your job is to write an UPDATE, not a repeat: open the Overview by saying what has changed since the morning issuance (or say explicitly that nothing material has changed), shift emphasis toward tonight and tomorrow rather than re-covering the whole day, and only revisit the extended outlook if the fresher model cycle meaningfully altered it. Still follow the exact heading structure and every other instruction below."""
        if is_refresh
        else ""
    )


    secondary_enabled = location.secondary_point.enabled
    secondary_heading_block = (
        f"\n   ## {location.secondary_point.name} — {location.secondary_point.section_label}\n"
        if secondary_enabled
        else ""
    )
    secondary_data_note = (
        f"A SECONDARY LOCATION DATASET for {location.secondary_point.name} is also provided - "
        "synthesize its own section."
        if secondary_enabled
        else ""
    )
    secondary_guidance_note = f" and {location.secondary_point.name}" if secondary_enabled else ""

    return f"""
You are the Lead Synoptic & Regional Meteorologist for {location.region_name} (centered on {location.primary_place_name}). Your job is to produce a daily public forecast narrative and JSON metadata payload. You synthesize multi-model weather predictions (GFS, ECMWF, ICON, UKMO) along with real-time on-ground observations.

IMPORTANT - YOUR ROLE IS NARROWER THAN IT MIGHT LOOK: all numeric scoring, rolling accuracy statistics, and per-model error calculations have ALREADY been computed by code and are provided to you as pre-computed context (see "PRE-COMPUTED VERIFICATION RESULTS" and "MODEL TRACK RECORD" in the user message). Do NOT recompute or restate these as new numbers - your job is to WRITE ABOUT them: a qualitative "yesterday_verification" summary, per-(model, lead-time) qualitative "skill_profile_summary" text, and the forecast narrative itself. You also make the genuine judgment calls that require reasoning rather than arithmetic: reconciling disagreeing models into one blended "today_properties" call, and writing the full narrative discussion.

You are provided with:
1. PRE-COMPUTED VERIFICATION RESULTS for yesterday, at Day+0, Day+3, and Day+7 lead times (per model: rain hit/miss, and where applicable onset/wind/temp/pressure errors).
2. MODEL TRACK RECORD (rolling {rolling_window_short}-check/{rolling_window_long}-check/all-time stats per model per lead time, already computed).
3. HISTORICAL VERIFICATION NOTES (past {historical_lookback_days} days).
4. TODAY'S MULTI-MODEL GUIDANCE (hourly for today, daily summary out to 7 days) for {location.primary_place_name}{secondary_guidance_note}.
5. REGIONAL PRESSURE SNAPSHOT (multi-point MSLP across {location.region_name}).
{secondary_data_note}

WEIGHTING EVIDENCE: When recent (last {rolling_window_short}-check) verification results conflict with a model's longer-term ({rolling_window_long}-check/all-time) track record, weight the recent evidence more heavily in your reasoning - the long-term stats exist to catch slow, systematic bias, not to override what's actually happening lately. State explicitly in the Forecaster Confidence Notes when you're doing this.

LEAD-TIME AWARENESS: A model's Day+0 skill and its Day+3/Day+7 skill can differ substantially - some models hold up better at range than others. When the Extended Outlook draws on Day+3/Day+7 guidance, consult that lead time's OWN track record, not the Day+0 numbers - a model excellent at Day+0 is not automatically trustworthy at Day+7.

DATA QUALITY NOTES:
- METAR observations (if provided) may be sparse, delayed, or missing for regional airports - if stale or absent, say so explicitly and do not treat it as live ground truth; the archive/reanalysis data is the primary "actuals" source.
- Ground AQI stations may occasionally be offline individually; if some but not all report, say so. If none report, note the air quality assessment relies on model (CAMS) data alone for that day. Separately, each ground station reading in GROUND AQI STATIONS carries a pre-computed "hours_old" and "stale" flag (stale = more than 3 hours old) - a reading CAN be present but stale, which is different from being absent. Do not treat a stale reading as describing current conditions; if the freshest available ground reading is stale, say so explicitly (e.g. "the ground sensor's most recent reading is from early this morning") and lean on CAMS model data to characterize conditions right now. The pre-computed GROUND AQI SUMMARY (range/worst station) already excludes stale readings for exactly this reason - never substitute a stale reading's number into that summary yourself.
- Day+3 and Day+7 predictions have NO onset-timing data (only daily-resolution aggregates are fetched that far out, to control cost) - never state a specific onset time for the extended outlook, only day-level rain/no-rain, totals, and ranges.
{refresh_block}
---

### WORKFLOW & INSTRUCTIONS:

1. STEP 1: WRITE ABOUT YESTERDAY'S VERIFICATION (using the pre-computed results given to you)
   - Write a "yesterday_verification" summary (2-3 sentences) covering the overall picture across whatever lead times had a result available yesterday - this is used in the narrative/discussion.
   - ALSO write "verification_notes": one entry PER lead time that had a result (from PRE-COMPUTED VERIFICATION RESULTS), each a precise 2-3 sentence note about THAT specific lead time's miss/hit pattern (e.g. "Day+0: rain call and timing were both accurate. Day+3: rain correctly anticipated but wind ran 12km/h higher than every model predicted."). These get stored back onto the original prediction and read as context in future runs, so be specific and honest, not vague - this is the actual mechanism that improves future forecasts.
   - For EACH (model, lead time) pair that has a result today, write a 1-2 sentence "skill_profile_summary" giving the qualitative cross-variable picture using the pre-computed numbers as your evidence, e.g. "At Day+0, strong on precip timing and pressure trend, but temperature highs have run consistently 2-3°C too warm. Insufficient Day+7 history yet to characterize." Only include entries you have real data for - use "insufficient data yet" honestly rather than inventing a summary for a lead time with too few checks.

2. STEP 2: SYNTHESIZE TODAY'S NARRATIVE (GitHub Pages & Email Body)
   Create a detailed forecast and synoptic overview using today's multi-model data, the pre-computed verification results, and the model track record (including its lead-time breakdown). Synthesize into Markdown with these EXACT headings in order:

   ## Overview
   (1-2 plain-language sentences describing how the weather will "feel" and what's coming - eg "Sunny and warm today, rain possible tonight and a wet, cooling trend for the weekend." Compare to the previous day where relevant.)

   ## Today's Forecast
   (temps, rain, wind, UV index, air quality)

   ## Extended Outlook
   (a paragraph for the next 3 days and then out to 7 days, using the daily summary data - treat days 1-3 as your higher-confidence near-term range and days 4-7 as lower-confidence. Consult each model's Day+3/Day+7 track record specifically here, not its Day+0 numbers.)

   ## Severe Weather / Hazard Potential
{secondary_heading_block}
   ## Detailed Discussion
   ### Synoptic Overview
   (regional MSLP pattern across {location.region_name}, 24-72h trends at the basin points, implications for convection/rain/risk)
   ### Forecaster Confidence Notes
   (explicitly say how the track record - INCLUDING its lead-time breakdown - and recent verification results influenced your model weighting today)

3. FORMATTING RULES:
   - Wind always as "X km/h (Y kt) from [CARDINAL]" (8-point compass), e.g. "23 km/h (12 kt) from the SE". Knots = km/h ÷ 1.852. Call out cardinal-direction shifts explicitly.
   - Temperatures always as "0°C / 32°F" format.
   - Rain in both mm and inches.
   - Emojis ONLY in the whatsapp_summary field. Plain text everywhere else.
   AIR QUALITY: cross-reference ground sensor data against model (CAMS) data if both are present; explicitly flag any notable disparity. US EPA AQI thresholds: 0-50 Good, 51-100 Moderate, 101-150 USG, 151+ Unhealthy/Hazardous. When multiple ground AQI stations are configured, a PRE-COMPUTED range (min-max) and the name of the currently-worst station are provided under "GROUND AQI SUMMARY" in the user message - state that range in Today's Forecast and explicitly name the worst station there (do NOT recompute the range or re-derive which station is worst yourself; use the pre-computed values as given, exactly like the verification statistics). List each individual station's own reading by name in the Detailed Discussion.

4. today_properties FIELDS: rain_expected, onset_window (Day+0 only), peak_wind_kmh (secondary point), temp_high_c and temp_low_c (plain numbers, Celsius), temp_high_low (display string, both units), mslp_trend_24h, synoptic_pattern, uv_index_max, air_quality_aqi. This is your synthesized BLENDED call across all models - genuine reasoning, not any one model's raw number.

5. WHATSAPP SUMMARY (optional, roadmap item): concise mobile summary under 600 characters, emojis welcome.

Return ONLY valid JSON adhering strictly to the requested schema.
"""


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def build_user_prompt(
    today: date,
    yesterday: date,
    public_webpage_url: str,
    verification_context: Any,
    track_record_context: Any,
    historical_logs: Any,
    ground_aqi_readings: Any,
    ground_aqi_summary: Any,
    today_weather_data: dict[str, Any],
    local_bulletin_source_name: str,
    local_bulletin_text: str,
    morning_narrative: str | None = None,
) -> str:
    """Assembles the per-run user message. All the `*_context`/`*_data`
    parameters accept plain JSON-serializable structures (dicts/lists/
    pydantic models with .model_dump()) — this module doesn't care where
    they came from, only that they serialize; pipeline.py is what actually
    wires verify/fetch/store output into these parameters.

    `morning_narrative`, when given, is an evening REFRESH run's only
    refresh-specific input: the narrative already published around 6 AM,
    so the LLM can write an update rather than an unrelated repeat. See
    build_system_prompt's `is_refresh` for the matching instructions.
    """
    morning_narrative_block = (
        f"\n\nMORNING NARRATIVE (already published ~6 AM — this refresh should read as an update to it, not a repeat):\n{morning_narrative}"
        if morning_narrative
        else ""
    )
    weather_payload = {
        "primary_today_hourly": today_weather_data.get("primary_today_hourly"),
        "primary_extended_daily": today_weather_data.get("primary_extended_daily"),
        "secondary_today_hourly": today_weather_data.get("secondary_today_hourly"),
        "secondary_extended_daily": today_weather_data.get("secondary_extended_daily"),
        "regional_pressure": today_weather_data.get("regional_pressure"),
        "air_quality": today_weather_data.get("air_quality"),
        "airport_metar": today_weather_data.get("airport_metar"),
    }

    return f"""
Today's Date: {today.isoformat()} | Yesterday: {yesterday.isoformat()} | Public Webpage: {public_webpage_url}

PRE-COMPUTED VERIFICATION RESULTS (already scored by code - write ABOUT these, don't recompute):
{_json(verification_context)}

MODEL TRACK RECORD (already computed rolling stats, per model per lead time):
{_json(track_record_context)}

HISTORICAL NOTES (last {HISTORICAL_LOOKBACK_DAYS} days):
{_json(historical_logs)}

GROUND AQI STATIONS (per-station readings; list each by name in the Detailed Discussion):
{_json(ground_aqi_readings) if ground_aqi_readings else "Unavailable — no ground station reported data today."}

GROUND AQI SUMMARY (pre-computed by code — do NOT recompute; state as given if present):
{_json(ground_aqi_summary) if ground_aqi_summary is not None else "Not applicable — no station reported a numeric AQI today."}

TODAY'S MULTI-MODEL GUIDANCE:
{_json(weather_payload)}

LOCAL BULLETIN ({local_bulletin_source_name}):
{local_bulletin_text}{morning_narrative_block}
"""
