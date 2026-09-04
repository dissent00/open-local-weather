"""Replay a fixed set of inputs through the LLM, and diff the outputs.

ROADMAP item 27, and specifically the half that other items are waiting on.
Item 27 asks two different questions and they need different tools:

  - "Is this MODEL better?" — only weeks of scored forecasts can answer that,
    partitioned by `meta.llm_model` and gated by `review.py` exactly as the
    weather models are. Not this module.
  - "Did this PROMPT EDIT change anything I did not intend?" — answerable in
    minutes, by sending the same inputs before and after and reading the
    difference. That is this module.

WHY IT EXISTS. Items 48 and 53.3 both rewrote parts of the system prompt with
no way to see what else moved. Neither is known to have broken anything; the
point is that nobody could have said so either way, and the prompt is now
451 lines of accumulated rules where the interactions are not obvious.

THE FROZEN INPUTS ARE THE PROMPT VECTORS, deliberately. `spec/vectors/
llm_user_prompt.json` and `llm_system_prompt.json` already hold complete
input sets, are already committed, and are already updated by anyone who
changes the prompt — a second corpus would be a second thing to keep in step,
and the one that rots is always the one nobody's test suite reads.

WHAT A DIFFERENCE MEANS depends on which half moved, which is why
`ReplayDiff` separates them. The narrative is prose and a change to it is a
judgement call. `today_properties` is the blended call that gets SCORED
against tomorrow's observations and published on the accuracy page, so a
prompt edit that moves it has changed the forecast rather than the wording.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.llm.prompt import build_system_prompt, build_user_prompt
from openlocalweather.llm.provider import LLMProvider
from openlocalweather.llm.schema import GeminiForecastResponse

VECTORS_DIR = Path(__file__).resolve().parents[2] / "spec" / "vectors"

# The half of the response that is SCORED and published, as opposed to the
# half that is prose. Named here so a diff can say which kind of change it is
# looking at rather than leaving the reader to work it out.
SCORED_PREFIX = "today_properties"


@dataclass(frozen=True)
class ReplayCase:
    name: str
    system_prompt: str
    user_prompt: str


@dataclass
class ReplayResult:
    case: str
    model: str
    latency_s: float
    response: GeminiForecastResponse

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "model": self.model,
            "latency_s": self.latency_s,
            "response": self.response.model_dump(mode="json"),
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "ReplayResult":
        return ReplayResult(
            case=d["case"],
            model=d["model"],
            latency_s=d["latency_s"],
            response=GeminiForecastResponse(**d["response"]),
        )


@dataclass
class ReplayDiff:
    case: str
    changed_fields: list[str]
    # True when the change reached the blended call the record scores, rather
    # than only the prose. The distinction item 27 asks for.
    scored_changed: bool


def frozen_cases() -> list[ReplayCase]:
    """The committed prompt vectors, as replayable input sets.

    Each case's system prompt is BUILT FROM THAT CASE'S OWN FLAGS, not looked
    up. The two vector files name their cases differently, and an earlier
    version matched them by name and silently fell back to the first system
    prompt for every case — so the "no ground stations configured" case was
    replayed with a system prompt that talks about ground stations. That is
    a mismatched pair, and it produces differences that are artefacts of the
    harness rather than of the change under test. Caught by noticing every
    system prompt came out the same length.

    The location comes from the system-prompt vector rather than being
    invented here, so the two halves describe one place.
    """
    users = json.loads((VECTORS_DIR / "llm_user_prompt.json").read_text())["cases"]
    systems = json.loads((VECTORS_DIR / "llm_system_prompt.json").read_text())["cases"]
    # Built the same way tests/test_vectors.py builds it: the vector records
    # only the fields the system prompt actually reads, so the rest are given
    # neutral values rather than being invented from a real deployment.
    loc = systems[0]["input"]["location"]
    sec = loc["secondary_point"]
    location = LocationConfig(
        region_name=loc["region_name"],
        primary_place_name=loc["primary_place_name"],
        timezone="UTC",
        primary_point=Point(lat=0.0, lon=0.0),
        secondary_point=SecondaryPoint(
            enabled=sec["enabled"], name=sec["name"], section_label=sec["section_label"]
        ),
    )

    cases = []
    for case in users:
        i = dict(case["input"])
        ground = i.get("ground_stations_configured", True)
        bulletin = i.get("local_bulletin_configured", True)
        # `earlier_today`, NOT `historical_logs`. The second is the multi-day
        # history and is present on ordinary runs too; only the first means
        # "this day has already been forecast once", which is the condition
        # the pipeline itself uses and the one the system prompt branches on.
        # Getting this wrong replays the two refresh cases with a first-run
        # system prompt — the same mismatched-pair bug as the lookup above,
        # wearing a different hat.
        reissue = bool(i.get("earlier_today"))

        user = build_user_prompt(
            today=date.fromisoformat(i.pop("today")),
            yesterday=date.fromisoformat(i.pop("yesterday")),
            **i,
        )
        cases.append(
            ReplayCase(
                name=case["name"],
                system_prompt=build_system_prompt(
                    location,
                    is_reissue=reissue,
                    ground_stations_configured=ground,
                    local_bulletin_configured=bulletin,
                ),
                user_prompt=user,
            )
        )
    return cases


@dataclass
class ReplayFailure:
    case: str
    error: str


def run_replay(
    provider: LLMProvider, cases: list[ReplayCase]
) -> tuple[list[ReplayResult], list[ReplayFailure]]:
    """One generate() per case, in order, recording how long each took.

    Returns (what succeeded, what did not) rather than raising. EVERY CASE IS
    A PAID CALL, and an exception partway through would discard work already
    bought — measured 2026-09-03, when a real replay completed the first case,
    failed four times on the second, and returned nothing at all. A six-case
    run dying on the fifth would otherwise cost everything and yield nothing.

    A failure is recorded rather than swallowed: a diff computed over a
    partial run must be readable as partial, or it silently becomes a claim
    about cases that were never compared.

    Latency is recorded because "slightly better and three times slower" is a
    real outcome and a legitimate reason to decline a change — item 27 says so
    explicitly, and without a number that trade is made on impression too.
    """
    results: list[ReplayResult] = []
    failures: list[ReplayFailure] = []
    for case in cases:
        started = time.monotonic()
        try:
            response = provider.generate(
                case.system_prompt, case.user_prompt, GeminiForecastResponse
            )
        except Exception as e:  # noqa: BLE001 - a failed case must not cost the others
            failures.append(ReplayFailure(case=case.name, error=str(e)))
            continue

        results.append(
            ReplayResult(
                case=case.name,
                model=getattr(provider, "model", "unknown"),
                latency_s=time.monotonic() - started,
                response=response,
            )
        )
    return results, failures


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
        return out
    return {prefix: value}


def diff_replays(before: list[ReplayResult], after: list[ReplayResult]) -> list[ReplayDiff]:
    """What moved, per case, and whether it reached the scored half.

    Latency is deliberately NOT diffed. It varies run to run for reasons that
    have nothing to do with the change under test, and a diff that reports
    noise as a difference is a diff nobody reads. It is recorded per result
    for a human to compare; it is not evidence of anything on its own.
    """
    after_by_case = {r.case: r for r in after}

    diffs = []
    for b in before:
        a = after_by_case.get(b.case)
        if a is None:
            diffs.append(ReplayDiff(case=b.case, changed_fields=["<missing>"], scored_changed=True))
            continue

        flat_b = _flatten(b.response.model_dump(mode="json"))
        flat_a = _flatten(a.response.model_dump(mode="json"))
        changed = sorted(
            k for k in set(flat_b) | set(flat_a) if flat_b.get(k) != flat_a.get(k)
        )
        if not changed:
            continue

        diffs.append(
            ReplayDiff(
                case=b.case,
                changed_fields=changed,
                scored_changed=any(k.startswith(SCORED_PREFIX) for k in changed),
            )
        )
    return diffs


def write_replay(directory: Path, results: list[ReplayResult]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "replay.json").write_text(
        json.dumps([r.to_json() for r in results], indent=2, ensure_ascii=False) + "\n"
    )


def read_replay(directory: Path) -> list[ReplayResult]:
    raw = json.loads((directory / "replay.json").read_text())
    return [ReplayResult.from_json(d) for d in raw]
