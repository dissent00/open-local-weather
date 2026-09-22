"""Location config loading and validation.

Ports the LOCATION block from the Apps Script pipeline into a validated
Pydantic model, loaded from YAML. Unlike the Apps Script version — where a
malformed or missing field just produces `undefined` somewhere downstream and
fails silently or confusingly — this fails fast at startup with a clear
error, since YAML edited by hand across many forks is exactly where typos
happen.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import yaml
from openlocalweather.reasoning import LLMRefreshPolicy
from openlocalweather.llm.provider import DEFAULT_LLM_PROVIDER, VALID_LLM_PROVIDERS
from openlocalweather.spend import DEFAULT_MAX_LLM_CALLS_PER_24H
from openlocalweather.models import DeviationBands
from pydantic import BaseModel, Field, field_validator


class Point(BaseModel):
    lat: float
    lon: float


class RegionPoint(Point):
    name: str


class SecondaryPoint(Point):
    enabled: bool = False
    name: str = ""
    section_label: str = ""
    lat: float = 0.0
    lon: float = 0.0


class DeviationBandsConfig(BaseModel):
    """What this deployment counts as a deviation worth REPORTING — ROADMAP
    item 145.

    REPORTING ONLY. Nothing here can change what a run spends on LLM calls:
    `disagreement` decides that from a constant no configuration reaches, and
    a swept test proves these cannot move it. That separation is the whole
    point of the block — see `models.DeviationBands`.

    OMITTING IT IS THE NORMAL CASE. Every field defaults to the shipped value,
    so a deployment that says nothing behaves exactly as it did. Setting one
    is a statement that this deployment's readers want a different answer, and
    item 143 records why that is a reasonable thing to want: the shipped low
    band is about twelve times the measured gap here, which is right for
    someone walking to work and wrong for someone whose crop is frost-tender.
    """

    low_c: float | None = None
    low_freezing_c: float | None = None
    # Item 145's next step: the high's and the onset's reporting margins.
    high_c: float | None = None
    onset_min: int | None = None


class WaqiStation(BaseModel):
    """One ground-truth AQI monitoring station. `name` is OUR configured
    display name — used everywhere a station is named (site, email, the
    LLM prompt) — not WAQI's own `city.name`, which can be verbose or
    inconsistent between stations. Verify the station_id manually at
    waqi.info; it cannot be validated from code, and a wrong ID silently
    poisons the "ground truth" comparison (see fetch/waqi.py)."""

    name: str
    station_id: str


class AcknowledgedGap(BaseModel):
    """A coverage gap that is known, understood, and not a fault.

    `variable` omitted means every variable at that (model, lead time).
    `reason` is required on purpose: an acknowledgement without a stated
    reason is indistinguishable from a silenced alarm, and in a year nobody
    will remember which it was.

    `since` is when the gap was acknowledged — ROADMAP item 152 step 4. An
    acknowledgement is an exclusion made on a measurement, and the day the
    source changes (`became_available`) the reader needs to know how old
    that measurement was. Optional because a fork's entries may predate the
    field; the reference deployment's are all dated from the file's history.
    """

    model: str
    lead_time_days: int
    reason: str
    variable: str | None = None
    since: date | None = None

    def covers(self, model: str, lead_time_days: int, variable: str) -> bool:
        return (
            self.model == model
            and self.lead_time_days == lead_time_days
            and (self.variable is None or self.variable == variable)
        )


class LLMProviderEntry(BaseModel):
    """One link in the fallback chain that names its own credentials.

    ROADMAP item 81, 2026-09-22, from the operator: *"I want to be able to
    have multiple sources in cascading order. So gemini first, then
    openrouter, then another and another as long as I have api keys and
    providers."*

    The chain itself already walked a list of any length. What stopped it
    being extended was that a VENDOR NAME decided which environment variables
    were read, so two OpenAI-compatible gateways were indistinguishable: both
    would read LLM_BASE_URL and LLM_MODEL and the chain would hold the same
    endpoint twice.

    A BARE STRING IS STILL VALID AND STILL MEANS WHAT IT MEANT. This list
    accepts strings and mappings together, so no existing config file has to
    change — the same discipline as the 2026-09-15 change that made the field
    a list in the first place.
    """

    # The matrix row: vendor plus API. What `VALID_LLM_PROVIDERS` names.
    kind: str
    # What this link is CALLED in warnings, on stderr and in the record. With
    # two `openai` entries, "openai was dropped" does not say which one.
    name: str | None = None
    # The credential namespace: OPENROUTER means OPENROUTER_API_KEY,
    # OPENROUTER_BASE_URL, OPENROUTER_MODEL. Defaults per kind to the names
    # this project has always used.
    env_prefix: str | None = None
    # The gateway's OWN in-request order (OpenRouter's `models`), which is a
    # different order from this chain. It belongs to the entry because once a
    # chain holds two gateways, a top-level list cannot say which it means.
    fallback_models: list[str] | None = None

    # This link's own 24-hour ceiling — ROADMAP item 170. None means the
    # deployment's `max_llm_calls_per_24h`.
    #
    # A CAP BELONGS TO AN ACCOUNT, NOT TO A DEPLOYMENT, and the numbers are
    # not alike: 20 is Google's free calendar-day allowance, which is where
    # the global default came from, while OpenRouter's free tier is 50 a day
    # on entirely separate terms. One number cannot hold both, and on
    # 2026-09-22 it did not: eight Gemini 503 retries left five of twenty for
    # the next morning while neither vendor's real quota had been touched.
    max_calls_per_24h: int | None = None

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        kind = v.strip().lower()
        if kind not in VALID_LLM_PROVIDERS:
            raise ValueError(
                f"unknown llm_providers kind {v!r}: expected "
                f"{', '.join(VALID_LLM_PROVIDERS)}."
            )
        return kind


class LocationConfig(BaseModel):
    region_name: str
    primary_place_name: str
    timezone: str
    primary_point: Point
    secondary_point: SecondaryPoint = Field(default_factory=SecondaryPoint)
    region_points: list[RegionPoint] = Field(default_factory=list)
    metar_station_icao: str = ""
    # How to NAME that station to a reader — ROADMAP item 143.
    #
    # The divergence footnote is a fact about ONE station, not a claim about
    # the basin, and it only reads that way if it names the place: "the
    # airport reported 20C against a forecast of 18.2C" tells a reader what to
    # do with it, while "HKKI reported 20C" does not. Empty falls back to the
    # ICAO, which is ugly but never wrong.
    metar_station_name: str = ""
    # ROADMAP item 145. Absent means "the shipped defaults", which is what
    # almost every deployment wants; see DeviationBandsConfig.
    deviation_bands: DeviationBandsConfig = Field(default_factory=DeviationBandsConfig)
    waqi_stations: list[WaqiStation] = Field(default_factory=list)
    local_bulletin_url: str = ""
    local_bulletin_source_name: str = ""
    # The national met service's CAP warning feed, where one exists — ROADMAP
    # item 2. Empty means this location has none, which is a configuration and
    # not a fault: most of the world's services publish no CAP at all.
    #
    # Kenya's was found at /api/cap/rss.xml only because somebody probed an
    # API namespace after the WordPress feed paths all 404'd, so do not assume
    # a service has none because the obvious paths are empty.
    cap_feed_url: str = ""

    # The met service's own name for the administrative area this location
    # sits in — the key its bulletin is indexed by ("Kisumu" county for
    # Kisumu city). Empty disables met-service scoring while leaving the
    # bulletin text itself untouched, so a fork whose met service publishes
    # unparseable bulletins still gets the narrative context.
    # Coverage gaps that have been investigated and accepted — see
    # coverage.py. Without this, a permanent and documented limitation (ICON
    # and UKMO forecast horizons ending short of 7 days) reports every week
    # alongside genuine finds, and ten expected lines around one real one is
    # how a check stops being read.
    acknowledged_coverage_gaps: list[AcknowledgedGap] = Field(default_factory=list)

    # Hard ceiling on LLM calls in any rolling 24 hours, enforced in code
    # before each call (see spend.py). NOT a rate limit and not a target: the
    # run fails loudly rather than waiting, because a cap that skips quietly
    # would be discovered only by noticing missing forecasts days later.
    #
    # Counts CALLS, not forecasts — one forecast can cost several attempts
    # when a provider is flaky.
    max_llm_calls_per_24h: int = DEFAULT_MAX_LLM_CALLS_PER_24H

    # WHICH PROVIDER THIS DEPLOYMENT USES — moved here 2026-09-15, from a
    # code constant that could only be overridden through the environment.
    #
    # It belongs beside `max_llm_calls_per_24h` because it is the same KIND of
    # decision: operator policy about how this deployment talks to a model,
    # not forecast logic and not a secret. The cap has lived here since it
    # existed; the provider was the odd one out.
    #
    # The argument for keeping it in the environment was that a committed
    # choice makes every fork carry a diff. That argument does not apply to
    # THIS file: it is this deployment's own config — forks start from
    # `location.example.yaml` — and it already holds coordinates, a METAR
    # station and a call cap that no fork would share.
    #
    # Keys stay in the environment, and that distinction is the real one. A
    # provider NAME is configuration and belongs in version control where a
    # change to it is reviewable and attributable. An API KEY is a secret and
    # must never be committed. Splitting them was treating one decision as
    # two.
    #
    # A LIST, not a string, and that is deliberate rather than premature.
    # Item 81's fallback question — what happens when one provider sheds — is
    # answered by an ORDER, and a schema that takes a single name would have
    # to be migrated to express one. Today only the first entry is used and
    # the validator says so out loud, so the shape is honest about what it
    # does; adding the fallback later is then a change to behaviour and not
    # to everyone's config file.
    llm_providers: list[str | LLMProviderEntry] = [DEFAULT_LLM_PROVIDER]

    # The models an OpenRouter-style gateway should try, in order, INSIDE one
    # request — ROADMAP item 81, 2026-09-21. Distinct from `llm_providers`
    # above, which is the order of VENDORS this project walks itself: this
    # list never leaves the gateway, and the gateway decides when to move down
    # it. Empty for every deployment that does not use one.
    #
    # HERE RATHER THAN IN THE ENVIRONMENT, for the reason the provider itself
    # moved here on 2026-09-15: which models serve a deployment is a decision
    # that should have a diff, a commit message and a history, and a value
    # typed into a hosting provider's web UI has none of those. `LLM_MODEL`
    # stays in the environment because it is paired with `LLM_API_KEY` in the
    # per-service setup; this is the deployment's own editorial choice.
    llm_fallback_models: list[str] = []

    @field_validator("llm_providers")
    @classmethod
    def _known_providers(cls, v: list) -> list:
        """Rejects a name nothing can build, and says that only the first is
        used.

        THE FAILURE THIS PREVENTS IS A SILENT FALLBACK TO GEMINI. A typo'd
        provider name would otherwise reach `_build_llm_provider`, miss every
        branch and raise — or worse, if the branch order ever changes, build
        something the operator did not ask for. Caught at load, it names the
        valid set instead.

        THE LIST IS NOW AN ORDER, 2026-09-21. This validator used to warn
        that entries after the first were ignored, which was the honest half
        of shipping a list before the thing that consumes it. Item 81's chain
        consumes it: `cli._build_llm_provider` builds every named provider,
        drops the ones whose keys this deployment does not hold, and wraps
        what is left in a `FallbackProvider`. The warning is gone because it
        became false, and the schema never had to change to get here.
        """
        if not v:
            raise ValueError(
                "llm_providers must name at least one provider; "
                f"expected one of {', '.join(VALID_LLM_PROVIDERS)}."
            )

        # A MAPPING ENTRY VALIDATES ITS OWN `kind`, so this checks only the
        # bare strings. Both paths reject a name nothing can build, and for
        # the same reason: caught at load it names the valid set, where a
        # typo reaching the builder would be dropped from the chain in
        # silence and the deployment would run shorter than its config says.
        unknown = [
            name for name in v
            if isinstance(name, str) and name.lower() not in VALID_LLM_PROVIDERS
        ]
        if unknown:
            raise ValueError(
                f"unknown llm_providers {unknown}: expected "
                f"{', '.join(VALID_LLM_PROVIDERS)}."
            )

        return [name.lower() if isinstance(name, str) else name for name in v]

    # When a LATER issuance of a day may spend an LLM call — ROADMAP items 121
    # and 120. Observations refresh in code on every run regardless; this
    # governs only whether the judgment and the narrative are re-reasoned.
    #
    # Defaults to chasing model runs. C2's own answer also re-forecasts on a
    # contradicting observation, and that value exists — see LLMRefreshPolicy
    # for why it is not the default, which is a measurement that has not been
    # taken rather than a disagreement with C2.
    llm_refresh_policy: LLMRefreshPolicy = LLMRefreshPolicy.NEW_CYCLE_ONLY

    local_bulletin_area_name: str = ""
    # Model id the met service is scored under, alongside gfs_seamless and
    # the rest. Kept configurable so a fork's accuracy page names its own
    # service rather than Kenya's.
    local_bulletin_model_id: str = "local_met_service"


def load_location_config(path: str | Path) -> LocationConfig:
    """Load and validate a location.yaml file.

    Raises FileNotFoundError if the path doesn't exist, and
    pydantic.ValidationError (with a field-level message) if the YAML is
    missing required fields or has the wrong shape — both are meant to be
    loud, readable failures rather than something that silently degrades a
    forecast run.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Location config not found at {path}. "
            "Copy config/location.example.yaml to config/location.yaml and fill it in."
        )
    raw = yaml.safe_load(path.read_text())
    if not raw or "location" not in raw:
        raise ValueError(f"{path} must have a top-level 'location:' key.")
    return LocationConfig.model_validate(raw["location"])


def deviation_bands(location: LocationConfig) -> DeviationBands:
    """This deployment's reporting bands, with the shipped defaults filling
    every gap — ROADMAP item 145.

    FIELD BY FIELD, not all-or-nothing. A deployment that tightens the
    near-freezing band should not silently inherit a stale value for the other
    one, and `None` means "not configured" rather than zero — absence is
    absence, as everywhere else in this record.
    """
    configured = location.deviation_bands
    shipped = DeviationBands()

    return DeviationBands(
        low_c=shipped.low_c if configured.low_c is None else configured.low_c,
        low_freezing_c=(
            shipped.low_freezing_c
            if configured.low_freezing_c is None
            else configured.low_freezing_c
        ),
        high_c=shipped.high_c if configured.high_c is None else configured.high_c,
        onset_min=shipped.onset_min if configured.onset_min is None else configured.onset_min,
    )
