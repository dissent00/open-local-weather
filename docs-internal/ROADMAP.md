# Roadmap

Ordered roughly by value-per-effort. Each item states the problem first,
because several of these look obvious until you hit the actual constraint.

Status legend: **Next** · **Planned** · **Deferred** · **Done**

---

## 1. Second daily forecast run — 6 AM + 6 PM · **Done**

Shipped: `olw refresh-forecast` (`pipeline.run_refresh_pipeline`), scheduled
via `.github/workflows/evening_refresh.yml` at `7 15 * * *` (~18:07 EAT).
Live-verified end-to-end against a real committed entry — the narrative
correctly read as an update ("no material changes... since the morning
issuance") rather than a repeat, and staleness detection carried over
correctly since the fetch step is shared code with the morning run. 219
tests passing, including that `model_predictions`/`verification`/
`meta.generated_at_utc` survive a refresh byte-for-byte.

Went with Phase 1 (refresh mode) as designed below, as a genuinely separate
CLI subcommand and workflow file rather than a mode flag on the existing
ones — cleaner given how different the step sequence actually is (no
verification, must read the existing entry first). One addition beyond the
original design: `evening_refresh.yml` shares daily.yml's concurrency group
(both write the same `data/log/<date>.json`, so a delayed morning run and
the evening run must never race on it), and refresh fails loudly
(`RefreshWithoutMorningRunError`) rather than silently no-oping if the
morning run never happened — which doubles as an earlier, second daily
health signal: if `daily.yml` silently doesn't fire again, the evening run
now fails and emails at ~15:07 UTC instead of the gap only surfacing the
next morning.

Phase 2 (first-class scored issuances, tracking the evening forecast's own
skill separately) stays a real option — not taken up, per the original
"only if Phase 1 proves the value" plan.

### Measured inputs

All models run 4 cycles a day (00/06/12/18z) and take several hours to
arrive. Delays measured twice on 2026-08-11 from Open-Meteo's per-model
metadata endpoints (`/data/{model}/static/meta.json`), consistent both times:

| Model | Availability delay | Usable horizon |
|---|---|---|
| ICON | ~3.8 h | ~7.5 d |
| GFS 0.13 | ~6.6 h | ~16 d |
| ECMWF IFS 0.25 | ~7.1 h | ~8 d |
| UKMO 10 km | ~7.3 h | ~7.2 d |

**The 6.6–7.3 h delays set a hard floor: model data can never be fresher
than ~8 hours old, at any hour of the day.** That reframes an earlier note
in this repo that called the morning run "~9 hours stale" — technically
true, but only *one hour* worse than the theoretical best. The morning slot
is not the problem it first appeared to be.

What actually varies by hour is **cycle alignment** — whether all four
models are on the same run, or some are a cycle behind:

| Run at (UTC) | (EAT) | ECMWF | GFS | ICON | UKMO | Oldest data |
|---|---|---|---|---|---|---|
| 02:00 | 05:00 | 18z | 18z | 18z | 18z | 8 h |
| **03:00** | **06:00** | 18z | 18z | 18z | 18z | **9 h** ← current |
| 07:00 | 10:00 | 18z | 00z | 00z | 18z | 13 h ✗ split |
| 08:00 | 11:00 | 00z | 00z | 00z | 00z | 8 h |
| 13:00 | 16:00 | 00z | 06z | 06z | 00z | 13 h ✗ split |
| 14:00 | 17:00 | 06z | 06z | 06z | 06z | 8 h |
| **15:00** | **18:00** | 06z | 06z | 06z | 06z | **9 h** |
| 19:00 | 22:00 | 06z | 12z | 12z | 06z | 13 h ✗ split |
| 20:00 | 23:00 | 12z | 12z | 12z | 12z | 8 h |

Aligned windows open at **02:00, 08:00, 14:00 and 20:00 UTC** and stay clean
for about two hours before the faster models jump a cycle ahead. Landing in
a *split* window is the thing to avoid — mixing a 06z ECMWF with a 12z ICON
makes model disagreement partly an artefact of run age rather than genuine
forecast uncertainty, which is exactly the signal the whole synthesis
depends on reading correctly.

### Recommendation: keep 03:00 UTC, add 15:00 UTC

| | Morning | Evening |
|---|---|---|
| Cron | `7 3 * * *` (unchanged, off-hour) | `7 15 * * *` (off-hour, shipped) |
| Delivery | ~06:07 EAT | ~18:07 EAT ✓ your target |
| Cycle | 18z (prev. day) | **06z (same day)** |
| Age | 9 h | 9 h |
| Alignment | all four aligned ✓ | all four aligned ✓ |

15:00 UTC hits 6 PM EAT exactly, has all four models on the same cycle, and
uses a run initialised **12 hours later** than the morning's. Same freshness,
genuinely newer information — most valuable for "tonight and tomorrow",
which is what an evening forecast is for.

If you'd rather trade an hour of clock time for an hour of data freshness,
**14:00 UTC (17:00 EAT)** is the optimum (8 h). You said afternoon/evening
is flexible, so this is a real option — but 18:00 is the better *product*
time and the difference is one hour of model age.

Do **not** pick 16:00 or 17:00 UTC (19:00/20:00 EAT): still aligned, but
10–11 h old with nothing gained.

### Why not wait for 12z?

The 12z cycle is the freshest same-day data, but all four models aren't in
until ~19:20 UTC — a 20:00 UTC (23:00 EAT) run. Too late to be useful, and
the 06z run at 15:00 UTC already covers the evening well.

### The design question this forces

A second run re-forecasts a day that already has a committed entry. What
happens to `data/log/YYYY-MM-DD.json`?

This matters more than it looks: that file's `model_predictions` are what
tomorrow's verification scores. Overwrite them and you're no longer scoring
what you actually published at 6 AM.

**Recommended: phase it.**

**Phase 1 — refresh mode (low risk, most of the value).**
Add `olw run-daily --mode=refresh`, which:
- fetches fresh model data and re-synthesises the narrative
- rewrites `narrative_markdown`, `today_properties`, and republishes `docs/`
- **skips** verification entirely (yesterday's actuals don't change during
  the day — nothing new to score)
- **preserves** the morning run's stored `model_predictions`, so the
  accuracy loop keeps scoring what was actually published
- records `refreshed_at` in `meta` so the audit trail stays honest

Public-facing forecast improves; the skill-tracking machinery is untouched.

**Phase 2 — first-class issuances (only if Phase 1 proves the value).**
Make each run a separately-scored issuance so the *evening* forecast's skill
is tracked too — plausibly the more accurate one, and worth knowing. Needs a
schema change (`issuances: [...]` per day, or date-plus-label files) and a
lead-time lookup that's issuance-aware. Real complexity; don't take it on
speculatively.

### Prompt design

The evening narrative shouldn't read like a stale copy of the morning's.
`build_system_prompt(..., is_refresh=True)` adds an instruction block: lead
with what's changed since the morning issuance (or say explicitly that
nothing material has changed), shift emphasis to tonight and tomorrow, and
`build_user_prompt(..., morning_narrative=...)` gives the LLM the actual
morning text to update rather than blindly rewrite. Confirmed live — see
above.

### The design question this forced — resolved

A second run re-forecasts a day that already has a committed entry.
`data/log/YYYY-MM-DD.json`'s `model_predictions` are what tomorrow's
verification scores, so a refresh must never overwrite them — see "Shipped"
above for how this was implemented (`run_refresh_pipeline` reads the
existing entry, merges only narrative/today_properties/ground_aqi/
whatsapp_summary/meta.refreshed_at into a copy, leaves everything else
byte-for-byte untouched).

### Prerequisite — already handled before this shipped

A second run would have double-counted `all_time_checks` every single day
(the counters are the one non-self-healing piece of state). Fixed and
regression-tested via the `last_verified_target_date` guard before this item
was implemented. Refresh mode skipping verification entirely makes it
doubly safe.

### Remaining open question

Whether the evening refresh should also email, or stay web-only. Shipped as
**web-only** (no `email_sender` call in `run_refresh_pipeline` by design) —
revisit if there's a real desire for an evening email distinct from the
morning one.

---

## 2. Severe-weather alerts — hourly monitoring · **Next**

### First, a correction worth stating plainly

**Kenya Met publishes no RSS feed.** Verified directly:

| Probe | Result |
|---|---|
| `/feed/`, `/rss`, `/weather-warnings/feed/` | 404 |
| `/?feed=rss2` | HTTP 200 but returns ordinary HTML — the param is ignored |
| `/wp-json/wp/v2/posts` | 404 (REST API disabled) |
| `<link rel="alternate" type="application/rss+xml">` | absent from page head |

So the plan can't be "poll the RSS feed" — it has to be **HTML scraping with
change detection**. The good news: `https://meteo.go.ke/weather-warnings/`
lists warning posts newest-first in page order, the same structure the
existing KMD bulletin scraper already handles, so
`fetch/bulletin/kenya_kmd.py` gives us a proven pattern to mirror.

Sample of what's there today: `heavy-rainfall-advisory-752026`,
`large-waves-24042026`, `strong-winds-24042026` — slugs carry the hazard
type and often a date.

### Second source: GDACS (this one *does* have a real feed)

`https://www.gdacs.org/xml/rss.xml` — verified live, ~1 MB of XML, real
severity-coded global alerts (floods, cyclones, earthquakes) with
geo-coordinates. Needs bounding-box filtering to the Nyanza basin. Useful as
an independent cross-check so a single scraper breaking doesn't blind the
whole alert path.

### Design

```
hourly cron ──▶ scrape KMD /weather-warnings/  ──┐
           └──▶ fetch + geo-filter GDACS RSS  ───┤
                                                 ▼
                              new item not in data/alerts_seen.json?
                                                 │ no → exit (cheap, no LLM)
                                                 ▼ yes
                              LLM triage: relevant to our region?
                                          severity? plain-language summary?
                                                 │ not relevant → record, exit
                                                 ▼ relevant
                              write data/alerts/<id>.json, commit,
                              surface banner on site, push alert email
                                                 (see "Delivery" below —
                                                 not the mailer's own poll)
```

### Delivery: the alert email must be pushed, not polled

Caught while wiring up the second daily send (`mailer/AppsScriptMailer.gs`
now has two: `sendDailyForecastEmail()` for the morning run,
`sendEveningRefreshEmail()` for the evening refresh) — both work by having
Apps Script poll GitHub on its own fixed timer. That's the right design for
a routine forecast email, where being off by a few minutes is irrelevant
and there's a retry loop to absorb it. **It is the wrong design for a
severe-weather alert.** If `alerts.yml`'s hourly poll detects a new,
relevant KMD/GDACS warning at :13 past the hour, and alert delivery is
*also* just "Apps Script checks for a new alert file on its own separate
timer," the email could sit unsent for up to another full timer interval on
top of the up-to-an-hour detection delay already inherent to hourly
polling — stacking two independent poll delays for exactly the message
where that matters most.

**Fix: push, don't poll, for the last hop only.** Once `alerts.yml`
detects and commits a genuinely new, relevant alert, it should hand it
straight to the mailer instead of waiting for a second independent timer
to notice:
- Deploy `AppsScriptMailer.gs` as an Apps Script **Web App** (Deploy → New
  deployment → Web app), adding a `doPost(e)` handler.
- `doPost(e)` validates a shared secret (an Apps Script Script Property,
  matched against a new GitHub Actions secret — the alert *payload* itself
  is public data either way, this is just to stop randoms from POSTing
  fake alerts) and calls a new `sendAlertEmail(alertPayload)`.
- `alerts.yml`, right after committing `data/alerts/<id>.json`, does an
  HTTP POST to the Web App URL with the alert JSON as the body.
- A poll-based `sendAlertEmail` check should still exist as a **backstop**
  (e.g. folded into the existing hourly cadence) in case the push itself
  fails — log the push's HTTP response rather than treating a failure as
  silent success, but don't make the backstop the primary path or the
  original problem just comes back.
- Detection stays exactly as poll-based as it has to be — KMD/GDACS give
  no push mechanism of their own. Only the last hop, "alert is ready → get
  it to a human," changes from poll to push. Worst case becomes "up to an
  hour to detect, then roughly a minute to deliver," not "up to two
  stacked poll intervals."

Routine daily/evening forecast emails are correctly left poll-based (see
above) — this fix is scoped to the alert path only, where minutes actually
matter and the whole point is speed the detection side can't provide alone.
**Build the `doPost()` endpoint generically from the start** (keyed by a
`type` field, not alert-only) — see item 3, which reuses this exact
mechanism to retire the daily/evening polling too, once it exists.

**Deduplication is the whole ballgame.** An alert system that re-sends the
same warning every hour trains people to ignore it, which is worse than
having no alerts. `data/alerts_seen.json` (committed, like everything else)
records every warning ID ever seen; only genuinely new IDs proceed. KMD
sometimes posts an "updated" advisory for an ongoing event — treat as new
only if content materially changed, not merely re-listed.

### Cost — measured, not estimated

**Compute: free.** GitHub Actions gives public repos unlimited scheduled
minutes. Real per-step timings from this repo's CI:

| Step | Time |
|---|---|
| checkout | 2 s |
| setup-python | 0 s (cached) |
| `pip install` | **11 s** ← dominates |
| actual work | 1–2 s |
| **total** | **~16 s** |

24 runs/day ≈ **6.5 minutes of Actions time daily**, all free. Worth
installing only `requests` for this job rather than the full package —
`pdfplumber`, `jinja2`, `pydantic` and friends aren't needed to poll a web
page, and that cuts the 11 s install to ~3 s.

**Network: negligible.** The warnings page is 111 KB and returns in ~0.11 s.
24 fetches/day ≈ 2.7 MB — trivial for us, and polite toward meteo.go.ke.

Note: the page sends **no `ETag` and no `Last-Modified`**, so cheap
conditional GETs aren't possible — every poll downloads the full 111 KB.
It does send `Cache-Control: max-age=14400`, i.e. the server itself
considers the content good for **4 hours**. Hourly polling is therefore
already 4× more frequent than the origin expects to change, which is a
reasonable margin for alerting but argues against going below hourly.

**LLM: near-zero.** Triage only runs when a genuinely new warning appears —
realistically a handful of times a month, not 24× a day. Well inside the
Gemini free tier.

**Real limitation to accept up front:** GitHub's scheduled workflows are
routinely delayed 5–20 minutes under load, and the guarantee is
best-effort, not punctual. Combined with hourly polling, this system
delivers *"usually within the hour"*, **not** *"within minutes"* —
detection is the bottleneck, not delivery (see "Delivery" above: pushing
the email once an alert is detected removes the *second* poll delay, but
can't remove the first one, since KMD/GDACS give no faster option). That is
fine for heavy-rainfall advisories issued hours ahead. It is **not** a
flash-flood warning system, and the site copy should not imply otherwise.

**Alert fatigue guardrails:**
- severity floor — advisories below a threshold go to the site, not to email
- rate limit — hard cap on alert emails per day
- geographic filter — KMD covers all of Kenya; most warnings won't touch
  Nyanza. Filter on county names from `location.yaml` before alerting.
- alert emails must be visually distinct from the daily forecast

### Honest risks
- Scraping an HTML page nobody promised to keep stable. It *will* break.
  Mitigation: alert on scraper failure (a warnings page that suddenly parses
  to zero posts is suspicious, not "all clear"), and keep GDACS as an
  independent second source.
- We are re-publishing an official met service's warnings. Attribute clearly
  and link the original — never let a paraphrase read as our own authority.
- Never let the LLM *invent* urgency. Its job is triage and plain-language
  summary of a warning that already exists, nothing more.

### Effort estimate

Roughly a day's work, in dependency order:

| Piece | Effort | Notes |
|---|---|---|
| `fetch/alerts/kenya_kmd_warnings.py` | ~2 h | Mirrors the proven `kenya_kmd.py` pattern |
| `store/alerts_store.py` (dedup) | ~1 h | The part that must be right |
| `llm/alert_triage.py` | ~1.5 h | New pydantic schema; provider layer already exists |
| `.github/workflows/alerts.yml` | ~0.5 h | Minimal deps, `13 * * * *` (offset off the exact hour — see the cron-congestion note in ARCHITECTURE.md; `daily.yml`/`health_check.yml` already learned this the hard way) |
| Site banner + `docs/alerts/` | ~1.5 h | New template + publisher hook |
| `sendAlertEmail()` + `doPost()` in the mailer | ~2 h | New Apps Script function, Web App deployment + shared-secret auth, poll-based backstop trigger, harness cases. Key `doPost()` by `type` from the start — item 3 reuses it for daily/evening |
| `alerts.yml` push step | ~0.5 h | POST to the Web App URL right after committing a new alert; log response, don't swallow failures |
| `fetch/alerts/gdacs.py` | ~1.5 h | Real feed; geo-filter is the fiddly bit |
| Scraper-health alarm | ~0.5 h | Zero-posts-parsed ⇒ notify, never "all clear" |

A useful first slice is the first four rows plus the health alarm: that
gets alerts onto the site, with email (poll-based backstop only, push
deferred) and GDACS following once the dedup
behaviour has been observed against real KMD posting patterns for a while.

### Tasks
- [ ] `fetch/alerts/kenya_kmd_warnings.py` — scrape, parse, stable IDs
- [ ] `store/alerts_store.py` — `alerts_seen.json`, dedup, `alerts/<id>.json`
- [ ] `llm/alert_triage.py` — relevance/severity/summary schema
- [ ] `.github/workflows/alerts.yml` — `13 * * * *` (not `0 * * * *`), minimal deps
- [ ] Scraper-health alarm (zero-posts-parsed ⇒ notify, don't assume calm)
- [ ] Site alert banner + `docs/alerts/`
- [ ] `sendAlertEmail()` in the Apps Script mailer, poll-based backstop trigger
- [ ] `doPost()` Web App endpoint + shared-secret auth, for push delivery
- [ ] `alerts.yml` push step — POST to the Web App URL right after committing
- [ ] `fetch/alerts/gdacs.py` — RSS + bounding-box filter

---

## 3. Push-based mailer delivery — replace Apps Script polling entirely · **Planned**

### The idea

Item 2's alert-delivery fix (GitHub Actions → Apps Script Web App
`doPost()`, shared-secret-guarded) isn't actually alert-specific — it's a
strictly better version of what the daily/evening sends already do by
polling. Build it once, generically, and it replaces polling for all three
payload types (morning, evening, alert), not just the new one.

### What it would replace

Today: `daily.yml`/`evening_refresh.yml` commit a JSON file on a schedule;
`AppsScriptMailer.gs` runs on its *own separate* schedule (~13 minutes
later, hand-tuned) and polls GitHub's raw-content CDN, retrying up to 3
times over ~4.5 minutes if the commit hasn't landed yet. This works —
tested and live-verified — but:
- the ~13-minute offset is a hand-tuned guess, not a guarantee;
- a day where *both* independent schedulers are unusually late is missed
  silently, by design (documented in `AppsScriptMailer.gs`'s TIMING note);
- every send does at least one GitHub fetch whether or not anything's
  actually new — the file existing is the *only* signal Apps Script has;
- the retry-with-sleep loop exists purely to paper over not knowing when
  the commit actually landed.

With push: right after `git push`, the workflow POSTs the date + run type
(`morning`/`evening`/`alert`) to the Apps Script Web App. `doPost()` routes
by type to the matching `send*Email()` — the exact same fetch/build/send
code already in `AppsScriptMailer.gs`, just invoked the instant there's
something to send instead of on a timer guessing when that might be. No
more offset-tuning, no more blind retry loop, no more "missed if both
schedulers are late" gap — there's only one scheduler now (GitHub Actions'
own, already reliable), and Apps Script only acts when told.

### Keep the poll-based triggers as a backstop, don't delete them

Push can fail — a network blip mid-workflow, an Apps Script quota hit, a
misconfigured deployment. Keep `createDailyTrigger()` /
`createEveningRefreshTrigger()` firing at their current times as a safety
net, but make `send*Email()` idempotent first: check an
"already sent for this date+run" marker (a Script Property, e.g.
`LAST_SENT_MORNING=2026-08-13`) before sending, so a backstop poll that
fires after a successful push is a no-op, not a duplicate email. Item 2
needs this exact pattern for the alert push anyway — build it once,
generically, and both cases get it for free.

### Sequencing

Do this as part of item 2's `doPost()` work, not a separate deployment:
same endpoint, same shared secret, same Web App, handler keyed by
`type: "morning" | "evening" | "alert"` from the start. Building the alert
push first and *not* reusing it here would mean maintaining two delivery
mechanisms for no reason. Natural order: ship item 2's push generically →
small follow-up wires `daily.yml`/`evening_refresh.yml` to call it too,
demoting the existing triggers to backstop.

### Honest risk

This is genuinely more moving parts than "cron + poll + retry," even
though each individual piece is simpler and more certain. If the Web App
deployment or shared secret ever gets misconfigured, the backstop poller
is what saves the day's email — so it has to stay real and tested (in
`test_mailer.js`), not decay into a "we'll never actually need this"
fallback nobody's checked in months.

### Tasks
- [ ] `LAST_SENT_<RUN>` idempotency guard in `send*Email()` (needed before
      push+backstop can coexist without double-sending)
- [ ] `daily.yml` push step — POST `type: "morning"` after commit
- [ ] `evening_refresh.yml` push step — POST `type: "evening"` after commit
- [ ] Confirm `doPost()` (built in item 2) routes all three types correctly
- [ ] Harness cases: push delivers, push fails → backstop still sends,
      push succeeds → backstop is a no-op

---

## 4. Ground AQI staleness — review and decide on a fix · **Planned**

### The problem, with real morning evidence

Ground AQI stations are configured 3-deep for Kisumu (Airport, Ochieng'
Avenue, Dunga Beach — `config/location.yaml`'s `waqi_stations`), likely
solar-powered low-cost sensors, per the working theory from earlier in this
project ("they just started updating... guessing solar-powered"). The
morning run consistently catches them mid-wake-up. Real evidence, both
mornings this project has actually run:

| Date | Run time (UTC) | Stations reporting *anything* | Notes |
|---|---|---|---|
| 2026-08-11 | ~04:43 | 1 of 3 (Airport only) | `aqi: 84` |
| 2026-08-12 | ~04:43 | 1 of 3 (Airport only) | `aqi: null`, `pm25: 157.0` — composite AQI missing even though PM2.5 reported |

A same-day afternoon test run (unrelated dry-run, ~13:00 local) got all 3
stations back with real numbers — consistent with the solar theory:
Ochieng' Avenue and Dunga Beach aren't just *stale* in the morning, they
report **nothing at all** yet.

**A second, distinct failure mode found in the same evidence**: the
2026-08-12 Airport reading has `measured_at: null` — WAQI didn't return a
parseable timestamp that call, even though `pm25` came through. Since
`aqi.is_stale()` treats a missing age as stale (`age is None or age >
STALE_THRESHOLD_HOURS`, `aqi.py`), that reading gets excluded from
`GROUND AQI SUMMARY` for the same reason a genuinely 6-hour-old reading
would — "no timestamp from WAQI this call" and "sensor hasn't reported
since last night" currently look identical downstream, even though they're
different problems with potentially different fixes.

### Current behavior (already shipped, not broken — just worth revisiting)

`summarize_ground_aqi()` excludes stale/unknown-age readings from the
range/worst-station summary; the prompt's DATA QUALITY NOTES section
already tells the LLM to say so explicitly and lean on CAMS model data
alone when this happens (`llm/prompt.py`). This is honest and doesn't
crash — the open question is whether it's the *best* product decision, not
whether it's broken.

### A second, more fundamental problem: hyperlocal volatility, not just staleness

Confirmed by the operator's own ongoing observation (not just the two
mornings captured above — "the morning run never has good ground data from
the stations I'm polling," i.e. the 1-of-3 pattern is consistent, not
occasional): local AQI "fluctuates wildly locally with widespread refuse
burning." This is a different problem from staleness, and no amount of
better timing fixes it — open trash burning produces sharp, hyperlocal
pollution spikes that a regional CAMS model won't capture, and that even a
"fresh" reading from the nearest configured station (Kisumu Airport,
several km from any given point in the city) may simply not represent,
depending on wind and exact proximity to burning sites. Candidates 1-3
below only address *when* a station reports, not *whether a station a few
km away is even measuring the right air*.

### Four candidate paths (three as originally raised, one new) — none chosen yet, needs more data first

1. **Delay the morning run** to give sensors more time to wake up. Real
   constraint: this can't be tuned in isolation — item 1's cycle-alignment
   analysis already fixed 03:00 UTC against a specific tradeoff table
   (aligned model windows open at 02:00/08:00/14:00/20:00 UTC only). Any
   change here has to be re-checked against that table, not picked on AQI
   grounds alone. Also unproven: is "later" reliably enough later, or does
   the wake-up time itself vary day to day? Don't know yet.
2. **Ask the LLM to predict AQI from historical data.** Worth flagging
   against this project's core invariant up front: numeric estimation
   should be deterministic code, not an LLM guess — see `models.py` and
   `verify/scoring.py` throughout, and the rain-skill trend work (this
   session) as the most recent example of moving a comparison *out* of the
   LLM's hands, not into them. The architecturally-consistent version of
   this idea is a deterministic "typical AQI for this station at this time
   of day," computed in code from stored history (the same shape as the
   rolling-window machinery already built for rain skill), with the LLM
   only narrating around a pre-computed estimate — not an ungrounded
   forecast from the model itself.
3. **Show last night's (most recent pre-stale) reading with a note.**
   Closest to what's already partly true — individual per-station readings
   already render with age + a stale flag (`forecast.html.jinja`'s Ground
   AQI Stations section) — but the *summary* line (the one actually quoted
   in Today's Forecast) currently drops stale readings rather than saying
   "last known: 84 as of 22:14 last night." Cheapest of the three options,
   and doesn't touch the run schedule or add LLM-side estimation. Doesn't
   address the hyperlocal-volatility problem either — a stale-but-nearby
   reading isn't necessarily a better proxy than no reading, if a burning
   event happened between when it was taken and now.
4. **Get a personal, always-on sensor** — the operator's own current
   instinct, and arguably the only option here that addresses *both*
   problems at once: sited exactly where it matters (fixes hyperlocal
   representativeness, not just the 3 configured stations' distance from
   any given point in the city) and mains-powered rather than solar (fixes
   the morning-staleness pattern directly, no schedule tuning needed).
   **Integration path matters and was checked, not assumed**: PurpleAir —
   probably the best-known consumer option — was *removed* from WAQI's
   aggregation in September 2024, so a PurpleAir sensor would need its own
   bespoke fetch path (PurpleAir has its own API), not a free ride through
   the existing `waqi_stations` config. **Sensor.Community (formerly
   Luftdaten)**, by contrast, is still aggregated by WAQI — 35,000+
   stations per WAQI's own network page — so a Sensor.Community-compatible
   sensor (classic hardware: an SDS011 particulate sensor + ESP8266/ESP32,
   widely available as a DIY kit) would show up in WAQI's own station
   search once registered, and could be added to `config/location.yaml`'s
   `waqi_stations` list with **zero new code** — same `fetch/waqi.py` path
   already handling the other 3 stations. Worth choosing hardware with
   this in mind rather than picking on brand recognition alone.

### Status: no longer just "gather more data," a real decision is close

The 1-of-3-stations morning pattern is now confirmed persistent by the
operator's own ongoing observation, not just the two logged mornings above
— that part of the "review, then decide" gate has effectively been met.
What's still open is whether to pursue candidate 4 (buy a sensor) versus
one of 1-3 (software-only fixes) — a real vs. hardware tradeoff, not a
data-gathering one anymore. `measured_at: null` (the second failure mode
found in the evidence) is a smaller, independent bug worth fixing
regardless of which path is chosen — it's conflating "WAQI didn't return a
timestamp" with "sensor genuinely hasn't reported in hours," which candidate
4 wouldn't fix on its own if the new sensor's `measured_at` handling has the
same quirk.

---

## 5. Real sending domain for email · **Planned**

### Where things stand

Email currently goes out via `mailer/AppsScriptMailer.gs` (Google Apps
Script `MailApp`) after two dead ends:

1. **Brevo / any third-party ESP** — needs a verified custom domain with
   DKIM/SPF/DMARC under Google/Yahoo/Microsoft's 2024 bulk-sender rules. A
   `@gmail.com` from-address can *never* pass DKIM alignment through a third
   party, because only Google can sign for `gmail.com`.
2. **Direct Gmail SMTP** — needs an app password, which Google no longer
   issues on some newer accounts.

`MailApp` sidesteps both. It works, and it's a reasonable place to be. But:

### Why it's still worth fixing

- **Sender legitimacy** — `something@gmail.com` reads as a person, not a
  service. A `forecast@<domain>` address is more credible for a public
  utility people are meant to rely on.
- **Quota** — consumer Apps Script caps around 100 recipients/day (500 on
  Workspace). Fine now; a hard ceiling later.
- **No self-serve signup** — this is the real cost. There's no safe way to
  put a subscribe form on a static site without a form backend, which is
  exactly what a verified ESP provides. Right now subscribers are added by
  hand, which doesn't scale past friends-and-family.
- **Deliverability + unsubscribe** — bulk senders are expected to offer
  one-click unsubscribe (RFC 8058). ESPs handle it; `MailApp` doesn't.

### Plan
1. Register a domain (~$10–15/yr). Something matching the project reads best.
2. Add it in Brevo → add the DKIM/SPF/DMARC records they generate at the
   registrar → wait for verification.
3. Write `publish/email_brevo.py` implementing `pipeline.EmailSender`
   (transactional send + contacts API). The Protocol already exists, so this
   plugs in without touching `pipeline.py`.
4. Build the Brevo-hosted (or embedded) double-opt-in signup form, restore
   the `Subscribe` nav link, and ship `docs/subscribe.html`. `NavLinks`
   still carries a `subscribe` field for exactly this.
5. Migrate existing subscribers with their consent — don't silently import.
6. Retire the Apps Script mailer, or keep it as a fallback path.

DNS propagation is the only slow part; the code is a few hours.

---

## 6. Verify secondary-point predictions · **Planned**

Found during review: the pipeline fetches and caches **secondary-point
actuals** (Lake Victoria) every single day — an extra API call — and
`store/actuals_cache.py` faithfully stores them, but **nothing ever reads
them**. Verification only ever receives `actuals_primary`.

This is a faithful port of the Apps Script original, which also passed
`dailyActualsSecondary` into its scoring function and never used it. We
inherited a latent no-op.

Meanwhile `peak_wind_kmh` — a secondary-point value — *is* published in
every forecast, and is exactly the number boaters would act on. It is
currently never scored.

Two honest options:
- **Wire it up** (preferred): score secondary-point wind against secondary
  actuals, giving the "Conditions for Boaters" section a real skill record.
- **Or stop fetching it** and drop the wasted daily call.

Either is fine. Silently fetching data nobody reads is not.

---

## 7. Operational hardening · **Planned**

- **Pipeline failure alerting.** Currently relies on GitHub's default
  workflow-failure email. Fine, but easy to miss in a busy inbox — and 60
  consecutive silent failures also gets scheduled workflows auto-disabled.
  The weekly health check covers the slow version of this; a louder signal
  on the daily path would be better.
- **Float noise in committed JSON.** Values like
  `mslp_trend: 0.8999999999999773` make diffs uglier than they need to be.
  Round on write.
- **Data retention.** `LOG_RETENTION_DAYS = 180` is defined and documented
  but not implemented — deliberately, since git history preserves everything
  and small daily JSON files stay manageable well past 180 days. Revisit
  only if `data/log/` actually becomes unwieldy.
- **Cold-start honesty on the site.** Until ~10 verified checks accumulate,
  the track record is statistically meaningless. The prompt already tells
  the LLM to say so; the site should show it too, so the accuracy claim is
  never overstated.
- **No retry logic in `fetch/open_meteo.py`.** Confirmed real, not
  hypothetical: an evening refresh run aborted on 2026-08-14 with
  `Read timed out (read timeout=30)` calling Open-Meteo — correctly
  failed loudly rather than publishing partial data (no bad commit), and
  a later backup schedule slot caught it about an hour later with no
  data loss, but the run itself had zero cushion against a single
  transient timeout. `GeminiProvider` already has bounded exponential
  backoff for exactly this class of failure (`llm/gemini.py`,
  `_post_with_retry` — added after an earlier real 503 aborted a run);
  Open-Meteo gets called several times per run (hourly, extended daily,
  regional pressure, air quality, archive), so the aggregate odds of
  *some* call hitting a transient failure in a given run are higher than
  any single Gemini call's own failure rate. Same retry pattern, applied
  to `fetch/open_meteo.py`'s `requests.get` calls, would reduce how often
  a single hiccup costs a whole run attempt instead of relying on a
  backup slot to eventually catch it later.

---

## 8. Multi-provider LLM support · **Done**

Shipped: `LLM_PROVIDER` selects between `gemini` (default, unchanged),
`anthropic` (`llm/anthropic.py`) and `openai` (`llm/openai_compat.py`,
covering OpenAI, OpenRouter, Groq, Cerebras, Together, vLLM, LM Studio and
Ollama in one class). Brought forward from "someday" because
bring-your-own-key turned out to be a prerequisite for two other things:
the forkability quickstart (item 15) and the planned mobile app, which is
premised on the user supplying a key for the LLM of their choice.

Each provider owns its own schema adapter as designed. Anthropic and the
OpenAI family share `to_strict_json_schema()` (standard JSON Schema);
Gemini keeps `to_gemini_schema()` because its dialect genuinely differs —
uppercase type names and a `nullable` flag versus lowercase types with
null as a type union.

Anthropic's structured output uses **forced tool use** rather than a
`response_format` field, which turns out to be the sturdiest of the three:
the `tool_use` block carries an already-parsed object, so there's no
JSON-in-a-string step and no markdown-fence stripping to get wrong.

**Not yet done — the resilience half of the original motivation.** This
adds *choice* of provider, not *failover* between them: a provider outage
still stops that run. Automatic fallback (try the configured provider,
then a secondary) is now a small change since the Protocol has three real
implementations behind it, but it isn't built, and it needs a deliberate
decision about whether a fallback forecast should be marked as such in the
committed entry.

**Live-verification status, honestly:** Gemini is proven daily in
production and was re-verified through the refactored provider-selection
path. Anthropic and OpenAI-compatible are unit-tested against mocked HTTP
(53 tests) but have not yet made a real API call — no key was available in
the environment where they were written. First real use should be a
`--dry-run` against the live endpoint, not a scheduled run.

Also worth revisiting: Google's newer **Interactions API**. Researched
2026-08-11 — `generateContent` remains fully supported with no announced
deprecation, so there's no urgency, and migration would be contained to
`llm/gemini.py` + `llm/schema.py`.

---

## 9. WhatsApp distribution · **Deferred**

Carried over from the original design. Meta's Cloud API loses its free
service-message window on 2026-10-01; cost at small subscriber counts would
be trivial but not zero.

**Do not** build on unofficial WhatsApp-Web automation libraries — ToS
violation with real account-ban risk.

The `whatsapp_summary` field already exists in the LLM response schema and
is populated, so the content side is ready whenever the delivery side is
worth doing.

---

## 10. OCI free-tier deployment guide — full migration off GitHub Actions · **Deferred**

`ops/README.md` already documents this as "path 3" (full migration off
GitHub Actions — the most reliable option for scheduling, at the cost of
needing a real server) but deliberately doesn't build it, since path 2
(`ops/trigger_workflow.sh`, an externally-triggered `workflow_dispatch`
from cron on infrastructure the operator already has) covers the actual
reliability gap without that tradeoff.

**Conditional on how the backup-slot fix performs.** Explicitly not
started while daily.yml/evening_refresh.yml's new backup cron slots are
still being observed (see item 2's sibling discussion — GitHub's own docs
admit scheduled jobs "may be dropped" under load, confirmed twice on this
repo already) — only worth building if that mitigation turns out to be
insufficient and a move to path 2 or 3 actually happens.

**If it does happen, worth doing properly rather than as a one-off.** A
start-to-finish guide for Oracle Cloud Infrastructure's Always Free tier
specifically — not a 12-month trial like AWS/GCP's free tiers, genuinely
free forever (2 AMD VMs, or up to 4 ARM Ampere A1 cores + 24 GB RAM) — as
a documented alternative to "fork this and rely on GitHub Actions alone"
that stays true to the project's zero-ongoing-cost goal for forks in
underserved locations, for an operator who'd rather run real cron than
depend on GitHub Actions' scheduler at all (for triggering only, per path
2, or for the full pipeline, per path 3).

Scope, if built: instance provisioning (ARM Ampere vs. AMD tradeoffs),
outbound-only firewall/security-list rules (this workload only calls out
to Open-Meteo/WAQI/Gemini/GitHub — no inbound ports needed), secrets
handling (env file vs. OCI Vault — Vault has its own free-tier limits
worth checking against actual usage), cron setup, and an explicit
decision on whether it hosts the full pipeline (path 3) or just
`trigger_workflow.sh` (path 2) — these have different setup steps and the
guide should be honest about which one it's demonstrating.

---

## 11. Met-office catalog + location setup script — toward "country + coordinates in, config out" · **Planned — priority raised**

**This became app-critical.** The app's whole philosophy is "use your
location, generate your own forecast," which means a user in an arbitrary
place must be able to pick up their *local* sources without editing YAML or
knowing what a met service is. Two discovery problems, both of which the
app needs solved and neither of which is solved today:

- **National/local met services** — the catalog described below. Needed so
  the app can offer "add your local forecast source" rather than leaving
  the field blank forever.
- **Ground AQI stations** — happily much easier, and already possible:
  WAQI's own API has a geo-search endpoint (`GET /feed/geo:{lat};{lng}/`,
  confirmed in item 4's research) that finds nearby stations from
  coordinates with no catalog at all. The app should use it to offer
  nearby stations at setup instead of asking a user to hand-verify station
  ids at waqi.info, which is a genuinely unreasonable thing to ask.

Not being built yet, but it moves ahead of the other Planned items once
the app's settings screen exists — that screen is where both land.

### The goal, and why it's two very different problems wearing one trench coat

The ask: catalog national/local met offices globally, keep the links current,
and move toward a setup script where a forker gives a country and
city/lat-long and gets a working `config/location.yaml` back — automating
what's currently a hand-written config file per fork. Real, valuable
direction, directly extending this project's existing "location-agnostic
config" design principle (README's design-principles list) rather than
introducing a new one. But `LocationConfig`'s fields split cleanly into
"genuinely solvable generically" and "needs bespoke work per place," and
conflating them would produce a plan that overpromises on the hard half.

**Already solvable, close to free:**
- **`timezone`** — a coordinate-to-timezone lookup is a solved problem
  (e.g. the `timezonefinder` Python package works fully offline from
  lat/long, no API or catalog needed).
- **`waqi_stations`** — WAQI's own API has a geo-search endpoint,
  `GET /feed/geo:{lat};{lng}/`, that finds nearby stations directly from
  coordinates. No catalog needed at all for this field — confirmed via
  WAQI's own API docs (aqicn.org/api), not assumed from memory.
- **`primary_point`** — is just the input coordinates.

**Plausible, needs real work but no fundamentally hard research problem:**
- **`metar_station_icao`** — open datasets of airport ICAO codes +
  coordinates exist (e.g. OurAirports' public data); "nearest airport with
  METAR reporting" is buildable from that, not yet verified in detail.
- **`region_points`** (the regional MSLP snapshot points — currently
  hand-picked nearby towns for Kisumu, e.g. Busia/Homa Bay/Migori/Kisii) —
  auto-generating "N points at roughly X km spacing around the primary
  point" is plausible, but which points actually matter meteorologically
  for a given region is more of a judgment call than a lookup; may always
  need a human sanity-check even if auto-generated as a starting point.

**The genuinely hard one: `local_bulletin_url` / `local_bulletin_source_name`
and an actual working `BulletinFetcher`.** A catalog of *links* is
achievable and valuable on its own — but the existing `KenyaKMDBulletinFetcher`
(`fetch/bulletin/kenya_kmd.py`) is bespoke HTML-scraping + PDF-extraction
code written specifically for KMD's site structure. There is no universal
met-bulletin API; every national service's website is its own bespoke
scrape target, exactly the tension the README's "Known, permanent
limitations" section already names ("Local bulletin fetching is genuinely
location-specific and will likely need rewriting per fork"). A links
catalog tells a forker *where* to point `local_bulletin_url` and gives them
`kenya_kmd.py` as a worked example to adapt — it does not, by itself,
produce a working scraper for a new country. Don't let the setup script's
UX promise more automation here than the underlying problem allows.

### Where the catalog itself should come from — don't hand-build it

**[WMO's own Contacts Directory](https://contacts.wmo.int/members/)** lists
all 193 WMO member states' national meteorological services — this is the
authoritative source to derive the catalog from, not something to
hand-curate country by country. Also worth checking before committing to
"one bespoke scraper per country" as the only path: **WMO's [World Weather
Information Service](https://worldweather.wmo.int/)** aggregates official
forecasts *sourced from* those same national services under one platform.
If it exposes warnings/bulletins in something more structurally uniform
than 193 independent government websites, that could meaningfully shrink
the hard half of this problem — not yet verified in enough depth to know,
flagged here specifically so that check happens before a lot of per-country
scraper effort does.

### Phased plan

**Phase 1 — the catalog + a config-generator that's honest about its gaps.**
- `config/met_offices.yaml` (or similar), keyed by country/region, sourced
  from WMO's Contacts Directory: name, website, bulletin/warnings URL if
  findable, a format hint (HTML page / PDF / RSS if one exists / unknown).
- A setup script (`olw init-location` or similar) that takes country +
  city/lat-long and generates a starter `location.yaml`: fills
  `timezone`/`waqi_stations`/`primary_point` for real, looks up the
  catalog for a `local_bulletin_url` suggestion (clearly marked as
  "needs a fetcher written, defaults to `NullBulletinFetcher`" if none
  exists yet), and leaves `region_points`/`metar_station_icao` as
  clearly-flagged manual TODOs rather than guessing badly.
- This alone is real progress — "here's your met office, here's your
  timezone and AQI stations for free, here's what's still on you" is a
  much better forking experience than a blank YAML file and a README.

**Phase 2 (speculative, only if Phase 1 shows demand) — generic-enough
bulletin fetching for the subset of met offices that turn out to share
common site patterns** (e.g. many government sites run on the same CMS
platforms). Would reduce, not eliminate, the "write a bespoke scraper"
burden — some fraction of countries, not all 193.

### Honest risk

Phase 1's catalog will go stale — met office URLs change, sites get
redesigned. "Keep the links up to date" (as asked) needs an actual
mechanism, not a one-time build: candidates are a periodic health-check
(similar in spirit to the existing weekly model-deprecation check) that
flags catalog entries returning 404s, or simply accepting it needs manual
maintenance and saying so rather than presenting stale data as current.

---

## 12. LLM-guided setup as a parallel path to scripted automation · **Planned**

### Not hypothetical — this project already proves the concept once

`reference/CLAUDE_CODE_HANDOFF_BRIEF.md` is the actual document that got
*this* project built: a detailed brief that let an AI coding agent take a
working Google Sheets/Apps Script pipeline and rebuild it from scratch onto
GitHub-native infrastructure — including exactly the hard, judgment-heavy,
not-mechanically-automatable work item 11 flags as its ceiling (KMD's
bulletin scraper, `fetch/bulletin/kenya_kmd.py`, was written by inspecting
the actual site live — checking for RSS/`wp-json`/etc., reading the real
page structure — not by consulting a pre-built catalog). Item 12 is
proposing to formalize and generalize that same process for *forking to a
new location*, not invent a new capability.

### Why this is genuinely parallel to item 11, not a duplicate of it

Item 11's catalog + setup script handles the mechanically-solvable fields
fast and cheaply (`timezonefinder`, WAQI's geo API) without needing an LLM
session at all for those — no reason to spend a Gemini/Claude call
computing a timezone from coordinates. Where item 11 hits its honest
ceiling — a bulletin fetcher for a country not yet in the catalog,
deciding sensible `region_points`, adapting anything that needs actual
judgment — is exactly where a guided LLM session earns its keep: it can
research the target met office's site live, write a new `BulletinFetcher`
implementation against the existing Protocol (`fetch/bulletin/__init__.py`)
by generalizing the `kenya_kmd.py` pattern, and ask the human clarifying
questions a static script can't. The two are meant to compose: item 11's
catalog (once built) becomes an input the guided setup consults rather
than re-deriving from scratch every time.

### What this would actually be — smaller than it first looks

Worth being precise about *why* the original brief was necessary before
assuming its successor needs the same shape. `CLAUDE_CODE_HANDOFF_BRIEF.md`
existed because the old Apps Script/Sheets pipeline's logic lived in code
that wasn't self-documenting — narrating that opaque logic into something
rebuildable *was* the brief's entire job. The current Python codebase isn't
in that position: `ARCHITECTURE.md` already documents the invariants and
*why* behind past decisions, `ROADMAP.md` already shows the actual method
used for things a new fork would need to redo for its own location (the
cron-timing/model-cycle-alignment analysis in item 1 is a worked example a
new deployment could follow, not just a Kisumu-specific answer), and
`config/location.example.yaml` is already the fork template. Most of
"current state of the art" is already written down — just addressed to a
human reader, and not sequenced as a runbook.

So the real gap is smaller and more specific than a from-scratch document:
a short, **agent-addressed** setup sequence (imperative steps: ask for
country + city/lat-long, derive what item 11's tooling can derive, consult
the met-office catalog, point at `ARCHITECTURE.md`'s cron-timing method for
this location's own optimal schedule, etc.) that mostly *points into* the
existing README/ARCHITECTURE/ROADMAP/`ops/`/`mailer/` docs in the right
order rather than re-explaining their content. The old handoff brief's job
is done (the migration it described already happened — it stays in
`reference/` as history, not something to update in place); its natural
successor is a new, much shorter file whose job is orchestration, not
narration.

### Honest risk

**Realistic scope depends entirely on what tools the LLM instance actually
has** — a chat-only model with no code execution or browsing can narrate
instructions for a human to run by hand; an agentic coding assistant with
shell/git/web access (like the one that wrote this line) can do the whole
setup directly, the same way this session has been operating on the live
Kisumu deployment all along. The brief should be written to degrade
gracefully across that range, not assume the most-capable case.

---

## 13. Ground AQI beyond WAQI — docs/examples for other sensor networks · **Planned**

### Why this belongs on the roadmap even though it wouldn't help Kisumu

PurpleAir isn't meaningfully represented in Kenya — confirmed while
researching item 4, and part of why it's not a real option for *this*
deployment even setting aside the aggregation issue. But PurpleAir has
excellent density in the US and parts of Europe, and other citizen-science
networks (Sensor.Community, IQAir AirVisual, government-run low-cost
networks in various countries) dominate in other regions. A fork of this
project deployed somewhere PurpleAir *is* dense should be able to use it —
the current ground-AQI code is WAQI-specific by construction
(`WaqiStation`, `waqi_stations`, `WAQI_TOKEN`, `fetch/waqi.py`), so nothing
else plugs in today regardless of how good the coverage is locally for a
given fork. This is a forkability gap, not a Kisumu problem — same category
as items 11/12.

### What this actually needs

Generalizing the ground-AQI fetch path the same way `fetch/bulletin/` already
generalizes local met bulletins: a `BulletinFetcher` Protocol with
`NullBulletinFetcher` (default) and `KenyaKMDBulletinFetcher` (the one real,
worked example) as implementations — `local_bulletin_url`/
`local_bulletin_source_name` in config select which one applies. The
equivalent here: a `GroundAqiFetcher`-shaped Protocol, WAQI's current
implementation kept as-is (it works, it's what Kisumu actually uses), and a
new `PurpleAirGroundAqiFetcher` as the second real, worked example — not a
big abstract multi-network framework built speculatively for networks
nobody's using yet. Per the ask, the deliverable can be as light as "docs +
one working example," matching how `kenya_kmd.py` itself started as the
single reference implementation for local bulletins, not a general scraper
framework.

Concretely, PurpleAir needs its own API key (`PURPLEAIR_API_KEY`, separate
from `WAQI_TOKEN`) and its own request/response handling — different shape
from WAQI's aggregator API, not a drop-in. Worth verifying PurpleAir's
actual current response format directly before writing the fetcher (same
"check, don't assume" habit that already caught the PurpleAir/WAQI
delisting in item 4) — in particular whether it returns a computed AQI or
only raw PM2.5, since converting PM2.5 to US AQI needs the EPA breakpoint
formula applied in code if PurpleAir doesn't do it for you, consistent with
this project's "all arithmetic in code, never assumed from an API" habit.

Config shape also needs to generalize a level: `waqi_stations: list[WaqiStation]`
only fits one network type. A fork wanting both WAQI stations AND a
PurpleAir sensor needs something like a `type`-discriminated list rather
than a WAQI-only one — real schema work, not just a new fetcher file.

### Status

Explicitly not started. The user's own call: "let's just leave the AQI
stuff as it is for now" for Kisumu specifically (item 4 stays as
documented, no code change) — this item exists so the PurpleAir-support
idea is captured and ready, not lost, whenever it's actually worth building
for a fork that would benefit from it.

---

## 14. Multiple audience "voices" from one LLM call · **Planned**

### Not a new pattern — this codebase already proves it works

`GeminiForecastResponse` already returns two differently-styled outputs
from a single call: `today_narrative` (the full formal multi-section
discussion) and `whatsapp_summary` (short, emoji-forward, casual) —
same underlying facts, two different renderings, zero extra API calls.
This item is asking for the same trick generalized: N audience-specific
narratives (sailors: wind-focused; surfers: water/air temp + wind + wave-
focused, casual tone; etc.) instead of one fixed extra format, still one
call. Worth citing this precedent plainly — the risk here is prompt/schema
complexity and output quality at N>2 voices, not "can one call produce
multiple styles at all," which is already a solved, shipped fact in this
repo.

### Design: config-driven, not hardcoded to Kisumu's audiences

Sailors and surfers are Kisumu/Lake-Victoria-specific. A landlocked fork
might want "farmers" or "hikers" instead, or none at all. Consistent with
every other location-specific feature in this project (`secondary_point`,
`waqi_stations`, `local_bulletin_*`), the voice set belongs in
`config/location.yaml`, not in code:

```yaml
audience_voices:
  - key: sailors
    label: "For Sailors"
    focus_hint: "Emphasize wind speed, direction, and gust risk over the lake; keep a professional, safety-conscious tone."
  - key: surfers
    label: "For Surfers"
    focus_hint: "Emphasize water and air temperature, wind, and wave conditions; casual, friendly tone."
```

Empty/absent by default — a fork that doesn't configure any gets exactly
today's single-narrative behavior, unchanged.

### What actually needs to change

- `models.LocationConfig` gains `audience_voices: list[AudienceVoice]`
  (empty default).
- `llm/schema.GeminiForecastResponse` gains
  `audience_narratives: list[AudienceNarrative]` (`audience_key: str`,
  `narrative_markdown: str`), alongside the existing `today_narrative`
  (kept as-is — the general/default voice every existing consumer,
  mailer included, already reads and needs no changes for).
- `llm/prompt.build_system_prompt` lists the configured voices and their
  focus hints, and states explicitly: **same underlying facts and numbers
  across every voice, only emphasis and tone differ** — this needs to be
  an unambiguous instruction, not implied, given this project's zero-
  tolerance history around numbers drifting between representations (see
  the AFD/site-styled email work, where keeping two renderings
  content-consistent was an explicit, named design goal, not an
  afterthought).
- `models.DailyLogEntry` gains `audience_narratives: dict[str, str]` (or
  equivalent) to store the result.
- Site: each configured voice gets its own labeled section (simplest:
  appended below the main narrative, à la the existing Ground AQI Stations
  section) or its own page — the morning/evening separate-URL pattern
  just shipped is a reasonable model to reuse if per-voice pages end up
  wanted, not decided here.
- Mailer: **not touched in a first version.** Making email voice-aware
  means per-subscriber preference, which doesn't exist in the current
  comma-separated `SUBSCRIBER_EMAILS` config at all — real feature, but a
  separate scope decision. First version is site-only, additive, and
  doesn't touch anything the mailer or existing narrative consumers rely
  on.

### Honest cost estimate — from real measured numbers, not a guess

The thinking-level experiment earlier this project measured the real
production call at "high" thinking: 4,235 thinking tokens, 2,352 output
tokens, 45,063 total — of which the single narrative is the dominant
share of output (the rest — verification notes, skill summaries,
`today_properties`, `whatsapp_summary` — are comparatively short
structured fields). Two more audience narratives at roughly similar
density would plausibly add somewhere in the 800-1,600 output-token range
(shorter than the general narrative if focused, as intended — a wind-only
sailor narrative shouldn't need the full Detailed Discussion/Synoptic
Overview treatment). Trivial against the 250K-token/run budget either
way. **Not yet verified**: gemini-3.6-flash's actual max-output-tokens
ceiling — worth confirming before committing to a large voice count,
though nothing here suggests it'd be a real constraint at 2-3 voices.

### Canned presets, not just a free-text box

Asking a user to describe the voice they want is a blank-page problem, and
most people will never fill it in. Ship a set of ready-made audiences they
can pick with one tap, with free-text as the escape hatch rather than the
default:

- **Sailors** — wind speed/direction/gusts, lake or coastal state, safety-
  conscious tone.
- **Surfers** — wave, wind, water and air temperature; casual.
- **General outdoors** — comfort, rain timing, UV, "is this a good day to
  be outside".
- **Camping** — overnight lows, rain overnight, wind for tents, a
  go/no-go leaning.
- **Farm/garden** — rain totals, frost risk, soil-relevant framing.
- **Commuters** — rain onset/end around morning and evening peaks.

These map cleanly onto the `focus_hint` field already in the config sketch
above, so presets are *data*, not new code paths — which also means a fork
can add its own without touching the pipeline. The camping one is worth
noting as slightly different in kind: it implies a **recommendation**
("good window Thursday night"), not just a description, which is a
stronger claim than the rest and should be held to the same
no-overclaiming standard as everything else here.

### Honest risks

- **Distinctiveness at scale is unproven.** Two voices (narrative +
  WhatsApp summary) already works. Whether a single call reliably produces
  N *meaningfully* differentiated voices — not just the same content with
  synonyms swapped — needs a real empirical check against actual output,
  the same way the thinking-level change was validated with a live call
  before being trusted, not assumed to just work at arbitrary N.
- **Consistency drift is the real failure mode to test for**, not
  omission — a sailor voice quietly stating a different wind speed than
  the surfer voice would be far worse than a voice that's merely bland,
  since it would look like two independent forecasts disagreeing rather
  than one forecast framed two ways. The prompt's "same facts, different
  emphasis" instruction (above) exists specifically to prevent this, and
  should be a named thing to check for when this actually gets validated,
  not just hoped for.

---

## 15. Fork-ready setup documentation · **Done**

Shipped [QUICKSTART.md](../QUICKSTART.md): fork to working daily emails in
about an hour, no local setup and no server.

The gap it closed was larger than "the README could be friendlier" —
three required steps were documented **nowhere**: enabling GitHub Pages
(without which the site silently never exists and every email link is
dead), adding repository secrets, and getting an LLM API key at all. Plus
two silent traps: GitHub disables Actions on forks by default, and — the
one that corrupts rather than fails — **a fork inherits the upstream
location's `data/log/` history**, so the accuracy loop scores the previous
location's stored predictions against the new town's weather and feeds the
LLM meaningless statistics as its track record. That is now a prominent
step with exact commands, not a footnote.

Also removed `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD`/`SUBSCRIBER_EMAILS` from
`daily.yml`, which had been telling every newcomer they needed a Gmail app
password — the exact dead end this project already documented as
unworkable, and never actually set on the live deployment.

---

## 16. Mobile app (Flutter, Android first) · **Planned**

Full design: **[APP_ARCHITECTURE.md](APP_ARCHITECTURE.md)**.

Summary of the decisions taken there:

- **App runs the whole pipeline on-device** rather than reading this
  repo's published forecast. A read-only client only serves people who
  want *Kisumu's* data; the point of an app is that anyone sets their own
  coordinates with no GitHub account. Prerequisite was item 8
  (multi-provider LLM), now done.
- **Two modes, one app**: standalone (default, no account) and connected
  (paired with a GitHub deployment for guaranteed scheduled delivery).
- **Flutter**, chosen over React Native mainly for `fl_chart` — the
  accuracy history is the differentiating screen — plus a single toolchain
  and typing that maps cleanly onto the existing pydantic models.
- **Measured budgets**: ~21 KB of weather fetches and ~200 KB of LLM
  request per run; 14 KB stored per day, ~5 MB/year. Size is a non-issue,
  which is what made standalone viable.

**v1 scope:** Android gets the full feature set (scheduled local
generation, notifications, spend controls); iOS ships tap-to-generate plus
a staleness prompt. That split follows the platform capabilities rather
than fighting them — see below.

**Refresh strategy is a user choice, not a fixed design.** Tap-to-generate,
scheduled local generation, night-before generation, staleness prompt, and
connected mode are all real options with different platform support and
different tradeoffs; the app offers what the platform can actually honor
and says plainly what each one promises. Later phases add night-before
generation and **in-app setup of connected mode** — the GitHub API can
create a repo from a template and set secrets, so "want guaranteed 6am
delivery? I'll set up your free deployment" could be an in-app flow rather
than a doc to go read, turning the iOS gap into an upgrade path.

Two things flagged as genuinely hard, not glossed:

1. **Scheduling.** Mobile background execution is *less* reliable than
   GitHub Actions' scheduler, which this project already fought at length.
   iOS gives no timing guarantee at all; Android's WorkManager is subject
   to Doze and manufacturer battery-killers. Standalone mode therefore
   promises "fresh when you open it," not "waiting at 6am" — and connected
   mode exists precisely to offer the latter honestly.
2. **Porting the credibility-critical core.** ~2,400 lines port to Dart,
   of which 431 (`extract`, `scoring`, `verify/pipeline`, `aqi`) compute
   the accuracy statistics this project's trustworthiness rests on. A
   silent divergence there wouldn't crash, it would produce *wrong numbers
   that look fine*. Mitigation is to export the existing 68 hand-computed
   fixtures as language-neutral JSON test vectors that both
   implementations must pass — and to do it **before** the port, as
   phase 1. **Phase 1 is done**: [`spec/`](../spec/README.md) holds 54
   cases across 10 files, with `tests/test_vectors.py` keeping them from
   rotting (verified by deliberately breaking the sign convention and
   confirming it goes red).

---

## 17. OpenRouter, and the "no key required" question · **Planned**

Two separable ideas; the second is much harder than it looks.

### 17a. OpenRouter as a provider — already works today

Nothing to build. `LLM_PROVIDER=openai` with
`LLM_BASE_URL=https://openrouter.ai/api/v1` already routes through
OpenRouter, since it speaks the OpenAI chat-completions API. That single
endpoint reaches most current models, including `:free` variants, and
QUICKSTART.md already documents it.

Worth doing: name specific `:free` model ids in QUICKSTART as a
recommended zero-cost starting point, and verify the strict `json_schema`
path works on them (some free models are served by backends that only
support `json_object` — the fallback exists, but which models need it is
unverified).

### 17b. Centralizing the key so users don't need one — the hard part

Appealing, and the onboarding problem is real: asking for an API key
before someone has seen a single forecast is a brutal first run. But two
constraints bite hard, and both are worth stating before any design work.

**You cannot ship an API key in a mobile app.** An APK is trivially
decompiled; a key embedded in the binary is a key published to the world,
and it would be drained within days. "Centralize" therefore necessarily
means *running a proxy server* that holds the key and rate-limits per
install. That gives up this project's zero-infrastructure property and
makes the operator liable for other people's usage and abuse.

**The free ceiling is lower than it sounds.** OpenRouter's documented
limits (checked 2026-08): **20 requests/minute**, and **50 requests/day**
on an unfunded account or **1,000/day** after a one-time $10 credit
purchase. Those are per-account, so a centralized key means all users
share one pool. At two forecasts a day per user that is roughly **500
users maximum**, with no headroom for retries — and the 20/min ceiling
means a morning burst across timezones would start failing before the
daily cap is even reached.

**Decision for now: bring-your-own-key.** The app assumes the user
supplies a key; a trial is a later problem to solve deliberately rather
than a v1 blocker. The rest of this section is the analysis to pick up
from when it becomes one.

**The version that actually works: a bounded free trial.** Not "nobody
needs a key" but "nobody needs a key *to try it*." A small proxy issues N
free forecasts per install (say 10-20), enough to see real value, after
which the app asks for a key. That bounds the operator's exposure to a
known number, keeps the free ceiling meaningful, and solves the genuine
onboarding problem rather than the theoretical one. It still needs a
server — so it belongs alongside the other server-shaped ideas (item 16's
connected mode), not as a way to avoid them.

Open question if pursued: OpenRouter's terms on proxying a key to third
parties need reading properly before building anything, not after.

---

## 18. Weekly review — drift and blind spots · **Partly shipped**

**Required in BOTH the open-source pipeline and the app**, not one or the
other. "Accuracy demonstrably improving over time" is the single strongest
differentiator this project has against every other weather product, and a
review that only existed server-side would leave the app unable to make the
claim its own accuracy screen is built around. The deterministic half
belongs in `app/olw_core` alongside the rest of the shared math, so both
implementations compute identical findings from identical data — the same
reasoning that produced `spec/`. Only the narration differs by surface.

### What shipped

`review.py` computes findings across the whole stored record — comparative
rankings, systematic temperature/wind bias, and never-verified lead times —
each carrying the evidence and confidence that produced it. Findings are
gated: a ranking needs both models at 10+ checks AND a gap above the
~15-point binomial noise floor at n=10, so weak evidence produces no
finding rather than a hedged one.

Wired into three places: the daily and refresh prompts (with an explicit
instruction not to re-derive a withheld ranking from the raw track-record
percentages, which the model can otherwise see and compare by eye), and a
public `accuracy.html` rendered with no LLM involvement at all.

Verified against the live 8-day record, where `best_match` sat 38 points
clear of ECMWF and the review correctly published nothing. Also surfaced a
real case the design had only anticipated in theory: at Day+7 ICON and UKMO
had 0 checks against the other models' 1, because their forecast horizons
end short of 7 days — so coverage within a lead time is reported by the
*weakest* model, never the best-covered one.

### What has not shipped

Items 2 and 4 below — pattern-level blind spots ("afternoon convective
events are under-called") and meta-verification of the LLM's own confidence
claims — both need substantially more history than exists yet, and neither
should be attempted on a record this thin. Item 3 (never-verified
variables) is only partly covered: an unscored *lead time* is reported, an
unscored *variable* like the secondary point's `peak_wind_kmh` is not.
There is also no narrated weekly issuance and no weekly workflow; the
deterministic findings refresh on every daily run instead, which costs no
LLM call and keeps the page current rather than up to six days stale.

The Dart port in `app/olw_core` carries the prompt instruction but not yet
`review.py` itself.

### What it replaced

There *is* a weekly branch in `pipeline.py` (`WEEKLY_BATCH_WEEKDAY`, Monday),
but it is purely **data hygiene**: re-fetch 40 days of actuals and replace
the cache wholesale, so any drift in the daily upserts self-heals. It
performs no analysis at all.

Before this item, the daily loop saw exactly two horizons: yesterday's
individual scores, and rolling 10/30-check aggregates. Nothing ever looked
across the record and asked *what are we systematically getting wrong?*

### What a review could see that the daily run structurally cannot

1. **Drift over time.** `rain_pct_trend` (shipped) compares recent against
   longer-term, but only for rain and only as a two-window snapshot. A
   review could look at the whole series and distinguish a genuine decline
   from noise.
2. **Blind spots.** The daily run scores individual days; it never asks
   what *kind* of day gets missed. "Four of the last five afternoon
   convective events were under-called" is a pattern only visible across
   the record, and is exactly the sort of thing a forecaster would notice.
3. **Never-verified variables.** `peak_wind_kmh` for the secondary point is
   fetched, stored and published every day — and scored never (see item 5).
   A review should surface "this number has never been checked" rather than
   letting it sit indefinitely.
4. **Meta-verification.** The LLM writes qualitative confidence notes and
   skill summaries. Nothing checks whether those claims were *right*.
   Comparing "what the forecaster said it was confident about" against
   "what actually verified" is the most novel thing on this list and fits
   the project's ethos precisely.
5. **Coverage honesty.** How many of the last 30 days actually got
   verified? Already partly tracked (`checks_in_window_10`), and it matters
   much more in-app — see below.

### Design constraints, learned the hard way elsewhere in this project

- **All arithmetic in code.** The review computes its statistics
  deterministically; the LLM only narrates them. Same rule as everywhere
  else, and the same reason.
- **Regenerate, never accumulate.** Reviews must be recomputed from the
  raw record each week, never built on top of previous reviews. Otherwise
  an unverified week-1 claim ("GFS runs warm") gets read by week 2, echoed,
  and hardens into received wisdom that no longer traces to any
  measurement. This is the same principle that makes the rolling stats
  stateless and self-correcting, applied to narrative.
- **Feeding findings forward is the actual payoff.** A review that only
  produces a page nobody reads is decoration. The value is injecting its
  *deterministic findings* into subsequent daily prompts, so the system
  reasons from longitudinal patterns rather than only from yesterday.
- Cost is one extra LLM call a week. Negligible.

### Verification in the app-as-pipeline model

The user's question, and it has a sharp answer worth recording.

The logic ports unchanged — that's what `app/olw_core` and the shared
vectors are for. What does *not* port is the assumption of unattended
daily execution:

- **Actuals are recoverable.** Open-Meteo's archive API serves any past
  date, so an app that missed a week can backfill the observations when
  next opened.
- **Predictions are not.** If the app never ran on a given day, no
  prediction was ever stored for it, and Day+0 for that date can never be
  scored — nor the Day+3/Day+7 chains that pass through it. Gaps are
  **permanent holes** in the accuracy record.

That asymmetry is the real cost of standalone mode, and it must be
surfaced honestly rather than hidden: the accuracy screen should show
coverage ("22 of the last 30 days verified"), not just percentages
computed over whatever happens to exist. A 90% accuracy figure drawn from
six scattered days is not the same claim as one drawn from thirty, and the
UI should not let those look identical.

Connected mode has no gaps, because the server runs whether or not anyone
opens the app — another concrete reason it earns its place rather than
being a hedge.

---

## 19. Spoken forecast — audio output · **Planned**

### The insight that makes this cheap and good

The obvious approach — run text-to-speech over the forecast we already
write — produces something bad, and it's worth saying why before anyone
builds it. The current narrative is written *to be read*: AFD-style
`.SECTION...` headers, `"23 km/h (12 kt) from the SE"`, temperatures given
twice in two units, markdown structure. Read aloud, that is close to
unlistenable.

So the right move is not "TTS the forecast." It is **generate a spoken-form
script as another audience voice** (item 14's mechanism, same single LLM
call, no extra request), then speak *that*. A `spoken` voice would drop
section markers, give each number once in one unit, use sentence rhythm
instead of bullet density, and run maybe 45-60 seconds. Item 14 already
provides the machinery; audio is one more `focus_hint`.

That also means the marginal LLM cost of audio is **zero** — the script
comes back in the call already being made.

### Two ways to actually produce sound

| | On-device TTS | Hosted TTS API |
|---|---|---|
| Cost | Free | ~$15/1M chars → ~$0.015/forecast, ~$11/yr at 2/day |
| Network | None — works offline | Required |
| Latency | Instant | A round trip |
| Quality | Serviceable, clearly synthetic | Natural, and improving fast |
| Effort | `flutter_tts`, small | HTTP + audio file caching |

**Start with on-device.** It is free, offline, instant, and needs no key —
which matters because it works even when the user hasn't configured an LLM
provider yet, and it keeps working on a cached forecast with no signal.
Hosted TTS is a quality upgrade to offer later as an opt-in, priced
visibly like every other API cost in the app (item 16's spend controls).

If hosted TTS is added: generate once per forecast, cache the audio file
alongside the entry, never regenerate on replay. Audio is the one output
here that is expensive to recompute and trivial to store.

### Why this matters more than "nice to have"

This project exists for places underserved by professional meteorology.
A spoken forecast is a genuine accessibility feature for low-literacy
users and for visually impaired users — and it is usable hands-free while
driving, farming, or on the water, which is exactly when a weather
forecast is most actionable. It reaches people a text app structurally
cannot.

### Open questions

- Does the spoken script belong in the committed JSON entry (so the
  open-source pipeline could serve audio from the website too), or is it
  app-only? Committing it costs a few hundred bytes a day and keeps both
  surfaces consistent — probably worth it.
- Language. On-device TTS covers many languages, but the forecast text is
  currently English-only. Multilingual output is a much larger question
  that this item should not quietly smuggle in.

---

## 20. Historical backfill for cold start — measured, with a real catch · **Planned**

### The problem it solves

Accuracy statistics need roughly 10 verified checks per model per lead time
before they mean anything, and a weekly review needs several weeks. For this
deployment that was a one-off phase. For the **app it is the permanent
default**: every new user in every new location starts at zero, so at any
moment most users are inside the useless window — and the accuracy screen is
supposed to be the product's differentiator.

### What was confirmed

**Open-Meteo archives past FORECASTS, not just past observations**
(`historical-forecast-api.open-meteo.com`), using the same
`{variable}_{model}` field naming the existing extractor already parses.
Verified live: an hourly multi-model request for a past date returns 24 hours
across all five models, in exactly the shape
`extract_day0_predictions_from_hourly` expects.

So a brand-new location *can* arrive with a verified track record on day one
instead of after months.

**Not available:** lead-time-specific archived forecasts. `previous_dayN`
variables were rejected by the API, so backfill covers **Day+0 only** —
Day+3/Day+7 still have to accumulate in real time. Worth another look; the
feature may exist under different naming or for hourly variables only.

### The catch, measured against this deployment's own record

Backfilling the 8 days we have real recorded predictions for, and scoring both
against the same actuals:

| | Live (what the pipeline actually ran) | Backfilled |
|---|---|---|
| gfs_seamless | 5/8 (62%) | 6/8 (75%) |
| ecmwf_ifs025 | 4/8 (50%) | 6/8 (75%) |
| icon_seamless | 5/8 (62%) | 6/8 (75%) |
| ukmo_seamless | 6/8 (75%) | 6/8 (75%) |
| best_match | 7/8 (88%) | 7/8 (88%) |
| **all models** | **27/40 (68%)** | **31/40 (78%)** |

**Backfilled skill reads ~10 percentage points optimistic.** Temperatures
match almost exactly (mean difference -0.12C) but rain calls diverge 16% of
the time and onset timing agrees only 4/14 — the signature of a *fresher model
run*. The morning pipeline runs at 03:07 UTC on the previous day's 18z cycle;
the archive stores the most recent run for that date, which is later and
genuinely better informed. That gap cannot be closed without abandoning the
morning delivery slot, since the ~8h model-availability floor is hard (item 1).

Caveat on the number itself: n=40 checks. The *direction* is solid — four of
five models improved, none got worse — but the exact magnitude needs more days
before being quoted as fact.

### Why this cannot simply be mixed in

A user who backfills at 78% and then accumulates live data at 68% would watch
their track record decay for purely methodological reasons. Worse, the
`rain_pct_trend` detector (item shipped earlier) would read that as a genuine
"declining" trend, and the system prompt explicitly instructs the LLM to name a
declining trend and lower its confidence. The result would be a **manufactured
false signal**, which is precisely the failure this project's honesty rules
exist to prevent.

### Design, if built

Backfilled checks are kept in a **separate bucket**, never merged into
`all_time_*` or the rolling windows:

- surfaced as a clearly-labelled *baseline*, with the methodology difference
  stated ("uses fresher model runs than this deployment can, and reads
  optimistic by roughly ten points")
- never fed to `compute_rain_pct_trend`, so the backfill/live boundary cannot
  masquerade as a trend
- live data takes precedence as it accumulates; the baseline is context, not
  a substitute
- the same treatment serves a new open-source fork, which has an identical
  cold start

Open question worth settling before building: is a systematically optimistic
baseline more useful than an honest "not enough data yet"? It is genuinely
arguable either way, and this project's usual answer is to prefer the honest
gap.

---

## Completed

- Multi-model fetch + synthesis pipeline, git-as-database, GitHub Pages
- Deterministic lead-time verification and rolling skill tracking
- KMD bulletin PDF extraction (confirmed text-based; no OCR needed)
- Weekly model-deprecation + repo-staleness health check
- Apps Script email delivery with retry against scheduler jitter
- Review fixes: all-time double-count guard, archive backfill, LLM retry
- Multi-station ground AQI with deterministic range/worst-station + staleness detection
- Second daily forecast run (evening refresh, web-only, morning predictions preserved)

---

## 21. Scoring the local met service as a model · **Partly shipped**

The national met service is the one forecast source with local knowledge no
global model has, and until now this pipeline consumed its bulletin as
narrative context without ever asking whether it was right. It is now
scored alongside GFS/ECMWF/ICON/UKMO, at zero marginal cost.

### Why it needs no LLM call

KMD's prose looks like free text but is a **controlled vocabulary the
bulletin itself defines**, in a glossary on its last page: Light/Moderate/
Heavy are mm bands, Few/Several/Most places are area bands, Possible/
Chance of/Likely/Expected are probability bands. Decoding a documented
encoding is not the same problem as interpreting natural language, which
is what makes a deterministic parser defensible rather than brittle. The
per-county table also carries numeric max/min temperatures, so temperature
never has to be inferred from words at all.

The structured prediction comes out of the same bulletin fetch the
narrative already required, so met-service verification adds no HTTP
request and no LLM call.

### Why the DAILY bulletin, not the weekly one

The repo originally fetched KMD's weekly 7-day forecast. The daily
bulletin is better on three independent counts, all verified against live
documents:

1. **Lead time.** The daily is issued ~3pm for 9pm-to-9pm the next day, so
   it is a genuine Day+0 prediction comparable to the models. A weekly
   bulletin's entry for a Thursday is a four-day-old forecast; scoring it
   against models' fresh same-morning runs would understate the met service
   for reasons that have nothing to do with skill.
2. **Extractability.** The weekly PDF is periodically published as scanned
   images — 18-24 Aug 2026 had 0 extractable characters against ~8,600 in
   each of the five preceding weeks.
3. **Numbers.** Only the daily carries the per-county temperature table.

### Deliberate non-decisions

The parser does **not** multiply KMD's probability term by its area term to
get a point-probability. "Rain expected over few places" is high confidence
of rain somewhere in under a third of a county, which is not the chance of
rain at one airport, and no honest arithmetic turns one into the other. The
rain cutoff sits at KMD's own "more probable than not" boundary and the
area term is recorded separately. If that rule is wrong, the verification
record will show it — putting the met service through the same accuracy
loop as everything else is precisely what makes that question answerable
rather than arguable.

### Generalising to ~200 other met services

Written up as a procedure in
[MET_SERVICE_INTEGRATION.md](MET_SERVICE_INTEGRATION.md), separating what
transfers (the controlled-vocabulary insight, picking products by lead time,
anchoring on the bulletin's own validity date, storing the extract rather
than the document, sparse fields as absent) from what is only ever Kenya
(the scraping shape, the table layout, the area key, the exact rain cutoff).

### What has not shipped

~~**The 5-day bulletin, which would give Day+3.**~~ **Shipped.** KMD publishes a five-day
forecast designed to work alongside the daily one, structured as a
per-county x per-day grid (max/min plus morning/afternoon/night for each
of five dates) — confirmed extractable, 82 tables, ~20,000 characters. A
forecast issued on day D covering D+2..D+6 supplies exactly the Day+3
prediction this project already tracks. Only the daily (Day+0) is wired up
so far; Day+7 has no met-service equivalent, since the weekly bulletin is
the unreliable one.

Also unshipped: no fork other than Kenya has a parser. The catalog in item
11 is where that generalises — and the controlled-vocabulary insight may
well transfer, since WMO-influenced met services tend to publish similar
glossaries.


---

## 22. The Synoptic Overview has no synoptic data · **Planned**

Reported from a live forecast: the Detailed Discussion had "lost the big
picture — fronts/highs/lows at the much larger scale", the kind of sentence
a reader expects from an AFD ("a low pressure system is approaching from the
west, currently over X, and will...").

**This is a data problem, not a prompt problem**, and the numbers are stark.
`region_points` for the live Kisumu deployment spans:

```
lat -1.06..0.06  (1.12 deg)     lon 34.29..34.78  (0.49 deg)
roughly 125 km x 55 km
```

Synoptic features — highs, lows, the ITCZ, tropical systems — have
wavelengths of **1,000–4,000 km**. A 125 km box fits entirely *inside* a
single system's pressure gradient. There is no big picture in that data to
find, so the model has been describing a mesoscale gradient under a heading
that promises a synoptic one. Asking it to do better would be asking it to
invent.

### The fix is one API call

Measured 2026-08-19: a nine-point ring at ±12° (~2,600 km) around Kisumu,
`pressure_msl_mean`, 3 days, `best_match`:

**HTTP 200 in 1.16 s, 3,133 bytes, one request.**

| Point | MSLP day 0/1/2 |
|---|---|
| centre | 1015.9 / 1015.2 / 1014.4 |
| N | 1010.9 / 1011.4 / 1013.4 |
| **NE** | **1006.2 / 1006.3 / 1005.9** |
| E | 1016.0 / 1015.7 / 1015.4 |
| SE | 1018.6 / 1018.4 / 1018.0 |
| **S** | **1020.4 / 1020.0 / 1019.5** |
| SW | 1016.9 / 1016.2 / 1015.5 |
| **W** | **1014.6 / 1013.0 / 1012.7** |
| NW | 1013.1 / 1012.1 / 1013.1 |

A 14 hPa gradient across the domain, a genuine low to the NE, a high to the
S, and pressure falling steadily to the W — real synoptic structure, and
exactly the material the missing sentence is made of.

### Design notes

- Keep it a **separate** `synoptic_points` config block, not an enlargement
  of `region_points`. The existing near-field points feed the local
  gradient/convection reasoning and should stay tight; conflating the two
  scales would degrade both.
- Derive the descriptive terms **in code**, per the project's standing rule:
  which quadrant holds the lowest/highest MSLP, the gradient magnitude, and
  the 24/48/72 h tendency per point. Hand the LLM labels, not nine raw
  arrays to subtract in its head — this is precisely the mistake the
  day-over-day comparison already had to be rescued from (see item 15).
- The ring is location-agnostic: offsets in degrees around the primary
  point, so a fork gets it with no extra configuration.
- Honesty limit stands. Point pressure at 12° spacing supports "lower
  pressure lies to the northeast and is deepening"; it does not support a
  named storm centre with a track. README's "no true storm-center or track
  forecasting" limitation should be **narrowed**, not deleted.

---

## 23. Day-over-day comparison is over-applied · **Shipped**

Reported from a live forecast: *"with rain expected again today, as it
rained yesterday too."* Circular, and it appeared more than once in the
issuance.

The instruction to open with the comparison (item 15) is doing its job too
enthusiastically. Two separate faults:

1. **Repetition across sections.** The comparison belongs in the Overview
   and should not resurface in Today's Forecast. The prompt never says
   "once".
2. **Belabouring a non-difference.** When `rain_contrast` says conditions
   match yesterday, the honest rendering is a brief clause or nothing at
   all — not a sentence constructed to sound informative about sameness.
   The prompt already contains exactly this instruction for temperature
   ("if high_label is 'about the same', say the day feels much like
   yesterday; do not manufacture a difference"), and the same discipline
   simply was never extended to rain.

### Root cause, which was neither of the above

Traced to the actual run: `rain_contrast` came back as the literal string
`"rain expected again today, as it rained yesterday too"` — **written in
`comparison.py`, not by the LLM.** The prompt instructs the model to use
that field AS GIVEN, precisely so it cannot invent a difference the numbers
don't support, so it did exactly the right thing with a badly-worded label.

The lesson generalises beyond this field: any pre-computed label the prompt
says to use verbatim is a **user-facing sentence fragment**, not an internal
enum, and has to be written as prose. The first version read like a
description of the data ("rain expected again today, as it rained yesterday
too" states one fact twice), which is exactly what a label written for a
developer looks like when a reader encounters it.

Fixed in both implementations, held in step by the shared vectors:

| Case | Before | After |
|---|---|---|
| wet → dry | drier than yesterday — yesterday saw rain, today is not expected to | drier than yesterday, which saw rain |
| dry → wet | wetter than yesterday — yesterday was dry, rain is expected today | wetter than yesterday, which stayed dry |
| wet → wet | rain expected again today, as it rained yesterday too | another wet day, like yesterday |
| dry → dry | dry again today, as it was dry yesterday | dry again, like yesterday |

The prompt now also requires one flowing sentence rather than three labels
bolted together, collapses them when two or three say nothing has changed,
forbids repeating "yesterday" more than once, and states the comparison in
the Overview only.

Worth revisiting alongside the "voices" work (item 14), since a terser voice
will want this shorter still and a chattier one may want it differently — but
the circularity itself is fixed.

---

## 24. Published data feed for apps — and the limits of remote config · **Planned**

Two questions, and they deserve different answers.

### The feed: yes, and it is nearly free

The pipeline already commits `docs/` and GitHub Pages already serves it. A
machine-readable `docs/api/latest.json` alongside the HTML costs one more
file write and no infrastructure. An app fetches a few hundred bytes from a
CDN; there is no backend, no account, no per-user anything.

Scope it to what genuinely cannot be computed on-device:

- **Local met-service predictions.** The motivating case — PDF *table*
  extraction has no Dart equivalent, and a scraper must be fixable with a
  git push rather than an app-store release. See APP_ARCHITECTURE.md.
- Later, plausibly: a shared observation cache, so twenty users in one town
  don't each re-fetch the same archive.

Everything else the app already computes itself, and should keep computing
itself — a feed that supplied *forecasts* rather than *inputs* would quietly
turn the app into a thin client and give up the standalone property.

### Remote configuration: useful, and the trap is auditability

The instinct is sound and comes from a real event. Open-Meteo's
`windgusts_10m` alias silently returns all-nulls for `ecmwf_ifs025`; the
current name is `wind_gusts_10m`. A deployed app pinned to the old spelling
loses one model's wind with no error — and waits for an app-store release.

But a config that can change behaviour remotely collides with this
project's central claim: *every published number is recomputable from the
committed record*. If the same app version can produce different forecasts
on different days because a remote file changed, a stored forecast is no
longer reproducible — and the accuracy record silently spans two different
systems.

Constraints that keep it honest, all of which should hold before any remote
config ships:

1. **Data only, never logic.** Variable names, endpoints, model ids,
   thresholds. Never anything evaluated. (Also what app-store policy
   permits: downloading declarative data is fine, executable code is not.)
2. **Version-stamped into every stored forecast.** A record must say which
   config produced it, or the audit trail is broken. This is the
   non-negotiable one.
3. **Defaults must be built in and sufficient.** An install that can never
   reach the feed produces forecasts, not errors.
4. **Signed or integrity-checked.** A trusted feed reaching every install is
   a supply-chain surface, and saying so out loud is cheaper than
   discovering it.

### The stronger first line of defence is not config at all

Both breakages this project actually suffered were fixed by making the
parser **tolerant**, not by making it configurable:

- Open-Meteo's gust rename: `pick_series()` tries both spellings and skips
  an all-null series. An app shipping both names was never exposed.
- KMD's county row shifting columns between consecutive daily issues: read
  by *kind* (numbers are temperatures, prose is a period) rather than by
  position.

Tolerant parsing needs no network, no trust, no version stamp, and costs
nothing in auditability. Remote config should therefore be the narrow second
line — reserved for what tolerance genuinely cannot absorb, such as an
endpoint moving or a model being retired outright — rather than the first
reach.

---

## 25. Detect absent data, don't just survive it · **Planned**

The missing third leg of the resilience argument in item 24, and the one
that would have mattered most.

### What actually happened

`ecmwf_ifs025` returned no Day+0 wind for the entire life of this
deployment. Every layer behaved *correctly*: Open-Meteo served a
correctly-named all-null array, the extractor recorded `wind_kmh=None`,
`score_prediction` declined to score a None, and the rolling stats
excluded it. Absence propagated cleanly as absence — exactly as designed,
which is why nothing raised.

It surfaced only when the weekly review aggregated wind error per model and
one model had a dash where four had numbers. That took months, and took
building an unrelated feature.

### The gap

Tolerance (item 24) keeps the system *running* through upstream change.
Nothing currently makes it *visible*. Those are different properties, and
the second is what turns a months-long silent gap into a one-day one.

A coverage check is cheap because the information is already there — the
extractor knows a series came back empty:

- Per (model, variable, lead time), did today's fetch yield a value?
- Flag a variable that was present historically and is now absent for N
  consecutive runs. That is the signature of an upstream rename or a
  retired model, and is distinct from a variable a model has *never*
  published (ECMWF gusts under the legacy alias, UV for ICON/UKMO), which
  is a permanent property and should be recorded once, not alerted on
  daily.
- `health_check.py` is the natural home; it already watches model
  deprecation and repo staleness, and already has somewhere to report.

### Why it matters more for the app than the server

On the server an upstream change is a git push. In an app it is an
app-store release — so knowing *quickly* is worth more, and the app can
also degrade honestly in the meantime by naming the gap on its accuracy
screen rather than showing a silently narrower model set.

This also reframes item 24's residual risk. The question is not only "can
we push a fix without an app release", but "how long before anyone knows a
fix is needed". Detection shortens the second, and is far cheaper and safer
than remote configuration, because it changes no behaviour at all — it only
reports.
