# Operational extras (deployment-specific)

`ops/` is one way to trigger the pipeline; it is not the only one, and
nothing here is part of the forecast logic. Pick a path below — since
`forecast.yml` carries no `schedule:` block, picking one is required, not
an upgrade. Same core pipeline whichever you pick.

## Why this exists

GitHub's own docs for the `schedule` event say: *"some queued jobs may be
dropped"* under high load. Confirmed on this repo, three times, in three
different failure shapes:

- `daily.yml`'s schedule trigger produced **zero runs** across its first
  two real scheduled occurrences (once on an exact-hour cron, once on the
  off-hour fix meant to help) — a clean drop, no run in the history at
  any status.
- After adding 4 backup cron slots (+15/+30/+45 min) per workflow with a
  self-healing skip-check (so a slot that fires after the day's result
  already exists skips real work instead of wasting a Gemini call): the
  skip-check worked exactly as designed, but all 4 slots for one morning
  fired **~1h49m to ~2h13m late, clustered within ~24 minutes of each
  other** — not one slot randomly dropped while others fired on time, but
  the whole schedule queue for the repo delayed by a similar systemic
  amount. Spacing multiple cron entries a few minutes apart doesn't help
  much against a delay that shape, since it hits all of them roughly
  equally.
- On 2026-08-28, four `forecast.yml` runs were created between 00:22:32Z
  and 00:37:28Z — 3 to 6 minutes apart instead of the 15 their cron entries
  ask for, and hours from any slot the file declares. The likeliest reading
  is that these were the previous day's 15:0x slots draining, since GitHub
  does not fire a schedule early. Crossing UTC midnight, they stopped being
  yesterday's re-issue and became today's FIRST run, at 00:27 — before the
  02:00 UTC window in which the models are aligned. A slot that arrives late
  enough does not simply run late; it runs as a different kind of run, on a
  different day, against a cycle nobody chose. The concurrency group queued the second run behind the first, as
  intended, but `actions/checkout@v7` with no `ref:` had already pinned it
  to `github.sha` — the branch head at creation time, before the first
  run's commit. The queued run's checkout never saw that commit, so the
  pipeline's own duplicate-trigger guard found no log entry, ran a full
  first-run LLM call it didn't need, and its `git push` was rejected
  non-fast-forward.
- It happened again on 2026-08-29: four runs created 23:59:53Z–00:09:21Z,
  the first writing `forecast: 2026-08-29` at 00:06:05Z on guidance
  initialised 12z, age 12.03 h. Which declared slot produced each run is
  not knowable — GitHub exposes no intended-fire time — and the draining
  reading above no longer obviously fits, since that day's 15:0x slots had
  already fired at 15:16–15:41Z. What is knowable is arrival times against
  the slots that exist, over the whole history of these four 03:0x crons
  under `forecast.yml` and under `daily.yml` before it with identical
  entries: 33–52 min late on 2026-08-26, ~11 h late on 2026-08-27
  (14:04–14:34Z), a cluster around midnight on 08-28 and again on 08-29.
  Not once punctual. The 15:0x family has never been more than ~35 min out.

Net effect, and the decision taken on 2026-08-29: **`forecast.yml`'s
`schedule:` block was removed.** Backup slots lower the odds of a *total*
miss and do nothing for punctuality once GitHub's queue backs up. Worse, a
03:0x slot arriving after 00:00 UTC is not a late backstop — it is the next
day's FIRST run, and the first run owns the write-once numbers the accuracy
record scores. Twice running those were built on 12-hour-old guidance while
the crontab dispatch, firing on time, could only re-issue the narrative. It
also buys an extra issuance, and the mailer sends on novelty
(`mailer/AppsScriptMailer.gs`, 30-minute poll), so an extra issuance is an
extra email to every subscriber. For a daily product with real readers, one
trigger that is occasionally silent beats two that occasionally collide.

## The documented setup paths

**1. GitHub Actions' own `schedule:` (removed here, restorable in a
fork).** Add a `schedule:` block back to `forecast.yml` and it needs no
trigger of your own at all — the lowest-setup path, and the reason it
existed: forkable for underserved locations by people who may not have (or
want to pay for/maintain) anything else. Reliability is whatever GitHub
delivers that day, which on this repo has meant slots hours late often
enough to matter (see *Why this exists*). Path 2a costs one dashboard form
and removes that, so prefer it if you can.

**2a. A free hosted cron service — no server, no script, the easiest real
fix (recommended starting point).** Services like
[cron-job.org](https://cron-job.org/en/) let you configure an HTTP request
on a schedule entirely through a web dashboard — no code to write, nothing
to deploy or maintain. Point it directly at GitHub's `workflow_dispatch`
REST API and it replaces GitHub's own scheduler as the trigger, without
needing any infrastructure of your own at all. cron-job.org specifically:
free (donation-funded, no paid tier to upsell you to), no credit card, in
continuous operation 15+ years, open-source codebase. [Cloudflare Workers
Cron Triggers](https://developers.cloudflare.com/workers/platform/pricing/)
is a solid alternative if you'd rather have a large company's
infrastructure behind it and don't mind writing a few lines of JS — also
free, also no credit card, capped at 3 triggers/Worker at 1-minute
granularity.

Setup. There is **one workflow to call, `forecast.yml`**, and the two cron
jobs differ only in their time — the day decides whether a run is its first
or an update (see `pipeline.run_forecast`). Add a third job at another hour
and it becomes a third issuance; nothing else needs to change. Runs less
than an hour apart are refused as duplicate triggers.

1. Create a **fine-grained GitHub PAT scoped to this repo only**, with
   just the **"Actions: Read and write"** permission — nothing broader.
   Give it an **expiration date**, not "no expiration." See the SECURITY
   note below for why both of these matter here specifically.
2. In cron-job.org (or equivalent), create a job:
   - **URL**:
     `https://api.github.com/repos/<owner>/<repo>/actions/workflows/forecast.yml/dispatches`
     (the same URL for both jobs)
   - **Method**: `POST`
   - **Headers**:
     `Authorization: Bearer <your PAT>`
     `Accept: application/vnd.github+json`
     `X-GitHub-Api-Version: 2022-11-28`
     `Content-Type: application/json`
   - **Body**: `{"ref":"main"}`
   - **Schedule**: whatever times you want issuances. Pick the first one
     inside an aligned model window (`cycle.py`) — this deployment uses
     03:01 and 15:01 UTC, the crontab lines below. No need to offset off
     the exact hour the way GitHub's own crons do; that trick works around
     GitHub Actions' scheduler congestion specifically, which doesn't
     apply to a dedicated cron service.
3. A successful dispatch returns HTTP 204 with an empty body — it means
   GitHub accepted the request to start a run, not that the run will
   succeed. This is now the only trigger — `forecast.yml` declares no
   `schedule:` — so if it stops firing, nothing else covers it. Adding a
   second trigger is safe in itself: one that repeats another from the last
   hour returns inside the pipeline without calling the model, whichever
   got there first. Read *Why this exists* before adding one anyway; the
   collision that matters is not a wasted call but which trigger gets to
   open the day. **Don't pass a `force` input
   on the dispatch call** — leaving it at its default (`false`) is what
   lets this trigger safely lose a race to any other trigger without
   burning a redundant call; `force: true` exists only for a human
   deliberately regenerating today's forecast by hand, not for routine
   automated triggers.

**SECURITY — read before doing this.** This is the first place in this
project a credential leaves GitHub's own security boundary. Every other
secret (`GEMINI_API_KEY`, `WAQI_TOKEN`) lives in GitHub Actions' encrypted
secrets store, decrypted only into an ephemeral runner, never visible
outside GitHub. A PAT configured in a third-party dashboard is a secret
sitting on infrastructure you don't operate — a real, different trust
boundary, not a formality:
- **Scope it to nothing more than it needs** — this repo only,
  "Actions: Read and write" only. If it leaks, the worst case is someone
  triggering extra runs (burns your Actions minutes and LLM quota), not
  arbitrary repo access.
- **Set an expiration** so a leaked-and-unnoticed token stops working
  eventually, at the cost of rotating it periodically.
- **Enable 2FA on the cron service account itself** — its dashboard is now
  part of your security perimeter, since the token (and, depending on the
  service, request logs) are visible to anyone logged into it.
- GitHub's dispatch API genuinely requires *some* GitHub credential
  outside GitHub for this mechanism to exist at all — there's no way
  around that. A GitHub App issuing short-lived (1-hour) installation
  tokens is more secure in principle, but is real added setup complexity
  (registering an App, a JWT token exchange on every trigger) that cuts
  against this path's whole point of being the easy option. A scoped,
  expiring PAT is the proportionate choice here, not the paranoid-maximum
  one — go further only if your threat model genuinely calls for it.

**2b. `ops/trigger_workflow.sh` from your own server's cron.** For anyone
who already has a server and would rather keep the trigger logic in
version control (or wants retry/logging behavior beyond what a hosted
cron UI gives you) than depend on a third-party dashboard. Functionally
equivalent to 2a — same `workflow_dispatch` API call, same PAT scoping
advice applies (the token now lives in a file on your server instead of a
third-party dashboard, which is its own, different trust tradeoff — your
server's security becomes the perimeter instead). See the script's own
header comment for full setup steps.

The two crontab lines this deployment runs, with its own paths replaced by
`$HOME` (cron sets `HOME` from `/etc/passwd`, so this works verbatim):

```cron
1 3  * * * TOKEN_FILE=$HOME/.secrets/olw-dispatch-token $HOME/bin/trigger_workflow.sh forecast.yml >> $HOME/olw-dispatch.log 2>&1
1 15 * * * TOKEN_FILE=$HOME/.secrets/olw-dispatch-token $HOME/bin/trigger_workflow.sh forecast.yml >> $HOME/olw-dispatch.log 2>&1
```

Identical bar the hour, which is the whole point of one `forecast.yml`: the
day decides whether a run is its first or an update. A third line at another
hour becomes a third issuance and nothing else needs to know.

Four things that are easy to get wrong here:

- **The times are your server's timezone, not UTC.** These assume a
  UTC-configured box; check `timedatectl` before copying them. Getting this
  wrong lands the first run of the day outside the window in which the
  models are on the same cycle (`cycle.py`), which costs freshness silently
  — the forecast still publishes.
- **Redirect the output.** Without `>>` the script's timestamped log goes to
  cron's mail, which on most servers means nowhere. That log is the only
  thing that answers "when did this stop working": the failure mode here is
  silence, and silence is what a missing log looks like too.
- **The minute is deliberate.** 03:01 UTC puts the first run of the day
  just inside the 02:00 UTC aligned window (`cycle.py`), which is what the
  hour is chosen for; the minute itself no longer has to beat anything,
  since there are no GitHub-side slots left to race. There is no need to
  offset off the exact hour the way GitHub-side crons do; that trick works
  around GitHub Actions' scheduler congestion, which an ordinary cron
  daemon does not share.
- **A copy of the script is not the script, and that is the right trade.**
  `$HOME/bin/trigger_workflow.sh` above is a copy, so `git pull` in a
  checkout will not update it, and the two can drift apart invisibly. Copy
  it anyway rather than symlinking cron's target into a working tree: a
  symlink turns `git pull` into a deployment with no review step, so
  whatever lands in the repo executes as your cron user on the next tick.
  Re-copy deliberately when the script changes — the drift is a thing you
  can check, while the symlink is a standing grant.

The trailing `forecast.yml` is optional: the script defaults to it, and
naming it is a spelling opportunity that a bare call does not have (a
misspelt workflow is an HTTP 404 the script reports and does not retry).
Naming it is still defensible — it says in the crontab what the line
actually dispatches.

**3. Full migration off GitHub Actions (not built, a real option if ever
needed).** Run `olw forecast` directly from cron
on a server you control, at whatever times you want issuances — no GitHub Actions involved in scheduling *or*
execution. The most reliable option for scheduling specifically, at the
cost of everything paths 1 and 2a are designed to avoid: secrets move off
GitHub's encrypted secrets onto your box, you're patching/monitoring/paying
for a server instead of free GitHub compute, and a fork of this project
would inherit "go run a server" as a requirement instead of "fork the repo,
add two secrets." Not pursued while path 2a/2b cover the actual reliability
gap without that tradeoff — documented here (and in
[ROADMAP.md item 10](../docs-internal/ROADMAP.md), a possible future
free-tier OCI walkthrough) so it doesn't need re-deriving if circumstances
change.

All four are deployment-specific choices layered on the same core pipeline;
none of them forks the logic. This deployment runs 2b. A fork inherits no
trigger at all, which is the one thing to notice before relying on it.

## Credential handling (planned improvement)

Production credentials currently live as plaintext exports in the operator's
shell profile. That means anything which reads that file — any tool, script,
or assistant — sees them, and one such accident has already happened.

Planned: move them into the macOS Keychain (`security add-generic-password`)
and have the shell read them out on demand, so no plaintext copy sits on
disk. Deferred until after a pending OS upgrade, since the keys are being
reissued anyway.

Two notes for whoever does it:

- The GitHub PAT matters more than the API keys. An API key costs quota or
  money; a PAT with write scope can rewrite workflows and edit the committed
  forecast record, which is the one thing this project's credibility rests
  on. Keep it minimal-scope, on the trigger host only, and never in a shell
  profile.
- Rotating a key means updating it in **both** places — the operator's shell
  and the GitHub Actions secret. They are one value stored twice, and a
  half-rotation looks like a working local run with a failing scheduled one.
