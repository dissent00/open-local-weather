"""Weekly health checks: Gemini model deprecation status, repo staleness,
and whether the measured aligned-window table still matches reality.

Built in direct response to a real incident during initial setup: the
default model (gemini-2.5-flash at the time) started 404ing with "no longer
available to new users" — but it was STILL listed as generateContent-capable
in GET /v1beta/models. A bare model-list check would not have caught this;
the account-tier restriction wasn't visible in the metadata. Two more
reliable signals instead:

1. Read Gemini's own deprecations page (ai.google.dev/gemini-api/docs/
   deprecations), which lists real shutdown dates per model, and have an
   LLM check whether the configured model appears there. An LLM read of
   this prose/table page is far less brittle than a hand-written HTML
   scraper against a page format we don't control, and it's genuinely
   forward-looking (real dates), not just "does this still exist."
2. Track days since the last git commit — a proxy for "is the daily
   pipeline actually running and pushing." GitHub auto-disables scheduled
   workflows after 60 days of repo inactivity; warning at
   DEFAULT_STALENESS_WARNING_DAYS (50) gives a real lead-time window to
   react, since the checking workflow is (by construction) still running
   normally at day 50 — the 60-day disable hasn't happened yet.
3. Compare the derived aligned-window table against one live observation
   (check_aligned_window below). Belongs here for the same reason as the
   staleness proxy: it decays slowly, invisibly, and only a periodic look
   would ever notice.

These checks run on their own weekly schedule (see
.github/workflows/health_check.yml), decoupled from the daily forecast
pipeline's own cron so a health-check failure never blocks or depends on
that day's forecast run.
"""

from __future__ import annotations

import re
from collections import Counter
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import requests
from pydantic import BaseModel

from openlocalweather.cycle import AlignedCycle, aligned_cycle_at
from openlocalweather.fetch.model_run import RUN_SETTLE_MINUTES, ModelRun
from openlocalweather.llm.provider import LLMProvider
from openlocalweather.models import RunDegradation

DEPRECATIONS_PAGE_URL = "https://ai.google.dev/gemini-api/docs/deprecations"
REQUEST_TIMEOUT_S = 30

# GitHub disables scheduled workflows after 60 days of repo inactivity;
# warn with a real lead-time buffer rather than right at the edge.
DEFAULT_STALENESS_WARNING_DAYS = 50


class ModelDeprecationCheck(BaseModel):
    deprecated_or_scheduled: bool
    notes: str


@dataclass
class HealthCheckResult:
    ok: bool
    messages: list[str]


def fetch_deprecations_page_text() -> str:
    resp = requests.get(DEPRECATIONS_PAGE_URL, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return resp.text


def check_model_deprecation(
    llm_provider: LLMProvider, model_id: str, page_text: str | None = None
) -> ModelDeprecationCheck:
    """Asks the LLM to read Gemini's deprecations page and report whether
    `model_id` is listed as deprecated, shut down, or scheduled for future
    shutdown. `page_text` is injectable for testing without a live fetch.
    """
    text = page_text if page_text is not None else fetch_deprecations_page_text()
    system_prompt = (
        "You are checking a Gemini API deprecations page on behalf of an "
        "automated weekly monitoring job. Be conservative: if the page is "
        "ambiguous, or doesn't mention the model at all, report "
        "deprecated_or_scheduled=false and say so plainly in notes, rather "
        "than guessing or inferring from unrelated entries."
    )
    user_prompt = (
        f"Model id to check: {model_id}\n\n"
        f"Page content (from {DEPRECATIONS_PAGE_URL}):\n\n{text}\n\n"
        "Is this exact model id listed with a shutdown/deprecation date, or "
        "otherwise flagged as deprecated or scheduled for future shutdown? "
        "If it lists a recommended replacement model, name it in notes."
    )
    return llm_provider.generate(system_prompt, user_prompt, ModelDeprecationCheck)


def check_repo_staleness(
    days_since_last_commit: int, warning_threshold_days: int = DEFAULT_STALENESS_WARNING_DAYS
) -> bool:
    """True if the repo is approaching GitHub's 60-day scheduled-workflow
    auto-disable threshold. Pure function — measuring the actual days-since
    is the caller's job (see cli.py, which reads `git log`), so this stays
    trivially testable without any git/filesystem dependency.
    """
    return days_since_last_commit >= warning_threshold_days


class AlignedWindowStatus(Enum):
    """Three outcomes, not a boolean: "the table looks wrong" and "nothing
    could be compared this time" call for different words and different exit
    codes, and collapsing them would report a silent endpoint as agreement.
    """

    AGREES = "agrees"
    DRIFTED = "drifted"
    NOT_CHECKED = "not_checked"


@dataclass
class AlignedWindowCheck:
    status: AlignedWindowStatus
    message: str


# How far from a window boundary the observation and the table stop being
# comparable. The availability delays the table was built from vary run to
# run — ECMWF ~7.1 h measured twice on 2026-08-11, then 8h25m for 18z and
# 7h46m for 00z on 2026-08-28 — while the windows are written on the hour.
# So for about an hour around a boundary the observed cycle can sit ahead of
# the table (that run published early) or behind it (that run has not landed
# yet), and neither is evidence about the table itself.
WINDOW_BOUNDARY_SLACK_HOURS = 1

_BOUNDARY_CAVEAT = (
    " This ran within an hour of a window boundary, where the observation and the table "
    "disagree for reasons that say nothing about the table — re-run it well inside a window "
    "before re-measuring anything."
)


def _near_a_window_boundary(now: datetime, derived: AlignedCycle) -> bool:
    """Asks cycle.aligned_cycle_at rather than restating where the windows
    fall: if the answer an hour from now names a different window, one opens
    within the hour."""
    slack = timedelta(hours=WINDOW_BOUNDARY_SLACK_HOURS)

    just_opened = (now - derived.window_opened_at) < slack
    opens_shortly = aligned_cycle_at(now + slack).window_opened_at != derived.window_opened_at

    return just_opened or opens_shortly


def _drift_message(now: datetime, derived: AlignedCycle, observed: ModelRun) -> str:
    direction = (
        "the table claims a cycle is aligned before the slowest model has it, so the derived "
        "floor is understating how old the guidance is"
        if observed.initialised_at < derived.initialised_at
        else "the window is opening earlier than the table says, so the derived floor is only "
        "pessimistic"
    )
    caveat = _BOUNDARY_CAVEAT if _near_a_window_boundary(now, derived) else ""

    return (
        f"observed {observed.model} run ({observed.initialised_at.isoformat()}) disagrees with "
        f"the derived aligned cycle ({derived.initialised_at.isoformat()}) at "
        f"{now.isoformat()} — {direction}. Re-measure the aligned-window table in "
        "docs-internal/ROADMAP.md from each model's own /data/<model>/static/meta.json."
        f"{caveat}"
    )


def check_aligned_window(now: datetime, observed: ModelRun | None) -> AlignedWindowCheck:
    """Does the derived aligned-window table still agree with what the one
    observable model actually published? Pure — the caller fetches (see
    fetch/model_run.fetch_settled_run), for the same reason
    check_repo_staleness does not shell out to git.

    WHY THIS IS A HEALTH CHECK. The table in docs-internal/ROADMAP.md is a
    hand measurement taken twice on 2026-08-11 and never since. Every
    forecast that cannot observe a real run falls back to it, and a stale
    table is invisible from the forecast itself: the number it produces
    looks exactly as authoritative as an observed one. This comparison is
    the only thing that would ever say the table had moved, and re-measuring
    it stays a manual act this warning exists to prompt.

    It also covers a surface that cannot check itself: the mobile app makes
    no metadata request at all, so EVERY forecast it issues states the
    derived floor (app/olw_core's forecast.dart). The table being right
    matters more there than here, and this weekly comparison is the only
    thing watching it on the app's behalf.

    WHAT A DISAGREEMENT MEANS, IN EACH DIRECTION:

    - Observed OLDER than derived — the table claims a cycle is aligned
      before the slowest model even has it. The dangerous direction: the
      derived floor then understates the age of the guidance, which is the
      one thing the floor exists to never do.
    - Observed NEWER than derived — the window is opening earlier than the
      table says, so the floor is merely pessimistic. Safe, but it is the
      same evidence that the table has moved, and it bears on when the
      pipeline could usefully run (roadmap item 49).

    NEAR A WINDOW BOUNDARY THE TWO LEGITIMATELY DISAGREE — see
    _near_a_window_boundary, which does not change the verdict but says so
    in the message. The weekly slot (04:17 UTC) is over two hours inside an
    open window and clear of all four boundaries; a check-health run by hand
    is not necessarily.
    """
    derived = aligned_cycle_at(now)

    if observed is None:
        return AlignedWindowCheck(
            status=AlignedWindowStatus.NOT_CHECKED,
            message=(
                "No settled model run to compare against — the metadata endpoint was "
                f"unreachable, or its newest run landed within the last {RUN_SETTLE_MINUTES} "
                "minutes. The aligned-window table was not checked this time."
            ),
        )

    if observed.initialised_at == derived.initialised_at:
        return AlignedWindowCheck(
            status=AlignedWindowStatus.AGREES,
            message=(
                f"observed {observed.model} run {observed.initialised_at.isoformat()} matches the "
                f"derived aligned cycle at {now.isoformat()}."
            ),
        )

    return AlignedWindowCheck(
        status=AlignedWindowStatus.DRIFTED,
        message=_drift_message(now, derived, observed),
    )


# How many issuances back this looks. Seven days of runs at two or three a
# day is enough to tell a repeat from a coincidence, and short enough that a
# fault fixed a month ago has stopped being reported.
DEGRADATION_LOOKBACK_ISSUANCES = 20

# How many appearances of ONE code make a pattern. Two, not three, even
# though the incident that prompted this ran three times: by the third the
# reader had already been rained on, and the whole point is to arrive before
# that.
DEGRADATION_REPEAT_THRESHOLD = 2


class DegradationStatus(Enum):
    """Four outcomes. CLEAN and NOT_CHECKED are separated for the same reason
    check_aligned_window separates them — having nothing to read is not
    evidence that anything is healthy.

    ISOLATED and REPEATED are separated because only one of them should turn
    a job red. A single lost fetch is now visible in the committed record and
    on the published page (ROADMAP item 53.4), so failing the weekly job for
    it would make red the normal colour, and a red job that is normal is a
    green job. A code that comes back is different in kind: it is not bad
    luck, it is something broken that nobody has been told about.
    """

    CLEAN = "clean"
    ISOLATED = "isolated"
    REPEATED = "repeated"
    NOT_CHECKED = "not_checked"


@dataclass
class DegradationCheck:
    status: DegradationStatus
    message: str


def check_recent_degradations(
    recent: list[list[RunDegradation] | None],
    repeat_threshold: int = DEGRADATION_REPEAT_THRESHOLD,
) -> DegradationCheck:
    """Have recent issuances been running on less data than usual, and has
    the same block gone missing more than once?

    Pure — the caller reads the entries, the same way check_repo_staleness
    does not shell out to git and check_aligned_window does not fetch.
    `recent` is one list per issuance, in any order; only counts matter.

    WHY THIS IS A HEALTH CHECK AND NOT JUST A RECORD FIELD. On 2026-08-29 the
    forward hourly fetch timed out on three consecutive runs. Every one of
    them printed a line to stderr, inside a GitHub Actions log, and every one
    of them committed an entry that looked structurally identical to a clean
    run's. The failure was found when a reader was rained on while holding a
    forecast that said the evening was dry. Item 53.4's point is that the
    record and the page make a degraded run visible TO SOMEONE LOOKING AT IT,
    and nobody was looking. A weekly red job is the part that goes and finds
    someone.
    """
    # None is "this issuance predates degradation recording", not "this
    # issuance was fine" — see LogEntryMeta.degradations. Counting the two
    # together is the bug this signature exists to make impossible.
    recorded = [issuance for issuance in recent if issuance is not None]
    unrecorded = len(recent) - len(recorded)
    unread = (
        f" ({unrecorded} older issuance(s) predate this check and are not recorded)"
        if unrecorded
        else ""
    )

    if not recorded:
        return DegradationCheck(
            status=DegradationStatus.NOT_CHECKED,
            message=(
                f"Nothing to check: {len(recent)} issuance(s) in the window, none with "
                "degradations recorded. Not the same as nothing being wrong — a pipeline "
                "that has stopped committing shows up here as silence too, and repo "
                "staleness is the check for that."
            ),
        )

    counts = Counter(d.code for issuance in recorded for d in issuance)

    if not counts:
        return DegradationCheck(
            status=DegradationStatus.CLEAN,
            message=(
                f"every one of the last {len(recorded)} recorded issuance(s) had the data "
                f"it expects{unread}."
            ),
        )

    repeated = sorted(c for c, n in counts.items() if n >= repeat_threshold)
    summary = ", ".join(f"{code} ×{counts[code]}" for code in sorted(counts))

    if repeated:
        return DegradationCheck(
            status=DegradationStatus.REPEATED,
            message=(
                f"{', '.join(repeated)} recurred across the last {len(recorded)} recorded "
                f"issuance(s) ({summary}){unread}. A block that goes missing twice is not "
                "bad luck — find out why before a forecast is built on it again."
            ),
        )

    return DegradationCheck(
        status=DegradationStatus.ISOLATED,
        message=(
            f"one-off gaps in the last {len(recorded)} recorded issuance(s): {summary}"
            f"{unread}. Recorded and shown on the affected forecast; not failing the check "
            "on a single occurrence."
        ),
    )


# How long a CAP feed may go quiet before the check says so out loud. Not a
# failure threshold — see CapFeedStatus.QUIET. Ninety days is chosen to span
# a dry season without comment and to notice a feed that slept through a wet
# one: Kisumu's rains run roughly March-May and October-December.
CAP_QUIET_DAYS = 90


class CapFeedStatus(Enum):
    """Five outcomes, and the important line is between QUIET and UNREACHABLE.

    A CAP feed carries warnings, which are episodic. Months of silence may be
    entirely correct — Kenya's feed published nothing between 2026-05-07 and
    at least 2026-09-03, and the long rains had ended. A check that went red
    on a quiet season would be red most of the year, and a check that is
    usually red is a check nobody reads.

    A feed that stops ANSWERING is different, and is worth failing on. This
    endpoint was found only because somebody probed a namespace nobody had
    thought to try (ROADMAP item 2), so it moving again would otherwise go
    unnoticed until an alert was missed.
    """

    FRESH = "fresh"
    QUIET = "quiet"
    EMPTY = "empty"
    UNREACHABLE = "unreachable"
    NOT_CONFIGURED = "not_configured"


@dataclass
class CapFeedCheck:
    status: CapFeedStatus
    message: str
    days_since_newest: int | None = None


def newest_cap_item_age(feed_xml: str, *, now: datetime) -> int | None:
    """Whole days since the newest item's pubDate, or None if none parses.

    The NEWEST item, not the first: RSS ordering is a convention rather than a
    guarantee, and the age of a feed is the age of the most recent thing in
    it. An unparseable date is skipped rather than guessed at — a feed whose
    dates cannot be read is a feed whose age is unknown, and "unknown" must
    not quietly become "old" or "new".
    """
    newest: datetime | None = None
    for raw in re.findall(r"<pubDate>(.*?)</pubDate>", feed_xml, re.S):
        try:
            when = parsedate_to_datetime(raw.strip())
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if newest is None or when > newest:
            newest = when

    if newest is None:
        return None

    return (now - newest).days


def check_cap_feed(
    feed_xml: str | None, *, now: datetime, configured: bool = True
) -> CapFeedCheck:
    """Is the national warning feed answering, and has it said anything lately?

    Pure — the caller fetches, as with every other check here.

    `feed_xml` is None when the fetch failed, and an empty or item-less body
    when the service answered with nothing. Those are different: the first is
    a broken endpoint, the second is a working service in a quiet spell, and
    collapsing them would send somebody to debug a URL that is fine.
    """
    if not configured:
        return CapFeedCheck(
            status=CapFeedStatus.NOT_CONFIGURED,
            message="No CAP warning feed configured for this location.",
        )

    if feed_xml is None:
        return CapFeedCheck(
            status=CapFeedStatus.UNREACHABLE,
            message=(
                "The CAP warning feed did not answer. This endpoint was found by "
                "probing a namespace nobody had tried, so a move would otherwise "
                "go unnoticed until an alert was missed — check the URL before "
                "assuming the service is down."
            ),
        )

    age = newest_cap_item_age(feed_xml, now=now)
    if age is None:
        return CapFeedCheck(
            status=CapFeedStatus.EMPTY,
            message=(
                "The CAP feed answered but carries no readable item dates. The "
                "service is up and has published nothing this check can date."
            ),
        )

    if age > CAP_QUIET_DAYS:
        return CapFeedCheck(
            status=CapFeedStatus.QUIET,
            message=(
                f"The CAP feed is up, and its newest alert is {age} days old. "
                "Silence is not failure — warnings are episodic — but a feed that "
                "sleeps through a wet season is a feed to stop relying on. See "
                "ROADMAP item 2."
            ),
            days_since_newest=age,
        )

    return CapFeedCheck(
        status=CapFeedStatus.FRESH,
        message=f"newest CAP alert is {age} day(s) old.",
        days_since_newest=age,
    )
