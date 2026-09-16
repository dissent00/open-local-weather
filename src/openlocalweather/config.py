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
from pathlib import Path

import yaml
from openlocalweather.reasoning import LLMRefreshPolicy
from openlocalweather.llm.provider import DEFAULT_LLM_PROVIDER, VALID_LLM_PROVIDERS
from openlocalweather.spend import DEFAULT_MAX_LLM_CALLS_PER_24H
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
    """

    model: str
    lead_time_days: int
    reason: str
    variable: str | None = None

    def covers(self, model: str, lead_time_days: int, variable: str) -> bool:
        return (
            self.model == model
            and self.lead_time_days == lead_time_days
            and (self.variable is None or self.variable == variable)
        )


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
    llm_providers: list[str] = [DEFAULT_LLM_PROVIDER]

    @field_validator("llm_providers")
    @classmethod
    def _known_providers(cls, v: list[str]) -> list[str]:
        """Rejects a name nothing can build, and says that only the first is
        used.

        THE FAILURE THIS PREVENTS IS A SILENT FALLBACK TO GEMINI. A typo'd
        provider name would otherwise reach `_build_llm_provider`, miss every
        branch and raise — or worse, if the branch order ever changes, build
        something the operator did not ask for. Caught at load, it names the
        valid set instead.

        The warning on extra entries is the honest half of shipping a list
        before the thing that consumes it. The shape is right for item 81's
        fallback order and the schema should not have to change again to get
        there; today only `[0]` is read, and an operator who writes a second
        entry deserves to be told it does nothing rather than to discover it
        during an outage.
        """
        if not v:
            raise ValueError(
                "llm_providers must name at least one provider; "
                f"expected one of {', '.join(VALID_LLM_PROVIDERS)}."
            )

        unknown = [name for name in v if name.lower() not in VALID_LLM_PROVIDERS]
        if unknown:
            raise ValueError(
                f"unknown llm_providers {unknown}: expected "
                f"{', '.join(VALID_LLM_PROVIDERS)}."
            )

        if len(v) > 1:
            print(
                f"WARNING: llm_providers names {len(v)} providers and only the "
                f"first ({v[0]}) is used. Fallback ordering is ROADMAP item 81 "
                f"and is not built yet — the rest are ignored.",
                file=sys.stderr,
            )

        return [name.lower() for name in v]

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
