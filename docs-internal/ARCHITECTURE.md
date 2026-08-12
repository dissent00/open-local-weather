# Architecture

How Open Local Weather actually works, and — more usefully — *why* it works
that way. Written for someone picking this up cold, or forking it for a new
location.

> Naming note: this file lives in `docs-internal/`, not `docs/`. `docs/` is
> the **generated GitHub Pages site** — anything put there gets published
> publicly and overwritten by the pipeline. Internal docs live here.

## The one-paragraph version

Every morning, GitHub Actions runs a Python pipeline that pulls raw
forecasts from five weather models, scores *yesterday's* predictions
against what actually happened, hands an LLM the raw disagreeing model data
plus that scoring history, and asks it to write a forecast narrative. The
result is committed back to this repo as JSON (git is the database),
rendered to a static site, and emailed to subscribers. An optional evening
run re-synthesizes the narrative on a fresher model cycle without touching
the accuracy loop at all — see step 7. Over time the scoring history tells
the LLM which models to trust, per variable and per lead time.

## Data flow

```
                    ┌─────────────────────────────────────┐
                    │  GitHub Actions cron (~03:07 UTC)   │
                    └──────────────────┬──────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 1. FETCH          fetch/open_meteo.py, metar.py, waqi.py,          │
   │                   bulletin/kenya_kmd.py                            │
   │    • 5 models × hourly today + daily out to Day+7                  │
   │    • regional MSLP snapshot, CAMS air quality                      │
   │    • optional: METAR, ground AQI, local met-office bulletin (PDF)  │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 2. ACTUALS        store/actuals_cache.py                           │
   │    • daily: fetch yesterday only, upsert into cache                │
   │    • Mondays: full 40-day re-fetch, replace cache (self-healing)   │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 3. VERIFY         verify/scoring.py, verify/pipeline.py            │
   │    PURE CODE — NO LLM. For each lead time (0, 3, 7):               │
   │    • score yesterday's prediction from the row dated (yest − k)    │
   │    • re-derive rolling 10/30-check stats from scratch              │
   │    • increment all-time counters (once per target date)            │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 4. EXTRACT        extract.py — PURE CODE, per-model predictions    │
   │                   for today/Day+3/Day+7, stored for future scoring │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 5. SYNTHESIZE     llm/prompt.py → llm/gemini.py                    │
   │    LLM receives: raw disagreeing model data + PRE-COMPUTED scores  │
   │    LLM produces: narrative, verification notes, skill summaries,   │
   │                  and one blended "today_properties" call           │
   │    LLM never does arithmetic.                                      │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 6. PERSIST + PUBLISH                                               │
   │    • data/log/YYYY-MM-DD.json  (new)                               │
   │    • data/log/<past>.json      (patched: verification notes)       │
   │    • data/track_record.json    (rewritten)                         │
   │    • docs/                     (regenerated static site)           │
   │    • workflow commits + pushes all of the above                    │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 7. EMAIL (separate system, ~06:20 EAT)                             │
   │    mailer/AppsScriptMailer.gs pulls the committed JSON from        │
   │    GitHub raw and sends via MailApp. Not part of the pipeline.     │
   └────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────────┐
                    │  GitHub Actions cron (~15:07 UTC)   │  optional, same
                    └──────────────────┬──────────────────┘  concurrency
                                       │                       group as above
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ EVENING REFRESH   pipeline.run_refresh_pipeline()                  │
   │    • repeats step 1 only (fresh guidance, later model cycle)       │
   │    • re-runs step 5 in refresh mode (LLM sees the morning          │
   │      narrative, writes an update — "what's changed", not a repeat) │
   │    • merges narrative/today_properties into the EXISTING entry —   │
   │      model_predictions/verification/generated_at_utc untouched     │
   │    • republishes docs/; web-only, does not email                  │
   │    • steps 2-4 (actuals, verify, extract) never run — nothing new  │
   │      to verify mid-day, and predictions must stay what was         │
   │      actually published at 6 AM                                    │
   └────────────────────────────────────────────────────────────────────┘
```

## The load-bearing ideas

### Git is the database

Every run commits a JSON file per day. No hosted DB, no credentials, no
cost, and the entire prediction history is auditable in `git log` — you can
prove a forecast wasn't retroactively edited. Small per-day files (rather
than one growing array) keep diffs reviewable and avoid rewrite churn.

`data/track_record.json` is the exception: it's a **cache**, rewritten in
full each run, because it's derivable from the log plus fresh actuals. Its
diffs are still useful — they read as a changelog of model skill over time.

### The LLM never does arithmetic

This is the single most important constraint in the system, inherited from
the Apps Script original after a real incident there: asking the model to
compute rolling accuracy percentages risked silent, drifting numbers that
nothing downstream could detect. So:

- **Code** computes: every score, every error, every rolling average, every
  raw per-model prediction extraction.
- **The LLM** does: qualitative verification notes, per-model skill
  summaries, narrative prose, and the genuinely judgment-shaped task of
  reconciling five disagreeing models into one blended call.

Numbers flow *into* the prompt as pre-computed context. They never flow back
out of it.

### Stateless rolling stats, one incremental exception

Rolling 10/30-check windows are re-derived from scratch every run by walking
backwards through the log and re-scoring against freshly fetched actuals.
Nothing accumulates, so nothing can drift — a bad run self-corrects the next
day.

The exception is `all_time_checks` / `all_time_correct`, which genuinely
accumulate. That makes them the one place a bug is *permanent*, which is why
they're guarded by `last_verified_target_date` (see below) and flagged with
comments in `models.py` and `verify/pipeline.py`. Don't "simplify" that
guard away.

### Lead-time separation

A model good at Day+0 is not automatically good at Day+7, and the system
tracks them as separate entities: 5 models × 3 lead times = 15 track-record
rows. The prompt explicitly instructs the LLM to consult the *matching* lead
time's record when writing the extended outlook.

Day+3 and Day+7 predictions have **no onset timing** — only daily aggregates
are fetched that far out, to control API cost — so onset error is only ever
scored at Day+0, and the prompt forbids stating onset times beyond today.

### Everything optional degrades, everything required aborts

`fetch/open_meteo.py` raises on failure: without model data or actuals there
is nothing honest to publish. METAR, ground AQI, and the local bulletin all
return `None`/an explanatory string instead — the forecast still goes out,
and the prompt instructs the LLM to say what was missing.

The LLM call is required, but now retries transient 429/5xx errors with
backoff before giving up (added after a real 503 aborted a run).

## Key invariants

Break these and the system quietly stops being trustworthy:

| Invariant | Enforced in | Why |
|---|---|---|
| A k-lead prediction for date D lives in the row dated `D − k` | `dates.prediction_row_date_for_target()` | Single source of this math; all scoring depends on it |
| Rolling stats are never accumulated | `verify/scoring.rescore_rolling_window()` | Self-healing; no drift |
| All-time counters increment at most once per target date | `last_verified_target_date` guard | The one non-self-healing field |
| The LLM's numbers are never read back as data | `verify/` owns all math | Prevents silent arithmetic drift |
| Onset is scored only at Day+0 | `verify/scoring.score_prediction()` | Day+3/+7 never had onset data |
| Missing model data is `rain=None`, never `False` | `extract.py` + `score_prediction()` | "No data" scored as "no rain" manufactures fake skill |
| `docs/` is generated, never hand-edited | `publish/pages.py` | Overwritten every run |

## Extension points

Designed to be swapped without touching pipeline logic:

- **LLM provider** — implement `llm/provider.LLMProvider` (one method).
  Groq/Cerebras/OpenRouter would each need their own JSON-schema adapter,
  mirroring `llm/schema.to_gemini_schema()`.
- **Email** — implement `pipeline.EmailSender`. Currently satisfied by an
  external Apps Script instead; `publish/email_gmail.py` exists as a
  Python-native alternative if a domain gets set up.
- **Publishing** — implement `pipeline.Publisher`.
- **Local bulletin** — implement `fetch/bulletin.BulletinFetcher`. This one
  *will* need rewriting per location; formats vary wildly and don't
  generalize.
- **Location** — `config/location.yaml` only. That's the whole fork surface
  for a new place.

Deliberately *not* abstracted: Open-Meteo itself. The multi-model design
depends on its consistent `{variable}_{model}` field-naming convention
across the forecast, archive, and air-quality APIs. Adding a model it
already serves is one line in `defaults.MODELS`; replacing the provider
entirely would mean rewriting `fetch/open_meteo.py` and `extract.py`
together.

## Timing, and why it matters

Model runs land on a delay. Measured from Open-Meteo's per-model metadata
endpoints (`/data/{model}/static/meta.json`) on 2026-08-11:

| Model | Availability delay | Usable horizon |
|---|---|---|
| ICON | ~3.8 h | ~7.5 d |
| GFS 0.13 | ~6.6 h | ~16 d |
| ECMWF IFS 0.25 | ~7.1 h | ~8 d |
| UKMO 10 km | ~7.3 h | ~7.2 d |

Two consequences worth internalising:

**There is an ~8-hour floor on data freshness.** Because the slowest models
take 6.6–7.3 h to arrive, no run at any hour can use data fresher than about
8 hours. The 03:00 UTC run's data is ~9 h old — one hour off the theoretical
best, not the serious staleness it might first appear.

**Cycle alignment matters more than raw age.** All four models run 00/06/12/18z
but arrive at different speeds, so at some hours they're split across
different cycles. Mixing a 06z ECMWF with a 12z ICON makes part of the
model disagreement an artefact of run age rather than genuine forecast
uncertainty — and reading that disagreement correctly is the entire point of
the synthesis. Aligned windows open at **02:00, 08:00, 14:00 and 20:00 UTC**;
03:00 UTC sits inside one. Full hour-by-hour table in [ROADMAP.md](ROADMAP.md).

**Not every model reaches every lead time.** UKMO tops out around 7.2 days
and ICON around 7.5, so at Day+7 one or both may have no data at all. That
absence is recorded as `rain=None`, never `False` — see the invariants table
above; conflating the two manufactures fake skill scores.

## Testing

219 tests, all offline and deterministic — no network, no LLM calls, no
sleeps. `pytest -q` runs in under a second, and CI runs it on every push.

The verification/scoring module carries the deepest coverage on purpose:
it's the part the project's credibility rests on. Its tests include
hand-computed rolling averages (checked with a calculator, not just
"doesn't crash"), lead-time offset correctness, cold-start behavior, gaps in
the log, and the all-time idempotency guard.

Not covered by CI, by design: live API calls and real LLM output are
non-deterministic and burn quota. Use `olw run-daily --dry-run` for that —
it does everything real except writing files, publishing, and emailing.

The Apps Script mailer has its own harness (`node mailer/test_mailer.js`),
also outside CI, since it doesn't run on GitHub Actions at all.
