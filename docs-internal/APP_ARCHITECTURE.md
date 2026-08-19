# Mobile app — architecture and plan

Status: **design, not built.** This is the document to argue with before
any Dart gets written.

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) (the Python pipeline) and
[ROADMAP.md](ROADMAP.md) (item 16 tracks this).

---

## What it is

A Flutter app (Android first, iOS to follow) that gives anyone a
multi-model weather forecast for **any** location, written in a voice and
focus they choose, using **their own LLM API key**.

The crucial framing, which took a wrong turn to find: the app is *not*
primarily a reader for this repo's Kisumu deployment. A "hybrid" design —
app reads the published forecast, re-voices it locally — only serves people
who want Kisumu's data. The point of an app is that a user in Masterton or
Mombasa sets their own coordinates and gets a real forecast with no GitHub
account and no setup. The app has to be able to run the whole pipeline
itself.

## Two sources, several delivery strategies

**Where a forecast comes from** is one axis:

**Standalone (default).** Everything on-device: fetch → verify → LLM →
store → render. Any location. No account, no server, no GitHub. The user
supplies an LLM key.

**Connected (opt-in).** Point the app at a GitHub deployment — this repo,
the user's own fork, or a friend's. The pipeline runs on schedule in
Actions as it does today; the app reads the committed JSON and can trigger
an on-demand run. Gets guaranteed morning delivery, email, and a shared
auditable history.

These are not separate products. Same screens, same rendering, same local
store — only the origin of an entry differs, and a user can switch.

**How and when it refreshes** is a separate axis, and deliberately a *user
choice* rather than something the app decides for them. Not all options are
available on all platforms, because the OS constraints below are real:

| Strategy | Android | iOS | Notes |
|---|---|---|---|
| Tap to generate | ✅ | ✅ | Always available; the guaranteed floor |
| Scheduled local generation | ✅ | ❌ | Exact alarm + WorkManager; the 6am ideal |
| Night-before generation | ✅ | ⚠️ | Trades model freshness for readiness |
| Staleness prompt | ✅ | ✅ | Notification says "tap to refresh" — see below |
| Connected (server generates) | ✅ | ✅ | The only true guarantee on iOS |

The app should be explicit in the UI about which strategy is active and
what it actually promises. Silently implying "it'll be ready at 6am" on a
platform that cannot deliver that is the failure mode to avoid.

### The staleness prompt — why it works on iOS

**Scheduling a local notification requires no background execution.** The
OS delivers it whether or not the app ever receives CPU time. This is the
key asymmetry: iOS can't reliably *generate* on a schedule, but it can
absolutely *prompt* on one.

So: whenever a forecast is generated, the app schedules a local
notification for the moment that forecast goes stale — "your forecast is
from yesterday morning, tap to refresh." Generating again cancels and
reschedules it. If an opportunistic background refresh happens to succeed
in the meantime, the notification is rescheduled rather than fired.

Worst case on iOS therefore becomes: a reliable prompt at the time the
user cares about, and a forecast a few tens of seconds after they tap —
rather than silence.

A refinement worth considering later: tie staleness to **model-cycle
availability** rather than a wall clock. ROADMAP item 1 measured that
aligned windows (all four models on the same cycle) open at 02:00, 08:00,
14:00 and 20:00 UTC. "A genuinely fresher cycle is now available" is a
more meaningful trigger than "24 hours elapsed," and the data to compute
it is already in the entry's metadata.

---

## Scheduling is the real constraint

**Mobile OSes will not reliably run a forecast at 6am.** This deserves to
be stated plainly because it is worse than the GitHub Actions scheduling
problem this project already fought at length (see ROADMAP items 3 and the
`ops/` docs — GitHub drops scheduled runs under load; we added four backup
cron slots and an external trigger to cope).

### The platforms are not symmetric — Android can do this, iOS cannot

**Android — viable.**
- `WorkManager`: 15-minute minimum periodicity, subject to Doze, and
  aggressively killed by several manufacturers' battery optimizations
  (Samsung, Xiaomi, Huawei are the usual culprits). Unreliable for a
  specific time, but its **10-minute execution budget comfortably fits a
  ~1-minute forecast run**.
- `AlarmManager.setExactAndAllowWhileIdle()`: fires at a specific
  wall-clock time even in Doze — the mechanism alarm clocks use. Needs the
  `SCHEDULE_EXACT_ALARM` permission (a one-time user grant in settings on
  Android 12+), and onboarding should include the standard "exclude this
  app from battery optimization" step. **This is the path to a real 6am
  local generation on Android.**

**iOS — not viable for local generation.** Verified against current
documentation rather than assumed:

| Mechanism | Execution budget | Catch |
|---|---|---|
| `BGAppRefreshTask` | **~30 seconds**, shared across all pending tasks | Far too short for a ~1-minute run — it would be killed mid-LLM-call |
| `BGProcessingTask` | Several minutes | Only runs when idle/charging, and **terminated immediately if the user picks up the device** — i.e. exactly when a 6am forecast would be generating |

Neither gives a timing guarantee in any case; the OS schedules them from
learned usage patterns, and an app left unopened can stop receiving
background time altogether.

### What every other weather app actually does

Worth stating because it settles the question: **no iOS weather app
performs scheduled on-device forecast computation.** Apple Weather, Yahoo
Weather and the rest all deliver morning forecasts by computing them on a
**server** and pushing via APNs. The device does no work at all.

This is not a workaround they settled for — it is the only mechanism iOS
offers. Any "ready before you wake up" requirement on iOS implies a server
somewhere, full stop.

The useful consequence for this project: **that server already exists.**
Connected mode is a GitHub Actions deployment doing exactly this, and it's
built and running today. Connected mode is therefore not a hedge or a
consolation prize — it is *the* iOS answer for guaranteed morning
delivery, and the [QUICKSTART](../QUICKSTART.md) already documents how a
user stands one up in about an hour.

Consequences, accepted deliberately:

1. **On Android**, standalone mode can honestly offer scheduled local
   generation — but only after the user grants exact-alarm permission and
   (on aggressive OEMs) excludes the app from battery optimization. Until
   both are done, it degrades to opportunistic.
2. **On iOS**, standalone mode promises *"fresh when you open it"* plus a
   staleness prompt, never "waiting for you at 6am." The UI must not imply
   otherwise.
3. Background refresh, where it exists at all, is opportunistic: when it
   fires it generates and notifies; when it doesn't, the staleness prompt
   and opening the app are the fallbacks.

One option not taken: a push-triggered run (a server sends FCM/APNs, the
app wakes and generates). It works, and it is what would let iOS have true
6am standalone delivery — but it requires running a server for *every*
user, which gives up the project's zero-infrastructure property wholesale.
Connected mode achieves the same result for the users who actually want it,
without imposing infrastructure on everyone else. Revisit only if
opportunistic refresh proves worse in practice than expected.

---

## Port scope — measured, not guessed

The Python package is **3,200 non-blank non-comment lines**. Not all of it
crosses over:

| Ports to Dart | Lines | Note |
|---|---|---|
| `models.py` | 192 | data classes; the JSON shape is the contract |
| `pipeline.py` | 465 | orchestration |
| `fetch/open_meteo.py` | 176 | the only *required* network source |
| `llm/*` (3 providers, prompt, schema) | 750 | prompt is a big string — easy; providers are HTTP |
| `verify/scoring.py` + `verify/pipeline.py` | 279 | **correctness-critical** |
| `extract.py` | 83 | **correctness-critical** |
| `aqi.py` | 69 | **correctness-critical** |
| `store/*` | 152 | becomes local DB, not JSON files on disk |
| `fetch/metar.py`, `fetch/waqi.py` | 135 | optional data sources |
| `dates.py`, `defaults.py`, `config.py` | 119 | config becomes app settings, not YAML |

| Does NOT port | Lines | Why |
|---|---|---|
| `cli.py` | 300 | no CLI on a phone |
| `publish/pages.py` | 199 | app renders natively; no HTML |
| `publish/email_gmail.py` | 78 | email stays a server/Apps Script concern |
| `health_check.py` | 75 | server-side operational check |
| `fetch/bulletin/*` + met-service parsers | ~600 | location-specific scraping and PDF table extraction; see below |

**~2,400 lines to port. 431 of them are the credibility-critical core**
(`extract` + `scoring` + `verify/pipeline` + `aqi`).

### Two later additions, and where they land

**`review.py` (weekly review) — ports.** Pure computation over stored
records, no I/O, and roadmap item 18 requires it in both surfaces:
"accuracy demonstrably improving over time" is this project's strongest
differentiator, and an app that couldn't compute its own findings would be
unable to make the claim its accuracy screen is built around. It also has to
produce *identical* findings to the server's from identical data, which is
exactly what the shared vectors exist to guarantee.

**Met-service parsing (`kmd_daily_parse`, `kmd_5day_parse`, the fetchers) —
does NOT port.** Three reasons, in increasing order of importance:

1. It is PDF table extraction. The Python side leans on pdfplumber; there is
   no comparable on-device Dart equivalent, and doing it in-app would mean
   shipping a PDF layout engine to parse a document the server already has.
2. It is ~600 lines per met service, times ~200 services.
3. **The decisive one: update latency.** When KMD changes its site layout,
   a server-side parser is a git push. An in-app parser is an app-store
   release, and every user's met-service data is silently absent until they
   update — for the users least likely to update promptly, indefinitely.
   Fragile scrapers belong where they can be fixed in minutes.

So the met service reaches the app as **data, not code**. Two paths, and the
second is nearly free given what already exists:

- **Connected mode:** the server sends the already-parsed `ModelPrediction`
  along with everything else. No app-side work at all.
- **Standalone mode:** the pipeline already publishes to GitHub Pages, so it
  can publish a small machine-readable JSON of the day's met-service
  prediction alongside the HTML. A standalone app fetches a few hundred bytes
  instead of scraping a PDF, and a fork gets the same for free by running the
  pipeline it already runs.

If neither is available, the app simply has no met-service model — and that
degrades honestly with no extra code, because a model with zero verified
checks is already named and excluded from the confidence figure rather than
quietly dragging it to zero. That behaviour was built for exactly this shape
of gap.

### De-risking the critical 431 lines

This project's entire claim to trustworthiness is that accuracy statistics
are computed deterministically in code and never by the LLM. A subtle
divergence between the Python and Dart implementations wouldn't crash —
it would silently produce *wrong accuracy numbers*, which then feed the LLM
prompt as its "track record." That is the worst failure mode available
here, because it looks fine.

**Mitigation: shared, language-neutral test vectors.** The Python suite
already contains hand-computed fixtures for exactly these modules:

| Suite | Tests |
|---|---|
| `test_scoring.py` | 26 |
| `test_aqi.py` | 14 |
| `test_pipeline.py` (verification) | 12 |
| `test_extract.py` | 11 |
| `test_dates.py` | 5 |
| **total** | **68** |

**Done** — see [`spec/`](../spec/README.md). 54 cases across 10 files,
covering `score_prediction`, `compute_rain_pct_trend`, `mean`, both
extraction functions, `get_onset_hour`, `prediction_row_date_for_target`,
`add_days`, `hours_old`/`is_stale` and `summarize_ground_aqi`.

`tests/test_vectors.py` asserts Python still satisfies its own exported
vectors, so the contract cannot silently rot as the code changes. That
guard is verified rather than assumed: inverting the error-sign convention
in `score_prediction` makes it fail, restoring it makes it pass.

The vectors deliberately pin the invariants that are easy to get wrong in
a port and damaging when wrong — unknown-is-not-false, `actual - predicted`
error signs, a real dry call versus an absent series, onset only at lead
time 0, and stale AQI excluded from the range but still counted. See
[`spec/README.md`](../spec/README.md) for why each one matters.

---

## Data model

**The committed JSON schema is the contract**, unchanged. `DailyLogEntry`
(see `models.py`) is already a clean, versioned, provider-neutral record
with `model_predictions`, `verification`, `morning_issuance`, and `meta`.
The app stores the same shape.

Storage: SQLite (`sqflite`/`drift`) with the full entry JSON in a column
rather than a shredded relational schema. Reasons: the shape evolves (three
fields have been added in the last week alone), queries are almost entirely
"give me date X" or "give me the last N," and keeping the blob identical to
what the pipeline commits means connected mode is a straight import with no
translation layer.

At 14 KB/day, a decade of history is ~50 MB. Storage is a non-issue.

## Measured budgets

| Per forecast run | Size |
|---|---|
| Open-Meteo fetches (hourly, daily×8d, regional, air quality) | ~21 KB |
| LLM request (~45K tokens) | ~200 KB |
| Stored result | 14 KB |

Two runs/day ≈ 400 KB/day, ~150 MB/year. Fine on wifi; worth a
"wifi-only background refresh" setting for metered plans.

## LLM key handling

The user's key goes in platform secure storage
(`flutter_secure_storage` → Android Keystore / iOS Keychain), never in
shared preferences and never in the local database.

The app calls the provider API **directly from the device**. This is a real
advantage of native over a PWA: Anthropic's API blocks browser-origin
requests by default, and a native app isn't subject to that or to CORS
generally.

Providers mirror the Python side exactly — Gemini, Anthropic, and the
OpenAI-compatible family (which covers OpenRouter, Groq, Together and
others through one implementation). See `llm/` for the three reference
implementations, including the quirks worth carrying over: Anthropic's
forced-tool-use structured output and 529 `overloaded_error` retries;
OpenAI's strict `json_schema` mode with a `json_object` fallback.

## Spend control is a hard requirement

The user is paying per run with their own key, so an app that can quietly
burn money is unacceptable. This is a correctness requirement, not a
settings-screen nicety.

- **The user sets the refresh budget** — e.g. 1, 2, or 4 automatic runs a
  day — and that number is a **hard cap enforced in code**, not a target.
  A counter of runs-so-far-today gates every automatic generation.
  Overrunning it should be as impossible as double-counting an all-time
  verification check (see the `last_verified_target_date` guard in
  `verify/pipeline.py` — same principle, same reason).
- **Retries count against the budget.** The pipeline's providers already
  retry transient failures with backoff; on a metered key each attempt is
  real money, so the cap must be on *API calls*, not on *successful
  forecasts*.
- **Manual refresh is always available but never silent.** The button
  states the cost ("this uses 1 API call, ~X tokens") and requires an
  explicit tap to confirm. Manual runs are the user's own decision, so
  they may exceed the automatic budget — but they can never happen by
  accident.
- **Running usage is visible**: calls and estimated tokens today and this
  month, per provider.
- **Model choice is a cost *and* latency lever.** The ~1-minute figure
  comes from Gemini at `thinking_level: high` with this project's large
  prompt. A faster or cheaper model materially changes both. The settings
  screen should present that trade honestly rather than hiding it — and
  note that a genuinely fast configuration is the only thing that could
  ever fit inside iOS's 30-second window, though that should not be
  *relied* on.

## Screens (v1)

1. **Today** — current forecast in the chosen voice; stat grid; the full
   discussion.
2. **Outlook** — days 1–3 and 4–7.
3. **Accuracy** — per-model, per-lead-time skill over time. The
   differentiator versus every other weather app, and where `fl_chart`
   earns the framework choice.
4. **History** — past forecasts, both issuances where present.
5. **Settings** — location, LLM provider + key, voice/audience, run
   frequency, standalone vs connected.

## Voices

Directly reuses ROADMAP item 14 (multiple audience voices from one LLM
call). In standalone mode the app requests the user's chosen voice(s) in
its single call — which makes the feature *cheaper* on mobile than on the
server, since the app knows exactly which one voice this user wants and
needn't generate all of them.

---

## Open questions

- **Verification in standalone mode needs yesterday's actuals**, which
  means the app must fetch archive data for days it may not have been
  running. Recoverable (Open-Meteo's archive API goes back years), but the
  backfill logic is new work with no server-side equivalent.
- **A single device is a fragile home for an accuracy record.** Lose the
  phone, lose the history. Export/import, or optional sync to the user's
  own GitHub repo, is probably needed — which is connected mode wearing a
  different hat.
- **Notification content**: full forecast, or a summary that opens the app?
- **Does standalone need the WhatsApp summary field at all**, or is that
  purely a server/email concern?
- **iOS timeline** — Apple developer account pending; Android first is the
  plan, but the Dart port is platform-neutral so this is a release
  question, not an architecture one.

## Scope decision for v1

**Android gets the full feature set. iOS ships tap-to-generate plus the
staleness prompt.** Deliberate, and it follows the platform capabilities
rather than fighting them: Android can genuinely do scheduled local
generation, iOS genuinely cannot, and pretending otherwise would ship an
iOS app that quietly fails to do the thing it implies.

Everything else — night-before generation, in-app setup of connected
mode — is real and wanted, but is a later phase (ROADMAP item 16) rather
than a v1 blocker.

## Phasing

1. ~~**Export shared test vectors**~~ — **done**, see [`spec/`](../spec/README.md).
   De-risked everything after it, and was cheapest to do before the port
   existed.
2. **Dart core**: models, dates, extract, scoring, verification — passing
   the shared vectors. No UI.
3. **Fetch + LLM providers** in Dart; one forecast generated end-to-end
   in a test harness.
4. **Minimal UI**: Today + Settings, standalone, tap-to-generate only.
   This alone is a usable app on both platforms.
5. **Storage + history + accuracy charts.**
6. **Spend controls** — budget cap, usage display, confirm-on-manual.
   Before any automatic generation exists, so a scheduled run can never
   predate the cap that governs it.
7. **Scheduled local generation + notifications (Android).**
8. **Staleness prompt (both platforms).**
9. **iOS release.**
10. **Later:** connected mode, night-before generation, in-app deployment
    setup.

Steps 1–3 are where the correctness risk lives and deserve the care; 4–8
are conventional app work. Note that step 6 deliberately precedes step 7:
spend control is a precondition for automatic runs, not a follow-up to
them.
