# Architecture

How Open Local Weather actually works, and — more usefully — *why* it works
that way. Written for someone picking this up cold, or forking it for a new
location.

> Naming note: this file lives in `docs-internal/`, not `docs/`. `docs/` is
> the **generated GitHub Pages site** — anything put there gets published
> publicly and overwritten by the pipeline. Internal docs live here.

> **Checked against the code on 2026-09-22.** The 09-15 check predated items
> 104, 121, 137, 138 and 159, and the stamp went on vouching for three claims
> they had invalidated — see the later-run box below, which described prompt
> behaviour the test suite actively forbids.
>
> **Checked against the code on 2026-09-15.** This document describes what is
> TRUE and STABLE and cites `ROADMAP.md` for why; the roadmap is the argument,
> this is the shape. It had gone 145 `src/` commits without a check (item 136)
> and was wrong about the two things that matter most — the number of LLM
> calls and the entry point — so if something here disagrees with the code,
> **the code is right and this file has rotted again.**

## The one-paragraph version

A Python pipeline pulls raw forecasts from five weather models, scores
*yesterday's* predictions against what actually happened, and hands an LLM
the raw disagreeing model data plus that scoring history. It does so in **two
calls**: a JUDGMENT call that returns the scored commitment, and a NARRATIVE
call that writes the prose and cannot touch a scored field. The result is
committed back to this repo as JSON (git is the database), rendered to a
static site, and emailed to subscribers. Over time the scoring history tells
the LLM which models to trust, per variable and per lead time.

There is **one entry point, `run_forecast`, and it does not branch on the
clock** — the first run of a day owns verification and the day's predictions;
every later run is an update that rewrites the narrative and preserves them.
See *The two-call split* and *Every run is an issuance* below.

## Data flow

```
                    ┌─────────────────────────────────────┐
                    │  operator's crontab -> workflow_    │
                    │  dispatch (this deployment: 03:01   │
                    │  and 15:01 UTC; any hours you like) │
                    │  forecast.yml declares NO schedule: │
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
   │ 5. SYNTHESIZE     llm/prompt.py → the configured provider          │
   │    TWO CALLS since 2026-09-11 (ROADMAP item 59 step 3):            │
   │                                                                    │
   │    5a. JUDGMENT   build_judgment_prompt → today_properties and     │
   │                   extended_properties. THE SCORED COMMITMENT.      │
   │    5b. NARRATIVE  build_narrative_prompt, handed 5a's answer as    │
   │                   settled. Prose only — CANNOT alter a scored      │
   │                   field. See "The two-call split" below.           │
   │                                                                    │
   │    Both receive raw disagreeing model data + PRE-COMPUTED scores.  │
   │    The LLM never does arithmetic.                                  │
   │    WHICH provider: config/location.yaml `llm_providers`, not code  │
   │    and not a GitHub variable. LLM_PROVIDER env overrides it.       │
   └───────────────────────────────────┬────────────────────────────────┘
                                       │
   ┌───────────────────────────────────▼────────────────────────────────┐
   │ 6. PERSIST + PUBLISH                                               │
   │    • data/log/YYYY-MM-DD.json  (new)                               │
   │    • data/log/<past>.json      (patched: verification notes)       │
   │    • data/station/<ICAO>/<UTC day>.json (merged: every report seen)│
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

   ┌────────────────────────────────────────────────────────────────────┐
   │ A LATER RUN OF THE SAME DAY — STILL A FORECAST                     │
   │                                                                    │
   │ There is no separate evening function. `run_refresh_pipeline` and  │
   │ `run_daily_pipeline` were MERGED into `run_forecast` — item 104.   │
   │ It resolves one question, "is there an entry for today already?",  │
   │ and `_issue_forecast` branches on the answer:                      │
   │                                                                    │
   │    • repeats step 1 (fresh guidance, later model cycle)            │
   │    • re-runs step 5 — and is NOT told it is a later issuance.      │
   │      The only difference in the narrative prompt is a block saying │
   │      this day's verification is already written. Nothing asks for  │
   │      "what changed": items 137/138 retired that, and               │
   │      test_prompt.py asserts "LATER ISSUANCE" and "not a repeat"    │
   │      are ABSENT. This box claimed otherwise until 2026-09-22.      │
   │    • steps 2-4 never run: nothing new to verify mid-day, and the   │
   │      day's predictions must stay what was actually published       │
   │    • republishes docs/. The PYTHON mailer is first-issuance only   │
   │      and is unwired in production; the Apps Script mailer polls    │
   │      and sends EVERY issuance, so a later run does reach readers.  │
   │                                                                    │
   │ WHY MERGED, and it is the opposite of what was expected: two       │
   │ bodies of code could not be held in step by intent. Six fields     │
   │ had already drifted between the two re-issue paths, plus a         │
   │ ground-AQI merge one path skipped and an archive key that          │
   │ destroyed the morning's stored prompt. The write-once rules now    │
   │ live in ONE list in `_compose_log_entry`. See run_forecast's       │
   │ docstring — it records the claim it used to make and why it was    │
   │ wrong.                                                             │
   └────────────────────────────────────────────────────────────────────┘
```

## The load-bearing ideas

### The two-call split, and the firewall it creates

Since 2026-09-11 a forecast is two LLM calls, not one (ROADMAP item 59 step
3). The JUDGMENT call returns `today_properties` and `extended_properties` —
the numbers tomorrow scores. The NARRATIVE call is handed that answer **as
settled** and writes prose.

**A narrative-prompt change provably cannot move the accuracy record.** That
is not a convention, it is the shape of the data flow: the scored fields are
already fixed before the narrative prompt is built. It has two consequences
worth knowing before changing anything:

- Most prompt work here is narrative, and **none of it can be validated
  against the record** — no amount of running it live produces evidence in
  the ledger (item 131). It needs a different instrument: item 77's harness,
  or item 129's static check.
- A judgment-prompt change **is** measurable — rain accuracy, temperature
  error, wind error and onset error all move or they do not. Items 133 and
  134 are deliberately on that side of the line for exactly this reason.

The judgment prompt is byte-identical whether or not this is a re-issue; only
the narrative prompt branches. So the half that decides the scored numbers is
the simpler half, on purpose.

### Every run is an issuance, and the clock is not the axis

`run_forecast` is the only entry point. It branches on whether the day
already has an entry, never on the time of day:

- the **first** run of a day owns verification and the day's
  `model_predictions` — the numbers tomorrow scores
- **every later** run is an update: narrative only, predictions preserved

`prediction_rows` is append-only and row 0 is immutable. Those write-once
rules live in one list in `_compose_log_entry`, which is the point of the
merge — see item 104 and the data-flow note above.

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

### Reasoning effort is turned up — on one of the two Gemini paths

> **As of 2026-09-15 this deployment runs `gemini-interactions`, which is NOT
> sent a thinking level.** The Interactions API takes no measured equivalent
> of `thinkingConfig`, and passing one that is silently ignored would be
> worse than passing none — the run would look configured and behave
> otherwise. So the paragraph below is true of `GeminiProvider`
> (`generateContent`) and not of the provider currently configured. The
> switch therefore changed two things at once, which is why `thought_tokens`
> is now stored per log entry: it is the only way to see the second one.
> ROADMAP items 80 and 132.

`GeminiProvider` defaults to `thinkingConfig.thinkingLevel: "high"`
(`GEMINI_THINKING_LEVEL` env var, `cli.py`) for the actual forecast
pipeline. Measured directly against this project's real production prompt:
"low" produced 739 thinking tokens, "high" produced 4,235 — a real
difference, visible in the output too (the Forecaster Confidence Notes
section reasons more specifically about which model's track record drove
each call). At ~45K tokens/call against a measured 250K-token/run free-tier
limit, there's no real cost to defaulting high for tasks that are
genuinely multi-step reasoning (reconciling five disagreeing models,
weighing recent vs. long-term skill). `check-health`'s model-deprecation
check deliberately does NOT set this — it's a simple factual lookup, not
reasoning, and uses Gemini's own default instead.

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

METAR now has a second, heavier role. `fetch_metar()` remains the
best-effort current-conditions cross-check described above, but
`observed_thunder_by_date()` feeds the accuracy record itself, from a
separate archive endpoint. It degrades the same way — no configured ICAO,
or an unreachable archive, leaves every `thunder` at `None` and scoring
behaves exactly as it did before the field existed — so a fork without an
airport nearby loses nothing it previously had.

Since 2026-09-18 every archive read goes through `store/station_reports.py`
(ROADMAP item 151): the rows are merged into `data/station/<ICAO>/` as the
archive served them and the readers — the same-day snapshot, the day
aggregates, the +24 h window — read the union of every fetch. What was
derived from the station is recomputable from the record, and a row the
archive later drops is still counted.

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
| Rain is scored against observed CONVECTION, not reanalysis precipitation alone | `models.DailyActual.observed_convection()` | ERA5 at ~25 km smooths isolated tropical storms into nothing; 5 of the first 42 stored days were filed as dry while the airport watched a storm pass over |
| An absent observation is `thunder=None`, never `False` | `fetch/metar.observed_thunder_by_date()` | A station that filed nothing is not a station that saw nothing |
| The whole record is re-derivable from stored predictions plus refetched observations | `olw rebuild-record` | A correction to what was observed must reach every figure, not only days scored after the fix |
| `docs/` is generated, never hand-edited | `publish/pages.py` | Overwritten every run |
| `prediction_rows` is append-only and row 0 is immutable | one list in `pipeline._compose_log_entry` | A later issuance must not rewrite the numbers the day was scored on. Written twice, the two copies drifted in six fields — ROADMAP item 104 |
| The narrative call cannot alter a scored field | the two-call split, `llm/prompt.py` | It is what makes the accuracy record a controlled measurement rather than an impression — items 59, 131 |
| Every request that reaches a model is counted before it is made | `pipeline.attach_spend_cap`, guarded by `tests/test_spend_coverage.py` | The row is written BEFORE the call, so a crash mid-call still counts. Four callers have broken this rule once each; the guard is what stops the fifth |

## Extension points

Designed to be swapped without touching pipeline logic:

- **LLM provider** — implement `llm/provider.LLMProvider` (one method), then
  add the name to `VALID_LLM_PROVIDERS` in that same module. Four ship today:
  `gemini` (`generateContent`), `gemini-interactions` (the Interactions API),
  `anthropic`, and `openai` — which covers OpenAI, OpenRouter, Groq, Together,
  vLLM and Ollama. **A name there is a row in the supported matrix, not a
  vendor** (item 81): `gemini` and `gemini-interactions` are the same vendor
  and the same model reached through two APIs, and the API decides the shape
  of the call. Each needs its own JSON-schema adapter — `to_gemini_schema()`
  for `generateContent`, `to_strict_json_schema()` for the other three.
- **Email** — implement `pipeline.EmailSender`. Currently satisfied by an
  external Apps Script instead; `publish/email_gmail.py` exists as a
  Python-native alternative if a domain gets set up.
- **Publishing** — implement `pipeline.Publisher`.
- **Local bulletin** — implement `fetch/bulletin.BulletinFetcher`. This one
  *will* need rewriting per location; formats vary wildly and don't
  generalize.
- **Location** — `config/location.yaml` only. That's the whole fork surface
  for a new place, and since 2026-09-15 it is also where the PROVIDER is
  named (`llm_providers`). Keys stay in the environment: a provider name is
  configuration and belongs in version control where a change has a diff and
  an author; an API key is a secret and must not be committed.

Deliberately *not* abstracted: Open-Meteo itself. The multi-model design
depends on its consistent `{variable}_{model}` field-naming convention
across the forecast, archive, and air-quality APIs. Adding a model it
already serves is one line in `defaults.MODELS`; replacing the provider
entirely would mean rewriting `fetch/open_meteo.py` and `extract.py`
together.

## Two days, and which one owns what

**The record is keyed on the LOCAL day.** `cli.py` calls
`today_in_tz(location.timezone)`, so `data/log/YYYY-MM-DD.json`, `entry.date`
and the phrase "the day's first run" all mean the station's own calendar date.
A run at 23:50 local and a run at 00:10 local are different days' forecasts,
whatever the UTC clock says.

**The models run on UTC, and so does the station archive.** Both days are in
the record already, doing different jobs:

| what | clock |
|---|---|
| the log filename and `entry.date` | local |
| `meta.issued_local_time` | local |
| `meta.generated_at_utc` | UTC |
| `guidance_initialised_at` (the model cycle) | UTC |
| `data/station/<ICAO>/<day>.json` | **UTC** |

The station store is the one that runs on the other clock from its neighbours,
which is worth knowing before reading it.

**A local day always overhangs its UTC date at one end**, by the size of the
offset and in the direction of its sign. Nairobi's 2026-09-22 runs from 21:00Z
on the 21st to 20:59Z on the 22nd, so one local day's observations live in TWO
UTC-keyed station files. `metar.ARCHIVE_PADDING_DAYS` is why that works: one
day of padding on each side of the fetch covers every real timezone.

**FORKERS: GitHub's cron is UTC and the record is local.** At this deployment
the offset is +3 and both slots land on the same date under either clock, so
nothing diverges and nothing warns. At a large negative offset it does — a
03:01Z slot is 19:01 the PREVIOUS local day at UTC-8, so the run you think of
as your morning writes yesterday's entry and becomes that day's second run.
Check which LOCAL day your chosen hours fall on before assuming the early slot
is the day's first.

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

1,217 tests, all offline and deterministic — no network, no LLM calls, no
sleeps. `pytest -q` runs in a few seconds, and CI runs it on every push.
(It said 219 until 2026-09-15, which is how item 136 was raised: a number
nobody re-checked for three weeks.)

Every new guard is expected to be MUTATION-TESTED — break the code, watch the
test fail, put it back, watch it pass. Clear `__pycache__` between runs: a
stale one made a correct file fail on 2026-09-10 and cost an hour.

The verification/scoring module carries the deepest coverage on purpose:
it's the part the project's credibility rests on. Its tests include
hand-computed rolling averages (checked with a calculator, not just
"doesn't crash"), lead-time offset correctness, cold-start behavior, gaps in
the log, and the all-time idempotency guard.

Not covered by CI, by design: live API calls and real LLM output are
non-deterministic and burn quota. Use `olw forecast --dry-run` for that —
it does everything real except writing files, publishing, and emailing.
(`run-daily` was renamed; there is no such command.)

The Apps Script mailer has its own harness (`node mailer/test_mailer.js`),
also outside CI, since it doesn't run on GitHub Actions at all.
