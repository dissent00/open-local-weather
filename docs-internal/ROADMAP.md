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

### Three candidate paths (as raised) — none chosen yet, needs more data first

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
   and doesn't touch the run schedule or add LLM-side estimation.

### Before picking one: gather more real mornings

Two data points (both showing the same 1-of-3 pattern) is suggestive, not
proof of a consistent daily pattern. Worth watching for a couple of weeks
— does it recover by 07:00 EAT every day, or is it irregular? Does the
`measured_at: null` case (distinct from genuine staleness) happen often
enough to matter on its own? This item is explicitly "review, then decide"
— see the weekly check-in cadence already agreed for this project.

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

---

## 8. Multi-provider LLM support · **Planned**

`llm/provider.LLMProvider` exists precisely for this; nothing else needs to
change. Each new provider needs its own JSON-schema adapter mirroring
`llm/schema.to_gemini_schema()` (which inlines `$ref`/`anyOf`, since Gemini's
schema dialect supports neither).

Candidates: Groq, Cerebras, OpenRouter. Value is resilience — a provider
outage or a model deprecation currently stops the forecast. The weekly
health check gives early warning of deprecation; a fallback provider would
give actual continuity.

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

## 11. Met-office catalog + location setup script — toward "country + coordinates in, config out" · **Planned**

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

### What this would actually be

A structured setup document — likely `docs-internal/FORK_SETUP_BRIEF.md`
or similar, sibling to the existing handoff brief — written to be
LLM-provider-agnostic ("any LLM," per the ask) rather than assuming
Claude-specific tooling. Concretely: ask for country + city/lat-long,
derive what item 11's tooling can derive, consult the met-office catalog
for a bulletin URL, and if no fetcher exists yet for that country, walk
through inspecting the real site and writing one — then GitHub secrets,
Apps Script mailer deployment, GitHub Pages setup, the same sequence this
project's own `mailer/README.md` / `ops/README.md` / main `README.md`
already document for a human doing it manually, restructured as
instructions an agent can execute rather than a person reading prose.

### Honest risk

**Realistic scope depends entirely on what tools the LLM instance actually
has** — a chat-only model with no code execution or browsing can narrate
instructions for a human to run by hand; an agentic coding assistant with
shell/git/web access (like the one that wrote this line) can do the whole
setup directly, the same way this session has been operating on the live
Kisumu deployment all along. The brief should be written to degrade
gracefully across that range, not assume the most-capable case.

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
