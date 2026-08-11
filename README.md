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
   GitHub Pages, and email subscribers via Brevo.

All of this runs daily on GitHub Actions' free scheduled-workflow minutes, on
a public repo.

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

## Status

This repository is under active initial development — see open issues and
commit history for current phase. Not yet running in production.

## License

MIT — see [LICENSE](LICENSE).
