"""Rebuild a day's narrative from the judgment call that already succeeded.

ROADMAP items 132 and 127. On 2026-09-15 the judgment call went through at
03:02:28 and every narrative attempt was refused, so the figures published with
no write-up — the first `narrative_unavailable` in 36 stored days, and a
failure mode the prompt split invented.

THE EXPENSIVE HALF SURVIVED. The scored call is made, stored and immutable;
what is missing is prose. So a repair is ONE request rather than two, which
against a ceiling of 20 a day is the difference between affordable and not.

### What it does NOT touch

`prediction_rows` and every scored field. Row 0 is what tomorrow verifies
against and it is write-once — the whole accuracy record rests on that. This
rewrites `narrative_markdown`, the verification summary and the degradation
note, and nothing else. A repair that could move a scored number would be a
worse problem than the one it fixes.

### Why the judgment is rebuilt from the ENTRY

The prompt archive stores what was SENT and not what came back, so the
judgment object itself is gone. What survives is the published call on the
entry plus the blend's own scored rows, and between them they rebuild
`GeminiJudgmentResponse` — the exact object `build_narrative_user_prompt`
JSON-dumps under THE FORECASTER'S CALL.

**THIS PARAGRAPH USED TO CLAIM MORE THAN IT DELIVERED, and the correction is
the finding.** It read: *"the published call on the entry, which is the same
numbers ... so the renderer sees exactly what it would have seen."* It was
not. The tool sent a FLAT dict of nine fields; production sends a nested
object of two — `today_properties` with THIRTEEN fields and
`extended_properties`, a list. So a re-render was handing the model a
materially narrower call than a real run does: no `rain`, no `onset_hour`,
no `precip_mm`, no `rain_probability_pct`, and nothing at all about Day+3 or
Day+7, against a system prompt that says in terms *"YOUR PROSE MUST AGREE
WITH THEM"* of `today_properties` AND `extended_properties`.

Found on 2026-09-16 by item 77's harness — twice, because the first
correction trusted this docstring instead of reading
`build_narrative_user_prompt`. A false "measured" claim in a comment costs
more than no comment, which this repo already knew and this is the proof.

### What it does NOT restore, and this is a real loss

`GeminiNarrativeResponse` also carries `verification_notes` and
`skill_profile_summaries`, which a normal run writes back onto YESTERDAY's
prediction rows — the mechanism the prompt calls "the actual mechanism that
improves future forecasts". This writes neither.

Two reasons, and the second is the one that decides it. They belong to other
days' entries, so restoring them means a repair reaching across files it was
not asked to touch. And a note written now would describe a verification that
happened at 03:02 under a prompt that failed, which is not the same note the
run would have written — it would be a plausible reconstruction landing in a
record that is read as evidence for weeks.

So the day is repaired for the READER and the learning loop keeps the gap. That
is the honest trade and it should be visible rather than quietly patched.

### The flags have to match production or the prompt is a different one

`build_narrative_prompt` branches on `verification_already_written` and on
whether ground
stations and a met service are configured. The archive stores
`narrative_prompt_sha256`, so the right combination is not guessed: this sweeps
them and refuses unless one reproduces the stored hash. Guessing would rebuild
a prompt production never used and quietly test the wrong thing.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openlocalweather.config import load_location_config  # noqa: E402
from openlocalweather.llm.prompt import (  # noqa: E402
    build_narrative_prompt,
    build_narrative_user_prompt,
)
from openlocalweather.llm.schema import GeminiNarrativeResponse  # noqa: E402
from openlocalweather.pipeline import attach_spend_cap  # noqa: E402
from openlocalweather.store import log_store  # noqa: E402
from openlocalweather.verify.scoring import scored_predictions  # noqa: E402
from openlocalweather.defaults import BLEND_MODEL_ID  # noqa: E402

# `today_properties`, split by WHERE each field survives.
#
# Nine are published on the entry; the other four live only on the blend's own
# Day+0 scored row, because they are the numbers the record grades rather than
# the ones the page prints. Both halves are needed: production sends all
# fourteen, and a renderer told nothing about `rain` or `onset_hour` is being
# asked to agree with a call it cannot see.
CALL_FIELDS_ON_ENTRY = (
    "rain_expected", "onset_window", "peak_wind_primary_kmh",
    "peak_wind_secondary_kmh", "temp_high_c", "temp_low_c",
    "mslp_trend_24h", "synoptic_pattern", "uv_index_max", "air_quality_aqi",
)
# (TodayProperties name, attribute on the scored blend row)
CALL_FIELDS_ON_BLEND = (
    ("rain", "rain"),
    ("onset_hour", "onset"),
    ("precip_mm", "precip_mm"),
    ("rain_probability_pct", "rain_probability_pct"),
)


def _blend_row(entry, lead: str):
    """The blend's own scored row at this lead, or None.

    `olw_blend` is the call the record grades. Any other model here would be
    an input to the call rather than the call itself.
    """
    rows = getattr(scored_predictions(entry), lead, []) or []
    return next((p for p in rows if p.model == BLEND_MODEL_ID), None)


def _rebuild_judgment(entry) -> dict:
    """`GeminiJudgmentResponse` as the narrative call receives it.

    SHAPE FIRST, VALUES SECOND. The renderer is handed
    `judgment.model_dump()`, which is two keys — `today_properties` and
    `extended_properties`. Handing it a flat dict of the same numbers is not
    the same document: rules addressing `today_properties.temp_high_c` by
    path have no referent, and a prompt that says the prose must agree with
    `extended_properties` is arguing about something absent.

    `extended_properties` carries only what `ExtendedDayProperties` holds —
    lead time, rain, probability — so it rebuilds exactly from the blend's
    Day+3 and Day+7 rows with nothing invented.
    """
    today = {f: getattr(entry, f, None) for f in CALL_FIELDS_ON_ENTRY}

    day0 = _blend_row(entry, "day0")
    if day0 is None:
        # REFUSE RATHER THAN SEND A NARROWER CALL. `rain` is non-nullable on
        # TodayProperties, so without the blend row the rebuilt object does
        # not even validate — and the failure this tool exists to avoid is
        # exactly handing the model a call that is not the one production
        # sends. A day with no blend row cannot be re-rendered faithfully, so
        # it is not re-rendered.
        raise SystemExit(
            f"no {BLEND_MODEL_ID} Day+0 row on {entry.date}: the forecaster's call "
            "cannot be rebuilt, and a partial one would render a prompt production "
            "never sent. Refusing rather than guessing."
        )

    for name, attr in CALL_FIELDS_ON_BLEND:
        today[name] = getattr(day0, attr, None)

    extended = []
    for lead_days, lead in ((3, "day3"), (7, "day7")):
        row = _blend_row(entry, lead)
        if row is None:
            continue
        extended.append({
            "lead_time_days": lead_days,
            "rain": row.rain,
            "rain_probability_pct": row.rain_probability_pct,
        })

    return {"today_properties": today, "extended_properties": extended}


def matching_flags(location, target_sha: str) -> dict:
    for ground, bulletin, reissue in itertools.product((True, False), repeat=3):
        flags = dict(verification_already_written=reissue, ground_stations_configured=ground,
                     local_bulletin_configured=bulletin)
        built = build_narrative_prompt(location, **flags)
        if hashlib.sha256(built.encode()).hexdigest() == target_sha:
            return flags
    raise SystemExit(
        "no flag combination reproduces the archived narrative prompt hash — the "
        "prompt has changed since that issuance, so a re-render would send a "
        "different prompt than the one that failed. Refusing rather than guessing."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/location.yaml")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--date", default="", help="defaults to the newest archived prompt")
    ap.add_argument("--yes", action="store_true", help="actually call. Otherwise nothing is sent.")
    ap.add_argument(
        "--publish-only",
        action="store_true",
        help="Republish docs/ from the stored entry. Sends nothing and calls no model.",
    )
    ap.add_argument("--docs-dir", default="docs", help="Path to the docs/ (GitHub Pages) directory")
    ap.add_argument(
        "--public-url",
        default=os.environ.get("OLW_PUBLIC_URL", ""),
        help="Public site URL. Without it the repaired day is written to the log but not republished.",
    )
    a = ap.parse_args()

    data_dir = Path(a.data_dir)
    day = a.date or sorted(p.stem for p in (data_dir / "prompts").glob("*.json"))[-1]
    archive = json.loads((data_dir / "prompts" / f"{day}.json").read_text())
    issuance = archive["issuances"][0]

    entry = log_store.read_log_entry(data_dir, date.fromisoformat(day))
    if entry is None:
        raise SystemExit(f"no stored entry for {day}")

    degradations = [d.code for d in (entry.meta.degradations or [])]
    call = _rebuild_judgment(entry)

    location = load_location_config(a.config)
    flags = matching_flags(location, issuance["narrative_prompt_sha256"])
    system_prompt = build_narrative_prompt(location, **flags)
    user_prompt = build_narrative_user_prompt(issuance["user_prompt"], call)

    print(f"day:          {day}")
    print(f"degradations: {degradations or 'none'}")
    print(f"flags:        {flags}  (reproduce the archived hash)")
    print(f"narrative:    {len(entry.narrative_markdown or '')} chars stored now")
    print(f"prompts:      system {len(system_prompt):,} + user {len(user_prompt):,} chars")
    print(f"provider:     {os.environ.get('LLM_PROVIDER') or 'gemini (default)'}")
    # A FINGERPRINT, NOT THE KEY. On 2026-09-15 a submit was refused with
    # ACCESS_TOKEN_TYPE_UNSUPPORTED minutes after the probe had been accepted
    # against the same endpoint with the same auth mechanism — so the first
    # thing to rule out is that the two runs used different credentials. That
    # is answerable by eye and costs no requests, which is the only reason this
    # is printed at all.
    # BOTH OF THESE ARE ABOUT A CALL, so neither belongs on --publish-only,
    # which makes none. Printed there they actively mislead: a fingerprint
    # implies a credential is about to be used, and the overwrite warning
    # describes a re-render that is not going to happen.
    if not a.publish_only:
        key = os.environ.get("GEMINI_API_KEY") or ""
        print(f"credential:   {'set, ' + str(len(key)) + ' chars, ' + key[:4] + '...' + key[-4:]
                               if len(key) >= 8 else 'MISSING or too short'}")

        if "narrative_unavailable" not in degradations:
            print("\nNOTE: this day carries no narrative_unavailable degradation. Re-rendering")
            print("would replace a write-up that was produced normally.")

    # Imported here rather than at module scope because the provider factory
    # reads the environment at call time.
    from openlocalweather.cli import (  # noqa: PLC0415
        _build_llm_provider,
        _build_pages_publisher,
    )

    if a.publish_only:
        # THE REPAIR MINUS THE CALL. A render that succeeded and a publish that
        # did not is one state; so is a day repaired before this tool could
        # republish at all, which is how 2026-09-15 ended up needing it. Both
        # want the pages rebuilt from what is already on disk, and neither
        # should spend a request to get it — the narrative is already stored.
        publisher = _build_pages_publisher(location, data_dir, a.docs_dir, a.public_url)
        if publisher is None:
            raise SystemExit("--publish-only needs --public-url, or nav links break.")

        publisher.publish(entry)
        print(f"\nrepublished docs/ from the stored entry for {day}. Nothing was sent.")
        return 0

    if not a.yes:
        print("\nDRY RUN — nothing sent. Re-run with --yes.")
        return 0

    provider = _build_llm_provider(providers=location.llm_providers)
    # A REPAIR SPENDS THE SAME ALLOWANCE AS A FORECAST. Missing on the first
    # version of this tool, and the run of 2026-09-15 went into Google's count
    # and not into ours — which mattered more than usual, because item 132's
    # whole argument is a request count and the ledger it argues from was
    # already short. `tests/test_spend_coverage.py` now scans tools/ so the
    # fifth caller fails a test instead of a budget.
    #
    # The cap is enforcement, not bookkeeping: assert_capacity refuses before
    # the first call if the day cannot afford one, so a repair attempted
    # against an exhausted allowance stops here rather than adding a 503 to
    # the pile that caused it.
    verify_spend, _ = attach_spend_cap(
        provider,
        data_dir,
        max_calls=location.max_llm_calls_per_24h,
        purpose="narrative-rerender",
    )
    narrative: GeminiNarrativeResponse = provider.generate(
        system_prompt, user_prompt, GeminiNarrativeResponse
    )

    verify_spend()

    entry.narrative_markdown = narrative.today_narrative
    entry.yesterday_verification_summary = narrative.yesterday_verification
    # THE DEGRADATION GOES because it is no longer true: the write-up exists.
    # Left in place it would tell a reader the page is short when it is not.
    entry.meta.degradations = [
        d for d in (entry.meta.degradations or []) if d.code != "narrative_unavailable"
    ]
    log_store.write_log_entry(data_dir, entry)

    print(f"\nwrote {len(narrative.today_narrative):,} chars of narrative to {day}")
    print("prediction_rows and every scored field are untouched.")

    # REPUBLISHING IS PART OF THE REPAIR, not a follow-up the operator is
    # told to do. The first version of this tool printed "Run `olw rebuild`",
    # which is not a command — there is none that re-renders pages from a
    # stored entry, because pages are written as a side effect of a forecast
    # run. So the instruction sent the operator to an argparse error, and the
    # nearest real command, `rebuild-record`, is actively the wrong one: it
    # re-derives the ACCURACY record from freshly fetched observations and
    # says in its own docstring that it does not touch narratives.
    publisher = _build_pages_publisher(location, data_dir, a.docs_dir, a.public_url)
    if publisher is None:
        print("\nNOT REPUBLISHED — no --public-url, so pages would carry broken nav links.")
        print(f"Pass --public-url to rebuild docs/ for {day}.")
        return 0

    publisher.publish(entry)
    print(f"republished docs/ from the stored entry for {day}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
