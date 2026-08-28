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
#    assume a UTC-configured server, and sit a few minutes AHEAD of
#    forecast.yml's own first backstop slot (03:07 / 15:07 UTC — see that
#    file for why those hours) so this trigger wins the race and GitHub's
#    scheduler stays the fallback:
#      1 3  * * * TOKEN_FILE=$HOME/.secrets/olw-dispatch-token /path/to/ops/trigger_workflow.sh >> $HOME/olw-dispatch.log 2>&1
#      1 15 * * * TOKEN_FILE=$HOME/.secrets/olw-dispatch-token /path/to/ops/trigger_workflow.sh >> $HOME/olw-dispatch.log 2>&1
#    BOTH LINES ARE IDENTICAL, and that is the change: one workflow, and
#    the day decides whether a run is its first or an update. Add a third
#    line at another hour and it becomes a third issuance; nothing else
#    needs to know. Runs less than an hour apart are refused as duplicate
#    triggers (see pipeline.MIN_REISSUE_INTERVAL_MINUTES).
#    No need to offset off the exact hour the way the GitHub-side crons
#    do — that trick works around GitHub Actions' own scheduler
#    congestion specifically, which doesn't apply to an ordinary cron
#    daemon on your own box.
# 4. Done. Every line is timestamped and the exit code is non-zero on
#    failure, so `tail` on that log answers "when did this stop working".
#    Wire your system's own cron failure notification (mail, a monitoring
#    agent, whatever you already have) if you want to be told rather than
#    to look. Same "watch for silence" principle as everything else here —
#    and the likeliest silence is a fine-grained PAT quietly expiring,
#    which this script now names explicitly (HTTP 401) instead of
#    reporting as a generic failure.
# =====================================================================

set -euo pipefail

# forecast.yml is the only workflow to schedule: one verb, and the day
# decides whether this is its first run or an update (see
# pipeline.run_forecast). An argument is still accepted for the transition
# off daily.yml / evening_refresh.yml, and for anyone dispatching something
# else by hand.
WORKFLOW_FILE="${1:-forecast.yml}"
REPO="${GH_REPO:-dissent00/open-local-weather}"
BRANCH="${GH_BRANCH:-main}"
# Overridable so this script can be exercised against a local stub instead
# of dispatching a real run. Nothing in production sets it.
API_BASE="${GH_API_BASE:-https://api.github.com}"
TOKEN_FILE="${TOKEN_FILE:?Set TOKEN_FILE to the path of a file containing your GitHub PAT -- see the SETUP comment above}"

# Attempts, and the pause between them. A dispatch that fails on a network
# blip currently loses the whole slot: the workflow's own backup slots cover
# it, but they ride GitHub's scheduler, which is the failure domain this
# script exists to route around. Three tries over ~30s stays well inside the
# 15-minute gap to the next slot.
MAX_ATTEMPTS="${DISPATCH_ATTEMPTS:-3}"
RETRY_DELAY_S="${DISPATCH_RETRY_DELAY_S:-10}"

# Timestamped, because this runs from cron and its output is read weeks
# later, out of a log, by someone asking when a thing stopped working.
log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

if [ ! -r "$TOKEN_FILE" ]; then
  log "Cannot read TOKEN_FILE at ${TOKEN_FILE}" >&2
  exit 1
fi

# A token readable by other users on this machine is a token to rotate, not
# a reason to stop — the run is what matters, the warning is what gets read.
if [ "$(uname)" = "Darwin" ]; then
  TOKEN_MODE=$(stat -f "%Lp" "$TOKEN_FILE" 2>/dev/null || echo "")
else
  TOKEN_MODE=$(stat -c "%a" "$TOKEN_FILE" 2>/dev/null || echo "")
fi
case "$TOKEN_MODE" in
  ""|600|400) ;;
  *) log "WARNING: ${TOKEN_FILE} is mode ${TOKEN_MODE}; chmod 600 it." >&2 ;;
esac

TOKEN=$(cat "$TOKEN_FILE")

RESPONSE_BODY=$(mktemp)
trap 'rm -f "$RESPONSE_BODY"' EXIT

attempt=1
while :; do
  # curl's own exit code is separated from the HTTP status on purpose: a
  # connection that never happened and a request GitHub refused need
  # different handling, and only the first is worth retrying blindly.
  if HTTP_STATUS=$(curl -sS -o "$RESPONSE_BODY" -w "%{http_code}" \
    --connect-timeout 15 --max-time 30 \
    -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${API_BASE}/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches" \
    -d "{\"ref\":\"${BRANCH}\"}"); then
    CURL_FAILED=0
  else
    CURL_FAILED=1
    HTTP_STATUS=0
  fi

  # A successful dispatch call returns 204 No Content with an empty body —
  # it does NOT wait for the workflow run to complete, or tell you whether
  # it will succeed, only that GitHub accepted the request to start one.
  if [ "$CURL_FAILED" -eq 0 ] && [ "$HTTP_STATUS" -lt 300 ]; then
    log "Dispatched ${WORKFLOW_FILE} on ${REPO}@${BRANCH} (HTTP ${HTTP_STATUS})."
    exit 0
  fi

  # 4xx is an answer, not a blip: the token, the repo or the workflow name
  # is wrong, and retrying just repeats the same rejection.
  if [ "$CURL_FAILED" -eq 0 ] && [ "$HTTP_STATUS" -lt 500 ]; then
    case "$HTTP_STATUS" in
      401)
        log "Dispatch REFUSED (HTTP 401): the token in ${TOKEN_FILE} is expired or invalid." >&2
        log "  Fine-grained PATs expire. Issue a new one (Actions: Read and write, this repo only) and replace the file." >&2
        ;;
      403)
        log "Dispatch REFUSED (HTTP 403): the token is valid but lacks 'Actions: Read and write' on ${REPO}." >&2
        ;;
      404)
        log "Dispatch REFUSED (HTTP 404): no workflow ${WORKFLOW_FILE} on ${REPO}@${BRANCH}, or the token cannot see the repo." >&2
        log "  If a crontab line still names a deleted workflow, this is what that looks like." >&2
        ;;
      *)
        log "Failed to dispatch ${WORKFLOW_FILE} on ${REPO}@${BRANCH}: HTTP ${HTTP_STATUS}" >&2
        ;;
    esac
    cat "$RESPONSE_BODY" >&2
    exit 1
  fi

  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    if [ "$CURL_FAILED" -eq 1 ]; then
      log "Failed to reach ${API_BASE} after ${attempt} attempt(s)." >&2
    else
      log "Failed to dispatch ${WORKFLOW_FILE}: HTTP ${HTTP_STATUS} after ${attempt} attempt(s)." >&2
      cat "$RESPONSE_BODY" >&2
    fi
    exit 1
  fi

  log "Attempt ${attempt}/${MAX_ATTEMPTS} failed (curl_failed=${CURL_FAILED}, http=${HTTP_STATUS}); retrying in ${RETRY_DELAY_S}s." >&2
  attempt=$((attempt + 1))
  sleep "$RETRY_DELAY_S"
done
