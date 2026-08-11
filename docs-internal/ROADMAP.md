# Roadmap

Ordered roughly by value-per-effort. Each item states the problem first,
because several of these look obvious until you hit the actual constraint.

Status legend: **Next** · **Planned** · **Deferred** · **Done**

---

## 1. Second daily forecast run — 6 AM + 6 PM · **Next**

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
| Cron | `0 3 * * *` (unchanged) | `0 15 * * *` |
| Delivery | 06:00 EAT | 18:00 EAT ✓ your target |
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

### Prompt note

The evening narrative shouldn't read like a stale copy of the morning's. Add
a mode-aware line to the system prompt: for the evening run, lead with
tonight and tomorrow, and explicitly note what changed since the morning
issuance — that's the reason someone would read both.

### The design question this forces

A second run re-forecasts a day that already has a committed entry. What
happens to `data/log/YYYY-MM-DD.json`?

This matters more than it looks, because that file's `model_predictions`
are what tomorrow's verification scores. Overwrite them and you're no
longer scoring what you actually published at 6 AM.

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

### Prerequisite — already handled

A second run would have double-counted `all_time_checks` every single day
(the counters are the one non-self-healing piece of state). Fixed and
regression-tested via the `last_verified_target_date` guard before this item
was written. Refresh mode skipping verification makes it doubly safe.

### Tasks
- [ ] `--mode=refresh` in `cli.py` + `pipeline.py` (skip verify, preserve predictions)
- [ ] `meta.refreshed_at` field on `DailyLogEntry`
- [ ] Second workflow (or a matrix/input on `daily.yml`) at `0 14 * * *`
- [ ] Site shows issue time so readers can tell morning from evening
- [ ] Decide whether the evening refresh also emails, or is web-only

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
                              surface banner on site, trigger alert email
```

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
delivers *"usually within the hour"*, **not** *"within minutes"*. That is
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
| `.github/workflows/alerts.yml` | ~0.5 h | Minimal deps, `0 * * * *` |
| Site banner + `docs/alerts/` | ~1.5 h | New template + publisher hook |
| `sendAlertEmail()` in the mailer | ~1.5 h | New Apps Script function + trigger + harness cases |
| `fetch/alerts/gdacs.py` | ~1.5 h | Real feed; geo-filter is the fiddly bit |
| Scraper-health alarm | ~0.5 h | Zero-posts-parsed ⇒ notify, never "all clear" |

A useful first slice is the first four rows plus the health alarm: that
gets alerts onto the site, with email and GDACS following once the dedup
behaviour has been observed against real KMD posting patterns for a while.

### Tasks
- [ ] `fetch/alerts/kenya_kmd_warnings.py` — scrape, parse, stable IDs
- [ ] `store/alerts_store.py` — `alerts_seen.json`, dedup, `alerts/<id>.json`
- [ ] `llm/alert_triage.py` — relevance/severity/summary schema
- [ ] `.github/workflows/alerts.yml` — `0 * * * *`, minimal deps
- [ ] Scraper-health alarm (zero-posts-parsed ⇒ notify, don't assume calm)
- [ ] Site alert banner + `docs/alerts/`
- [ ] `sendAlertEmail()` in the Apps Script mailer, own trigger
- [ ] `fetch/alerts/gdacs.py` — RSS + bounding-box filter

---

## 3. Real sending domain for email · **Planned**

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

## 4. Verify secondary-point predictions · **Planned**

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

## 5. Operational hardening · **Planned**

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

## 6. Multi-provider LLM support · **Planned**

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

## 7. WhatsApp distribution · **Deferred**

Carried over from the original design. Meta's Cloud API loses its free
service-message window on 2026-10-01; cost at small subscriber counts would
be trivial but not zero.

**Do not** build on unofficial WhatsApp-Web automation libraries — ToS
violation with real account-ban risk.

The `whatsapp_summary` field already exists in the LLM response schema and
is populated, so the content side is ready whenever the delivery side is
worth doing.

---

## Completed

- Multi-model fetch + synthesis pipeline, git-as-database, GitHub Pages
- Deterministic lead-time verification and rolling skill tracking
- KMD bulletin PDF extraction (confirmed text-based; no OCR needed)
- Weekly model-deprecation + repo-staleness health check
- Apps Script email delivery with retry against scheduler jitter
- Review fixes: all-time double-count guard, archive backfill, LLM retry
