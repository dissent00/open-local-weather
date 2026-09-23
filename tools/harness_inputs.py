"""Build item 77's harness inputs for the NARRATIVE call, faithfully.

Three things this gets right that a hand-assembled pair does not, the first
two learned the hard way on 2026-09-22 and the third on 2026-09-23:

1. THE NARRATIVE CALL'S USER MESSAGE IS NOT THE ARCHIVED ONE.
   `build_narrative_user_prompt` APPENDS the judgment call's answer as "THE
   FORECASTER'S CALL", and only the base is archived. Hand a cold reader the
   bare archive and it correctly reports the block missing, then declares five
   dependent rules unfollowable — all artefacts.

2. `extended_properties` IS RECONSTRUCTABLE and must not be left empty.
   The blend's own Day+3 and Day+7 commitment is stored on the prediction row
   as the `olw_blend` model. Leaving the array empty makes the Extended
   Outlook unjudgeable, which a reader flagged before this was fixed.

3. THE ARCHIVE IS THE PROMPT AS IT WAS BUILT THAT DAY.
   A block whose CONSTRUCTION changed since reproduces in its old form,
   silently, while the system prompt beside it is rebuilt from current code —
   so the pair is internally inconsistent and neither half can say so. The
   payloads survive inside the blocks, so `_rerendered` parses them back out
   and runs them through today's `build_user_prompt`. Watch its line of
   output: "NOTHING" means the markers stopped matching.

Usage: python harness_inputs.py <date> <out_dir>
"""
import json, sys
from datetime import date
from pathlib import Path

ROOT = Path.home() / "weather-app"
sys.path.insert(0, str(ROOT / "src"))
from openlocalweather.config import load_location_config
from openlocalweather.llm.prompt import (
    build_narrative_prompt,
    build_narrative_user_prompt,
    build_user_prompt,
)

day, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
entry = json.loads((ROOT / f"data/log/{day}.json").read_text())
archived = json.loads((ROOT / f"data/prompts/{day}.json").read_text())["issuances"][-1]["user_prompt"]

cfg = load_location_config(str(ROOT / "config/location.yaml"))
system = build_narrative_prompt(
    cfg, verification_already_written=True, ground_stations_configured=True,
    local_bulletin_configured=True, extended_outlook_available=True)

rows = (entry.get("prediction_rows") or [{}])[0].get("predictions") or {}
extended = []
for lead, key in ((3, "day3"), (7, "day7")):
    blend = next((r for r in rows.get(key, []) if r.get("model") == "olw_blend"), None)
    if blend and blend.get("rain") is not None:
        extended.append({
            "lead_time_days": lead,
            "rain": blend["rain"],
            "rain_probability_pct": blend.get("rain_probability_pct"),
        })

_day0_rows = (entry.get("prediction_rows") or [{}])[0].get("predictions", {}).get("day0", [])
_blend_day0 = next(
    (r for r in _day0_rows if r.get("model") == "olw_blend"), None
)

judgment = {
    "today_properties": {
        "rain": True,
        "rain_expected": entry["rain_expected"],
        "onset_window": entry["onset_window"],
        # FROM THE BLEND ROW, because the entry does not carry it at top
        # level — `pipeline` maps the judgment's `onset_hour` onto the
        # `olw_blend` prediction as `onset` and reads it back from there.
        # Reconstructing without it made a cold reader report on 2026-09-23
        # that "the field the prompt says was given to you is absent from the
        # call block", which is true of the HARNESS and false of the
        # deployment. A harness that invents a gap costs the same as one that
        # hides a real one.
        "onset_hour": _blend_day0.get("onset") if _blend_day0 else None,
        "rain_probability_pct": (
            _blend_day0.get("rain_probability_pct") if _blend_day0 else None
        ),
        "peak_wind_primary_kmh": entry["peak_wind_primary_kmh"],
        "peak_wind_secondary_kmh": entry["peak_wind_secondary_kmh"],
        "temp_high_c": entry["temp_high_c"],
        "temp_low_c": entry["temp_low_c"],
        "mslp_trend_24h": entry["mslp_trend_24h"],
        "synoptic_pattern": entry["synoptic_pattern"],
        # NO `uv_index_max` — it left `today_properties` with item 161 on
        # 2026-09-22. Code takes the UV index from the daily block for the day
        # the horizon points at, so reconstructing it here would hand the
        # reader a field the schema no longer has.
        "air_quality_aqi": entry.get("air_quality_index"),
    },
    "extended_properties": extended,
}
#: Blocks whose payload the archive still carries verbatim, so they can be
#: re-rendered by TODAY's code instead of reproducing as they were sent.
#: Matched as a line PREFIX and nothing more, because the heading's own text
#: is one of the things that changes: on 2026-09-23 the daily block went from
#: a bare "TODAY'S MULTI-MODEL GUIDANCE:" to one carrying its units, and a
#: marker written with the parenthesis matched neither archive nor rebuild.
RERENDERABLE = (
    "HOURS AHEAD",
    "TODAY'S MULTI-MODEL GUIDANCE",
    # Tabulated by item 176. Added the same day: without them the harness
    # showed the JSON the archive was written with, which is the exact trap
    # this mechanism exists to close.
    "MODEL TRACK RECORD",
    "EXTRACTED PER-MODEL PREDICTIONS",
    "CALENDAR",
    "GROUND AQI STATIONS",
)

#: Which `build_user_prompt` argument each block's payload is.
BLOCK_ARGUMENT = {
    "HOURS AHEAD": "forward_hourly",
    "TODAY'S MULTI-MODEL GUIDANCE": "today_weather_data",
    "MODEL TRACK RECORD": "track_record_context",
    "EXTRACTED PER-MODEL PREDICTIONS": "model_predictions_context",
    "CALENDAR": "forward_calendar",
    "GROUND AQI STATIONS": "ground_aqi_readings",
}


def _rendered(text, prefix):
    """A block as it appears, as (start, end, heading) — whatever its format.

    Blocks are separated by blank lines and neither pretty-printed JSON nor a
    table contains one, so the body runs from the heading to the next blank
    line. Format-agnostic on purpose: the FRESH prompt may render a block as a
    table where the archive had JSON, which is the whole point of item 176,
    and an extractor that assumed JSON silently found nothing on that side.
    """
    lines = text.splitlines()
    start = next(
        (i for i, l in enumerate(lines)
         if l.startswith(prefix) and i + 1 < len(lines) and lines[i + 1].strip()),
        None,
    )
    if start is None:
        return None

    end = next((i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines))
    return start, end - 1, lines[start]


def _block(text, prefix):
    """The heading line and its pretty-printed JSON, as (start, end, heading, payload).

    For the ARCHIVE side, which is always JSON: it was written before any
    block was tabulated. `_json` indents by two, so a block ends at the first
    column-0 closer.
    """
    lines = text.splitlines()
    start = next(
        (
            i for i, l in enumerate(lines)
            # The heading is the occurrence with a payload under it. The same
            # words appear in prose elsewhere in the message.
            if l.startswith(prefix) and i + 1 < len(lines) and lines[i + 1] in ("{", "[")
        ),
        None,
    )
    if start is None:
        return None

    closer = "}" if lines[start + 1] == "{" else "]"
    end = next((i for i in range(start + 2, len(lines)) if lines[i] == closer), None)
    if end is None:
        return None

    return start, end, lines[start], json.loads("\n".join(lines[start + 1:end + 1]))


def _rerendered(archived_text):
    """The archive's own payloads, rendered by the CURRENT prompt builder.

    WHY THIS EXISTS. The archive stores the finished user message, not the
    guidance that produced it, so a change to how a block is BUILT — item
    174 stripped the API's units and envelopes and dropped the UV series —
    reproduces here in its old form, silently. On 2026-09-23 that cost a
    harness run: the data change was tested against a prompt that still
    carried the data, and nothing said so.

    The payloads themselves survive verbatim inside the block, though. So
    they are parsed back out and handed to `build_user_prompt`, whose output
    for those two blocks is what today's code would send. No key list is
    duplicated here on purpose: the transforms and the headings both come
    from the module under test, so neither can drift from it.

    Returns the spliced text and the names of the blocks it replaced.
    """
    blocks = {p: _block(archived_text, p) for p in RERENDERABLE}
    if any(b is None for b in blocks.values()):
        return archived_text, []

    payloads = {BLOCK_ARGUMENT[p]: b[3] for p, b in blocks.items()}
    fresh = build_user_prompt(
        today=date.today(),
        yesterday=date.today(),
        public_webpage_url="",
        verification_context=None,
        ground_aqi_summary=None,
        yesterday_actual=None,
        local_bulletin_source_name="",
        local_bulletin_text="",
        # The archive's own heading says whether the window was narrowed.
        forward_window_narrowed="REST OF TODAY ONLY" in blocks["HOURS AHEAD"][2],
        **payloads,
    )

    out, replaced = archived_text, []
    for prefix in RERENDERABLE:
        old, new = _rendered(out, prefix), _rendered(fresh, prefix)
        if old is None or new is None:
            continue

        lines, nl = out.splitlines(), fresh.splitlines()
        out = "\n".join(lines[:old[0]] + nl[new[0]:new[1] + 1] + lines[old[1] + 1:])
        replaced.append(prefix)

    return out, replaced


archived, rerendered = _rerendered(archived)
user = build_narrative_user_prompt(archived, judgment)

# THE ARCHIVE IS THE PROMPT AS IT WAS BUILT THAT DAY, not as today's code
# would build it. A block changed since the archive was written reproduces in
# its OLD form, silently, and the reader then audits a prompt that no longer
# exists. There is no general fix — the guidance inputs are not stored, so
# the user message cannot be rebuilt — so this says so instead of pretending.
stale = [
    name for name, marker in (
        ("WIND DIRECTION", "Say nothing about direction."),
        ("the Overview's comparison", '"overview_comparison"'),
    )
    if marker in user
]
if stale:
    print("\n  WARNING: this archive predates a change to " + ", ".join(stale) + ".")
    print("  Those blocks reproduce in their OLD form. Splice the current block in,")
    print("  or use an archive written after the change, before trusting an audit")
    print("  of them. ROADMAP item 77.")
(out / "system.txt").write_text(system)
(out / "user.txt").write_text(user)
print(f"system {len(system):,} chars / {len(system.splitlines())} lines")
print(f"user   {len(user):,} chars / {len(user.splitlines())} lines")
print(f"extended_properties reconstructed: {extended}")
print("re-rendered by current code: " + (", ".join(rerendered) or "NOTHING — check the block markers"))

# WHAT THE DEPLOYMENT SUPPLIES THAT THESE TWO FILES DO NOT. Tell the reader,
# or it reports each as a missing rule and the finding is an artefact. Four
# such have now been chased: `hours_old`, a case-sensitive PEAK UV INDEX
# match, `onset_hour`/`target_date`, and the response schema below.
print("""
  TELL THE READER, or it will report these as defects:
  - THE RESPONSE SCHEMA IS NOT IN THE PROMPT. It is attached to the API call
    out of band — forced tool use on Anthropic, `response_format.json_schema`
    elsewhere — from a Pydantic model. "Return ONLY valid JSON adhering
    strictly to the requested schema" refers to that, not to anything in
    these files.
  - THE PEAK WIND FIELDS BELONG TO THE EARLIER CALL. The narrative schema has
    no `peak_wind_primary_kmh`; rules naming it address the judgment call.
  - THE CAPE THRESHOLD IS NOT PUBLISHED. `models_above_threshold` is
    pre-computed; the cut is not stated and is not meant to be re-derived.
  - CITED ITEMS AND DATED INCIDENTS ARE NOT SUPPLIED. ROADMAP references are
    provenance for the rule beside them, not documents the reader is missing.
""")
