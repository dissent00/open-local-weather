# Quickstart — your own daily forecast

This walks you from nothing to a working daily forecast for **your** town:
a public web page, a daily email, and a git-versioned accuracy record that
improves the forecast over time.

**Time:** about an hour, most of it waiting for things to save.
**Cost:** $0 is achievable — see [What this costs](#what-this-costs).
**Prerequisites:** a GitHub account. That's genuinely it; everything else
is set up below.

You do not need to know Python. You will not run any code locally unless
you want to.

---

## What you'll have when you're done

- A page like `https://<you>.github.io/open-local-weather/` refreshed twice
  a day.
- An email each morning (and optionally each evening).
- `data/log/YYYY-MM-DD.json` committed daily — every prediction from every
  weather model, plus how it later scored. This is the file the system
  reads back to get better, and it's yours forever in git history.

---

## Step 1 — Get your own copy

Click **Fork** on
[dissent00/open-local-weather](https://github.com/dissent00/open-local-weather).

Then **two things that are easy to miss**:

**1a. Enable Actions.** Open the **Actions** tab in your fork. GitHub
disables workflows on forks by default and shows a banner — click the
button to enable them. Skip this and nothing will ever run, with no error
to tell you why.

**1b. Clear the previous location's history.** Your fork arrives carrying
Kisumu's forecast history. This is not cosmetic: the accuracy loop scores
*stored past predictions* against *yesterday's real weather*. Leave
Kisumu's data in place and your first runs will score Kisumu's predictions
against your town's weather, generating meaningless accuracy statistics
that then get fed to the LLM as its "track record."

Easiest way — in your fork, open a terminal (or use github.dev by pressing
`.` on the repo page) and run:

```bash
git rm -r --cached data/log/*.json data/track_record.json data/actuals_cache/actuals.json docs/archive docs/index.html
rm -rf data/log/*.json data/track_record.json data/actuals_cache/actuals.json docs/archive docs/index.html
git commit -m "Clear upstream location's history"
git push
```

Keep `data/log/.gitkeep`, `data/actuals_cache/.gitkeep`, and
`docs/assets/style.css` — those are structure, not data.

> Accuracy statistics need roughly 10 verified days before they mean
> anything. The system says so in its own forecast text rather than
> pretending otherwise, so early forecasts are honest about being new.

---

## Step 2 — Describe your location

Edit **`config/location.yaml`** — this is the only file you need to change.
[`config/location.example.yaml`](config/location.example.yaml) documents
every field.

The minimum that produces a good forecast:

```yaml
location:
  region_name: "Wairarapa"
  primary_place_name: "Masterton, New Zealand"
  timezone: "Pacific/Auckland"        # any IANA timezone name

  primary_point:
    lat: -40.951
    lon: 175.658

  secondary_point:
    enabled: false                    # set true for a lake/coast/mountain

  region_points:                      # 3-6 nearby towns; drives the
    - name: "Carterton"               # regional pressure discussion
      lat: -41.021
      lon: 175.526
    - name: "Featherston"
      lat: -41.115
      lon: 175.331

  metar_station_icao: ""              # nearby airport, e.g. "NZMS" — or ""
  waqi_stations: []                   # see Step 6 (optional)
  local_bulletin_url: ""              # leave empty; see below
  local_bulletin_source_name: ""
```

Find coordinates by right-clicking a spot in Google Maps. Timezone names
are the `Region/City` form from the
[IANA list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

**About `local_bulletin_url`:** this pulls in your national weather
service's own written bulletin as extra context. It's genuinely
location-specific scraping — the shipped example
(`src/openlocalweather/fetch/bulletin/kenya_kmd.py`) is written for Kenya's
met service specifically and won't work elsewhere. Leave it empty; the
pipeline skips it cleanly. Writing one for your country is a nice later
project.

Commit that file.

---

## Step 3 — Pick an LLM and get a key

The forecast text is written by an LLM. **Gemini is the default and has a
free tier**, so start there unless you have a reason not to.

| Option | Cost | Where to get a key |
|---|---|---|
| **Google Gemini** *(default)* | Free tier, generous | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Anthropic Claude** | Paid | [console.anthropic.com](https://console.anthropic.com) |
| **OpenAI** | Paid | [platform.openai.com](https://platform.openai.com) |
| **OpenRouter** | Pay-per-use; some free models | [openrouter.ai](https://openrouter.ai) |
| **Groq** | Free tier | [console.groq.com](https://console.groq.com) |
| **Ollama** (local) | Free | Runs on your own machine — [see caveat](#ollama-and-other-local-models) |

One run uses roughly 45,000 tokens. Two runs a day is comfortably inside
Gemini's free tier.

### Ollama and other local models

A local model is free, but **GitHub's servers cannot reach your laptop**.
Local models only work if you run the pipeline yourself — on your own
machine or a server you control — rather than on GitHub Actions. See
[`ops/README.md`](ops/README.md) for that path. If you want the
zero-infrastructure setup this guide describes, use a hosted provider.

---

## Step 4 — Add your key and settings to GitHub

In your fork: **Settings → Secrets and variables → Actions**. That page has
two tabs, and the distinction matters — *Secrets* are hidden in logs,
*Variables* stay readable, which makes a typo'd model name debuggable
instead of showing up as `***`.

### If you're using Gemini (the default)

Under the **Secrets** tab → *New repository secret*:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | your key from Step 3 |

That's all. No variables needed — Gemini is the default.

### If you're using Anthropic Claude

**Secrets** tab:

| Name | Value |
|---|---|
| `LLM_API_KEY` | your Anthropic key |

**Variables** tab:

| Name | Value |
|---|---|
| `LLM_PROVIDER` | `anthropic` |
| `LLM_MODEL` | e.g. `claude-sonnet-5` — check [the model list](https://docs.claude.com/en/docs/about-claude/models) for current ids |

### If you're using OpenAI, OpenRouter, Groq, or anything OpenAI-compatible

**Secrets** tab:

| Name | Value |
|---|---|
| `LLM_API_KEY` | your key for that service |

**Variables** tab:

| Name | Value |
|---|---|
| `LLM_PROVIDER` | `openai` |
| `LLM_MODEL` | the model id, e.g. `gpt-4.1` or `meta-llama/llama-3.3-70b-instruct` |
| `LLM_BASE_URL` | see below |

| Service | `LLM_BASE_URL` |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Together | `https://api.together.xyz/v1` |

> If a run fails complaining about `response_format` or `json_schema`, that
> endpoint doesn't support strict structured output. Add a variable
> `LLM_JSON_MODE` = `json_object` and try again.

---

## Step 5 — Turn on GitHub Pages

**Settings → Pages → Build and deployment → Source: Deploy from a branch**,
then choose branch `main` and folder **`/docs`**. Save.

Without this the forecast still runs and commits, but no web page ever
appears and the links in your emails go nowhere.

Your site will be at `https://<your-username>.github.io/<repo-name>/`.

---

## Step 6 — *(Optional)* Ground air-quality sensors

Skip this if you don't care about air quality; everything works without it.

The forecast can compare model-predicted air quality against real ground
sensors. Find stations near you at [waqi.info](https://waqi.info), then get
a free token at [aqicn.org/data-platform/token](https://aqicn.org/data-platform/token/).

Add secret `WAQI_TOKEN`, and list the stations in `config/location.yaml`:

```yaml
  waqi_stations:
    - name: "Airport"
      station_id: "A418534"
```

**Verify each station id by hand** — a wrong id silently poisons the
"ground truth" comparison rather than erroring.

---

## Step 7 — Run it once, and check that it worked

Don't wait for the schedule. **Actions → Daily Forecast → Run workflow**.

Give it a couple of minutes, then verify all three:

1. The run is green in the Actions tab.
2. A new file exists at `data/log/<today>.json`.
3. Your Pages URL shows the forecast.

If something failed, jump to [Troubleshooting](#troubleshooting).

Once this works, you have a working forecast. Everything below is delivery
and reliability.

---

## Step 8 — Email

Email goes through a small Google Apps Script, not the pipeline. That's a
deliberate workaround, not laziness: sending real mail from a third-party
service requires a verified domain with DKIM/SPF/DMARC under the 2024
bulk-sender rules, and a plain Gmail address can never satisfy that.
Google's own `MailApp` sends through Google's infrastructure with no app
password and no domain of your own.

Full instructions are in the header comment of
[`mailer/AppsScriptMailer.gs`](mailer/AppsScriptMailer.gs). Short version:

1. [script.google.com](https://script.google.com) → **New project**.
2. Replace `Code.gs` with the contents of `mailer/AppsScriptMailer.gs`.
3. **Project Settings → Script Properties**, add (values **without**
   quotes):
   - `SUBSCRIBER_EMAILS` — `you@example.com,friend@example.com`
   - `GITHUB_REPO` — `your-username/open-local-weather`
   - `GITHUB_BRANCH` — `main`
   - `LOCATION_NAME` — must match `primary_place_name`
   - `TIMEZONE` — must match your `timezone`
   - `PUBLIC_URL` — your Pages URL, e.g.
     `https://you.github.io/open-local-weather/`
4. Run `createDailyTrigger` once from the editor. Approve the permissions
   prompt — that consent screen *is* the authentication; there's no app
   password step.
5. If you also want the evening update, run `createEveningRefreshTrigger`
   once too.

Recipients are managed by editing `SUBSCRIBER_EMAILS`. There's no
self-serve signup form — that needs a verified domain, which is why it
isn't here.

---

## Step 9 — *(Recommended)* Make the schedule reliable

GitHub's own documentation admits that under load, scheduled workflows may
be delayed **or dropped entirely**. This project has hit both: two complete
no-shows, and a morning where every backup slot fired about two hours late.

The workflows already schedule four attempts per run to soften this. If
you want it genuinely dependable, trigger it from outside GitHub — a free
hosted cron service like [cron-job.org](https://cron-job.org) can call
GitHub's API on schedule with no server of your own.

[`ops/README.md`](ops/README.md) has the field-by-field setup, including
the security notes for the access token it needs.

---

## Troubleshooting

**The workflow never ran.**
Actions aren't enabled on your fork (Step 1a), or the schedule was dropped
by GitHub (Step 9). Use **Run workflow** to trigger it by hand.

**`GEMINI_API_KEY environment variable is required`**
The secret isn't set, or is named differently. Check
Settings → Secrets and variables → Actions → *Secrets*. Note that using a
non-Gemini provider needs `LLM_API_KEY`, not `GEMINI_API_KEY`.

**`LLM_PROVIDER=openai requires LLM_BASE_URL and LLM_MODEL`**
Those go under the *Variables* tab, not *Secrets* (Step 4).

**The run is green but there's no web page.**
Pages isn't enabled, or is pointed at the wrong folder — it must be branch
`main`, folder `/docs` (Step 5).

**`No forecast entry found for <date>` in the Apps Script logs.**
The mailer ran before the pipeline committed. It retries on its own; if it
happens daily, the pipeline is running late (Step 9).

**Email links show `" "` instead of a URL.**
`PUBLIC_URL` was entered with quote marks around it. Re-enter it bare.

**Accuracy numbers look wrong or absurd.**
You probably still have the upstream location's history (Step 1b).

**A model shows no Day+7 data.**
Expected. Not every model forecasts that far — UKMO stops around 7.2 days.
Missing data is recorded as unknown, never as "no rain."

---

## What this costs

| | |
|---|---|
| GitHub Actions | Free — unlimited minutes on public repos |
| GitHub Pages | Free |
| Weather data (Open-Meteo) | Free, no key needed |
| Air quality (WAQI) | Free token |
| Email (Apps Script) | Free, ~100 recipients/day on a consumer account |
| LLM | Free on Gemini's free tier; otherwise a few cents a day |

A public repo is required for the free Actions minutes. Your forecast data
is public either way — that's the point of the auditable record.

> **One caveat on "free forever":** GitHub disables scheduled workflows
> after 60 days with no repository activity. A working deployment commits
> daily, so it stays active on its own — but a *broken* one goes quiet and
> then gets switched off. The weekly health check watches for this.

---

## Where to go next

- [`README.md`](README.md) — what the system does and the principles behind it
- [`docs-internal/ARCHITECTURE.md`](docs-internal/ARCHITECTURE.md) — how it works and why
- [`docs-internal/ROADMAP.md`](docs-internal/ROADMAP.md) — what's planned
- [`ops/README.md`](ops/README.md) — scheduling and deployment options
- [`mailer/README.md`](mailer/README.md) — email delivery details

Something in this guide wrong or unclear? That's a bug — please open an
issue.
