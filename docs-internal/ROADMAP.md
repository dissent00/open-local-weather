# Roadmap

Ordered roughly by value-per-effort. Each item states the problem first,
because several of these look obvious until you hit the actual constraint.

Status legend: **Next** · **Planned** · **Partly shipped** · **Shipped** ·
**Deferred** · **Done**

Several items here need work in the Ensemble app, which is a separate private
repository. That side of the ledger lives in one place — the "Owed from
open-local-weather" table at the end of `ROADMAP.md` there — so it cannot
drift out of step with a copy kept here. Add to that table when an item
raised here lands app work. Shared forecast logic is a different question and
follows `spec/README.md`.

---

## Working order, as of 2026-08-31

**The forward-fetch experiment was answered 2026-09-03 — neither hypothesis,
and the confound is unresolved.** See item 53, "THE ANSWER". One open
decision sits there: whether to spend a degraded forecast reverting the order
to prove causation, or leave it and add the per-request diagnostic instead.
Nothing is running in production awaiting an answer.

A snapshot, not a contract, and dated because it will go stale. Numbered
items keep their own status; this only says what to pick up next and why.
Where an item is a decision rather than a build, it is marked so — those are
cheap and they gate other work, which is why they sit high.

1. ~~**53.4 — a degraded run says it is degraded.**~~ **Shipped
   2026-08-31.** Record, page and a weekly red job. The app half is owed in
   the Ensemble repo.
2. ~~**57 — baselines in the ledger.**~~ **Done server-side 2026-08-31**,
   including the backfill and the review gate, with 58's storage half
   alongside it as planned. Only the app's own accuracy screen is left, and
   it is wiring rather than arithmetic — see the Ensemble repo's owed table.
   The headline: at Day+0 only one of five models demonstrably beats
   repeating yesterday's weather.
3. **45 — which source is "what actually happened".** **The decision is
   made** (2026-09-03): declared confidence bands rather than a learned
   weighting, graded rather than a strict chain, and the LLM reads sources
   but never adjudicates them. What remains is a BUILD, and its first step is
   trap 2's provenance stamp — which item 44 needs too, and which item 63
   cannot ship without. Do not start the precedence machinery: the item's own
   sequencing says stamp provenance, store the extra readings unscored, and
   let divergence accumulate before deciding what it earns.
4. ~~**27 — the harness for judging model changes.**~~ **The blocking half
   shipped 2026-09-01** — `olw replay` / `olw replay-diff`, which is what
   items 58, 59, 61 and 57's remaining half were actually waiting on. The
   A/B-by-alternating-days half is still open and blocks nothing.

   Item 48's pass and 53.3's rule 7 are both unvalidated for
   side effects — not wrong, unmeasured — and items 58 and 59 are larger
   prompt changes than either.
5. **58 — score the probability.** Additive to the boolean, so the existing
   series stays comparable. The incentive argument is the real one: a proper
   scoring rule pays for the honesty that rule 7 currently has to ask for in
   English.
6. **59 — split judgment from prose.** Needs 27 first. Large, and it doubles
   a surface pinned across two languages, so it is worth doing once.
7. **53.5, then 47 → 11 → 44.** The METAR nowcasting role, then observing
   stations everywhere — the biggest accuracy lever a reader controls, and
   where the product gets better rather than only more correct.
8. **20, then 60.** Backfill makes the archive queryable; analogues are what
   query it. Forty days is not enough for either.

**Item 2 still carries the status Next, and this list does not open with
it.** That marker predates item 53. Severe-weather alerting is the expensive
version of what 53.4 and 53.5 buy cheaply — a run that admits it is
degraded, and a METAR with a nowcasting role — so it belongs after them
rather than before. The status is left as it is rather than quietly
demoted; it is the operator's word to change.

Deliberately NOT next, listed because they read as though they might be:
54/55/56 (mailer and glossary, raised 2026-08-31 and none of them urgent),
41 (satellite — right answer, not the cheap one), 40 (AGENTS.md cleanup).

### Added 2026-09-04: 69 jumps this queue

Items 68, 69, 70 and 71 came out of one question — how to handle model
updates over years rather than weeks — and 69 is small, cheap, and blocks the
other three.

**69 stores the rendered user prompt per issuance.** Roughly 3.4 MB a year
against a `data/` directory currently totalling 652 KB. Without it, every
model question is a month-long forward experiment confounded by the weather;
with it, the same question is a paired backtest answered in an afternoon, for
any candidate, including one that does not exist yet.

The reason it jumps: **its cost grows the longer it waits.** Every day
without it is a day that can never be backtested, and the days already lost
cannot be recovered. Nothing else on this list has that property — item 45's
provenance stamp and item 65's lightning are as cheap to build in November as
today. This one is not.

70 (a prompt hash in `meta`) is one line and pairs with it. 68 and 71 are the
work 69 unblocks and can wait for a decision.

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

### First, a correction to the correction — KMD publishes CAP, 2026-09-03

The section below concluded that KMD has no feed and the plan must be HTML
scraping. **It tested the wrong namespace.** Every probe was for a WordPress
feed — `/feed/`, `/?feed=rss2`, `/wp-json/` — while the feed is an API
endpoint following the CAP convention instead:

```text
https://meteo.go.ke/api/cap/rss.xml
```

Verified live 2026-09-03: HTTP 200, `application/xml`, 4 KB,
`<copyright>public domain</copyright>`, five alert items each linking a full
CAP document. The documents are **CAP 1.2** (`urn:oasis:names:tc:emergency:
cap:1.2`), and they carry everything this item was going to have to infer:

| CAP field | What this item planned to do instead |
|---|---|
| `identifier` (`urn:oid:2.49.0.1.404...`) | invent an ID from a page slug |
| `severity` / `urgency` / `certainty` | judge severity with an LLM call |
| `onset` / `expires` | guess when an alert stopped applying |
| `area` polygons | bounding-box the Nyanza basin by hand |
| `status` / `msgType` / `scope` | tell a real alert from a test or a drill |

**The geo-filter is exact, and that was checked rather than assumed.** The
2026-05-07 Heavy Rainfall Advisory carries 34 county areas with real
polygons — Bungoma's alone has 2,297 points. A point-in-polygon test for the
configured primary point (-0.0917, 34.7680) returns `Kisumu` and nothing
else. So relevance becomes geometry rather than a text match on a place
name, which is the difference between "advisory mentions western Kenya" and
"this advisory covers the point we forecast for".

**Severity floors stop being a judgement call.** This item's open design
question — which advisories reach email and which stay on the site — is
answerable against `severity`, a standard enumeration, instead of an LLM's
opinion. That also removes the triage call from the common path: `severity`
and a polygon test are free, and the LLM is then only needed to write prose
for an alert already known to be relevant.

### The dormancy question, which is not answered

All five items in the feed are from April and May 2026. Nothing since, and
today is 2026-09-03 — roughly four months of silence.

Two readings, and the record cannot yet distinguish them:

1. KMD issues alerts episodically and has had nothing to warn about. The
   April-May clustering matches Kenya's long rains (March-May), which makes
   this entirely plausible.
2. The feed was populated once and abandoned.

**This is exactly the trap the source research warns about** — "an
operational catalogue entry with stale observations is not a usable current
source", and "discovery metadata is not data". The global source matrix
calls this feed *verified*, and the endpoint is; its contents are four
months old. Any registry this project adopts needs `last_live_observation`
as a required field rather than an optional one.

**A cheap test that answers itself. Shipped 2026-09-03.**
`check_cap_feed` runs in `check-health` weekly, against `cap_feed_url` in
`location.yaml`. Kisumu's short rains run roughly October to December; if the
feed is alive, alerts reappear then and the check says FRESH without anyone
remembering to look.

Five outcomes, and the line that matters is between QUIET and UNREACHABLE.
**A quiet feed does not fail the check** — warnings are episodic, a red job
through every dry season is a job nobody reads, and the honest report is the
age. **An unreachable feed does fail**, because this endpoint was found only
by probing a namespace nobody had tried, so a move would otherwise go
unnoticed until an alert was missed. EMPTY is kept separate from UNREACHABLE
so a working URL is never debugged by mistake.

First live run, 2026-09-03: `quiet`, newest alert 118 days old.

**Do not build the alert path on the assumption that it is live.** Build the
freshness probe first, let October decide, and keep GDACS below as the
independent cross-check either way — a single national feed going quiet is
precisely the failure a second source exists to cover.

### What this does NOT change

- **GDACS stays.** Two independent sources, so one going quiet does not
  blind the alert path.
- **Deduplication is still the whole ballgame**, and CAP makes it easier
  rather than unnecessary: `identifier` is a real key, and `msgType` of
  `Update` or `Cancel` distinguishes a genuine revision from a re-listing.
- **The push-vs-poll delivery design below is untouched.** CAP changes what
  is fetched and how relevance is decided; it says nothing about how an
  alert reaches a person.

### The original finding, kept because it was right about what it tested

**Kenya Met publishes no WordPress RSS feed.** Verified directly:

| Probe | Result |
|---|---|
| `/feed/`, `/rss`, `/weather-warnings/feed/` | 404 |
| `/?feed=rss2` | HTTP 200 but returns ordinary HTML — the param is ignored |
| `/wp-json/wp/v2/posts` | 404 (REST API disabled) |
| `<link rel="alternate" type="application/rss+xml">` | absent from page head |

So the plan cannot be "poll the WordPress feed". It was concluded here that
the alternative was **HTML scraping with change detection**, mirroring
`fetch/bulletin/kenya_kmd.py` — superseded by the CAP feed above, and kept
as the fallback if that feed proves dormant. `https://meteo.go.ke/weather-
warnings/` lists warning posts newest-first in page order, so the scraper
remains a known-viable plan B rather than the plan.

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

## 4. Ground AQI staleness — review and decide on a fix · **Partly shipped**

**Shipped 2026-08-26 — the reporting half.** When every station is stale,
`summarize_ground_aqi` returned null, the prompt said "Not applicable", and
the LLM improvised: 2026-08-25 gave no station numbers at all, 2026-08-26
listed all three and then called them stale. Neither was wrong, and that was
the problem — a different contract each morning. `last_known_ground_aqi` now
quotes the most recent real reading with its station, its value and its age,
and the prompt states that alongside the CAMS estimate so a reader can see
which is which.

That does not close this item. It makes the staleness *legible*; it does not
make the sensors any fresher, and the underlying question — whether three
stations that routinely go hours stale are the right ground truth at all —
is still open. See also the pm25/pm10 mislabelling noted in item 43, and
item 33, which stopped an afternoon re-fetch replacing the morning's real
readings with nulls — so a station that reports once a day now keeps that
reading for the whole day.

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


### The discovery mechanism, named 2026-09-03

This item asked how someone in an arbitrary place finds their sources
without editing YAML, and had no answer for where the catalogue comes from.
The source research supplies it, and `docs-internal/GLOBAL_SOURCE_REGISTRY_
REFERENCE.md` plus `OPEN_LOCAL_WEATHER_GLOBAL_SOURCE_MATRIX_v1.0.xlsx` are
the first pass at it.

- **Surface stations**: WMO **OSCAR/Surface** for discovery, **WIS2** as the
  delivery mechanism behind it, METAR and national adapters where they
  exist. Identity reconciliation matters: WIGOS ID, WMO number and ICAO can
  all name the same asset, and treating them as three would manufacture
  corroboration that does not exist.
- **Radar**: the WMO **Weather Radar Database** for discovery, regional
  providers (OPERA in Europe, NEXRAD in the US) for actual data. Optional by
  geography — see item 63, where Kisumu turns out to have none.
- **Satellite**: selection is derivable from coordinates alone, since which
  geostationary operator covers a point is a geometry problem. See item 41.
- **Warnings**: **CAP** where a national service publishes it, which item 2
  now shows Kenya does.

**Three registries, not one**, per the reference: a COUNTRY registry (what a
member state publishes), an ASSET registry (individual stations, radars,
satellites) and a PROVIDER registry (who serves the data, and under what
terms). Collapsing them is what makes "add a source" ambiguous — the same
asset can reach you through several providers, and the same provider serves
many countries.

**The matrix is a reference, not a work item.** 193 members, Kenya scored P1
at 87.3 with a "very high" native adapter value. It is incomplete by its own
account and it will go stale. Its value is not the scores; it is that the
fork-portability question — how someone in Peru configures this — now has
a document to answer from rather than a shrug.

**What it does NOT license: ingesting broadly.** Item 45's OR is monotonic
and its divergence numbers are days old. Adding sources before those numbers
mean something would move every published figure for instrumentation
reasons, invisibly. Discovery is the cheap half; deciding what a source is
worth is the half this project exists to do carefully.

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

The Dart port shipped: `app/olw_core/lib/src/review.dart`, held to identical
output by `spec/vectors/weekly_review.json`. Storage-agnostic by design — it
takes a predictions lookup rather than a log-entry type, because on a phone
the history is a database table, not JSON files on disk.

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

## 22. The Synoptic Overview has no synoptic data · **Shipped**

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

## 25. Detect absent data, don't just survive it · **Shipped**

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

### The same gap exists in operations, and one instance is live now

The daily workflow fires from two sources: four backup `cron` slots, and a
`workflow_dispatch` call from `ops/trigger_workflow.sh` on an external
server, authenticated with a GitHub PAT. The dispatch path exists precisely
because GitHub's scheduler proved unreliable enough to miss issuances.

**GitHub does email the token owner before a PAT expires**, so the most
likely single cause is covered — confirmed in practice, one such notice
arrived while this was being written. That notification is worth keeping in
view, but it is narrower than the problem:

- It reaches the **token owner**, who on a fork may not be whoever operates
  the trigger host.
- It covers **expiry only**. A revoked token, a deleted token file, a
  decommissioned server, a broken cron entry, or a host that simply stopped
  resolving DNS all produce the same silence with no email at all.
- It arrives **before** expiry, so acting on it is a human step that can be
  missed; nothing afterwards confirms the trigger resumed.

In every one of those cases the failure mode below still applies:

1. `workflow_dispatch` silently stops firing.
2. The cron slots keep firing, unreliably, exactly as before the dispatch
   trigger was added.
3. Commits therefore keep appearing, so `check_repo_staleness` — which only
   trips after **50 days** with no commit at all — never fires.
4. The system quietly reverts to the unreliable mode that caused the
   original problem, and the first symptom is a late or missing forecast.

Tolerance without vigilance, in the operational layer rather than the data
layer. The system survives the failure and never mentions it.

Cheap to detect, and the data already exists in git: **which trigger
produced each run**. `github.event_name` distinguishes `schedule` from
`workflow_dispatch`, so recording it in the log entry's `meta` makes "the
external trigger hasn't fired in N days" a query rather than an
investigation. Worth doing at the same time as the per-variable coverage
check, since it is the same idea pointed at the pipeline itself.

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

---

## 26. Hard cap on LLM calls — required in BOTH surfaces · **Shipped**

**Shipped in the pipeline** (`spend.py`, enforced at both LLM call sites,
configured by `max_llm_calls_per_24h` in `config/location.yaml`, default 10).

**Shipped in the app too**, later: `spend_store.dart`, enforced in
`ForecastRunner`, covered by `test/spend_cap_test.dart`. The ledger counts
HTTP requests rather than forecasts in both surfaces — a retry loop that
runs out of budget is stopped mid-flight, and the invariants are pinned
across the two implementations by the S1-S6 table in `spec/README.md`.

The design notes below stand as written; what follows describes the problem
as it was, and the resolution of the "wrinkle" is recorded at the end.

**There was no cap anywhere.** Nothing counted API calls, in the
pipeline or the app. The only limiter is `daily.yml`'s `already_done` skip
check, which exists to stop duplicate runs and is deliberately bypassed by
`force: true`.

Measured exposure as it stands:

| Path | Calls per day |
|---|---|
| Nominal (morning + evening refresh) | 2 |
| With retries (`MAX_ATTEMPTS = 4` per run) | up to 8 |
| `force: true` in a loop, or a misconfigured cron | **unbounded** |

The last row is the one that matters. A mistake in a crontab on a machine
nobody is watching can run up a bill against someone's key with nothing to
stop it, and the first signal would be the bill.

### Why this is a correctness requirement, not a setting

The app spends the **user's own money** through a key they supplied. An
application that can silently do that without limit is not a product with a
missing feature; it is one that should not ship. The same applies to a fork
running the pipeline on their own key.

### What "ironclad" has to mean

1. **Count API CALLS, not forecasts.** Retries are real money — a single
   forecast can cost four calls. A cap on successful runs is not a cap.
2. **Record the intent to call BEFORE making it, not the result after.**
   This is the crux. Incrementing after a call returns means a crash,
   timeout, or kill between send and record loses the count, and the cap
   silently permits an overrun — exactly when things are already going
   wrong. Write first, call second.
3. **Fail closed.** If the ledger cannot be read or written, refuse to call.
   A spend guard that degrades to "allow" under failure is not a guard.
4. **Rolling 24 hours, not calendar day.** A midnight reset permits a full
   budget either side of it, so a "4 a day" cap allows 8 in a few hours.
5. **Durable across restarts.** In the pipeline, the ledger is committed
   like everything else. In the app, it belongs in the local database, not
   in memory.
6. **Manual runs are the user's decision**, so they may exceed the automatic
   budget — but never silently, and never by accident. The confirm dialog
   already built in Ensemble is that half.

### The wrinkle that turned out not to be one

This was first written up as a deliberate exception to **recompute, never
accumulate**. On inspection it is not an exception at all, and the
distinction is worth keeping straight because it decides how the ledger is
allowed to evolve.

That principle forbids storing *derived* numbers — accuracy percentages,
all-time counts — because a wrong one persists invisibly. It has never
forbidden storing raw records: the whole of `data/log/` is append-only and
never recomputed.

A spend ledger is raw data of exactly that kind. Each entry records one call
attempt; the 24-hour total is **recomputed from those entries on every
check** and never stored. The derived figure stays derived, as everywhere
else. A test asserts no total is persisted, so a later "optimisation" that
caches one fails rather than silently drifting from the entries it claims to
summarise.

The genuine constraint is different, and is the same asymmetry that forced
storing the met bulletin: weather actuals can be re-fetched, but **a call
attempt cannot**. Miss the moment and it is gone — hence recording before
the call rather than after.

### Scope

Both surfaces, sharing the decision logic in `olw_core` so the app and the
pipeline cannot disagree about whether a call is permitted — with each
supplying its own storage. The pipeline additionally needs the cap to apply
to `force: true`, since that is the path with no ceiling today.

---

## 27. A harness for judging model changes · **Partly shipped — replay, 2026-09-01**

Prompted by a concrete question: `gemini-3.7-flash` now exists, and the
pipeline is tuned on `gemini-3.6-flash`. Is the newer one better *for this*?
There is currently no way to answer that except by reading a few forecasts
and forming an impression.

That matters beyond one version bump. Model choice is simultaneously a
quality, latency and cost lever — the ~1-minute generation time comes largely
from `thinking_level: high` on a Flash model — and every one of those trades
is currently made on vibes.

### The good news: the data is already being recorded

`LogEntryMeta` already stores `llm_provider` and `llm_model` on every
committed forecast. So the record already knows which model wrote which
forecast, and every one of those forecasts already gets scored against
observations by the existing verification loop.

The harness is therefore mostly **analysis, not plumbing**: partition the
existing accuracy record by `meta.llm_model` the same way it is already
partitioned by weather model and lead time.

### Design

- **A/B by alternating days rather than running both.** Running two models
  per forecast doubles the cost and doubles the quota pressure. Alternating
  costs nothing extra and, given the verification loop needs weeks to say
  anything anyway, is no slower in practice.
- **Reuse the existing gates.** `review.py` already refuses to rank on thin
  evidence and requires a gap above the sampling-noise floor. Comparing two
  LLM models is the same statistical problem as comparing GFS against ECMWF,
  and deserves the same refusal to over-claim. Do not build a second, laxer
  comparison path.
- **Separate what the LLM controls from what it does not.** Rain and
  temperature calls come from the deterministic extraction, identical
  whichever model narrates. What the model actually affects is the *blended*
  `today_properties` call and the narrative. Scoring the wrong half would
  produce a confident non-answer.
- **Record latency and token counts alongside**, since "slightly better and
  three times slower" is a real outcome and a legitimate reason to decline an
  upgrade.
- **A frozen-input replay mode** for prompt work specifically: feed a stored
  day's exact inputs to two models and diff the outputs. Cheap, immediate,
  and the right tool for "did this prompt edit help", as distinct from "is
  this model better", which only time can answer.

### The two halves are separable, and only one gates anything

Written down 2026-09-01, because it halves the work that four other items
were waiting on. "Is this MODEL better" needs weeks of scored forecasts
partitioned by `meta.llm_model`, and nothing can hurry it. "Did this PROMPT
EDIT move something I did not intend" is answerable in minutes. Items 58, 59,
61 and 57's remaining half all need the second and none needs the first.

**The replay half shipped 2026-09-01.** `replay.py`, `olw replay --out DIR`
and `olw replay-diff BEFORE AFTER`. Run it either side of a prompt change and
read the list.

**The frozen inputs are the committed prompt vectors**, deliberately rather
than a new corpus: `llm_user_prompt.json` and `llm_system_prompt.json`
already hold six complete input sets — fully populated, two evening
refreshes, cold start, no ground stations, no met service — and are already
updated by anyone who touches the prompt. A second corpus would be a second
thing to keep in step, and the one that rots is always the one nobody's test
suite reads.

**A diff separates the prose from the scored call**, which is item 27's
"separate what the LLM controls" note made concrete. `today_narrative`
changing is a judgement; `today_properties` changing means the blended call
this project scores and publishes has moved, and the command says so.
Latency travels with each result and is deliberately NOT diffed — it varies
run to run for reasons unrelated to the change, and a diff that reports noise
is a diff nobody reads.

**It spends real money** — one call per case, on the operator's key, counted
by the same cap as the forecast — so it prints the cost and does nothing
without `--yes`. There is no dry-run: a replay that does not call the model
is not a replay of anything.

**Two bugs of one shape were caught building it, both of which produced a
harness that looked fine.** Pairing the two vector files BY NAME never
matched, so every case silently got the first system prompt; then reading
`historical_logs` as the re-issue marker was wrong, because that is the
multi-day history and is present on ordinary runs too. Either way the two
refresh cases would have been replayed against a first-run system prompt.
A mismatched pair does not fail — it reports differences that are artefacts
of the harness rather than of the change under test, which is worse than a
harness that does not run. The first was caught by noticing every system
prompt came out the same length. Both are now guarded by a test that asserts
the pairings differ where the configuration differs.

**Still open here:** the A/B-by-alternating-days half, which is what answers
"is gemini-3.7-flash better for this". That one is analysis over
`meta.llm_model` and reuses `review.py`'s gates; it is not blocking anything.

### Why not just eyeball it

The project's entire argument is that forecast quality claims should be
measured rather than asserted. Choosing the model that writes those forecasts
by impression, while publishing an accuracy page about everything else, would
be the one unmeasured link in the chain.

---

## 28. A watcher for provider terms, pricing and limit changes · **Planned**

Prompted by a concrete problem: this project bakes claims about other
companies' products into its own interface, and those claims rot.

Within a single day, "roughly 20 free calls a day" went into the app's
onboarding as a dated fact and then had to be removed, because Google no
longer publishes free-tier figures at all and says limits depend on your
account tier. That was caught by chance while researching something else. The
next such change will not be.

### What actually depends on provider terms

More than it first appears:

- **Onboarding copy** — how to get a key, whether a free tier exists, what a
  key costs. Wrong instructions here are where a paying user gives up.
- **The default spend cap.** "10 a day is sensible headroom" is a judgement
  made against today's limits. If a provider halves its free allowance, that
  default silently becomes a way to exhaust someone's quota; if it raises
  them, the default becomes needlessly restrictive.
- **Which provider is recommended.** Google is recommended *because* it has
  a free tier. That recommendation is a claim with an expiry date.
- **The default model.** Deprecations already have a checker; pricing and
  rate-limit changes do not.
- **Whether a provider works at all.** Anthropic cannot be called from a
  browser today. If that changed, an accurate warning would become a false
  one.

### The precedent to build on

`health_check.py` already does a version of this: `check_model_deprecation`
fetches Gemini's deprecations page and asks an LLM whether a named model is
listed, with instructions to be conservative and report nothing rather than
guess when the page is ambiguous. That shape — fetch, ask, prefer a false
negative — is the right starting point.

### The hard part is materiality, not fetching

Diffing these pages naively is useless: they change constantly for reasons
that do not matter, and an alert that fires weekly on cosmetic edits gets
muted within a month. That failure is worse than no watcher, because it
produces the *appearance* of monitoring.

So the question asked has to be specific and answerable:

- Does a free tier still exist for this provider?
- Have the documented rate limits changed, in either direction?
- Has the key-creation flow changed enough that our written steps are now
  wrong?
- Is our recommended model still current, and still priced as assumed?

Store the last answer, alert only on a *changed answer* — never on changed
page text. That is the same distinction the coverage check draws between a
variable that was present and is now absent, and one that was never there.

### Getting the warning to users

Detection is half of it. The app cannot be updated instantly, so a provider
changing terms today would leave stale guidance on phones until a store
release — which is precisely the case item 24's published data feed exists
for. A notice carried in that feed and shown in-app would close the gap
without a release, and is a far better use of a remote channel than remote
configuration, because it changes what the user is TOLD rather than what the
app DOES.

### Deliberate non-goal

This does not attempt to read or interpret legal terms of service for
compliance purposes. It watches the operational facts this app states to its
users and depends on for its own defaults. Anything beyond that is a
question for a lawyer, not a cron job.

---

## 29. Pollen and allergen forecasts · **Planned**

Requested by an actual user, which puts it above several items above it.

### Feasible, free, and already half-fetched

Open-Meteo's air-quality API — the same endpoint this pipeline already calls
for PM2.5 and AQI — carries pollen: `alder_pollen`, `birch_pollen`,
`grass_pollen`, `mugwort_pollen`, `olive_pollen`, `ragweed_pollen`. No key, no
extra request if folded into the existing air-quality call.

### The catch, verified rather than assumed

**It is Europe-only.** Measured 2026-08-21:

| Location | Result |
|---|---|
| Frankfurt (50.11, 8.68) | 24/24 non-null — real values |
| **Kisumu (-0.09, 34.77)** | **0/24 non-null on every species** |

The data comes from the CAMS *European* air-quality model. So this feature
works for a large share of forks and **not for the reference deployment**,
which is an unusual shape for this project and worth stating plainly rather
than discovering after building it.

### The trap this walks straight into

A naive implementation would show Kisumu "grass pollen: 0" — which reads as
*no pollen today* when it means *nobody measured*. For someone choosing
whether to take an antihistamine before going out, that is not a cosmetic
difference.

This is the same unknown-versus-false distinction that runs through the whole
project: a model with no Day+7 data has `rain: null`, not `rain: false`,
because recording absence as a confident negative accrues fake accuracy. Here
the stakes are more direct, because a person acts on it.

So the requirements are:

- **All-null means absent, and absent means the section does not appear.**
  Not a zero, not a dash, not "low" — nothing, with a one-line explanation if
  the user goes looking. `pick_series` already refuses to latch onto an
  all-null array; the display layer needs the same discipline.
- **Never state a pollen level the data does not support**, in the narrative
  or in the stat grid. The prompt will need explicit instruction, because a
  model handed six zeros will happily write "pollen levels are low today".

### Beyond Europe

Worth investigating separately rather than blocking on:

- **Ground networks.** Several countries run pollen-count stations whose data
  is published; this is the same shape as the WAQI ground-AQI integration
  already built, including its staleness handling.
- **National met services.** Some publish pollen alongside the forecasts the
  met-service parser already reads — so for a fork whose service does, this
  may arrive through machinery that already exists.
- **Seasonality as honesty.** Where no measurement exists, saying "not
  measured here" is correct and finished. Inferring pollen from season and
  vegetation would be inventing data, which is the one thing this project
  will not do.

### Where it surfaces

Both surfaces, once available: a stat-grid entry and a sentence in the
narrative when levels are notable, on the site and in the app. It also fits
the "audience voices" work (item 14) unusually well — an allergy-focused
voice is a genuine use case, not a novelty, and is exactly the kind of
personalisation a single LLM call can produce for free.

---

## 30. Twilight times — the light people actually plan around · **Planned**

Sunrise and sunset now reach the forecast (item 31's sibling change), but they
are not the times most readers care about. On the lake, what matters is when
there is enough light to get back in — which is civil twilight, roughly the
period when the sun is within 6° below the horizon and you can still make out
a shoreline without a torch.

**Open-Meteo does not provide it.** Checked rather than assumed: the daily
endpoint offers `sunrise`, `sunset`, `daylight_duration` and
`sunshine_duration`, and rejects `civil_twilight_begin` outright with
`Cannot initialize ForecastVariableDaily from invalid String value`. So this
means computing solar position ourselves.

That is a well-defined calculation — the NOAA solar position algorithm, taking
latitude, longitude and date, and solving for the times the solar elevation
crosses −6°. It fits the project's "all arithmetic in code" rule exactly, and
it has to be **ported to Dart and vector-locked** like everything else in
`daypart`.

**Most of it is already written.** Item 39 shipped that algorithm in
`solar.py` and `solar.dart`, solving for −0.833° — the sun's upper limb at the
horizon. Civil twilight is the same routine with the altitude at −6°, so this
is now a parameter and two vector cases rather than forty lines. Read
`solar`'s docstring before starting: the accuracy table there is the answer to
"is this good enough at my latitude", and twilight at high latitude is
*harder* than sunrise, not easier, because the crossing is shallower still.

Two things to get right, both of which the sunrise/sunset work already hit:

- **It has no answer at high latitude for part of the year.** In polar summer
  the sun never gets 6° below the horizon and civil twilight never ends; in
  polar winter it may never begin. The function must return "no such time"
  rather than a number, and the display must omit the field rather than render
  a blank one. Note that `sun_times` does NOT do this — it matches
  Open-Meteo's convention of a 24-hour or zero-length day instead, because
  `daypart.classify_phase` reads that span. Twilight has no such consumer, so
  it is free to say "no such time", and should.
- **Do not approximate it as "sunset plus 25 minutes."** That is roughly right
  in Kisumu, where the sun sets nearly vertically, and badly wrong at 55°N
  where twilight can last over an hour. An approximation that holds at the one
  latitude we test is exactly the kind of thing that ships and is wrong
  everywhere else.

Worth doing after the app has a settled surface for showing it, since the
value is in the display rather than in the narrative.

---

## 31. Do not mention a sunrise that has already happened · **Planned**

A forecast issued at 18:15 has no business saying "sunrise at 06:40" as though
it were coming. The `daypart` work already gives the pipeline everything
needed to know this — the phase, and whether `now` is before or after each
event — and the statement handed to the model already gets it right (it says
"the sun rose at 06:40" after the fact, and names *tomorrow's* sunrise after
dark).

What is not yet handled is the **published stat block**, which renders
`Sunrise 06:40 / Sunset 18:47` identically at every hour of the day. By the
evening issuance the sunrise figure is a fact about a morning that is over.

The shape of the fix, once the display settles:

- Before sunrise: show today's sunrise and today's sunset.
- Between them: show sunset, and today's sunrise as past tense or not at all.
- After sunset: show **tomorrow's** sunrise, labelled as tomorrow's, and drop
  today's sunset.

This pairs naturally with the dusk mapping in `daypart` — the same phase
already decides what the narrative leads with, so the stat block should follow
it rather than keep its own rules. Two components deciding independently what
"sunrise" means is how they end up disagreeing on the same page.

Small, but the kind of detail that separates a forecast someone trusts from
one that reads as generated.

---

## 32. Storage keeps two issuances a day, the prompt now allows any number · **Shipped**

Making every run time-aware (items 30/31's sibling work) generalised the
*prompt* to any number of issuances a day. Storage did not follow, and the gap
is invisible until someone schedules a third run.

**What happens today**, pinned by `test_a_third_run_loses_the_middle_narrative`:

- `DailyLogEntry.narrative_markdown` holds the **latest** issuance.
- `morning_issuance` holds the **first**, written only if not already set.
- Everything between is overwritten and gone from the committed record.

With two runs a day nothing is lost, which is why this has never mattered. At
three or more, the middle issuances vanish — and they are exactly the ones a
reader would want when asking "what did it say at lunchtime?".

It also degrades the update itself. `_earlier_issuances` can only return what
is stored, so a third run is shown the second one's narrative and has no idea
the first ever existed. The instruction to say what has changed *since the
last issuance* still works; the ability to see the day's arc does not.

**What it should be:** a list of issuances on the entry, each with its time,
narrative, properties and the model cycle it used — with `morning_issuance`
becoming the first element rather than a special case. That shape also makes
the published archive page able to show a day as it actually unfolded, which
is a better artefact than a single final narrative.

**Care needed on migration.** Every committed entry in `data/log/` predates
this, so the reader must accept both shapes indefinitely — the archive is the
project's record and rewriting history to fit a new schema would destroy the
thing being preserved. The `morning_issuance` field already carries a comment
explaining that it was added after entries existed; the same discipline
applies.

**Not urgent.** Two runs a day is the current deployment and the common case
for forkers. This becomes real the moment someone wants four.

**Shipped 2026-08-28**, and it stopped being hypothetical first: `olw forecast`
(item 34b) makes a third issuance a cron line rather than a code change, and a
delayed scheduler slot can produce one unasked (item 49).

`DailyLogEntry.earlier_issuances` holds every issuance before the current one,
oldest first; the current one stays in the top-level fields, so there is never
a second copy of the latest narrative to keep consistent.
`MorningIssuanceSnapshot` is now `IssuanceSnapshot` — named for what it holds
rather than when. `morning_issuance` keeps being written, redundantly and
deliberately: `data/log/*.json` is the public archive and `publish/pages.py`
keys off it by name. One accessor, `issuance_log()`, reads both shapes and
appends the current issuance last; committed entries are never migrated, and
the docstring says why — an archive rewritten to look like it always had a
field is no longer a true account of what was stored.

**Two bugs came out of it, both in the prompt rather than the storage.**

The block listing what has already been published today returned ONE element,
`narrative_markdown` — so a third run saw the second issuance and had no idea
the first existed, though `morning_issuance` held it the whole time. That was
the half of this item that improves output rather than only the record.

And it read `entry.meta.generated_at`, which does not exist. `getattr` returned
None every time, so every earlier issuance reached the prompt as "earlier
today" rather than a clock time. Silent for as long as the field has existed.

**Timestamps needed care, found in review of the change itself.** An
issuance's time is `last_issued_at` — `refreshed_at` if the entry has been
re-issued, else `generated_at_utc`. The predecessor helper only ever captured
the day's FIRST issuance, where the two are equal; generalising it to any
issuance made that equivalence false, and a snapshot reading
`generated_at_utc` stamps a 22:00 update as 06:07 in both the archive and the
next run's prompt. Verified against all 18 committed entries, which now yield
correct per-issuance times through the accessor.

**Deferred, deliberately:** the published archive still renders exactly two
pages a day, `<date>.html` and `<date>-morning.html`, from `morning_issuance`.
Those URLs are published and permanent. Showing a day as it actually unfolded
is the follow-on, and it needs a URL scheme that can name an arbitrary
issuance without breaking the two that already exist.

---

## 33. A fresher null should not erase an older real reading · **Shipped**

Found while investigating the 2026-08-22 evening refresh. The morning run
captured three ground AQI stations with real values — Kisumu Airport 46,
Ochieng' Avenue **160**, Dunga Beach 27, all timestamped 00:00Z. The refresh
re-fetched at 11:00Z, got the same three stations back with **`aqi: null`**,
and stored those. The current entry now shows three nulls.

The refresh worked correctly: it fetched, the timestamps advanced, upstream
simply had no numeric AQI at that hour. The narrative fell back to CAMS model
data, exactly as designed.

**What is wrong is that a real measurement was replaced by an absence.** The
morning's 160 at Ochieng' Avenue — Unhealthy for Sensitive Groups, the single
most actionable number in that day's forecast — is now visible only inside
`morning_issuance`. Anyone reading the current entry, the published page, or a
future analysis of the stored record sees three nulls and no indication that a
station read 160 nine hours earlier.

This is not new and was not introduced by the time-awareness work; it has been
true for every refresh. It became visible because the values happened to be
interesting that day.

**The shape of the fix.** A later reading replaces an earlier one only when it
actually carries a value. Where the fresh fetch returns null, keep the stored
reading and its original timestamp, so `hours_old` and the existing `stale`
flag do their job — the machinery for "this reading is old" already exists and
is the honest way to present it. The narrative can then say the last real
reading was 160 at midnight and is now nine hours old, which is far more useful
than silence.

Worth applying the same rule anywhere a re-issue overwrites fetched data.
Sun times already work this way as of the fix in the same commit as this note,
and for the same reason: fresher is not better when the fresher value is
"unknown".

**Shipped.** `aqi.merge_ground_aqi` keeps a stored reading, with its original
timestamp, wherever the fresh fetch has no value for that station; a station
absent from the re-fetch is treated identically, since `fetch_ground_aqi_stations`
drops a station whose fetch failed and absence cannot be told apart from one.
The refresh merges before it builds the prompt, and the summary and last-known
reading are recomputed from the merged list, so the narrative quotes the kept
160 with its true age instead of reporting nothing.

Ported to `olw_core` as `mergeGroundAqi` and vector-locked (`aqi_merge.json`,
9 cases), ahead of the app consuming ground AQI: the rule is about which
reading survives a re-issue, and the app re-issues too. `generateForecast`'s
doc names it, so whoever wires the app's stations cannot miss it.

**Its sibling, fixed in the same pass: a deployment with no stations was told
about them anyway.** Both prompts rendered `GROUND AQI STATIONS: Unavailable —
no ground station reported data today` whether the stations were silent or
had never been configured, and the system prompt asked the model to note when
"none report". A fork with no WAQI token would report that failure every day,
for stations it never had. `build_system_prompt` and `build_user_prompt` now
take `ground_stations_configured` (Dart: `groundStationsConfigured`, and
`generateForecast` defaults it to false, since an app has none until someone
configures them): false drops every ground-station passage and all three
blocks, and the data-quality note says air quality comes from model (CAMS)
data alone. Absent instructions beat instructions that say "ignore this" —
the model cannot mention what it was never told about. Vector cases added on
both prompts.

**And the same for the local met service**, which had the same three states
collapsed into two. `LOCAL BULLETIN ():` with nothing under it is a fetch that
failed; a location with no service wired has not failed at anything, and the
prompt went on to demand the service be named EVERY TIME. Now:
*configured and answered* carries the bulletin; *configured and silent* still
says so explicitly, which is why the states are worth separating; *not
configured* drops the peer-model guidance, the naming rule and the block.

One deliberate asymmetry with the AQI case: the met service's absence is
still STATED once, because the model knows real met services for a real place
and silence would leave it free to attribute a forecast to one it never
consulted. Silence prevents a report of a failure; it does not prevent an
invention. `generateForecast` derives the flag from
`localBulletinSourceName` — a source with no name is not a source.

---

## 34. One forecast command that knows whether it is the day's first · **Shipped — two operator steps remain**

**Decided 2026-08-27: this splits in two, and only the first half is urgent.**

**34a — move the guard into the pipeline. SHIPPED.**
`run_daily_pipeline` now keeps whatever `model_predictions` the date already
holds, byte-for-byte, exactly as `HistoryStore.savePredictions` does in the
app — this run's own blend is not appended to a kept set either, since the
day's blend belongs to the run that made it first. `force` reaches the
narrative and nothing else. A run whose predictions are kept is handed the
KEPT ones for its prompt, so the narrative cannot describe values the record
does not contain.

**The rest of it, closed next:** a forced re-run kept the scored numbers but
still rewrote the day's own history — it built a brand-new entry, wiping
`morning_issuance` (the only copy of the morning's narrative), resetting
`meta.generated_at_utc`, and clearing `meta.refreshed_at`, which re-opened
`evening_refresh.yml`'s gate so the next refresh would snapshot the FORCED
narrative as that day's morning issuance. A run on a date that already has an
entry is now a later issuance whatever verb was typed: it is told so, it is
shown what has already been published, and it carries forward
`morning_issuance`, `generated_at_utc`, the verification block and
`yesterday_verification_summary`. Its verification notes — a placeholder by
design on a re-issue — no longer reach the historical row the morning run
scored.

Found and fixed alongside it: a re-issue was being shown the blend's own row
in all three prompt blocks that name models — the predictions block (which is
the stored Day+0 list, blend included), the track record, and the review
findings. Only `run_daily_pipeline` had ever been filtered, and only
`run_daily_pipeline` had a test asserting on the prompt text. Both paths are
now covered.

**34b — the single `olw forecast` verb and the workflow collapse. SHIPPED in
the repo; two steps remain on the operator's machine.**

`olw forecast` reads the day and dispatches: no entry → `run_daily_pipeline`,
entry → `run_refresh_pipeline`. A dispatcher, not a rewrite — the two keep
their own bodies, because merging them would lose the invariant that makes
the accuracy record trustworthy.

The `check` jobs are gone, and that is the substance of it rather than a
tidy-up. Two guards written in YAML, on behaviour this repo could not test,
are replaced by one inside the pipeline: a trigger repeating one from the
last hour (`MIN_REISSUE_INTERVAL_MINUTES`) returns without calling the model.
`--force` overrides that and nothing else — since 34a it cannot reach the
scored numbers. A skip exits 0, because a backup slot that correctly did
nothing must not colour a run red.

`forecast.yml` carried all eight backstop slots. It carries none now — see
item 49 for why they were removed on 2026-08-29. `daily.yml` and
`evening_refresh.yml` are dispatch-only — their `schedule:` blocks were
removed in the same commit as 34b, so two workflows can never fire for the
same day — and they stay until the crontab is switched, because a cron line
naming a deleted workflow fails silently from the crontab's side.

The crontab's two lines are now IDENTICAL apart from the hour, which is the
whole point: `ops/trigger_workflow.sh` defaults to `forecast.yml`, so adding a
third issuance is one more cron line and nothing else.

**Remaining, on the operator's machine, in this order:**

1. Switch both crontab lines to `ops/trigger_workflow.sh` with no argument.
2. Confirm a real run from the morning slot and the evening slot, then delete
   `daily.yml` and `evening_refresh.yml`.

The dispatch script was overhauled in the same pass, since a forgotten
deployment's likeliest silence is its own trigger: three attempts with a
pause for network failures and 5xx, no retry on a 4xx (an answer, not a
blip), timestamped output so a log answers "when did this stop working", an
explicit HTTP 401 message naming an expired fine-grained PAT, an explicit 404
naming a workflow that no longer exists, and a warning on a world-readable
token file. Exercised against a local stub for each path.

### Why the split, and the principle behind it

The record is not currently being muddled. Two guards hold: the operator's
crontab calls `evening_refresh.yml` in the afternoon, which is the SAFE path —
`run_refresh_pipeline` deliberately keeps the morning's predictions and even
computes its day-over-day comparison against them — and `daily.yml`'s
`already_done` check skips a duplicate daily run. `trigger_workflow.sh`
deliberately never sets `force`.

But both of those guards live OUTSIDE the pipeline, in a YAML condition and a
cron line on a machine this repo cannot see or test. The app enforces the same
rule in its store, where it is structural and covered by tests. One of those
survives someone ticking `force` on a manual dispatch; the other does not.

**THE TRIGGER IS UNTRUSTED INPUT.** A crontab line, a workflow_dispatch click,
a fork's own scheduler, an operator's `at` job — none of it is code this
project reviews, and all of it can pass parameters. Every guard that protects
the accuracy record belongs inside the pipeline. A caller should be able to
invoke this thing wrongly, with any combination of flags, and be unable to
corrupt the record; the worst it should achieve is a wasted API call.

That generalises past this item and is worth applying to anything else a
trigger can reach.

### The original writeup follows

Today there are two commands, and the operator picks between them by time of
day: `run-daily` in the morning, `refresh-forecast` in the evening. That is
the wrong axis. The real distinction has nothing to do with the clock:

- The **first run of a day** owns verification and the day's
  `model_predictions` — the numbers tomorrow scores.
- **Every later run** is an update: narrative only, predictions preserved
  byte-for-byte.

Since every run now knows what time it is (items 30/31 and the daypart work),
"which command is this?" is the last place the morning/evening split survives.

### Why it cannot simply be "always run-daily"

Verified, not assumed — see
`test_run_daily_a_SECOND_time_overwrites_the_days_predictions`:

- **Normally nothing happens.** `daily.yml`'s `check` job sees today's entry
  and sets `already_done=true`. The evening run is silently skipped, with no
  error, because skipping a duplicate trigger is correct behaviour.
- **With `force: true`, something worse happens.** The pipeline itself does not
  guard this. It re-extracts `model_predictions` from evening-cycle data —
  predictions made with ~12 hours less lead time — and stores them as the
  day's call. Tomorrow they are scored as though issued at 06:00. Every model's
  Day+0 accuracy improves and **nothing in the record shows why**.

`refresh-forecast` currently raises `RefreshWithoutMorningRunError` when
there is no entry to refresh, which is the safety net and must survive any
redesign.

### The shape

A single verb — `olw forecast` — that reads the day's entry and dispatches:

| State | Action |
|---|---|
| No entry for today | Full run: verification, predictions, track record, publish, email |
| Entry exists | Re-issue: narrative only, predictions and verification untouched |

`run_daily_pipeline` and `run_refresh_pipeline` stay as they are underneath.
This is a dispatcher, not a rewrite — the two paths have genuinely different
responsibilities and merging their bodies would lose the invariant that makes
the accuracy record trustworthy.

### What must not break

- **Predictions are written once per day.** The existing test asserting three
  re-issues leave them byte-identical is the guard; it must keep passing.
- **`--force` must not become a way to overwrite predictions.** If a forced
  re-run of a completed day is wanted, it should force the *narrative*, never
  the scored numbers. DECIDED: remove `force`'s ability to reach the
  prediction path at all — this is 34a, shipped, and it is the whole reason
  the split exists.
- **A first run that fails must not leave a half-entry** that makes the next
  run look like a re-issue. Check what happens today if the LLM call fails
  after the entry is written.
- **Both workflows collapse into one.** `daily.yml` and `evening_refresh.yml`
  become a single `forecast.yml` with the same `check` job semantics, and the
  schedule/backstop split described in `evening_refresh.yml`'s header is
  preserved — GitHub crons remain the backstop to the operator's crontab, and
  the two are in different failure domains on purpose (see item 3/11 and
  `ops/trigger_workflow.sh`).

### The server-side change this requires

**This is why it is not a drive-by.** The operator's crontab currently calls
two distinct workflows by name via `ops/trigger_workflow.sh`. Collapsing them
means:

1. The new `forecast.yml` must exist and be proven before the old ones are
   removed, or a cron entry points at a workflow that no longer exists — which
   fails silently from the crontab's point of view.
2. `ops/trigger_workflow.sh` and its documented usage change.
3. The crontab lines change on a machine this repo cannot see or test.

Sequence it so nothing is ever broken between steps: add `forecast.yml`
alongside the existing two, switch the crontab to it, confirm a real run from
each slot, and only then delete the old workflows. Do NOT delete first.

### What the app shares, and what it does not

Worth being exact, because "the engine decides" is true of the RULES and not
of one shared implementation.

Shared, and vector-locked: the prompt (including `isReissue`), the extraction
and scoring, `mergeGroundAqi`, and the two configured/unconfigured source
states. Both sides derive "this is a later issuance" from the same input —
Python from the day's entry, Dart from `earlierToday`.

NOT shared: `run_forecast` itself. There is no Dart dispatcher. The app's
`ForecastRunner` reads `ForecastStore.issuedOn`, passes `earlierToday`, and
calls `savePredictions` unconditionally — the write-once rule living in
`PrefsHistoryStore` rather than in a caller. Same two rules, two
implementations, and only the Python one refuses a duplicate trigger inside
an hour; the app's equivalent bound is the spend cap, which counts calls
rather than issuances.

That is defensible — the app has a user tapping a button, not a crontab, and
a shared dispatcher would have to abstract over storage that has nothing in
common. It is also exactly the shape of drift the vectors exist to catch, and
the vectors do not cover it. If a third rule is ever added to the "which run
is this" decision, it has to be added twice, and nothing will fail if it is
not.

### The app had the same hazard · **Fixed first**

`ForecastRunner.run` in Ensemble always called `savePredictions`, which
overwrote by date, so a second generate on the same day replaced the day's
predictions with fresher-cycle ones. `PrefsHistoryStore.savePredictions` now
returns early for a date it already holds, and the runner passes
`earlierToday` so a later generate reads as an update. The app got the rule
before the pipeline did, which is why 34a describes it as the reference.

---

## 35. Surface convective disagreement — the models argue about thunder and we do not say so · **Planned**

On 2026-08-22 the evening forecast said *"No severe weather hazards are
expected for the remainder of tonight"* and *"mostly dry"*. It was thundering
in Kisumu at the time, with little rain.

The forecast was defensible on precipitation and wrong on the thing the reader
actually experienced. And the data to say so was already in the payload:

| Hour | GFS CAPE | ICON CAPE | ECMWF CAPE |
|---|---|---|---|
| 17:00 | 210 | **1170** | 960 |
| 19:00 | 70 | **780** | 960 |
| 22:00 | 40 | **790** | 720 |

GFS saw essentially no instability. ICON and ECMWF saw 700–1200 J/kg — solidly
convective — all evening. Precipitation totals were near zero in every model,
so on *rain* they agreed; on *instability* they disagreed sharply, and the
narrative resolved that silently toward the quiet answer.

That is the opposite of what this project is for. "Where they agree and where
they disagree" is the entire premise, and this was a textbook disagreement.

**`cape` is already fetched** — it is in `HOURLY_FORECAST_VARS` and reaches the
prompt. What is missing is any instruction about it: `grep -i cape` on
`llm/prompt.py` returns **zero** matches. Nothing tells the model that CAPE
matters, what values mean, or that a split between models on it is worth
stating outright.

Notes for doing this properly:

- **Thunder without rain is a real and common outcome**, and precipitation
  totals alone cannot predict it. High CAPE with modest precipitable water
  gives exactly the "dark and thundering, no rain yet" the operator observed.
- Give the model **thresholds rather than adjectives** — roughly: under 300
  J/kg convection is unlikely, 300–1000 marginal to moderate, above 1000
  supportive of thunderstorms — and require it to cite the spread across
  models the way the synoptic ring already requires.
- **Lake Victoria is not an ordinary location for this.** The basin is among
  the most thunderstorm-prone places on Earth, and its convection is driven by
  lake-breeze convergence at scales global models at 9–25 km resolve poorly.
  A systematic under-forecast of storms here is expected, which is an argument
  for leaning on the models that *do* show instability rather than averaging
  them away.
- This should feed the **hazard section specifically**, which is where a reader
  looks before going out on the water.

Worth checking afterwards whether CAPE deserves its own scored variable in the
accuracy record. Probably not directly — there is no CAPE observation to verify
against — but "did thunder occur" may be answerable from METAR present-weather
codes, which are already fetched.

**Answered on 2026-08-26: yes, and it mattered more than expected.** METAR
present-weather is now parsed and scored — see item 42. The Overview half of
this item also shipped: `instability.py` decides on a 1000 J/kg threshold and
the prompt must carry a thunder clause when it fires, rather than leaving the
Overview to the temperature. What remains open here is the *hazard-section*
requirement to name which model says what, which is still prompt guidance
rather than anything code enforces.

---

## 36. User weather feedback — ground truth from the person standing outside · **Planned**

Proposed by the operator on 2026-08-22, after observing thunder that the
forecast had not called.

Every accuracy figure in this project is scored against Open-Meteo's archive:
temperature, rain occurrence, wind, pressure. That is genuine verification and
it is why the published record can be trusted. But it measures what a
**reanalysis grid cell** recorded, not what a person experienced, and the two
diverge exactly where this project's readers live — convection is local, and a
storm over the lake and a dry street two kilometres away are the same grid box.

A reader who can say *"it thundered here at 19:30 and never rained"* is
supplying information no archive holds.

### What it could be

Deliberately minimal, because elaborate feedback goes unused:

- On the forecast page and in the app, one line: *was this right?* with a small
  number of concrete options — rained / stayed dry / thundered / windier than
  said — plus optional free text.
- Timestamped and located, since both matter and neither can be inferred.

### The hazards, which decide the whole design

- **It must never contaminate the automated record.** The published accuracy
  figures are deterministic, reproducible from the stored log, and their value
  rests entirely on that. Human reports are subjective, sparse and
  self-selecting — people report when a forecast was *wrong*. Blending them
  into per-model skill scores would destroy the one number this project can
  defend. Store and present them **separately**, always.
- **Sparse data invites over-reading.** Three reports is an anecdote. The same
  sufficiency discipline the weekly review already applies — say so when there
  is not enough to conclude anything — has to apply here from the start.
- **It is a personal-data surface**, which nothing in this project currently
  is. Location plus timestamp plus free text is enough to identify a routine.
  Anonymous, coarse location, no accounts, and say plainly what is stored.
- **It needs somewhere to go.** The pipeline is git-as-database with no server
  and no inbound path; the whole architecture assumes one-way publication.
  Accepting input is the first thing that breaks that property, and the
  mechanism chosen (a form service, a repo issue, an app-only local store)
  should be weighed against how much of the zero-infrastructure guarantee it
  costs.

### Where it would pay off

The most valuable use is not scoring — it is **finding the systematic misses**.
If thunder is reported on evenings the forecast called quiet, that is a
detectable pattern pointing at item 35, and it is the kind of blind spot no
amount of archive verification would surface, because the archive agrees with
the forecast that it barely rained.

---

## 37. Every variable needs a day-character, not just rain · **Planned**

Item 33's fix taught the summary that 0.6 mm at 20:00 is not "a wet day". The
same reasoning applies to every other headline variable and has not been done.

**Wind.** `ModelPrediction.wind_kmh` is the day's PEAK and carries no timing.
So 15 gusting 20 km/h from 08:00 — a genuinely breezy day someone plans around
— is indistinguishable from the same peak arriving at 16:00, which is an
afternoon feature at the end of a calm day. The operator raised exactly this.
Needs a peak-wind HOUR stored alongside the value, then banded the way rain now
is: calm / breezy / windy, crossed with morning / afternoon / evening.

**Cloud cover.** `cloud_cover` is fetched and reaches the prompt, but nothing
reduces it to a day-shape. "Sunny until midday then clouding over" and
"overcast all day" are different days with similar averages, and the average is
what a model reading raw hourly arrays will tend to describe.

**The shared discipline** — and the reason these belong together — is that a
mean over a calendar day is almost never the right summary of a day. The
question is always *how much, and when*. Whatever is built here should follow
the rain implementation: bands and onset computed in code, ready-made phrases
handed to the prompt, and the SCORED values left untouched.

The last part matters most. `rain` stayed a boolean when amount was added,
because the accuracy record is built on it. Any wind or cloud work must keep
`wind_kmh` exactly as it is scored today.

---

## 38. Humidity and "feels like" are not in the forecast at all · **Planned**

Raised by the operator. Checked rather than assumed: `relative_humidity_2m`,
`apparent_temperature` and `dew_point_2m` appear **nowhere** in the codebase.
Only `cloud_cover` among the comfort-related variables is fetched.

This is a real gap for Kisumu specifically. On the lake basin at ~1,100 m, 29°C
at 40% humidity and 29°C at 85% are different days to be outside in, and the
forecast currently reports one number for both. Humidity is arguably the
variable a reader's body notices most after rain.

All three are available from Open-Meteo hourly at no extra cost — the fetch
already asks for a list of variables and adding to it is free.

Where it should go:

- **The Overview**, which is the "how will this feel" sentence and currently
  has only temperature and rain to work with. "Warm and close" versus "warm and
  dry" is the kind of orientation that sentence exists for.
- Probably a stat-grid field, subject to the same rule as UV: omit it when it
  is not telling the reader anything.

Two cautions:

- **Apparent temperature is a model output, not an observation**, and its
  formula varies. Prefer stating humidity plainly alongside the real
  temperature over publishing a single "feels like" number whose derivation we
  cannot show. This project's whole posture is that its numbers are checkable.
- **It needs a day-character like everything else** (item 37). Peak humidity at
  dawn is normal and unremarkable; peak humidity at 15:00 with 29°C is the
  thing worth saying.

---

## 39. Compute sun times instead of fetching them · **Shipped**

Originally noted inside item 30 (twilight). A live failure on the first full
day makes it worth its own entry.

The 2026-08-23 morning run logged:

```
Sun times unavailable (Request to https://api.open-meteo.com/v1/forecast
failed: ... Read timed out. (read timeout=30)); using the clock alone.
```

The degradation behaved correctly — the run continued, the forecast shipped,
the prompt said the part of day was unknown rather than guessing. But the
published forecast carried no sunrise or sunset that day, and the refresh
inherits the gap because it now falls back to the stored value rather than
overwriting a good one with null.

**Measured 2026-08-28, and it is not one bad day: it is every day.** All 18
committed log entries carry `sunrise: null`. The field shipped on 2026-08-22
(846c146, with the refresh-path fix at 267df6f the same evening), so the
entries that should hold a value are 08-23 onward — and they do not. Three
production run logs were read in full: 2026-08-26T03:03:29Z,
2026-08-27T03:03:24Z and 2026-08-28T00:29:56Z. All three logged the same 30
second read timeout on `api.open-meteo.com/v1/forecast`. The runs of 08-23,
08-24 and 08-25 were not checked; the three that were are unanimous.

The same call from a laptop answers in well under a second, and a full
pipeline run against the live network stores `sunrise: '06:38'` — so this is
not the endpoint being down, and not a bug in the current tree. It is that
one specific call, from GitHub's runners, reliably enough to have never once
succeeded there.

Note what that means for the reader: the site has shown no sun times for six
days, and every prompt in that window was told the part of day was unknown —
`daypart_without_sun` keeps the clock and drops the phase, so the model has
been writing "it is 06:01" without knowing whether that is before or after
dawn. Nothing flagged it. This is item 51's motivating case, found while
looking for something else.

**Sunrise and sunset are deterministic.** They depend on latitude, longitude
and date, and nothing else. Fetching them over a network for a fixed location
is the only part of this that can fail, and it is failing for a quantity that
could be computed offline in about forty lines — the same NOAA solar-position
routine item 30 needs for civil twilight.

Doing that would:

- remove a network call and its timeout from every run,
- make sun times available even when Open-Meteo is unreachable, which is
  precisely when a forecast most needs to explain what it is missing,
- deliver twilight (item 30) as a by-product rather than as separate work,
- and be vector-lockable across Python and Dart like everything else.

The one thing to be careful of: Open-Meteo's values account for refraction and
elevation, and a naive computation will differ by a minute or two. Match the
convention (sun's upper limb at the horizon, standard refraction) and vector
the result against a handful of known locations and dates — including a polar
one, where the answer is "no sunrise" rather than a time.

### What shipped, 2026-08-28

`solar.sun_times` in Python and `sunTimes` in `olw_core`: NOAA's solar
position equations for the sun's centre at -0.833 degrees, which is upper limb
plus standard refraction — Open-Meteo's convention, so this is a replacement
and not a change. `fetch_sun_times` and `fetchSunTimes` are gone from both
fetch layers. Vectored as `spec/vectors/solar.json`, nine cases.

**Accuracy, measured against Open-Meteo over 108 consecutive days at each of
eleven locations.** One minute at worst from the equator to 55 degrees, two to
64 degrees, 14 at Tromso (median 2) and 21 at Longyearbyen (median 4). The
high-latitude spread is inherent and NOAA documents it above about 72 degrees:
the sun crosses the horizon at a shallow angle there, so a small difference in
the assumed altitude moves the crossing by many minutes. It concentrates at
the ends of the midnight sun — every Tromso disagreement over 5 minutes falls
in the three days after its midnight sun ended. Kisumu is at 0.09 degrees
south, where the worst case is one minute; the table is in `solar`'s
docstring for anyone forking further north.

**Python and Dart swept against each other over 17,787 cases** — latitude -89
to +89, every longitude, a full year, and nine UTC offsets including the
half-hour and three-quarter-hour ones. Zero disagreements. The vector cases
alone would not have shown that; see `spec/README.md` on why they pin the
cases you chose rather than the function.

**Two polar conventions were matched rather than invented**, because
`daypart.classify_phase` reads the SPAN between sunrise and sunset to reach
its polar phases. The midnight sun is local midnight to local midnight the
next day, a 24 hour span; polar night is local midnight to local midnight, a
span of zero. Both verified against the live API on 2026-08-28, and matched on
all 148 polar days in the accuracy sample.

### Three things found while doing it

**Polar night does not return nulls.** A comment in `pipeline._sun_context`
said it did, and the `if not rises or not sets` branch beneath it existed to
handle that. Open-Meteo returns 00:00 for both with `daylight_duration` 0.
Nothing depended on the claim, which is how it survived.

**Open-Meteo applies ONE UTC offset to a whole response, and not always the
right one.** Asked for Europe/London on 2025-12-15 alone — deep in GMT — it
answered `utc_offset_seconds: 3600` and put sunrise at 08:59 against a
published 07:59. Across the 2025 changeover it returned 3600 for all three
days, so the two after it were an hour out. Kisumu keeps no daylight saving
and the live pipeline only ever asked for today, so this never reached
production here; a fork in a DST zone would have met it immediately.
`dates.utc_offset_seconds` takes the offset per date, at local noon.

**The app's Dart client had `'\$lat'` — the dollar escaped — in
`fetchSunTimes` AND `fetchForecastHourlyForward`.** Both sent the literal five
characters, both 400'd, and both callers swallow failures by design. Shipped
2026-08-22 in `5e625e7`; found 2026-08-28. So since the day the feature
landed, an app-generated forecast has had no sun times AND no forward window —
`daypartWithoutSun` every run, and no overnight data. The sun half is gone
with the fetch; the forward half is fixed, and both now have a request-shape
test, which is what was missing. The same commit's `_fetch` never captured the
`Date` header either, so `reconcileNow` was a no-op in the app; it captures it
now.

---

## 40. Bring existing code up to AGENTS.md · **Planned, low priority**

`AGENTS.md` was adopted after most of this code was written. Known gaps, with
an honest read on whether each is worth the churn.

**Braces on one-line `if` — worth doing.** 67 sites in `olw_core`. Each is
individually harmless; the rule exists because adding a second statement to a
brace-less `if` silently leaves it outside the branch. Mechanical, low risk,
no behaviour change.

**Enums instead of boolean parameters — now a soft rule.** Four exist: `dry_run`, `force`, `is_reissue`,
`duringValidation`. The rule targets languages where `f(x, true)` is possible;
Python keyword arguments and Dart named parameters already make every call
site here self-describing (`dry_run=False`, never a bare `False`). So the
readability win is small.

The exception is `is_reissue`, which is genuinely mis-typed: the real question
is *which run of the day is this*, and item 34 replaces it with that. Convert
it there rather than in a sweep.

**Function names under 30 characters — now a soft rule.**
`extract_day_n_predictions_from_daily` is 36 and says exactly what it does.
Shortening it costs clarity to satisfy a number. Two names exceed the limit
and both are clear. Not worth changing.

Do the braces as a single mechanical commit. Take the rest opportunistically —
when a function is being edited for another reason, bring it into line.

---

## 41. Satellite analysis · **Planned**

Raised by the operator on 2026-08-26, in the same session that established
why it matters: a thunderstorm was observed at Kisumu Airport on 2026-08-24
(METAR `TS`, 13:30–14:30Z, with CB cloud and a 32°C→25°C outflow drop), and
the reanalysis this project scores against recorded 0.5 mm and no
thunderstorm code at all. See item 42 for the METAR fix that closed the
immediate gap.

METAR fixed the *point*. Satellite is the argument for fixing the *field*.

### The two distinct things "satellite" could mean here

**1. Cloud-top temperature, for convection.** Geostationary infrared gives
cloud-top brightness temperature every 15 minutes or better. Deep convection
has a signature that is hard to miss — cloud tops colder than roughly −60°C
mean a storm with real vertical development. Over the Lake Victoria basin,
which is among the most thunderstorm-prone places on Earth and whose
convection is lake-breeze driven at scales global models at 9–25 km resolve
poorly, this is the observation that would tell us what actually happened
across the whole basin rather than at one runway.

**2. Satellite precipitation estimates, for rainfall.** This is the one that
may matter more, because it attacks the actuals problem directly. The
project's entire accuracy record is scored against Open-Meteo's archive
(ERA5-family reanalysis), and 2026-08-24 is a worked example of that archive
smoothing an isolated tropical convective event into nothing. Satellite-based
precipitation products are built for exactly this failure and run at far finer
resolution.

The second is a candidate *replacement or cross-check for the actuals source*,
which makes it a much bigger change than the first — and a much bigger prize.

### What needs verifying before any of this is committed to

Everything below my training cutoff is stale by definition and none of it has
been checked live. Do that first:

- **Coverage.** East Africa sits under the Meteosat series at 0°/41.5°E
  (EUMETSAT), not under GOES. Confirm which satellite and which service is
  current, and whether the older MSG or the newer MTG generation is the one
  serving data now.
- **Access and licensing.** Registration requirements, rate limits, whether
  free access covers automated daily pulls, and whether the licence permits
  redistributing derived values in a public repo — this project commits its
  data into git and publishes it. A source we cannot republish is a source
  we cannot use here.
- **Latency.** The morning run fires at 03:07 UTC and scores *yesterday*.
  Anything with more than ~12 hours of latency still works for verification
  even if it is useless for the forecast itself.
- **Precipitation products specifically.** Confirm what is currently offered,
  at what resolution and latency, and under what terms. Do not assume the
  product names or the access paths I would reach for from memory still
  describe reality.

### Sequencing

Cross-check before replacement. Fetch it, store it alongside the existing
actuals, and let a few weeks of disagreement accumulate before any decision to
score against it. If satellite and reanalysis agree on most days and diverge
exactly on convective ones, that is a measured finding worth writing down —
and the argument for switching makes itself.

Related: item 35 (convective disagreement), item 36 (user feedback as ground
truth), item 42 (METAR observations).

---


### Named targets, 2026-09-03

From `RADAR_SATELLITE_REGIONAL_OBSERVATIONS_HANDOFF.md`. This item was an
aspiration with no endpoint; it now has operators, platforms and channels.

**Kisumu sits under EUMETSAT's Meteosat service, and satellite coverage here
is dramatically better than radar coverage** — which is not a general truth
but a local one, and the reason satellite rather than radar is this
deployment's remote-sensing route (item 45, item 63).

- **Meteosat-12 / MTG-I1**, instrument FCI, operational near 0° — the modern
  production platform to target through EUMETSAT.
- **Meteosat-11**, SEVIRI, still operational at 9.5°E, and the one with a
  ready prototyping path.

**RealEarth exposes Meteosat-11 SEVIRI full-disk products by ID**, which
makes a cross-check cheap before committing to an EUMETSAT data-store
integration. `Met11-SEVIRI-FD-BAND09` is IR 10.8 µm, the channel most
relevant to verification because it carries cloud-top brightness
temperature and reads cirrus.

**MTG's Lightning Imager is the interesting one for this location.** The
events this project observes worst are exactly the convective ones (item
45), and lightning is a direct observation of convection rather than an
inference from it — the one instrument that could see what the reanalysis
misses and the airport only catches when it is overhead.

**Physical semantics are not negotiable, and this is where they bite.**
Brightness temperature is not air temperature; reflectivity is not rainfall
accumulation; a satellite pixel, a point station and a 25 km model cell do
not observe the same spatial object. A satellite product joins item 45's
ladder as reliable-for-OCCURRENCE, never as gold: it sees cloud and inferred
rate, not a gauge.

**One trap recorded from the research.** A global product frame timestamp
does not prove the swath covered this point at that instant. Polar-orbiting
products in particular must be queried for extent or point value, with
"outside swath" treated as no observation rather than as no cloud — the
same absence-is-not-evidence rule everything else here follows.

## 42. The reanalysis does not see thunderstorms · **Shipped**

Found 2026-08-26, from a reader's own observation that a forecast had called
the previous day "dry again" when it had thundered.

Every accuracy figure in this project is scored against Open-Meteo's archive.
For 2026-08-24 that archive recorded 0.5 mm and WMO codes 0/1/51 — clear sky
and light drizzle, no thunderstorm code anywhere. Kisumu Airport, four
kilometres away, reported this:

| Time (Z) | Report |
|---|---|
| 13:00 | `FEW029CB` — cumulonimbus building |
| 13:30 | `TS FEW029CB BKN030`, 31°C |
| 14:00 | `TS FEW027CB BKN028`, 28°C |
| 14:30 | `TS FEW024CB BKN025`, 25°C |

A thunderstorm, with a 7°C outflow temperature drop across ninety minutes.
ERA5-family reanalysis at ~25 km cannot resolve isolated tropical convection,
and the Lake Victoria basin runs on exactly that.

Archive CAPE was checked as an alternative signal and is useless here: the
endpoint returns the variable and fills it with zeros at this location.

### What it cost

Not just prose. The verification note for that day reads *"GFS, ECMWF, and
Kenya Met falsely predicted rainfall"* — three models were marked wrong for
calling a day that convected, and the two that called it dry were credited.

Measured across the 42 days then stored, **5 were filed as dry while the
airport watched a storm pass over**: 2026-07-17, 07-20, 07-22, 08-17, 08-24.
Sixteen of 44 days in the window had observed thunder.

### What shipped

- `DailyActual.thunder`, three-valued. `None` is "not observed" and never
  "no thunder"; a fork with no ICAO configured scores exactly as before.
- Rain scored against `observed_convection()` — reanalysis rain OR observed
  thunder. A dry call on a thunder day is now wrong, which is the
  uncomfortable half and the correct one.
- `describe_day_rain` never calls a thunder day dry.
- `olw rebuild-record`, which re-derives the whole record from stored
  predictions plus refetched observations.

### The rebuild, and what it did to the rankings

| Model | Day+0 all-time, before | After |
|---|---|---|
| best_match | 73.3% | **86.7%** |
| ecmwf_ifs025 | 73.3% | 73.3% |
| icon_seamless | 86.7% | **73.3%** |
| gfs_seamless | 40.0% | **53.3%** |
| ukmo_seamless | 66.7% | **53.3%** |
| kenya_met | 50.0% | **66.7%** |

ICON was the headline performer and is now level with ECMWF; UKMO drops
sharply. Both were being rewarded for calling dry days that thundered. Only
15 Day+0 checks back this, so it is a correction to a thin record, not a
settled ranking.

### Source, and the constraint behind the choice

Uses Iowa State's ASOS/METAR archive, not the aviationweather.gov endpoint
already in use. That endpoint serves a rolling window (`hours`, verified to
48; the `date` parameter returns HTTP 400), which is enough for a run that
scores yesterday and never misses, but cannot rebuild history and would drop
an observation permanently after a missed run. The archive is idempotent, so
a gap self-heals on the next run.

Verified live 2026-08-26: the archive covers 2026-07-14 onward for HKKI at a
median of 34 reports/day.

### Left alone deliberately

The published narrative for 2026-08-24 still says those three models were
wrong. It is a record of what was said at the time. Recomputing an arithmetic
record is one thing; rewriting a published narrative to match is another, and
this project does not do the second.

### Still open

- The reader who noticed this is a better convection sensor than either data
  source. That is item 36.
- METAR is one point. Item 41 is the argument for seeing the whole basin.
- A station reporting `TS` with no rain group means thunder heard at the
  aerodrome. Whether it rained a few kilometres away is not something this
  can answer.

Related: item 35 (convective disagreement), item 36 (user feedback), item 41
(satellite analysis).

---

## 43. WAQI sub-indices are stored under concentration names · **Planned**

Noticed 2026-08-26 while working on item 4, not acted on.

`fetch/waqi.py` reads `iaqi.pm25.v` and `iaqi.pm10.v` into fields named
`pm25` and `pm10`. WAQI's `iaqi` values are **AQI sub-indices, not µg/m³
concentrations**. The evidence is in the stored data: `aqi` equals `pm25`
exactly on every day both are present, which is what you would expect when
PM2.5 is the dominant sub-index and the composite AQI is its maximum.

Separately, `pm10` has been frozen at 15 / 18 / 37 for the three configured
stations on **every day from 2026-08-17 to 2026-08-26** while `pm25` moves
daily. Whatever that field is, it is not a live reading.

| Date | Airport pm25 | Airport pm10 | Ochieng' pm10 | Dunga pm10 |
|---|---|---|---|---|
| 2026-08-17 | 84.0 | 15.0 | 18.0 | 37.0 |
| 2026-08-21 | 69.0 | 15.0 | 18.0 | 37.0 |
| 2026-08-26 | 63.0 | 15.0 | 18.0 | 37.0 |

Neither field reaches the narrative today, so nothing published is currently
wrong. But `data/log/*.json` is committed, and item 24 proposes publishing it
as a feed for other apps — at which point a field labelled `pm25` carrying an
AQI sub-index becomes someone else's bug.

Do this when item 24 is picked up, and probably as part of it:

- Rename to what the values are (`pm25_aqi` / `pm10_aqi`), or fetch genuine
  concentrations if WAQI exposes them for these stations, and check live
  which is actually available rather than assuming.
- Decide what to do about the stuck `pm10`. A value that never changes is
  either cached upstream or not measured at all; publishing it either way is
  worse than omitting it.
- Migrating stored entries is optional — the field is unread — but the
  rename must not silently reinterpret history.

Related: item 4 (ground AQI staleness), item 24 (published data feed).

---

## 44. A sources page — what feeds this, and what each source is for · **Planned**

Raised by the operator on 2026-08-26, immediately after item 42 landed.

The lesson from 42 was not "METAR is useful". It was that **a source had been
in the config since the Apps Script original, and nobody — including the
person who put it there — could remember whether it was a decision or an
accident, or what it was allowed to affect.** The answer was buried in a
comment on line 196 of a reference file. That is a bad place for it, and the
same question is now live for every other source here.

This project's entire claim is that its numbers are checkable. A reader
cannot check a number whose origin is undocumented.

### What is actually feeding the forecast right now

Assembled while writing this item, and longer than it feels from inside the
code:

| Source | Used for | Scored? |
|---|---|---|
| Open-Meteo forecast API, 5 models (`gfs_seamless`, `ecmwf_ifs025`, `icon_seamless`, `ukmo_seamless`, `best_match`) | The forecast itself — hourly today, forward window, daily extended | Yes, per model per lead time |
| Open-Meteo archive (ERA5 family) | "What actually happened" — every verification figure | It *is* the yardstick |
| Kenya Met daily bulletin | Narrative context, and a peer prediction | Yes, as `kenya_met` |
| Iowa State ASOS/METAR archive (HKKI) | Observed thunder → convection | Yes, since item 42 |
| aviationweather.gov METAR (HKKI) | Current-conditions cross-check in the prompt | No |
| Open-Meteo air-quality (CAMS) | Forecast AQI | No |
| WAQI, 3 Kisumu stations | Ground-truth AQI, with staleness | No |
| Open-Meteo regional + synoptic pressure | Pressure gradient, Synoptic Overview | No |
| Open-Meteo sun times | Issuance/daypart reasoning | No |
| Gemini | Prose only, never arithmetic | n/a |

Nine data sources, four of them scored, and no single place says so.

### What the page should show

- **Every source, what it feeds, and whether it is scored.** Roughly the
  table above, generated rather than typed.
- **Which source verifies which variable.** This is the part that would have
  prevented item 42's blind spot lasting as long as it did — see the
  measurement below.
- **Freshness and reachability.** Ground AQI already computes staleness; the
  local bulletin has a `fetched_at_utc`; a source that silently stopped
  returning data should be visible here rather than inferred from a gap in
  the narrative.
- **Per-source skill, where one exists.** The accuracy page has this for
  models; a reader should be able to get from "who says this" to "how often
  are they right" in one hop.

### Measured: which sources actually disagree

Checked 2026-08-26 across the 42 stored days, ERA5 archive against HKKI METAR:

| Variable | Agreement | Conclusion |
|---|---|---|
| Daily high temperature | mean +0.43 °C (METAR warmer), max +1.9 °C, over 1 °C on 6 of 42 days | Agree within noise. METAR reports whole degrees, which explains much of the spread. Not worth cross-verifying. |
| Peak wind gust | METAR reported a gust group on **2 of 42 days** | METAR only files a gust when one occurs. Too sparse to verify against. |
| Convection | Diverged on **5 of 42 days**, every one a real storm the archive missed | The whole value was here |

So the honest headline for the page: **the two sources agree on almost
everything, and the one place they disagreed was invisible until someone
standing outside said so.** That is worth stating on the page rather than
implying every source is a redundant check on every other.

### Two surfaces

**Published page** (`docs/sources.html`). The extension point already exists
and is clean: `render_accuracy_page` / `accuracy.html.jinja` / `NavLinks`
have exactly the shape needed, and `publish/pages.py` regenerates on every
run. Generated, never hand-edited — same rule as the rest of `docs/`, and
doubly so here, because a hand-maintained sources list is the specific thing
that goes stale and then lies.

**App screen.** Same content from `olw_core`. The app has a stronger claim on
it than the website does: someone who installed a weather app has no `config/`
to read.

### Notes for doing it

- Build it from `config/location.yaml` plus `defaults.MODELS` plus what the
  fetch layer actually returned this run — not a hardcoded list. A forker who
  leaves `metar_station_icao` blank must see that reflected, not see HKKI.
- Say plainly where a source is a single point (the airport) versus a grid
  cell (the reanalysis) versus a model (CAMS). The failure in item 42 was
  precisely a point-versus-grid confusion.
- Licensing and attribution belong here too, and this is where item 41 will
  need to declare whatever satellite terms come with it.

Related: item 24 (published data feed — same provenance question for
machines), item 41 (satellite), item 42 (why this item exists), item 43
(WAQI field naming, which this page would have made obvious).

---

## 45. Which source is "what actually happened" · **Designed and stamped 2026-09-03**

Raised by the operator on 2026-08-26, immediately after item 42 and item 44.

Item 42 gave the airport a scored role for exactly one variable. The
question that follows: what if the airport is simply *better*? Someone
living beside a well-equipped station may reasonably want it to be reality
every day — not a per-field patch applied when the reanalysis misses a
storm. And a fork or app user who adds two, three, four sources of their
own needs an answer to the same question at a larger scale.

So: do we **learn** which source to believe, or **let the user choose**?

### Choose. This one cannot be learned.

Learning a source's quality requires scoring it against a truth. Here the
sources *are* the truth candidates, so there is no held-out yardstick to
fit against. That is the structural difference from model skill, which is
learnable precisely because a forecast has something to be scored against.

Item 42 is the proof. The archive's blind spot was not found by learning;
it was found by a reader standing outside. Item 44's measurement is
*agreement*, not accuracy — no volume of data would have said which of the
two was right.

What is measurable without a truth is **disagreement**. That is a flag for
a human decision, not a weight.

### Per-variable precedence, not a primary source

Not one "reality" source, and not a blend. For each observed variable, an
ordered chain; the first source that reported for that day wins.

```yaml
truth:
  high_c:   [airport_hkki, era5_archive]
  low_c:    [airport_hkki, era5_archive]
  thunder:  [airport_hkki]              # nothing else observes it
  wind_kmh: [era5_archive]              # see trap 1
```

Blending is wrong here. Averaging a point observation and a 25 km grid
mean produces a number that is neither, and cannot be honestly scored
against. `rain` is a boolean; there is nothing to average.

### The four traps, worst first

**1. Absence has three states, and they differ per variable.** A station
can report a value, report and genuinely observe nothing, or not report at
all. Only the third should fall through. METAR files a gust group *only*
when a gust occurs — 2 of 42 days, per item 44. Read "no gust group" as
"did not report" and the airport never wins the wind chain; read it as
"0 km/h" and it is wrong every day. `DailyActual.thunder` already encodes
this distinction for one field. Generalising it means per-variable absence
semantics, never a naive "first not None".

**2. Fall-through changes the yardstick mid-record. THE STAMP SHIPPED
2026-09-03** — `DailyActual.provenance`, a `{field: source_id}` map written
where the values are produced, in both languages and vector-locked. Three-
valued like `thunder` before it: `None` means the day predates recording, and
an empty map would claim we looked and found no sources, which is never true
of a stored day. A field the source had no value for is left UNSTAMPED, since
a stamp asserts an observation was made. `confidence_of()` derives item 45's
ladder from the source id rather than storing it per day, because confidence
is a property of the instrument and not of the weather — and an unrecognised
source is `"unknown"`, never something trustworthy by default. The original
note follows.

**2. Fall-through changes the yardstick mid-record.** The airport is truth
for 300 days, down for 5, and those 5 are scored against a coarser
instrument. That is acceptable — but only if provenance is stamped per day
per variable in the stored actual, so an unexplained dip in the accuracy
record can be traced rather than guessed at. `DailyActual` has no
provenance field today: the METAR/archive split is implicit in
`_apply_observed_thunder`. This is item 44's complaint one level deeper,
and it is the prerequisite for everything else in this item.

**3. Changing precedence must force a rescore, never a silent
reinterpretation.** `olw rebuild-record` already does the work. The rule
established for `precip_mm` applies unchanged: altering what a stored
field means makes every stored day incomparable with every other.

**4. `observed_convection()` is not a general merge rule.** Its OR is an
asymmetry justified by physics — a station reporting `TS` is strong
positive evidence, a grid cell reporting dry is weak negative evidence.
For temperature, precedence is right and OR/max is nonsense. It must not
become the template for the other variables.

### The one question the "add a source" flow must ask

Is this an **observation** (a truth candidate, joins a precedence chain) or
a **prediction** (joins the scored model list)? Kenya Met is a prediction,
scored as `kenya_met`. The airport is an observation. A user adding "my
local met office app" will not classify it correctly unasked, and getting
the fork wrong contaminates the record in a way that is hard to unwind.

Item 11 already owes the app a source-discovery flow. This is the question
that flow has to ask first.

### Default from distance, do not assume

"I live right by the airport" is doing a lot of work in the framing, and it
is computable — the config already carries the station and the user's
point. A fork 40 km from its nearest ASOS should not inherit
airport-as-truth by default.

### Sequencing

Item 41's rule applies here too: cross-check before replacement. Stamp
provenance, fetch the extra readings, store them alongside, change nothing
that is scored. Let divergence accumulate for a few weeks, then decide with
numbers. If the airport and the archive agree on temperature within noise —
and item 44 measured mean +0.43 °C, so they do — the precedence machinery
earns nothing for that variable, and that is worth knowing before building
it.

**The readings shipped 2026-09-03**, stored and unscored, as this sequencing
asks. One fetch serves both what the station SAW and what it MEASURED —
`observed_station_data`, since the archive request is the slowest call in the
verification pass and asking it twice for two views of the same rows would
double that for nothing. `station_high_c`, `station_low_c` and
`station_peak_wind_kmh` sit beside the reanalysis values and are stamped
`metar_station` in the provenance map. Nothing reads them, which is the
point.

Applied by one shared helper from BOTH the daily pipeline and
`rebuild-record`: a rebuild that dropped them would silently erase weeks of
accumulation and look exactly like the station having said nothing.

`p01i` and `gust` are deliberately not requested — the first is a constant
0.00 here, the second missing on all 932 rows of the sample. Fahrenheit and
knots are converted on the way in so a later comparison is a subtraction
rather than a conversion someone has to remember, and `M`/`T` markers become
`None` rather than numbers.

Driven live 2026-09-03 over 2026-08-28 to 09-01: five days, highs 31-33 °C,
lows 16-18 °C, peak winds 18.5-32.4 km/h. Sensible, and now accumulating.

### `olw divergence`, and what it already says

Written 2026-09-03, before there was continuous data to point it at,
deliberately: the decision it feeds is weeks away and the reasoning about
what to measure is freshest now. Written later, under the pressure of wanting
an answer, it would be tempting to measure whatever made the choice look
obvious.

It reports signed AND absolute error per variable — a systematic offset is a
calibration difference somebody could correct for, the same magnitude
scattered either way is noise, and a signed mean near zero hides the second
completely. Rain is a contingency table rather than a mean, because averaging
booleans produces a number that reads like an error magnitude and is not one.
It adjudicates nothing.

**The continuous variables have no overlap yet** — the readings started
today. But occurrence has been accumulating since the station was first read,
and over 43 days it already says something uncomfortable:

| | days |
|---|---|
| both saw rain | 9 |
| both saw none | 20 |
| **station only** — the reanalysis missed it | **4** |
| **reanalysis only** — wet cell, dry airport | **10** |

**They disagree on 14 of 43 days, and the disagreement runs mostly the
opposite way to the one this project has been telling itself about.** Items
42 and 53 are both stories about the station catching rain the reanalysis
scored as dry, and that is real — it happened 4 times. But the reanalysis
claims wet on 10 days the airport reported and saw nothing, two and a half
times as often.

That matters for `observed_convection()`, which ORs the two. The OR is not
mostly the station rescuing missed convection; it is mostly the reanalysis
adding wet days the point observation did not see. Whether those are real
rain the airport missed four kilometres away, or a 25 km mean smearing rain
from elsewhere in the cell, **this report cannot say and neither can anything
else** — which is item 45's thesis arriving as a measurement rather than an
argument.

Nothing should be changed on the strength of it yet. It is one location, one
station, 43 days, and the direction of the error is not the same question as
which source is right.

Concretely, the extra readings are cheaper than they look:
`fetch_metar_archive` requests `data=metar`, the raw report text only. Iowa
State's ASOS service exposes structured fields alongside it, so temperature
and wind from the airport are a parameter change on an existing fetch, not
a new source.

### Checked live 2026-09-03: HKKI files no precipitation amount at all

The item told whoever got here to check before designing around it. The
answer is worse than "no data", and it is trap 1 in its most dangerous form.

Queried 932 hourly rows, 2026-07-20 to 09-03, `data=p01i,wxcodes`:

- **`p01i` is `0.00` on every single row.** Not missing — zero. Including
  16:00Z on 2026-08-29, the hour whose own report carries `-RA`. The station
  says "light rain is falling" and "0.00 inches" in the same breath.
- `gust` is missing on all 932 rows, matching item 44's finding that METAR
  files a gust group only when a gust occurs.
- `wxcodes` is the signal that IS real, and it is sparse: 17 rows in 932,
  `TS` ×7, `VCSH` ×3, `-RA` ×2, `TSSH` ×2, `-TSRA`, `TSRA`, `SHRA`.

**So `p01i` from this station is a constant zero dressed as a measurement.**
Put it in a precedence chain as an observation and it becomes a confident
"no rain" on every day of the record, including the days it rained — which
is exactly trap 1's warning about reading a missing gust group as 0 km/h,
except that here the API supplies the wrong number rather than an absence,
so nothing downstream could even detect it.

The existing parser is already doing the right thing by reading present
weather out of the raw report text and ignoring the amount. **Any per-
variable chain for precipitation at this location gets the reanalysis for
AMOUNT and the station for OCCURRENCE, and never the station for amount.**
Worth generalising for forks: a station that reports a variable as a
constant is indistinguishable from one that measures it, unless somebody
looks — so the "add a source" flow needs a sanity pass, not just a fetch.

### Trust the positive — yes for rain, and here is what it costs

The operator's instinct, 2026-09-03: if the airport says it rained, it
rained; if the airport says dry and the archive says wet, it may have rained
elsewhere in the region.

That is already what ships. `observed_convection()` is exactly this OR, and
trap 4 above already fences it to rain for the right reason: a station
reporting `TS` is strong positive evidence, a grid cell reporting dry is weak
negative evidence, and for temperature the same rule would be nonsense.

**The cost, which is not obvious and matters more as sources are added.** An
OR is MONOTONIC: every source you add can only ever create wet days, never
remove them. So the observed rain rate drifts upward with the size of the
source set, and every model's dry call looks worse over time — a change in
the record's instrumentation that would read exactly like the models getting
worse. Item 53.1 measured this in miniature: adding station precipitation
moved the all-time figures down by about five points in a day.

Two consequences, both cheap if done now and expensive later:

1. **The source set must be stamped per day**, which is trap 2's provenance
   field arriving for a second independent reason. Accuracy figures are only
   comparable across days that were scored against the same instruments.
2. **"Trust the positive" quietly answers a different question than the
   forecast asks.** The forecast is about a point; "did any source see rain"
   is about a region, and it gets broader with every station added. Where
   that is the intent, say so on the accuracy page. Where it is not, the
   chain needs distance to enter it.

### Confidence is not truth, and the record can carry both

The operator's framing, 2026-09-03, and it supersedes the strict chain above
rather than contradicting it. Sources are not equally good and the record
should say so:

| Evidence | Confidence |
|---|---|
| A ground station that MEASURED an amount | gold |
| An airport reporting rain — measured, or a `wxcodes` group | reliable |
| Reanalysis precipitation over a 25 km cell | possible |

**Still declared, not learned.** Where a value sits on that ladder follows
from what the instrument physically is, which is knowable before any data
arrives. The section above still holds: with no held-out truth there is
nothing to fit against, and that is as true of an LLM as of a formula.

**Why a grade beats a chain.** A precedence chain picks a winner and throws
the rest away, which loses the one thing worth keeping: how sure we are. A
day whose only evidence is a grid cell saying "wet" is not the same
observation as a day a station measured 12 mm, and scoring both as an
unqualified `rain: true` discards that difference before anything can use it.

**What carrying it buys, concretely.** Accuracy can then be reported per
confidence band, which turns one number into an honest pair: *"ECMWF 75%
over all scored days, 82% over the days the record is confident about."* The
gap between those two is itself a measurement — of how much the published
figure depends on the weakest instrument in the set. Today that gap is
invisible and unbounded.

**This is item 58's dual.** That item makes the FORECAST probabilistic
because a boolean cannot tell a confident wrong call from an honest hedge.
This makes the OBSERVATION graded for the same reason on the other side. A
proper scoring rule handles both; a boolean ledger handles neither. They
should be designed together, and neither needs the other to ship first.

### Why this matters here specifically, and the asymmetry it exposes

At this location the events being missed are pop-up thunderstorms and other
localised convection, not regional systems. A 25 km grid mean is structurally
the wrong instrument for a 2 km storm — not inaccurate, but answering a
different question — and HKKI reporting `0.00` inches in the same report as
`-RA` is the same failure from the other direction.

**Which produces an asymmetry worth stating plainly: this project forecasts
convection better than it observes it.** CAPE gives real skill at predicting
exactly the events the truth sources are worst at seeing. The observation
problem is therefore hardest precisely where the forecast is most
interesting, and every accuracy figure for rain is limited by the weaker
half.

Two consequences for priority. Item 63 (more reporting stations) is worth
more than it looks, because it attacks the binding constraint rather than the
visible one. And item 46 (asking the reader about yesterday) is the cheapest
high-confidence source available — a person who stood in it outranks a grid
cell, and costs nothing to fetch.

### Where satellite and radar arrive

The ladder is graded rather than fixed precisely so new instruments can join
without restructuring anything, which is the point of choosing this shape now:

- **Radar**, if reachable here, sits between the station and the reanalysis:
  regional like the archive but at roughly 1 km and 5 minutes rather than
  25 km and an hour, so it can see a cell the archive averages away. Not
  currently a roadmap item, and **whether usable coverage exists over Kisumu
  is unchecked** — the same question `p01i` turned out to answer badly.
- **Satellite** is item 41, and is the right answer for basin-scale truth
  rather than point truth. It joins the ladder as reliable-for-occurrence,
  not gold: it sees cloud and inferred rain rate, not a gauge.

Neither changes the design. That is the test this shape had to pass.

### The LLM reads sources; it must never adjudicate them

Raised by the operator as a case where an LLM beats a formula. Half right,
and the halves need separating because one of them is dangerous.

**Where it genuinely wins, and a formula cannot.** Unstructured local signal:
a METAR `RMK` section carrying precipitation notes outside the coded groups,
a met service bulletin's prose, a satellite product's description. That is
this project's real comparative advantage over the numerical models — it can
ingest what they cannot read — and it is exactly the kind of source that
would otherwise need a bespoke parser per format.

**Where it must not go: deciding what happened.** If the forecaster sets the
truth its own blended call is scored against, it grades its own homework.
That is the closed loop `models_visible_to_the_forecaster` exists to prevent,
one level deeper and considerably worse — because a forecaster that can move
the truth improves every number on the accuracy page while none of them
continues to mean anything. It would not present as a bug. It would present
as the project succeeding.

**The test to apply to any proposed use.** Does it produce something a human
could check afterwards against the source? Lifting "RMK RAB35" into a
structured field, yes — the report is right there. Choosing which of two
disagreeing sources was correct, no — there is nothing to check it against,
which is the whole reason this item exists.


### Adopted from the source research, 2026-09-03

Three things from `OBSERVED_REALITY_SOURCE_HANDOFF.md` that this item needed
and did not have. That document's own principles are close enough to this
one's to be worth noting — missing is not zero, source identity matters,
preserve the audit trail — which is some evidence the design here is not
merely locally convincing.

**A source has six states, not two.** "Configured" and "not configured" is
too coarse, and every one of these has been hit already:

```text
EXISTS         a catalogue lists it
OPERATIONAL    the operator says it runs
ACCESSIBLE     a public endpoint answers
FRESH          that endpoint returned something recent
CAPABLE        it measures the variable in question
REPRESENTATIVE it measures it where the forecast applies
```

HKKI is EXISTS through CAPABLE for present weather and fails CAPABLE for
precipitation amount. Kericho is ACCESSIBLE and FRESH and fails
REPRESENTATIVE for temperature, being 820 m higher. The nearest catalogue
station is not automatically the best truth source, and a resolver that
returns one without these states will pick wrongly and look right.

**`METADATA_CONFLICT` is a legitimate stored state.** Aggregate metadata
status, national authoritative status and successful live retrieval are three
different claims, and forcing them into one field destroys the disagreement
rather than recording it. Kenya's radars are the worked example: the WMO
radar database lists two as active while KMD's own metadata reports both
closed since 2021.

**The radar question is closed for this deployment.** Nearest are Nairobi
(~276 km) and Malindi (~688 km). At that range beam height and terrain make
Nairobi a poor low-level precipitation truth for Kisumu even if it were
running, and KMD says it is not. **No dependable local ground-radar truth
source exists here**, and that is a result rather than a gap — item 63's
"unchecked" note is answered, and radar stays geographically optional.

**Satellite is the realistic remote-sensing route here**, not radar. Kisumu
sits under EUMETSAT's Meteosat service: Meteosat-12 / MTG-I1 (FCI)
operational near 0°, Meteosat-11 SEVIRI still running at 9.5°E. See item 41,
which now has named targets rather than an aspiration.

**One caution the research states and this item should inherit: do not
manufacture corroboration.** The same physical station delivered through
METAR and through WIS2 is one observing asset, not two independent
witnesses. Given the OR above is already monotonic, counting one station
twice would inflate the wet-day rate for no reason at all.


Related: item 11 (source discovery in the app), item 35 (convective
disagreement), item 36 (user feedback), item 41 (satellite as a candidate
actuals source — the same decision at basin scale), item 42 (why this item
exists), item 44 (the sources page, which needs the provenance field this
item adds), item 46 (asking the reader when the chain is in doubt).

---

## 46. Ask the reader when the record is in doubt · **Planned**

Proposed by the operator on 2026-08-26. App-first.

Item 36 puts a standing *"was this right?"* line under every forecast. This
is the sharper version: the app asks a **specific closed question about
yesterday, only when the stored record is genuinely uncertain** — "did it
rain here yesterday?", "was it mostly cloudy?" — and stays silent otherwise.

The difference matters. A permanent feedback widget is passive and
self-selecting: people answer when a forecast was wrong, which is the
sample least useful for measuring anything. A question asked on the days
the data cannot settle is targeted, cheap for the reader, and lands exactly
where the archive is weakest.

### When is there doubt

All of these are derivable from data already stored, and each one names a
known failure mode rather than a guess:

- **Sources disagree.** Once item 45 stores more than one reading per
  variable, disagreement is a first-class signal. The 5 convective days in
  item 42 would all have fired.
- **The rain boolean is near its threshold.** `RAIN_THRESHOLD_MM` is 0.5.
  An archive day recording roughly 0.2–1.0 mm is close to a coin flip, and
  the boolean it produces carries the whole accuracy record for rain.
- **No observation was available.** `thunder is None` on a day the models
  disagreed about convection (item 35) is the case where nothing in the
  system knows what happened.
- **A model broke from the pack.** One model calling rain against four is
  either skill or noise, and the record cannot yet tell which.

### Form

One tap, a closed set of options, about yesterday only. Free text stays
optional and secondary — item 36's reasoning holds: elaborate feedback goes
unused. Asking about yesterday rather than now also keeps it answerable;
people remember whether it rained, not what the peak gust was.

### The toggle, and the frequency ceiling

A hard off in settings, as asked. But the toggle is not the real control —
**frequency is**. A prompt that appears daily trains people to dismiss it,
and a dismissed prompt is worse than no prompt because it looks like
consent. Cap it well below one ask per day, prefer streaks of silence, and
treat repeated dismissal as an answer in itself: stop asking.

Whether it defaults on or off is open. On is defensible only because the
question is rare by construction.

### What it is allowed to affect

Not the published accuracy record. Item 36's constraint is inherited
verbatim and is not negotiable: the figures on the accuracy page are
deterministic and reproducible from the stored log, and that is the whole
of their value. Human reports are stored and presented separately, always.

What it *may* feed:

- the reader's own view — their reports against what the record says
- flagging days for the operator's weekly review (item 18)
- item 35's convective disagreement, which is the pattern this is most
  likely to expose

### Why the app is the right surface

Item 36 lists "it needs somewhere to go" as a hazard, because the pipeline
is git-as-database with no inbound path and accepting input breaks the
zero-infrastructure property. In the app that hazard mostly disappears: an
answer can stay on the device and steer that user's display without ever
being transmitted. Anything that does leave the device is a second
decision, taken later, with item 36's personal-data constraints attached —
location plus timestamp plus free text is enough to identify a routine.

Related: item 16 (the app), item 18 (weekly review), item 35 (convective
disagreement), item 36 (the general case this narrows), item 42 (the miss
that a reader caught first), item 45 (source disagreement as the trigger).

---

## 47. A nearby observing station is the single biggest accuracy lever a reader controls · **Planned, app-first**

Item 42 established that reanalysis does not see thunderstorms, and shipped
`observed_convection` so a day the airport watched a storm on is no longer
scored dry. That fix only works where an ICAO code is configured. Everywhere
else `thunder` stays null, `observed_convection` collapses back to reanalysis
precipitation, and the models that call convection best go on being marked
wrong for it.

So the accuracy record's quality is gated on a piece of configuration most
readers do not know exists, and nothing anywhere tells them so.

### What a station actually changes

Not a decoration on the forecast — a change to what the forecast is SCORED
against, which compounds. Every day without one is a day the record cannot
distinguish "the models were wrong" from "the grid cell did not notice".

Concretely, from Kisumu on 2026-08-22: thunder over the city, little measured
rain, and every convective model penalised for calling it right. Without a
station that day is a permanent, invisible defect in the record.

### The two halves

**Tell them it matters.** Both products currently present source
configuration as setup, at the same weight as a timezone. It is not: it is
the one input a reader controls that improves the record for as long as they
keep using the product. The sources screen (item 44) is the natural home, but
this has to reach someone who has not gone looking — onboarding in the app,
and the setup docs on the site.

**Make it findable.** Asking someone for an ICAO code is asking them to know
what an ICAO code is. A reader who has just entered a location has already
given us the input needed to find the nearest reporting stations — the search
should be ours to do, offering candidates by name and distance with a plain
statement of what each one would add. Multiple stations should be allowed:
one airport 40 km east is better than nothing and worse than two.

METAR is the obvious first network because it is free, global, standardised
and already parsed on the Python side (`fetch/metar.py`). It is not the only
one, and the design should not assume it is — the question a reader is
answering is "what real observations exist near me", not "what is your
airport".

### Constraints this inherits

- **Item 45 decides the classification.** A station is an OBSERVATION source,
  a truth candidate the record is measured against — not another model to
  score. Getting that backwards contaminates the record in a way that is hard
  to unwind, so this cannot ship before that question is settled.
- **Distance has to be shown, not hidden.** A station 80 km away across a lake
  is not the reader's weather, and presenting it as "your local station"
  would put confident wrong observations into the truth column.
- **Absence stays honest.** Somewhere with no nearby station must say so
  plainly and keep working, exactly as `thunder: null` already does. The
  fallback is the current behaviour, not a degraded one.

Related: item 11 (source discovery), item 42 (why this matters at all),
item 44 (the sources screen), item 45 (observation vs prediction).

---

## 48. Prompt review: what the 2026-08-27 pass found · **Shipped**

A full read of `llm/prompt.py`, prompted by the observation that the prompt
has changed with nearly every revision and needed checking as a whole rather
than patch by patch. Three findings shipped that day (items in 597b8d1 and
635bbab). All seven findings have now shipped — 1, 4, 5 and 6 in 7613f94, and 2, 3 and 7
in the follow-up. Finding 2 shipped in substance rather than to the letter; see
its entry for what was deliberately not done.

Do this periodically. The individual tweaks are each defensible and the drift
they produce collectively is not visible from any one of them.

### 1. The re-issue block contradicts the Overview spec · **Shipped** (7613f94)

The Overview section says "OPEN with the day-over-day comparison". The
re-issue block says "Open the Overview with what has changed since the last
issuance, and with what is still AHEAD". On a later issuance the model
receives two mandatory openings and has to choose.

Fix: the re-issue instruction should REPLACE the day-over-day opening, not sit
beside it. Yesterday-vs-today is a first-issuance frame; by 22:00 the reader's
reference point is this morning's forecast, not yesterday's weather.

### 2. Emphasis is spent · **Shipped, in substance**

Roughly twenty ALL-CAPS directives. "USE rain_contrast VERBATIM" is
typographically equal to "the record does not yet support ranking models".
When everything is shouted the model infers priority from position and
recency, which favours whatever was patched last.

The Overview enumeration bug was the symptom: an explicit collapse instruction
lost to an example sitting beside it. That is instruction saturation, not a
bad example.

Restructure into three tiers, caps reserved for the first:

1. Inviolable — what may not be asserted. Don't recompute, don't rank without
   a finding, don't upgrade confidence, don't invent. Six rules or so.
2. Section contracts — what each heading must contain.
3. Style — phrasing, units, tone. No caps at all.

Related: "do not recompute" appears eight times in different words. Once, as a
principle with a list of the pre-computed blocks, would be shorter and
stronger.

**What shipped, and what did not.** A tier-one block now opens the prompt: six
numbered rules stated to outrank everything below them, covering what may not
be CLAIMED rather than how to write. "Do not recompute" went from eight
scattered injunctions to one, with the list of pre-computed blocks attached —
the count is now literally one mention in the file. Editorial directives lost
their capitals so the tier-one rules keep theirs; correctness and honesty
directives kept them.

NOT done, and deliberately: the full three-way split of the body. Section
contracts and style guidance are still interleaved inside each heading's
parenthetical, because separating them means rewriting every section wholesale
and the risk of silently dropping a rule outweighs the tidiness. The emphasis
problem — which is what the finding was actually about — is addressed by the
tier-one block and the de-capping. Revisit the split only if a later review
finds instructions still being lost.

### 3. Block ordering works against the model · **Shipped**

The user prompt runs ISSUED, HOURS AHEAD, verification, track record,
historical notes, AQI x3, instability, day-over-day, TODAY'S GUIDANCE,
extracted predictions, review, bulletin.

The weather sits in the middle, where attention is weakest, and statistics
ABOUT PAST FORECASTS get primacy over the data this forecast is about. Put
today's guidance and HOURS AHEAD adjacent near the top; move verification,
track record and review to the end, where recency helps them — they inform
confidence language, which is written last anyway.

### 4. No uncertainty-expression rules · **Shipped** (7613f94)

The prompt says "state the spread" for CAPE and pressure and never governs how
certainty reaches the reader. Nothing stops `onset_window: "13:00-16:00"` when
the models span 11:00 to 19:00.

Elaborate machinery exists to avoid overclaiming about MODEL SKILL, and
overclaiming about TODAY is ungoverned. Require the onset window to reflect
actual model spread, and forbid a single time unless the models agree within a
stated band. Note that `onset_hour` (635bbab) now makes the overclaim
scoreable, which is the first time this has been measurable.

### 5. The learning loop is two-thirds closed · **Shipped** (7613f94)

The system learns through the track record, the verification notes fed
forward, and the review findings. The prompt exploits the first and third
hard. It WRITES the second — "these get stored back and read as context in
future runs, so be specific" — and then never instructs the model to use them.
`HISTORICAL NOTES` is passed with no directive attached.

Add one: before finalising, check whether today's setup resembles a documented
past miss, and say so. Cheapest high-value change available.

### 6. No general negative-space check · **Shipped** (7613f94)

Item 42's thunder miss was "the data supported a statement nobody made". The
fix was a hard-coded CAPE flag — a patch for one instance of a general class.
A closing instruction ("every provided block appears in the output or is
explicitly noted unavailable") generalises it and would have caught the
original miss without a bespoke flag.

### 7. Over-constraint: phrasing is governed, judgement is not · **Shipped**

Several verbatim rules are really one rule — "do not contradict a computed
value". Stated once as a principle, the model could phrase `rain_contrast`
naturally instead of pasting a fragment into the most-read sentence in the
product. Watch for every Overview reading identically across a week; that is
the cost of the current approach.

The persona also fights itself: "Lead Synoptic & Regional Meteorologist"
followed immediately by "your role is narrower than it might look".

**Shipped.** The role now states what the job IS rather than what it is not:
the arithmetic is done, and what is left — reconciling models that disagree,
deciding which to believe today, judging what a reader needs — is the reason a
forecaster is in the loop at all. "Narrower than it might look" is gone; it
framed the most valuable half of the work as a restriction.

### Not in this list, tracked elsewhere

- **A persistence baseline.** Nothing scores "same as yesterday", which in
  the tropics is a strong baseline. If the ensemble cannot beat it, that is
  the most important honesty fact about the product and nothing could
  currently reveal it. Scoring change, not a prompt change.
- **Climatology.** No seasonal normal is available, so "32 C" cannot be
  reported as "3 above normal for late August" — context readers value and
  the record has no source for.
- **Unscored prose.** Synoptic reading, hazard calls and boater conditions
  are asserted and never verified. Item 47's stations make the hazard half
  scoreable; the rest is item 46's territory.

---

## 49. A slot that arrives late enough is a different run · **Shipped — one guard outstanding**

Found on 2026-08-28, live. Four `forecast.yml` runs were created between
00:22:32Z and 00:37:28Z — three to six minutes apart, and hours from any slot
the file declares (03:07/22/37/52 and 15:07/22/37/52 UTC). GitHub does not
fire a schedule early, so the likeliest reading is that these were the
previous day's 15:0x slots draining. That is the third measured shape of
scheduler failure on this repo; `ops/README.md` carries all three.

**The consequence is not lateness.** Crossing UTC midnight, those runs stopped
being yesterday's re-issue and became **today's first run**, at 00:27 — which
is the run that owns verification and the day's `model_predictions`, taken
before the 02:00 UTC window in which the models are aligned. The record is
honest about it (`generated_at_utc`, `trigger_source: schedule`), and the
operator's own 03:01 dispatch then correctly skipped a day already done. But
the numbers tomorrow scores came from a cycle nobody chose, and the 03:07 slot
— the one picked from a measured availability table — contributed nothing.

The stale-checkout fix in the same incident is unrelated to this and already
shipped; this is the half that remains.

### Why the pipeline cannot currently tell

A run has no idea which cron slot produced it. `github.event.schedule` names
the cron expression that fired, but nothing in the payload distinguishes "this
slot, on time" from "this slot, nine hours late" — the event carries no
intended-fire timestamp, and the run's own creation time is the delivery time,
not the schedule's. So the pipeline sees only "a trigger arrived at 00:27".

### Three options, none chosen

1. **A "not before" bound on the first run of a day.** Config gets an hour
   before which a first run is refused (say 02:00 UTC, when the aligned window
   opens), and a trigger arriving earlier returns `ForecastSkipped`. Cheap,
   testable, and in the pipeline where every guard now lives. The cost is a
   real one: a day whose only trigger arrives at 00:27 then gets NO forecast
   at all until the next one, and "no forecast" is worse than "a forecast from
   an early cycle" for a reader. It also needs care at high latitude and in
   timezones where the local morning sits either side of UTC midnight — the
   bound is a statement about model cycles, which are UTC, not about the
   reader's morning.
2. **Pass the intended hour into the run.** `forecast.yml` could hand each
   slot its own expected time (a per-cron input, or a matrix), and the pipeline
   could refuse a run arriving more than N hours from it. Distinguishes late
   from early correctly, and does not blank a day: a late slot simply becomes
   a re-issue if the day already has an entry. Costs a second place where the
   schedule is written down, which is the coupling item 34 just removed.
3. **Accept it, and let the record show it.** A first run at 00:27 is a worse
   forecast than one at 03:07, not a wrong one. The archive already carries
   the hour, and the accuracy record scores what was published. The operator's
   crontab — which fires on time, being an ordinary cron daemon — is the real
   answer to GitHub's scheduler, and this only bites when the crontab is not
   the trigger that got there first.

Option 3 is where this sits today, by default rather than by decision. Worth
settling once the crontab is switched to `forecast.yml`, since that changes
how often the scheduler is the trigger that wins.

**Progress 2026-08-28: the evidence this needs is being built.** Deciding
between the three options above meant guessing how bad a late run's data
actually is. `cycle.aligned_cycle_at` now answers it in code: the 00:27 run
was working from 12z guidance initialised 12.45 hours earlier. Once each
issuance records its own data age, the choice stops being a judgement about
GitHub's scheduler and becomes a question with a column of numbers behind it.

**Do not "fix" this by widening `MIN_REISSUE_INTERVAL_MINUTES`.** That guard
answers "is this trigger a repeat of the last one", and a run nine hours late
is genuinely not a repeat. Two questions, two guards.

### Settled 2026-08-29: option 4, delete the slots

It happened again the night the crontab was finally pointed at
`forecast.yml`, which made the cost legible. Four schedule runs 23:59:53Z–
00:09:21Z; the first wrote `forecast: 2026-08-29` at 00:06:05Z on 12z
guidance, age 12.03 h; the crontab dispatch fired on time at 03:01:04Z into
the 02:00 UTC window and could only re-issue the narrative, because the
numbers were already written. Twice in two days the day's scored predictions
came from the trigger nobody chose.

The measurement that decides it is the whole history of those four 03:0x
crons, under this file and `daily.yml` before it with identical entries:
33–52 min late on 08-26, ~11 h late on 08-27, a cluster around midnight on
08-28 and 08-29. Not once punctual. The 15:0x family has never been more
than ~35 min out. Attribution of a run to a declared slot is inference —
GitHub exposes no intended-fire time — but the arrival times are not. A
backstop that is never on time for the run that matters is not redundancy,
it is a second scheduler competing to own the day.

So: **`forecast.yml`'s `schedule:` block was removed.** The crontab is the
only trigger. What that gives up is real and was the block's whole purpose —
a dead server, a rotated token or a broken cron line now produces silence,
and `health_check.yml` only looks weekly. The trade was made deliberately:
the mailer sends on novelty, so a surplus issuance is a surplus email to
every subscriber, and for a beta with real readers one silent morning costs
less than spam.

Options 1 and 2 above are not wrong and are not built. They stay written
down because anyone who puts a `schedule:` block back — a fork with no
external trigger, which is the documented reason to — inherits the same
problem, and option 1 is the cheap half of the answer.

**What is still unguarded.** Nothing in the pipeline refuses to open a day
on stale guidance. Removing the block removes the only trigger that has ever
done it here, not the possibility: a crontab firing late, a server in the
wrong timezone, or a hand dispatch at the wrong hour would do the same. That
is option 1, still unbuilt, now the only part of this item outstanding.

**Why the lost backstop is acceptable today, stated so it can expire.** The
operator is watching the daily runs directly during the beta, which turns a
silent miss from a gap into a reading — the thing this repo otherwise has no
way to measure is how often the crontab alone actually delivers. That holds
only while someone is looking. When the beta ends, or attention moves, the
weekly `health_check.yml` becomes the only watcher again, and nothing in it
asks "did today's run happen at all" — item 51 is the same blip-versus-death
problem one layer out, applied to sources rather than to the trigger, so the
frame exists and the check does not.

A fork gets none of that attention and should not inherit this deployment's
trigger either way — item 52.

---

## 50. An issuance is justified by fresher data, not a later clock · **Partly shipped**

The original design had a "morning" forecast and an "evening" refresh, and
that framing survived in the code long after the runs stopped being tied to
those hours. Item 34b removed the last place the OPERATOR had to pick by time
of day. This item removes the last place the SYSTEM thinks in those terms.

The reason a second run exists was never that it is evening. It is that the
data is fresher. Nothing in the record said how fresh, so the pipeline could
not state its own justification for re-issuing, and a run on twelve-hour-old
guidance looked exactly like a run on fresh guidance.

**Shipped 2026-08-28:**

- `cycle.aligned_cycle_at` infers the cycle from the measured aligned-window
  table, ported to Dart and vector-locked. An inference, and it says so. Read
  it as a FLOOR on the age of the guidance: a window stays clean about two
  hours before the faster models jump ahead, so outside that the cycle named
  is the one the SLOWEST model is still on.
- `fetch/model_run.py` observes the real thing for `ecmwf_ifs025`, the one
  model of the five that can answer — the other four are blends and return
  HTTP 500, having no single run to report. ECMWF is the right one to ask
  anyway: measured slowest to publish, so its newest run is in practice the
  newest cycle every model has.
- An observation counts only once it has SETTLED (`RUN_SETTLE_MINUTES`), on
  the provider's own eventual-consistency recommendation. Unsettled falls
  back to the derived floor rather than claiming data we may not have
  received.
- Each issuance stores its own `guidance_initialised_at`, `guidance_age_hours`
  and `guidance_source` — which is why item 32 had to land first. A re-issue
  archives the outgoing issuance's recency along with its narrative.
- Observed and derived are compared on every run, and a disagreement prints a
  warning naming both. The table is a one-time hand measurement from
  2026-08-11; this comparison is the only thing that would ever say it had
  drifted.

Validated twice against live metadata: at 2026-08-28T00:27Z derived said 12z
and ECMWF's 18z was not yet available; at 09:29Z both said 00z.

**Shipped 2026-08-28, part two — the prompt states it.** GUIDANCE RECENCY
carries `models_last_aligned_at`, `hours_old`, `source` and
`newer_than_previous_issuance`. The key is named for the FLOOR so a reader of
the prompt cannot mistake it for a claim about every model, and the system
prompt says to state it as "the models were last all on the same cycle at X",
never "the data is from X". When no newer cycle has landed since the forecast
being updated, the model is told that plainly — an honest "no new model
guidance since this morning" is a better update than a paragraph rewritten to
look like news.

Three edge cases have guards, each with the reasoning attached:

- **An impossible age** (a cycle initialised in the future) means this
  machine's clock is wrong or the provider said something impossible. The
  record keeps the anomaly; the prompt is told the recency is unknown rather
  than handed a negative number to narrate.
- **A cycle OLDER than the previous issuance's** means this run fell back to
  the derived floor while the last one had a real observation — we know less
  than the run before us did. That is not "no new guidance" (which licenses a
  short, quiet update), it is no basis for comparison, and `null` says so.
- **Cross-language rounding.** `hours_old` is rendered into a prompt whose two
  versions are pinned character for character, and Python's `round(x, 1)`
  rounds the DECIMAL expansion of a binary float in a way no reasonable Dart
  implementation reproduces. Measured over a 0.05-step sweep from 0 to 240
  hours: **962 of 4801 values disagreed**, including 0.05 and 9.55. Both
  sides now run identical IEEE-754 arithmetic —
  `cycle.round_hours_to_tenths` / `roundHoursToTenths`, vector-locked with the
  tie values as its cases — and the same sweep gives 0 divergences. Caught in
  review of the port, not by the vectors, whose sampled points happened to
  agree.

**Remaining:**

1. ~~**Tell the model.**~~ **Done, above.** For the record, the original note: The prompt still cannot say why this issuance exists.
   It should state the age as a floor — "the models were last all on the same
   cycle at X", never "the data is from X" — and on a re-issue, whether that
   is newer than the previous issuance's. When it is NOT newer, the honest
   line is that no new guidance has landed, which is far better than
   manufacturing change. Shared prompt, so Python then Dart then vectors.
2. ~~**Surface the drift warning where someone reads it.**~~ **Done
   2026-08-28.** For the record, the original note: Today it prints into a run
   log. `check-health` is the surface that already exists for "something that
   only rots slowly", and re-measuring the table stays a manual act the
   warning exists to prompt.

   `health_check.check_aligned_window` runs the same comparison weekly and
   **fails the check** when the two disagree — a green log nobody reads would
   have moved the problem, not fixed it, so the notification is a red job.
   Three outcomes, not two: a metadata endpoint that says nothing is reported
   as NOT CHECKED and does not fail, because silence is not evidence the table
   is still right. The pipeline keeps its own stderr line: that one records the
   disagreement at the moment it actually affected a forecast.

   The model to ask and the settle rule moved to `fetch/model_run.py`
   (`fetch_settled_run`), so the two surfaces cannot drift on either.

   Worth stating because it is easy to miss: the app makes no metadata
   request at all, so every forecast it issues states the DERIVED floor
   (`app/olw_core`'s `forecast.dart`). The table being right matters more
   on that side than on this one, and this check is the only thing watching
   it there.

   **A boundary caveat, measured.** ECMWF's availability delay varies more
   than the whole hour the windows are rounded to — ~7.1 h on 2026-08-11,
   8h25m for 18z and 7h46m for 00z on 2026-08-28 — so within about an hour of
   a window opening the observation legitimately runs a cycle ahead of the
   table (published early) or behind it (not landed yet). The verdict is
   unchanged there, but the message says so, because re-measuring the table by
   hand is real work to send someone on for nothing. The 04:17 UTC weekly slot
   sits over two hours inside an open window and is clear of all four
   boundaries.

   Verified: 644 Python tests, and the check driven against the live endpoint
   at 2026-08-28T10:22Z — derived 00z, observed 00z (available 07:45:55Z),
   AGREES. NOT verified: no `check-health` run has yet gone red on this, and
   the weekly job has not run with the check in place; the drift path is
   proven by tests and by the exit code, not by a real disagreement.
3. **Settle item 49 with the evidence.** The choice about late slots was
   blocked on not knowing how bad a late run's data actually is. It is now a
   recorded number per issuance. Settled 2026-08-29, on the second day those
   numbers said 12 hours: the `schedule:` block was deleted rather than
   guarded.

**Deliberately not done:** guessing the raw-model mapping for the other four.
`gfs_seamless` resolves to `ncep_gfs013` for near-term hours here, and the
seamless names resolve differently by LOCATION — icon_d2 and icon_eu do not
cover Kisumu. A mapping that is right for this deployment and wrong for a
fork does not belong in a permanent archive.

---

## 51. Tell a blip from a death · **Planned**

Raised by Conor on 2026-08-28, from the third outcome in item 50's
aligned-window check: "we're relying on a lot of free/open sources here, and
some that may change without notice — there's nothing telling your local
airport or forecast service that they can't change their URL, or even that
Open-Meteo can't change or pass on changes from upstream. Or that they need
to have more than a few nines, if any as we see with AQI data, of uptime. So
all that to say, how we handle both temporary failures, and long-term
failures, has to be resilient and aware of the difference."

Every best-effort source in this project is handled tolerantly: the fetch
returns None, the run continues, the record stores the absence, and the
prompt says what is missing rather than guessing. That is correct for a blip
and wrong for a death, and **nothing currently tells them apart.**

### The case that proves it, found the same day

Sun times have failed on every production run for six days (item 39). The
degradation worked perfectly each time. Nobody knew. The published forecast
lost a feature within a day of gaining it, and the only reason it surfaced
was an unrelated read of the log file.

Item 25 already established the principle for model VARIABLES — tolerance
keeps the system running, visibility is a separate property, and absence
that propagates cleanly is absence nobody investigates. This is the same
lesson one layer out: it applies to whole SOURCES, not just to fields within
a source that answered.

### Two questions, two mechanisms

- **"Did this fetch fail?"** is per-run, belongs in the driver, and is
  already answered: best-effort, never fatal. Nothing to change.
- **"Has this source been failing?"** is a property of the record over time.
  `coverage.py` already answers exactly this shape twice — `detect_coverage`
  for a variable that stopped arriving, `detect_trigger_regression` for a
  trigger that died while output carried on — and both derive it from the
  committed log with no new storage and no extra fetch. That is the pattern
  to extend, not a new subsystem.

Surface it through `check-health`, which item 50 just established as the
place for things that rot slowly, under the same rule that check follows: a
blip must not turn the job red, a death must.

### The real work: the record stores the absence, not the reason

`sunrise: null` looks identical whether the fetch timed out, the response
shape changed, or the location is in polar night — and the third is a
correct answer, not a fault. `ground_aqi: []` is a WAQI outage and a station
with nothing to report. Until a run records WHY it has no value, a
record-based check can only count nulls, and counting nulls would have
alarmed on Longyearbyen.

So the sequence is: record the reason, then count consecutive failures, then
report. Not the other way round.

**Where the reason lives is not decided.** A per-run `sources` block
(`{name: ok | absent | failed}`) is one place to look and one thing to write;
a sibling field next to each optional value keeps the reason beside the
thing it explains. The first is probably right, but the second survives
schema drift better. Settle it when building, not now.

### Thresholds are per-source, because "normal" differs

A WAQI station going quiet for two days is routine (item 4 documents how
routine). A daily met bulletin missing twice is not. Sun times were the third
example here and are no longer a source at all — item 39 computes them, which
is the other way to fix a source that should never have needed a network.
Worth remembering when setting a threshold: sometimes the answer is to remove
the fetch rather than to watch it. `COVERAGE_ABSENT_RUNS = 3` is the existing
precedent for "more than one, because a single failed fetch is noise" — a
starting point per source, not a global.

### The specific debt this pays off first

`check-health`'s aligned-window check reports NOT_CHECKED and exits 0 when
the metadata endpoint says nothing, because a blip must not go red. A
permanently dead endpoint therefore stays permanently green. The record
already holds the fix: every issuance stores `guidance_source`, so "the last
N issuances all fell back to `derived`" is the failing condition, and it
needs no new state.

Blocked on rows: 0 of 18 entries carry `guidance_source` as of 2026-08-28,
the field having shipped after that day's only run. Item 49 no longer waits
on this — two issuances were enough to settle it.

Related: items 4, 25, 39, 49, 50.

---

## 52. A fork should choose its own trigger, not inherit this one · **Planned**

Deleting `forecast.yml`'s `schedule:` block (item 49) fixed this deployment
and made a fork's first day harder: a fork now inherits no trigger at all.
The setup burden moved from "nothing to do, and it fires at the wrong time"
to "read `ops/README.md` and pick a path" — more honest, still a wall of
prose that ends in one deployment's two crontab lines.

**Those two times are not copyable, and nothing says so loudly enough.**
03:01 and 15:01 UTC are two decisions wearing one number. Half of it
travels: both sit inside an aligned model window, and model cycles are UTC,
so that reasoning holds anywhere. Half of it does not: they were picked to
land near 06:00 and 18:00 EAT. A forker in Lagos pasting them gets a 04:01
local forecast; one in Suva gets 15:01. Both still publish, which is why
this is worth an item — the failure is a reader opening the page before the
day's forecast exists, and from the operator's side that looks like nothing
at all.

**What would count as done**

- One command that answers the question actually being asked. `olw schedule
  --at 06:00` reads the configured location's timezone, prints the UTC times
  inside an aligned window that land nearest it, and emits the paste-ready
  artefact for each path: a `schedule:` block, cron-job.org's field values,
  crontab lines. `cycle.py` already holds the window table; this reads it
  forward instead of the operator reading it by hand.
- The derivation living once, where the answer is generated, instead of
  three times in prose (`ops/README.md`, `forecast.yml`'s header comment,
  QUICKSTART step 9).
- QUICKSTART step 9 collapses to: run the command, paste the output, done.

**What this is not.** Not a scheduler, and not configuration the pipeline
reads at run time. The trigger stays outside the repo — being outside is
what makes it route around GitHub's scheduler at all. This generates the
thing an operator installs; it does not own or execute it.

**The open question, and why this is Planned rather than Next.** How much of
the window logic belongs on this side. `olw_core`'s `cycle.dart` carries the
same table (item 50), the two are vector-locked, and a generator here is a
third reader of a table that must not drift. Worth settling before writing
it, not after.

---

## 53. A silent fetch failure became an all-clear · **Partly shipped — 4 of 6**

Found 2026-08-30, from a reader who was rained on while holding an email
that said the evening was dry. The first big convective miss this project
has had, and it is three separate defects stacked, each of which would
have been survivable alone.

Kisumu, 2026-08-29. A pop-up convective shower, no thunder, roughly one
hour from ~18:45 EAT. The day's three issuances:

| Issued (EAT) | Guidance | Headline |
|---|---|---|
| 03:06 | 28th 12Z, 12.0 h | **Dry / Elevated Evening Thunder Potential** |
| 06:04 | 28th 18Z, 9.0 h | Dry / No Rain — CAPE "unavailable" |
| 18:04 | 29th 06Z, 9.0 h | Dry / No Rain — CAPE "unavailable" |

The 03:06 issuance called it, on the OLDEST guidance of the three:
*"Thunder is possible this evening, peaking around 18:00 as model
instability guidance diverges."* The two later issuances erased it. The
system knew, then unknew, and the reader got the unknowing.

### Defect 1 — one fetch fails, and nothing says so

`fetch_forecast_hourly_forward` timed out on three consecutive runs
(08-29 03:01Z, 08-29 15:01Z, 08-30 03:01Z):

```
Forward hourly window unavailable (Request to
https://api.open-meteo.com/v1/forecast failed: HTTPSConnectionPool(
host='api.open-meteo.com', port=443): Read timed out. (read timeout=30));
continuing without it.
```

`_get` had already spent its 3 attempts × 30 s. **There is no loss of
data at the source.** Measured 2026-08-30 from a developer machine
against the same endpoint: `forecast_days=1` and `forecast_days=2` both
return HTTP 200 in ~1.1 s with a full CAPE series on all five models.
The archive endpoint is the one that is useless (below); the forecast
endpoint is fine.

What makes this specific rather than general flakiness:
`fetch_forecast_hourly_today` — **same host, same endpoint, same
`HOURLY_FORECAST_VARS` including `cape`, differing only in
`forecast_days=1` against `=2`** — succeeded in all three runs. It was
the only failure in each run. Why that one request shape fails from a
GitHub runner and not from here is NOT established; the payloads are
9 KB and 15 KB, so size is not an obvious explanation.

The cost is not CAPE alone. The whole HOURS AHEAD block is lost, so the
evening prompt carried `Unavailable this run.` where the hour-by-hour
guidance for tonight belongs — which is why that narrative reads like a
generic day.

**The data was in memory the whole time.** `primary_hourly` is fetched
successfully in the same run and carries `cape` for all 24 hours of the
local day. `summarize_instability` is simply never offered it.

### Defect 2 — the prompt turns a gap into a reassurance

`summarize_instability` returned `None`, which is correct and is what
`instability.py`'s docstring exists to guarantee: absent CAPE must read
as a gap, not as a calm afternoon. The narrative then wrote:

> "Model convective instability guidance (CAPE) was unavailable for this
> cycle. **Under stable synoptic conditions and limited atmospheric
> moisture, no thunderstorm or severe weather hazards are anticipated**
> for the basin tonight or tomorrow."

The code half of the contract held. The prompt half has no rule
forbidding the model from filling the hole with an all-clear, so it
reasoned from absence of evidence to evidence of absence — in the one
section a reader checks before going out on the water. Both the 06:04
and 18:04 issuances did this, in near-identical words, so it is the
model's default behaviour and not a one-off.

**What the reader should have been told.** CAPE forecast for 2026-08-29
evening, retrieved 2026-08-30 via `past_days`:

| Hour | GFS | ECMWF | ICON | UKMO | Best Match |
|---|---|---|---|---|---|
| 18:00 | 110 | 50 | 250 | **1830** | 20 |
| 19:00 | 90 | 80 | 430 | 560 | 770 |
| 20:00 | 80 | 120 | 450 | **1430** | 680 |

`convective` would have been true, `peak_hour` 18:00, two models above
the 1000 J/kg threshold — and the Overview clause is MANDATORY in that
case. Caveat: `past_days` serves whatever cycle is current now, not
necessarily the 06Z cycle the 18:04 run held. The corroboration is that
the 03:06 issuance, on 12-hour-old 12Z guidance, independently named
18:00 as the peak.

### Defect 3 — the miss is scored as a success

Open-Meteo's archive records **0.0 mm for 2026-08-29** and does not show
the outflow at all (29.9 °C at 18:00 local, 25.8 °C at 20:00). Kisumu
Airport, four kilometres away:

| Time (Z) | Report |
|---|---|
| 13:00 | `22007KT 9999 FEW029 32/10 Q1015` |
| 15:00 | `24010KT 9999 FEW027CB SCT028 30/16 Q1015` |
| 16:00 | `15010KT 9999 **-RA** FEW024CB SCT090 27/18 Q1017` |
| 17:00 | `34005KT FEW020CB BKN080 22/18 Q1018 **RERA**` |
| 18:00 | `33006KT FEW020CB BKN080 23/18 Q1018 **RERA**` |

Dewpoint 10 → 16 → 18, temperature 32 → 22, pressure 1015 → 1018, wind
220° → 240° → 150° → 340°. A full outflow reversal.

**Item 42's backstop does not catch this.** `observed_convection()` is
`rain or thunder`; `THUNDER_GROUP` matches `TS` only. This event had no
`TS` — the reader confirmed no thunder was heard, and the reports agree.
So `rain` is False from the reanalysis, `thunder` is False from the
station, and the day scores DRY. Item 42 fixed thunderstorms and left
rain-without-thunder falling through the same hole.

The consequence is worse than a lost data point. This morning's
issuance told readers:

> "all evaluating models correctly verified dry conditions for their
> target dates"

Every model that called the day dry banked a win for a day it rained,
and those rolling figures are the same ones the prompt cites when it
decides whom to trust ("ECMWF 100% rolling Day+0 rain verification").
Each convective miss makes the models' dry bias look better evidenced.
The `-RA` and `RERA` groups are sitting in reports the parser already
downloads.

### The archive's CAPE is not an escape route

Re-confirmed 2026-08-30, and worse than item 42 recorded. Item 42 says
the archive "returns the variable and fills it with zeros at this
location". It now returns the `cape` key with **no values at all** —
`n=0` over 2026-08-24 to 08-29. Either way it cannot be used for
verification, and the forecast endpoint remains the only CAPE source.

### What would count as done

Separable, and worth doing in this order — the first three are hours of
work and the last is weeks.

1. **The METAR archive parser reads precipitation, not just thunder.**
   **Shipped 2026-08-30.** Extends item 42 rather than waiting on item
   41. `observed_convection` is now `rain or thunder or precipitation`,
   three-valued the same way `thunder` is, and the stored record was
   rescored — the whole point of having chosen an idempotent archive
   source.

   **What it actually moved, measured rather than predicted.** Over the
   45 days stored, the station observed precipitation on 9, and 2 of
   those had been scored dry by BOTH the reanalysis and the thunder
   check: 2026-07-21 (reanalysis 0.9 mm) and 2026-08-29 (0.0 mm).

   | Model | Was | Now |
   |---|---|---|
   | best_match | 89.5% | 84.2% |
   | ecmwf_ifs025 | 78.9% | 73.7% |
   | icon_seamless | 78.9% | 73.7% |
   | kenya_met | 80.0% | 70.0% |
   | gfs_seamless | 63.2% | 57.9% |
   | ukmo_seamless | 63.2% | 57.9% |
   | olw_blend | 100.0% | 50.0% (2 checks) |

   This item predicted before running it that rain-without-thunder would
   be the commoner event. It is not: 14 thunder days against 9
   precipitation days here, and only 2 days moved. The fix is still
   worth having — those two were invisible by construction, and a record
   five points kinder to itself than the truth is exactly what this
   project exists not to publish — but the prediction was wrong and is
   left here rather than quietly edited.

   Also vector-locked in the same change: the scoring truth table across
   both station observations, which item 42 shipped covered by Python
   tests alone. Verified by breaking `observedConvection` in Dart and
   watching `scoring score_prediction` go red.

1a. **The day-over-day phrase still calls those days dry. OUTSTANDING, and
   introduced by 53.1.** `describe_day_rain` takes `precip_mm`, `onset`
   and `thunder`, and decides the phrase from the reanalysis amount with
   thunder as the only override. So 2026-08-29 now scores as a wet day in
   the record and is still described to readers as "dry" — the two halves
   disagree where before they were wrong together.

   That disagreement is the exact complaint that opened item 42: a reader
   told "dry again" about a day they had stood outside in. Leaving it is
   not an option; it was left out of 53.1 only because the fix picks a
   USER-FACING PHRASE, and that is the operator's call rather than a
   mechanical port. `thunder` had "dry but thundery" ready-made; observed
   rain the reanalysis missed has no phrase yet.

   The plumbing is the same shape as `thunder`'s and crosses the same
   three surfaces: `describe_day_rain` gains a fourth argument,
   `day_over_day` passes `yesterday_actual.precipitation`, the
   `describe_day_rain` and `day_over_day` vectors regenerate, and
   `comparison.dart` follows. Suggested phrase, to be confirmed rather
   than assumed: "dry but for a brief shower" where the reanalysis band
   is dry, leaving the wetter bands alone since they already say rain
   fell.
2. **Instability falls back to the day-0 hourly window. Shipped
   2026-08-30.** When the forward fetch fails, the window is trimmed from
   `primary_hourly` instead — same host, same endpoint, same
   `HOURLY_FORECAST_VARS` including `cape`, differing only in
   `forecast_days=1`, and fetched unguarded, so reaching that line means
   it succeeded. No new network call and no new dependency.

   The fallback feeds BOTH the convective outlook and the HOURS AHEAD
   block, which previously read `Unavailable this run.` and cost the
   narrative its hour-by-hour reasoning for tonight as well as its CAPE.

   **The narrowing is declared, not hidden.** `forecast_days=1` stops at
   23:00 local, so an evening run sees this evening and nothing past
   midnight. The HOURS AHEAD header says so — REST OF TODAY ONLY, ENDS
   AT 23:00 local — because a series that simply stops would otherwise
   read as a forecast of a quiet night, which is the same
   absence-is-not-evidence trap as 53.3 one layer down.

   Ported to `olw_core` in the same change: the app's `generateForecast`
   had the identical hole.

3. **A gap must not be reportable as safety. Shipped 2026-08-30.** The
   CONVECTIVE INSTABILITY block no longer says only "Unavailable". It
   says the gap is a gap, and forbids the inference by name — no "no
   thunderstorms expected", no "no severe weather anticipated", no
   "conditions are stable", and no substituting rainfall totals, dry
   synoptics or humidity for a CAPE series that never arrived.

   A standing rule 7, A MISSING BLOCK IS NOT AN ALL-CLEAR, generalises
   it to every block, because the shape is not specific to CAPE: stale
   ground sensors are not clean air either. It is deliberately the
   MIRROR of rule 6 — 6 says do not go silent about a block that
   arrived, 7 says do not draw conclusions from one that did not.

   Both texts are ported verbatim into `prompt.dart` and pinned by the
   shared `llm_system_prompt` / `llm_user_prompt` vectors, which is what
   caught the drift when only the Python half had been changed.
   **A rebuild does not republish.** `olw rebuild-record` writes
   `actuals.json` and `track_record.json` and nothing else, because
   `docs/accuracy.html` is rendered by a forecast run. So corrected
   figures are public in the data and stale on the PAGE until the next
   scheduled run. Measured 2026-08-30: the page showed the
   pre-correction percentages for several hours after the fix was
   pushed. The command now says so on stdout; see the comment in
   `cli._run_rebuild_record` for why it does not render the page itself.

4. **A degraded run says it is degraded. Shipped 2026-08-31.** Three
   silent runs in a row, surfaced only because a reader got rained on. A
   missing HOURS AHEAD block now reaches three surfaces instead of stderr:
   the committed record (`LogEntryMeta.degradations`, and each issuance's
   own copy in `IssuanceSnapshot`), the published page, and `check-health`.

   **Per issuance, not per day.** A degraded morning followed by a clean
   evening is one of each. The outgoing issuance's list is snapshotted the
   same way its `guidance_*` values are, so a re-issue never inherits the
   morning's gaps and never erases them. All three paths into an existing
   day are covered — refresh, forced re-run, and first run — because the
   forced re-run is the one that quietly lost earlier issuances in items 8
   and 34.

   **`fetch_metar` no longer fails silently.** Item 53 had to leave "did
   the 18:04 run reach the station?" under Not established because
   `fetch_metar` returns `None` on every failure path and `airport_metar`
   was never persisted. A configured station that does not answer is now
   recorded. A location with NO station is not — that is a configuration,
   not a degradation, and recording it would empty the field of meaning
   within a week.

   **The field is three-valued, and that was a defect caught late.** `[]`
   means the run looked and found nothing missing; `None` means it was
   never asked. The first version defaulted to `[]`, and running
   `check-health` against the real committed record — rather than against
   its tests, which all passed — had it announce that the last 20
   issuances "had the data they expect", the 2026-08-29 runs among them.
   Absence of a record is not a record of absence; the same rule the
   CONVECTIVE INSTABILITY block and `thunder` already follow.

   **check-health fails on a REPEAT, not on a single gap.** One lost fetch
   is now visible in the record and on the page, and failing the weekly job
   for it would make red the normal colour. A code that comes back twice is
   not bad luck. The threshold is two rather than three, because by the
   third run of the original incident the reader had already been rained
   on.

   **Plain at the top, technical at the end (2026-08-31).** A degradation
   carries two texts, not one. `summary` is what a reader meets above the
   forecast: no jargon, and it says what the gap MEANS. `detail` is the
   technical account and sits in a "Notes on this forecast" section at the
   end. The top of a forecast is where somebody decides whether to go
   outside, and "the forward hourly window did not arrive" tells that person
   nothing they can act on.

   The narrowed-window detail also says WHEN waiting would help, which
   needed item 10's shared half: `cycle.next_aligned_window` and its Dart
   port, vector-locked, plus a 24-hour minute-by-minute sweep proving it
   agrees with `aligned_cycle_at` rather than restating the table a second
   time. Hedged as "usually in by about HH:MM local", never a promise —
   item 50 measured ECMWF's availability varying by more than the hour the
   windows are rounded to, and a notice that names an exact time and is
   wrong twice teaches the reader to ignore every notice.

   All three surfaces carry the split: the page, and both bodies of the
   email (`mailer/AppsScriptMailer.gs`, with harness cases). **The mailer is
   a manual deployment** — the change is committed but does not reach
   subscribers until it is pasted into the Apps Script editor.

   Ported to `olw_core` in the same change, per item 53.2's precedent: the
   app computed the identical narrowing and told only the prompt, so
   `ForecastRun` now carries `degradations` for the app to store and show.
   A bug caught in the port and worth recording: `generateForecast` works in
   the LOCATION's wall clock, so `now.toUtc()` there would have converted
   using the DEVICE's offset — a guess about where the reader is standing
   relative to the place they asked about. `nextGuidanceSentence` takes the
   offset the hourly response already carries, and returns an empty string
   rather than a guessed hour when it is absent.
   Verified: 699 Python tests, 103 Dart, analyzer clean; each new assertion
   watched failing with its fix removed; and the page and the health check
   both driven against the real committed record rather than fixtures.

   **Not done here:** the app's own storage and Today-screen notice, which
   are Ensemble work and sit in that repo's owed table. Ground AQI
   staleness and a bulletin that did not answer are NOT recorded as
   degradations yet — both already have their own handling in the prompt,
   and folding them in without deciding what "stale enough to count" means
   would be guessing.
5. **The current METAR earns a nowcasting role.** Today the prompt's
   only instruction about METAR is a caveat telling the model to
   distrust it. At 15:04Z the newest report was 15:00Z and already
   carried `FEW027CB` overhead with the wind veered 220°→240° and the
   dewpoint up 4 °C. Rain itself appears at 16:00Z, an hour after
   publication — so this run could NOT have observed rain, and could
   have observed convection initiating. Within two hours that outranks a
   nine-hour-old model cycle.
6. **Then item 41 (satellite).** It is still the right answer for
   basin-wide truth. It is not the cheap one.

### Not established

- ~~Why only the `forecast_days=2` request times out from GitHub runners.~~
  **The question was probably wrong. Investigated 2026-09-01; see "It is the
  seventh request, not the second day" below.**
- Whether `fetch_metar` succeeded on the 18:04 run. It returns `None`
  silently on every failure path and `airport_metar` is passed to the
  prompt but never persisted, so the record cannot answer it. That is a
  gap of its own and part of item 53.4.
- Whether Open-Meteo was degraded at those times.
- How many stored days flip under a rain-aware parser. Item 53.1 must
  report the number it actually moves.

### It is the seventh request, not the second day

Investigated 2026-09-01 across the workflow logs, which reach further back
than the degradation field does.

**It is not intermittent.** The forward fetch failed on **8 of the last 9
real runs**. Zero failures in the 13 real runs before 2026-08-29 03:01Z; the
last clean run was 2026-08-30 15:01Z. The degradation record shows only the
recent handful because the field started being written on 2026-08-31 —
`check-health` was reading a true but badly incomplete picture.

**The onset is a step change with no code behind it.** Clean at 2026-08-29
00:06Z, failing at 03:01Z, and no forecast-code commit sits in that window.

**No correlation with anything obvious.** Not time of day (both the 03:0x and
15:0x slots fail), not day of week (failures span Sat–Wed), not runner image
(a clean run and a failing run share `ubuntu-24.04` / `20260823.283`), not
workflow event type.

**The correlation that IS there.** This is the second call to show exactly
this signature, and the two are adjacent in the request sequence:

- `fetch_sun_times` — same host, same `/v1/forecast` endpoint — read-timed
  out on EVERY GitHub run from 2026-08-23 to 08-28 (item 39 recorded it), and
  in those same runs the forward fetch succeeded.
- `55fc556` (2026-08-28 18:32Z) deleted `fetch_sun_times`. The forward fetch
  began failing on the second run afterwards.
- In the old order, sun-times was the **7th** request to
  `api.open-meteo.com/v1/forecast` in a run and the forward call was the 8th.
  With sun-times gone, the forward call is now the 7th. **The failure stayed
  at position 7.** It did not follow the request shape.
- Timing agrees, in both eras. The error is printed 114-122 s after the
  `olw forecast` step begins, and `_get` burns 94.5 s on three timed-out
  attempts, so the doomed request is the one issued ~20-28 s in — position
  7, before and after.

**The confound, stated because it is real.** `fetch_sun_times` also used
`forecast_days=2`, so "position 7" and "`forecast_days=2`" both fit most of
the data. What separates them is one case: in the old era a
`forecast_days=2` request at position 8 SUCCEEDED immediately after a
`forecast_days=2` request at position 7 FAILED. Position explains that;
shape does not.

**What this makes it look like** — a hypothesis, not a finding — is
something that trips on the Nth request to that host inside a short
window: a rate limit that drops rather than answering, or connection
behaviour. Worth noting that the pipeline uses a bare `requests.get` per
call with no `Session` and therefore no connection reuse, and makes seven
`/v1/forecast` requests plus air-quality within about half a minute.

### The experiment, run 2026-09-01 to 09-03

The forward fetch has been moved to **second** in `_fetch_forward_guidance`,
directly after the day-0 hourly call it depends on for the reconciled clock.
Driven for real against Open-Meteo from a developer machine: 8 requests, all
200, 8.8 s total, `forward_window_narrowed: False`.

Counting only `/v1/forecast` requests, the sequence is now:

1. primary hourly (`forecast_days=1`)
2. **forward (`forecast_days=2`)** — was 7th
3. primary daily (`=8`)
4. regional pressure (`=7`)
5. secondary hourly (`=1`)
6. secondary daily (`=8`)
7. **synoptic ring** — was 6th

### THE ANSWER, 2026-09-03: neither. Read the caveat before acting on it.

Four consecutive runs, established from the workflow logs rather than the
record (the record only shows what the pipeline chose to write):

| Run (UTC) | Reorder present | Forward fetch | Synoptic |
|---|---|---|---|
| 2026-09-01 15:15 | no | **FAILED** | — |
| 2026-09-02 03:01 | no | **FAILED** | — |
| 2026-09-02 15:01 | **yes** | ok | ok |
| 2026-09-03 03:01 | **yes** | ok | ok |

**Neither predicted code appeared.** `hours_ahead_narrowed` did not recur, so
the request shape is not sufficient on its own. `synoptic_unavailable` did
not appear either, so the fault did not follow the 7th slot — synoptic now
sits there, is issued ~20-28 s into the run exactly as the forward call used
to be, and is fine.

So both hypotheses in their simple forms are dead. What is left is some
combination — the surviving guess is a LARGE request late in a burst, since
the forward call is ~15 KB against synoptic's ~3 KB — but that is a guess
with no evidence behind it yet.

**THE CONFOUND, AND IT IS SERIOUS.** This fault BEGAN on 2026-08-29 with no
code change of ours in the window. Something that starts by itself can stop
by itself, and two clean runs immediately after our change is exactly what
an unrelated upstream fix would also look like. Under the prior failure rate
(8 of 9) two consecutive clean runs is about a 1 in 80 coincidence, which is
suggestive and is not proof, and this project does not call 1-in-80 a finding
anywhere else.

**What would actually settle it** is reverting the order for one run: a
recurrence convicts the sequence, continued health exonerates it. That costs
one degraded forecast, which is a real cost to a reader, and it is the
operator's call whether the knowledge is worth it.

**The cheaper alternative was taken, 2026-09-03.** Three changes, none of
which requires knowing the cause, chosen after establishing what was already
there — `_get` ALREADY retries three times with backoff, and all three
attempts timed out on every one of the eight failing runs, so more retries of
the same shape buy nothing.

1. **One connection for the whole run.** `_SESSION` replaces a fresh
   TCP+TLS handshake per call across ~8 calls in half a minute. Connection
   churn is a plausible mechanism, this is the cheap half of testing it, and
   a measured side benefit either way: the real fetch sequence went from
   8.8 s to 5.6 s, a third faster.
2. **A second, SPACED attempt at the forward window.** Not more retries in
   the same burst — one more try after the rest of the run has happened,
   which is the only variable the surviving hypothesis says matters. The
   day-0 fallback stays the floor, so a second failure lands exactly where
   one used to, and the degradation is recorded only if both attempts fail.
3. **The diagnostic that was missing all along**: which request, its position
   in the run, and how long it had been going. A recurrence now identifies
   itself on the first run instead of costing another investigation.

**None of these is a claimed fix.** The confound above stands: the fault
began without a code change and could end without one. They are redundancy
and instrumentation, which are worth having whatever the cause turns out to
be.

**The original prediction, for the record.** On the next scheduled run:

- **If POSITION is the cause**, the run records `synoptic_unavailable` and
  NOT `hours_ahead_narrowed`. The failure follows the 7th slot to whatever
  is standing in it.
- **If REQUEST SHAPE is the cause**, the run records `hours_ahead_narrowed`
  as it has for the last eight, and the synoptic ring is fine.
- **If neither appears**, the fault was in the sequence LENGTH or timing
  rather than an absolute position, and reordering merely moved the doomed
  request out of whatever window it was landing in. That is a third answer,
  not a null result, and it is the one that points hardest at pacing.

The synoptic call was made to say when it fails as part of the same change.
It used to be `except Exception: synoptic = None` with no log — invisible on
every surface, the same shape as the incident this whole item is about — and
leaving it silent would have wasted the run that is supposed to answer this.

**This placement is an experiment, not a decision**, and the comment in
`_fetch_forward_guidance` says so. Whoever reads the answer should record it
here and then decide deliberately where the call belongs.

**Nothing failed loudly, by design.** All nine recent runs report success:
the pipeline catches, logs to stderr, falls back and publishes. That is item
53.4 working — and the reason it took a reader getting rained on to notice
the first time.

**Not established:** why, in either direction. There is no evidence about
Open-Meteo's state at those times, no response headers (nothing is logged
beyond the exception string), and the synoptic fetch at position 6 swallows
its exceptions silently so the record cannot say in general whether it fails
too — the timing rules it out for the runs examined, not for all runs.

### This is live, not historical

Measured 2026-08-30 07:0x EAT, from the `forecast_days=1` call the
pipeline already makes successfully: Best Match 2180 J/kg at 22:00, UKMO
1860 at 20:00, ICON 1110 at 19:00 — three models above threshold. This
morning's 06:04 issuance says "no thunderstorm or severe weather hazards
are anticipated". The same failure is mis-forecasting today.

(Also noted: ECMWF returns small NEGATIVE CAPE values at some hours
(−10, −30). `max()` handles it, but anything that sums, averages or
assumes non-negativity must not.)

Related: item 35 (convective disagreement), item 41 (satellite), item 42
(the METAR fix this extends), item 45 (which source is "what actually
happened"), item 25 (detect absent data, don't just survive it).

---

## 54. A signup form, and somewhere for the list to live · **Planned**

Subscribing is currently an act only the operator can perform. The list is
`SUBSCRIBER_EMAILS`, a comma-separated Script Property, read once in
`getConfig()` (`mailer/AppsScriptMailer.gs`). Someone who wants the email
has to ask, and be added by hand.

**This is a regression from the pre-rebuild pipeline, not a gap that was
always there.** `reference/KisumuForecastPipeline_v2.gs` read a
`Subscribers` sheet — `sendEmailBroadcast()` takes the sheet, reads column
A from row 2 down, and filters on `@` — and a Google Form wrote into it.
The rebuild replaced the sheet-backed list with a Script Property and never
replaced the form. Worth stating plainly because it means the design
question was already answered once, and the answer worked.

### The constraint that decides the shape

The public site is GitHub Pages. It is static, so a form on it has nowhere
to POST. Whatever collects addresses is an endpoint somewhere else, and the
options are:

- **A Google Form again.** Zero build. Also zero validation, zero double
  opt-in, and a Google-branded page rather than the site's own — and the
  form URL is the thing that gets shared, not the site.
- **An Apps Script Web App** (`doPost`) deployed from the mailer's own
  project. The shortest path to an endpoint that can write a Sheet, because
  the OAuth authorization and the deployment step already exist for the
  mailer. One project, one place to look when it breaks.

  **And it is already planned.** Item 2's build table has `doPost()` plus a
  Web App deployment and shared-secret auth in the mailer, with the note to
  key it by `type` from the start because item 3 reuses it. Signup would be
  the third consumer of one endpoint rather than a new one — which moves the
  cost of this item most of the way to zero, provided the keying happens as
  item 2 says.
- **An ESP's own hosted form**, which is item 5's territory. If a real
  sending domain lands, subscription management, double opt-in and
  unsubscribe stop being things this repo builds at all. That is the
  strongest argument for doing item 5 first and this second.

### What is missing today besides the form

Both of these are absent right now, and neither is optional once anyone but
the operator can join:

- **No double opt-in.** An open endpoint means anyone can subscribe anyone
  else. A confirmation link is the mitigation, and it needs a `doGet` with a
  token — so the endpoint has to handle two verbs, not one.
- **No unsubscribe.** Both renderings carry the line "You are receiving this
  because you subscribed to this experimental forecast service", and neither
  offers a way out. Adding a way in without a way out is the wrong order.

### Storage

A Sheet, as before. Not the Script Property: it cannot hold per-subscriber
state, which is what item 55 needs, and a single comma-joined value has a
size ceiling. **The exact Script Property and `MailApp` daily recipient
quotas were NOT checked for this write-up** — both are Google figures that
change, and the numbers matter enough that they should be read from Google's
current documentation at the time this is built rather than quoted from
memory here.

Related: item 5 (real sending domain), item 3 (push-based delivery), item 55
(what a subscriber can choose).

---

## 55. What a subscriber gets to choose · **Planned**

One email, every issuance, all of it. A reader who wants the two-sentence
version once a day has to take the full multi-section discussion two or
three times a day instead.

### One half of this is nearly free

The narrative already arrives pre-sectioned. `narrative_markdown` in
2026-08-31's entry carries `## Overview`, `## Today's Forecast`,
`## Extended Outlook`, `## Severe Weather / Hazard Potential`,
`## Lake Victoria — Conditions for Boaters` and `## Detailed Discussion`
(with `### Synoptic Overview` and `### Forecaster Confidence Notes` under
it). `whatsapp_d` is a shorter rendering of the same facts, already
generated, already paid for.

So "just the Overview" is a filter over headings the LLM already emits. No
extra call, no prompt change, no pipeline change. Cheaper than item 14's
trick, which asks the model for several narratives; this one slices the
narrative already in hand.

### The other half is state, and that is where the work is

"Once a day" is not a filter. It is per-subscriber send state, and the
mailer has exactly one: `SENT_ISSUANCES`, a single date →
issuance-timestamp map, global to every recipient. `sendEntryEmail()`
builds one `body` and one `htmlBody` and loops the addresses.

Per-subscriber frequency means a marker per address, and the send loop
becomes one body per distinct preference set per issuance. Note what this
does NOT cost: `MailApp.sendEmail` is already called once per recipient, so
filtering changes no send counts, and "once a day" reduces them. The cost is
build time against the consumer account's 90-minute daily runtime quota
(`mailer/README.md`), and it has NOT been measured — that is the number to
check a design against, not to assume is comfortable.

### The hazard, which is item 53 one level up

A subscriber on "once a day, Overview only" will not receive a re-issue that
changes the hazard verdict. That is a preference silently suppressing a
warning — the same shape as a missing block read as an all-clear, and the
same reader standing outside in it.

So any preference scheme needs a floor, decided before it is built rather
than after someone is caught out: either severe-weather content is not
opt-out-able, or a re-issue whose hazard section changed overrides the
frequency choice. This is a decision, not code, and it should be made first.
It also decides how item 2 (severe-weather alerts) reaches these people at
all.

### Where the logic should live, given the operator's objection

The objection is fair: `AppsScriptMailer.gs` is JavaScript in a browser
editor, deployed by hand, checked by a harness (`node mailer/test_mailer.js`)
that is deliberately outside CI. Every rule added there is a rule the
Python suite does not cover. Three ways out, in increasing order of what they
solve:

1. **Keep the preferences in the Sheet, keep the rendering in the script.**
   Cheapest. The untested surface grows.
2. **Render the variants in the pipeline** and publish them, so the script
   only picks a field and delivers it. Puts the logic where the tests are.
   Pairs with item 24 (published data feed) and item 3 (push delivery), and
   makes the script dumber rather than smarter, which is the direction of
   travel both of those already imply.
3. **Move to an ESP** (item 5). Preference centres, double opt-in and
   unsubscribe are that product's job, not this repo's.

Option 2 is the one consistent with everything else here. Worth settling
before writing any of it.

Related: item 14 (audience voices — the same rendering trick, chosen by
audience rather than by subscriber), items 2, 3, 5, 24, 54.

---

## 56. A glossary for the forecast's vocabulary · **Planned, both sides**

The forecast is written to be technical where it needs to be, and nothing
explains its vocabulary. Counted case-insensitively over the single
entry for 2026-08-31 (`narrative_markdown`, 5060 characters, headings
included): hPa 6, ` kt` 6, gust 6, J/kg 5, CAPE 4, `Day+` 4, "instability"
4, AQI 3, "synoptic" 2, "convective" 2, UV 1. That is one ordinary day, not
a bad one.

### Static data, not an LLM call

The project's first design principle is that arithmetic lives in code and
never in the LLM. A definition is the same kind of thing as arithmetic: it
has one right answer that does not depend on today's weather. A glossary
generated per issuance would cost a call, and would define CAPE slightly
differently on Tuesday than on Monday — drift in the one part of the page
whose whole job is to be a fixed reference.

So: a static table in this repo, written once, reviewed like prose.

### Ship the cheap version first

Two designs, and only one of them is a day's work:

- **A glossary page or email section listing the terms in play.** Needs no
  text matching, cannot corrupt the narrative, and is done when the table is
  written.
- **Inline linking of terms inside the narrative.** Wants matching a term
  list against LLM-written free text at render time, in three places and
  three languages: `publish/pages.py`, the mailer's two email bodies, and
  the app. The failure modes are concrete: `kt` matching inside a word,
  a term inside a code span or heading, and the model having already written
  "CAPE (Convective Available Potential Energy)" so the tooltip repeats the
  sentence it is attached to.

Do the first. Treat the second as a separate decision made after seeing the
first in use.

### One copy, read by both surfaces

The app has the stronger claim — someone who installed an app has no repo to
read — and the site and email have the readers today. Two hand-maintained
copies is the thing that goes stale and then lies, so the term list is
written once. Two routes:

- **In `olw_core`**, alongside the other shared material, reaching the app
  through `spec/README.md`. Updating a definition then needs an app release.
- **In the published feed** (item 24), so terms can be corrected without one.
  Item 24 has to land first, and it is the item that decides how much the app
  may be told remotely at all.

### The thing a glossary does not fix

If the Overview needs a glossary, the Overview is wrong. It is the section a
reader checks before going outside, and it should be readable without one —
the Detailed Discussion is where the vocabulary belongs and where it is
deliberately kept. A glossary is the cheap fix for the whole page; making the
top of it plainer is the real one, and that is item 14's ground (voices) or a
prompt change, not this item's. Both are wanted. Only this one is cheap.

App work: listed in the "Owed from open-local-weather" table in the Ensemble
repo's `ROADMAP.md`, per the note at the top of this file.

Related: item 14 (audience voices), item 24 (published feed), item 44 (a
sources page — the other "explain the machinery to the reader" surface).

---

## 57. A percentage with no baseline beside it is not a claim · **Partly shipped — server side, 2026-08-31**

`docs/accuracy.html` publishes "ECMWF 75% rolling Day+0 rain verification"
and the prompt cites those figures when it decides whom to trust. Nothing on
the page or in the record says what a number would have to beat to be worth
anything, so 75% cannot be read as good, bad or noise.

### Measured 2026-08-31, over the 20 scored Day+0 checks

Baselines computed from `data/actuals_cache/actuals.json` against the same
`observed_convection` truth the ledger already uses. Persistence at lead L
means the most recent observation available when a forecast at that lead is
issued — target day minus L minus one.

| Day+0 | rain accuracy |
|---|---|
| best_match | 85% |
| ecmwf_ifs025 / icon_seamless | 75% |
| kenya_met | 72.7% (11 checks) |
| **persistence — "like yesterday"** | **65%** |
| olw_blend | 66.7% (3 checks) |
| gfs_seamless / ukmo_seamless | 60% |
| **always-dry** | **55%** |

Two of the five models lose to a rule with no inputs, and the project's own
blended call sits one day of luck above it on three checks.

**The lead times invert, which a single baseline would have hidden.**
Persistence scores 35% at Day+3 and 30% at Day+7 — below chance, meaning its
INVERSE scores 65% and 70%, and GFS's 76.9% at Day+7 beats that by one day.
So at Day+0 the trivial rule is a real competitor, and at Day+7 the trivial
rule is a competitor only when flipped. Whether that inversion is a genuine
3-4 day oscillation in the wet/dry sequence here or an artefact of 20 checks
is NOT established, and it is exactly the sort of local structure a flat
scoreboard cannot show.

**Read every number above as provisional.** n=20, and a base rate that moves
from 45% over the scored window to 57.5% over the 40 days of actuals. Six
days separate 35% from 65%. The finding is not "GFS is bad" — it is that the
record cannot presently tell you whether any of this beats a coin, and
nothing in the system says so.

### What would count as done

Persistence and climatology enter the ledger as scored models, verified by
the same code path as GFS. The shape already exists: `kenya_met` is carried
as a non-Open-Meteo model with null fields, and `scored_models` is a list.

- **`persistence`** — the target day equals the last observation available
  at issuance. Needs no forecast data at all, only the actuals already
  stored.
- **`climatology`** — the trailing base rate over the record so far. Honest
  about being thin, and it improves on its own. Not a fixed 30-year
  normal, which this deployment does not have.

Both are free: no API call, no LLM call, no new fetch. They then appear on
the accuracy page and in MODEL TRACK RECORD beside the real models, which
means the prompt is told what a model has to beat before it is worth citing.

The review module (`review.py`) already gates cross-model rankings on sample
size and a sampling-noise floor. Extending that gate to "beats the best
trivial baseline by more than the noise floor" is the same machinery and is
the finding that actually matters.

### Why this is first

Every other item here proposes changing the system. This one changes what
the existing numbers MEAN, costs a day, and cannot be wrong in a way that
damages the record — a baseline is another row, and rows are already
first-write-wins per date. It also tells you whether the LLM's blended call
is earning its API spend, which nothing currently does.

### What shipped 2026-08-31

`baselines.py` and its Dart port, vector-locked, plus a 2,000-case random
sweep across the language boundary with zero disagreements — the vectors pin
the cases chosen, and the float means here are exactly the kind of arithmetic
AGENTS.md says to sweep rather than trust.

Both baselines enter `scored_models`, are predicted at every lead time, and
are withheld from the forecaster by `models_visible_to_the_forecaster` — the
same standing rule as the blend, for an adjacent reason: a yardstick handed
to the forecaster reads as a sixth opinion, and persistence's only input is
an observation the forecaster already holds.

**They leaked on the first attempt, through MODEL TRACK RECORD.** Three
separate filters each named `BLEND_MODEL_ID` by hand, so a second hidden
model meant remembering three places. Two of them now test membership of
`forecaster_models` instead. The third deliberately still names the blend:
that list is re-stored with `_blend_prediction` appended, so dropping the
blend prevents a duplicate while dropping the baselines would delete them
from the record on every re-run — an asymmetry now written beside the code.

**The page's own summary survived a hazard it had met before.** Two models at
zero checks would have turned "11 checks per model" into "0 — not enough to
say anything", which is exactly what happened once when the met service was
added. `_describe_sufficiency` already excludes unscored models from the
headline and names them separately, so the page now reads "climatology,
persistence have no verified checks at Day+0 yet".

### Measured offline against the stored record, 2026-08-31

Over the 40 days of actuals, scored against the same `observed_convection`
truth the ledger uses:

| | persistence | climatology |
|---|---|---|
| Day+0 | 66.7% (26/39) | 51.3% (20/39) |
| Day+3 | 50.0% (18/36) | 47.2% (17/36) |
| Day+7 | 37.5% (12/32) | 37.5% (12/32) |

Not the ledger's own figures — a different window from the 20 scored checks,
and computed by a throwaway script rather than by `verify/`. They are here to
say the implementation produces sane numbers, not as a result.

### The ledger's own figures, 2026-08-31, after the backfill

Like-for-like: same 20 checks, same `observed_convection` truth, same code
path as every other row.

| Day+0 | | Day+3 | | Day+7 | |
|---|---|---|---|---|---|
| best_match | 85.0% | kenya_met | 100.0% (6) | gfs_seamless | 76.9% |
| ecmwf_ifs025 | 75.0% | best_match | 88.2% | ecmwf_ifs025 | 69.2% |
| icon_seamless | 75.0% | gfs_seamless | 76.5% | best_match | 69.2% |
| kenya_met | 72.7% (11) | ecmwf_ifs025 | 76.5% | **climatology** | **53.8%** |
| **persistence** | **70.0%** | ukmo_seamless | 70.6% | **persistence** | **30.8%** |
| olw_blend | 66.7% (3) | icon_seamless | 64.7% | | |
| gfs_seamless | 60.0% | **persistence** | **52.9%** | | |
| ukmo_seamless | 60.0% | **climatology** | **52.9%** | | |
| **climatology** | **45.0%** | | | | |

**At Day+0, "the same as yesterday" beats two of the five numerical models
and beats this project's own blend.** Only best_match, ECMWF, ICON and the
met service clear it. That is the number the page has never carried, and it
is the whole reason for the item.

Read with the caution the record deserves: 20 checks, and the gap from 70%
to 60% is two days. The point is not that GFS is bad — it is that until now
nothing on the page could tell you whether 60% meant anything, and the
answer at Day+0 turns out to be "less than repeating yesterday's weather".

At Day+3 and Day+7 the models clear both baselines comfortably, which is the
opposite result and worth saying: the guidance earns its keep further out,
where a reader has no cheap alternative.

### The backfill: done 2026-08-31

Baselines are stored at forecast time, so only runs from now on have them.
Every stored entry predates the field and cannot be scored retroactively by
`rebuild-record`, which re-derives from *stored predictions* and cannot
invent one that was never made.

`olw backfill-baselines` (`backfill.py`), run over the 21 stored entries on
Conor's say-so. NOT a hindsight cheat: both baselines are deterministic
functions of data that existed at each issuance, so what was written is
exactly what those runs would have produced. It adds prediction rows and
nothing else — verified by parsing every changed file before and after and
comparing them as models with the new rows removed: 0 of 21 differed.

Idempotent, and `--dry-run` prints the plan. One side effect worth knowing:
rewriting a file re-serialises the whole entry, so fields the current schema
has and an older file lacked appear filled with defaults. Cosmetic — that is
what the before/after model comparison establishes — but the diff looks far
larger than the change is.

### The gate, shipped 2026-08-31

`review.py` now emits a `baseline` finding per lead: which models clear the
best trivial baseline by more than the same sampling-noise floor the ranking
uses, or that none do. It also STOPS ranking baselines as though they were
models — "best_match is the strongest rain caller and climatology the
weakest" compares guidance against a yardstick, and would have started
appearing the moment the backfill landed. Bias findings exclude them for the
same reason: "persistence under-forecasts peak wind" is a statement about
yesterday's weather.

What it says about the real record, 2026-08-31:

> At Day+0, best_match beats the best trivial baseline. persistence 14/20
> (70%), the best of 2; a model has to clear it by more than the 15-point
> noise floor to count. Clearing it: best_match 85%.

**One model out of five.** ECMWF and ICON both sit at 75% — five points above
persistence, inside the floor — so the record cannot presently say they beat
repeating yesterday's weather at Day+0. At Day+3 four models clear it and at
Day+7 three do, which is the more comfortable half of the same story.

No baseline finding reaches the FORECASTER: its review is built from
`models_visible_to_the_forecaster`, so there are no baseline cells to compare
against and the block is simply absent. That is deliberate and is the
remaining half — see below.

### Still open
- **Telling the forecaster.** The bar is now on the page and not in the
  prompt. It is arguably the single most useful thing the forecaster could be
  told — "GFS does not beat persistence at Day+0 here" is more actionable
  than any rolling percentage — but it is a prompt change and a framing
  problem ("here is the bar", never "here is another opinion"), so it waits
  on item 27.
- **The app.** Its accuracy screen has the same missing yardstick, and its
  record is per-device. Listed in the Ensemble repo's owed table.

Related: item 18 (the weekly review this extends), item 58 (which changes
what a baseline is measured with), item 26 (the spend this justifies).

---

## 58. Score the probability, not the guess · **Partly shipped — scoring 2026-09-03**

`rain` is a boolean. `extract.py` thresholds `precip_mm` at
`RAIN_THRESHOLD_MM` and the ledger stores true or false, so a model that
said "60% chance" and a model that said "certainly" score identically when
it rains, and identically when it does not.

Open-Meteo already sends `precipitation_probability` — it is in
`HOURLY_FORECAST_VARS` and `precipitation_probability_max` is in the daily
vars. **Nothing downstream reads either.** They are fetched every run and
discarded.

### Three things a proper scoring rule buys that a binary cannot

1. **Discrimination at a base rate near a coin flip.** The scored window is
   45% wet. A binary in that climate carries very little information per
   check, which is why item 57's baselines land so close to the models.
2. **Separating confidently wrong from honestly uncertain.** A Brier score
   decomposes into calibration and resolution. That distinction is the whole
   subject of item 53 — an issuance that said "no hazards are anticipated"
   and an issuance that said "instability guidance is missing, check the sky"
   are both scored as one dry call today.
3. **The incentive.** This is the important one. Rule 7 of the system prompt
   legislates honesty in English because the ledger does not reward it: under
   a binary, a confident guess and a well-hedged call score the same, so the
   prompt has to ask for restraint that the scoring actively fails to pay
   for. A proper scoring rule pays for it arithmetically. **It enforces in
   the record what the prompt is currently trying to enforce in prose.**

### The scoring shipped 2026-09-03

`verify/brier.py` and its Dart port, vector-locked, plus a 3,000-case random
sweep across the language boundary with zero disagreements. Float arithmetic
crossing languages is what AGENTS.md says to sweep rather than trust.

`VerificationScore.rain_brier` is `(p - outcome)**2`, scored against the same
`observed_convection()` truth as the boolean, because two columns scored
against two different truths would not be comparable. `None` when the model
supplied no probability — never a default of 0.5, which would invent a hedge
nobody made.

**LOWER IS BETTER, and that inversion is the thing most likely to be rendered
backwards.** Every other figure this project publishes is a percentage where
higher is better. Anything displaying Brier has to say so in words.

**The percentage-to-probability conversion happens in exactly one place**, in
`score_prediction`, and `brier_score` raises on anything outside [0, 1]
precisely to catch a missed one. A 70 read as a probability scores 4761,
which would pass silently into every mean it touched.

### Climatology became the reference forecast

Raw Brier is not interpretable on its own — 0.2 is good or bad depending
entirely on the base rate — which is item 57's lesson arriving in a second
place. The standard answer is a skill score against a reference, and the
reference is climatology, which item 57 already put in the ledger.

So `climatology` now emits the trailing base rate as
`rain_probability_pct`. Its boolean call was discarding exactly the
information a proper scoring rule needs. **The probability and the boolean can
disagree and that is correct**: at a 40% base rate the boolean says dry and
the probability says 40, two honest answers to two different questions.

`persistence` deliberately gets no probability. It repeats an observation, and
an observation is certain about the day it describes while saying nothing
about the chance of another one; inventing 100 or 0 would claim a confidence
it never expressed and score badly for a reason that is an artefact.

**`brier_skill_score` is not clamped at zero.** Item 57 measured two of five
models losing to persistence on the boolean; a negative skill score states
that same result in a form that cannot be read as merely "less good".

### What it cannot say yet

**3 of 24 stored days carry a probability.** The field started being recorded
2026-09-03 and cannot be backfilled — unlike the baselines, a model's own
stated probability is not derivable after the fact from anything stored. So
`brier_checks` is reported SEPARATELY from `checks_found` in the rolling
window, because a window holding 30 scored days of which 3 have a probability
would otherwise imply the Brier figure rests on evidence it does not have.

### The blend now states its own probability, 2026-09-03

Which is where this item's argument actually lands. `TodayProperties`
carries `rain_probability_pct`, `_blend_prediction` passes it into the scored
prediction, and the prompt asks for it in both languages — the paragraph is
lifted verbatim from the Python so the two cannot diverge, and it is
byte-identical apart from Dart's `$` escaping.

**The prompt says why honesty pays, because the model has no way to know
otherwise.** It states that this is checked for CALIBRATION rather than for
being right, that claiming 95% when you mean 60% is the most expensive
mistake available, and that hedging to 50% on a day with strong evidence is
nearly as costly in the other direction. Under a proper scoring rule all of
that is true, which is the point: rule 7 asks for restraint in English and
this makes restraint the winning strategy.

**Telling the forecaster HOW it is scored is not showing it its own record.**
The standing rule in `models_visible_to_the_forecaster` withholds the
blend's track record, and that is untouched. This is instruction, not
feedback — no closed loop.

**Optional in the schema**, so a stored response from before the field
existed does not fail a run; it simply is not Brier-scored, exactly like a
numerical model with no probability. Absent is not 50.

### The replay was attempted 2026-09-03 and did not produce a comparison

Worth recording because the attempt found more than the comparison would
have.

**Two cases, current prompt. Case 1 succeeded in ~37 s. Case 2 failed four
times, every attempt a 60-second read timeout, and the run raised.**

**The timeouts now reproduce from a developer machine.** Item 53 measured, on
2026-08-30, that `forecast_days=2` failed only from GitHub runners while both
calls returned 200 in ~1.1 s locally — and that framing has been load-bearing
ever since. The Gemini timeouts are a different endpoint, but the same
assumption ("it works locally") no longer holds for the LLM path either. That
is a change in the world since 08-30, not a difference of opinion about it.

**Two defects in the harness itself, both now fixed and both mine.**

- `run_replay` raised on the first failing case and discarded the one that
  had already succeeded. Every case is a paid call; a six-case run dying on
  the fifth would have cost everything and returned nothing. It now returns
  `(results, failures)`, writes what succeeded, and says plainly that a diff
  over a partial run is partial.
- The replay was run with `thinking_level=None` while production uses
  `high`. A harness whose conditions differ from production answers a
  question nobody asked. `olw replay` now builds its provider through
  `_build_pipeline_deps`, so it cannot drift from the forecast's own
  configuration again.

**Cost: 5 calls for no comparison**, against a configured cap of 10. That is
item 66's arithmetic arriving in practice rather than in theory.

### Still open

- **Nothing displays Brier yet.** The accuracy page and the track record
  carry the boolean figures only.
- **The prompt change has not been replayed.** `olw replay` exists (item 27)
  and this is precisely what it is for, but it spends real money on the
  operator's key — so the before/after comparison is a deliberate act, not
  something to slip into a build.

### The comparability hazard, and the way around it

Changing what is scored breaks continuity with every check already stored —
the exact failure items 32, 33 and 34 were fought over. So this is additive,
not a replacement:

- `rain` (boolean) stays, scored as it is now, forever. The existing series
  remains comparable to itself.
- `rain_probability` is added beside it, for every model that can supply one
  and for the blended call, scored by Brier alongside.

Two ledgers over the same days, one of which starts empty. The boolean is
never retired, because a record that cannot be compared with its own past is
worth less than a cruder one that can.

### Not established

- Whether Open-Meteo's `precipitation_probability_max` is CALIBRATED here.
  **The storage half shipped 2026-08-31**, in both languages and at every
  lead: `ModelPrediction.rain_probability_pct`, recorded before anything
  scores it, because the check needs history and history only accrues
  forwards. Day+3/+7 take the served daily maximum; Day+0 derives it as the
  highest hour, since no daily maximum exists at hourly resolution. Absent is
  `None` and never `0` — zero is a confident claim that it will not rain.
  The question itself stays open until enough stored days exist to answer it.
- What a "probability" from the LLM's blended call is actually worth. It may
  be well-calibrated, it may be a stylistic number. Brier is how that gets
  answered rather than argued about, and the answer may be that the blend
  should not emit one.

App work: listed in the Ensemble repo's owed table.

Related: item 57 (baselines, which want a scoring rule to be measured with),
item 53 (the incident this scores properly), item 27 (how a prompt change
here gets validated).

---

## 59. One call writes the forecast and writes the prose · **Planned**

`llm/prompt.py` is 451 lines. Single instruction paragraphs run past 400
words. It is at once a reasoning engine, a style guide and an incident log:
rule 7 exists because of 2026-08-29, and the day-over-day paragraph exists
because one issuance said "similar warmth, calmer winds, and dry again".

That is a codebase accreting patches with no regression suite. The Python is
not allowed to work that way and item 40 exists to hold it to AGENTS.md; the
prompt is held to nothing, and item 27 — the harness that would judge a
change — is still Planned.

### The split

- **A judgment call.** Structured output only, no prose: the blended
  `today_properties`, the confidence, which models to weight today and why.
  Small enough to read in one screen, and regression-testable against the
  stored archive, because the inputs and the outcome are both already there.
- **A rendering call.** Judgment in, narrative out. Roughly 90% of the
  current prompt's rules belong here — the day-over-day phrasing, the
  repetition bans, the section order, the register.

The point is what a mistake costs on each side. A bad rule in the renderer
produces a clumsy sentence. A bad rule in the judgment produces a wrong
forecast. Today they are edits to the same string, and a change to either
can silently move the other.

### The hazards, stated before anyone starts

- **Two calls cost more than one.** On the server that is a rounding error.
  In the app it is spend against the reader's own cap (item 26), and a cap
  sized for one call a day now refuses at half the forecasts. That is an app
  decision, not a server one, and it is in the owed table.
- **A judgment reduced to fields may be worse than a judgment written as
  prose.** Reasoning that has room to argue with itself sometimes arrives
  somewhere a schema would not let it go. This is a real risk and the
  harness is how it gets measured rather than assumed.
- **The prompt is pinned character-for-character across two languages.**
  Splitting it doubles that surface. Worth doing once, not twice.

### Order

**Item 27 comes first, and should always have.** Every prompt change made so
far — item 48's pass, 53.3's rule 7 — is unvalidated for side effects. Not
wrong; unmeasured. You cannot improve a prompt you cannot measure, and this
item is a large prompt change.

App work: listed in the Ensemble repo's owed table.

Related: item 27 (the prerequisite), item 14 (voices — the renderer is where
they would live), item 40, item 48.

---

## 60. The record is a scoreboard and never an input · **Planned**

Forty days of (conditions → prediction → outcome) at one point, and the
forecast never looks at any of it except as percentages. `review.py` reads
the record to report on models; nothing reads it to forecast.

The retrieval a machine does well and a human forecaster does badly, from
memory, and is most admired for: find the days in the archive whose setup
most resembles today's, and say what actually happened on them. Nearest
neighbour over a feature vector — synoptic pattern, CAPE profile, MSLP
trend, wind, month — against the project's own stored days. No API call, no
LLM call, and the answer improves every day the record grows.

**This is local knowledge in the only form an LLM can hold it.** The model
cannot accumulate twenty years of afternoons; it can be handed the three
past days that look like this one, with their outcomes, on every run. Given
into the prompt as evidence, not as a conclusion — the same way the track
record is.

### Why it is last, not first

Forty days is not an archive. Most days will have no close neighbour, and
handing the model a weak match dressed as a precedent is worse than handing
it nothing — the failure shape of item 53 again, a thin signal read as a
strong one. So a match needs a distance threshold, and below it the honest
output is "no comparable day in the record", which the prompt must be
allowed to say.

Item 20 (historical backfill) is the unlock, and this is a stronger argument
for it than the cold-start one it is currently filed under: backfill does not
merely start the scoreboard sooner, it makes the archive queryable as a
forecast input.

### The asymmetry worth naming now

The server's archive is one place, growing daily, and public. The app's
record is per device, starts empty on install, and belongs to a reader who
may have generated forty forecasts or four. Analogues computed on the app's
own history would be nearly useless for years. If the app is to have them at
all, they come from the server's archive through item 24's feed — which
makes this one more reason that item exists.

App work: listed in the Ensemble repo's owed table.

Related: item 20 (backfill, the prerequisite), item 24 (feed), item 18
(review — the other consumer of the whole record), item 35 (model
disagreement, a feature this would key on).

---

## 61. The Overview stops at today · **Planned**

Requested 2026-09-01. The Overview says what today is like and nothing about
what is coming, so a reader who wants to know whether to move a plan to
Thursday has to get through the Extended Outlook section to find out. The
shape asked for, verbatim:

> "Warm and sunny today (Tuesday), much like yesterday, though calmer;
> isolated evening thunder is possible around 20:00 as models show strong
> disagreement on atmospheric instability. Wednesday through Friday show a
> (warming/cooling/consistent) trend with slightly increasing chances of
> precipitation and thunderstorm activity.""

### The parenthesis in that example is the whole design

`(warming/cooling/consistent)` is a comparison of numbers, and this project's
first principle is that arithmetic lives in code and never in the LLM. The
measured reason is item 23: a live run asked to compare 29.6 °C against
29.5 °C described it as "about 1 °C cooler", a ten-fold overstatement in the
one sentence most readers act on. That fix produced pre-computed labels —
`high_label`, `wind_label`, `rain_contrast` — which the prompt is told to
use VERBATIM. An extended-range trend needs the same treatment for the same
reason, or the model will call a 0.3 °C drift a cooling trend.

So what is actually being built is a `describe_extended_trend` beside
`comparison.py`'s day-over-day: over days 1-3, a temperature direction, a
precipitation direction, and a confidence, each banded in code with
thresholds that clear real day-to-day noise before anything is named.
Handed to the prompt ready-made, so the prompt change is one clause rather
than a paragraph about how to compare numbers.

**Day names are arithmetic too.** "Wednesday through Friday" is a date
computation in the LOCATION's timezone, not the reader's or the server's, and
it is the kind of thing that silently reads a day early. `dates.py` already
owns this and should produce the phrase.

### The budget is two sentences, and that is a real constraint

The Overview is already the most rule-laden paragraph in the prompt. Item 48
found it saying the same thing four ways ("much like yesterday, with similar
warmth, similar winds, and dry again"), and item 23's fix is a long
instruction about NOT enumerating labels that agree. A third clause is
exactly where that regresses.

Two rules that follow, and should be written into the prompt with the change
rather than discovered later:

- The extended clause never restates the day-over-day clause. "Much like
  yesterday, and tomorrow similar too" is one fact twice.
- **A steady spell is worth saying, and saying plainly.** An earlier draft of
  this item had the clause go quiet when nothing was changing; the operator
  pushed back, correctly. "It'll be about the same for the next few days" is
  one of the most useful things this forecast can tell someone deciding when
  to do a job — the absence of change IS the planning answer, and a reader
  who is told nothing has to go and check.

  What is banned is the empty formulation, not the content. "Wednesday
  through Friday show a consistent trend" is bureaucratic and says less than
  "much the same through Friday" while taking longer. The labels handed over
  must therefore include a real steady band with real words in it, not a null
  that the prompt is left to phrase or skip. Same discipline as
  `rain_contrast`, which ships "dry again" as a finished phrase rather than a
  flag meaning nothing-to-report.

### What it depends on

- **Item 27**, the harness. This is a change to the single most-read paragraph
  in the product, and neither the operator nor a session can currently
  measure whether a change to it made the rest worse. The CODE half —
  the trend computation and its vectors — is testable today and can ship
  first; the prompt half should not.
- **Item 35** for the example's own middle clause. "Models show strong
  disagreement on atmospheric instability" is item 35's output, and it does
  not exist yet. The request presumes it.
- Item 37 is adjacent but different: that one is day-CHARACTER within a day
  (when the wind peaks, how the cloud evolves), this one is direction ACROSS
  days. They share the discipline — bands in code, phrases handed over,
  scored values untouched — and neither blocks the other.

**The scored values stay exactly as they are.** Item 37's closing rule
applies here without change: a trend label is a description, and nothing it
does may touch what `verify/` measures.

---

## 62. A hazard that crosses a line, and what silence means · **Planned**

Requested 2026-09-01, alongside item 61: air quality, weather alerts and
regional alerts should reach the Overview when they are bad enough to matter,
and stay out of it when they are not.

### The pattern already exists and works — generalise it, do not invent it

`prompt.py` already carries exactly this for one hazard:

> INSTABILITY BELONGS IN THE OVERVIEW WHEN CODE SAYS IT DOES. "CONVECTIVE
> INSTABILITY" in the user message carries a pre-computed "convective" flag.
> When it is true, the Overview MUST carry a short clause... The flag
> decides; you phrase it.

That rule exists because a real forecast opened "similar warmth, calmer
winds, and dry again" on a day whose afternoon CAPE reached 2600 J/kg, and
discussed the instability twice further down where a reader who stopped at
the Overview never saw it. So the work here is a small set of
code-computed "the Overview must mention this" flags with the same contract,
not a new mechanism.

### The trap, stated before anyone builds it

**A rule that adds a line above a threshold is a rule that stays silent below
it, and silence is not self-explaining.** No AQI clause can mean "the air is
fine" or "no station reported" — and the second is common here, because
ground sensors go stale (item 4). A reader cannot tell those apart, and
reading the second as the first is precisely the shape of item 53: a gap
consumed as reassurance.

So each flag needs three states, not two: above the line, below the line, and
NOT KNOWN. The third must be able to produce a clause of its own — the same
discipline standing rule 7 already applies to whole blocks, pushed up into
the Overview.

### Thresholds belong in config, not in code

An AQI of 100 is an unusual day in Kisumu and an ordinary Tuesday in Delhi. A
number compiled into the pipeline is a number that is wrong for every fork,
and this project already keeps location-dependent judgements in
`config/location.yaml` — `waqi_stations`, `secondary_point`,
`local_bulletin_*`. Overview thresholds belong there with them, absent by
default so a fork that configures nothing behaves exactly as today.

### Where it overlaps, and what has to settle first

- **Item 2** already owns the alert side and already contains a severity-floor
  decision: "advisories below a threshold go to the site, not to email". That
  floor and this one are the same question asked about two surfaces, and
  answering them separately would produce a forecast whose Overview and whose
  alert email disagree about what counts as serious. Item 2 should settle it.
- **Item 4** owns AQI staleness and is partly shipped. The "not known" state
  above is its output, not new work.
- **Item 35** is the instability half, already flagged.

Which makes the honest order: 2 and 4 settle their own thresholds, then this
item generalises the flag mechanism over all of them. Building it first would
mean guessing two numbers that other items are already going to decide.

**App work, unlike item 61's.** The flag mechanism itself is shared logic and
reaches the app through the pin with nothing to do on that side. The
thresholds do not: there is no `location.yaml` on a phone, so a configurable
floor needs a settings surface and a default there. Listed in the Ensemble
repo's owed table.

Related: items 2, 4, 35, 53 (rule 7), 61 (the other half of the same
Overview change).

---

## 63. Encourage more than one reporting station · **Planned**

Requested 2026-09-03, out of item 45's precedence discussion: setup for both
OLW and the app should push whoever is configuring it toward SEVERAL local
observing stations rather than one.

The case is item 47's, one level up. A nearby station is the biggest accuracy
lever a reader controls, and one station is a single point that can be down,
can be four kilometres from the weather, and can report a variable as a
constant (item 45 measured exactly that at HKKI: `p01i` zero on all 932 rows
including an hour whose own report says `-RA`). Two stations disagreeing is
information; one station is a claim.

### It sharpens a problem before it solves one

**More sources make the OR bias worse, and the OR is already how rain is
scored.** `observed_convection()` is true if ANY source saw rain, so each
station added can only create wet days, never remove them, and the observed
rain rate climbs with the size of the set. Adding one station's
precipitation moved the all-time figures about five points in item 53.1.
Adding four would move them further, and nothing on the accuracy page would
say why.

So this item cannot ship before item 45's provenance stamp. The order is:
provenance per day per variable, then more stations, then a rule for
combining them. Doing it the other way round produces a record whose numbers
drift for reasons nobody can reconstruct.

**And it changes the question being answered.** One station at the reader's
point answers "did it rain here". Five stations across a basin answer "did it
rain somewhere in the basin", which is a different and broader question that
gets broader with each addition. Whichever is intended has to be stated on
the accuracy page rather than emerging from how many stations someone
happened to configure.

### What it probably looks like

- **Onboarding asks for more than one** and says why, rather than accepting
  the first ICAO and moving on. Item 47 already owns the app-side onboarding
  moment; this is a change to what that moment asks for.
- **Distance enters the record.** With several stations, "nearest that
  reported" is a better rule than "any", and it needs each station's
  distance from the primary point — computable from config, which already
  carries both.
- **Disagreement is surfaced, not resolved.** Item 45's finding holds: with
  no held-out truth, disagreement between observation sources is a flag for
  a human, never a weight to fit. Two stations differing on rain is
  precisely the case item 46 exists to ask the reader about.

### The candidates, named 2026-09-03

`OBSERVED_REALITY_SOURCE_HANDOFF.md` answers this item's "not established"
question. Kisumu does have neighbours, and none of them is a drop-in:

| Station | WIGOS | Distance | The catch |
|---|---|---|---|
| Kisumu Airport (HKKI) | `0-20000-0-63708` | ~4 km | already used; no usable precipitation amount |
| Kakamega MET AWS | `0-404-300-372021012AS63681` | ~40 km | closest candidate; public data availability unconfirmed |
| Kericho (HKKR) | `0-20000-0-63710` | ~64 km | **820 m higher** — cannot substitute for surface temperature |
| Kisii (HKKS) | `0-20000-0-63709` | ~66 km | metadata exists; no fresh public observation established |

**So the answer to "is there a second station" is yes, with conditions —
which is worse than a clean no, because a clean no would stop someone
adding one.** Kericho is the sharp case: it is ACCESSIBLE and FRESH and
would look like a perfectly good source in any resolver that ranked by
distance. At 820 m of elevation difference its temperature is a different
climate, and a precedence chain that took it for `high_c` would corrupt the
record while every status check stayed green.

That is the case for item 45's six-state ladder rather than a distance sort:
REPRESENTATIVE is a separate question from FRESH, and it is the one that
bites silently.

**Per-variable, not per-station.** Kericho may be perfectly good evidence
for rain OCCURRENCE — convection over the basin is a regional event — while
being useless for temperature. A station is not admitted or rejected whole;
it joins a chain for the variables it can actually speak to, which is what
item 45's per-variable precedence was already designed for.

**And the OR problem compounds with each addition.** `observed_convection()`
is monotonic, so every station added can only create wet days. Measured
2026-09-03 by `olw divergence`: the reanalysis alone already claims wet on
10 of 43 days the airport reported and saw nothing. Adding three more
sources on an OR would move the published rain accuracy substantially, for
instrumentation reasons, and nothing on the page would say so. The
provenance stamp shipped, so the *ability* to say so now exists — using it
is the work this item cannot skip.

### Not established

~~Whether Kisumu has a second usable station at all.~~ **Answered above,
2026-09-03.** What remains unestablished is narrower and more practical:
whether Kakamega AWS and Kisii actually serve fresh public observations, as
distinct from appearing in a catalogue. Same instruction item 45 issued about
`p01i`, which turned out to matter — EXISTS is not ACCESSIBLE, and neither
is FRESH.

Related: item 45 (precedence and provenance, the prerequisite), item 47 (the
onboarding moment this changes), item 11 (source discovery), item 44 (the
sources page), item 46 (asking the reader when sources disagree).

---

## 64. Source research, September 2026 — what these documents are · **Reference**

Five documents landed in `docs-internal/` on 2026-09-03 from a separate
research effort. They are REFERENCE, not a work item, and this entry exists
so nobody mistakes them for a plan.

| Document | What it is | Read it when |
|---|---|---|
| `OBSERVED_REALITY_SOURCE_HANDOFF.md` | The synthesis. Roles for an observational source, WIGOS/WIS2/METAR/radar/satellite, and a set of principles | Before adding any truth source |
| `GLOBAL_SOURCE_REGISTRY_REFERENCE.md` | Registry design: three registries, richness dimensions, coordinate-based selection | Building item 11 |
| `OPEN_LOCAL_WEATHER_GLOBAL_SOURCE_MATRIX_v1.0.xlsx` | 193 WMO members scored for adapter priority | Answering "how does a fork elsewhere configure this" |
| `RADAR_SATELLITE_REGIONAL_OBSERVATIONS_HANDOFF.md` | Earlier, superseded in part. Kept for the Kisumu radar and Meteosat findings | Item 41 |
| `WEATHER_STATION_DISCOVERY_HANDOFF.md` | Earlier, superseded in part. Kept for the station discovery test | Item 63 |

### What was taken from them, and where it went

- Item 2 — the KMD CAP feed, which replaced a scraping plan.
- Item 41 — Meteosat-12/MTG-I1, Meteosat-11 SEVIRI, RealEarth band IDs.
- Item 45 — the six-state source ladder, `METADATA_CONFLICT`, radar closed.
- Item 63 — the named station candidates and Kericho's elevation problem.
- Item 11 — OSCAR/WIGOS/WIS2 as the discovery mechanism, three registries.

### What was deliberately NOT taken

The recommended build sequence in the handoff's §43 — WIGOS discovery, WIS2
paths, EUMETSAT MTG access, cloud truth products, Lightning Imager,
RealEarth, OPERA — is a research programme, and this project's binding
constraint is not source count.

`observed_convection()` is an OR and therefore monotonic: every source added
can only create wet days. `olw divergence` measured on 2026-09-03 that the
reanalysis alone already claims wet on 10 of 43 days the airport reported
and saw nothing. Adding six sources before those numbers mean anything would
move every published accuracy figure for instrumentation reasons, and the
page would show models getting worse.

The handoff's own §43 agrees, at point 9: *do not change deterministic
scoring precedence until those measurements exist*. Item 45's sequencing is
the same instruction. So the order stands — provenance stamped, readings
stored, divergence accumulating — and sources join afterwards, one at a
time, each with its own divergence measured before it counts.

### One correction the research needs applied to itself

The matrix marks Kenya's CAP feed **verified**, and the endpoint is: HTTP
200, valid CAP 1.2. Its newest alert is four months old. The same document
insists that "an operational catalogue entry with stale observations is not
a usable current source" and that "discovery metadata is not data" — which
applies to its own verification column. **`last_live_observation` belongs in
the registry schema as a required field**, not an optional one, or the
matrix will accumulate exactly the false confidence it warns against.

Related: items 2, 11, 41, 44, 45, 63.

---

## 65. Lightning and cloud as their own truth, not as rain · **Planned**

Raised 2026-09-03: lightning happens with or without precipitation, and is
worth knowing either way. Cloud cover is the same shape — the forecast
predicts it and nothing observes it.

### The conflation this fixes already exists in the record

`observed_convection()` is `rain OR thunder OR precipitation`, so a station
reporting `TS` already scores the day WET. Measured over the 43 days with a
station thunder observation:

| | days |
|---|---|
| thunder observed | 11 |
| thunder with precipitation from either source | 8 |
| **scored wet on thunder alone** | **3** |

Three of 23 wet days — 13% — rest on thunder with no precipitation seen at
the station. **But read the detail before concluding anything**: all three
had reanalysis totals of 0.2, 1.1 and 0.5 mm, spread thin enough that no
single hour crossed `RAIN_THRESHOLD_MM` of 0.5. So they are not bone-dry
thunderstorms; they are light convective rain the hourly threshold missed,
which is precisely what item 42 added the thunder term to catch.

The conflation is therefore real, modest, and the cases are borderline rather
than clear-cut. That is an argument for separating the variables rather than
for removing the OR term, because the current arrangement forces a choice
that the data does not support making.

### Separate variables end the argument instead of settling it

"Did it storm" and "did it rain" are different questions with different
answers, and the ledger currently has one column for both. Scored
separately, a model that predicted thunder and got a dry storm is right about
thunder and wrong about rain, which is more information than either verdict
alone — and nobody has to decide whether a dry thunderstorm is "a wet day".

**And this is the reason to start with lightning.** It adds vision without
touching comparability: a new variable is not an OR term, so it cannot move
the rain rate, cannot make the models look worse for instrumentation
reasons, and needs no waiting for divergence numbers. Every other source
discussed so far fails that test.

### Lightning first

MTG's Lightning Imager (item 41) observes convection DIRECTLY rather than
inferring it from cloud or from rainfall. For a deployment whose worst
failures are pop-up convection the reanalysis averages away and a point
station catches only when it is overhead, that is the sharpest instrument
available.

What it needs:

- A `lightning` observation on `DailyActual`, three-valued like `thunder`,
  stamped with its own source id. **Not folded into
  `observed_convection()`** — see above, and see item 45's OR problem.
- A prediction to score it against. Nothing currently forecasts lightning as
  a committed value; CAPE is the closest, and it is an instability index
  rather than a call. So this ships as an OBSERVATION first, scored against
  nothing, exactly as the station readings did.
- The station's `TS` group as a cross-check. Two independent witnesses to
  the same physical event, one satellite and one surface, is a rare luxury
  here — and a disagreement between them is informative rather than a
  problem to resolve.

### Cloud second

The forecast predicts `cloud_cover`; nothing observes it. A satellite is the
only instrument that can, and FCI's IR channels are the standard way.

Harder than lightning for a reason worth recording now: **brightness
temperature is not cloud cover.** Deriving one from the other is a
retrieval, with assumptions, and it would be this project's first observation
that is a MODEL of an observation rather than a reading. That is not
disqualifying — the reanalysis is already a model — but it must be stamped
as such, and it argues for taking an existing published cloud product rather
than deriving one here.

Also worth stating: cloud is where item 37's "a mean over a day is almost
never the right summary" bites hardest. "Sunny until midday then clouding
over" and "overcast all day" have similar averages and are different days.
Whatever is observed has to keep the shape, or it will verify a number
nobody experienced.

### Not established

- Whether MTG Lightning Imager data is reachable for this point at a
  cadence and cost that make sense. Item 41 names the platform; nobody has
  fetched anything.
- Which published cloud product to prefer, and whether any of them is free
  at this location.
- Whether a lightning observation adds anything the station's `TS` group does
  not already provide. Kisumu Airport is four kilometres away and reports
  hourly; the satellite's advantage is basin-wide coverage, which matters
  only if storms are being missed OUTSIDE the airport's view. That is
  measurable once both exist, and it should be measured before the satellite
  path is treated as load-bearing.

Related: item 41 (the platform), item 45 (why this must not join the OR),
item 37 (day-character, which cloud needs), item 63 (the other vision
source), item 58 (what scoring a new variable properly would require).

---

## 66. A lost forecast reported success · **Fixed 2026-09-03**

Found while investigating why the LLM spend cap had no headroom. The
investigation's own question turned out to be the smaller half.

### What happened

On 2026-09-02 the 15:01 evening refresh made four Gemini attempts — two
HTTP 503s and two 60-second read timeouts — printed `Critical Error:
forecast aborted, the LLM call failed`, and produced nothing. `data/log/
2026-09-02.json` still carries `refreshed_at: null`.

**GitHub reported the run as `success`.**

### Why

```yaml
olw forecast --public-url "..." $FLAGS \
  | tee "$RUNNER_TEMP/forecast.log"
```

A pipeline's exit status is its LAST command's, and `tee` always succeeds.
GitHub's default shell is `bash -e {0}` — `-e` but **not** `-o pipefail` —
so `olw forecast`'s exit code 1 was discarded. Demonstrated rather than
assumed:

```text
bash -e   -c 'false | tee /dev/null; echo $?'   ->  0
bash -eo pipefail -c 'false | tee /dev/null'    ->  step fails
```

The CLI was innocent throughout: `_run_forecast` returns 1 on
`LLMResponseError`, and always has.

### Why it is item 53's shape, one layer up

No commit, no email, no red job, and the only trace a line in an Actions log
nobody reads. A forecast simply did not exist that evening and every surface
this project has said nothing. That is the same failure mode as the silent
fetch degradation, moved from inside the pipeline to the thing that runs it.

### The fix, and the guard

`set -o pipefail` in the run block, with the reasoning beside it. The `tee`
is wanted (the commit subject is derived from the run's own output), so the
flag is the fix rather than the removal.

`tests/test_workflows.py` asserts that any workflow run block containing a
pipe also sets `pipefail`, and was watched failing with the line removed. A
future step that pipes without it would re-open exactly this hole, silently,
which is the definition of a thing worth a guard rather than a comment.

### What is still not recorded, and is deliberately not being built

**A run that aborts leaves no trace in the record.** Item 53.4's degradations
describe a run that COMPLETED with something missing; an aborted run writes
no issuance at all, so `check_recent_degradations` cannot see it either. The
record cannot distinguish "no evening re-issue because none was scheduled"
from "no evening re-issue because it died".

A red job is now the notification, and that is proportionate. Closing the gap
properly would need an expected-schedule model — something that knows a run
was DUE — and that is a real design, not a patch. Written down rather than
half-built.

### The thing that started this: the cap has no room for a bad day

`MAX_ATTEMPTS` is 4 and the spend cap counts each HTTP request, which is
correct: a flaky provider issuing four billable requests against one
recorded "call" is the bug that put the counter there. But with two
scheduled runs a day:

```text
worst case   2 runs x 4 attempts  =  8
cap                                = 10
left for anything else             =  2
```

So one bad day leaves no room for a manual run, and none at all for `olw
replay`, which needs 12 calls for a six-case before/after. That is not a bug
in the cap; it is the cap being sized for a good day. Related: item 26 (the
cap), item 58 (whose replay this blocked), item 59 (which would double the
per-run cost).

### The timeout hypothesis was tested and is FALSE, 2026-09-03

Worth recording at length because it was a good hypothesis, it was wrong, and
the wrongness is the useful part.

**The reasoning that produced it.** Runs were burning 3-4 calls each. Every
failure was a 60-second read timeout. `REQUEST_TIMEOUT_S` is 60 and item 27
records that generation takes "roughly a minute" under `thinking_level:
high`. If generation sat on the boundary, every attempt would be a coin flip
and the retries would be self-inflicted — a client misconfiguration wearing
the costume of a provider outage.

**The test.** One request, production's exact configuration, the same prompt
pair that had failed four times an hour earlier, with the ceiling lifted to
180s and retries disabled so the duration would be the measurement rather
than the ceiling.

```text
case:   evening refresh carries the morning narrative
input:  33,777 char system + 4,590 char user
model:  gemini-3.6-flash, thinking_level=high, timeout 180s
result: SUCCEEDED in 32.9s
```

**Generation takes about 33 seconds. The 60-second ceiling is not marginal,
and raising it would fix nothing.** The failures are Gemini being
intermittently unavailable — HTTP 503s and connections that never answer —
which is also what the 09-02 logs show directly.

> **The second sentence above is wrong.** Corrected the next day against the
> spend ledger — see "The correction: one sample against thirteen" below.
> The first sentence stands; the inference drawn from it does not.

**What this changes.** The cap pressure is real but it is not a bug on this
side. A bad day genuinely costs 2 runs x 4 attempts = 8 billable requests,
and 10 left no room for anything else. The cap is now 16, which absorbs a bad
day and stays under the account's real 20/day quota — raised for a different
reason than the one that prompted raising it.

**What is still worth considering, and is NOT being changed blind.** The
backoff is 5s, 10s, 20s — four attempts inside about two minutes. If the
provider is having a bad minute, all four land inside it. A longer schedule
would span more of the outage for the same number of billable requests, at
the cost of a slower run. That is a real trade and it should be decided with
more than one bad day of evidence.

> Decided 2026-09-04, on the evidence below: 30s, 60s, 120s.

### The correction: one sample against thirteen, 2026-09-04

The probe above was a single successful call, and it was read as evidence
about a distribution. It is not. It shows generation CAN finish in 33
seconds; it says nothing about the tail, and the tail is the whole question.

**The spend ledger had the answer the whole time and was not consulted.**
Every attempt is timestamped there before it is sent — that is what item 26
built it for — so subtracting the known 5/10/20 backoff from consecutive
timestamps recovers how long each failed attempt actually ran. Across the
scheduled runs on record, 13 failed attempts with a recoverable duration
(replay bursts excluded: consecutive entries there are separate cases, not
retries of one another):

```text
8 of 13   60.1s                     — exactly the client's own ceiling
5 of 13   1.6, 16.5, 19.0, 21.6, 29.5s
```

Bimodal, with nothing in between. **Nothing has ever failed at 35s, or at
45s.** A cluster sitting precisely on our own deadline is this side giving
up, not the provider refusing — and 8 of 13 is not a corner case, it is the
majority failure mode. The 09-03 forecast run is the clean example: four
attempts, three of them dying at 60.1s, the whole burst over in 215 seconds.

So the correct statement is narrower than either version before it: **the
60s ceiling was being hit constantly; whether lifting it helps is unknown.**

**What 90s settles, and what it does not.** Two hypotheses remain and this
side cannot distinguish them:

- *Slow generations.* Requests that would have completed at 70-80s. Raising
  to 90s converts them into successes.
- *Hung connections.* Requests that were never going to answer. Raising to
  90s makes them fail 30 seconds later, for the same billable cost, and
  changes nothing else.

**The prediction, recorded before the fact.** Re-run this same ledger
analysis in a week. If `90.1s` has simply replaced `60.1s` as the cluster,
it was hangs, the timeout was not the fix, and the right response is a
SHORTER ceiling — failing at 40s and retrying costs less than failing at 90s
and retrying. If the cluster disperses, it was slow generations and the
raise did its job.

**The method is the durable part.** The ledger was built to enforce a cap,
and it turns out to be a latency record too, because it timestamps before
the call. Any question of the form "how long was this provider taking on the
day it broke" is answerable from it retrospectively, without instrumenting
anything. That is worth more than this particular answer.

**One thing the probe established for free.** The response carried
`rain=True` with `rain_probability_pct=60` — the first real evidence that
item 58's new field produces a sensible, self-consistent value on a live
call, rather than merely passing a schema. The replay would have shown this
properly; a single successful call shows it partially.

### The app must not inherit the pipeline's patience

Widening the backoff to 30/60/120 exposed something the old numbers hid.
`olw_core`'s retry loop is shared by both callers, and until now both got the
same answer — which was tolerable at 5/10/20 and absurd at 30/60/120, where
a transient 503 would have held the app's UI for eight and a half minutes
before saying anything.

The two callers fail in opposite directions:

- `alarm_scheduler.dart` runs with **nobody watching**. Losing the run costs
  a forecast and the next chance is hours away, so it should sit out a bad
  minute rather than give up inside one.
- `app_state.dart` runs because **someone pressed a button and is holding
  the phone**. In the app the user IS the retry loop: the error screen has a
  retry button and they can see whether pressing it is worth their time. The
  pipeline has no such person, which is precisely why its loop lives in code.

So `RetryPolicy` now carries attempts, backoff and timeout as one decision,
with two named answers — `interactive` (2 attempts, 3s, 75s ceiling: ~2.5
min worst case) and `batch` (4 / 30s / 90s, matching the Python constants
deliberately, so one incident explains both implementations). The timeout
stays generous in both: cutting it is how a working forecast becomes a
failed one to save nothing.

Bundling the three into one type rather than leaving three knobs is the
point. They are only correct in combination, and the next section is what
happens when they drift apart.

### A clamp that silently stopped matching its own schedule

`retryAfter` (and Python's `_retry_after_seconds`) ignored any `Retry-After`
over 60 seconds. That was right when our own longest sleep was 20s. At
30/60/120 it became incoherent: a provider sending an authoritative
`Retry-After: 90` would have been **overruled in favour of sleeping 120
seconds on a guess.**

Both halves read as reasonable on their own, which is why this kind of defect
survives review. The ceiling is now derived — `RETRY_AFTER_MAX_S`, and
`RetryPolicy.retryAfterMax` — from one rule: *never sleep longer on a
provider's say-so than we would sleep on our own.* Tests in both languages
pin the derivation rather than the number, so the next schedule change cannot
re-open it.

### And a second, quieter leak found in the same reconciliation

`check-health`'s weekly model-deprecation call was **spending and recording
nothing.** The spend hook is deliberately attached after construction —
cli.py builds the provider, pipeline.py owns the ledger — and `check-health`
built one and attached nothing. Up to `MAX_ATTEMPTS` billable requests a
week, invisible to the cap and absent from the ledger.

Worse than uncapped: it also makes this project's own count disagree with the
provider's for reasons nobody can reconstruct, which is exactly the
reconciliation that found it. Fixed by lifting the attachment into
`_attach_spend_hook`, used by both paths, with a test that asserts the
behaviour rather than a constructor signature.

A cap refusal there is caught by the existing handler and reported as a
skipped check, which is the right outcome — being out of budget is a real
answer, not a reason to spend anyway.

Related: items 26, 53, 53.4, 58, 59.

---

## 67. A later issuance opens by narrating what is already over · **Shipped — unverified in production**

Reported 2026-09-03 as still happening every day. Measured on the 09-03
evening re-issue, first sentence of **Today's Forecast**:

> "As dusk falls and sunset approaches at 18:43 local time, daytime highs
> near 31°C / 88°F and solar UV exposure are in the past."

Three things a reader cannot use: the phase (already in the stat block), the
day's high (over), and UV (over). The forecast only starts on the second
sentence. Compare the same section on a morning run, 09-04:

> "Conditions across Kisumu will stay dry and warm through the day..."

Which leads with what is coming. **The defect is specific to the later
issuance**, which is the run a reader opens precisely because they want to
know what is left of the day.

### Item 31 assumed this was already handled

That item says the stat block is the problem and that "the statement handed
to the model already gets it right". The narrative half of that assumption is
false, and item 31 should be read with this beside it. Item 31's fix is still
wanted; it is not this one.

### The cause is not a tension. The prompt asks for it explicitly.

Found 2026-09-03, and it supersedes the guess below. `prompt.py` says, in the
section governing the narrative:

> "Anything already past is past tense, **or left out**. Issued at 16:45,
> 'peak UV index will reach 9.0 around noon' is wrong twice: noon has gone,
> and nothing can be done about it now. **Say the day's peak UV was 9 around
> midday** and what is left of it before sunset."

The rule offers two options and then demonstrates only one. The worked
example — the part a model actually copies — instructs it to state the spent
value in the past tense. So the 09-03 output is not the model resolving a
tension badly; it is the model doing precisely what it was told, and doing it
in the opening sentence because that is where the example puts it.

That rule was right about the error it was written for: a forecast at 16:45
must not say UV "will reach" 9 at noon. It over-corrected. "Do not describe a
past event in the future tense" became "narrate past events in the past
tense", and the second is a much larger instruction than the first.

**The fix is therefore a rewrite of an existing rule, not a new prohibition
bolted beside it** — which matters, because a contradicting rule added
further down is how a 451-line prompt becomes unpredictable. The distinction
to encode:

- A spent value that changes what a reader should DO is worth a clause. "The
  heat is behind you" is a fact someone can act on.
- A spent value that merely completes an inventory is left out. The day's
  high, the UV peak, and the phase are already in the stat block, and
  repeating them as history spends the first sentence of the section a
  reader opened to find out what happens NEXT.
- Never the future tense for something past. That was the original point and
  it stays.

### The earlier guess, kept because it was wrong in a useful way

`prompt.py` tells the model both of these, correctly and in the same
paragraph:

- write the prose for the hours AHEAD; and
- `today_properties.temp_high_c` stays the whole calendar day's high whether
  or not it has already happened, because narrowing it would break the scored
  comparison.

The model resolves the tension by SAYING which values no longer apply. That
is compliant, accurate and useless — the same shape as item 48's finding
(one fact stated four ways) and item 23's (a comparison applied where it
adds nothing). Technically right, and it costs the reader the first sentence
of the section they came for.

### The fix is a prohibition with the example attached

The rule that works in this prompt is the one that names the real output it
is banning — that is what items 23, 48 and 53.3 all converged on. Something
of the form: the prose covers the hours ahead, and does NOT enumerate which
of the day's values are already spent. A high that has passed is simply not
mentioned; it does not need a sentence explaining its absence.

Worth stating in the same breath: this must not push the model into the
opposite error of implying a past high is still coming. "Not mentioning" and
"misrepresenting" are different, and rule 7's discipline applies — silence
about a spent value is correct, a false future tense is not.

### What shipped, 2026-09-04

The rule was rewritten in place, in both `prompt.py` and its Dart mirror, and
split into the two instructions it had been conflating. Where it read

> "Anything already past is past tense, or left out. [...] Say the day's peak
> UV was 9 around midday and what is left of it before sunset."

it now reads, as two separately-headed rules:

> **OPEN ON WHAT IS STILL AHEAD.** [...] **LEAVE A SPENT VALUE OUT** unless
> it changes what the reader should DO [...] Where a spent value does still
> matter, it goes in a subordinate clause AFTER what is coming, never ahead
> of it.
>
> **NEVER THE FUTURE TENSE FOR SOMETHING PAST.** Issued at 16:45, "peak UV
> index will reach 9.0 around noon" is wrong twice [...] Omitting it is the
> first choice; if it does earn a mention, it reached 9 around midday - it is
> not going to.

The 09-03 sentence is now quoted in the prompt as the counter-example, which
is the only part of a worked example a model reliably copies.

**A contradiction closed as a side effect.** Rule 3 already carried "no 'peak
temperature already occurred' unless the number itself still matters for what
comes next" — the correct rule, thirty lines below a worked example telling
the model the opposite. The two now agree. This is worth noting because the
old state is the failure mode this prompt is most exposed to at 451 lines:
not a missing rule, but two rules that disagree, where the nearer one wins.

Guarded by `test_a_spent_value_is_omitted_rather_than_narrated_in_past_tense`,
which pins both halves — the old phrasing and its example must stay gone, and
the future-tense prohibition must survive. Watched failing against the old
prompt before the fix. 835 Python and 120 Dart tests pass; the regenerated
`llm_system_prompt.json` vector is what proves the two languages still emit
the same bytes.

**Not yet verified where it counts.** No replay has been run against this,
and no live evening issuance has been produced with it. Both are below.

### Validation

This is a prompt change, and `olw replay` (item 27) now exists specifically
so one can be checked for what else it moved. The evening-refresh cases in
the frozen set are the ones that matter here; the morning cases are the
control, and should not move at all.

Related: item 31 (the stat-block half, and the assumption this corrects),
item 23, item 48, item 27 (the harness), item 61 (the other Overview change).

---

## 68. The forecaster is two model versions behind · **Planned**

Raised 2026-09-03. `DEFAULT_GEMINI_MODEL` is `gemini-3.6-flash`; the current
Flash is 3.8. Nothing here is broken — the pipeline pins a model on purpose,
and drifting to whatever is newest would make the accuracy record
incomparable with its own past. But "pinned" and "two versions behind and
nobody decided" are different states, and this has been the second one.

### What already exists, and what does not

**Item 27 already owns the mechanism** and its unshipped half is exactly
this: A/B by alternating days, partitioned on `meta.llm_model`, reusing
`review.py`'s existing sample-size and noise-floor gates so an LLM comparison
is held to the same standard as GFS against ECMWF. That does not need
rebuilding.

**What is new is that half of it is now cheap.** Item 27 distinguishes "is
this model better", which only weeks of scored forecasts can answer, from
"did this change the output", which `olw replay` answers in minutes. A model
swap is replayable today: same frozen inputs, two models, diff the results.
That will not say which forecasts better. It will say whether 3.8 writes
materially different prose, whether it still obeys the structural rules the
prompt spends 451 lines establishing, and whether its `today_properties`
land in the same place — and a model that quietly stops honouring rule 7 is
something to find before it ships, not after.

### What the decision actually needs

1. **A replay diff, 3.6 against 3.8.** Cheap, immediate, and it answers the
   only question that can be answered immediately.
2. **Then a backtest, not alternating days** — revised 2026-09-04, and the
   reversal is item 69's. Alternating days confounds the model with the
   weather: a month of alternating gives two samples of DIFFERENT weather,
   not two forecasters, and days differ enormously in how predictable they
   are. Replaying both models over the same stored days gives a PAIRED
   comparison — same inputs, same outcomes, two probabilities — and delivers
   it in an afternoon instead of a month.

   Item 69 shipped 2026-09-04, so this is no longer blocked — but the
   archive starts EMPTY. Every day before that date is permanently
   un-backtestable, so the sample grows from zero at two issuances a day.
   A month of accumulation is the earliest this is worth running.
3. **Shadow mode only if the backtest is ambiguous.** Running the challenger
   alongside the incumbent on the same live run is statistically the same
   paired comparison, arriving a month later — worth paying for only when a
   burst could not resolve it. If it is used, the challenger call takes
   `attempts: 1`: a lost data point is cheap, and only the incumbent's
   failure costs a forecast.
4. **Latency and cost recorded alongside**, per item 27 — "slightly better
   and three times slower" is a real outcome and a legitimate reason to
   decline.

### The trap worth naming before anyone starts

**A model change is not additive to the accuracy record the way a new source
is.** Every stored check was produced by 3.6. If 3.8 becomes the default,
the record's `olw_blend` column silently becomes a blend of two different
forecasters, and its all-time figure describes neither. `meta.llm_model` is
already stored on every entry, so the record CAN be partitioned — but the
partition has to actually be used, or the published number becomes an
average over a change nobody can see.

### And the trap is smaller than it looks

Worth putting beside the paragraph above, because that paragraph on its own
reads as a reason to never change models. Nine columns are scored every day:

```text
gfs_seamless  ecmwf_ifs025  icon_seamless  ukmo_seamless
best_match    kenya_met     persistence    climatology    olw_blend
```

**One of the nine involves the LLM.** The other eight are NWP output, a
parsed bulletin, and two arithmetic baselines, and a model swap does not
touch any of them. "ECMWF runs warm at Day+3" and "persistence beats two of
five at Day+0" do not care what wrote the prose.

And `olw_blend`'s value measured against a FIXED reference survives even its
own column's discontinuity. Raw accuracy is not comparable across a model
change; Brier skill against climatology is, because climatology does not move
when the forecaster does. That is the argument for reporting skill rather
than hit-rate as the headline number, and item 58 shipped the machinery.

One contamination path that is NOT clean, and should be said out loud: the
LLM authors the verification notes and skill summaries that feed FORWARD into
later prompts. The numeric record is model-independent; the qualitative
memory is model-written and accumulates across a swap. Nothing currently
marks where the authorship changed — see item 70.

Related: item 27 (the mechanism and the harness), item 28 (the watcher that
would have flagged the version gap), item 58 (whose scoring makes a model
comparison sharper than a boolean one), item 69 (which unblocks the
backtest), item 70 (forecaster identity), item 71 (the ceiling probe).

---

## 69. The past is not replayable, because the inputs are not stored · **Shipped 2026-09-04**

Found 2026-09-04 while answering "how do we handle model updates long term".
The answer turned out to depend on a storage gap rather than on a schedule.

### What is stored, and what is missing

`data/log/YYYY-MM-DD.json` keeps the day's OUTPUTS in full — the narrative,
`today_properties`, all nine `model_predictions`, the verification, and
`meta`. What it does not keep is the prompt that produced them.

```text
stored:   guidance_source, guidance_initialised_at, guidance_age_hours
missing:  the guidance itself
```

Also missing: the hourly arrays, the synoptic pressure ring, per-model CAPE,
the Day+1..Day+7 blocks, the ground-station readings as they stood, and the
MODEL TRACK RECORD block as it was rendered that day. The user prompt is
built and thrown away.

### Why this is the item everything else waits on

**A model cannot be evaluated against a day whose inputs are gone.** That
single fact forces every model question into a forward experiment — run the
candidate live for a month and see — which is slow, costs a call a day, and
confounds the model with the weather.

With the inputs stored, the same question is a burst: 30 days x 1 call,
answered in an afternoon, paired by construction because both models see
byte-identical input and are scored against the same recorded outcome.

It also removes retirement as a category of problem. **You never needed the
dead model — you needed its inputs and the outcomes,** and both would be on
disk. A model that vanishes tomorrow can still be compared against its
replacement over every day it ever ran.

### Cost, measured rather than estimated

```text
user prompt              ~4.6 KB per run
runs                      2 per day
                        = ~3.4 MB per year

whole data/ directory      652 KB  (2026-08-11 to 2026-09-04)
```

The record is currently smaller than a photograph. This would make the inputs
the dominant term in it, and that is the correct outcome: the inputs ARE the
asset, and they are the half currently being discarded.

### Shape

Store the rendered user prompt per issuance, not a reconstruction recipe. A
recipe re-derives through code that will have changed by the time anyone
replays it, which reintroduces exactly the drift being avoided. The system
prompt does not need storing per run — it is vector-pinned and recoverable
from git, given item 70's hash to identify WHICH one.

Open question worth deciding before building: whether to store the raw
provider responses too. Larger, and it would allow re-deriving inputs under a
changed parser, which is a different and also useful capability. Not
obviously worth it; noted rather than assumed either way.

### What shipped

`store/prompt_archive.py`, written to `data/prompts/YYYY-MM-DD.json`, one
file per date matching `log_store`'s convention and for the same reason.
Issuances accumulate within a file — a morning run and an evening refresh are
separate forecasts from separate inputs, and keeping only the last would make
the day's first issuance permanently un-backtestable while the log still
showed it happened.

The user prompt is stored verbatim. The system prompt is stored as a SHA-256
only: ~34 KB against ~4.6 KB, already vector-pinned and recoverable from git —
but only if you know WHICH one ran, which is item 70's field and the reason
these shipped together.

Written beside the log entry rather than before the LLM call, deliberately.
An archived issuance with no log entry is a dangling input, backtestable
against nothing, because the pairing this exists to enable needs the
incumbent's own scored prediction on the other side.

`.github/workflows/forecast.yml` already does `git add data/`, so the archive
commits with everything else and needed no workflow change.

Guarded by `tests/test_prompt_archive.py` — nine tests, driven through the
REAL pipeline rather than the store, because the failure worth catching is
not "the writer is broken" but "the writer is never called" or "the refresh
archived the morning's prompt". Both paths build local `system_prompt` and
`user_prompt` variables and archiving the wrong one produces an archive that
looks complete, replays cleanly, and answers about the wrong run.

**Note for whoever implements log retention.** `LOG_RETENTION_DAYS` is 180 and
documented as deliberately unimplemented, so nothing deletes anything today.
When it is implemented, prompts must not be pruned ahead of logs: inputs
without outcomes are unscoreable, and outcomes without inputs are exactly the
state this item existed to end.

Related: item 68 (which this unblocks), item 70, item 71, item 72, item 27.

---

## 70. The record cannot say which forecaster produced an entry · **Shipped 2026-09-04**

`meta` stores `llm_provider`, `llm_model` and `pipeline_version`. The first
two are real. The third is the string `"0.1.0"` and has never changed.

The prompt was edited twice on 2026-09-04 alone — item 67's rewrite and item
58's probability field — and **the record cannot tell those entries from the
ones before them.**

### The forecaster is not the model

It is (model + prompt + input set), and all three drift. Versioning only the
model gives the appearance of rigour without it, because prompt changes are
far more frequent than model changes and at least as capable of moving the
output. Item 67's rewrite changed what the opening sentence of every evening
forecast is allowed to contain; that is not a smaller intervention than a
minor version bump.

### Shape

A hash of the rendered system prompt in `meta`, alongside `llm_model`. One
line to compute, and it makes "which forecaster produced this entry" a
question the record can answer instead of one requiring `git log` and a guess
about deploy timing.

The published number should then partition on the pair, not on the model
alone — the same discipline item 68 asks for, applied to the axis that
actually moves week to week.

### What shipped

`meta.system_prompt_sha256`, alongside `llm_model`. Three-valued like
`degradations`: `None` means the entry predates the field and its prompt is
genuinely unrecoverable — only deploy timing in git says which one ran. A
default of `""` would claim an identity those runs never had, and a backtest
harness would then try to resolve a hash matching nothing.

An evening refresh overwrites it with its own, rather than carrying the
morning's forward. `is_reissue=True` alone makes them different documents,
and this field names the forecaster that wrote the narrative currently in the
entry.

`pipeline_version` is left as it is. It is not load-bearing, and repurposing
a field that has read `"0.1.0"` since the first commit would make old entries
mean something new.

Related: item 68, item 69, item 27.

---

## 71. How much headroom is in the LLM layer at all? · **Planned**

Proposed by the operator 2026-09-04, and it answers a question none of the
other model items ask.

### The question

68 asks "is 3.8 better than 3.6" — a candidate trial between two things that
could plausibly run in production. **71 asks whether the LLM layer is the
bottleneck at all.** Run a frontier model over the same stored days as a
reference point, and compare skill.

The value is in the answer either way:

- **If a frontier model is barely better than free-tier Flash**, the LLM is
  not the constraint, and every hour spent on model selection is an hour not
  spent on sources. That matches the operator's stated position — the binding
  constraint is source quality, not count — and would turn it from a belief
  into a measurement.
- **If it is substantially better**, there is real headroom, and the
  interesting question becomes which part of the prompt the smaller model is
  failing to execute. That is diagnosable by diffing the two outputs on the
  same input, which is what item 27's replay already does.

### It is a reference measurement, not a candidate

The frontier model is not a production candidate here — the entire point of
this project is a free or near-free API. Recording it as a dated ceiling is
the use:

> Opus 5, 2026-09-04, scored X over these N days.

The operator's expectation is that free-tier Flash reaches roughly today's
frontier capability inside a year. That is a testable claim, and it needs a
fixed dated marker to test against. Re-running whatever is free in a year
against the SAME stored days and the SAME recorded outcomes answers it.

### Three limits, none fatal, all of them design constraints

1. **The scorer must not be able to see the answer.** The outcomes sit in
   `data/actuals_cache/` on the same filesystem as everything else. A model
   asked to forecast a stored day, with repo access, can simply look it up —
   and would not even be cheating on purpose. **The probe has to run in a
   subprocess given the prompt and nothing else.** Not a detail; it is the
   difference between a measurement and a number.
2. **A session that helped write the prompt is not a clean subject.** It
   knows the failure modes, the roadmap, and what each rule was written to
   prevent. The subprocess handles this too, as long as it is handed the
   prompt rather than the conversation.
3. **Wide error bars, and a narrower target than expected.** Twenty or
   thirty days of rain Brier is a small sample, and two forecasters separated
   by 0.03 are separated by noise. Worse, item 72: `olw_blend` is scored at
   Day+0 only, which is exactly where the free NWP is already near its
   ceiling. Expect "indistinguishable", and do not read it as "models do not
   matter".
   `review.py`'s existing noise-floor gates apply here exactly as they do to
   GFS against ECMWF, and the honest answer may well be "indistinguishable at
   this sample size" — which is itself informative, and is the first outcome
   above.

Unblocked 2026-09-04 by item 69, and gated by the same thing 68 is: the
archive begins empty and fills at two issuances a day.

Related: item 69, item 68, item 27, item 58 (whose Brier makes this a number
rather than an impression).

---

## 72. The forecaster is only ever scored where the NWP is already near its ceiling · **Planned**

Found 2026-09-04 while asking how much a model change could be expected to
move anything. The record answers it, and the answer is uncomfortable.

### What the record says today

Rain hit-rate, all-time, by lead time:

```text
Day+0   best_match  87.5% (n=24)    olw_blend  85.7% (n=7)
        persistence 75.0% (n=24)    climatology 54.2% (n=24)

Day+3   best_match  90.5% (n=21)    olw_blend    n/a (n=0)
Day+7   gfs         82.4% (n=17)    olw_blend    n/a (n=0)
```

**`olw_blend` has no Day+3 or Day+7 entries, and never will.** This is not a
sample that will fill in with time. `_blend_prediction` in `pipeline.py`
builds from `today_properties`, which is by definition today, so the blend is
structurally a Day+0-only forecaster. The Extended Outlook covers days 1-7
and is prose — nothing in it is scored, or scoreable.

### Why that is the wrong place to be measured

Day+0 is where the free NWP is already strongest. `best_match` alone gets
87.5%, and the honest reading of `olw_blend`'s 85.7% is **"indistinguishable
at n=7"** — six of seven, where one more miss makes it 75%. The interesting
claim is not that the blend is behind; it is that at Day+0 there is almost no
room above `best_match` for it to demonstrate anything.

Meanwhile the LLM's most defensible job — reconciling models that disagree —
is hardest and most valuable at Day+3 and Day+7, where the spread is widest.
At Day+7 the gap between GFS (82.4%) and best_match (64.7%) is 17.7 points,
and there is no scored blend to compare against either.

So the forecaster is measured exclusively where it can least distinguish
itself, and is unmeasured everywhere it might.

### What this means for items 68 and 71

**It bounds them.** A model comparison run against today's record can only
compare Day+0 rain booleans on a handful of checks. Both items should expect
"indistinguishable at this sample size", and should NOT read that as "models
do not matter" — it is at least as likely to mean the measurement cannot see
what it is looking for.

Item 58's Brier helps (a probability carries more information per check than
a boolean) and does not fix it, because the lead-time gap is structural
rather than statistical.

### Shape

Structured, scoreable fields for Day+1..Day+7 in the response schema, mirrored
into the prediction record so the blend is checked at the lead times the
existing models are already checked at. Then `olw_blend` appears in all three
tables and the comparison becomes a real one.

Not small: it widens the schema, the storage and the two-language surface, and
it makes every forecast a larger structured commitment. It is also the change
that makes "is the forecaster any good" answerable, so it is worth doing
properly rather than as a fourth column bolted onto Day+0.

Sequencing note: **69 still comes first.** Days not stored cannot be
backtested, and this item changes what future days record without recovering
any past ones.

Related: item 68, item 69, item 71, item 57 (which established the baselines
this is read against), item 58, item 27.
