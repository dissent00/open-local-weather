"""Command-line entrypoint.

`olw forecast` is what .github/workflows/forecast.yml invokes, and since
ROADMAP item 104 step 4 it is the only verb that issues one — `run-daily`
and `refresh-forecast` are gone, because which run of the day this is was
never the operator's to choose. It reads
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
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from pathlib import Path

from openlocalweather import __version__
from openlocalweather.config import LocationConfig, load_location_config
from openlocalweather.verify.scoring import scored_predictions, verify_closed_windows
from openlocalweather.coverage import (
    OBSERVED_FIELDS,
    actionable,
    actionable_narrated,
    detect_coverage,
    detect_narrated_coverage,
    detect_observation_coverage,
    detect_trigger_regression,
    newly_available,
    observation_status,
)
from openlocalweather.defaults import (
    KNOWN_DUPLICATES,
    LEAD_TIMES_DAYS,
    PROMPT_GROWTH_TRAILING_RUNS,
    REVIEW_MIN_CHECKS_FOR_COMPARISON,
    scored_models,
)
from openlocalweather.dates import add_days, format_date, today_in_tz
from openlocalweather.defaults import WATCHED_COLUMN_LOOKBACK_DAYS
from openlocalweather.fetch import metar as metar_fetch
from openlocalweather.fetch import open_meteo
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
    CAP_STATUS_KEY,
    cap_feed_woke_up,
    check_cap_feed,
    check_known_duplicates,
    check_recent_degradations,
    check_watched_columns,
    check_prompt_growth,
    PromptGrowthStatus,
    check_model_deprecation,
    check_repo_staleness,
)
from openlocalweather.llm.anthropic import DEFAULT_BASE_URL as DEFAULT_ANTHROPIC_BASE_URL
from openlocalweather.llm.anthropic import DEFAULT_MAX_TOKENS as DEFAULT_ANTHROPIC_MAX_TOKENS
from openlocalweather.llm.anthropic import AnthropicProvider
from openlocalweather.llm.gemini import GeminiProvider
from openlocalweather.llm.gemini_interactions import GeminiInteractionsProvider, LLMResponseError
from openlocalweather.llm.fallback import FallbackProvider
from openlocalweather.llm.openai_compat import OpenAICompatProvider
from openlocalweather.llm.provider import (
    CREDENTIAL_FAMILIES,
    DEFAULT_ENV_PREFIXES,
    DEFAULT_LLM_PROVIDER,
    VALID_LLM_PROVIDERS,
)
from openlocalweather.observed import describe_observed_so_far
from openlocalweather.pipeline import (
    ForecastSkipped,
    _station_reports,
    forecast_horizons_of,
    ObservationsRefreshed,
    attach_spend_cap,
    PipelineDeps,
    run_forecast,
)
from openlocalweather.publish.email_gmail import GmailSMTPSender, parse_recipient_list
from openlocalweather.publish.pages import GitHubPagesPublisher
from openlocalweather.review import (
    build_weekly_review,
    compare_early_to_late,
    compare_window_to_calendar,
)
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
from openlocalweather.models import SOURCE_REANALYSIS, SOURCE_STATION, RunDegradation
from openlocalweather.spend import complete_attempt, record_attempt
from openlocalweather.store.log_store import (
    list_log_dates,
    make_log_lookup,
    read_log_entry,
    write_log_entry,
)
from openlocalweather.store.track_record import read_track_record, write_track_record
from openlocalweather.store.health_status import read_health_status, write_health_status
from openlocalweather.verify.pipeline import run_deterministic_verification_and_scoring

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "location.yaml"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"

# The CAP feed is 4 KB and this check must not hold up the rest of the run.
CAP_FEED_TIMEOUT_S = 20

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
# "high" by default for the actual forecast pipeline (forecast,
# refresh-forecast) — measured against the real production prompt, "high"
# vs "low" is a real difference (4,235 vs 739 thinking tokens) and at
# ~45K tokens/call against a 250K-token/run free-tier limit there's ample
# headroom. check-health's model-deprecation check is a simple factual
# lookup, not multi-step reasoning, and intentionally does NOT set this —
# it uses Gemini's own default.
DEFAULT_GEMINI_THINKING_LEVEL = "high"

# DEFAULT_LLM_PROVIDER and VALID_LLM_PROVIDERS live in `llm/provider.py`, with
# the reasoning for what a name in that tuple means. They moved there so
# `config.py` can validate against them without inverting the dependency.


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


@dataclass(frozen=True)
class _ProviderEntry:
    """One link in the chain, with the credentials it reads already decided.

    ROADMAP item 81, 2026-09-22. The chain built on 09-21 walked VENDOR NAMES,
    and a name was wired to one fixed set of environment variables. That is
    what stopped the list being extended: two gateways cannot both be `openai`
    when both would read LLM_BASE_URL and LLM_MODEL.

    `label` is what the operator called this link and what stderr and the
    warnings name. With two `openai` entries, "openai was dropped" does not
    say which one.
    """

    kind: str
    label: str
    env_prefix: str
    fallback_models: tuple[str, ...] = ()

    # This link's own 24-hour ceiling, or None for the deployment's — item
    # 170. A cap belongs to an ACCOUNT: 20 is Google's free calendar-day
    # allowance, OpenRouter's free tier is 50 on separate terms, and one
    # number held both until a failing vendor's retries spent the budget its
    # fallback needed.
    max_calls_per_24h: int | None = None

    def env(self, suffix: str, default: str = "") -> str:
        return _env(f"{self.env_prefix}_{suffix}", default)


def _resolve_provider_entries(
    providers: list, fallback_models: list[str] | None
) -> list[_ProviderEntry]:
    """Normalises a config list of strings and/or mappings into entries.

    A BARE STRING KEEPS EVERY VARIABLE IT HAD. `DEFAULT_ENV_PREFIXES` maps it
    to the prefix the project has always used, so an existing config file
    behaves identically and no deployment has to change to get here — the same
    discipline as the 2026-09-15 list change.

    `fallback_models` is the top-level `llm_fallback_models`, and it reaches
    BARE STRING entries only. A single gateway is the case that field was
    written for; once a chain holds two, a top-level list cannot say which
    one it means, so a mapping entry gets only the order it declares.
    """
    resolved: list[_ProviderEntry] = []
    for raw in providers:
        if isinstance(raw, str):
            kind = raw.strip().lower()
            resolved.append(
                _ProviderEntry(
                    kind=kind,
                    label=kind,
                    env_prefix=DEFAULT_ENV_PREFIXES.get(kind, "LLM"),
                    fallback_models=tuple(fallback_models or ()),
                )
            )
            continue

        entry = raw if isinstance(raw, dict) else raw.model_dump()
        kind = str(entry.get("kind", "")).strip().lower()
        own = entry.get("fallback_models")
        resolved.append(
            _ProviderEntry(
                kind=kind,
                label=str(entry.get("name") or kind).strip().lower(),
                env_prefix=str(
                    entry.get("env_prefix") or DEFAULT_ENV_PREFIXES.get(kind, "LLM")
                ).strip().upper(),
                # A MAPPING ENTRY DOES NOT INHERIT THE TOP-LEVEL LIST, and
                # this is the one line of this change that driving found and
                # the tests did not. `llm_fallback_models` holds OpenRouter
                # model ids; a five-link chain built from the live config on
                # 2026-09-22 handed all three to the `groq` entry, which
                # would have asked Groq for "google/gemma-4-31b-it:free".
                # A bare string still inherits it, which is the deployment
                # shape the field was written for.
                fallback_models=tuple(own or ()),
                max_calls_per_24h=entry.get("max_calls_per_24h"),
            )
        )

    return resolved


def _reject_colliding_entries(entries: list[_ProviderEntry]) -> None:
    """Two links that would read each other's credentials, or the same link twice.

    Both are always config mistakes and both were silent. Sharing a prefix is
    legitimate for ONE case and the map is what says so: `gemini` and
    `gemini-interactions` are one key reaching two APIs.
    """
    seen: dict[tuple[str, str], str] = {}
    for entry in entries:
        key = (entry.kind, entry.env_prefix)
        if key in seen:
            raise SystemExit(
                f"llm_providers names {entry.kind!r} twice reading the same "
                f"{entry.env_prefix}_* variables — the second link would be "
                f"identical to the first. Give one its own env_prefix, or "
                f"remove it."
            )
        seen[key] = entry.label

    by_prefix: dict[str, list[_ProviderEntry]] = {}
    for entry in entries:
        by_prefix.setdefault(entry.env_prefix, []).append(entry)

    for prefix, sharing in by_prefix.items():
        families = {CREDENTIAL_FAMILIES.get(e.kind, e.kind) for e in sharing}
        if len(families) > 1:
            names = ", ".join(sorted(e.kind for e in sharing))
            raise SystemExit(
                f"llm_providers entries {names} would all read {prefix}_API_KEY "
                f"and {prefix}_MODEL, so at least one would be built with "
                f"another's credentials and fail at call time, after the "
                f"attempt was spent. Give each its own env_prefix."
            )


def _build_llm_provider(
    *,
    thinking_level: str | None = None,
    providers: list[str] | None = None,
    fallback_models: list[str] | None = None,
):
    """Builds the configured LLMProvider.

    Secrets and endpoints come from env vars, never CLI args, for the same
    reason as everything else here — they'd otherwise land in shell history
    and process listings. `thinking_level` is Gemini-specific and simply
    ignored by other providers; check-health passes None because a factual
    model-lookup doesn't need extended reasoning.

    WHICH provider, though, comes from `config/location.yaml` — `providers`
    is `location.llm_providers` — and the environment only OVERRIDES it.
    That order is the point of the change on 2026-09-15.

    The name used to live in a code constant that nothing set, so the live
    deployment ran on the default and switching it meant either editing a
    constant or defining a repository variable in a web UI. Neither is where
    an operator looks, and neither leaves a reviewable trace: a variable
    changed in a browser has no diff, no commit message and no history. The
    provider is now a line in the deployment's own config, beside the call cap
    it is inseparable from.

    The environment override stays because it is genuinely useful and costs
    nothing: `LLM_PROVIDER=gemini-interactions ... tools/rerender_narrative.py`
    is how a one-off runs against a different provider without editing a
    committed file, which is exactly how this endpoint was first driven.
    """
    # ONE NAME, OR AN ORDER — ROADMAP item 81, 2026-09-21.
    #
    # The environment override still selects exactly ONE provider, because
    # that is what it is for: `LLM_PROVIDER=gemini-interactions ...
    # tools/rerender_narrative.py` is a one-off against a named endpoint, and
    # a one-off that quietly fell through to a second vendor would report the
    # wrong thing about the first.
    #
    # A LIST FROM CONFIG IS A CHAIN. `config.py` has carried this field as a
    # list since 2026-09-15 with a validator saying only the first entry was
    # used; that validator's warning is what this removes.
    override = _env("LLM_PROVIDER")
    entries = _resolve_provider_entries(
        [override.lower()] if override else list(providers or [DEFAULT_LLM_PROVIDER]),
        fallback_models,
    )
    names = [entry.kind for entry in entries]

    # A NAME NOTHING CAN BUILD IS ALWAYS FATAL, checked before anything is
    # attempted. `_build_one_llm_provider` signals both "unknown name" and
    # "keys absent" with SystemExit, and the loop below treats SystemExit in a
    # chain as "this deployment does not hold that vendor" — which is right
    # for a missing key and badly wrong for a typo, since a misspelt entry
    # would be dropped in silence and the deployment would run on a shorter
    # chain than its config names. `config.py` validates the names it loads,
    # so this guards the callers that bypass it.
    unknown = [name for name in names if name not in VALID_LLM_PROVIDERS]
    if unknown:
        raise SystemExit(
            f"Unknown LLM provider(s) {unknown} — expected one of "
            f"{', '.join(VALID_LLM_PROVIDERS)}."
        )

    # CHECKED BEFORE ANYTHING IS BUILT, for the same reason the name check is:
    # a collision produces a provider that looks configured and fails on the
    # call, which is the most expensive place to find out.
    _reject_colliding_entries(entries)

    built = []
    unavailable: list[str] = []
    for entry in entries:
        try:
            link = _build_one_llm_provider(entry, thinking_level=thinking_level)
            # ITS OWN CEILING, marked here rather than passed to the provider
            # — item 170. The cap hook resolves whichever link is live and
            # reads this off it, so a number in location.yaml reaches
            # enforcement without threading through a pipeline that does not
            # know what a chain entry is. An attribute because the provider
            # classes are `olw_core`'s, shared verbatim with the app, and a
            # ceiling is a deployment's concern rather than a provider's.
            if entry.max_calls_per_24h is not None:
                link.max_calls_per_24h = entry.max_calls_per_24h
            built.append(link)
        except SystemExit as e:
            # A SINGLE NAME KEEPS ITS OLD BEHAVIOUR EXACTLY: the message that
            # names the missing variable, and a non-zero exit. Nothing about a
            # one-provider deployment changes, which is most of them.
            if len(entries) == 1:
                raise
            # In a CHAIN a missing key means "this deployment does not have
            # that vendor", which is the operator's own answer to "if the user
            # has the right api keys" — not an error. Collected rather than
            # printed here so the order of the report follows the order of the
            # chain even when the first entry is the one that is missing.
            unavailable.append(f"{entry.label}: {e}")

    if not built:
        raise SystemExit(
            "No LLM provider could be built from llm_providers "
            # THE LABELS, not the kinds: a chain of three gateways is three
            # entries of kind `openai`, and ['openai', 'openai', 'openai']
            # names none of them.
            f"{[entry.label for entry in entries]}. Each was unavailable:\n  "
            + "\n  ".join(unavailable)
        )

    for reason in unavailable:
        print(
            f"WARNING: configured LLM provider is unavailable and was dropped "
            f"from the chain — {reason}",
            file=sys.stderr,
        )

    if len(built) == 1:
        return built[0]

    # SAID OUT LOUD, every run. A deployment served by its second choice looks
    # exactly like one served by its first, and which it was is the whole
    # reliability question this chain exists to answer.
    print(
        "LLM fallback chain: "
        + " -> ".join(
            f"{type(p).__name__}({getattr(p, 'model', 'unknown')})" for p in built
        ),
        file=sys.stderr,
    )
    return FallbackProvider(built)


def _build_one_llm_provider(
    entry: _ProviderEntry,
    *,
    thinking_level: str | None = None,
):
    """One provider by name, raising SystemExit when its keys are absent.

    Split out of `_build_llm_provider` for the chain: the caller decides
    whether a missing key ends the run or simply drops that entry, and this
    stays the single place that knows which environment variable each vendor
    needs.
    """
    if entry.kind == "gemini":
        api_key = entry.env("API_KEY")
        if not api_key:
            raise SystemExit(
                f"{entry.env_prefix}_API_KEY environment variable is required "
                f"(llm_providers entry {entry.label!r})."
            )
        return GeminiProvider(
            api_key=api_key,
            model=entry.env("MODEL", DEFAULT_GEMINI_MODEL),
            thinking_level=thinking_level,
        )

    if entry.kind == "gemini-interactions":
        api_key = entry.env("API_KEY")
        if not api_key:
            raise SystemExit(
                f"{entry.env_prefix}_API_KEY environment variable is required "
                f"(llm_providers entry {entry.label!r})."
            )
        # NO `thinking_level`. It is a `generationConfig` field on
        # `generateContent`; whether this API takes an equivalent is unmeasured,
        # and passing one that is silently ignored would be worse than not
        # passing it — the run would look configured and behave otherwise.
        return GeminiInteractionsProvider(
            api_key=api_key,
            model=entry.env("MODEL", DEFAULT_GEMINI_MODEL),
        )

    if entry.kind == "anthropic":
        api_key = entry.env("API_KEY")
        model = entry.env("MODEL")
        if not api_key or not model:
            raise SystemExit(
                f"llm_providers entry {entry.label!r} (anthropic) requires "
                f"{entry.env_prefix}_API_KEY and {entry.env_prefix}_MODEL to be set. "
                "See QUICKSTART.md for recommended model ids."
            )
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            # Only needed for a proxy/gateway; defaults to api.anthropic.com.
            base_url=entry.env("BASE_URL", DEFAULT_ANTHROPIC_BASE_URL),
            max_tokens=int(entry.env("MAX_TOKENS", str(DEFAULT_ANTHROPIC_MAX_TOKENS))),
        )

    if entry.kind == "openai":
        base_url = entry.env("BASE_URL")
        model = entry.env("MODEL")
        if not base_url or not model:
            raise SystemExit(
                f"llm_providers entry {entry.label!r} (openai) requires "
                f"{entry.env_prefix}_BASE_URL and {entry.env_prefix}_MODEL to be set "
                f"(plus {entry.env_prefix}_API_KEY for any hosted endpoint). "
                "See QUICKSTART.md for per-service values."
            )
        # The gateway's OWN fallback list — item 81. `LLM_FALLBACK_MODELS`
        # overrides the config for a one-off, the same way LLM_PROVIDER does.
        # `require_parameters` travels with it because a list of models is
        # only as good as the routing behind it: without it OpenRouter may
        # serve one through an upstream that treats the JSON schema as a hint,
        # and the call is paid for and then fails validation.
        configured = entry.env("FALLBACK_MODELS")
        models = (
            [m.strip() for m in configured.split(",") if m.strip()]
            if configured
            else list(entry.fallback_models)
        )
        return OpenAICompatProvider(
            # Empty is legitimate here: local runtimes like Ollama don't
            # need a key. Hosted endpoints will fail loudly on the first
            # call, which is clearer than guessing at intent up front.
            api_key=entry.env("API_KEY"),
            model=model,
            base_url=base_url,
            json_mode=entry.env("JSON_MODE", "json_schema"),
            fallback_models=models,
            require_parameters=bool(models),
        )

    raise SystemExit(
        f"Unknown LLM provider kind {entry.kind!r} — expected one of {', '.join(VALID_LLM_PROVIDERS)}."
    )


def _build_pages_publisher(
    location: LocationConfig, data_path: Path, docs_dir: str, public_webpage_url: str
) -> GitHubPagesPublisher | None:
    """The publisher, or None when no absolute base URL was given.

    Publisher needs an absolute base URL to build sane nav links (see
    publish/pages.py's module docstring) — skip it gracefully rather than
    publish broken-relative-link pages if none was given, same "None = skip"
    pattern pipeline.py already uses for publisher/email_sender.

    EXTRACTED 2026-09-15 so a repair tool can republish a stored day without
    running a forecast. There is no `olw` command that re-renders pages from
    an entry already on disk — pages are written as a side effect of the
    forecast run — so `tools/rerender_narrative.py` had repaired the log and
    then told the operator to run a command that does not exist. Rebuilding
    the pages needs exactly this object and nothing else from the pipeline,
    and duplicating its five providers in a tool is how the two drift.
    """
    if not public_webpage_url:
        return None

    return GitHubPagesPublisher(
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
            # The same stored horizons the row labels are written from.
            forecast_horizons=forecast_horizons_of(read_track_record(data_path).entries),
        ),
    )


def _build_pipeline_deps(config_path: str, data_dir: str, docs_dir: str, public_webpage_url: str) -> PipelineDeps:
    location = load_location_config(config_path)
    data_path = Path(data_dir)

    gemini_thinking_level = _env("GEMINI_THINKING_LEVEL", DEFAULT_GEMINI_THINKING_LEVEL) or None
    llm_provider = _build_llm_provider(
        thinking_level=gemini_thinking_level,
        providers=location.llm_providers,
        fallback_models=location.llm_fallback_models,
    )
    waqi_token = _env("WAQI_TOKEN")

    publisher = _build_pages_publisher(location, data_path, docs_dir, public_webpage_url)

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


# What kind of run this turned out to be, on its own line and first.
#
# A contract, not decoration: .github/workflows/forecast.yml greps for these
# to pick a commit subject, because one workflow produces several kinds and
# one subject for all of them would flatten the archive's own history.
# Change the strings and change the workflow with them —
# `test_workflows.py` now enforces that rather than leaving it to this
# sentence, which is how "reissue" survived the concept it named.
#
# ONE KIND FOR EVERY FORECAST — ROADMAP item 137, the operator's decision
# 2026-09-16: "every run is a fresh forecast". There were two, `first` and
# `reissue`, and the workflow mapped the second to the commit subject
# "forecast refresh". Both words were dead: `run_refresh_pipeline` no longer
# exists, and a later run with new model guidance does the full job rather
# than something lesser.
#
# WHAT WAS DROPPED WAS THE KIND, NOT THE FACT. Whether this was the day's
# first issuance is still real and still used — `ForecastRunResult.
# first_issuance` drives `verification_already_written` and which summary is
# printed below. It is a fact about ORDER, not about kind, and this field is
# called run-KIND. A reader who needs the ordering reads the timestamps, as
# the spend ledger does.
RUN_KIND_FORECAST = "run-kind: forecast"
RUN_KIND_SKIPPED = "run-kind: skipped"
# A run that refreshed what the station has seen and reasoned nothing —
# ROADMAP item 121. The outcome an hourly cron should mostly produce, and the
# one that costs no LLM call.
RUN_KIND_OBSERVED = "run-kind: observed"


def _print_first_issuance(result, dry_run: bool) -> None:
    entry = result.log_entry
    print(f"Forecast issued for {result.today} (dry_run={dry_run}).")
    print(f"  rain_expected:   {entry.rain_expected}")
    print(f"  temp:            {entry.temp_high_low_display}")
    print(f"  synoptic:        {entry.synoptic_pattern}")
    print(f"  newly_verified:  {result.newly_verified}")
    print(f"  published:       {result.published}")
    print(f"  emailed:         {result.emailed}")
    if dry_run:
        print("\n--- narrative preview (not written to data/, nothing published/emailed) ---\n")
        print(entry.narrative_markdown)


def _print_later_issuance(result, dry_run: bool) -> None:
    entry = result.log_entry
    print(f"Forecast issued again for {result.today} (dry_run={dry_run}).")
    print(f"  rain_expected:   {entry.rain_expected}")
    print(f"  temp:            {entry.temp_high_low_display}")
    print(f"  synoptic:        {entry.synoptic_pattern}")
    print(f"  refreshed_at:    {entry.meta.refreshed_at}")
    print(f"  published:       {result.published}")
    if dry_run:
        print("\n--- narrative preview (not written to data/, nothing published) ---\n")
        print(entry.narrative_markdown)


def _print_observations_refreshed(result, dry_run: bool) -> None:
    entry = result.log_entry
    print(f"Observations refreshed for {result.today} (dry_run={dry_run}).")
    print(f"  as of:           {entry.meta.observations_local_time}")
    print(f"  forecast issued: {entry.meta.issued_local_time}")
    # Composed through the same function the page and the prompt use, so what
    # an operator reads here is what a reader gets rather than a second
    # wording of it.
    print(f"  observed:        {describe_observed_so_far(entry.observed_so_far, as_of=entry.meta.observations_local_time)}")
    print(f"  published:       {result.published}")


def _run_prompt_size(args: argparse.Namespace) -> int:
    """The prompt, sized per block over the archive — ROADMAP item 148, step 1.

    An analysis verb, read-only. Item 134 measured that the prompt grew 10%
    in ten days and "nothing noticed", and items 134 and 147 each rebuilt the
    per-block table by hand; this is that table as a query. It re-derives
    every issuance from the archived user prompt with the same rule the
    pipeline stores under `meta.prompt_size`, and checks the two agree for
    the latest run, so a change to the rule cannot silently re-base the
    series.
    """
    from openlocalweather.llm.prompt_size import prompt_block_sizes
    from openlocalweather.store.prompt_archive import list_archived_dates, read_prompt_archive

    issuances: list[tuple[str, dict[str, int]]] = []
    for d in list_archived_dates(args.data_dir):
        for issuance in read_prompt_archive(args.data_dir, d):
            issuances.append((issuance["issued_at"], prompt_block_sizes(issuance["user_prompt"])))

    if not issuances:
        print("No archived prompts under data/prompts/.")
        return 0

    shown = issuances[-args.last:]
    print(f"{'issued_at':27} {'user chars':>11} {'delta':>8}")
    previous_total = None
    for issued_at, sizes in shown:
        total = sum(v for k, v in sizes.items() if "/" not in k)
        delta = "" if previous_total is None else f"{total - previous_total:+d}"
        print(f"{issued_at:27} {total:>11,} {delta:>8}")
        previous_total = total

    latest_at, latest = issuances[-1]
    before = issuances[-2][1] if len(issuances) > 1 else {}
    print()
    print(f"Blocks of the latest issuance ({latest_at}), delta against the one before:")
    print(f"{'block':52} {'chars':>8} {'delta':>8}")
    for name, chars in sorted(latest.items(), key=lambda kv: -kv[1]):
        delta = f"{chars - before[name]:+d}" if name in before else "new" if before else ""
        print(f"{name:52} {chars:>8,} {delta:>8}")
    for name in before:
        if name not in latest:
            print(f"{name:52} {'-':>8} {'gone':>8}")

    # The stored figure against the re-derived one, for the same run.
    dates = list_log_dates(args.data_dir)
    entry = make_log_lookup(args.data_dir)(dates[-1]) if dates else None
    stored = entry.meta.prompt_size if entry is not None else None
    print()
    if stored is None:
        print("Stored size: none — the latest entry predates item 148 step 1.")
        return 0
    if stored.blocks == latest and stored.user_prompt_chars == sum(
        v for k, v in latest.items() if "/" not in k
    ):
        print("Stored size matches the archive re-derivation for the latest run.")
        return 0

    print("STORED SIZE DISAGREES with the archive re-derivation for the latest run:")
    for name in sorted(set(stored.blocks) | set(latest)):
        if stored.blocks.get(name) != latest.get(name):
            print(f"  {name}: stored {stored.blocks.get(name)} vs re-derived {latest.get(name)}")
    return 1


def _run_window_vs_day(args: argparse.Namespace) -> int:
    """Both Day+0 series, on the days that carry both — ROADMAP item 104's
    contract items 2 and 3.

    An analysis verb rather than anything the pipeline runs, in the same
    spirit as `divergence`: it exists so a decision the operator has to take —
    what happens to the calendar series when the window replaces it — is taken
    against a measurement instead of against the argument for the reframe.
    """
    location = load_location_config(args.config)
    cache = read_actuals_cache(args.data_dir)
    rows = compare_window_to_calendar(
        make_log_lookup(args.data_dir),
        as_date_dict(cache.primary),
        list_log_dates(args.data_dir),
        models=scored_models(location.local_bulletin_model_id),
    )

    paired = max((r.paired_checks for r in rows), default=0)
    if not paired:
        print("No day yet carries both a scored window and a scorable Day+0.")
        print(
            "A window is scorable only once every hour it covers lies on a finished "
            "day — see verify.scoring.window_is_scorable."
        )
        return 0

    print(f"{'model':16} {'paired':>7} {'rain cal/win':>13} {'high cal/win':>15} {'cloud cal/win':>17}")
    for r in rows:
        if not r.paired_checks:
            continue

        def _pair(a, b, fmt="{:+.1f}"):
            left = fmt.format(a) if a is not None else "-"
            right = fmt.format(b) if b is not None else "-"
            return f"{left} / {right}"

        print(
            f"{r.model:16} {r.paired_checks:>7} "
            f"{f'{r.calendar_rain_correct}/{r.window_rain_correct}':>13} "
            f"{_pair(r.calendar_high_error_c, r.window_high_error_c):>15} "
            f"{_pair(r.calendar_cloud_error_pct, r.window_cloud_error_pct):>17}"
        )

    print()
    print("Errors are observed minus forecast. The two sides are scored against")
    print("DIFFERENT observations on purpose: the calendar claim against the calendar")
    print("day, the window claim against the 24 hours it actually covered.")
    return 0


def _run_early_vs_late(args: argparse.Namespace) -> int:
    """The day's opening window call against its last — ROADMAP item 104.

    The question the reframe exists to make askable, and it is NOT a
    window-versus-calendar one: the calendar series holds a single row per
    day, so a late issuance has no calendar counterpart at all. See
    `olw window-vs-day` for the other half, and note that one measures the
    case where the reframe matters LEAST.
    """
    location = load_location_config(args.config)
    rows = compare_early_to_late(
        make_log_lookup(args.data_dir),
        list_log_dates(args.data_dir),
        models=scored_models(location.local_bulletin_model_id),
    )

    if not max((r.paired_days for r in rows), default=0):
        print("No day yet carries two scored windows.")
        print(
            "Each needs every hour it covers to lie on a finished day, so a "
            "day's pair lands about 48 hours after its later issuance."
        )
        return 0

    print(f"{'model':16} {'days':>5} {'rain early/late':>16} {'high early/late':>18} {'cloud early/late':>20}")
    for r in rows:
        if not r.paired_days:
            continue

        def _pair(a, b):
            left = f"{a:+.1f}" if a is not None else "-"
            right = f"{b:+.1f}" if b is not None else "-"
            return f"{left} / {right}"

        print(
            f"{r.model:16} {r.paired_days:>5} "
            f"{f'{r.early_rain_correct}/{r.late_rain_correct}':>16} "
            f"{_pair(r.early_high_error_c, r.late_high_error_c):>18} "
            f"{_pair(r.early_cloud_error_pct, r.late_cloud_error_pct):>20}"
        )

    print()
    print("Errors are observed minus forecast. The two cover DIFFERENT 24 hours —")
    print("that is the point, not a flaw: the later call's whole advantage is that")
    print("its period starts later. On one day a difference may be weather; across")
    print("days what remains is whether newer guidance verifies better.")
    return 0


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

    # Nothing was reasoned, so there is no forecast to print — only what the
    # station has seen since the standing one.
    if isinstance(result, ObservationsRefreshed):
        print(RUN_KIND_OBSERVED)
        _print_observations_refreshed(result, dry_run=args.dry_run)
        return 0

    # The RUN's own answer, not its type's — see pipeline.ForecastRunResult.
    print(RUN_KIND_FORECAST)

    # The SUMMARY still differs — a later run has a standing forecast to
    # describe itself against and the first does not. That is a difference in
    # what there is to say, not in what kind of run it was.
    if result.first_issuance:
        _print_first_issuance(result, args.dry_run)
    else:
        _print_later_issuance(result, args.dry_run)
    return 0


def _watched_observation_points(location: LocationConfig, cache) -> list[tuple[str, dict, dict]]:
    """Which sources each point of the actuals cache should be held to.

    The station's fields are watched only where a station is configured, and
    only on the primary point — the secondary point has none, so its station
    fields are absent by construction and asking would add six permanent
    count lines (measured 2026-09-17).
    """
    reanalysis = {SOURCE_REANALYSIS: OBSERVED_FIELDS[SOURCE_REANALYSIS]}
    primary = dict(reanalysis)
    if location.metar_station_icao:
        primary[SOURCE_STATION] = OBSERVED_FIELDS[SOURCE_STATION]

    points = [("primary", as_date_dict(cache.primary), primary)]
    if location.secondary_point.enabled:
        points.append(("secondary", as_date_dict(cache.secondary), reanalysis))

    return points


def _recent_prompt_growth(data_dir: str) -> list[tuple[str, float | None]]:
    """Each recent day's first issuance against its own trailing median,
    newest first, derived from the prompt archive — ROADMAP item 148, step 2.

    From the archive rather than from the entries' stored figure, because an
    entry's meta is the LATEST issuance's and a re-issue would hide the
    morning that grew; the stored figure is the run's own record, and
    `olw prompt-size` checks the two rules agree.
    """
    from openlocalweather.llm.prompt_size import prompt_growth
    from openlocalweather.store.prompt_archive import (
        first_issuance_prompts,
        list_archived_dates,
        read_prompt_archive,
    )

    out: list[tuple[str, float | None]] = []
    for d in sorted(list_archived_dates(data_dir), reverse=True)[:PROMPT_GROWTH_TRAILING_RUNS]:
        issuances = read_prompt_archive(data_dir, d)
        if not issuances:
            continue
        trailing = first_issuance_prompts(data_dir, before=d, limit=PROMPT_GROWTH_TRAILING_RUNS)
        growth = prompt_growth(len(issuances[0]["user_prompt"]), [len(p) for p in trailing])
        out.append((format_date(d), None if growth is None else growth.growth_pct))
    return out


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


# How far back the duplicate re-check reads — the same month the coverage
# watcher uses, so one stretch of the record answers both.
DUPLICATE_LOOKBACK_DAYS = 30


def _recent_probabilities(
    data_dir: str, days: int
) -> list[tuple[str, int, str, str, float | None]]:
    """Every stored row's rain probability per model and lead over the last
    `days` log days, as check_known_duplicates wants them. Every issuance of
    a day counts, keyed by the issuance's own date and lead."""
    path = Path(data_dir)
    out: list[tuple[str, int, str, str, float | None]] = []
    for d in sorted(list_log_dates(path), reverse=True)[:days]:
        entry = read_log_entry(path, d)
        if entry is None:
            continue
        for i, row in enumerate(entry.prediction_rows):
            for lead in (0, 3, 7):
                for p in row.predictions.for_lead(lead):
                    out.append((f"{d}#{i}", lead, p.model, "rain_probability_pct", p.rain_probability_pct))
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
def _run_check_health(args: argparse.Namespace) -> int:
    # Loaded before the provider is built, not after: since 2026-09-15 the
    # config names the provider, so building first would have quietly used
    # the default while the deployment asked for something else.
    location = load_location_config(args.config)
    # No thinking_level: the deprecation check is a factual lookup, not the
    # multi-step reasoning the forecast pipeline asks for.
    llm = _build_llm_provider(
        thinking_level=None,
        providers=location.llm_providers,
        fallback_models=location.llm_fallback_models,
    )
    # Counted like any other call, through the SAME function the pipeline
    # and the replay use. This had its own near-copy until 2026-09-10, which
    # silently omitted the fail-closed check and the shout when a provider
    # ignores the hook. The config is loaded here rather than further down because the
    # cap's size lives in it, and a hook attached after the call would be no
    # hook at all.
    #
    # A refusal is caught below and reported as a skipped check, which is the
    # right outcome: being out of budget is a real answer, not a reason to
    # spend anyway.
    attach_spend_cap(
        llm,
        Path(args.data_dir),
        max_calls=location.max_llm_calls_per_24h,
        purpose="health-check",
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
    # ROADMAP item 152 step 2. Not a failure and not filtered by the
    # acknowledgements: a source supplying something it did not is news for
    # a person, and an acknowledged gap closing is the case that most needs
    # one — the config entry describing it is now wrong.
    arrivals = newly_available(findings, location.acknowledged_coverage_gaps)
    if arrivals:
        print(f"  NOTICE: {len(arrivals)} variable(s) started arriving:")
        for f in arrivals:
            print(f"    - {f.message}")
    inert = len(findings) - len(needs_attention) - len(arrivals)
    if inert:
        print(f"  ({inert} known or universal gap(s) not reported — see acknowledged_coverage_gaps.)")

    # The other half of coverage: the fields the FORECASTER writes, which fail
    # differently and were watched by nothing. A display value that stops
    # arriving renders as no tile rather than as an error — see ROADMAP
    # item 102, where the Air Quality tile left the page for three days.
    print("Checking the fields the forecaster writes...")
    narrated = detect_narrated_coverage(
        make_log_lookup(data_path), today_in_tz(location.timezone)
    )
    narrated_attention = actionable_narrated(narrated)
    if narrated_attention:
        # Does NOT fail the check, for the same reason the data-coverage
        # findings above do not: the forecast is produced and correct, and a
        # missing display string is recorded as unknown rather than wrong.
        # Flip this to `ok = False` if a vanished tile should be a red run.
        print(f"  {len(narrated_attention)} item(s) worth checking:")
        for f in narrated_attention:
            print(f"    - {f.message}")
    else:
        print("  OK — every watched field is still being supplied.")
    never = len(narrated) - len(narrated_attention)
    if never:
        print(f"  ({never} field(s) the forecaster has never once supplied.)")

    # ROADMAP item 152 step 3: the OBSERVATION side, which had no watcher.
    # Read once here and written once below, on every path, because two
    # sections now keep memory in it and a section writing only its own key
    # would erase the other's.
    status = read_health_status(args.data_dir)
    print("Checking coverage of the observed record...")
    observed = []
    today = today_in_tz(location.timezone)
    for point, bucket, sources in _watched_observation_points(location, read_actuals_cache(data_path)):
        observed += detect_observation_coverage(
            bucket, point=point, sources=sources, today=today, remembered=status,
        )
        status.update(observation_status(
            bucket, point=point, sources=sources, today=today, remembered=status,
        ))
    lost = [f for f in observed if f.kind == "regression"]
    back = [f for f in observed if f.kind == "became_available"]
    if lost:
        # FAILS THE CHECK, unlike a model variable going missing. One model
        # losing one field degrades one row of the record; an observation
        # field going missing stops that field being scored for EVERY model,
        # and item 151 showed this side failing silently for a fortnight.
        print(f"  WARNING: {len(lost)} observed field(s) stopped arriving:")
        for f in lost:
            print(f"    - {f.message}")
        ok = False
    else:
        print("  OK — every watched observation is still being recorded.")
    if back:
        print(f"  NOTICE: {len(back)} observed field(s) started arriving:")
        for f in back:
            print(f"    - {f.message}")
    unpublished = len(observed) - len(lost) - len(back)
    if unpublished:
        print(f"  ({unpublished} observed field(s) no check has ever seen supplied.)")

    # ROADMAP item 148 step 2. A NOTICE and never a failure, operator's
    # decision 2026-09-17: the prompt growing is a decision nobody took,
    # not a broken run, and nothing refuses a run on it (step 3).
    print("Checking whether the prompt grew...")
    growth = check_prompt_growth(_recent_prompt_growth(args.data_dir))
    prefix = {
        PromptGrowthStatus.GREW: "NOTICE: ",
        PromptGrowthStatus.STEADY: "OK — ",
    }.get(growth.status, "")
    print(f"  {prefix}{growth.message}")

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

    # ROADMAP item 152. Has a column this project decided to ignore started
    # arriving? The exclusions in fetch/metar.py were measured and correct
    # when taken, and they removed the evidence that could overturn them — a
    # column nobody requests produces no series. This is the only place that
    # asks, and it asks on its own request so the daily parse, which is
    # positional, is untouched.
    #
    # NOT A FAILURE EITHER WAY. A column that started arriving is news for a
    # person to act on, not a broken pipeline, so it never sets `ok = False`.
    if location.metar_station_icao:
        print("Checking columns this project excluded...")
        try:
            watched_rows = metar_fetch.fetch_metar_archive_rows(
                location.metar_station_icao,
                add_days(today_in_tz(location.timezone), -WATCHED_COLUMN_LOOKBACK_DAYS),
                today_in_tz(location.timezone),
                extra_columns=metar_fetch.ARCHIVE_WATCHED_COLUMNS,
            )
        except metar_fetch.ArchiveUnavailable as e:
            watched_rows = None
            print(f"  Station archive gave no usable answer ({e}); no column check this run.")
        if watched_rows is None:
            print("  No station rows; no column check this run.")
        else:
            watched = check_watched_columns(
                watched_rows,
                watched=metar_fetch.ARCHIVE_WATCHED_COLUMNS,
                read_column_count=len(metar_fetch.ARCHIVE_DATA_COLUMNS),
            )
            print(f"  {'NOTICE: ' if watched.changed else 'OK — '}{watched.message}")

    # ROADMAP item 2. Two questions, and only one of them is a failure: is the
    # warning feed still answering, and has it said anything lately?
    # ROADMAP item 158 step 4. Read from the stored rows, so no fetch and
    # no dependence on today's guidance; quiet while the pair still matches.
    print("Checking known duplicate fields on the stored rows...")
    duplicates = check_known_duplicates(
        _recent_probabilities(args.data_dir, DUPLICATE_LOOKBACK_DAYS), KNOWN_DUPLICATES
    )
    print(f"  {'NOTICE: ' if duplicates.changed else 'OK — '}{duplicates.message}")

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

        # What the last run saw, so a CHANGE can be reported and not just a
        # state. See store/health_status.py for why this check needed memory.
        previous = status.get(CAP_STATUS_KEY)
        woke_up = cap_feed_woke_up(previous, cap.status)

        if cap.status is CapFeedStatus.UNREACHABLE:
            # A quiet feed may be correct; a feed that stopped answering has
            # moved or died, and nothing else here would notice until an alert
            # was missed.
            print(f"  WARNING: {cap.message}")
            ok = False
        elif woke_up:
            # A red job carrying good news, which is unusual and is the point.
            # This deployment has exactly one channel that reaches a person —
            # a failing job — and ROADMAP item 2 is gated on precisely this
            # event: the feed that has been silent since May is issuing again,
            # so the alerting work it was holding can start.
            print(f"  WAKE-UP: {cap.message}")
            print(
                "  The CAP feed was last seen "
                f"{previous} and is now carrying current alerts. ROADMAP item 2 "
                "is gated on this — the alert path can be built against a feed "
                "that is demonstrably live. This failure is the notification, "
                "and it will not repeat: the new status is recorded below."
            )
            ok = False
        elif cap.status is CapFeedStatus.FRESH:
            print(f"  OK — {cap.message}")
        else:
            print(f"  {cap.message}")

        status[CAP_STATUS_KEY] = cap.status.value

    # Recorded on EVERY path, including the ones that just failed the run.
    # An alarm that fires on a transition must record the new state, or the
    # same transition is re-detected every week and a signal meant to be
    # seen once becomes a weekly red job nobody reads.
    write_health_status(args.data_dir, status)

    days = _days_since_last_commit()
    print(f"Days since last commit: {days}")
    if check_repo_staleness(days):
        print(
            f"  WARNING: last commit was {days} days ago. GitHub auto-disables scheduled "
            "workflows after 60 days of repo inactivity — investigate why forecast.yml "
            "isn't "
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

    # _build_pipeline_deps, NOT a bare provider — so the replay runs with the
    # SAME thinking_level the forecast uses. Measured 2026-09-03: a hand-rolled
    # replay passed thinking_level=None, which is a different model
    # configuration and therefore a different thing being measured. A harness
    # whose conditions differ from production answers a question nobody asked.
    deps = _build_pipeline_deps(args.config, args.data_dir, args.docs_dir, args.public_url)

    # AND COUNTED, which this said it was and was not until 2026-09-10.
    #
    # The docstring above has promised since item 27 that a replay is "counted
    # by the same spend cap the forecast uses", and the line printed a few
    # lines up tells the operator the same thing. Neither was true: building
    # deps and calling run_replay reaches the provider without ever passing
    # through attach_spend_cap, so nothing recorded, nothing counted, and
    # assert_capacity never asked whether there was budget left.
    #
    # Measured on the run that found it — six cases, eight requests once two
    # 503s had retried, and not one row in the ledger. The failure mode is not
    # the missing rows: it is that the most expensive command here could run
    # with the cap already spent and leave the morning forecast to be refused.
    #
    # `purpose="replay"` rather than "forecast" so a later read can tell a
    # harness run from the forecast it was meant to be compared against.
    verify_spend, _ = attach_spend_cap(
        deps.llm_provider,
        deps.data_dir,
        max_calls=deps.location.max_llm_calls_per_24h,
        purpose="replay",
    )

    results, failures = replay.run_replay(deps.llm_provider, cases)
    verify_spend()

    out = Path(args.out)
    # Written even when some cases failed: every case is a paid call, and
    # discarding what succeeded because a later one did not is the one thing
    # this must never do.
    replay.write_replay(out, results)

    print(f"\nWrote {len(results)} result(s) to {out}/replay.json")
    for r in results:
        print(f"  {r.case[:52]:54s} {r.latency_s:5.1f}s")

    if failures:
        print(f"\n{len(failures)} case(s) FAILED and are not in the file:")
        for f in failures:
            print(f"  {f.case[:52]:54s} {f.error[:90]}")
        print(
            "\nA diff against this run covers only the cases above that "
            "succeeded. Treat it as partial, or it becomes a claim about "
            "comparisons that never happened."
        )
        return 1

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
                for p in scored_predictions(entry).day0
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


def _rescore_windows(location, data_dir, log_dates, today, *, dry_run: bool) -> list:
    """Every scorable window on every entry, rescored; returns the entries
    that changed. Prints the per-model rain verdicts that moved, so a rescore
    is read rather than trusted."""
    if not log_dates:
        return []
    try:
        archive = open_meteo.fetch_archive_range(
            location.primary_point.lat, location.primary_point.lon,
            min(log_dates), add_days(today, -1), location.timezone,
        )
    except Exception as e:  # noqa: BLE001
        print(f"\nWindows not rescored: archive unavailable ({e}).", file=sys.stderr)
        return []
    station_reports = _station_reports(location, min(log_dates), add_days(today, -1), data_dir)

    changed = []
    lookup = make_log_lookup(data_dir)
    for d in log_dates:
        entry = lookup(d)
        if entry is None or not entry.prediction_rows:
            continue
        before = {
            (i, m): sc.rain_correct
            for i, row in enumerate(entry.prediction_rows)
            for m, sc in row.window_scores.items()
        }
        if not verify_closed_windows(
            entry, archive, today=today,
            station_reports=station_reports, timezone_name=location.timezone, force=True,
        ):
            continue
        after = {
            (i, m): sc.rain_correct
            for i, row in enumerate(entry.prediction_rows)
            for m, sc in row.window_scores.items()
        }
        changed.append(entry)
        moved = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if moved:
            print(f"\nWindow rain verdicts moved on {d}:")
            for i, m in moved:
                print(f"  row {i} {m:16} {before.get((i, m))} -> {after.get((i, m))}")
    print(f"\nWindows rescored on {len(changed)} entr{'y' if len(changed) == 1 else 'ies'}"
          f"{' (dry run)' if dry_run else ''}.")
    return changed



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
    try:
        weather_by_date, readings_by_date = metar_fetch.observed_station_data(
            location.metar_station_icao, min(actuals), max(actuals), location.timezone,
            data_dir=data_dir,
            on_fallback=lambda reason: print(
                f"Station archive gave no usable answer ({reason}); rebuilding from the stored reports.",
                file=sys.stderr,
            ),
        )
    except metar_fetch.ArchiveUnavailable as e:
        print(f"Station archive gave no usable answer ({e}).", file=sys.stderr)
        weather_by_date, readings_by_date = None, None
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

    # THE WINDOWS TOO — ROADMAP item 139. A scored window is left alone by
    # every run (idempotent by stamp), so this is the one place a window is
    # deliberately rescored: from the archive over the whole log and the
    # station over the same span, exactly as the daily pass scores a fresh
    # one. The first scored window had been scored without the station.
    rescored = _rescore_windows(location, data_dir, log_dates, today, dry_run=args.dry_run)

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 0

    replace_all(cache, "primary", actuals)
    write_actuals_cache(data_dir, cache)
    write_track_record(data_dir, result.updated_track_record)
    for entry in rescored:
        write_log_entry(data_dir, entry)
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
            "Forecast for today — a full run if the day has no entry yet, a fresh "
            "forecast if new model guidance has landed, and otherwise a free refresh "
            "of what the station has observed. Every run is a forecast; the verb to "
            "schedule, at any frequency."
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
            "Re-issue even if a forecast went out in the last hour, and even if no new model "
            "cycle has landed. Forces the NARRATIVE only — the day's scored predictions are "
            "written once and cannot be reached from here."
        ),
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

    wvd = sub.add_parser(
        "window-vs-day",
        help="Both Day+0 series side by side, on the days that carry both — ROADMAP item 104.",
    )
    wvd.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    wvd.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")

    psz = sub.add_parser(
        "prompt-size",
        help="Every archived issuance's prompt, sized per block, with deltas — ROADMAP item 148.",
    )
    psz.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")
    psz.add_argument("--last", type=int, default=10, help="How many issuances to list (default 10)")

    evl = sub.add_parser(
        "early-vs-late",
        help="Is a later issuance better informed? The day's first window against its last.",
    )
    evl.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to location.yaml")
    evl.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Path to the data/ directory")

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
    if args.command == "window-vs-day":
        return _run_window_vs_day(args)
    if args.command == "early-vs-late":
        return _run_early_vs_late(args)
    if args.command == "prompt-size":
        return _run_prompt_size(args)

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
