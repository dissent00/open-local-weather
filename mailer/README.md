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

See the setup instructions in the header comment of
[`AppsScriptMailer.gs`](AppsScriptMailer.gs) for deployment steps
(script.google.com, Script Properties, authorizing the trigger).

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
