# Project Brief: Open Local Weather (GitHub Actions Architecture)

Paste this into your first message in Claude Code, in the freshly-cloned
repo, to carry over context from prior design work.

## Goal
An open-source, location-agnostic daily weather forecast system: genuine
multi-model synthesis (not a relayed single-source number) with a
self-improving accuracy loop, for places underserved by professional
meteorology. Currently proven out in a Google Sheets/Apps Script
implementation (Kisumu, Kenya) - this repo is a from-scratch rebuild on
a more open, self-contained architecture.

## Architecture for this repo
- **Hosting/scheduling**: GitHub Actions `on.schedule.cron`, public repo
  (unlimited free minutes). Note: GitHub auto-disables scheduled workflows
  after 60 days of repo inactivity - keep commits flowing.
- **Language**: Python.
- **LLM**: Gemini (free tier) as the primary/default provider, but build
  a thin provider-abstraction layer from day one - multi-model support
  (Groq, Cerebras, OpenRouter, etc.) is an explicit roadmap item, not
  Gemini-only forever.
- **"Database"**: git itself. Each day's run commits a JSON (or similar)
  file back to the repo - free, unlimited, fully version-controlled audit
  trail of every prediction and verification.
- **Public site**: GitHub Pages, regenerated from the latest committed
  data each run.
- **Email**: Brevo (free tier, 300/day, no card) - account already being
  set up.
- **WhatsApp**: explicitly deferred to roadmap. Meta's Cloud API loses
  its free service-message window Oct 1 2026; would cost trivially
  little at small subscriber counts, but not literally free - revisit
  later. Do not build on unofficial WhatsApp-Web automation libraries -
  ToS violation, real account-ban risk.

## Design principles established during prior design work (carry these over)
- **Genuine synthesis, not relay**: the LLM must see each model's raw,
  disagreeing data (GFS, ECMWF, ICON, UKMO, Open-Meteo blend) side by
  side and reason about disagreement explicitly, not receive a
  pre-averaged number.
- **Per-model AND per-variable skill tracking**: track accuracy
  separately for precipitation, wind, temperature, and pressure trend
  per model - a model can be trusted on one variable and not another in
  the same forecast.
- **Lead-time tracking**: score predictions independently at Day+0,
  Day+3, and Day+7 - model skill decay with lead time is real and uneven
  across models (e.g. ECMWF often holds up better than GFS at range).
  Day+3/+7 predictions have no onset-timing data by design (only daily
  aggregates fetched that far out, for cost).
- **All arithmetic in code, not the LLM**: rolling accuracy stats, error
  calculations, and raw prediction extraction must be deterministic code.
  The LLM's job is narrower - qualitative verification notes, skill
  summaries, and narrative synthesis - using pre-computed numbers as
  context, never asked to recompute them. This was a deliberate fix after
  finding that asking Gemini to compute rolling stats risked silent
  arithmetic drift with no way to notice from outside.
- **Weekly batch, daily light-touch**: daily runs only need ONE day of
  new actuals (yesterday's) to verify yesterday's predictions. Rolling
  10/30-check stats are recomputed from a ~31-day batch fetch, but that
  batch only needs to run WEEKLY, not daily - cuts Open-Meteo archive API
  load ~7x with no real loss of signal, since rolling stats are
  inherently slow-moving aggregates.
- **Recency-weighted evidence**: recent (5-10 day) verification should
  outweigh 30-day/all-time stats when they conflict - long-term stats
  exist to catch slow systematic bias, not override current conditions.
- **Location-agnostic config**: one config block (coordinates, place
  names, optional secondary water/geographic feature, region points for
  synoptic pressure snapshot, METAR station, AQI station) should be the
  only thing that changes when forking for a new location. Local
  met-bulletin scraping is inherently location-specific and won't
  generalize cleanly - keep it modular/replaceable, not a blocker.
- **Data retention**: archive (don't delete) data older than ~180 days,
  to keep active working data lean without losing history - trivial with
  git's own history, may not even need a separate archive mechanism the
  way the Sheets version did.
- **Output format**: wind as "X km/h (Y kt) from [8-point cardinal]"
  (knots = km/h ÷ 1.852); temps as "0°C / 32°F"; section order: Overview
  (plain-language, 3-5 sentences) → Today's Forecast → Extended Outlook →
  Severe Weather/Hazard Potential → secondary-location section (if
  configured) → Detailed Discussion (Synoptic Overview + Forecaster
  Confidence Notes as subsections).
- **Known, permanent limitations to carry over honestly**: no CAPE at
  daily resolution (Open-Meteo limitation, affects Day+3/+7 hazard
  assessment only); no true storm-center/track forecasting, only regional
  pressure-gradient description from point data - don't overclaim
  precision the data doesn't support.

## Reference material
A prior Google Sheets/Apps Script implementation (`KisumuForecastPipeline_v2.gs`)
has all of the above logic working in JavaScript - useful as a reference
for the exact verification/scoring math and prompt structure, even though
this repo reimplements it in Python on different infrastructure. A
Python/Anthropic-API version of an earlier iteration also exists
(structurally closer to what this repo needs, different LLM/hosting) -
both live in a separate `KisumuWeather` repo if useful to consult.

## Immediate first steps
1. Repo structure: propose `fetch/`, `synthesis/`, `verify/`, `data/`
   (git-committed JSON), `.github/workflows/`, `docs/` (GitHub Pages
   source) or similar - your call on exact layout.
2. LLM provider abstraction: a small interface Gemini implements first,
   designed so adding Groq/Cerebras/OpenRouter later doesn't require
   touching the pipeline logic.
3. Port the fetch → verify → synthesize → publish → email flow from the
   Apps Script reference, adapted to git-as-database instead of Sheets.
