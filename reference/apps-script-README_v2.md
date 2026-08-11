# Phase 1b v2: Lead-Time Tracking, Deterministic Scoring, Location-Agnostic Config

This is a substantial rebuild of the original Gemini/Sheets pipeline. If
you're setting this up fresh, follow "New setup" below. If you already ran
v1, read "Migrating from v1" first - **the sheet schema changed and is not
backward compatible.**

## What changed and why

1. **Lead-time accuracy tracking.** v1 only ever scored "today's" prediction
   the following day - it had no way to know whether a model's 3-day-out or
   7-day-out guidance was any good, even though model skill decay with lead
   time is real and uneven across models. v2 stores and independently scores
   predictions at Day+0, Day+3, and Day+7.

2. **All arithmetic moved from Gemini to code.** v1 asked Gemini to compute
   rolling accuracy percentages and error means from JSON history each day.
   That's a real reliability risk - especially with a Flash-tier model doing
   unsupervised daily arithmetic - since a silently wrong number would
   corrupt the accuracy signal the whole system depends on, with no way to
   notice from the outside. v2's code fetches actuals, extracts raw
   predictions, scores them, and computes rolling stats deterministically.
   Gemini's role is now narrower and better-matched to what LLMs are
   actually good at: writing the qualitative verification notes, skill
   summaries, and the forecast narrative - using pre-computed numbers as
   context, never asked to recompute them.

3. **Stateless rolling stats.** Rather than maintaining fragile running
   totals, each run fetches ONE batch of actual/reanalysis data covering
   the last ~31 days (Open-Meteo's archive keeps this indefinitely) and
   re-derives the last 10/30 checks' scores from scratch using each
   historical row's already-stored raw predictions. Self-correcting by
   design - there's no incremental state to drift out of sync.

4. **Data retention.** Forecast Log rows older than `CONFIG.LOG_RETENTION_DAYS`
   (default 180) are moved to a "Forecast Log Archive" sheet automatically -
   archived, not deleted, so nothing is lost, but the active sheet stays lean.

5. **Location-agnostic config.** Everything specific to Kisumu/Lake
   Victoria/Nyanza now lives in the `LOCATION` block at the top of the
   script. Forking this for a new place should mean editing that block only
   - the system prompt is built dynamically from it via `buildSystemPrompt()`.

## New setup

1. **Google Sheet** with tabs: "Forecast Log", "Model Track Record",
   "Subscribers" (email addresses in column A).

2. **Forecast Log header row** (20 columns, must match `LOG_COLUMNS` in the
   script exactly - this is the single source of truth, cross-check there
   if anything looks off):
   ```
   Date | Rain Expected | Onset Window | Secondary Peak Wind km/h |
   Temp High/Low Display | MSLP Trend 24h | Synoptic Pattern | Ground AQI |
   Model Predictions Day0 Raw | Model Predictions Day3 Raw |
   Model Predictions Day7 Raw | Day0 Verified | Day0 Verification Note |
   Day3 Verified | Day3 Verification Note | Day7 Verified |
   Day7 Verification Note | Narrative | Temp High C | Temp Low C
   ```

3. **Model Track Record header row** (16 columns, NORMALIZED - one row per
   (model, lead time) pair, not one row per model. Match `TRACK_COLUMNS`):
   ```
   Model | Lead Time Days | Rolling 10 Rain % | Rolling 30 Rain % |
   All-Time Checks | All-Time Correct | All-Time Rain % |
   Avg Onset Error Hrs (10) | Avg Wind Error km/h (10) |
   Avg Temp High Error C (10) | Avg Temp Low Error C (10) |
   Avg MSLP Trend Error hPa (10) | Checks in Window (10) | Last Updated |
   Skill Profile Summary | Notes
   ```
   Seed with 15 rows: each of `gfs_seamless, ecmwf_ifs025, icon_seamless,
   ukmo_seamless, best_match` × lead times `0, 3, 7`. The script will find
   these by (model, lead time) match on first run and auto-create any
   missing rows, but starting with them present is cleaner.

4. **Apps Script setup**: same as before (Extensions → Apps Script, paste
   the script, set Script Properties `GEMINI_API_KEY`/`SPREADSHEET_ID`/
   `DOC_ID`/`WAQI_TOKEN`, enable the Drive API advanced service, run
   `createDailyTrigger()` once).

5. **Edit the `LOCATION` block** at the top of the script even if you're
   keeping Kisumu - it's now the only place location specifics live.
   Verify `WAQI_STATION_ID` manually at waqi.info as before.

## Migrating from v1

There's no automatic migration - the column layouts are incompatible (v1's
single `model_predictions_raw` column is now three Day0/Day3/Day7 columns,
plus new verification note columns per lead time, plus two new numeric temp
columns). Recommended path: **start a fresh Forecast Log tab** with the new
20-column header, and either archive your v1 data as historical reference
(rename the old tab, e.g. "Forecast Log v1 Archive") or discard it if the
history isn't valuable enough to keep in a mismatched format. The Model
Track Record tab also needs to be rebuilt in the new normalized (long)
format - old wide-format rows won't be read by v2's code.

## On the lead-time verification math, if you want to audit it

For a lead time of `k` days, a prediction made on date `D` targets date
`D + k`. So to verify what was predicted for **yesterday** at lead time `k`,
the script looks up the row dated `yesterday - k` and reads its
`Model Predictions Day{k} Raw` field. This is why Day+0, Day+3, and Day+7
verification can all be checked against the SAME actuals fetch (yesterday's)
- they're just looking back different distances into the log to find the
prediction that happened to target yesterday. `getPredictionRowDateForTarget()`
in the script is the one place this date math lives - if lead-time
verification is ever giving results that seem too early/late, that
function is the first place to check.

## Known limitations (carried forward, still true)

- No CAPE at daily resolution (Open-Meteo limitation) - Day+3/Day+7 hazard
  assessment relies on gust maxima and rain totals alone.
- No onset-timing data at Day+3/Day+7 (only daily aggregates fetched that
  far out) - the system prompt explicitly forbids stating a specific onset
  time for the extended outlook.
- Local bulletin fetching (`fetchLocalBulletinText()`) is genuinely
  location-specific and will likely need rewriting per fork - written for
  KMD's PDF-behind-a-landing-page pattern, which won't generalize to every
  local met service's format.
- All-Time accuracy stats are maintained incrementally (not re-derived from
  full history each run) - accurate going forward from whenever they're
  first computed, but won't retroactively backfill if you start the system
  after already having months of untracked history.
