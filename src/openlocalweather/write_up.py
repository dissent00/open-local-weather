"""The write-up's second chance — the operator's decision, 2026-09-30.

One run a day, at 03:01Z (ROADMAP item 186). When its write-up was refused,
the same job asks once more about an hour later, for the prose alone: the
scored call is stored and immutable, so the repair is one request. `olw
write-up` in cli.py makes the call; this module builds what it sends and
applies what comes back, and touches no scored field.

Moved here from tools/rerender_narrative.py, which found the two ways a
rebuilt call goes wrong and whose docstring records them: the call is the
NESTED object production sends, and the narrative prompt must reproduce the
archived hash or it is a prompt production never used.
"""

from __future__ import annotations

import hashlib
import itertools

from openlocalweather.defaults import BLEND_MODEL_ID
from openlocalweather.llm.prompt import build_narrative_prompt, build_narrative_user_prompt
from openlocalweather.models import DEGRADATION_NARRATIVE
from openlocalweather.verify.scoring import resolve_prediction_rows

# `today_properties`, split by WHERE each field survives: nine are published
# on the entry, four live only on the blend's own scored Day+0 row.
CALL_FIELDS_ON_ENTRY = (
    "rain_expected", "onset_window", "peak_wind_primary_kmh",
    "peak_wind_secondary_kmh", "temp_high_c", "temp_low_c",
    "mslp_trend_24h", "synoptic_pattern", "air_quality_aqi",
)
# (TodayProperties name, attribute on the scored blend row)
CALL_FIELDS_ON_BLEND = (
    ("rain", "rain"),
    ("onset_hour", "onset"),
    ("precip_mm", "precip_mm"),
    ("rain_probability_pct", "rain_probability_pct"),
)


class CannotRewrite(Exception):
    """The call or the prompt cannot be rebuilt as production sent it."""


def needs_write_up(entry) -> bool:
    """Whether the day's latest issuance published without its write-up."""
    return any(d.code == DEGRADATION_NARRATIVE for d in entry.meta.degradations or [])


def _blend_row(entry, lead_time_days: int):
    """The blend's own row at this lead on the LATEST issuance: the entry's
    published fields are the latest issuance's, so its call is too."""
    rows = resolve_prediction_rows(entry)
    if not rows:
        return None
    return next((p for p in rows[-1].predictions.for_lead(lead_time_days) if p.model == BLEND_MODEL_ID), None)


def rebuild_judgment(entry) -> dict:
    """`GeminiJudgmentResponse` as the narrative call receives it: two keys,
    `today_properties` and `extended_properties`, never a flat dict."""
    today = {f: getattr(entry, f, None) for f in CALL_FIELDS_ON_ENTRY}
    day0 = _blend_row(entry, 0)
    if day0 is None:
        # Refused rather than narrowed: `rain` is non-nullable, and a call
        # without the blend row is one production never sends.
        raise CannotRewrite(
            f"no {BLEND_MODEL_ID} Day+0 row on {entry.date}: the forecaster's call "
            "cannot be rebuilt, and a partial one would render a prompt production "
            "never sent. Refusing rather than guessing."
        )
    for name, attr in CALL_FIELDS_ON_BLEND:
        today[name] = getattr(day0, attr, None)

    extended = []
    for lead_days in (3, 7):
        row = _blend_row(entry, lead_days)
        if row is None:
            continue
        extended.append({
            "lead_time_days": lead_days,
            "rain": row.rain,
            "rain_probability_pct": row.rain_probability_pct,
        })
    return {"today_properties": today, "extended_properties": extended}


def matching_flags(location, target_sha: str) -> dict:
    """The narrative prompt's flags that reproduce the archived hash."""
    for ground, bulletin, reissue in itertools.product((True, False), repeat=3):
        flags = dict(verification_already_written=reissue, ground_stations_configured=ground,
                     local_bulletin_configured=bulletin)
        built = build_narrative_prompt(location, **flags)
        if hashlib.sha256(built.encode()).hexdigest() == target_sha:
            return flags
    raise CannotRewrite(
        "no flag combination reproduces the archived narrative prompt hash — the "
        "prompt has changed since that issuance, so a re-render would send a "
        "different prompt than the one that failed. Refusing rather than guessing."
    )


def write_up_prompts(entry, issuance: dict, location) -> tuple[str, str]:
    """The narrative call's (system, user) prompts for the entry's latest
    issuance, as production built them."""
    call = rebuild_judgment(entry)
    flags = matching_flags(location, issuance["narrative_prompt_sha256"])
    return (
        build_narrative_prompt(location, **flags),
        build_narrative_user_prompt(issuance["user_prompt"], call),
    )


def apply_write_up(entry, narrative, served_model: str) -> None:
    """The prose onto the entry, and the degradation off it, since it is no
    longer true. `verification_notes` and `skill_profile_summaries` belong
    to other days' rows and are not written back: see the tool's docstring."""
    entry.narrative_markdown = narrative.today_narrative
    entry.yesterday_verification_summary = narrative.yesterday_verification
    entry.meta.narrative_llm_model = served_model
    entry.meta.degradations = [
        d for d in (entry.meta.degradations or []) if d.code != DEGRADATION_NARRATIVE
    ]
