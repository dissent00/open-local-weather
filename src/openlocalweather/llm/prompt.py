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
    is_reissue: bool = False,
) -> str:
    reissue_block = (
        """

LATER ISSUANCE (this run only): a forecast for today has already been published — see EARLIER TODAY in the user message, which lists each previous issuance with the time it went out. Yesterday's actuals do not change during the day, so no new verification has happened: write a brief one-line placeholder for "yesterday_verification" and "verification_notes" noting this, and return an empty array for "skill_profile_summaries". Those three fields are read but NOT stored on a later issuance, so their wording does not matter; just do not leave them empty or invent verification that did not occur.

Your job is an UPDATE, not a repeat. Open the Overview with what has changed since the last issuance. If nothing material has changed, say so plainly in a sentence and move on - a reader who has already read this morning's forecast is asking "is it still right?", and the honest answer to that is often "yes", said briefly. Do not manufacture change to justify the update, and do not restate the earlier forecast at length in order to look thorough. Only revisit the extended outlook if the fresher model cycle actually moved it."""
        if is_reissue
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
6. LONG-RUN REVIEW FINDINGS (cross-model conclusions drawn in code from the entire stored record, each with its own evidence and confidence).
7. EXTRACTED PER-MODEL PREDICTIONS - each model's Day+0/Day+3/Day+7 call, already pulled out of the raw guidance in code. These are the exact values that will be scored against tomorrow's observations, and they include the local met service alongside the numerical models where one is configured.
{secondary_data_note}

WEIGHTING EVIDENCE: When recent (last {rolling_window_short}-check) verification results conflict with a model's longer-term ({rolling_window_long}-check/all-time) track record, weight the recent evidence more heavily in your reasoning - the long-term stats exist to catch slow, systematic bias, not to override what's actually happening lately. State explicitly in the Forecaster Confidence Notes when you're doing this. Each (model, lead time) entry in MODEL TRACK RECORD carries a pre-computed "rain_pct_trend" ("improving" / "declining" / "stable" / null) and "rain_pct_trend_delta" - already the recent-vs-longer-term comparison described above, done in code. Use this field as given rather than re-deriving whether recent and long-term agree by comparing the raw percentages yourself; a null trend means there isn't yet enough history in one of the windows to call it either way, and you should say so rather than guessing. When a model's trend is "declining" for a lead time you're relying on, name that explicitly and explain how it affects your confidence - this is exactly the kind of divergence the track record exists to catch.

LOCAL MET SERVICE AS A MODEL: where a national met service is configured, its own forecast appears in EXTRACTED PER-MODEL PREDICTIONS as another model, with its own track record and its own entry in the review findings. Treat it as a peer of the numerical models, not as a more authoritative source and not as a lesser one - what it has earned is whatever its verification record says it has earned, exactly as for GFS or ECMWF. It has genuine local knowledge a global model cannot have, and it is also a forecast that can be wrong; both are settled by the record rather than by deference. Note that it supplies only rain and temperature - no wind, no pressure, no onset - so a null there means "not forecast", never "no rain" or "calm". When it disagrees with the numerical consensus, say so explicitly and explain which way you lean and why, citing its track record at the lead time in question.

LONG-RUN REVIEW FINDINGS: The user message carries a REVIEW section: conclusions computed in code across the whole stored record, each carrying the evidence and confidence that produced it, plus a "data_sufficiency" statement of how much the record currently supports. These are the ONLY cross-model, long-run comparative claims you may make. Each one is gated on sample size in code - a ranking is emitted only when both models have enough verified checks AND their gap exceeds the sampling-noise floor.

The consequence matters: IF NO RANKING FINDING IS PRESENT FOR A LEAD TIME, THE RECORD DOES NOT YET SUPPORT RANKING MODELS AT THAT LEAD TIME. Say so plainly, and do NOT construct your own ranking by comparing the raw percentages in MODEL TRACK RECORD. That comparison has already been performed in code and deliberately withheld because the sample is too small to support it. Eyeballing those percentages yourself would reintroduce precisely the small-sample error the gate exists to prevent - an 8-check record can easily show one model 35 points "ahead" purely by chance. The same applies to bias claims: if no bias finding names a model, do not assert one from the error numbers yourself.

Use each finding at the confidence it states and do not upgrade it - "provisional" is not "established", and a finding is a description of the record so far, never a guarantee about today. Reflect the substance of "data_sufficiency" in the Forecaster Confidence Notes, including - especially - when it says there isn't enough data yet. Being straightforwardly honest that the record is still thin is correct and expected here, not a failure; this system's value comes from its accuracy claims being checkable, which requires never claiming more than the record holds.

LEAD-TIME AWARENESS: A model's Day+0 skill and its Day+3/Day+7 skill can differ substantially - some models hold up better at range than others. When the Extended Outlook draws on Day+3/Day+7 guidance, consult that lead time's OWN track record, not the Day+0 numbers - a model excellent at Day+0 is not automatically trustworthy at Day+7.

DATA QUALITY NOTES:
- METAR observations (if provided) may be sparse, delayed, or missing for regional airports - if stale or absent, say so explicitly and do not treat it as live ground truth; the archive/reanalysis data is the primary "actuals" source.
- Ground AQI stations may occasionally be offline individually; if some but not all report, say so. If none report, note the air quality assessment relies on model (CAMS) data alone for that day. Separately, each ground station reading in GROUND AQI STATIONS carries a pre-computed "hours_old" and "stale" flag (stale = more than 3 hours old) - a reading CAN be present but stale, which is different from being absent. Do not treat a stale reading as describing current conditions; if the freshest available ground reading is stale, say so explicitly (e.g. "the ground sensor's most recent reading is from early this morning") and lean on CAMS model data to characterize conditions right now. The pre-computed GROUND AQI SUMMARY (range/worst station) already excludes stale readings for exactly this reason - never substitute a stale reading's number into that summary yourself.
- Day+3 and Day+7 predictions have NO onset-timing data (only daily-resolution aggregates are fetched that far out, to control cost) - never state a specific onset time for the extended outlook, only day-level rain/no-rain, totals, and ranges.

ISSUANCE TIME: the user message opens with ISSUED, giving the local time, which part of the day it is, and WHAT MATTERS NOW - the periods a reader at this hour actually cares about, most pressing first. Lead with those periods and weight the whole forecast toward them. Do not re-narrate hours that have already passed except where they explain what is coming: someone reading at 18:15 lived through the afternoon and is asking about tonight.

"Tonight" means the whole stretch from dusk through to dawn, as WHAT MATTERS NOW spells out - not just the evening.

HOURS AHEAD gives the hour-by-hour multi-model guidance from the current hour forward, which is the data to reason from for near-term timing. TODAY'S MULTI-MODEL GUIDANCE still carries the full calendar day, needed for daily totals and for the day-over-day comparison; do not use it to describe the day as though it were all still ahead.

The sun times in ISSUED are computed in code and correct for this location and date. State them if useful, never recompute them, and never estimate sunset from latitude or season yourself.
{reissue_block}
---

### WORKFLOW & INSTRUCTIONS:

1. STEP 1: WRITE ABOUT YESTERDAY'S VERIFICATION (using the pre-computed results given to you)
   - Write a "yesterday_verification" summary (2-3 sentences) covering the overall picture across whatever lead times had a result available yesterday - this is used in the narrative/discussion.
   - ALSO write "verification_notes": one entry PER lead time that had a result (from PRE-COMPUTED VERIFICATION RESULTS), each a precise 2-3 sentence note about THAT specific lead time's miss/hit pattern (e.g. "Day+0: rain call and timing were both accurate. Day+3: rain correctly anticipated but wind ran 12km/h higher than every model predicted."). These get stored back onto the original prediction and read as context in future runs, so be specific and honest, not vague - this is the actual mechanism that improves future forecasts.
   - For EACH (model, lead time) pair that has a result today, write a 1-2 sentence "skill_profile_summary" giving the qualitative cross-variable picture using the pre-computed numbers as your evidence, e.g. "At Day+0, strong on precip timing and pressure trend, but temperature highs have run consistently 2-3°C too warm. Insufficient Day+7 history yet to characterize." Only include entries you have real data for - use "insufficient data yet" honestly rather than inventing a summary for a lead time with too few checks.

2. STEP 2: SYNTHESIZE TODAY'S NARRATIVE (GitHub Pages & Email Body)
   Create a detailed forecast and synoptic overview using today's multi-model data, the pre-computed verification results, and the model track record (including its lead-time breakdown). Synthesize into Markdown with these EXACT headings in order:

   ## Overview
   (1-2 plain-language sentences describing how the weather will "feel" and what's coming - eg "Sunny and warm today, rain possible tonight and a wet, cooling trend for the weekend." OPEN with the day-over-day comparison, using the PRE-COMPUTED labels in "DAY-OVER-DAY COMPARISON" in the user message. Readers rarely remember yesterday's numbers, but they do remember how it felt and what they wore, so this is the single most useful orienting sentence in the forecast. Use "high_label", "wind_label" and "rain_contrast" AS GIVEN and phrase them naturally - do NOT subtract the temperatures yourself or invent your own wording for the size of the change, exactly as with the verification statistics. If high_label is "about the same", say the day feels much like yesterday; do not manufacture a difference in order to sound informative. Write ONE flowing sentence, not a list of three labels bolted together: when two or three of them say little has changed, collapse them ("much like yesterday - similar warmth, lighter winds, rain again") rather than stating each in turn, and never repeat "yesterday" more than once in the sentence. Say it ONCE, in the Overview; do not restate the comparison in Today's Forecast or later sections. Compare only against what was actually OBSERVED yesterday - never against yesterday's forecast or its verification scores, which are a different thing and are also in your context. If "DAY-OVER-DAY COMPARISON" is unavailable, simply omit the comparison rather than guessing or hedging about its absence.)

   ## Today's Forecast
   (temps, rain, wind, UV index, air quality)

   ## Extended Outlook
   (a paragraph for the next 3 days and then out to 7 days, using the daily summary data - treat days 1-3 as your higher-confidence near-term range and days 4-7 as lower-confidence. Consult each model's Day+3/Day+7 track record specifically here, not its Day+0 numbers.)

   ## Severe Weather / Hazard Potential
{secondary_heading_block}
   ## Detailed Discussion
   ### Synoptic Overview
   (OPEN with the large-scale picture, then narrow to the local one. "synoptic_scale_pressure" in the user message carries a nine-point pressure ring spanning roughly 2,600 km, already reduced in code to which direction is lowest and highest, the spread between them, and each direction's three-day tendency — plus ready-made "statements". Use those AS GIVEN; do not re-derive which quadrant is lowest by comparing the raw numbers yourself. This is the difference between "a strong gradient with lower pressure to the northeast, and pressure falling to the west" and a bare local trend, and it is the sentence a reader expects here. STAY INSIDE WHAT THE SAMPLING SUPPORTS: say lower pressure LIES TOWARD a direction, never that a named low is centred over a named place, and never state a track, a speed of approach, or a frontal position — points 12 degrees apart locate a direction, not a centre, and the true centre may sit between points or outside the ring. If "synoptic_scale_pressure" is unavailable, say the large-scale picture could not be assessed this run rather than substituting the local gradient for it. THEN cover the regional MSLP pattern across {location.region_name}, 24-72h trends at the basin points, and implications for convection/rain/risk.)
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


def _issued_line(issuance: Any) -> str:
    """The one line telling the model when it is writing.

    Everything in it is computed in `daypart` — the time, the phase, the
    minutes to sunset, and which periods matter now. The model is told, never
    asked to work it out, exactly as with every other number here.
    """
    if issuance is None:
        return "Time of day unavailable this run — write for the day as a whole."
    d = issuance.to_json() if hasattr(issuance, "to_json") else issuance
    horizon = ", then ".join(d.get("horizon") or []) or "today"
    return (
        f"{d.get('statement', '')} "
        f"Part of day: {d.get('phase', 'unknown')}. "
        f"Sunrise {d.get('sunrise', '?')}, sunset {d.get('sunset', '?')}. "
        f"WHAT MATTERS NOW: {horizon}."
    )


def build_user_prompt(
    today: date,
    yesterday: date,
    public_webpage_url: str,
    verification_context: Any,
    track_record_context: Any,
    historical_logs: Any,
    ground_aqi_readings: Any,
    ground_aqi_summary: Any,
    yesterday_actual: Any,
    today_weather_data: dict[str, Any],
    local_bulletin_source_name: str,
    local_bulletin_text: str,
    earlier_today: list[dict] | None = None,
    issuance: Any = None,
    forward_hourly: Any = None,
    review_context: Any = None,
    model_predictions_context: Any = None,
) -> str:
    """Assembles the per-run user message. All the `*_context`/`*_data`
    parameters accept plain JSON-serializable structures (dicts/lists/
    pydantic models with .model_dump()) — this module doesn't care where
    they came from, only that they serialize; pipeline.py is what actually
    wires verify/fetch/store output into these parameters.

    `yesterday_actual` is what was actually OBSERVED yesterday (a
    DailyActual, or None if there's no record) — distinct from the
    verification context, which is how yesterday's *predictions* scored.
    It exists so the Overview can open with a real day-over-day comparison;
    before it was passed, the system prompt asked for that comparison
    without ever supplying the data, which invited vagueness or invention.

    Deliberately NOT accompanied by a pre-computed delta, despite this
    project's usual "arithmetic in code" rule: the comparison is against
    the LLM's own blended call for today, which doesn't exist until it
    responds. A delta computed here against model consensus could
    contradict the numbers the narrative itself goes on to state. Both
    numbers appear in the published text, so any reader can check the
    subtraction.

    `earlier_today`, when given, lists this day's previous issuances as
    {"time", "narrative"} in the order they went out. It replaces what used
    to be a single `morning_narrative`, because the day is no longer assumed
    to have exactly two runs: an operator may schedule two or five, and each
    one after the first needs to know what its readers have already been
    told. See build_system_prompt's `is_reissue`.

    `issuance` is a DayPart — the local time, the part of the day, and what a
    reader at this hour actually wants. Before it existed the prompt carried a
    date and nothing else, so a run at 18:15 could not tell itself apart from
    one at 06:00, and reliably wrote as though the whole day were ahead.

    `forward_hourly` is the multi-model hourly guidance trimmed to the hours
    still to come. It is narrative input only and is never scored — per-model
    predictions come from the untrimmed day-0 fetch, and must, or a model
    would be judged on a partial day against a full day's observation.
    """
    earlier_block = ""
    if earlier_today:
        issued = "\n\n".join(
            f"Issued {e.get('time', 'earlier')}:\n{e.get('narrative', '')}"
            for e in earlier_today
        )
        earlier_block = (
            "\n\nEARLIER TODAY (already published — this issuance must read as "
            "an update to these, not a repeat of them):\n" + issued
        )
    weather_payload = {
        "primary_today_hourly": today_weather_data.get("primary_today_hourly"),
        "primary_extended_daily": today_weather_data.get("primary_extended_daily"),
        "secondary_today_hourly": today_weather_data.get("secondary_today_hourly"),
        "secondary_extended_daily": today_weather_data.get("secondary_extended_daily"),
        "regional_pressure": today_weather_data.get("regional_pressure"),
        "air_quality": today_weather_data.get("air_quality"),
        "airport_metar": today_weather_data.get("airport_metar"),
        # Added late, and briefly forgotten here — the pipeline passed it and
        # this rebuild dropped it, so the Synoptic Overview instructions
        # referred to a key that never arrived. The key-by-key rebuild guards
        # against a stray key ENLARGING the prompt; the cost is that a new key
        # must be added in two places, and test_prompt.py now fails if any key
        # the pipeline sends goes missing here.
        "synoptic_scale_pressure": today_weather_data.get("synoptic_scale_pressure"),
    }

    return f"""
Today's Date: {today.isoformat()} | Yesterday: {yesterday.isoformat()} | Public Webpage: {public_webpage_url}

ISSUED: {_issued_line(issuance)}

HOURS AHEAD (hour-by-hour multi-model guidance from the current hour forward — reason from THIS for near-term timing):
{_json(forward_hourly) if forward_hourly is not None else "Unavailable this run."}

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

DAY-OVER-DAY COMPARISON (pre-computed by code from yesterday's OBSERVED conditions against today's model consensus — do NOT recompute; use high_label / wind_label / rain_contrast as given):
{_json(yesterday_actual) if yesterday_actual is not None else "Unavailable — no observed record for yesterday; omit the day-over-day comparison."}

TODAY'S MULTI-MODEL GUIDANCE:
{_json(weather_payload)}

EXTRACTED PER-MODEL PREDICTIONS (pulled from the raw guidance in code — these exact values get scored, so reason from them rather than re-deriving your own from the arrays below; a null field means that model does not forecast it, never zero or "no"):
{_json(model_predictions_context) if model_predictions_context is not None else "Unavailable this run."}

LONG-RUN REVIEW (computed in code over the whole stored record — these are the only cross-model long-run claims available to you; if a ranking is absent the record does not support one, so do NOT derive your own from the track record above):
{_json(review_context) if review_context is not None else "Unavailable — no review computed this run."}

LOCAL BULLETIN ({local_bulletin_source_name}):
{local_bulletin_text}{earlier_block}
"""
