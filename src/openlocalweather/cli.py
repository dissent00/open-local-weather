"""Command-line entrypoint.

`olw run-daily` is what .github/workflows/daily.yml invokes. It reads
secrets from the environment (never from CLI args, so they don't end up in
shell history or process listings) and leaves git commit/push and any
required approvals to the caller — see pipeline.py's module docstring for
why.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

import requests
from pathlib import Path

from openlocalweather import __version__
from openlocalweather.config import load_location_config
from openlocalweather.coverage import actionable, detect_coverage, detect_trigger_regression
from openlocalweather.defaults import (
    LEAD_TIMES_DAYS,
    REVIEW_MIN_CHECKS_FOR_COMPARISON,
    scored_models,
)
from openlocalweather.dates import add_days, today_in_tz
from openlocalweather.fetch import metar as metar_fetch
from openlocalweather.fetch import model_run as model_run_fetch
from openlocalweather.fetch.bulletin import BulletinFetcher, NullBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd import KenyaKMDBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd_daily import KenyaKMDDailyFetcher
from openlocalweather.fetch.open_meteo import OpenMeteoFetchError
from openlocalweather.health_check import (
    AlignedWindowStatus,
    DEGRADATION_LOOKBACK_ISSUANCES,
    CapFeedStatus,
    DegradationStatus,
    check_aligned_window,
    check_cap_feed,
    check_recent_degradations,
    check_model_deprecation,
    check_repo_staleness,
)
from openlocalweather.llm.anthropic import DEFAULT_BASE_URL as DEFAULT_ANTHROPIC_BASE_URL
from openlocalweather.llm.anthropic import DEFAULT_MAX_TOKENS as DEFAULT_ANTHROPIC_MAX_TOKENS
from openlocalweather.llm.anthropic import AnthropicProvider
from openlocalweather.llm.gemini import GeminiProvider, LLMResponseError
from openlocalweather.llm.openai_compat import OpenAICompatProvider
from openlocalweather.pipeline import (
    ForecastSkipped,
    PipelineDeps,
    PipelineRunResult,
    RefreshWithoutMorningRunError,
    run_daily_pipeline,
    run_forecast,
    run_refresh_pipeline,
)
from openlocalweather.publish.email_gmail import GmailSMTPSender, parse_recipient_list
from openlocalweather.publish.pages import GitHubPagesPublisher
from openlocalweather.review import build_weekly_review
from openlocalweather.store.actuals_cache import (
    as_date_dict,
    read_actuals_cache,
    replace_all,
    write_actuals_cache,
)
from openlocalweather import replay
from openlocalweather.backfill import backfill_entry_baselines
from openlocalweather.divergence import compare_sources
from openlocalweather.pipeline import apply_station_readings
from openlocalweather.baselines import CLIMATOLOGY_MODEL_ID, PERSISTENCE_MODEL_ID
from openlocalweather.models import RunDegradation
from openlocalweather.spend import record_attempt
from openlocalweather.store.log_store import (
    list_log_dates,
    make_log_lookup,
    read_log_entry,
    write_log_entry,
)
from openlocalweather.store.track_record import read_track_record, write_track_record
from openlocalweather.verify.pipeline import run_deterministic_verification_and_scoring

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "location.yaml"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"

# The CAP feed is 4 KB and this check must not hold up the rest of the run.
CAP_FEED_TIMEOUT_S = 20

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
# "high" by default for the actual forecast pipeline (run-daily,
# refresh-forecast) — measured against the real production prompt, "high"
# vs "low" is a real difference (4,235 vs 739 thinking tokens) and at
# ~45K tokens/call against a 250K-token/run free-tier limit there's ample
# headroom. check-health's model-deprecation check is a simple factual
# lookup, not multi-step reasoning, and intentionally does NOT set this —
# it uses Gemini's own default.
DEFAULT_GEMINI_THINKING_LEVEL = "high"

# Which LLMProvider implementation to build. "gemini" keeps this project's
# original free-tier path and is the default so existing deployments (and
# every doc written before multi-provider support) keep working with no
# environment changes at all. "openai" selects OpenAICompatProvider, which
# covers OpenAI, OpenRouter, Groq, Together, vLLM and Ollama — see that
# module's docstring.
DEFAULT_LLM_PROVIDER = "gemini"
VALID_LLM_PROVIDERS = ("gemini", "anthropic", "openai")


def _github_repo_slug() -> str:
    """Best-effort "owner/repo" for building the site's "View source on
    GitHub" link. GITHUB_REPOSITORY is set automatically by GitHub Actions;
    the git-remote fallback covers local runs. Returns "" (a harmless,
    non-fatal broken link) if neither source is available."""
    env_value = os.environ.get("GITHUB_REPOSITORY", "")
    if env_value:
        return env_value
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True, cwd=REPO_ROOT
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    url = result.stdout.strip()
    # Handles both "git@github.com:owner/repo.git" and
    # "https://github.com/owner/repo.git" remote URL forms.
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git")
    return ""


def _build_bulletin_fetcher(location) -> BulletinFetcher:
    local_bulletin_url = location.local_bulletin_url
    if not local_bulletin_url:
        return NullBulletinFetcher()
    # KMD's daily forecast additionally yields a scoreable prediction — see
    # fetch/bulletin/kenya_kmd_daily. Selected on the URL here rather than
    # auto-detected, matching how the weekly fetcher is wired.
    if "daily-forecast" in local_bulletin_url:
        from openlocalweather.dates import add_days

        return KenyaKMDDailyFetcher(
            local_bulletin_url,
            area_name=location.local_bulletin_area_name,
            model_id=location.local_bulletin_model_id,
            # The Day+3 slot this run will try to fill. Passed in so the
            # fetcher does no clock or timezone reasoning of its own.
            day3_target=add_days(today_in_tz(location.timezone), 3),
        )
    # This repo currently ships exactly one reference bulletin
    # implementation (Kenya Meteorological Department). A fork with a
    # different local met service should replace this wiring with its own
    # BulletinFetcher — see fetch/bulletin/__init__.py's module docstring
    # for why this isn't auto-detected from the URL.
    return KenyaKMDBulletinFetcher(local_bulletin_url)


def _env(name: str, default: str = "") -> str:
    """os.environ.get(), but treats an EMPTY value as absent.

    Load-bearing under GitHub Actions: `FOO: ${{ vars.FOO }}` sets FOO to
    the empty string when that repository variable isn't defined, rather
    than leaving it unset. A plain os.environ.get(name, default) therefore
    returns "" instead of the default — which would have set GEMINI_MODEL
    to "" (a hard crash: GeminiProvider rejects an empty model id) and
    silently downgraded GEMINI_THINKING_LEVEL from the deliberately-chosen
    "high" to None on every scheduled run.
    """
    return os.environ.get(name, "").strip() or default


def _build_llm_provider(*, thinking_level: str | None = None):
    """Builds the configured LLMProvider from the environment.

    Secrets and endpoints come from env vars, never CLI args, for the same
    reason as everything else here — they'd otherwise land in shell history
    and process listings. `thinking_level` is Gemini-specific and simply
    ignored by other providers; check-health passes None because a factual
    model-lookup doesn't need extended reasoning.
    """
    provider_name = _env("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).lower()

    if provider_name == "gemini":
        api_key = _env("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("GEMINI_API_KEY environment variable is required (LLM_PROVIDER=gemini).")
        return GeminiProvider(
            api_key=api_key,
            model=_env("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            thinking_level=thinking_level,
        )

    if provider_name == "anthropic":
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL")
        if not api_key or not model:
            raise SystemExit(
                "LLM_PROVIDER=anthropic requires LLM_API_KEY and LLM_MODEL to be set. "
                "See QUICKSTART.md for recommended model ids."
            )
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            # Only needed for a proxy/gateway; defaults to api.anthropic.com.
            base_url=_env("LLM_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL),
            max_tokens=int(_env("LLM_MAX_TOKENS", str(DEFAULT_ANTHROPIC_MAX_TOKENS))),
        )

    if provider_name == "openai":
        base_url = _env("LLM_BASE_URL")
        model = _env("LLM_MODEL")
        if not base_url or not model:
            raise SystemExit(
                "LLM_PROVIDER=openai requires LLM_BASE_URL and LLM_MODEL to be set "
                "(plus LLM_API_KEY for any hosted endpoint). "
                "See QUICKSTART.md for per-service values."
            )
        return OpenAICompatProvider(
            # Empty is legitimate here: local runtimes like Ollama don't
            # need a key. Hosted endpoints will fail loudly on the first
            # call, which is clearer than guessing at intent up front.
            api_key=_env("LLM_API_KEY"),
            model=model,
            base_url=base_url,
            json_mode=_env("LLM_JSON_MODE", "json_schema"),
        )

    raise SystemExit(
        f"Unknown LLM_PROVIDER {provider_name!r} — expected one of {', '.join(VALID_LLM_PROVIDERS)}."
    )


def _build_pipeline_deps(config_path: str, data_dir: str, docs_dir: str, public_webpage_url: str) -> PipelineDeps:
    location = load_location_config(config_path)
    data_path = Path(data_dir)

    gemini_thinking_level = _env("GEMINI_THINKING_LEVEL", DEFAULT_GEMINI_THINKING_LEVEL) or None
    llm_provider = _build_llm_provider(thinking_level=gemini_thinking_level)
    waqi_token = _env("WAQI_TOKEN")

    # Publisher needs an absolute base URL to build sane nav links (see
    # publish/pages.py's module docstring) — skip it gracefully rather than
    # publish broken-relative-link pages if none was given, same "None =
    # skip" pattern pipeline.py already uses for publisher/email_sender.
    publisher = None
    if public_webpage_url:
        publisher = GitHubPagesPublisher(
            docs_dir=Path(docs_dir),
            location=location,
            base_url=public_webpage_url,
            github_repo=_github_repo_slug(),
            all_dates_provider=lambda: list_log_dates(data_path),
            entry_provider=make_log_lookup(data_path),
            review_provider=lambda: build_weekly_review(
                log_lookup=make_log_lookup(data_path),
                actuals=as_date_dict(read_actuals_cache(data_path).primary),
                all_log_dates=list_log_dates(data_path),
                today=today_in_tz(location.timezone),
                models=scored_models(location.local_bulletin_model_id),
            ),
        )

    # Gmail SMTP direct-send — see publish/email_gmail.py's module
    # docstring for why this path was chosen over a third-party ESP.
    # Skipped gracefully (same "None = skip" pattern as publisher above)
    # unless both credentials AND at least one recipient are configured.
    email_sender = None
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipients = parse_recipient_list(os.environ.get("SUBSCRIBER_EMAILS", ""))
    if gmail_address and gmail_app_password and recipients:
        email_sender = GmailSMTPSender(
            gmail_address=gmail_address,
            gmail_app_password=gmail_app_password,
            recipients=recipients,
            location_name=location.primary_place_name,
        )

    return PipelineDeps(
        location=location,
        data_dir=data_path,
        llm_provider=llm_provider,
        public_webpage_url=public_webpage_url,
        waqi_token=waqi_token,
        bulletin_fetcher=_build_bulletin_fetcher(location),
        # GitHub sets this; empty for a local run, which is itself accurate.
        trigger_source=_env("TRIGGER_SOURCE"),
        publisher=publisher,
        email_sender=email_sender,
    )


def _run_daily(args: argparse.Namespace) -> int:
    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)
    try:
        result = run_daily_pipeline(deps, dry_run=args.dry_run)
    except OpenMeteoFetchError as e:
        print(f"Critical Error: pipeline aborted, a required weather fetch failed: {e}", file=sys.stderr)
        return 1
    except LLMResponseError as e:
        print(f"Critical Error: pipeline aborted, the LLM call failed: {e}", file=sys.stderr)
        return 1

    _print_daily_result(result, args.dry_run)
    return 0


def _run_refresh(args: argparse.Namespace) -> int:
    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)
    try:
        result = run_refresh_pipeline(deps, dry_run=args.dry_run)
    except RefreshWithoutMorningRunError as e:
        print(f"Critical Error: {e}", file=sys.stderr)
        return 1
    except OpenMeteoFetchError as e:
        print(f"Critical Error: refresh aborted, a required weather fetch failed: {e}", file=sys.stderr)
        return 1
    except LLMResponseError as e:
        print(f"Critical Error: refresh aborted, the LLM call failed: {e}", file=sys.stderr)
        return 1

    _print_refresh_result(result, args.dry_run)
    return 0


# What kind of run this turned out to be, on its own line and first.
#
# A contract, not decoration: .github/workflows/forecast.yml greps for these
# to pick a commit subject, because one workflow now produces both kinds and
# "forecast:" against every commit would flatten the archive's own history.
# Change the strings and change the workflow with them.
RUN_KIND_FIRST = "run-kind: first"
RUN_KIND_REISSUE = "run-kind: reissue"
RUN_KIND_SKIPPED = "run-kind: skipped"


def _print_daily_result(result, dry_run: bool) -> None:
    entry = result.log_entry
    print(f"Pipeline run complete for {result.today} (dry_run={dry_run}).")
    print(f"  rain_expected:   {entry.rain_expected}")
    print(f"  temp:            {entry.temp_high_low_display}")
    print(f"  synoptic:        {entry.synoptic_pattern}")
    print(f"  newly_verified:  {result.newly_verified}")
    print(f"  published:       {result.published}")
    print(f"  emailed:         {result.emailed}")
    if dry_run:
        print("\n--- narrative preview (not written to data/, nothing published/emailed) ---\n")
        print(entry.narrative_markdown)


def _print_refresh_result(result, dry_run: bool) -> None:
    entry = result.log_entry
    print(f"Refresh run complete for {result.today} (dry_run={dry_run}).")
    print(f"  rain_expected:   {entry.rain_expected}")
    print(f"  temp:            {entry.temp_high_low_display}")
    print(f"  synoptic:        {entry.synoptic_pattern}")
    print(f"  refreshed_at:    {entry.meta.refreshed_at}")
    print(f"  published:       {result.published}")
    if dry_run:
        print("\n--- narrative preview (not written to data/, nothing published) ---\n")
        print(entry.narrative_markdown)


def _run_forecast(args: argparse.Namespace) -> int:
    """The verb to schedule. Which kind of run this is, is the day's business,
    not the operator's — see pipeline.run_forecast.
    """
    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)
    try:
        result = run_forecast(deps, dry_run=args.dry_run, force=args.force)
    except OpenMeteoFetchError as e:
        print(f"Critical Error: forecast aborted, a required weather fetch failed: {e}", file=sys.stderr)
        return 1
    except LLMResponseError as e:
        print(f"Critical Error: forecast aborted, the LLM call failed: {e}", file=sys.stderr)
        return 1

    # A skip is the system working, so it exits 0. A red run for a backup
    # slot that correctly did nothing teaches an operator to ignore red runs.
    if isinstance(result, ForecastSkipped):
        print(RUN_KIND_SKIPPED)
        print(f"Nothing to do for {result.today}: {result.reason}")
        return 0

    if isinstance(result, PipelineRunResult):
        print(RUN_KIND_FIRST)
        _print_daily_result(result, args.dry_run)
        return 0

    print(RUN_KIND_REISSUE)
    _print_refresh_result(result, args.dry_run)
    return 0


def _recent_issuance_degradations(data_dir: str) -> list[list[RunDegradation]]:
    """Every issuance of the most recent stored days, newest day first, as
    the degradation lists check_recent_degradations wants.

    Per ISSUANCE, not per day. A day holding a degraded morning and a clean
    evening is one of each, and collapsing it to "that day was degraded"
    would both overstate the fault and hide a repeat that happened twice
    inside one day — which is exactly the shape the 2026-08-29 incident had
    (three runs, two of them on the same date).
    """
    path = Path(data_dir)
    out: list[list[RunDegradation]] = []
    for d in sorted(list_log_dates(path), reverse=True):
        entry = read_log_entry(path, d)
        if entry is None:
            continue
        for issuance in entry.issuance_log():
            out.append(issuance.degradations)
            if len(out) >= DEGRADATION_LOOKBACK_ISSUANCES:
                return out
    return out


def _days_since_last_commit() -> int:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    last_commit = datetime.fromtimestamp(int(result.stdout.strip()), tz=timezone.utc)
    return (datetime.now(timezone.utc) - last_commit).days


def _attach_spend_hook(provider, data_dir, *, purpose: str, max_calls: int) -> None:
    """Make a provider report every HTTP request it makes to the ledger.

    Set after construction rather than passed in, matching the pipeline: the
    provider is built here and the ledger belongs to the data directory, and a
    constructor knowing about both would couple them for no reason.

    WHY THIS IS A FUNCTION RATHER THAN A LINE. It used to be a line, in
    pipeline.py only — so `check-health` built a provider, called the model,
    and recorded nothing. Its weekly deprecation check spends up to
    MAX_ATTEMPTS billable requests, and the cap cannot bound what it cannot
    see. Worse than uncapped: it also makes the ledger disagree with the
    provider's own request count for reasons nobody can reconstruct, which is
    the reconciliation that found it.
    """

    def _record() -> None:
        used = record_attempt(
            data_dir,
            provider=type(provider).__name__,
            model=getattr(provider, "model", "unknown"),
            purpose=purpose,
            max_calls=max_calls,
        )
        print(f"LLM call {used}/{max_calls} in the last 24h")

    provider.before_attempt = _record


def _run_check_health(args: argparse.Namespace) -> int:
    # No thinking_level: the deprecation check is a factual lookup, not the
    # multi-step reasoning the forecast pipeline asks for.
    llm = _build_llm_provider(thinking_level=None)
    # Counted like any other call — see _attach_spend_hook for the gap this
    # closes. The config is loaded here rather than further down because the
    # cap's size lives in it, and a hook attached after the call would be no
    # hook at all.
    #
    # A refusal is caught below and reported as a skipped check, which is the
    # right outcome: being out of budget is a real answer, not a reason to
    # spend anyway.
    location = load_location_config(args.config)
    _attach_spend_hook(
        llm, Path(args.data_dir), purpose="health-check",
        max_calls=location.max_llm_calls_per_24h,
    )
    model_name = llm.model

    ok = True

    print(f"Checking whether '{model_name}' is listed as deprecated...")
    try:
        result = check_model_deprecation(llm, model_name)
    except LLMResponseError as e:
        print(f"  Could not complete the model-deprecation check: {e}", file=sys.stderr)
        ok = False
    else:
        if result.deprecated_or_scheduled:
            print(f"  WARNING: '{model_name}' may be deprecated or scheduled for shutdown.")
            print(f"  {result.notes}")
            ok = False
        else:
            print(f"  OK — {result.notes}")

    # Data coverage. Runs offline off the committed record, so it costs
    # nothing and works even when the LLM check above failed. `location` is
    # already loaded above, for the spend cap.
    data_path = Path(args.data_dir)
    # Operational coverage: the reliable trigger can die while the unreliable
    # fallback keeps producing forecasts, which is invisible to every other
    # check here precisely because output continues.
    print("Checking whether the external trigger is still firing...")
    trigger = detect_trigger_regression(make_log_lookup(data_path), today_in_tz(location.timezone))
    if trigger is not None:
        # Fails the check, unlike the data-coverage findings below. A missing
        # variable degrades one model; a dead trigger degrades delivery of the
        # whole forecast back to the unreliable path, which is what this
        # deployment already proved is not good enough.
        print(f"  WARNING: {trigger.message}")
        ok = False
    else:
        print("  OK — or no external trigger is configured for this deployment.")

    print("Checking data coverage across the stored record...")
    findings = detect_coverage(
        make_log_lookup(data_path),
        today_in_tz(location.timezone),
        scored_models(location.local_bulletin_model_id),
        LEAD_TIMES_DAYS,
    )
    needs_attention = actionable(findings, location.acknowledged_coverage_gaps)
    if needs_attention:
        # Not a failure: the forecast is fine, values are recorded as unknown
        # rather than wrong. But a variable nobody notices has stopped
        # arriving is one nobody fixes — which is how ECMWF went without
        # Day+0 wind for the life of this deployment.
        print(f"  {len(needs_attention)} item(s) worth checking:")
        for f in needs_attention:
            print(f"    - {f.message}")
    else:
        print("  OK — every model is supplying what its peers supply.")
    inert = len(findings) - len(needs_attention)
    if inert:
        print(f"  ({inert} known or universal gap(s) not reported — see acknowledged_coverage_gaps.)")

    # Slow rot, like the staleness proxy below: the aligned-window table is
    # a hand measurement from 2026-08-11, every forecast that cannot observe
    # a real run falls back to it, and nothing else in this project would
    # ever notice it had moved.
    print("Checking the aligned-window table against a live observation...")
    now = datetime.now(timezone.utc)
    window = check_aligned_window(now, model_run_fetch.fetch_settled_run(now))
    if window.status is AlignedWindowStatus.DRIFTED:
        print(f"  WARNING: {window.message}")
        # Fails the check, which is the entire point of moving this here:
        # the forecast pipeline already prints the same comparison into a
        # run log nobody reads. A red weekly job is the notification.
        ok = False
    elif window.status is AlignedWindowStatus.AGREES:
        print(f"  OK — {window.message}")
    else:
        # Not a failure. The observation is best-effort by design (see
        # fetch/model_run.py), and a silent endpoint says nothing about
        # whether the table is still right.
        print(f"  {window.message}")

    # ROADMAP item 53.4. The record and the page now say when a run was
    # degraded, but both of those only reach a person who goes and looks, and
    # the incident this comes from ran degraded three times with nobody
    # looking. This is the surface that goes and finds someone.
    print("Checking recent issuances for missing data...")
    degradation = check_recent_degradations(_recent_issuance_degradations(args.data_dir))
    if degradation.status is DegradationStatus.REPEATED:
        print(f"  WARNING: {degradation.message}")
        ok = False
    elif degradation.status is DegradationStatus.CLEAN:
        print(f"  OK — {degradation.message}")
    else:
        print(f"  {degradation.message}")

    # ROADMAP item 2. Two questions, and only one of them is a failure: is the
    # warning feed still answering, and has it said anything lately?
    if location.cap_feed_url:
        print("Checking the CAP warning feed...")
        try:
            resp = requests.get(
                location.cap_feed_url, timeout=CAP_FEED_TIMEOUT_S,
                headers={"User-Agent": "open-local-weather/check-health"},
            )
            body = resp.text if resp.status_code == 200 else None
        except requests.RequestException:
            body = None

        cap = check_cap_feed(body, now=datetime.now(timezone.utc))
        if cap.status is CapFeedStatus.UNREACHABLE:
            # The one failing case. A quiet feed may be correct; a feed that
            # stopped answering has moved or died, and nothing else here would
            # notice until an alert was missed.
            print(f"  WARNING: {cap.message}")
            ok = False
        elif cap.status is CapFeedStatus.FRESH:
            print(f"  OK — {cap.message}")
        else:
            print(f"  {cap.message}")

    days = _days_since_last_commit()
    print(f"Days since last commit: {days}")
    if check_repo_staleness(days):
        print(
            f"  WARNING: last commit was {days} days ago. GitHub auto-disables scheduled "
            "workflows after 60 days of repo inactivity — investigate why daily.yml isn't "
            "running or isn't pushing."
        )
        ok = False
    else:
        print("  OK.")

    return 0 if ok else 1


def _run_divergence(args) -> int:
    """Where the station and the reanalysis disagree — ROADMAP item 45.

    Reads the stored record and prints. Decides nothing: the sources are the
    truth candidates, so there is no yardstick that could say which is right,
    and this exists to put a number in front of the person who has to choose.

    Free and offline — no fetch, no LLM. Safe to run as often as you like
    while the record accumulates.
    """
    actuals = as_date_dict(read_actuals_cache(Path(args.data_dir)).primary)
    if not actuals:
        print("No stored observations.", file=sys.stderr)
        return 1

    d = compare_sources(actuals)
    days = sorted(actuals)
    print(f"Record: {days[0]} to {days[-1]}, {len(days)} day(s) stored.\n")

    print("Continuous variables — station MINUS reanalysis, over days both reported:")
    for v in d.variables:
        if v.days == 0:
            print(f"  {v.variable:16s} no overlap yet")
            continue
        print(
            f"  {v.variable:16s} n={v.days:<4d} mean {v.mean_signed:+.2f}  "
            f"mean|err| {v.mean_absolute:.2f}  worst {v.max_absolute:.2f}"
        )

    o = d.occurrence
    print(f"\nRain occurrence, over the {o.days} day(s) the station reported:")
    print(f"  both saw rain        {o.both_wet}")
    print(f"  both saw none        {o.both_dry}")
    print(f"  station only         {o.station_only}   <- the reanalysis missed it")
    print(f"  reanalysis only      {o.archive_only}   <- rain elsewhere in the cell, probably")

    # Thinness is reported PER SECTION, not once for the whole report. The
    # two halves fill up at completely different rates: occurrence has been
    # accumulating since the station was first read, while the continuous
    # variables only started on 2026-09-03. A single verdict covering both
    # announced "too thin to conclude anything" over a 43-day occurrence
    # table, which is exactly the kind of true-but-useless summary that
    # teaches people to skip the last line.
    variable_days = max((v.days for v in d.variables), default=0)
    if variable_days < REVIEW_MIN_CHECKS_FOR_COMPARISON:
        print(
            f"\n  (thin: fewer than {REVIEW_MIN_CHECKS_FOR_COMPARISON} overlapping "
            "days on any variable — let it accumulate.)"
        )
    if o.days < REVIEW_MIN_CHECKS_FOR_COMPARISON:
        print(
            f"  (thin: fewer than {REVIEW_MIN_CHECKS_FOR_COMPARISON} reported days.)"
        )

    print(
        "\nThis says where they disagree, never which is right — there is no "
        "held-out truth to decide that, and a point observation misses rain "
        "a few kilometres away just as a 25 km mean invents it. See ROADMAP "
        "item 45."
    )
    return 0


def _run_replay(args) -> int:
    """Send a fixed set of inputs through the LLM and keep the outputs.

    ROADMAP item 27. Run it once before a prompt change and once after, then
    `olw replay-diff` the two directories: that turns "did this edit move
    anything I did not intend" from an impression into a list. Items 48 and
    53.3 both changed the prompt with no way to answer it.

    IT SPENDS REAL MONEY, one call per case, on the operator's own key and
    counted by the same spend cap the forecast uses (item 26) — which means a
    careless replay can exhaust the day's budget and refuse the morning
    forecast. So the cost is printed and confirmation is required. There is
    no dry-run alternative: a replay that does not call the model is not a
    replay of anything.
    """
    cases = replay.frozen_cases()
    print(f"{len(cases)} frozen case(s) from the committed prompt vectors:")
    for c in cases:
        print(f"  {c.name}")
    print(f"\nThis makes {len(cases)} LLM call(s) on your key, counted against the spend cap.")

    if not args.yes:
        print("Nothing was sent. Re-run with --yes to spend.")
        return 0

    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)
    results = replay.run_replay(deps.llm_provider, cases)
    out = Path(args.out)
    replay.write_replay(out, results)

    print(f"\nWrote {len(results)} result(s) to {out}/replay.json")
    for r in results:
        print(f"  {r.case[:52]:54s} {r.latency_s:5.1f}s")
    return 0


def _run_replay_diff(args) -> int:
    """What moved between two replays, and whether it reached the scored half.

    The prose changing is a judgement call. `today_properties` changing means
    the blended call this project SCORES and publishes has moved, which is a
    different event and the one worth stopping for.
    """
    before = replay.read_replay(Path(args.before))
    after = replay.read_replay(Path(args.after))
    diffs = replay.diff_replays(before, after)

    if not diffs:
        print(f"No differences across {len(before)} case(s).")
        return 0

    scored = [d for d in diffs if d.scored_changed]
    print(f"{len(diffs)} of {len(before)} case(s) differ; {len(scored)} touched the scored call.\n")
    for d in diffs:
        mark = "SCORED" if d.scored_changed else "prose "
        print(f"  [{mark}] {d.case}")
        for f in d.changed_fields:
            print(f"             {f}")
    if scored:
        print(
            "\nA change to today_properties is a change to the forecast, not the "
            "wording — it is scored against tomorrow's observations and published "
            "on the accuracy page."
        )
    return 0


def _run_backfill_baselines(args) -> int:
    """Add persistence and climatology to days already stored — ROADMAP 57.

    Baselines are built at forecast time, so only runs from the day they
    shipped carry them, and `rebuild-record` cannot fill the gap: it
    re-derives from stored predictions and cannot invent one that was never
    made. Without this the comparison the item exists for is unreadable for
    about ten days.

    Not hindsight — see backfill.py. What it writes is exactly what those runs
    would have produced, because both baselines read only data that existed at
    each issuance.

    DRY RUN IS THE DEFAULT-ADJACENT HABIT HERE, not a nicety. This is the only
    command in the project that edits historical entries, so it prints the
    per-day plan either way and `--dry-run` stops before writing. Run it once
    to read the plan, then again to apply it.

    Scoring is deliberately NOT done here. `rebuild-record` is the command
    that turns predictions into verified figures, it already exists, and it is
    idempotent; doing it inline would duplicate that and hide which step
    produced which change.

    ONE SIDE EFFECT WORTH KNOWING ABOUT, measured on the 2026-08-31 run over
    21 entries. Rewriting a file re-serialises the whole entry, so fields the
    current schema has and an older file lacked appear filled with their
    defaults — `degradations: null`, `earlier_issuances: []`,
    `rain_probability_pct: null`. That is cosmetic: absent and default-valued
    parse to the same thing, and all 21 entries were verified identical as
    MODELS once the new baseline rows were removed. It is written down
    because the diff looks much larger than the change is, and someone
    reviewing it later should not have to work that out from scratch.
    """
    data_dir = Path(args.data_dir)
    actuals = as_date_dict(read_actuals_cache(data_dir).primary)
    if not actuals:
        print("No stored observations — nothing to build a baseline from.", file=sys.stderr)
        return 1

    changed, skipped, impossible = [], [], []
    for d in sorted(list_log_dates(data_dir)):
        entry = read_log_entry(data_dir, d)
        if entry is None:
            continue

        updated = backfill_entry_baselines(entry, actuals)
        if updated is None:
            # Two different reasons, separated because they call for different
            # actions: one is "already done", the other is "this day can never
            # have them" — the first day of the record has nothing before it.
            has_baselines = any(
                p.model in (PERSISTENCE_MODEL_ID, CLIMATOLOGY_MODEL_ID)
                for p in entry.model_predictions.day0
            )
            (skipped if has_baselines else impossible).append(d)
            continue

        changed.append(d)
        if not args.dry_run:
            write_log_entry(data_dir, updated)

    verb = "would add" if args.dry_run else "added"
    print(f"{verb} baselines to {len(changed)} day(s).")
    for d in changed:
        print(f"  {d}")
    if skipped:
        print(f"{len(skipped)} day(s) already had them and were left alone.")
    if impossible:
        print(
            f"{len(impossible)} day(s) have no earlier observation to build from "
            f"(the start of the record): {', '.join(str(d) for d in impossible)}"
        )

    if args.dry_run:
        print("\nDry run — nothing was written. Re-run without --dry-run to apply.")
    elif changed:
        # Same caveat rebuild-record carries: predictions are not figures.
        print(
            "\nThese are predictions, not scores. Run `olw rebuild-record` to verify "
            "them against the observations, and note that the published page is "
            "rendered by a forecast run, so it stays stale until the next one."
        )

    return 0


def _run_rebuild_record(args) -> int:
    """Re-derive the accuracy record from raw stored predictions and freshly
    fetched observations.

    Exists because the record is supposed to be re-derivable — verify/ never
    accumulates a running total, precisely so a correction to what was
    OBSERVED propagates through every figure rather than only affecting days
    scored after the fix. This is the command that exercises that property.

    Written for the 2026-08-26 correction, when airport-observed thunder
    became part of what a rain forecast is scored against and 5 of the 42
    stored days turned out to have been filed as dry while a storm passed
    over. It is not a one-off script: any future change to what counts as an
    observation needs exactly this, and a rebuild nobody can run is a record
    nobody can check.

    Does NOT touch the narrative notes already published in data/log/*.json.
    Those record what was said at the time, including where it was wrong, and
    rewriting them would be a different and much less honest thing than
    recomputing an arithmetic record.
    """
    location = load_location_config(args.config)
    data_dir = Path(args.data_dir)

    cache = read_actuals_cache(data_dir)
    actuals = as_date_dict(cache.primary)
    if not actuals:
        print("No actuals cached — nothing to rebuild.", file=sys.stderr)
        return 1

    before = {d: a.observed_convection() for d, a in actuals.items()}

    # ONE fetch for both, and the readings matter here as much as in the
    # daily pipeline: a rebuild that dropped them would silently erase weeks
    # of the accumulation item 45's sequencing depends on.
    weather_by_date, readings_by_date = metar_fetch.observed_station_data(
        location.metar_station_icao, min(actuals), max(actuals), location.timezone
    )
    if weather_by_date is None:
        print(
            f"No METAR observations available for {location.metar_station_icao or '(no station configured)'} — "
            "rebuilding from the reanalysis alone.",
            file=sys.stderr,
        )
        weather_by_date = {}

    for day, actual in actuals.items():
        if day not in weather_by_date:
            continue

        observed = weather_by_date[day]
        actual.thunder = observed.thunder
        actual.precipitation = observed.precipitation
        # All THREE fields, not the two booleans. Storing the flag without the
        # onset leaves the day-over-day description with no timing to reach
        # the dry band's shower phrases with, so a corrected day goes on being
        # called "dry" — see DailyActual.observed_onset and ROADMAP 53.1a.
        actual.precipitation_onset = observed.precipitation_onset
        apply_station_readings(actual, (readings_by_date or {}).get(day))

    changed = sorted(d for d, was in before.items() if was != actuals[d].observed_convection())
    print(
        f"Days held: {len(actuals)}  "
        f"observed thunder: {sum(1 for v in weather_by_date.values() if v.thunder)}  "
        f"observed precipitation: {sum(1 for v in weather_by_date.values() if v.precipitation)}"
    )
    for day in changed:
        # Which way the day moved, and on which observation, so the operator
        # can check the claim against the reports rather than taking the
        # number on trust.
        #
        # THE DIRECTION IS NOT ALWAYS "dry -> convective". A re-fetch that
        # finds the station reported and saw nothing takes a day back OUT of
        # the record, and this line was hard-coded to one direction — so it
        # announced the opposite of what it had just done, and named an
        # observation that did not exist. Found reading the diff for item 53.
        observed = weather_by_date[day]
        saw = " and ".join(
            w for w, seen in (("thunder", observed.thunder), ("rain", observed.precipitation)) if seen
        )
        direction = (
            "dry -> convective" if actuals[day].observed_convection() else "convective -> dry"
        )
        reported = f"airport reported {saw}" if saw else "airport reported neither"
        print(f"  {day}: {direction} (reanalysis {actuals[day].precip_mm} mm, {reported})")
    print(f"Days whose observed record changed: {len(changed)}")

    log_dates = list_log_dates(data_dir)
    today = today_in_tz(location.timezone)
    result = run_deterministic_verification_and_scoring(
        log_lookup=make_log_lookup(data_dir),
        prior_track_record=read_track_record(data_dir),
        earliest_log_date=min(log_dates) if log_dates else None,
        actuals_primary=actuals,
        today=today,
        yesterday=add_days(today, -1),
        models=scored_models(location.local_bulletin_model_id),
    )

    prior = {
        (e.model, e.lead_time_days): e.all_time_rain_pct
        for e in read_track_record(data_dir).entries
    }
    print("\nDay+0 rain accuracy, all-time:")
    for entry in sorted(result.updated_track_record.entries, key=lambda e: (e.lead_time_days, e.model)):
        if entry.lead_time_days != 0:
            continue

        was = prior.get((entry.model, entry.lead_time_days))
        now = entry.all_time_rain_pct
        arrow = "" if was == now else f"   (was {_pct(was)})"
        print(f"  {entry.model:16} {_pct(now)}  over {entry.all_time_checks} checks{arrow}")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 0

    replace_all(cache, "primary", actuals)
    write_actuals_cache(data_dir, cache)
    write_track_record(data_dir, result.updated_track_record)
    # TWO DATA FILES, AND DELIBERATELY NOT THE PAGES. `docs/accuracy.html` is
    # rendered by a forecast run, so a rebuild leaves the PUBLISHED figures
    # stale until the next scheduled run republishes them — measured
    # 2026-08-30, when correcting two days dropped every model's all-time
    # Day+0 rain accuracy by ~5 points and the public page went on showing
    # the old ones for several hours.
    #
    # Not fixed by rendering here: publishing is the forecast's job, this
    # command is re-derivation, and a rebuild that also republished would put
    # a page-write behind an operator command that is safe to run repeatedly.
    # The operator needs to KNOW, which is what this line is for.
    print("\nWrote actuals_cache/actuals.json and track_record.json.")
    print(
        "The published accuracy page is rendered by a forecast run, so it "
        "keeps the old figures until the next scheduled run."
    )
    return 0


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="olw", description="Open Local Weather")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    forecast = sub.add_parser(
        "forecast",
        help=(
            "Forecast for today — a full run if the day has no entry yet, a narrative "
            "re-issue if it has. The verb to schedule."
        ),
    )
    forecast.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    forecast.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    forecast.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR), help="Path to the docs/ (GitHub Pages) directory")
    forecast.add_argument(
        "--public-url",
        default="",
        help="Public GitHub Pages URL. Included in the LLM prompt; also enables GitHub Pages publishing if set.",
    )
    forecast.add_argument(
        "--dry-run", action="store_true", help="Run fetch/verify/LLM for real but skip writes, publish, and email."
    )
    forecast.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-issue even if a forecast went out in the last hour. Forces the NARRATIVE only — "
            "the day's scored predictions are written once and cannot be reached from here."
        ),
    )

    run_daily = sub.add_parser(
        "run-daily",
        help="The day's FIRST run explicitly. Prefer `forecast`, which decides for itself.",
    )
    run_daily.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    run_daily.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    run_daily.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR), help="Path to the docs/ (GitHub Pages) directory")
    run_daily.add_argument(
        "--public-url",
        default="",
        help="Public GitHub Pages URL. Included in the LLM prompt; also enables GitHub Pages publishing if set.",
    )
    run_daily.add_argument(
        "--dry-run", action="store_true", help="Run fetch/verify/LLM for real but skip writes, publish, and email."
    )

    refresh = sub.add_parser(
        "refresh-forecast",
        help="Optional same-day evening refresh — fresher narrative, morning's model_predictions preserved.",
    )
    refresh.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    refresh.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    refresh.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR), help="Path to the docs/ (GitHub Pages) directory")
    refresh.add_argument(
        "--public-url",
        default="",
        help="Public GitHub Pages URL. Included in the LLM prompt; also enables GitHub Pages publishing if set.",
    )
    refresh.add_argument(
        "--dry-run", action="store_true", help="Run fetch/LLM for real but skip writes and publish."
    )

    check_config = sub.add_parser("check-config", help="Load and validate a location.yaml, then exit.")
    check_config.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")

    rebuild = sub.add_parser(
        "rebuild-record",
        help="Re-derive the accuracy record from stored predictions and freshly fetched observations.",
    )
    rebuild.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    rebuild.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    rebuild.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing anything."
    )

    backfill = sub.add_parser(
        "backfill-baselines",
        help="Add persistence/climatology predictions to days stored before they existed.",
    )
    backfill.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    backfill.add_argument(
        "--dry-run", action="store_true", help="Print the plan without writing anything."
    )

    div = sub.add_parser(
        "divergence",
        help="Where the station and the reanalysis disagree, and by how much.",
    )
    div.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")

    rp = sub.add_parser(
        "replay",
        help="Send the frozen prompt vectors through the LLM and keep the outputs.",
    )
    rp.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    rp.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    rp.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR), help="Path to the docs/ directory")
    rp.add_argument("--public-url", default="", help="Public site URL, for the prompt")
    rp.add_argument("--out", required=True, help="Directory to write replay.json into")
    rp.add_argument("--yes", action="store_true", help="Actually spend; without it, only the plan is printed.")

    rpd = sub.add_parser(
        "replay-diff", help="Compare two replay directories and report what moved."
    )
    rpd.add_argument("before")
    rpd.add_argument("after")

    health = sub.add_parser(
        "check-health",
        help="Weekly health checks: model deprecation, repo staleness, data coverage.",
    )
    health.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    health.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")

    args = parser.parse_args(argv)

    if args.command == "check-config":
        cfg = load_location_config(args.config)
        print(f"OK: {cfg.primary_place_name} ({cfg.region_name}), tz={cfg.timezone}")
        return 0

    if args.command == "forecast":
        return _run_forecast(args)

    if args.command == "run-daily":
        return _run_daily(args)

    if args.command == "refresh-forecast":
        return _run_refresh(args)

    if args.command == "rebuild-record":
        return _run_rebuild_record(args)

    if args.command == "divergence":
        return _run_divergence(args)

    if args.command == "replay":
        return _run_replay(args)

    if args.command == "replay-diff":
        return _run_replay_diff(args)

    if args.command == "backfill-baselines":
        return _run_backfill_baselines(args)

    if args.command == "check-health":
        return _run_check_health(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
