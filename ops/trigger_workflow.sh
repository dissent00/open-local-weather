#!/usr/bin/env bash
#
# Triggers this repo's GitHub Actions workflows via the REST API, meant to
# be run from cron on infrastructure the operator controls — NOT part of
# the pipeline itself, and NOT required for forking this project. A fork
# works fine on GitHub Actions' own `schedule:` trigger alone, same as
# this repo did before this script existed; this is an optional,
# deployment-specific reliability enhancement for whoever happens to have
# a server to run it from.
#
# Don't have a server? You don't need one for this — see ops/README.md's
# "path 2a" for a free hosted cron service (e.g. cron-job.org) that calls
# the same workflow_dispatch API directly from its own dashboard, no
# script or infrastructure of your own required at all. This script is
# "path 2b", for people who already have somewhere to run it or want the
# trigger logic in version control instead of a third-party UI.
#
# WHY THIS EXISTS: GitHub's own docs for the `schedule` event say "some
# queued jobs may be dropped" under high load — confirmed the hard way on
# this exact repo. daily.yml's schedule trigger produced ZERO runs across
# its first two real scheduled occurrences (once on an exact-hour cron,
# once on the off-hour fix that was supposed to help); evening_refresh.yml
# fired on its one real occurrence, but ~56 minutes late. The workflow
# files' own backup cron slots (+15/+30/+45 min — see daily.yml /
# evening_refresh.yml) mitigate this but don't eliminate it, since they
# ride the same scheduler that already admits to dropping jobs. Triggering
# from a completely different scheduling system (this server's own cron)
# gives the pipeline a trigger path that doesn't share GitHub Actions'
# scheduling failure domain at all — this doesn't fix GitHub's scheduler,
# it routes around it.
#
# GitHub's own `schedule:` triggers are deliberately left in daily.yml /
# evening_refresh.yml as a backstop, not removed — if this script's cron
# job, this server, or its stored token ever breaks, the workflows still
# have their own (imperfect, but non-zero) chance of firing on their own.
#
# The two are not the same redundancy twice. This script routes around
# GitHub's scheduler; the schedule routes around this server. Dropping
# either leaves a single point of failure whose failure mode is silence —
# a run that never happens produces no error, and health_check.yml only
# looks weekly.
# Both paths are safe to overlap: the `check` job in each workflow skips
# real work (no wasted Gemini call) if the day's result already exists,
# regardless of which trigger produced it — this covers workflow_dispatch
# too (an earlier version of the workflow files exempted all
# workflow_dispatch calls from the check unconditionally, which was fine
# when that meant "an occasional manual click" but caused a real wasted
# double-run once THIS script started calling workflow_dispatch routinely
# every day; fixed via the `force` input, default false, which this
# script deliberately never sets — see daily.yml's `check`/`run` job docs).
#
# ============================== SETUP ==============================
# 1. Create a GitHub fine-grained PAT scoped to this repo only, with
#    "Actions: Read and write" permission (the same permission this
#    project's setup already needed for `gh workflow run` from a
#    terminal — reuse it if you still have it).
# 2. Store the token somewhere only this script (and cron, running as
#    you) can read — NEVER commit it to any git repo:
#      mkdir -p ~/.secrets
#      echo 'ghp_...your token...' > ~/.secrets/olw-dispatch-token
#      chmod 600 ~/.secrets/olw-dispatch-token
# 3. Add to crontab (`crontab -e`). Cron times are in YOUR SERVER's
#    configured timezone, not necessarily UTC — check `timedatectl` or
#    equivalent before copying these times verbatim. These two lines
#    assume a UTC-configured server, matching the workflows' own cron
#    comments (03:07 UTC / 15:07 UTC — see daily.yml / evening_refresh.yml
#    for why those specific times):
#      7 3  * * * TOKEN_FILE=$HOME/.secrets/olw-dispatch-token /path/to/ops/trigger_workflow.sh daily.yml
#      7 15 * * * TOKEN_FILE=$HOME/.secrets/olw-dispatch-token /path/to/ops/trigger_workflow.sh evening_refresh.yml
#    No need to offset off the exact hour the way the GitHub-side crons
#    do — that trick works around GitHub Actions' own scheduler
#    congestion specifically, which doesn't apply to an ordinary cron
#    daemon on your own box.
# 4. Done. Non-zero exit code on failure — wire your system's own cron
#    failure notification (mail, a monitoring agent, whatever you already
#    have) if you want to know when a dispatch call itself fails. Same
#    "watch for silence" principle as everything else in this project.
# =====================================================================

set -euo pipefail

WORKFLOW_FILE="${1:?Usage: trigger_workflow.sh <workflow-file.yml>  (e.g. daily.yml or evening_refresh.yml)}"
REPO="${GH_REPO:-dissent00/open-local-weather}"
BRANCH="${GH_BRANCH:-main}"
TOKEN_FILE="${TOKEN_FILE:?Set TOKEN_FILE to the path of a file containing your GitHub PAT -- see the SETUP comment above}"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "Cannot read TOKEN_FILE at ${TOKEN_FILE}" >&2
  exit 1
fi
TOKEN=$(cat "$TOKEN_FILE")

RESPONSE_BODY=$(mktemp)
trap 'rm -f "$RESPONSE_BODY"' EXIT

HTTP_STATUS=$(curl -sS -o "$RESPONSE_BODY" -w "%{http_code}" \
  -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches" \
  -d "{\"ref\":\"${BRANCH}\"}")

# A successful dispatch call returns 204 No Content with an empty body —
# it does NOT wait for the workflow run to complete, or tell you whether
# it will succeed, only that GitHub accepted the request to start one.
if [ "$HTTP_STATUS" -ge 300 ]; then
  echo "Failed to dispatch ${WORKFLOW_FILE} on ${REPO}@${BRANCH}: HTTP ${HTTP_STATUS}" >&2
  cat "$RESPONSE_BODY" >&2
  exit 1
fi

echo "Dispatched ${WORKFLOW_FILE} on ${REPO}@${BRANCH} (HTTP ${HTTP_STATUS})."
