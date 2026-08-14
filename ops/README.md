# Operational extras (optional, deployment-specific)

Everything in `ops/` is **optional**. Forking this project and running it
on GitHub Actions alone — the default, zero-servers-required path — works
fine without anything in this directory. `ops/` exists for operators who
want tighter scheduling reliability than GitHub Actions' own `schedule`
trigger provides. Same core pipeline either way — these are trigger paths,
not forks of the logic itself.

## Why this exists

GitHub's own docs for the `schedule` event say: *"some queued jobs may be
dropped"* under high load. Confirmed on this repo, twice, in two different
failure shapes:

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

Net effect: the backup-slot redundancy (still in `daily.yml` /
`evening_refresh.yml`, left in place as a backstop) meaningfully reduces
the odds of a *total* miss, but does nothing for punctuality when GitHub's
own scheduler queue backs up — and a forecast that lands two hours late is
still a missed subscriber email that day, since the mailer's own retry
window (`mailer/AppsScriptMailer.gs`) is only a few minutes wide. For a
daily product where "the email actually went out this morning" matters,
this is the reason to trigger from somewhere else entirely.

## The documented setup paths

**1. GitHub Actions only (the default, no `ops/` involved).** Fork the
repo, add the required secrets, done. Reliability is whatever GitHub's
`schedule` trigger delivers that day. This is the only path that requires
zero infrastructure of your own, which matters for this project's actual
goal: forkable for underserved locations by people who may not have (or
want to pay for/maintain) anything else.

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

Setup (per workflow — repeat for both `daily.yml` and
`evening_refresh.yml`, each as its own cron job with its own time):

1. Create a **fine-grained GitHub PAT scoped to this repo only**, with
   just the **"Actions: Read and write"** permission — nothing broader.
   Give it an **expiration date**, not "no expiration." See the SECURITY
   note below for why both of these matter here specifically.
2. In cron-job.org (or equivalent), create a job:
   - **URL**:
     `https://api.github.com/repos/<owner>/<repo>/actions/workflows/daily.yml/dispatches`
     (swap `daily.yml` for `evening_refresh.yml` on the second job)
   - **Method**: `POST`
   - **Headers**:
     `Authorization: Bearer <your PAT>`
     `Accept: application/vnd.github+json`
     `X-GitHub-Api-Version: 2022-11-28`
     `Content-Type: application/json`
   - **Body**: `{"ref":"main"}`
   - **Schedule**: the workflow's intended time (03:07 UTC / 15:07 UTC for
     this deployment — see each workflow file's own cron comment). No need
     to offset off the exact hour the way GitHub's own crons do; that
     trick works around GitHub Actions' scheduler congestion specifically,
     which doesn't apply to a dedicated cron service.
3. A successful dispatch returns HTTP 204 with an empty body — it means
   GitHub accepted the request to start a run, not that the run will
   succeed. GitHub's own `schedule:` crons stay in the workflow files as a
   backstop; both are safe to overlap, since each workflow's `check` job
   skips duplicate real work regardless of which trigger produced today's
   result. **Don't pass a `force` input on the dispatch call** — leaving
   it at its default (`false`) is what lets this trigger safely lose the
   race to GitHub's own schedule (or a backup slot) without burning a
   redundant Gemini call; `force: true` exists only for a human
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

**3. Full migration off GitHub Actions (not built, a real option if ever
needed).** Run `olw run-daily` / `olw refresh-forecast` directly from cron
on a server you control — no GitHub Actions involved in scheduling *or*
execution. The most reliable option for scheduling specifically, at the
cost of everything path 1 is designed to avoid: secrets move off GitHub's
encrypted secrets onto your box, you're patching/monitoring/paying for a
server instead of free GitHub compute, and a fork of this project would
inherit "go run a server" as a requirement instead of "fork the repo, add
two secrets." Not pursued while path 2a/2b cover the actual reliability
gap without that tradeoff — documented here (and in
[ROADMAP.md item 10](../docs-internal/ROADMAP.md), a possible future
free-tier OCI walkthrough) so it doesn't need re-deriving if circumstances
change.

Paths 2a, 2b, and 3 are all optional, deployment-specific choices layered
on top of the same core pipeline — picking one doesn't change what forkers
inherit by default (path 1).
