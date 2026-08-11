# Roadmap

Ordered roughly by value-per-effort. Each item states the problem first,
because several of these look obvious until you hit the actual constraint.

Status legend: **Next** · **Planned** · **Deferred** · **Done**

---

## 1. Second daily forecast run — fresher model data · **Next**

### The problem, measured

The daily run fires at 03:00 UTC (06:00 EAT). Model runs land on a delay,
measured from Open-Meteo's own per-model metadata endpoints
(`https://api.open-meteo.com/data/{model}/static/meta.json`) on 2026-08-11:

| Model | Delay after run time | 00z lands at |
|---|---|---|
| ICON | ~3.8 h | ~03:48 UTC |
| GFS 0.13 | ~6.6 h | ~06:36 UTC |
| ECMWF IFS 0.25 | ~7.1 h | ~07:06 UTC |
| UKMO 10 km | ~7.3 h | ~07:18 UTC |

At 03:00 UTC **none of the 00z runs have landed**. The morning forecast is
therefore built almost entirely on the *previous day's 18z* runs — roughly
9 hours stale at issue time, and initialised before the previous evening.

Moving the morning run later would fix the staleness but breaks the 6 AM
email, which is the actual product. So: add a second run instead.

### Recommended timing: 14:00 UTC (17:00 EAT)

By 13:19 UTC every model's **06z** run has landed (ICON ~09:48, GFS ~12:38,
ECMWF ~13:07, UKMO ~13:19). 14:00 UTC gives a ~40-minute safety margin.

That's a model cycle initialised **12 hours later** than what the morning
run used, and 17:00 EAT is a sensible "this evening and tonight" delivery
slot for readers.

Rejected alternative: waiting for the 12z runs (all landed by ~19:30 UTC =
22:30 EAT) — genuinely freshest, but too late at night to be useful.

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

**Cost:** hourly is 24 runs/day, free on a public repo. The LLM is only
called when something new appears (rare — a handful of times a month), so
Gemini free-tier limits are a non-issue.

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

### Tasks
- [ ] `fetch/alerts/kenya_kmd_warnings.py` — scrape, parse, stable IDs
- [ ] `fetch/alerts/gdacs.py` — RSS + bounding-box filter
- [ ] `store/alerts_store.py` — `alerts_seen.json`, dedup, `alerts/<id>.json`
- [ ] `llm/alert_triage.py` — relevance/severity/summary schema
- [ ] `.github/workflows/alerts.yml` — `0 * * * *`
- [ ] Site alert banner + `docs/alerts/`
- [ ] `sendAlertEmail()` in the Apps Script mailer, own trigger
- [ ] Scraper-health alarm (zero-posts-parsed ⇒ notify, don't assume calm)

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
