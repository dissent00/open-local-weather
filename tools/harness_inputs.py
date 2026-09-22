"""Build item 77's harness inputs for the NARRATIVE call, faithfully.

Two things this gets right that a hand-assembled pair does not, both learned
the hard way on 2026-09-22:

1. THE NARRATIVE CALL'S USER MESSAGE IS NOT THE ARCHIVED ONE.
   `build_narrative_user_prompt` APPENDS the judgment call's answer as "THE
   FORECASTER'S CALL", and only the base is archived. Hand a cold reader the
   bare archive and it correctly reports the block missing, then declares five
   dependent rules unfollowable — all artefacts.

2. `extended_properties` IS RECONSTRUCTABLE and must not be left empty.
   The blend's own Day+3 and Day+7 commitment is stored on the prediction row
   as the `olw_blend` model. Leaving the array empty makes the Extended
   Outlook unjudgeable, which a reader flagged before this was fixed.

Usage: python harness_inputs.py <date> <out_dir>
"""
import json, sys
from pathlib import Path

ROOT = Path.home() / "weather-app"
sys.path.insert(0, str(ROOT / "src"))
from openlocalweather.config import load_location_config
from openlocalweather.llm.prompt import build_narrative_prompt, build_narrative_user_prompt

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

judgment = {
    "today_properties": {
        "rain": True,
        "rain_expected": entry["rain_expected"],
        "onset_window": entry["onset_window"],
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
