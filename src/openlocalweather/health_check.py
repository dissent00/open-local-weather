"""Weekly health checks: Gemini model deprecation status + repo staleness.

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

Both checks run on their own weekly schedule (see
.github/workflows/health_check.yml), decoupled from the daily forecast
pipeline's own cron so a health-check failure never blocks or depends on
that day's forecast run.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from pydantic import BaseModel

from openlocalweather.llm.provider import LLMProvider

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
