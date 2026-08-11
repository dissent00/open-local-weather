/**
 * Open Local Weather - Generic Multi-Model Synoptic Forecast Pipeline
 * Originally built for Kisumu, Kenya; designed to be forked for ANY location
 * underserved by professional meteorology - see the LOCATION CONFIG block.
 *
 * Schedule: runs daily via Apps Script time-based trigger (createDailyTrigger()).
 *
 * ARCHITECTURE, v2 (this revision):
 * - Code (not the LLM) does ALL numeric work: fetching, extracting raw
 *   per-model predictions, aggregating actuals, scoring, and computing rolling
 *   accuracy stats. Gemini's role is narrowed to what LLMs are actually good
 *   at: writing the qualitative verification notes, skill-profile summaries,
 *   and the forecast narrative itself, using pre-computed numbers as context.
 *   This matters more as complexity grows - an LLM silently miscomputing a
 *   rolling percentage would corrupt the accuracy signal the whole system
 *   depends on, and that failure mode is very hard to notice from the outside.
 * - LEAD-TIME TRACKING: previously only "today's" (Day+0) prediction was ever
 *   scored. This version also stores and scores Day+3 and Day+7 predictions
 *   per model, since model skill decay with lead time is a real and uneven
 *   property across models (e.g. ECMWF often holds up better than GFS at
 *   longer range even when comparable at Day+0) - the old design was blind
 *   to this entirely.
 * - VERIFICATION METHOD: each run fetches ONE batch of actual conditions
 *   covering the last ~31 days (Open-Meteo's archive keeps this indefinitely),
 *   then re-derives the last 10/30 checks' scores at each lead time from
 *   that batch plus each historical row's already-stored raw predictions.
 *   Stateless and self-correcting - no fragile running totals to drift out
 *   of sync, except All-Time counts, which are incremented deterministically.
 * - DATA RETENTION: Forecast Log rows older than LOCATION.RETENTION_DAYS are
 *   moved to an Archive sheet (not deleted) during each run - keeps the
 *   active sheet lean without losing the historical record.
 * - LOCATION CONFIG: everything specific to Kisumu/Lake Victoria/Nyanza has
 *   been pulled into the LOCATION block below. Forking this for a new place
 *   should mean editing that block and nothing else - the system prompt is
 *   built dynamically from it.
 */

// ==========================================
// LOCATION CONFIG - edit this block to fork for a new place
// ==========================================
const LOCATION = {
  // Human-readable names used throughout the narrative and prompt.
  REGION_NAME: "Nyanza Basin",
  PRIMARY_PLACE_NAME: "Kisumu, Kenya",
  TIMEZONE: "Africa/Nairobi",

  // Main forecast point (a town/city).
  PRIMARY_POINT: { lat: -0.0917, lon: 34.7680 },

  // Optional second point of interest - a lake, coastline, mountain, etc.
  // Set enabled:false and this entire section (data + narrative) is skipped
  // for locations without a relevant secondary feature.
  SECONDARY_POINT: {
    enabled: true,
    name: "Lake Victoria",
    section_label: "Conditions for Boaters",
    lat: -0.75, lon: 33.15
  },

  // Neighboring points for the regional pressure-gradient snapshot (used for
  // the Synoptic Overview section). 3-6 points spread across the wider area
  // works well; more adds fetch cost without much narrative benefit.
  REGION_POINTS: [
    { name: "Siaya", lat: 0.0607, lon: 34.2881 },
    { name: "Homa Bay", lat: -0.5273, lon: 34.4571 },
    { name: "Kisii", lat: -0.6773, lon: 34.7796 },
    { name: "Migori", lat: -1.0634, lon: 34.4731 },
  ],

  // ICAO code for a nearby airport with METAR reporting (aviationweather.gov
  // covers most of the world, not just the US). Leave "" to skip METAR.
  METAR_STATION_ICAO: "HKKI",

  // WAQI ground air-quality station ID. VERIFY THIS MANUALLY at waqi.info -
  // it cannot be checked from code, and a wrong ID silently poisons the
  // "ground truth" AQI comparison. Leave "" to skip ground AQI entirely.
  WAQI_STATION_ID: "A418534",

  // Optional local met-service bulletin. This is inherently location-specific
  // (formats vary wildly - HTML, PDF, sometimes nothing usable at all) and
  // can't be meaningfully generalized; fetchLocalBulletinText() below is
  // written for KMD's specific PDF-behind-a-landing-page pattern and will
  // likely need rewriting per location. Set LOCAL_BULLETIN_URL to "" and
  // fetchLocalBulletinText() will skip gracefully if no equivalent exists.
  LOCAL_BULLETIN_URL: "https://meteo.go.ke/our-products/7-days-forecast/",
  LOCAL_BULLETIN_SOURCE_NAME: "Kenya Meteorological Department (KMD)",
};

// ==========================================
// SYSTEM CONFIG
// ==========================================
const CONFIG = {
  GEMINI_API_KEY: PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY'),
  SPREADSHEET_ID: PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID'),
  DOC_ID: PropertiesService.getScriptProperties().getProperty('DOC_ID'),
  WAQI_TOKEN: PropertiesService.getScriptProperties().getProperty('WAQI_TOKEN'),
  FORM_SUBSCRIBE_URL: "https://forms.gle/Q7NqX7tQqifY5aA27",
  GEMINI_MODEL: "gemini-3.6-flash",

  MODELS: ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless", "best_match"],
  LEAD_TIMES_DAYS: [0, 3, 7],       // which lead times get tracked separately
  ROLLING_WINDOW_SHORT: 10,          // "recent" window, in verified checks
  ROLLING_WINDOW_LONG: 30,           // "longer-term" window, in verified checks
  ACTUALS_BATCH_LOOKBACK_DAYS: 31,   // must be >= ROLLING_WINDOW_LONG + max(LEAD_TIMES_DAYS)... see note below
  HISTORICAL_LOOKBACK_DAYS: 30,      // how many days of notes to give Gemini as context

  LOG_RETENTION_DAYS: 180,           // rows older than this get archived, not deleted
};
// Note on ACTUALS_BATCH_LOOKBACK_DAYS: for a rolling window of N checks at lead
// time k, the OLDEST target date needed is (yesterday - N), and the row that
// MADE that prediction is dated (target - k), so Forecast Log needs at least
// (N + max(LEAD_TIMES_DAYS)) days of retained history for the longest window
// to fully populate. At defaults (30 + 7 = 37), comfortably under the 180-day
// retention window, so this isn't a real constraint in practice.

// Single source of truth for Forecast Log columns (1-indexed). Every
// read/write function uses this map, never a hardcoded number - this is
// what prevents the exact bug class found in the prior revision (mismatched
// column indices between write and read functions) from recurring.
const LOG_COLUMNS = {
  DATE: 1,
  RAIN_EXPECTED: 2,             // blended/synthesized call, Day+0
  ONSET_WINDOW: 3,               // blended, Day+0 only (no hourly data for +3/+7)
  SECONDARY_PEAK_WIND_KMH: 4,    // blended, secondary point (lake/coast/etc.)
  TEMP_HIGH_LOW_DISPLAY: 5,       // e.g. "26°C / 79°F"
  MSLP_TREND_24H: 6,
  SYNOPTIC_PATTERN: 7,
  GROUND_AQI: 8,
  MODEL_PREDICTIONS_DAY0_RAW: 9,
  MODEL_PREDICTIONS_DAY3_RAW: 10,
  MODEL_PREDICTIONS_DAY7_RAW: 11,
  DAY0_VERIFIED: 12,
  DAY0_VERIFICATION_NOTE: 13,
  DAY3_VERIFIED: 14,
  DAY3_VERIFICATION_NOTE: 15,
  DAY7_VERIFIED: 16,
  DAY7_VERIFICATION_NOTE: 17,
  NARRATIVE: 18,
  TEMP_HIGH_C: 19,
  TEMP_LOW_C: 20,
};
const LOG_LAST_COLUMN = 20;

// Model Track Record is NORMALIZED (long format): one row per (model, lead
// time) pair, not one row per model - a wide "12 columns x 3 lead times"
// design gets unwieldy fast. 5 models x 3 lead times = 15 rows.
const TRACK_COLUMNS = {
  MODEL: 1,
  LEAD_TIME_DAYS: 2,
  ROLLING_10_RAIN_PCT: 3,
  ROLLING_30_RAIN_PCT: 4,
  ALL_TIME_CHECKS: 5,
  ALL_TIME_CORRECT: 6,
  ALL_TIME_RAIN_PCT: 7,
  AVG_ONSET_ERROR_HRS_10: 8,      // Day+0 only; blank for +3/+7 (no hourly data that far out)
  AVG_WIND_ERROR_KMH_10: 9,
  AVG_TEMP_HIGH_ERROR_C_10: 10,
  AVG_TEMP_LOW_ERROR_C_10: 11,
  AVG_MSLP_TREND_ERROR_HPA_10: 12,
  CHECKS_IN_WINDOW_10: 13,        // how many of the last 10 actually had data (cold-start visibility)
  LAST_UPDATED: 14,
  SKILL_PROFILE_SUMMARY: 15,      // Gemini-written, qualitative
  NOTES: 16,
};
const TRACK_LAST_COLUMN = 16;

// ==========================================
// SYSTEM PROMPT (built dynamically from LOCATION)
// ==========================================
function buildSystemPrompt() {
  const secondary = LOCATION.SECONDARY_POINT.enabled
    ? `## ${LOCATION.SECONDARY_POINT.name} — ${LOCATION.SECONDARY_POINT.section_label}\n`
    : "";
  const secondaryDataNote = LOCATION.SECONDARY_POINT.enabled
    ? `A SECONDARY LOCATION DATASET for ${LOCATION.SECONDARY_POINT.name} is also provided - synthesize its own section.`
    : "";

  return `
You are the Lead Synoptic & Regional Meteorologist for ${LOCATION.REGION_NAME} (centered on ${LOCATION.PRIMARY_PLACE_NAME}). Your job is to produce a daily public forecast narrative and JSON metadata payload. You synthesize multi-model weather predictions (GFS, ECMWF, ICON, UKMO) along with real-time on-ground observations.

IMPORTANT - YOUR ROLE IS NARROWER THAN IT MIGHT LOOK: all numeric scoring, rolling accuracy statistics, and per-model error calculations have ALREADY been computed by code and are provided to you as pre-computed context (see "PRE-COMPUTED VERIFICATION RESULTS" and "MODEL TRACK RECORD" in the user message). Do NOT recompute or restate these as new numbers - your job is to WRITE ABOUT them: a qualitative "yesterday_verification" summary, per-(model, lead-time) qualitative "skill_profile_summary" text, and the forecast narrative itself. You also make the genuine judgment calls that require reasoning rather than arithmetic: reconciling disagreeing models into one blended "today_properties" call, and writing the full narrative discussion.

You are provided with:
1. PRE-COMPUTED VERIFICATION RESULTS for yesterday, at Day+0, Day+3, and Day+7 lead times (per model: rain hit/miss, and where applicable onset/wind/temp/pressure errors).
2. MODEL TRACK RECORD (rolling 10-check/30-check/all-time stats per model per lead time, already computed).
3. HISTORICAL VERIFICATION NOTES (past ${CONFIG.HISTORICAL_LOOKBACK_DAYS} days).
4. TODAY'S MULTI-MODEL GUIDANCE (hourly for today, daily summary out to 7 days) for ${LOCATION.PRIMARY_PLACE_NAME}${LOCATION.SECONDARY_POINT.enabled ? ` and ${LOCATION.SECONDARY_POINT.name}` : ""}.
5. REGIONAL PRESSURE SNAPSHOT (multi-point MSLP across ${LOCATION.REGION_NAME}).
${secondaryDataNote}

WEIGHTING EVIDENCE: When recent (last ${CONFIG.ROLLING_WINDOW_SHORT}-check) verification results conflict with a model's longer-term (${CONFIG.ROLLING_WINDOW_LONG}-check/all-time) track record, weight the recent evidence more heavily in your reasoning - the long-term stats exist to catch slow, systematic bias, not to override what's actually happening lately. State explicitly in the Forecaster Confidence Notes when you're doing this.

LEAD-TIME AWARENESS: A model's Day+0 skill and its Day+3/Day+7 skill can differ substantially - some models hold up better at range than others. When the Extended Outlook draws on Day+3/Day+7 guidance, consult that lead time's OWN track record, not the Day+0 numbers - a model excellent at Day+0 is not automatically trustworthy at Day+7.

DATA QUALITY NOTES:
- METAR observations (if provided) may be sparse, delayed, or missing for regional airports - if stale or absent, say so explicitly and do not treat it as live ground truth; the archive/reanalysis data is the primary "actuals" source.
- Ground AQI sensor data may occasionally be offline; if so, note the air quality assessment relies on model (CAMS) data alone for that day.
- Day+3 and Day+7 predictions have NO onset-timing data (only daily-resolution aggregates are fetched that far out, to control cost) - never state a specific onset time for the extended outlook, only day-level rain/no-rain, totals, and ranges.

---

### WORKFLOW & INSTRUCTIONS:

1. STEP 1: WRITE ABOUT YESTERDAY'S VERIFICATION (using the pre-computed results given to you)
   - Write a "yesterday_verification" summary (2-3 sentences) covering the overall picture across whatever lead times had a result available yesterday - this is used in the narrative/discussion.
   - ALSO write "verification_notes": one entry PER lead time that had a result (from PRE-COMPUTED VERIFICATION RESULTS), each a precise 2-3 sentence note about THAT specific lead time's miss/hit pattern (e.g. "Day+0: rain call and timing were both accurate. Day+3: rain correctly anticipated but wind ran 12km/h higher than every model predicted."). These get stored back onto the original prediction and read as context in future runs, so be specific and honest, not vague - this is the actual mechanism that improves future forecasts.
   - For EACH (model, lead time) pair that has a result today, write a 1-2 sentence "skill_profile_summary" giving the qualitative cross-variable picture using the pre-computed numbers as your evidence, e.g. "At Day+0, strong on precip timing and pressure trend, but temperature highs have run consistently 2-3°C too warm. Insufficient Day+7 history yet to characterize." Only include entries you have real data for - use "insufficient data yet" honestly rather than inventing a summary for a lead time with too few checks.

2. STEP 2: SYNTHESIZE TODAY'S NARRATIVE (Google Doc & Email Body)
   Create a detailed forecast and synoptic overview using today's multi-model data, the pre-computed verification results, and the model track record (including its lead-time breakdown). Synthesize into Markdown with these EXACT headings in order:

   ## Overview
   (1-2 plain-language sentences describing how the weather will "feel" and what's coming - eg "Sunny and warm today, rain possible tonight and a wet, cooling trend for the weekend." Compare to the previous day where relevant.)

   ## Today's Forecast
   (temps, rain, wind, UV index, air quality)

   ## Extended Outlook
   (a paragraph for the next 3 days and then out to 7 days, using the daily summary data - treat days 1-3 as your higher-confidence near-term range and days 4-7 as lower-confidence. Consult each model's Day+3/Day+7 track record specifically here, not its Day+0 numbers.)

   ## Severe Weather / Hazard Potential

   ${secondary}${LOCATION.SECONDARY_POINT.enabled ? "" : ""}
   ## Detailed Discussion
   ### Synoptic Overview
   (regional MSLP pattern across ${LOCATION.REGION_NAME}, 24-72h trends at the basin points, implications for convection/rain/risk)
   ### Forecaster Confidence Notes
   (explicitly say how the track record - INCLUDING its lead-time breakdown - and recent verification results influenced your model weighting today)

3. FORMATTING RULES:
   - Wind always as "X km/h (Y kt) from [CARDINAL]" (8-point compass), e.g. "23 km/h (12 kt) from the SE". Knots = km/h ÷ 1.852. Call out cardinal-direction shifts explicitly.
   - Temperatures always as "0°C / 32°F" format.
   - Rain in both mm and inches.
   - Emojis ONLY in the whatsapp_summary field. Plain text everywhere else.
   AIR QUALITY: cross-reference ground sensor data against model (CAMS) data if both are present; explicitly flag any notable disparity. US EPA AQI thresholds: 0-50 Good, 51-100 Moderate, 101-150 USG, 151+ Unhealthy/Hazardous.

4. today_properties FIELDS: rain_expected, onset_window (Day+0 only), peak_wind_kmh (secondary point), temp_high_c and temp_low_c (plain numbers, Celsius), temp_high_low (display string, both units), mslp_trend_24h, synoptic_pattern, uv_index_max, air_quality_aqi. This is your synthesized BLENDED call across all models - genuine reasoning, not any one model's raw number.

5. WHATSAPP SUMMARY (optional, roadmap item): concise mobile summary under 600 characters, emojis welcome.

Return ONLY valid JSON adhering strictly to the requested schema.
`;
}

// ==========================================
// MAIN PIPELINE EXECUTION
// ==========================================
function runDailyForecastPipeline() {
  const today = new Date();
  const yesterday = addDays(today, -1);
  const todayStr = formatDate(today);
  const yesterdayStr = formatDate(yesterday);

  Logger.log(`Starting pipeline for ${LOCATION.PRIMARY_PLACE_NAME} - ${todayStr}...`);

  const ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  const logSheet = ss.getSheetByName("Forecast Log");
  const trackSheet = ss.getSheetByName("Model Track Record");

  // 1. Fetch today's forward-looking model guidance + local sources.
  const weatherData = fetchAllWeatherData();
  const groundAQI = fetchGroundAQI();

  // 2. Fetch ONE batch of actuals covering the verification lookback window -
  //    this single fetch supports re-scoring rolling stats at every lead time.
  const batchStart = formatDate(addDays(today, -CONFIG.ACTUALS_BATCH_LOOKBACK_DAYS));
  const actualsBatchPrimary = fetchActualsBatch(LOCATION.PRIMARY_POINT, batchStart, yesterdayStr);
  const actualsBatchSecondary = LOCATION.SECONDARY_POINT.enabled
    ? fetchActualsBatch(LOCATION.SECONDARY_POINT, batchStart, yesterdayStr)
    : null;
  const dailyActualsPrimary = bucketHourlyByDate(actualsBatchPrimary);
  const dailyActualsSecondary = actualsBatchSecondary ? bucketHourlyByDate(actualsBatchSecondary) : null;

  // 3. Deterministic verification + rolling stats, written directly to the
  //    Track Record sheet. Returns the pre-computed results to hand to Gemini.
  const verificationContext = runDeterministicVerificationAndScoring(
    logSheet, trackSheet, dailyActualsPrimary, dailyActualsSecondary, todayStr, yesterdayStr
  );

  // 4. Historical notes context for Gemini.
  const historicalLogs = getHistoricalNotes(logSheet, CONFIG.HISTORICAL_LOOKBACK_DAYS);
  const trackRecordContext = getModelTrackRecord(trackSheet);

  // 5. Extract TODAY's raw per-model predictions deterministically (code, not LLM).
  const day0Raw = extractDay0PredictionsFromHourly(weatherData.primary_today_hourly);
  const day3Raw = extractDayNPredictionsFromDaily(weatherData.primary_extended_daily, 3);
  const day7Raw = extractDayNPredictionsFromDaily(weatherData.primary_extended_daily, 7);

  // 6. Call Gemini - narrative + qualitative notes + blended headline only.
  const docPublicUrl = `https://docs.google.com/document/d/${CONFIG.DOC_ID}/pub`;
  const geminiResponse = callGeminiAPI(
    weatherData, groundAQI, historicalLogs, trackRecordContext,
    verificationContext, todayStr, yesterdayStr, docPublicUrl
  );

  if (!geminiResponse) {
    Logger.log("Critical Error: Pipeline aborted due to Gemini API failure.");
    return;
  }

  // 7. Append today's row - raw predictions came from code (step 5), not Gemini.
  const groundAqiString = groundAQI ? `US AQI: ${groundAQI.aqi} (PM2.5: ${groundAQI.pm25})` : "N/A";
  appendTodayRow(logSheet, todayStr, geminiResponse.today_properties, groundAqiString,
                 geminiResponse.today_narrative, day0Raw, day3Raw, day7Raw);

  // 8. Write Gemini's qualitative notes/summaries back to the sheets.
  //    (numeric Track Record columns were already written by step 3; this is
  //    ONLY the text fields, written to the ORIGINAL rows that made each
  //    prediction, not today's row.)
  writeSkillProfileSummaries(trackSheet, geminiResponse.skill_profile_summaries);
  writeVerificationNotes(logSheet, geminiResponse.verification_notes, yesterdayStr);

  // 9. Publish + email.
  updateGoogleDocPage(geminiResponse.today_narrative, todayStr);
  const subscriberSheet = ss.getSheetByName("Subscribers");
  sendEmailBroadcast(subscriberSheet, geminiResponse.today_narrative, todayStr);

  // 10. Housekeeping: archive old rows so the sheet doesn't grow unbounded.
  archiveOldLogEntries(ss, logSheet, todayStr);

  Logger.log("Pipeline execution completed successfully.");
}

// ==========================================
// DATA FETCHING
// ==========================================
function fetchJSON(url) {
  try {
    const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) return { error: `HTTP ${res.getResponseCode()}`, url };
    return JSON.parse(res.getContentText());
  } catch (e) {
    return { error: e.toString(), url };
  }
}

function fetchAllWeatherData() {
  const models = CONFIG.MODELS.join(",");
  const p = LOCATION.PRIMARY_POINT;
  const s = LOCATION.SECONDARY_POINT;

  const urls = {
    primary_today_hourly: `https://api.open-meteo.com/v1/forecast?latitude=${p.lat}&longitude=${p.lon}&hourly=temperature_2m,precipitation_probability,precipitation,cloud_cover,windspeed_10m,windgusts_10m,winddirection_10m,cape,pressure_msl,uv_index&forecast_days=1&timezone=${LOCATION.TIMEZONE}&models=${models}`,
    // forecast_days=8 (not 7!) so index 7 genuinely represents 7 full days out.
    primary_extended_daily: `https://api.open-meteo.com/v1/forecast?latitude=${p.lat}&longitude=${p.lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max,windgusts_10m_max,pressure_msl_mean,uv_index_max&forecast_days=8&timezone=${LOCATION.TIMEZONE}&models=${models}`,
    regional_pressure: `https://api.open-meteo.com/v1/forecast?latitude=${[p, ...LOCATION.REGION_POINTS].map(x => x.lat).join(",")}&longitude=${[p, ...LOCATION.REGION_POINTS].map(x => x.lon).join(",")}&daily=precipitation_sum,windspeed_10m_max,pressure_msl_mean&forecast_days=7&timezone=${LOCATION.TIMEZONE}&models=best_match`,
    air_quality: `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=${p.lat}&longitude=${p.lon}&hourly=pm10,pm2_5,european_aqi,us_aqi&forecast_days=1&timezone=${LOCATION.TIMEZONE}`,
  };
  if (LOCATION.METAR_STATION_ICAO) {
    urls.airport_metar = `https://aviationweather.gov/api/data/metar?ids=${LOCATION.METAR_STATION_ICAO}&format=json`;
  }
  if (s.enabled) {
    urls.secondary_today_hourly = `https://api.open-meteo.com/v1/forecast?latitude=${s.lat}&longitude=${s.lon}&hourly=temperature_2m,precipitation_probability,precipitation,cloud_cover,windspeed_10m,windgusts_10m,winddirection_10m,cape,pressure_msl&forecast_days=1&timezone=${LOCATION.TIMEZONE}&models=${models}`;
    urls.secondary_extended_daily = `https://api.open-meteo.com/v1/forecast?latitude=${s.lat}&longitude=${s.lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max,windgusts_10m_max,pressure_msl_mean&forecast_days=8&timezone=${LOCATION.TIMEZONE}&models=${models}`;
  }

  const data = {};
  for (const key in urls) data[key] = fetchJSON(urls[key]);
  data.local_bulletin_text = fetchLocalBulletinText();
  return data;
}

/** Fetch a date-range of actual/reanalysis hourly data in ONE call - the
 * archive API supports arbitrary ranges and Open-Meteo retains this
 * indefinitely, which is what lets rolling stats be re-derived statelessly
 * rather than needing fragile incremental storage. */
function fetchActualsBatch(point, startDate, endDate) {
  const url = `https://archive-api.open-meteo.com/v1/archive?latitude=${point.lat}&longitude=${point.lon}&start_date=${startDate}&end_date=${endDate}&hourly=temperature_2m,precipitation,windspeed_10m,windgusts_10m,cloud_cover,pressure_msl&timezone=${LOCATION.TIMEZONE}`;
  return fetchJSON(url);
}

/** Splits a flat multi-day hourly response into {dateStr: {hourly fields}}
 * buckets, then aggregates each day to {rain, high_c, low_c, peak_wind_kmh,
 * mslp_trend}. This is what makes the batch-fetch-and-rescore approach work -
 * we can look up "what actually happened on date X" for any X in the range. */
function bucketHourlyByDate(hourlyRangeJson) {
  const result = {};
  if (!hourlyRangeJson || hourlyRangeJson.error || !hourlyRangeJson.hourly) return result;
  const h = hourlyRangeJson.hourly;
  const times = h.time || [];
  const byDate = {};
  times.forEach((t, i) => {
    const d = t.split("T")[0];
    if (!byDate[d]) byDate[d] = { temps: [], precip: [], wind: [], pressure: [], times: [] };
    byDate[d].temps.push(h.temperature_2m ? h.temperature_2m[i] : null);
    byDate[d].precip.push(h.precipitation ? h.precipitation[i] : null);
    byDate[d].wind.push(h.windgusts_10m ? h.windgusts_10m[i] : (h.windspeed_10m ? h.windspeed_10m[i] : null));
    byDate[d].pressure.push(h.pressure_msl ? h.pressure_msl[i] : null);
    byDate[d].times.push(t);
  });
  for (const d in byDate) {
    const day = byDate[d];
    const temps = day.temps.filter(v => v != null);
    const wind = day.wind.filter(v => v != null);
    const pressure = day.pressure.filter(v => v != null);
    result[d] = {
      rain: day.precip.some(v => (v || 0) >= 0.5),
      high_c: temps.length ? Math.max(...temps) : null,
      low_c: temps.length ? Math.min(...temps) : null,
      peak_wind_kmh: wind.length ? Math.max(...wind) : null,
      mslp_trend: pressure.length >= 2 ? (pressure[pressure.length - 1] - pressure[0]) : null,
      onset_hour: getOnsetHour(day.times, day.precip),
    };
  }
  return result;
}

function getOnsetHour(times, precip) {
  for (let i = 0; i < times.length; i++) {
    if ((precip[i] || 0) >= 0.5) return times[i].split("T")[1] || null;
  }
  return null;
}

/** Best-effort scrape of a local met service's bulletin. Location-specific -
 * written for KMD's PDF-behind-a-landing-page pattern. Rewrite this function
 * (not the rest of the pipeline) when forking for a location whose local met
 * service publishes differently, or leave LOCATION.LOCAL_BULLETIN_URL empty
 * to skip gracefully. */
function fetchLocalBulletinText() {
  if (!LOCATION.LOCAL_BULLETIN_URL) return "No local bulletin source configured for this location.";
  try {
    const pageRes = UrlFetchApp.fetch(LOCATION.LOCAL_BULLETIN_URL, { muteHttpExceptions: true });
    const html = pageRes.getContentText();
    const pdfMatch = html.match(/href=["'](https?:\/\/[^"']+\.pdf)["']/i) || html.match(/(https?:\/\/[^"']+\.pdf)/i);
    if (!pdfMatch) return `${LOCATION.LOCAL_BULLETIN_SOURCE_NAME}: PDF link not found on landing page.`;

    const pdfUrl = pdfMatch[1] || pdfMatch[0];
    const pdfBlob = UrlFetchApp.fetch(pdfUrl).getBlob();
    const fileMetadata = { name: "Temp_Bulletin_PDF_" + new Date().getTime(), mimeType: MimeType.GOOGLE_DOCS };
    const tempFile = Drive.Files.create(fileMetadata, pdfBlob, { ocr: true });
    const doc = DocumentApp.openById(tempFile.id);
    const textContent = doc.getBody().getText();
    DriveApp.getFileById(tempFile.id).setTrashed(true);
    return textContent.length > 0 ? textContent : "PDF converted, but no text found.";
  } catch (e) {
    Logger.log("Local bulletin fetch error: " + e.toString());
    return `Error reading ${LOCATION.LOCAL_BULLETIN_SOURCE_NAME} bulletin: ${e.message}`;
  }
}

function fetchGroundAQI() {
  if (!CONFIG.WAQI_TOKEN || !LOCATION.WAQI_STATION_ID) return null;
  const url = `https://api.waqi.info/feed/${LOCATION.WAQI_STATION_ID}/?token=${CONFIG.WAQI_TOKEN}`;
  try {
    const json = JSON.parse(UrlFetchApp.fetch(url, { muteHttpExceptions: true }).getContentText());
    if (json.status === "ok") {
      return {
        aqi: json.data.aqi,
        pm25: json.data.iaqi?.pm25?.v || "N/A",
        pm10: json.data.iaqi?.pm10?.v || "N/A",
        station: json.data.city?.name || LOCATION.PRIMARY_PLACE_NAME
      };
    }
  } catch (e) {
    Logger.log("WAQI fetch error: " + e.toString());
  }
  return null;
}

// ==========================================
// DETERMINISTIC EXTRACTION (code, not the LLM)
// ==========================================

/** Pulls each model's Day+0 prediction from hourly data - onset comes from
 * the actual hour-by-hour precip series, which only exists for today. */
function extractDay0PredictionsFromHourly(hourlyMultiModel) {
  if (!hourlyMultiModel || hourlyMultiModel.error || !hourlyMultiModel.hourly) return "";
  const h = hourlyMultiModel.hourly;
  const times = h.time || [];
  const parts = CONFIG.MODELS.map(model => {
    const precipKey = `precipitation_${model}`, windKey = `windgusts_10m_${model}`,
          tempKey = `temperature_2m_${model}`, pressKey = `pressure_msl_${model}`;
    const precip = h[precipKey] || h.precipitation || [];
    const wind = h[windKey] || h.windgusts_10m || [];
    const temp = h[tempKey] || h.temperature_2m || [];
    const press = h[pressKey] || h.pressure_msl || [];
    const rain = precip.some(v => (v || 0) >= 0.5);
    const onset = rain ? getOnsetHour(times, precip) : null;
    const windKmh = wind.length ? Math.max(...wind.filter(v => v != null)) : null;
    const temps = temp.filter(v => v != null);
    const highC = temps.length ? Math.max(...temps) : null;
    const lowC = temps.length ? Math.min(...temps) : null;
    const pressVals = press.filter(v => v != null);
    const mslpTrend = pressVals.length >= 2 ? (pressVals[pressVals.length - 1] - pressVals[0]) : null;
    return formatModelSegment(model, rain, onset, windKmh, highC, lowC, mslpTrend);
  });
  return parts.join(" | ");
}

/** Pulls each model's Day+N prediction from DAILY data - no onset available
 * at this resolution by design (see LOCATION config comments on cost). */
function extractDayNPredictionsFromDaily(dailyMultiModel, dayIndex) {
  if (!dailyMultiModel || dailyMultiModel.error || !dailyMultiModel.daily) return "";
  const d = dailyMultiModel.daily;
  const parts = CONFIG.MODELS.map(model => {
    const precip = (d[`precipitation_sum_${model}`] || d.precipitation_sum || [])[dayIndex];
    const wind = (d[`windgusts_10m_max_${model}`] || d.windgusts_10m_max || [])[dayIndex];
    const high = (d[`temperature_2m_max_${model}`] || d.temperature_2m_max || [])[dayIndex];
    const low = (d[`temperature_2m_min_${model}`] || d.temperature_2m_min || [])[dayIndex];
    const pressArr = d[`pressure_msl_mean_${model}`] || d.pressure_msl_mean || [];
    const mslpTrend = (dayIndex > 0 && pressArr[dayIndex] != null && pressArr[dayIndex - 1] != null)
      ? (pressArr[dayIndex] - pressArr[dayIndex - 1]) : null;
    const rain = (precip || 0) >= 0.5;
    return formatModelSegment(model, rain, null, wind, high, low, mslpTrend);
  });
  return parts.join(" | ");
}

function formatModelSegment(model, rain, onset, windKmh, highC, lowC, mslpTrend) {
  const fmt = (v, d) => v == null ? "NA" : (typeof v === "number" ? v.toFixed(d) : v);
  return `${model}: rain=${rain} onset=${onset || "NA"} wind_kmh=${fmt(windKmh, 0)} high_c=${fmt(highC, 1)} low_c=${fmt(lowC, 1)} mslp_trend=${fmt(mslpTrend, 1)}`;
}

/** Parses the pipe-delimited stored prediction string back into structured
 * per-model objects for scoring. */
function parseModelPredictionsRaw(raw) {
  const result = {};
  if (!raw) return result;
  raw.split("|").forEach(segment => {
    const colonIdx = segment.indexOf(":");
    if (colonIdx === -1) return;
    const model = segment.slice(0, colonIdx).trim();
    const fields = segment.slice(colonIdx + 1).trim();
    const obj = {};
    fields.split(/\s+/).forEach(kv => {
      const eq = kv.indexOf("=");
      if (eq === -1) return;
      const k = kv.slice(0, eq), v = kv.slice(eq + 1);
      if (v === "true") obj[k] = true;
      else if (v === "false") obj[k] = false;
      else if (v === "NA" || v === "") obj[k] = null;
      else if (!isNaN(parseFloat(v))) obj[k] = parseFloat(v);
      else obj[k] = v;
    });
    result[model] = obj;
  });
  return result;
}

// ==========================================
// DETERMINISTIC VERIFICATION & ROLLING STATS
// ==========================================

function hourDiff(predictedHHMM, actualHHMM) {
  const toMin = s => { const [h, m] = s.split(":").map(Number); return h * 60 + (m || 0); };
  return (toMin(actualHHMM) - toMin(predictedHHMM)) / 60;
}

/** Scores one model's stored prediction against one day's actual. Returns
 * null fields where that comparison isn't meaningful/available (e.g. no
 * onset error for Day+3/+7, which never had onset data to begin with). */
function scorePrediction(predicted, actualDaily, leadTimeDays) {
  if (!predicted || !actualDaily) return null;
  const rainCorrect = predicted.rain === actualDaily.rain;
  let onsetErrorHrs = null;
  if (leadTimeDays === 0 && actualDaily.rain && predicted.onset && actualDaily.onset_hour) {
    onsetErrorHrs = hourDiff(predicted.onset, actualDaily.onset_hour);
  }
  const windError = (predicted.wind_kmh != null && actualDaily.peak_wind_kmh != null)
    ? (actualDaily.peak_wind_kmh - predicted.wind_kmh) : null;
  const highError = (predicted.high_c != null && actualDaily.high_c != null)
    ? (actualDaily.high_c - predicted.high_c) : null;
  const lowError = (predicted.low_c != null && actualDaily.low_c != null)
    ? (actualDaily.low_c - predicted.low_c) : null;
  const mslpError = (predicted.mslp_trend != null && actualDaily.mslp_trend != null)
    ? (actualDaily.mslp_trend - predicted.mslp_trend) : null;
  return { rainCorrect, onsetErrorHrs, windError, highError, lowError, mslpError };
}

function mean(arr) {
  const vals = arr.filter(v => v != null && !isNaN(v));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

/**
 * The core of the accuracy loop. For each lead time k:
 *  - Verify yesterday's k-lead prediction (the row dated yesterday-k) against
 *    yesterday's actual, write DAYk_VERIFIED/DAYk_VERIFICATION_NOTE-adjacent
 *    data to that row (the note text itself comes from Gemini later; here we
 *    just mark it verified and stash the score for Gemini's context).
 *  - Recompute rolling 10/30-check stats by re-scoring the last N checks at
 *    that lead time, using the batch actuals + each row's stored raw
 *    predictions. Written directly to the Track Record sheet - no LLM math.
 *  - Increment All-Time counts.
 * Returns a compact summary for Gemini to write prose about.
 */
function runDeterministicVerificationAndScoring(logSheet, trackSheet, dailyActualsPrimary, dailyActualsSecondary, todayStr, yesterdayStr) {
  const lastRow = logSheet.getLastRow();
  const summaryForGemini = { lead_time_results: [] };
  if (lastRow < 2) return summaryForGemini;

  const allRows = logSheet.getRange(2, 1, lastRow - 1, LOG_LAST_COLUMN).getValues();
  const dateToRowIdx = {}; // dateStr -> 0-based index into allRows
  allRows.forEach((row, idx) => {
    const d = row[LOG_COLUMNS.DATE - 1];
    if (d) dateToRowIdx[formatDate(new Date(d))] = idx;
  });

  const rawColByLead = { 0: LOG_COLUMNS.MODEL_PREDICTIONS_DAY0_RAW, 3: LOG_COLUMNS.MODEL_PREDICTIONS_DAY3_RAW, 7: LOG_COLUMNS.MODEL_PREDICTIONS_DAY7_RAW };
  const verifiedColByLead = { 0: LOG_COLUMNS.DAY0_VERIFIED, 3: LOG_COLUMNS.DAY3_VERIFIED, 7: LOG_COLUMNS.DAY7_VERIFIED };
  const noteColByLead = { 0: LOG_COLUMNS.DAY0_VERIFICATION_NOTE, 3: LOG_COLUMNS.DAY3_VERIFICATION_NOTE, 7: LOG_COLUMNS.DAY7_VERIFICATION_NOTE };

  // Load existing Track Record rows for All-Time increment lookups.
  const trackLastRow = trackSheet.getLastRow();
  const trackRows = trackLastRow >= 2 ? trackSheet.getRange(2, 1, trackLastRow - 1, TRACK_LAST_COLUMN).getValues() : [];
  const trackRowIdx = {}; // "model|leadtime" -> 0-based index into trackRows
  trackRows.forEach((row, idx) => { trackRowIdx[`${row[0]}|${row[1]}`] = idx; });

  CONFIG.LEAD_TIMES_DAYS.forEach(k => {
    // --- Mark yesterday's k-lead prediction as verified (if it exists) ---
    const targetRowDate = getPredictionRowDateForTarget(yesterdayStr, k);
    const targetIdx = dateToRowIdx[targetRowDate];
    let yesterdayScoresByModel = {};
    if (targetIdx != null) {
      const raw = allRows[targetIdx][rawColByLead[k] - 1];
      const predictions = parseModelPredictionsRaw(raw);
      const actual = dailyActualsPrimary[yesterdayStr];
      CONFIG.MODELS.forEach(model => {
        const score = scorePrediction(predictions[model], actual, k);
        if (score) yesterdayScoresByModel[model] = score;
      });
      if (Object.keys(yesterdayScoresByModel).length > 0) {
        logSheet.getRange(targetIdx + 2, verifiedColByLead[k]).setValue(true);
        // Note text is written in a separate pass, AFTER Gemini responds - see
        // writeVerificationNotes(), called from runDailyForecastPipeline once
        // geminiResponse.verification_notes is available. We only mark
        // VERIFIED=true here since the note text doesn't exist yet.
      }
    }

    // --- Recompute rolling stats per model at this lead time ---
    CONFIG.MODELS.forEach(model => {
      const short = rescoreRollingWindow(model, k, CONFIG.ROLLING_WINDOW_SHORT, yesterdayStr, dateToRowIdx, allRows, rawColByLead, dailyActualsPrimary);
      const long = rescoreRollingWindow(model, k, CONFIG.ROLLING_WINDOW_LONG, yesterdayStr, dateToRowIdx, allRows, rawColByLead, dailyActualsPrimary);

      const key = `${model}|${k}`;
      let rowNum;
      let priorAllTimeChecks = 0, priorAllTimeCorrect = 0;
      if (trackRowIdx[key] != null) {
        rowNum = trackRowIdx[key] + 2;
        priorAllTimeChecks = trackRows[trackRowIdx[key]][TRACK_COLUMNS.ALL_TIME_CHECKS - 1] || 0;
        priorAllTimeCorrect = trackRows[trackRowIdx[key]][TRACK_COLUMNS.ALL_TIME_CORRECT - 1] || 0;
      } else {
        trackSheet.appendRow([model, k]);
        rowNum = trackSheet.getLastRow();
      }

      // All-time increments by at most the ONE new check for yesterday, if any.
      const newCheck = yesterdayScoresByModel[model];
      const newAllTimeChecks = priorAllTimeChecks + (newCheck ? 1 : 0);
      const newAllTimeCorrect = priorAllTimeCorrect + (newCheck && newCheck.rainCorrect ? 1 : 0);
      const allTimePct = newAllTimeChecks > 0 ? (100 * newAllTimeCorrect / newAllTimeChecks) : null;

      trackSheet.getRange(rowNum, TRACK_COLUMNS.ROLLING_10_RAIN_PCT).setValue(short.rainPct);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.ROLLING_30_RAIN_PCT).setValue(long.rainPct);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.ALL_TIME_CHECKS).setValue(newAllTimeChecks);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.ALL_TIME_CORRECT).setValue(newAllTimeCorrect);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.ALL_TIME_RAIN_PCT).setValue(allTimePct);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.AVG_ONSET_ERROR_HRS_10).setValue(k === 0 ? short.onsetErr : null);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.AVG_WIND_ERROR_KMH_10).setValue(short.windErr);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.AVG_TEMP_HIGH_ERROR_C_10).setValue(short.highErr);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.AVG_TEMP_LOW_ERROR_C_10).setValue(short.lowErr);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.AVG_MSLP_TREND_ERROR_HPA_10).setValue(short.mslpErr);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.CHECKS_IN_WINDOW_10).setValue(short.checksFound);
      trackSheet.getRange(rowNum, TRACK_COLUMNS.LAST_UPDATED).setValue(todayStr);
    });

    summaryForGemini.lead_time_results.push({
      lead_time_days: k,
      target_date_verified: targetRowDate,
      per_model_scores: yesterdayScoresByModel,
    });
  });

  return summaryForGemini;
}

/** Re-derives rolling stats for one (model, lead time) by walking backward
 * from yesterday, collecting up to `windowSize` scoreable checks. Stateless -
 * always recomputed from the raw predictions + batch actuals, so there's no
 * running total that can silently drift out of sync. */
function rescoreRollingWindow(model, leadTimeDays, windowSize, yesterdayStr, dateToRowIdx, allRows, rawColByLead, dailyActuals) {
  const scores = [];
  let cursor = new Date(yesterdayStr);
  let daysSearched = 0;
  const maxSearch = windowSize + 30; // safety bound in case of gaps in the log
  while (scores.length < windowSize && daysSearched < maxSearch) {
    const targetDateStr = formatDate(cursor);
    const rowDateStr = formatDate(addDays(cursor, -leadTimeDays));
    const idx = dateToRowIdx[rowDateStr];
    const actual = dailyActuals[targetDateStr];
    if (idx != null && actual) {
      const raw = allRows[idx][rawColByLead[leadTimeDays] - 1];
      const predictions = parseModelPredictionsRaw(raw);
      const score = scorePrediction(predictions[model], actual, leadTimeDays);
      if (score) scores.push(score);
    }
    cursor = addDays(cursor, -1);
    daysSearched++;
  }
  return {
    checksFound: scores.length,
    rainPct: scores.length ? (100 * scores.filter(s => s.rainCorrect).length / scores.length) : null,
    onsetErr: mean(scores.map(s => s.onsetErrorHrs)),
    windErr: mean(scores.map(s => s.windError)),
    highErr: mean(scores.map(s => s.highError)),
    lowErr: mean(scores.map(s => s.lowError)),
    mslpErr: mean(scores.map(s => s.mslpError)),
  };
}

function writeSkillProfileSummaries(trackSheet, summaries) {
  if (!summaries || !summaries.length) return;
  const lastRow = trackSheet.getLastRow();
  if (lastRow < 2) return;
  const rows = trackSheet.getRange(2, 1, lastRow - 1, 2).getValues();
  const idxByKey = {};
  rows.forEach((r, i) => { idxByKey[`${r[0]}|${r[1]}`] = i; });
  summaries.forEach(s => {
    const idx = idxByKey[`${s.model}|${s.lead_time_days}`];
    if (idx != null) {
      trackSheet.getRange(idx + 2, TRACK_COLUMNS.SKILL_PROFILE_SUMMARY).setValue(s.summary);
    }
  });
}

/** Writes Gemini's per-lead-time qualitative verification notes back to the
 * ORIGINAL row that made each prediction (dated target-k, not today's row) -
 * this is what populates the day{k}_note fields getHistoricalNotes() reads
 * for future runs' context. Called after Gemini responds, since the note
 * text doesn't exist until then. */
function writeVerificationNotes(logSheet, notes, yesterdayStr) {
  if (!notes || !notes.length) return;
  const noteColByLead = { 0: LOG_COLUMNS.DAY0_VERIFICATION_NOTE, 3: LOG_COLUMNS.DAY3_VERIFICATION_NOTE, 7: LOG_COLUMNS.DAY7_VERIFICATION_NOTE };
  const lastRow = logSheet.getLastRow();
  if (lastRow < 2) return;
  const dates = logSheet.getRange(2, LOG_COLUMNS.DATE, lastRow - 1, 1).getValues();
  const dateToRowNum = {};
  dates.forEach((d, i) => { if (d[0]) dateToRowNum[formatDate(new Date(d[0]))] = i + 2; });

  notes.forEach(n => {
    const targetRowDate = getPredictionRowDateForTarget(yesterdayStr, n.lead_time_days);
    const rowNum = dateToRowNum[targetRowDate];
    const col = noteColByLead[n.lead_time_days];
    if (rowNum && col) {
      logSheet.getRange(rowNum, col).setValue(n.note);
    }
  });
}

// ==========================================
// CONTEXT BUILDERS FOR GEMINI
// ==========================================
function getHistoricalNotes(sheet, days) {
  if (!sheet) return [];
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  const startRow = Math.max(2, lastRow - days);
  const values = sheet.getRange(startRow, 1, lastRow - startRow + 1, LOG_LAST_COLUMN).getValues();
  return values.map(row => ({
    date: formatDate(new Date(row[LOG_COLUMNS.DATE - 1])),
    rain_expected: row[LOG_COLUMNS.RAIN_EXPECTED - 1],
    day0_verified: row[LOG_COLUMNS.DAY0_VERIFIED - 1],
    day0_note: row[LOG_COLUMNS.DAY0_VERIFICATION_NOTE - 1],
    day3_verified: row[LOG_COLUMNS.DAY3_VERIFIED - 1],
    day3_note: row[LOG_COLUMNS.DAY3_VERIFICATION_NOTE - 1],
    day7_verified: row[LOG_COLUMNS.DAY7_VERIFIED - 1],
    day7_note: row[LOG_COLUMNS.DAY7_VERIFICATION_NOTE - 1],
  }));
}

function getModelTrackRecord(sheet) {
  if (!sheet) return [];
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  const values = sheet.getRange(2, 1, lastRow - 1, TRACK_LAST_COLUMN).getValues();
  return values.map(row => ({
    model: row[0], lead_time_days: row[1],
    rolling_10_rain_pct: row[2], rolling_30_rain_pct: row[3],
    all_time_checks: row[4], all_time_correct: row[5], all_time_rain_pct: row[6],
    avg_onset_error_hrs_10: row[7], avg_wind_error_kmh_10: row[8],
    avg_temp_high_error_c_10: row[9], avg_temp_low_error_c_10: row[10],
    avg_mslp_trend_error_hpa_10: row[11], checks_in_window_10: row[12],
    last_updated: row[13], skill_profile_summary: row[14], notes: row[15],
  }));
}

// ==========================================
// GEMINI API INTEGRATION
// ==========================================
function callGeminiAPI(weatherData, groundAQI, historicalLogs, trackRecord, verificationContext, todayStr, yesterdayStr, docPublicUrl) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${CONFIG.GEMINI_MODEL}:generateContent?key=${CONFIG.GEMINI_API_KEY}`;
  if (!CONFIG.GEMINI_API_KEY) { Logger.log("Critical Error: GEMINI_API_KEY missing."); return null; }

  const userPrompt = `
Today's Date: ${todayStr} | Yesterday: ${yesterdayStr} | Public Webpage: ${docPublicUrl}

PRE-COMPUTED VERIFICATION RESULTS (already scored by code - write ABOUT these, don't recompute):
${JSON.stringify(verificationContext, null, 2)}

MODEL TRACK RECORD (already computed rolling stats, per model per lead time):
${JSON.stringify(trackRecord, null, 2)}

HISTORICAL NOTES (last ${CONFIG.HISTORICAL_LOOKBACK_DAYS} days):
${JSON.stringify(historicalLogs, null, 2)}

ON-GROUND AQI SENSOR:
${groundAQI ? JSON.stringify(groundAQI, null, 2) : "Unavailable."}

TODAY'S MULTI-MODEL GUIDANCE:
${JSON.stringify({
  primary_today_hourly: weatherData.primary_today_hourly,
  primary_extended_daily: weatherData.primary_extended_daily,
  secondary_today_hourly: weatherData.secondary_today_hourly,
  secondary_extended_daily: weatherData.secondary_extended_daily,
  regional_pressure: weatherData.regional_pressure,
  air_quality: weatherData.air_quality,
  airport_metar: weatherData.airport_metar,
}, null, 2)}

LOCAL BULLETIN (${LOCATION.LOCAL_BULLETIN_SOURCE_NAME}):
${weatherData.local_bulletin_text}
`;

  const payload = {
    system_instruction: { parts: [{ text: buildSystemPrompt() }] },
    contents: [{ role: "user", parts: [{ text: userPrompt }] }],
    generationConfig: {
      responseMimeType: "application/json",
      responseSchema: {
        type: "OBJECT",
        properties: {
          yesterday_verification: { type: "STRING" },
          verification_notes: {
            type: "ARRAY",
            items: {
              type: "OBJECT",
              properties: {
                lead_time_days: { type: "NUMBER" },
                note: { type: "STRING" }
              },
              required: ["lead_time_days", "note"]
            }
          },
          skill_profile_summaries: {
            type: "ARRAY",
            items: {
              type: "OBJECT",
              properties: {
                model: { type: "STRING" },
                lead_time_days: { type: "NUMBER" },
                summary: { type: "STRING" }
              },
              required: ["model", "lead_time_days", "summary"]
            }
          },
          today_properties: {
            type: "OBJECT",
            properties: {
              rain_expected: { type: "STRING" },
              onset_window: { type: "STRING" },
              peak_wind_kmh: { type: "NUMBER" },
              temp_high_c: { type: "NUMBER" },
              temp_low_c: { type: "NUMBER" },
              temp_high_low: { type: "STRING" },
              mslp_trend_24h: { type: "STRING" },
              synoptic_pattern: { type: "STRING" },
              uv_index_max: { type: "STRING" },
              air_quality_aqi: { type: "STRING" }
            },
            required: ["rain_expected", "temp_high_c", "temp_low_c", "temp_high_low"]
          },
          today_narrative: { type: "STRING" },
          whatsapp_summary: { type: "STRING" }
        },
        required: ["yesterday_verification", "today_properties", "today_narrative"]
      }
    }
  };

  try {
    const res = UrlFetchApp.fetch(url, { method: "post", contentType: "application/json", payload: JSON.stringify(payload), muteHttpExceptions: true });
    const responseJson = JSON.parse(res.getContentText());
    if (responseJson.error) { Logger.log(`Gemini error (${responseJson.error.code}): ${responseJson.error.message}`); return null; }
    if (!responseJson.candidates || !responseJson.candidates.length) { Logger.log("Gemini returned no candidates."); return null; }
    return JSON.parse(responseJson.candidates[0].content.parts[0].text);
  } catch (e) {
    Logger.log(`Gemini execution error: ${e}`);
    return null;
  }
}

// ==========================================
// SHEET WRITE FUNCTIONS
// ==========================================
function appendTodayRow(sheet, todayStr, props, groundAqiStr, narrative, day0Raw, day3Raw, day7Raw) {
  const row = [];
  row[LOG_COLUMNS.DATE - 1] = todayStr;
  row[LOG_COLUMNS.RAIN_EXPECTED - 1] = props.rain_expected;
  row[LOG_COLUMNS.ONSET_WINDOW - 1] = props.onset_window;
  row[LOG_COLUMNS.SECONDARY_PEAK_WIND_KMH - 1] = props.peak_wind_kmh;
  row[LOG_COLUMNS.TEMP_HIGH_LOW_DISPLAY - 1] = props.temp_high_low;
  row[LOG_COLUMNS.MSLP_TREND_24H - 1] = props.mslp_trend_24h;
  row[LOG_COLUMNS.SYNOPTIC_PATTERN - 1] = props.synoptic_pattern;
  row[LOG_COLUMNS.GROUND_AQI - 1] = groundAqiStr;
  row[LOG_COLUMNS.MODEL_PREDICTIONS_DAY0_RAW - 1] = day0Raw;
  row[LOG_COLUMNS.MODEL_PREDICTIONS_DAY3_RAW - 1] = day3Raw;
  row[LOG_COLUMNS.MODEL_PREDICTIONS_DAY7_RAW - 1] = day7Raw;
  row[LOG_COLUMNS.DAY0_VERIFIED - 1] = false;
  row[LOG_COLUMNS.DAY0_VERIFICATION_NOTE - 1] = "";
  row[LOG_COLUMNS.DAY3_VERIFIED - 1] = false;
  row[LOG_COLUMNS.DAY3_VERIFICATION_NOTE - 1] = "";
  row[LOG_COLUMNS.DAY7_VERIFIED - 1] = false;
  row[LOG_COLUMNS.DAY7_VERIFICATION_NOTE - 1] = "";
  row[LOG_COLUMNS.NARRATIVE - 1] = narrative;
  row[LOG_COLUMNS.TEMP_HIGH_C - 1] = props.temp_high_c;
  row[LOG_COLUMNS.TEMP_LOW_C - 1] = props.temp_low_c;
  sheet.appendRow(row);
}

/** Moves rows older than CONFIG.LOG_RETENTION_DAYS to an Archive sheet
 * (created on first use, same column layout) rather than deleting them -
 * keeps the active sheet lean without losing the historical record. Deletes
 * from the bottom up to avoid row-index shifting bugs while iterating. */
function archiveOldLogEntries(ss, logSheet, todayStr) {
  const lastRow = logSheet.getLastRow();
  if (lastRow < 2) return;

  const cutoff = addDays(new Date(todayStr), -CONFIG.LOG_RETENTION_DAYS);
  const dates = logSheet.getRange(2, LOG_COLUMNS.DATE, lastRow - 1, 1).getValues();
  const rowsToArchive = [];
  dates.forEach((d, i) => {
    if (d[0] && new Date(d[0]) < cutoff) rowsToArchive.push(i + 2); // 1-indexed sheet row
  });
  if (rowsToArchive.length === 0) return;

  let archiveSheet = ss.getSheetByName("Forecast Log Archive");
  if (!archiveSheet) {
    archiveSheet = ss.insertSheet("Forecast Log Archive");
    const headers = logSheet.getRange(1, 1, 1, LOG_LAST_COLUMN).getValues();
    archiveSheet.getRange(1, 1, 1, LOG_LAST_COLUMN).setValues(headers);
  }

  const dataToMove = logSheet.getRange(rowsToArchive[0], 1, 1, LOG_LAST_COLUMN).getValues();
  // Batch-read all rows to archive, then append and delete bottom-up.
  const allRowsData = rowsToArchive.map(r => logSheet.getRange(r, 1, 1, LOG_LAST_COLUMN).getValues()[0]);
  allRowsData.forEach(rowData => archiveSheet.appendRow(rowData));
  rowsToArchive.sort((a, b) => b - a).forEach(r => logSheet.deleteRow(r));

  Logger.log(`Archived ${rowsToArchive.length} row(s) older than ${CONFIG.LOG_RETENTION_DAYS} days.`);
}

function updateGoogleDocPage(narrativeMarkdown, todayStr) {
  const doc = DocumentApp.openById(CONFIG.DOC_ID);
  const body = doc.getBody();
  body.clear();
  body.appendParagraph(`${LOCATION.PRIMARY_PLACE_NAME} Daily Weather Forecast`).setHeading(DocumentApp.ParagraphHeading.HEADING1);
  body.appendParagraph(`Published: ${todayStr}`).setItalic(true);

  const leadText = "Receive this forecast in your inbox daily: ";
  const linkText = "Click here to Subscribe";
  const subParagraph = body.appendParagraph(`${leadText}${linkText}\n───────────\n`);
  const textObj = subParagraph.editAsText();
  textObj.setFontFamily("Courier New").setItalic(false);
  textObj.setLinkUrl(leadText.length, leadText.length + linkText.length - 1, CONFIG.FORM_SUBSCRIBE_URL);

  body.appendParagraph(narrativeMarkdown);
  doc.saveAndClose();
}

function sendEmailBroadcast(subscriberSheet, narrativeMarkdown, todayStr) {
  if (!subscriberSheet) return;
  const lastRow = subscriberSheet.getLastRow();
  if (lastRow < 2) return;
  const emails = subscriberSheet.getRange(2, 1, lastRow - 1, 1).getValues().flat().filter(e => e.toString().includes("@"));
  if (!emails.length) return;

  const subject = `[${LOCATION.PRIMARY_PLACE_NAME} Weather] Daily Forecast — ${todayStr}`;
  const htmlBody = `
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 650px; margin: 0 auto;">
      <h2 style="color: #1a73e8; margin-bottom: 5px;">${LOCATION.PRIMARY_PLACE_NAME} Daily Forecast</h2>
      <p style="font-size: 0.9em; color: #666; margin-top: 0;">Date: ${todayStr}</p>
      <hr style="border: 0; border-top: 1px solid #ccc;"/>
      <div>${convertMarkdownToSimpleHtml(narrativeMarkdown)}</div>
      <hr style="border: 0; border-top: 1px solid #ccc; margin-top: 20px;"/>
      <p style="font-size: 0.8em; color: #888;">You are receiving this because you subscribed to this forecast service.</p>
    </div>`;
  emails.forEach(email => {
    try { MailApp.sendEmail({ to: email, subject, htmlBody }); }
    catch (e) { Logger.log(`Failed to send to ${email}: ${e}`); }
  });
}

// ==========================================
// UTILITIES & TRIGGER
// ==========================================
function formatDate(d) { return Utilities.formatDate(d, LOCATION.TIMEZONE, "yyyy-MM-dd"); }
function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
/** The row that made a k-lead prediction TARGETING `targetDateStr` is dated
 * (targetDateStr - k). Shared by both the scoring pass and the note-writing
 * pass so the date math only lives in one place. */
function getPredictionRowDateForTarget(targetDateStr, leadTimeDays) {
  return formatDate(addDays(new Date(targetDateStr), -leadTimeDays));
}

function convertMarkdownToSimpleHtml(md) {
  return md
    .replace(/^## (.*$)/gim, '<h3 style="color: #202124; margin-top: 18px; border-bottom: 1px solid #eee; padding-bottom: 4px;">$1</h3>')
    .replace(/^### (.*$)/gim, '<h4 style="color: #3c4043; margin-top: 12px;">$1</h4>')
    .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
    .replace(/\n/g, '<br/>');
}

function createDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger("runDailyForecastPipeline")
    .timeBased().atHour(6).everyDays(1).inTimezone(LOCATION.TIMEZONE).create();
  Logger.log(`Daily 6 AM ${LOCATION.TIMEZONE} trigger registered.`);
}
