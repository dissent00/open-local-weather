#!/usr/bin/env python3
"""Writes docs-internal/OBSERVATION_REGISTER.md from the code itself.

ROADMAP item 84. Every gap in this project's variable coverage has been found
the same way — from a bad output, after the fact. Humidity, cloud, lightning,
fog, wind chill: four separate investigations, and the answer each time was a
fact that does not change day to day, with nowhere to look it up.

GENERATED, NOT WRITTEN, and that is the whole point. Item 84 asked for a
static table. A static table is what let `cloud_cover` be fetched in three
separate places and discarded in all three for months — the answer was
knowable and nobody knew it. Anything derivable from the code is derived here,
so the register cannot drift from what the code does; a test regenerates it
and fails if the committed copy disagrees.

WHAT IS NOT DERIVED is the last column. Whether a variable SHOULD be observed,
and what it would cost, is a judgement, and judgements are written by hand
below and marked as such.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.comparison import DayOverDayComparison  # noqa: E402
from openlocalweather.config import load_location_config  # noqa: E402
from openlocalweather.fetch.open_meteo import (  # noqa: E402
    AIR_QUALITY_HOURLY_VARS,
    ARCHIVE_HOURLY_VARS,
    DAILY_FORECAST_VARS,
    HOURLY_FORECAST_VARS,
)
from openlocalweather.llm.prompt import (  # noqa: E402
    build_judgment_prompt,
    build_narrative_prompt,
)
from openlocalweather.models import DailyActual, ModelPrediction, VerificationScore  # noqa: E402

OUT = ROOT / "docs-internal" / "OBSERVATION_REGISTER.md"

# (display name, api variable fragments, DailyActual field, ModelPrediction
#  field, VerificationScore field, prompt search terms, day-over-day label)
#
# THE LABEL IS NAMED, NOT MATCHED. A first version guessed it by comparing
# substrings and gave "sustained wind" the gust label, which is precisely the
# kind of quiet wrongness a register exists to stop.
#
# THE ABSENT ROWS ARE THE POINT — item 84. A table listing only what exists
# answers no question anyone has asked, so humidity, dew point, visibility,
# fog and wind chill are here with empty columns, which is the answer.
VARIABLES = [
    ("temperature", ("temperature_2m",), "high_c", "high_c", "high_error_c", ("temperature",), "high_label"),
    ("overnight low", ("temperature_2m",), "low_c", "low_c", "low_error_c", ("low",), None),
    ("wind gust", ("wind_gusts_10m",), "peak_wind_kmh", "wind_kmh", "wind_error_kmh", ("gust",), "wind_label"),
    ("sustained wind", ("wind_speed_10m", "windspeed_10m"), "station_peak_wind_kmh", None, None, ("sustained",), None),
    ("precipitation amount", ("precipitation",), "precip_mm", "precip_mm", None, ("precip",), None),
    ("rain boolean", ("precipitation",), "rain", "rain", "rain_correct", ("rain",), None),
    ("rain probability", ("precipitation_probability",), None, "rain_probability_pct", "rain_brier", ("probability",), None),
    ("rain onset", ("precipitation",), "onset_hour", "onset", "onset_error_hrs", ("onset",), None),
    ("pressure / MSLP", ("pressure_msl",), "mslp_trend", "mslp_trend", "mslp_error_hpa", ("hPa", "MSLP"), None),
    ("cloud cover", ("cloud_cover",), "cloud_cover_pct", "cloud_cover_pct", None, ("cloud",), "cloud_label"),
    ("thunder", (), "thunder", None, None, ("thunder",), None),
    ("CAPE / instability", ("cape",), None, None, None, ("CAPE",), None),
    ("lightning", (), "lightning", None, None, ("lightning",), None),
    ("air quality", ("us_aqi", "pm2_5"), None, None, None, ("AQI",), None),
    ("UV index", ("uv_index",), None, None, None, ("UV",), None),
    ("humidity", (), None, None, None, ("humidity",), None),
    ("dew point", (), None, None, None, ("dew point",), None),
    ("apparent temperature", (), None, None, None, ("feels like",), None),
    ("visibility", (), None, None, None, ("visibility",), None),
    ("fog", (), None, None, None, ("fog",), None),
    ("wind chill", (), None, None, None, ("wind chill",), None),
]

# Hand-written, and marked so. Not derivable: this is what the variable would
# COST and whether it is worth it.
# THE TWO LOAD-BEARING COLUMNS NEED THREE VALUES EACH, not two — item 84 names
# them as the pair that decides whether a prompt edit is legal.
#
# "in prompt: yes" was true for humidity, because the prompt names it in a
# PROHIBITION. A register that answers "may the forecaster mention this?" with
# yes because the answer is written down as no is worse than no register.
#
# "observed: no" was true for dew point, which is in every METAR and parsed by
# nothing. "There is no source" and "there is a source we discard" are
# different answers to the question anyone is actually asking.
BANNED_FROM_THE_FORECAST = {"humidity", "dew point", "apparent temperature", "visibility"}
AVAILABLE_BUT_UNPARSED = {"dew point": "METAR", "visibility": "METAR"}

NOTES = {
    "sustained wind": "METAR `sknt` only; never scored — the scored column is gusts. Item 86.",
    "cloud cover": "Observed from METAR sky groups in eighths since 2026-09-09; the comparison uses percent on both sides. Items 87, 65.",
    "thunder": "METAR only. The one convective signal a station can actually catch — item 42.",
    "CAPE / instability": "Forecast-only, deliberately: nothing observes potential energy. Drives the convective flag.",
    "lightning": "Stored with nothing to score it against, on purpose. Item 65.",
    "air quality": "Ground stations plus CAMS; not scored, and the two disagree — see item 98's pattern.",
    "UV index": "Two of five models supply it and their series are identical. Not scored.",
    "humidity": "Absent. A run once wrote 'warm and humid' from nothing, which is why the prompt bans it.",
    "dew point": "In the METAR and parsed by nothing. Withheld from the forecast as a point observation. Item 87.",
    "visibility": "In the METAR and parsed by nothing. The fog signal, unused. Items 87, 84.",
    "fog": "Absent entirely. Visibility plus present weather is how it is actually observed.",
    "wind chill": "Absent. Not meaningful at this latitude; a fork at altitude would want it.",
    "rain boolean": "MEANS DIFFERENT THINGS AT DIFFERENT LEADS — hourly peak at Day+0, daily total beyond. Item 97.",
}


def main() -> None:
    # Both calls' instructions. The register asks whether a variable is
    # MENTIONED to the forecaster at all, which since ROADMAP item 59
    # step 3 means either of the two prompts.
    location = load_location_config(str(ROOT / "config/location.yaml"))
    prompt = build_judgment_prompt(location) + "\n" + build_narrative_prompt(location)
    forecast_vars = HOURLY_FORECAST_VARS + "," + DAILY_FORECAST_VARS + "," + AIR_QUALITY_HOURLY_VARS
    labels = {f for f in DayOverDayComparison.__dataclass_fields__ if f.endswith("_label")}

    rows = []
    for name, api, actual_f, pred_f, score_f, terms, label in VARIABLES:
        forecast = any(v in forecast_vars for v in api) if api else False
        observed = (any(v in ARCHIVE_HOURLY_VARS for v in api) if api else False) or (
            actual_f is not None and actual_f in DailyActual.model_fields
        )
        stored = []
        if actual_f and actual_f in DailyActual.model_fields:
            stored.append("actual")
        if pred_f and pred_f in ModelPrediction.model_fields:
            stored.append("forecast")
        scored = bool(score_f and score_f in VerificationScore.model_fields)
        in_prompt = any(t.lower() in prompt.lower() for t in terms)
        if label is not None and label not in labels:
            raise SystemExit(f"{name}: names a day-over-day label the comparison does not have: {label}")
        rows.append((name, forecast, observed, "+".join(stored) or "—", scored, in_prompt, label or "—"))

    def tick(b):
        return "yes" if b else "**no**"

    def observed_cell(name, ok):
        if ok:
            return "yes"
        source = AVAILABLE_BUT_UNPARSED.get(name)
        return f"**unparsed** ({source})" if source else "**no**"

    def prompt_cell(name, present):
        if name in BANNED_FROM_THE_FORECAST:
            return "**banned**"
        return tick(present)

    lines = [
        "<!-- GENERATED by spec/generate_observation_register.py — do not edit. -->",
        "<!-- Run that script after changing what is fetched, stored or scored. -->",
        "",
        "# Observation register",
        "",
        "ROADMAP item 84. One row per weather variable, answering the questions that",
        "have actually been asked — each of them, historically, only after a bad",
        "output made someone go and read the code.",
        "",
        "**Generated from the code.** Every column but the last is derived, so this",
        "cannot drift from what the system does; a test regenerates it and fails if",
        "the committed copy disagrees. That matters because a hand-written version is",
        "exactly what let `cloud_cover` be fetched in three places and discarded in",
        "all three.",
        "",
        "**The empty rows are the point.** A register listing only what exists answers",
        "no question anyone has asked.",
        "",
        "| variable | forecast | observed | stored | scored | in prompt | day-over-day |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, f, o, s, sc, p, d in rows:
        lines.append(
            f"| {name} | {tick(f)} | {observed_cell(name, o)} | {s} | {tick(sc)} | "
            f"{prompt_cell(name, p)} | {d} |"
        )

    lines += ["", "## Notes (hand-written — judgement, not derived)", ""]
    for name in [r[0] for r in rows]:
        if name in NOTES:
            lines.append(f"- **{name}** — {NOTES[name]}")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT} — {len(rows)} variables")


if __name__ == "__main__":
    main()
