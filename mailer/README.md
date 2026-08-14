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

**Email format**: plain text, styled as a nod to NOAA's Area Forecast
Discussion (AFD) product — dot-leader `.SECTION...` headers, `&&` segment
dividers, prose reflowed to a fixed width — rather than a styled marketing-
style HTML layout. `buildEmailPlainText()` builds the one true body; an
identical `<pre>`-wrapped Courier `htmlBody` is generated from that same
text (never independently) so the two representations can't drift apart.
Every email carries an explicit beta/experimental disclaimer and a link to
the live site — see `buildEmailPlainText()`'s doc comment in
[`AppsScriptMailer.gs`](AppsScriptMailer.gs) for the full rationale.

**Two independent sends, one per pipeline run**: `sendDailyForecastEmail()`
pairs with the morning full run (~06:07 EAT), `sendEveningRefreshEmail()`
pairs with the evening refresh run (~18:07 EAT) — see
[ARCHITECTURE.md](../docs-internal/ARCHITECTURE.md) for why that second run
exists. The evening send additionally waits for `meta.refreshed_at` to
actually be set on the day's entry (not just for the file to exist), so it
can't accidentally resend the unrefreshed morning content under an
"Evening Update" subject if the evening pipeline run is late or fails.

**Each send fires from several trigger slots per day, not one.** Real
evidence forced this: GitHub Actions' own scheduling has produced both
total no-shows and multi-hour clustered delays on this repo (see
`docs-internal/ROADMAP.md` items 3/11 and `ops/README.md`) — a single
mailer trigger with a few minutes of retry can't catch a pipeline that
lands two hours late. `MORNING_TRIGGER_SLOTS`/`EVENING_TRIGGER_SLOTS` in
`AppsScriptMailer.gs` spread each send across ~2 hours instead.
`createDailyTrigger()`/`createEveningRefreshTrigger()` register the full
slot set for their own handler and only ever touch *their own* handler's
triggers, never the other one — safe to re-run either independently, and
re-running one cleanly replaces its own slot set rather than
accumulating duplicates. Because several slots now call the same
`send*Email()` function on a normal day, each is guarded by a same-day
idempotency check (`alreadySentToday()`, backed by a `LAST_SENT_MORNING` /
`LAST_SENT_EVENING` Script Property) — only the first slot that finds
real, ready data actually sends; every later slot that day is a fast,
cheap no-op rather than a duplicate email to every subscriber.

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
