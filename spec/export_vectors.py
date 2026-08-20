#!/usr/bin/env python3
"""Exports the correctness-critical pipeline logic as language-neutral JSON
test vectors, so a port in another language can be proven to agree with
this implementation rather than merely believed to.

WHY THIS EXISTS: the planned Flutter app (docs-internal/APP_ARCHITECTURE.md)
reimplements ~431 lines of deterministic forecast math in Dart —
extraction, scoring, verification, AQI staleness. Those numbers are the
whole basis of this project's claim to trustworthiness. A subtle
divergence between the two implementations would not crash; it would
silently produce WRONG ACCURACY STATISTICS that then get fed to the LLM as
its "track record". That is the worst failure mode available here, because
it looks fine.

WHERE THE AUTHORITY COMES FROM — read this before trusting the output.
This script does NOT independently verify that the Python implementation
is correct; it captures what Python currently does. The expected values
are trustworthy because the same behaviour is separately pinned by the
hand-computed unit tests (tests/test_scoring.py, test_extract.py,
test_aqi.py, test_dates.py — 56 tests whose expectations were worked out
by hand, not generated). The cases below deliberately mirror those tests'
edge cases so that hand-verified expectations carry through.

So the guarantees are layered, and neither alone is enough:
  - the unit tests assert Python is RIGHT
  - these vectors assert any other implementation MATCHES Python
  - tests/test_vectors.py asserts Python still matches its own exported
    vectors, so they can't silently rot as the code changes

Regenerate with:  python spec/export_vectors.py
Committing the result is intentional — the vectors are the contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from openlocalweather.aqi import STALE_THRESHOLD_HOURS, hours_old, is_stale, summarize_ground_aqi
from openlocalweather.dates import add_days, prediction_row_date_for_target


def _iso(d):
    return d.isoformat()
from openlocalweather.defaults import (
    HISTORICAL_LOOKBACK_DAYS,
    RAIN_THRESHOLD_MM,
    ROLLING_WINDOW_LONG,
    ROLLING_WINDOW_SHORT,
)
from openlocalweather.extract import (
    extract_day0_predictions_from_hourly,
    extract_day_n_predictions_from_daily,
)
from openlocalweather.fetch.open_meteo import bucket_hourly_by_date, get_onset_hour
from openlocalweather.models import DailyActual, GroundAQIReading, ModelPrediction
from openlocalweather.comparison import compute_day_over_day
from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.llm.prompt import build_system_prompt, build_user_prompt
from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    to_gemini_schema,
    to_strict_json_schema,
)
from openlocalweather.verify.scoring import compute_rain_pct_trend, mean, score_prediction

VECTOR_FORMAT_VERSION = 1
OUT_DIR = Path(__file__).resolve().parent / "vectors"


def dump(value: Any) -> Any:
    """Serializes a result into plain JSON types. Pydantic models go through
    model_dump(mode="json") so datetimes/dates become ISO strings rather
    than Python objects — a port must see exactly what lands in the file."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [dump(v) for v in value]
    return value


def write(filename: str, function: str, description: str, cases: list[dict]) -> None:
    payload = {
        "vector_format_version": VECTOR_FORMAT_VERSION,
        "function": function,
        "description": description,
        "cases": cases,
    }
    path = OUT_DIR / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"  {path.relative_to(Path(__file__).resolve().parents[1])}: {len(cases)} cases")


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def export_dates() -> None:
    cases = []
    for target, lead in [
        ("2026-08-11", 0),
        ("2026-08-11", 3),
        ("2026-08-11", 7),
        # Month and year boundaries — the classic place off-by-one date math
        # goes wrong, and cheap to pin.
        ("2026-03-01", 1),
        ("2026-01-01", 7),
        ("2026-03-02", 3),  # crosses a non-leap February
    ]:
        d = date.fromisoformat(target)
        cases.append(
            {
                "name": f"target={target} lead={lead}",
                "input": {"target_date": target, "lead_time_days": lead},
                "expected": prediction_row_date_for_target(d, lead).isoformat(),
            }
        )
    write(
        "dates.json",
        "prediction_row_date_for_target",
        "The date of the log entry that MADE a prediction targeting target_date "
        "at the given lead time. A prediction made on D targets D+k, so the row "
        "is dated (target_date - k).",
        cases,
    )

    add_cases = []
    for start, n in [("2026-08-11", 1), ("2026-08-11", -1), ("2026-12-31", 1), ("2026-03-01", -1)]:
        add_cases.append(
            {
                "name": f"{start} + {n}d",
                "input": {"date": start, "days": n},
                "expected": add_days(date.fromisoformat(start), n).isoformat(),
            }
        )
    write("dates_add_days.json", "add_days", "Calendar day arithmetic.", add_cases)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def pred(**kw) -> ModelPrediction:
    base = dict(model="gfs_seamless", rain=True, onset="14:00", wind_kmh=20.0, high_c=26.0, low_c=18.0, mslp_trend=-1.0)
    base.update(kw)
    return ModelPrediction(**base)


def act(**kw) -> DailyActual:
    base = dict(rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0, onset_hour="14:00")
    base.update(kw)
    return DailyActual(**base)


def export_scoring() -> None:
    scenarios: list[tuple[str, ModelPrediction | None, DailyActual | None, int]] = [
        ("nothing to score when prediction missing", None, act(), 0),
        ("nothing to score when actual missing", pred(), None, 0),
        # rain=None means the model had no data at this lead time. Scoring it
        # would invent skill out of a gap — the single most important case here.
        ("unknown rain (no model data) is not scoreable", pred(rain=None), act(rain=True), 0),
        ("rain correct when both true", pred(rain=True), act(rain=True), 0),
        ("rain correct when both false", pred(rain=False), act(rain=False), 0),
        ("rain incorrect when mismatched", pred(rain=True), act(rain=False), 0),
        # Error sign convention is actual - predicted. A port getting this
        # backwards would invert every bias reading in the track record.
        (
            "error signs are actual minus predicted",
            pred(wind_kmh=20.0, high_c=26.0, low_c=18.0, mslp_trend=-1.0),
            act(peak_wind_kmh=25.0, high_c=24.0, low_c=19.0, mslp_trend=0.5),
            0,
        ),
        ("onset error at lead 0", pred(onset="14:00"), act(onset_hour="16:30"), 0),
        ("onset error is never computed beyond lead 0", pred(onset="14:00"), act(onset_hour="16:30"), 3),
        ("no onset error when actual stayed dry", pred(onset="14:00"), act(rain=False, onset_hour=None), 0),
        ("no onset error when prediction had no onset", pred(onset=None), act(onset_hour="16:30"), 0),
        (
            "missing fields propagate as null, not zero",
            pred(wind_kmh=None, high_c=None, low_c=None, mslp_trend=None),
            act(peak_wind_kmh=None, high_c=None, low_c=None, mslp_trend=None),
            0,
        ),
    ]
    cases = [
        {
            "name": name,
            "input": {"predicted": dump(p), "actual": dump(a), "lead_time_days": lead},
            "expected": dump(score_prediction(p, a, lead)),
        }
        for name, p, a, lead in scenarios
    ]
    write(
        "scoring_score_prediction.json",
        "score_prediction",
        "Scores one model's stored prediction against one day's actual. Returns "
        "null when there is nothing to score. Error fields are (actual - predicted).",
        cases,
    )

    mean_cases = [
        {"name": "filters nulls", "input": {"values": [1.0, None, 3.0]}, "expected": mean([1.0, None, 3.0])},
        {"name": "all null is null", "input": {"values": [None, None]}, "expected": mean([None, None])},
        {"name": "empty is null", "input": {"values": []}, "expected": mean([])},
        {"name": "negatives", "input": {"values": [-2.0, 1.0]}, "expected": mean([-2.0, 1.0])},
    ]
    write("scoring_mean.json", "mean", "Arithmetic mean ignoring nulls; null when nothing is present.", mean_cases)

    trend_args = dict(min_checks_short=5, min_checks_long=10, threshold_pct=15.0)
    trend_scenarios = [
        ("insufficient short window", 80.0, 60.0, 4, 20),
        ("insufficient long window", 80.0, 60.0, 10, 9),
        ("null short pct", None, 60.0, 10, 20),
        ("null long pct", 80.0, None, 10, 20),
        ("improving", 90.0, 60.0, 10, 20),
        ("declining", 40.0, 70.0, 10, 20),
        ("stable within threshold", 65.0, 60.0, 10, 20),
        ("exactly at threshold counts as improving", 75.0, 60.0, 10, 20),
        ("exactly at negative threshold counts as declining", 45.0, 60.0, 10, 20),
    ]
    trend_cases = []
    for name, short, long_, n_short, n_long in trend_scenarios:
        label, delta = compute_rain_pct_trend(short, long_, n_short, n_long, **trend_args)
        trend_cases.append(
            {
                "name": name,
                "input": {
                    "rolling_10_rain_pct": short,
                    "rolling_30_rain_pct": long_,
                    "checks_in_window_10": n_short,
                    "checks_in_window_30": n_long,
                    **trend_args,
                },
                "expected": {"label": label, "delta": delta},
            }
        )
    write(
        "scoring_rain_pct_trend.json",
        "compute_rain_pct_trend",
        "Deterministic recent-vs-longer-term skill comparison handed to the LLM "
        "pre-computed. Returns nulls when either window has too few checks to be "
        "meaningful. Threshold boundaries are inclusive.",
        trend_cases,
    )


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def export_extract() -> None:
    models = ["gfs_seamless", "ecmwf_ifs025"]

    hourly_normal = {
        "hourly": {
            "time": ["2026-08-11T12:00", "2026-08-11T13:00", "2026-08-11T14:00"],
            "precipitation_gfs_seamless": [0.0, 0.2, 1.4],
            "windgusts_10m_gfs_seamless": [10.0, 22.0, 18.0],
            "temperature_2m_gfs_seamless": [21.0, 26.5, 24.0],
            "pressure_msl_gfs_seamless": [1013.0, 1012.4, 1011.8],
            # Second model present but entirely null — "no data", which must
            # NOT be recorded as a confident dry forecast.
            "precipitation_ecmwf_ifs025": [None, None, None],
            "windgusts_10m_ecmwf_ifs025": [None, None, None],
            "temperature_2m_ecmwf_ifs025": [None, None, None],
            "pressure_msl_ecmwf_ifs025": [None, None, None],
        }
    }
    hourly_dry = {
        "hourly": {
            "time": ["2026-08-11T12:00", "2026-08-11T13:00"],
            "precipitation_gfs_seamless": [0.0, 0.1],
            "windgusts_10m_gfs_seamless": [5.0, 6.0],
            "temperature_2m_gfs_seamless": [20.0, 22.0],
            "pressure_msl_gfs_seamless": [1010.0, 1010.0],
        }
    }

    # The shape that hid a real bug for months: a key that is present and
    # correctly named but ALL-NULL, with the real data under a later
    # candidate. A presence-only lookup ("is it a non-empty list?") latches
    # onto the empty array and never tries the working key — no error, just a
    # model silently unscored on that variable.
    #
    # The all-null key here is the model-suffixed one and the data is under
    # the bare key, because that ordering is the one an implementation tries
    # first. A vector whose all-null key is only ever reached SECOND cannot
    # fail, whichever way the lookup is written.
    hourly_gust_alias = {
        "hourly": {
            "time": ["2026-08-11T12:00", "2026-08-11T13:00"],
            "precipitation_ecmwf_ifs025": [0.0, 0.0],
            "wind_gusts_10m_ecmwf_ifs025": [None, None],
            "wind_gusts_10m": [11.2, 18.4],
            "temperature_2m_ecmwf_ifs025": [None, None],
            "temperature_2m": [19.0, 21.0],
            "pressure_msl_ecmwf_ifs025": [1013.0, 1011.0],
        }
    }

    cases = [
        {
            "name": "an all-null series is skipped, not latched onto",
            "input": {
                "hourly_multi_model": hourly_gust_alias,
                "models": ["ecmwf_ifs025"],
                "threshold": RAIN_THRESHOLD_MM,
            },
            "expected": dump(
                extract_day0_predictions_from_hourly(hourly_gust_alias, ["ecmwf_ifs025"], RAIN_THRESHOLD_MM)
            ),
        },
        {
            "name": "rain with onset; null series yields unknown rain",
            "input": {"hourly_multi_model": hourly_normal, "models": models, "threshold": RAIN_THRESHOLD_MM},
            "expected": dump(extract_day0_predictions_from_hourly(hourly_normal, models, RAIN_THRESHOLD_MM)),
        },
        {
            "name": "present series never crossing threshold IS a real dry call",
            "input": {"hourly_multi_model": hourly_dry, "models": ["gfs_seamless"], "threshold": RAIN_THRESHOLD_MM},
            "expected": dump(extract_day0_predictions_from_hourly(hourly_dry, ["gfs_seamless"], RAIN_THRESHOLD_MM)),
        },
        {
            "name": "empty payload yields no predictions",
            "input": {"hourly_multi_model": {}, "models": models, "threshold": RAIN_THRESHOLD_MM},
            "expected": dump(extract_day0_predictions_from_hourly({}, models, RAIN_THRESHOLD_MM)),
        },
    ]
    write(
        "extract_day0.json",
        "extract_day0_predictions_from_hourly",
        "Day+0 per-model extraction from hourly data. An absent or all-null "
        "precipitation series means unknown (null) rain, never false.",
        cases,
    )

    daily = {
        "daily": {
            "time": ["2026-08-11", "2026-08-12", "2026-08-13"],
            "precipitation_sum_gfs_seamless": [0.0, 3.2, 0.1],
            "windgusts_10m_max_gfs_seamless": [25.0, 31.0, 20.0],
            "temperature_2m_max_gfs_seamless": [28.0, 26.0, 29.0],
            "temperature_2m_min_gfs_seamless": [17.0, 18.0, 17.5],
            "pressure_msl_mean_gfs_seamless": [1014.0, 1012.5, 1013.0],
            # Shorter arrays: this model's horizon doesn't reach index 2,
            # exactly like UKMO at Day+7 in production.
            "precipitation_sum_ecmwf_ifs025": [0.0, 1.0],
            "windgusts_10m_max_ecmwf_ifs025": [20.0, 22.0],
            "temperature_2m_max_ecmwf_ifs025": [27.0, 26.0],
            "temperature_2m_min_ecmwf_ifs025": [16.0, 17.0],
            "pressure_msl_mean_ecmwf_ifs025": [1013.0, 1012.0],
        }
    }
    day_n_cases = []
    for idx, label in [(0, "day 0 has no previous day for mslp trend"), (1, "mid-range day"), (2, "beyond one model's horizon")]:
        day_n_cases.append(
            {
                "name": f"index {idx} — {label}",
                "input": {"daily_multi_model": daily, "day_index": idx, "models": models, "threshold": RAIN_THRESHOLD_MM},
                "expected": dump(extract_day_n_predictions_from_daily(daily, idx, models, RAIN_THRESHOLD_MM)),
            }
        )
    write(
        "extract_day_n.json",
        "extract_day_n_predictions_from_daily",
        "Day+N per-model extraction from daily aggregates. No onset at this "
        "resolution by design. An index past a model's array means unknown "
        "(null) rain — its forecast horizon does not reach that far.",
        day_n_cases,
    )

    onset_cases = [
        {
            "name": "first crossing hour",
            "input": {"times": ["2026-08-11T12:00", "2026-08-11T13:00"], "precip": [0.1, 0.9], "threshold": RAIN_THRESHOLD_MM},
            "expected": get_onset_hour(["2026-08-11T12:00", "2026-08-11T13:00"], [0.1, 0.9], RAIN_THRESHOLD_MM),
        },
        {
            "name": "never crosses",
            "input": {"times": ["2026-08-11T12:00"], "precip": [0.1], "threshold": RAIN_THRESHOLD_MM},
            "expected": get_onset_hour(["2026-08-11T12:00"], [0.1], RAIN_THRESHOLD_MM),
        },
        {
            "name": "nulls treated as zero",
            "input": {"times": ["2026-08-11T12:00", "2026-08-11T13:00"], "precip": [None, 2.0], "threshold": RAIN_THRESHOLD_MM},
            "expected": get_onset_hour(["2026-08-11T12:00", "2026-08-11T13:00"], [None, 2.0], RAIN_THRESHOLD_MM),
        },
    ]
    write("extract_onset_hour.json", "get_onset_hour", "First HH:MM whose precipitation crossed the threshold.", onset_cases)


# ---------------------------------------------------------------------------
# ground AQI staleness
# ---------------------------------------------------------------------------


def export_aqi() -> None:
    now = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)

    def reading(name: str, aqi, hours_ago: float | None) -> GroundAQIReading:
        measured = None if hours_ago is None else datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc).fromtimestamp(
            now.timestamp() - hours_ago * 3600, tz=timezone.utc
        )
        return GroundAQIReading(name=name, station_id=name.lower(), aqi=aqi, pm25=None, pm10=None, measured_at=measured)

    fresh = reading("Airport", 42, 0.5)
    stale = reading("Beach", 171, 7.2)
    no_ts = reading("Unknown", 99, None)
    no_aqi = reading("NoComposite", None, 0.5)

    staleness_cases = []
    for r, label in [(fresh, "fresh"), (stale, "stale"), (no_ts, "no timestamp"), (no_aqi, "fresh but no composite aqi")]:
        staleness_cases.append(
            {
                "name": label,
                "input": {"reading": dump(r), "now": now.isoformat(), "stale_threshold_hours": STALE_THRESHOLD_HOURS},
                "expected": {"hours_old": hours_old(r, now), "is_stale": is_stale(r, now)},
            }
        )
    write(
        "aqi_staleness.json",
        "hours_old / is_stale",
        "Reading freshness. A missing timestamp is UNKNOWN freshness and is "
        "treated as stale — never assumed fresh.",
        staleness_cases,
    )

    summary_scenarios = [
        ("two fresh stations", [fresh, stale.model_copy(update={"measured_at": fresh.measured_at})]),
        ("stale station excluded from range but counted", [fresh, stale]),
        ("all stale yields null summary", [stale]),
        ("no numeric aqi yields null summary", [no_aqi]),
        ("empty list yields null summary", []),
        ("unknown-freshness station excluded", [fresh, no_ts]),
    ]
    summary_cases = []
    for name, readings in summary_scenarios:
        summary_cases.append(
            {
                "name": name,
                "input": {"readings": [dump(r) for r in readings], "now": now.isoformat()},
                "expected": dump(summarize_ground_aqi(readings, now)),
            }
        )
    write(
        "aqi_summary.json",
        "summarize_ground_aqi",
        "Deterministic range and worst-station summary across ground stations. "
        "Stale and unknown-freshness readings are excluded from the range but "
        "still counted in stations_stale/stations_total.",
        summary_cases,
    )



def export_bucketing() -> None:
    """bucket_hourly_by_date defines what "actually happened" on a given day,
    which every verification score is measured against. It belongs in the
    vectors for exactly the same reason score_prediction does — it was
    missed in the first pass and added when the Dart port reached it."""
    multi_day = {
        "hourly": {
            "time": [
                "2026-08-11T00:00", "2026-08-11T12:00", "2026-08-11T23:00",
                "2026-08-12T00:00", "2026-08-12T13:00",
            ],
            "temperature_2m": [18.0, 27.5, 19.0, 17.0, 29.0],
            "precipitation": [0.0, 1.2, 0.0, 0.0, 0.1],
            "windgusts_10m": [11.0, 30.0, 14.0, 9.0, 21.0],
            "pressure_msl": [1013.0, 1011.5, 1012.0, 1014.0, 1013.0],
        }
    }
    # windgusts present but partially null: the ARRAY's presence is what
    # counts, so windspeed must NOT be substituted in for the null hours.
    gusts_with_nulls = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00"],
            "temperature_2m": [18.0, 19.0],
            "precipitation": [0.0, 0.0],
            "windgusts_10m": [None, 25.0],
            "windspeed_10m": [99.0, 99.0],
            "pressure_msl": [1013.0, 1013.5],
        }
    }
    # windgusts absent entirely: only then does windspeed_10m apply.
    speed_fallback = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00"],
            "temperature_2m": [18.0, 19.0],
            "precipitation": [0.0, 0.0],
            "windspeed_10m": [12.0, 16.0],
            "pressure_msl": [1013.0, 1013.5],
        }
    }
    scenarios = [
        ("multi-day split, onset and aggregates per day", multi_day),
        ("gust array present with nulls — no windspeed substitution", gusts_with_nulls),
        ("gust array absent — falls back to windspeed", speed_fallback),
        ("empty payload yields no days", {}),
    ]
    cases = []
    for name, payload in scenarios:
        result = bucket_hourly_by_date(payload, RAIN_THRESHOLD_MM)
        cases.append(
            {
                "name": name,
                "input": {"hourly_json": payload, "threshold": RAIN_THRESHOLD_MM},
                # Keyed by ISO date so the shape is language-neutral.
                "expected": {d.isoformat(): dump(v) for d, v in sorted(result.items())},
            }
        )
    write(
        "bucket_hourly_by_date.json",
        "bucket_hourly_by_date",
        "Splits a flat multi-day hourly archive response into one DailyActual "
        "per calendar date — the definition of 'what actually happened' that "
        "every verification score is measured against. Wind uses the "
        "windgusts_10m ARRAY if it is present at all (even where individual "
        "hours are null); windspeed_10m applies only when the gust array is "
        "absent entirely.",
        cases,
    )



def export_llm_schemas() -> None:
    """Exports the exact provider-dialect schemas generated from the forecast
    response model.

    Python DERIVES these from the pydantic class; a port has no pydantic, so
    it will DECLARE them by hand. This vector is what makes that safe: the
    two must be byte-identical, so both implementations send the same
    structured-output contract to the same APIs. If the pydantic model
    changes, regenerating moves this file and the port's test fails until it
    is updated to match — which is the intended direction of travel.
    """
    cases = [
        {
            "name": "gemini responseSchema dialect",
            "input": {"model": "GeminiForecastResponse"},
            "expected": to_gemini_schema(GeminiForecastResponse),
        }
    ]
    write(
        "llm_schema_gemini.json",
        "to_gemini_schema",
        "Gemini's responseSchema dialect: uppercase type names, a `nullable` "
        "flag, and no $ref/$defs (inlined).",
        cases,
    )

    strict_cases = [
        {
            "name": "strict JSON Schema dialect (OpenAI + Anthropic tools)",
            "input": {"model": "GeminiForecastResponse"},
            "expected": to_strict_json_schema(GeminiForecastResponse),
        }
    ]
    write(
        "llm_schema_strict.json",
        "to_strict_json_schema",
        "Standard JSON Schema as OpenAI strict mode and Anthropic tool "
        "input_schema require: lowercase types, null as a type union, "
        "additionalProperties false, and EVERY property listed in required.",
        strict_cases,
    )



def export_user_prompt() -> None:
    """The per-run message. Locked verbatim for the same reason as the system
    prompt, plus one specific to this half: every "Unavailable — ..." string
    is load-bearing. A missing input must READ as missing, and an
    implementation that quietly emitted an empty list or "null" instead would
    invite the model to treat a gap as a measurement."""
    weather = {
        "primary_today_hourly": {"hourly": {"time": ["2026-08-19T00:00"], "precipitation_gfs_seamless": [0.4]}},
        "primary_extended_daily": {"daily": {"time": ["2026-08-19"], "precipitation_sum_gfs_seamless": [2.1]}},
        "secondary_today_hourly": None,
        "secondary_extended_daily": None,
        "regional_pressure": {"points": [{"name": "Kisumu", "mslp": 1012.4}]},
        "air_quality": {"hourly": {"pm2_5": [18.0]}},
        "airport_metar": "HKKI 190600Z 09008KT CAVOK 22/17 Q1013",
        # Present in the caller's map, deliberately absent from the prompt:
        # the payload is rebuilt key-by-key so a stray key cannot enlarge it.
        "unexpected_extra_key": "must not appear",
    }
    full = {
        "today": date(2026, 8, 19),
        "yesterday": date(2026, 8, 18),
        "public_webpage_url": "https://example.com/",
        "verification_context": [{"lead_time_days": 0, "per_model_scores": {"gfs_seamless": {"rain_correct": True}}}],
        "track_record_context": [{"model": "gfs_seamless", "lead_time_days": 0, "rain_pct": 62.5}],
        "historical_logs": [{"date": "2026-08-18", "rain_expected": "Yes"}],
        "ground_aqi_readings": [{"name": "Dunga Beach", "aqi": 42}],
        "ground_aqi_summary": {"lowest": 40, "highest": 55, "worst_station": "Dunga Beach"},
        "yesterday_actual": {"high_label": "about the same", "rain_contrast": "drier"},
        "today_weather_data": weather,
        "local_bulletin_source_name": "Kenya Meteorological Department (KMD)",
        "local_bulletin_text": "Sunny intervals, light rains expected over few places.",
        "review_context": {"data_sufficiency": "Day+0: 8 check(s) per model.", "findings": []},
        "model_predictions_context": {"day0": [{"model": "kenya_met", "rain": True, "high_c": 30.0}], "day3": [], "day7": []},
    }
    refresh = dict(full, morning_narrative="Warm and dry through the morning.")
    empty = {
        "today": date(2026, 8, 19),
        "yesterday": date(2026, 8, 18),
        "public_webpage_url": "https://example.com/",
        "verification_context": [],
        "track_record_context": [],
        "historical_logs": [],
        "ground_aqi_readings": [],
        "ground_aqi_summary": None,
        "yesterday_actual": None,
        "today_weather_data": {},
        "local_bulletin_source_name": "",
        "local_bulletin_text": "",
    }

    def case(name, kwargs):
        return {
            "name": name,
            "input": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in kwargs.items()},
            "expected": build_user_prompt(**kwargs),
        }

    write(
        "llm_user_prompt.json",
        "build_user_prompt",
        "The full per-run user message, verbatim. Covers a fully-populated "
        "run, an evening refresh, and a cold start where every optional input "
        "is absent — the last matters most, because each 'Unavailable' string "
        "is what stops a gap being read as a measurement.",
        [
            case("fully populated", full),
            case("evening refresh carries the morning narrative", refresh),
            case("cold start — every optional input absent", empty),
        ],
    )


def export_weekly_review() -> None:
    """The review's findings must be IDENTICAL on both surfaces.

    Roadmap item 18: "accuracy demonstrably improving over time" is this
    project's strongest differentiator, and an app whose accuracy screen
    disagreed with the server's would destroy exactly the credibility the
    feature exists to build. The gating thresholds are the sensitive part —
    an implementation that ranked models one check earlier than the other
    would publish a claim the other withholds.
    """
    from datetime import timedelta

    from openlocalweather.models import (
        DailyLogEntry,
        LogEntryMeta,
        ModelPredictionsByLead,
    )
    from openlocalweather.review import build_weekly_review

    models = ["alpha", "beta"]
    today = date(2026, 8, 21)

    def build(days: int, alpha_hits: int, beta_hits: int, alpha_high_bias: float = 0.0):
        logs, actuals = {}, {}
        for i in range(days):
            d = today - timedelta(days=i + 1)
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=(i < alpha_hits),
                                    high_c=26.0 - alpha_high_bias, low_c=18.0),
                    ModelPrediction(model="beta", rain=(i < beta_hits), high_c=26.0, low_c=18.0),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )
        return logs, actuals

    def case(name: str, days: int, a: int, b: int, bias: float = 0.0):
        logs, actuals = build(days, a, b, bias)
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models,
            lead_times_days=[0],
        )
        return {
            "name": name,
            "input": {
                # Flat, storage-agnostic: the app keeps history in a database,
                # not JSON files, so the vector describes predictions per
                # (row date, lead time) rather than a log-entry shape.
                "predictions": {
                    _iso(d): {
                        "0": [p.model_dump() for p in e.model_predictions.day0]
                    }
                    for d, e in sorted(logs.items())
                },
                "actuals": {_iso(d): a.model_dump() for d, a in sorted(actuals.items())},
                "today": _iso(today),
                "models": models,
                "lead_times_days": [0],
            },
            "expected": {
                "period_start": _iso(review.period_start),
                "period_end": _iso(review.period_end),
                "days_with_predictions": review.days_with_predictions,
                "days_verified": review.days_verified,
                "data_sufficiency": review.data_sufficiency,
                "cells": [
                    {
                        "model": c.model, "lead_time_days": c.lead_time_days,
                        "checks": c.checks, "correct": c.correct,
                        "rain_pct": c.rain_pct, "confidence": c.confidence,
                        "mean_high_error_c": c.mean_high_error_c,
                        "mean_low_error_c": c.mean_low_error_c,
                        "mean_wind_error_kmh": c.mean_wind_error_kmh,
                        "mean_onset_error_hrs": c.mean_onset_error_hrs,
                        "mean_mslp_error_hpa": c.mean_mslp_error_hpa,
                        "earliest": _iso(c.earliest) if c.earliest else None,
                        "latest": _iso(c.latest) if c.latest else None,
                    }
                    for c in review.cells
                ],
                "findings": [
                    {"kind": f.kind, "claim": f.claim, "evidence": f.evidence,
                     "confidence": f.confidence, "checks": f.checks}
                    for f in review.findings
                ],
            },
        }

    write(
        "weekly_review.json",
        "build_weekly_review",
        "Weekly-review findings and skill cells. The gates are the sensitive "
        "part: a thin record must produce NO ranking on either surface, and a "
        "sufficient one must produce the same ranking, or the app and the "
        "pipeline would publish different claims from identical data.",
        [
            case("8 checks — a large apparent gap must still yield no ranking", 8, 8, 2),
            case("30 checks — a real gap is ranked", 30, 27, 9),
            case("30 checks — a narrow gap is explicitly declined", 30, 20, 18),
            case("30 checks — a systematic temperature bias is named", 30, 15, 15, 2.0),
            case("4 checks — insufficient for anything", 4, 4, 1),
        ],
    )


def export_synoptic() -> None:
    """The large-scale pressure description, derived in code.

    Ported so the app's Synoptic Overview reads like the site's rather than
    reverting to a bare local trend. The bounded vocabulary is the part that
    must not drift: point sampling at 12-degree spacing supports "lower
    pressure lies toward the northeast", never a named centre or a track, and
    an implementation that quietly widened that claim would overstate what
    the data can carry.
    """
    from openlocalweather.synoptic import summarize_synoptic

    def ring(**by_label):
        return {"points": [{"label": k, "mslp_hpa": v} for k, v in by_label.items()]}

    live = ring(
        centre=[1016.1, 1015.2, 1014.4], N=[1010.9, 1011.4, 1013.4],
        NE=[1006.5, 1006.3, 1005.9], E=[1016.0, 1015.7, 1015.4],
        SE=[1018.6, 1018.4, 1018.0], S=[1020.7, 1020.0, 1019.5],
        SW=[1016.9, 1016.2, 1015.5], W=[1014.6, 1013.0, 1012.7],
        NW=[1013.1, 1012.1, 1013.1],
    )
    flat = ring(
        centre=[1013.0, 1013.1, 1013.0], N=[1013.2, 1013.1, 1013.0],
        E=[1013.4, 1013.3, 1013.2], S=[1013.1, 1013.0, 1013.1],
        W=[1012.9, 1013.0, 1013.1],
    )
    # A system building to the west that is NOT yet the lowest point — the
    # signal a lowest-quadrant-only summary would miss entirely.
    approaching = ring(
        centre=[1014.0, 1013.5, 1013.0], NE=[1008.0, 1008.1, 1008.0],
        W=[1015.0, 1012.5, 1010.0], S=[1018.0, 1018.1, 1018.0],
    )
    gaps = ring(centre=[1013.0], N=[None, None])
    missing_tail = ring(centre=[1013.0], N=[1009.0], S=[1017.0])

    cases = []
    for name, payload in [
        ("live ring — low to the NE, high to the S, pressure falling west", live),
        ("flat field — weak gradient, nothing deepening", flat),
        ("a feature building to the west before it is the lowest quadrant", approaching),
        ("too few usable readings yields nothing rather than a flat field", gaps),
        ("single-sample points still describe a gradient, with no tendencies", missing_tail),
        ("absent payload", None),
    ]:
        result = summarize_synoptic(payload)
        cases.append({
            "name": name,
            "input": {"payload": payload},
            "expected": None if result is None else {
                "centre_mslp_hpa": result.centre_mslp_hpa,
                "lowest_label": result.lowest_label,
                "lowest_mslp_hpa": result.lowest_mslp_hpa,
                "highest_label": result.highest_label,
                "highest_mslp_hpa": result.highest_mslp_hpa,
                "gradient_hpa": result.gradient_hpa,
                "gradient_strength": result.gradient_strength,
                "tendencies": result.tendencies,
                "statements": result.statements,
            },
        })

    write(
        "synoptic.json",
        "summarize_synoptic",
        "Large-scale pressure reduced to labels and ready-made statements. The "
        "bounded vocabulary is the contract: a direction, never a centre, a "
        "track, or a front.",
        cases,
    )


def export_coverage() -> None:
    """Noticing when a source quietly stops supplying something.

    Ported because a standalone app has the same blind spot and less
    recourse: on the server an upstream change is a git push, in an app it
    is a store release, so knowing quickly matters more. The three-way
    classification is the sensitive part — an implementation that treated a
    peer_gap as never_published would reproduce the exact months-long
    silence this module was written to end.
    """
    from datetime import timedelta

    from openlocalweather.coverage import detect_coverage
    from openlocalweather.models import (
        DailyLogEntry,
        LogEntryMeta,
        ModelPredictionsByLead,
    )

    today = date(2026, 8, 21)
    models = ["alpha", "beta"]

    def build(days: int, alpha_wind, beta_wind):
        logs = {}
        for i in range(days):
            d = today - timedelta(days=i + 1)
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=True, high_c=26.0, low_c=18.0,
                                    mslp_trend=-1.0,
                                    wind_kmh=alpha_wind(i) if callable(alpha_wind) else alpha_wind),
                    ModelPrediction(model="beta", rain=True, high_c=26.0, low_c=18.0,
                                    mslp_trend=-1.0,
                                    wind_kmh=beta_wind(i) if callable(beta_wind) else beta_wind),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )
        return logs

    def case(name, days, alpha_wind, beta_wind):
        logs = build(days, alpha_wind, beta_wind)
        findings = detect_coverage(
            log_lookup=lambda d: logs.get(d), today=today,
            models=models, lead_times_days=[0],
        )
        return {
            "name": name,
            "input": {
                "predictions": {
                    _iso(d): {"0": [p.model_dump() for p in e.model_predictions.day0]}
                    for d, e in sorted(logs.items())
                },
                "today": _iso(today),
                "models": models,
                "lead_times_days": [0],
            },
            "expected": [
                {
                    "kind": f.kind, "model": f.model, "lead_time_days": f.lead_time_days,
                    "variable": f.variable, "absent_runs": f.absent_runs,
                    "checked_runs": f.checked_runs,
                    "last_seen": _iso(f.last_seen) if f.last_seen else None,
                    "peers_with_value": f.peers_with_value,
                }
                for f in findings
            ],
        }

    write(
        "coverage.json",
        "detect_coverage",
        "Data-coverage findings. The three-way split is the contract: a "
        "regression means something changed, a peer_gap means one model alone "
        "lacks what its peers supply (the shape that hid a real bug for "
        "months), and never_published means nothing supplies it and there is "
        "nothing to chase.",
        [
            case("one model alone lacks what its peers supply — a peer_gap", 10, None, 25.0),
            case("nothing supplies it — a property, not a fault", 10, None, None),
            case("present then absent — a regression", 12,
                 lambda i: None if i < 4 else 22.0, 25.0),
            case("a single missed run is noise", 12,
                 lambda i: None if i < 1 else 22.0, 25.0),
            case("a healthy record yields nothing", 10, 24.0, 25.0),
        ],
    )


def export_spend() -> None:
    """The spend cap's decision logic.

    Both surfaces must agree on whether a call is permitted. If the app
    counted a window differently from the pipeline, one of them would allow
    spending the other refuses — and the whole point of a hard cap is that it
    cannot be exceeded, on any surface.

    The window boundary is the sensitive part: an implementation using >=
    instead of > on the cutoff, or a calendar day instead of a rolling one,
    permits real extra spending while looking correct.
    """
    from datetime import timedelta

    from openlocalweather.spend import SpendRecord, calls_in_window, prune

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    def rec(delta: timedelta) -> SpendRecord:
        return SpendRecord(at=now - delta, provider="p", model="m", purpose="forecast")

    scenarios = [
        ("empty ledger", []),
        ("all inside the window", [rec(timedelta(hours=1)), rec(timedelta(hours=23))]),
        (
            "exactly on the boundary is OUTSIDE — the cutoff is exclusive",
            [rec(timedelta(hours=24))],
        ),
        (
            "one second inside the boundary counts",
            [rec(timedelta(hours=23, minutes=59, seconds=59))],
        ),
        (
            "old calls do not count but are still retained for a week",
            [rec(timedelta(days=3)), rec(timedelta(hours=2))],
        ),
        (
            "far-old calls are pruned entirely",
            [rec(timedelta(days=30)), rec(timedelta(days=2))],
        ),
    ]

    cases = []
    for name, records in scenarios:
        cases.append({
            "name": name,
            "input": {
                "now": now.isoformat(),
                "records": [r.to_json() for r in records],
                "window_hours": 24,
                "keep_days": 7,
            },
            "expected": {
                "calls_in_window": calls_in_window(records, now),
                "kept_after_prune": [r.to_json() for r in prune(records, now)],
            },
        })

    write(
        "spend.json",
        "calls_in_window / prune",
        "Rolling-window counting for the hard spend cap. The boundary is the "
        "sensitive part: an inclusive cutoff or a calendar day instead of a "
        "rolling window permits real extra spending while looking correct.",
        cases,
    )


def export_system_prompt() -> None:
    """The system prompt is the instruction set that shapes every forecast.
    Drift between implementations would not be a formatting nit — the app and
    the pipeline would produce genuinely different forecasts from identical
    data, which is the sort of divergence nobody would notice until the
    accuracy records disagreed."""
    plain = LocationConfig(
        region_name="Example Region",
        primary_place_name="Example Town, Country",
        timezone="UTC",
        primary_point=Point(lat=1.0, lon=2.0),
        secondary_point=SecondaryPoint(enabled=False),
    )
    with_secondary = LocationConfig(
        region_name="Nyanza Basin",
        primary_place_name="Kisumu, Kenya",
        timezone="Africa/Nairobi",
        primary_point=Point(lat=-0.1, lon=34.75),
        secondary_point=SecondaryPoint(
            enabled=True,
            name="Lake Victoria",
            section_label="Conditions for Boaters",
            lat=-0.3,
            lon=34.2,
        ),
    )

    scenarios = [
        ("no secondary point, normal run", plain, False, {}),
        ("secondary point enabled, normal run", with_secondary, False, {}),
        ("secondary point enabled, REFRESH run", with_secondary, True, {}),
        (
            "non-default window sizes interpolate",
            plain,
            False,
            {"historical_lookback_days": 14, "rolling_window_short": 5, "rolling_window_long": 20},
        ),
    ]

    cases = []
    for name, loc, is_refresh, overrides in scenarios:
        kwargs = {
            "historical_lookback_days": HISTORICAL_LOOKBACK_DAYS,
            "rolling_window_short": ROLLING_WINDOW_SHORT,
            "rolling_window_long": ROLLING_WINDOW_LONG,
        }
        kwargs.update(overrides)
        cases.append(
            {
                "name": name,
                "input": {
                    "location": {
                        "region_name": loc.region_name,
                        "primary_place_name": loc.primary_place_name,
                        "secondary_point": {
                            "enabled": loc.secondary_point.enabled,
                            "name": loc.secondary_point.name,
                            "section_label": loc.secondary_point.section_label,
                        },
                    },
                    "is_refresh": is_refresh,
                    **kwargs,
                },
                "expected": build_system_prompt(loc, is_refresh=is_refresh, **kwargs),
            }
        )
    write(
        "llm_system_prompt.json",
        "build_system_prompt",
        "The full system prompt, verbatim. Covers the secondary-point branch "
        "(present/absent), refresh mode, and window-size interpolation.",
        cases,
    )



def export_day_over_day() -> None:
    """The Overview's opening sentence. Vector-tested because a live run got
    it wrong when the LLM was left to subtract: it called a 0.1°C difference
    "about 1°C cooler"."""
    def preds(highs, lows=None, winds=None, rains=None):
        n = len(highs)
        lows = lows or [18.0] * n
        winds = winds or [20.0] * n
        rains = rains or [True] * n
        return [
            ModelPrediction(model=f"m{i}", rain=rains[i], high_c=highs[i], low_c=lows[i], wind_kmh=winds[i])
            for i in range(n)
        ]

    def actual(**kw):
        base = dict(rain=True, high_c=29.6, low_c=18.8, peak_wind_kmh=40.7, mslp_trend=-0.5, onset_hour="16:00")
        base.update(kw)
        return DailyActual(**base)

    scenarios = [
        # The exact case that broke live: a 0.1 degree difference must NOT
        # read as a change.
        ("0.1C difference reads as about the same", actual(), preds([29.5, 29.4, 29.6], winds=[37.0, 35.0, 38.0])),
        ("2C warmer is slight", actual(high_c=27.0), preds([29.0, 29.0, 29.0])),
        ("5C cooler is noticeable", actual(high_c=34.0), preds([29.0, 29.0, 29.0])),
        ("10C cooler is much", actual(high_c=39.0), preds([29.0, 29.0, 29.0])),
        ("exactly at a band boundary rounds into the higher band", actual(high_c=27.5), preds([29.0, 29.0, 29.0])),
        ("wind change below threshold is not remarked on", actual(peak_wind_kmh=25.0), preds([29.0], winds=[30.0])),
        ("big wind increase is called out", actual(peak_wind_kmh=15.0), preds([29.0], winds=[40.0])),
        ("dry after a wet day", actual(rain=True), preds([29.0], rains=[False])),
        ("wet after a dry day", actual(rain=False), preds([29.0], rains=[True])),
        ("no observed record yields nothing at all", None, preds([29.0])),
        ("model with no data doesn't poison the consensus", actual(), 
         [ModelPrediction(model="a", rain=True, high_c=29.5, low_c=18.0, wind_kmh=37.0),
          ModelPrediction(model="b", rain=None, high_c=None, low_c=None, wind_kmh=None)]),
    ]

    cases = []
    for name, y, ps in scenarios:
        result = compute_day_over_day(y, ps)
        cases.append({
            "name": name,
            "input": {
                "yesterday_actual": dump(y),
                "today_day0_predictions": [dump(p) for p in ps],
            },
            "expected": dump(result),
        })
    write(
        "day_over_day.json",
        "compute_day_over_day",
        "Deterministic day-over-day comparison for the Overview. Compares "
        "today's MODEL CONSENSUS against yesterday's OBSERVED conditions and "
        "returns felt-change BANDS rather than raw deltas, because the "
        "consensus differs slightly from the LLM's final blended call and a "
        "band is stable across that gap where a number is not.",
        cases,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Exporting cross-language test vectors:")
    export_dates()
    export_scoring()
    export_extract()
    export_aqi()
    export_bucketing()
    export_llm_schemas()
    export_system_prompt()
    export_user_prompt()
    export_weekly_review()
    export_synoptic()
    export_coverage()
    export_spend()
    export_day_over_day()
    print("\nDone. Commit the result — the vectors are the contract.")


if __name__ == "__main__":
    main()
