# Email mailer (Google Apps Script)

`AppsScriptMailer.gs` sends the daily forecast email to subscribers. It is
a **standalone companion** to the main Python/GitHub Actions pipeline in
this repo — it does not run via GitHub Actions, has no dependency on this
repo's Python package, and runs entirely on its own daily trigger inside
Google's infrastructure.

**Why a separate Apps Script instead of sending email from the pipeline
itself:** real subscriber email needs either a verified custom domain
(what a third-party ESP like Brevo requires under Google/Yahoo/Microsoft's
2024 bulk-sender rules) or a Gmail "app password" for direct SMTP — and
app passwords aren't available on all Google accounts. `MailApp` sidesteps
both: it sends through Google's own infrastructure under this script's
normal OAuth authorization, no app password needed, and it isn't sending
from a shared/rotating CI IP.

It fetches the day's forecast as structured JSON straight from GitHub's
raw-content CDN (`data/log/YYYY-MM-DD.json`) — not a scrape of the
rendered [GitHub Pages site](https://dissent00.github.io/open-local-weather/) —
so it's immune to any future page-template change.

**Email format**: two independently-built representations of the same
`entry`/`config` data, not one derived from the other:
- `body` (plain text) — styled as a nod to NOAA's Area Forecast Discussion
  (AFD) product: dot-leader `.SECTION...` headers, `&&` segment dividers,
  prose reflowed to a fixed width. What non-HTML clients see.
- `htmlBody` — styled to match the [GitHub Pages
  site](https://dissent00.github.io/open-local-weather/) itself: system
  font, the same High/Low/Rain/Onset/UV/AQI stat-grid, narrative rendered
  as real `<h2>`/`<h3>`/`<p>`/`<ul>` HTML rather than monospace text. What
  most subscribers actually see, since HTML-capable clients prefer
  `htmlBody` over `body` when both are present.

An earlier version made `htmlBody` a literal `<pre>`-wrapped copy of the
AFD plain text, on the theory that guaranteed the two could never drift
apart. Replaced on request — what's guaranteed now is CONTENT consistency
(same narrative, same stats, same disclaimer, same links), built from the
same inputs, not byte-identical text; see `buildEmailHtml()`'s doc comment
in [`AppsScriptMailer.gs`](AppsScriptMailer.gs) for the full rationale,
including why the site's per-station Ground AQI section was deliberately
left out (would mean re-implementing `aqi.py`'s staleness logic a second
time in JS).

Every email carries an explicit beta/experimental disclaimer — deliberately
placed near the *bottom* of both representations, after the forecast
content, not as the first thing a subscriber sees — and a link to the live
site.

**One send function, one trigger.** `sendForecastEmail()` checks every
`CHECK_EVERY_MINUTES` (default 30) and sends whenever it finds an issuance it
has not sent yet — identified by the entry's own timestamp,
`meta.refreshed_at` once re-issued and `meta.generated_at_utc` otherwise.

This replaced a morning/evening pair with fixed slot lists. Two things made
that design obsolete. The pipeline can now be scheduled to run **any number of
times a day** and each run knows what time it is, so "the evening one" stopped
being something the mailer should know about. And the several slots per send
were never issuances — they were *retries*, added after real evidence that
GitHub Actions' scheduling can be badly late: this repo's run history shows
`daily.yml` producing zero scheduled runs on two separate occasions, and on
another day every backup cron slot firing 1h49m–2h13m late as a cluster (see
`docs-internal/ROADMAP.md` items 3/11 and `ops/README.md`).

Keying on *"have I sent this issuance?"* covers both at once. A pipeline that
lands two hours late is picked up by whichever check follows it; one that never
ran is simply never sent; and a fourth run a day needs no configuration here.

**Late-evening runs and midnight.** In the first `YESTERDAY_GRACE_HOURS`
(default 3) of a new day the mailer also checks *yesterday's* entry. A forecast
issued at 23:50 that no check reached before midnight would otherwise be lost
for good — from 00:00 the mailer asks for the new day's file and never looks
back. The old fixed slots ran 18:20–20:20 so this could not happen; it can now
that a run may be scheduled at any hour.

**There is no in-check retry, and that is deliberate.** With a 30-minute poll
the *next check* is the retry, at no execution cost. Keeping the old sleeping
retry would have been actively harmful: every check before the day's first
forecast finds no file and would sleep ~3 minutes, which from midnight to a
06:07 run is roughly 36 minutes of a consumer account's 90-minute **daily**
runtime quota, spent waiting for a file that is not due yet.

**The mailer does not decide when forecasts happen.** The pipeline's own
schedule does. There is nothing in `AppsScriptMailer.gs` to keep in sync with
it — `CHECK_EVERY_MINUTES` only controls how soon after publication an email
goes out.

**Upgrading from the morning/evening version**: run `createTriggers()`, then
`removeLegacyTriggers()` once. Apps Script does not delete a trigger when its
handler disappears — it keeps firing, failing, and emailing you about it. The
old `LAST_SENT_MORNING` / `LAST_SENT_EVENING` Script Properties are ignored and
can be deleted.

See the setup instructions in the header comment of
[`AppsScriptMailer.gs`](AppsScriptMailer.gs) for deployment steps
(script.google.com, Script Properties, authorizing both triggers).

**Before deploying a change**, run the verification harness (mocks Apps
Script's globals — `PropertiesService`, `UrlFetchApp`, `MailApp`, etc. —
and exercises the real script source against a real forecast entry fixture):

```bash
node mailer/test_mailer.js
```

This isn't part of the Python test suite or CI — it's a manual check, same
as this whole script is a manual deployment outside GitHub Actions.

Migrating to a verified-domain ESP later (e.g. once Brevo's domain
verification is sorted) means writing a `publish/email_brevo.py`
`EmailSender` and wiring it into `cli.py` — a one-line swap in
`pipeline.py`'s dependency injection, no changes needed here or there.
