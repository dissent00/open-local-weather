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

## Two modes, one app

Driven by a hard constraint (see [Scheduling](#scheduling-is-the-real-constraint)),
not by indecision:

**Standalone (default).** Everything on-device: fetch → verify → LLM →
store → render. Any location. No account, no server, no GitHub. The user
supplies an LLM key. Forecast generates when the app opens, plus
best-effort background refresh.

**Connected (opt-in).** Point the app at a GitHub deployment — this repo,
the user's own fork, or a friend's. The pipeline runs on schedule in
Actions as it does today; the app reads the committed JSON, and can
trigger an on-demand run. Gets reliable morning delivery, email, and a
shared auditable history.

These are not separate products. Same screens, same rendering, same local
store — the difference is where a forecast comes from, and a user can
switch. Standalone users who later want 6am reliability have an upgrade
path rather than a migration.

---

## Scheduling is the real constraint

**Mobile OSes will not reliably run a forecast at 6am.** This deserves to
be stated plainly because it is worse than the GitHub Actions scheduling
problem this project already fought at length (see ROADMAP items 3 and the
`ops/` docs — GitHub drops scheduled runs under load; we added four backup
cron slots and an external trigger to cope).

- **iOS** `BGAppRefreshTask`: the OS chooses when, learned from usage
  patterns. No time guarantee whatsoever. An app left unopened may stop
  getting background time entirely.
- **Android** `WorkManager`: better — but 15-minute minimum periodicity,
  subject to Doze, and aggressively killed by several manufacturers'
  battery optimizations (Samsung, Xiaomi, Huawei are the usual culprits).

Consequences, accepted deliberately:

1. Standalone mode promises **"fresh when you open it,"** not "waiting for
   you at 6am." The UI must not imply otherwise.
2. Background refresh is opportunistic. When it does fire, it generates
   and notifies; when it doesn't, opening the app is the fallback.
3. **Connected mode is the answer for guaranteed morning delivery** — and
   the honest reason to keep it, rather than a hedge.

There is a third option not taken: a push-triggered run (server sends FCM,
app wakes and generates). It works, but it requires running a server, which
gives up the project's zero-infrastructure property for everyone. Revisit
only if standalone background refresh proves worse in practice than
expected.

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
| `fetch/bulletin/*` | 97 | genuinely location-specific scraping; skip in v1 |

**~2,400 lines to port. 431 of them are the credibility-critical core**
(`extract` + `scoring` + `verify/pipeline` + `aqi`).

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

Plan: export these as JSON `{input, expected_output}` vectors into a
`spec/vectors/` directory. Both implementations run them. Python's suite
gains a test asserting it satisfies its own exported vectors (so the
vectors can't rot), and the Dart port must pass the identical file. The
two then cannot drift without a test going red in one of them.

This is worth doing **before** the port, not after — it's the cheapest
point at which it's still easy.

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

**Cost transparency is a product requirement, not a nicety.** The user is
paying per run with their own key. The app should show estimated token use
and let them choose model and run frequency accordingly.

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

## Phasing

1. **Export shared test vectors** (Python side, no Dart yet). De-risks
   everything after it.
2. **Dart core**: models, dates, extract, scoring, verification — passing
   the shared vectors. No UI.
3. **Fetch + LLM providers** in Dart; one forecast generated end-to-end
   in a test harness.
4. **Minimal UI**: Today + Settings, standalone only.
5. **Storage + history + accuracy charts.**
6. **Background refresh + notifications** (Android).
7. **Connected mode.**
8. **iOS.**

Steps 1–3 are where the correctness risk lives and deserve the care. 4–6
are conventional app work.
