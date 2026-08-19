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
from openlocalweather.llm.prompt import build_system_prompt
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
    export_day_over_day()
    print("\nDone. Commit the result — the vectors are the contract.")


if __name__ == "__main__":
    main()
