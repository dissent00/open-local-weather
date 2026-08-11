# Open Local Weather

An open-source, location-agnostic daily weather forecast system: genuine
multi-model synthesis (not a relayed single-source number) with a
self-improving accuracy loop, for places underserved by professional
meteorology.

This is a from-scratch rebuild of a working Google Sheets/Apps Script pipeline
(originally built for Kisumu, Kenya) onto free, self-contained GitHub-native
infrastructure. The Kisumu deployment is the live first instance of this repo,
and also the reference example for forking to a new location.

## How it works

1. **Fetch** — pull forward-looking multi-model guidance (GFS, ECMWF, ICON,
   UKMO, and an Open-Meteo blended "best match") from Open-Meteo, plus a
   regional pressure snapshot, air quality, optional METAR, optional
   ground-sensor AQI, and an optional local met-service bulletin.
2. **Verify** — deterministically (in code, never the LLM) score yesterday's
   predictions against actual observed conditions, independently at Day+0,
   Day+3, and Day+7 lead times, and recompute each model's rolling 10/30-check
   accuracy stats per lead time per variable.
3. **Synthesize** — hand an LLM the raw, disagreeing per-model data side by
   side along with the pre-computed verification results and track record,
   and have it reason explicitly about disagreement, write qualitative
   verification notes and skill summaries, and produce the forecast
   narrative. The LLM is never asked to do arithmetic.
4. **Publish** — commit the day's result as JSON back to this repo (git is the
   database — free, versioned, fully auditable), regenerate a static site on
   GitHub Pages, and email subscribers (see [Status](#status) for the current
   delivery mechanism).

All of this runs daily on GitHub Actions' free scheduled-workflow minutes, on
a public repo.

## Documentation

- **[docs-internal/ARCHITECTURE.md](docs-internal/ARCHITECTURE.md)** — how the
  system works and why, the invariants that keep it trustworthy, and the
  extension points for forking.
- **[docs-internal/ROADMAP.md](docs-internal/ROADMAP.md)** — what's next, with
  the measurements and constraints behind each decision.
- **[mailer/README.md](mailer/README.md)** — email delivery setup.

(`docs/` is the *generated* Pages site — don't hand-edit it; the pipeline
overwrites it every run.)

## Design principles

- **Genuine synthesis, not relay.** The LLM sees each model's raw, disagreeing
  data and reasons about disagreement — it is never handed a pre-averaged
  number to relay.
- **Per-model and per-variable skill tracking.** A model can be trusted on
  precipitation and not on wind, and that's tracked, not averaged away.
- **Lead-time tracking.** Model skill decays with lead time, unevenly across
  models — Day+0, Day+3, and Day+7 are scored independently. Day+3/Day+7
  never carry onset-timing data (only daily aggregates are fetched that far
  out, to control API cost), so the forecast never states a specific onset
  time beyond Day+0.
- **All arithmetic in code, never the LLM.** Rolling accuracy stats, error
  calculations, and raw prediction extraction are deterministic Python. This
  was a deliberate fix after finding that asking an LLM to compute rolling
  stats risked silent arithmetic drift with no way to notice from outside —
  the LLM's job is narrower: qualitative notes, skill summaries, and
  narrative synthesis, using pre-computed numbers as context.
- **Recency-weighted evidence.** Recent (5-10 day) verification outweighs
  30-day/all-time stats when they conflict — long-term stats exist to catch
  slow systematic bias, not override current conditions.
- **Location-agnostic config.** Forking this for a new place means editing
  `config/location.yaml` and nothing else — see `config/location.example.yaml`
  for a documented template. Local met-bulletin scraping is inherently
  location-specific (the shipped example is written for Kenya's KMD) and is
  designed to be a modular, swappable, skip-gracefully-if-unconfigured piece.
- **Stateless, self-correcting rolling stats.** Rather than maintaining
  fragile running totals, each run re-derives rolling-window scores from
  scratch off stored raw predictions and freshly fetched actuals. Only
  all-time check/correct counts are incremental — everything else
  self-heals.

## Known, permanent limitations

- No CAPE at daily resolution (an Open-Meteo limitation) — Day+3/Day+7 hazard
  assessment relies on gust maxima and rain totals alone.
- No true storm-center or track forecasting — only regional
  pressure-gradient description from point data. This system does not
  overclaim precision the underlying data doesn't support.
- Local bulletin fetching is genuinely location-specific and will likely need
  rewriting per fork.
- The 06:00 EAT run is built on model data roughly 9 hours old. Model runs
  land 3.8–7.3 h after their initialisation time (measured; see
  [ARCHITECTURE.md](docs-internal/ARCHITECTURE.md#timing-and-why-it-matters)),
  so at 03:00 UTC the 00z runs haven't arrived yet and the previous day's
  18z runs are the freshest available. A deliberate trade for a 6 AM email;
  a second afternoon run is the planned fix
  ([ROADMAP.md](docs-internal/ROADMAP.md)).
- Accuracy statistics are meaningless until roughly 10 verified checks have
  accumulated per model per lead time. The system says so in its own
  forecaster-confidence notes rather than implying more rigour than it has.

## Status

**Live**: [dissent00.github.io/open-local-weather](https://dissent00.github.io/open-local-weather/)
runs daily at 06:00 EAT for Kisumu, Kenya, with the subscriber email
following at ~06:20 EAT. Every forecast is committed to `data/log/` — the
full, auditable history of every prediction and its later verification.

Email delivery goes out via [`mailer/AppsScriptMailer.gs`](mailer/), a
standalone Google Apps Script companion — **not** the Python pipeline's
own `publish/email_gmail.py` (built first, but blocked: Gmail app
passwords aren't available on all accounts, and third-party ESPs like
Brevo need a verified custom domain under Google/Yahoo/Microsoft's 2024
bulk-sender rules, which a bare Gmail address can't satisfy either way).
The Apps Script sends via `MailApp` — Google's own infrastructure, OAuth
auth, no app password needed — on its own daily trigger, fetching that
day's committed forecast JSON straight from GitHub. See
[`mailer/README.md`](mailer/README.md) for the full rationale and setup.
No public self-serve subscribe form yet for the same domain-verification
reason; subscribers are added manually via Script Properties. Migrating to
a verified-domain ESP later is a contained swap either way — a new
`EmailSender` implementation behind `pipeline.py`'s Protocol for the
Python-native path, or just retiring the Apps Script.

A weekly health check (`.github/workflows/health_check.yml`) watches for
Gemini model deprecation and repo-inactivity risk — see
[`health_check.py`](src/openlocalweather/health_check.py).

## License

MIT — see [LICENSE](LICENSE).
