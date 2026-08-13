# Operational extras (optional, deployment-specific)

Everything in `ops/` is **optional**. Forking this project and running it
on GitHub Actions alone — the default, zero-servers-required path — works
fine without anything in this directory. `ops/` exists for operators who
want tighter scheduling reliability than GitHub Actions' own `schedule`
trigger provides and are willing to run a small piece of infrastructure
themselves to get it. Same core pipeline either way — these are trigger
paths, not forks of the logic itself.

## Why this exists

GitHub's own docs for the `schedule` event say: *"some queued jobs may be
dropped"* under high load. Confirmed the hard way on this repo:
`daily.yml`'s schedule trigger produced **zero runs** across its first two
real scheduled occurrences (once on an exact-hour cron, once on the
off-hour fix meant to help); `evening_refresh.yml` fired on its one real
occurrence, but ~56 minutes late. `daily.yml` / `evening_refresh.yml` now
each schedule 4 backup slots (+15/+30/+45 min) with a cheap self-healing
skip-check, which helps but rides the same scheduler GitHub itself admits
can drop jobs — it reduces the odds of a total miss, it doesn't eliminate
the underlying unreliability.

## The documented setup paths

**1. GitHub Actions only (the default, no `ops/` involved).** Fork the
repo, add the required secrets, done. Reliability is whatever GitHub's
`schedule` trigger delivers that day — which the backup-slot redundancy in
the workflow files improves on, but can't fully fix. This is the only path
that requires zero infrastructure of your own, which matters for this
project's actual goal: forkable for underserved locations by people who
may not have (or want to pay for/maintain) a server.

**2. GitHub Actions + externally-triggered dispatch (this repo's live
setup).** [`trigger_workflow.sh`](trigger_workflow.sh) runs from cron on
infrastructure the operator already has, and calls GitHub's
`workflow_dispatch` REST API directly — a trigger path that doesn't share
GitHub Actions' own scheduling failure domain. GitHub's `schedule:` crons
stay in place as a backstop, not replaced; both are safe to overlap
because each workflow's `check` job already skips duplicate real work
regardless of which trigger produced today's result. See the script's own
header comment for full setup steps (PAT scope, token storage, crontab
lines).

**3. Full migration off GitHub Actions (not built, a real option if ever
needed).** Run `olw run-daily` / `olw refresh-forecast` directly from cron
on a server you control — no GitHub Actions involved in scheduling *or*
execution. The most reliable option for scheduling specifically, at the
cost of everything path 1 is designed to avoid: secrets move off GitHub's
encrypted secrets onto your box, you're patching/monitoring/paying for a
server instead of free GitHub compute, and a fork of this project would
inherit "go run a server" as a requirement instead of "fork the repo, add
two secrets." Not pursued while path 2 covers the actual reliability gap
without that tradeoff — documented here so it doesn't need re-deriving if
circumstances change.

Paths 2 and 3 are both optional, deployment-specific choices layered on
top of the same core pipeline — picking one doesn't change what forkers
inherit by default (path 1).
