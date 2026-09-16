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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openlocalweather.cycle import round_hours_to_tenths
from openlocalweather.aqi import (
    STALE_THRESHOLD_HOURS,
    hours_old,
    is_stale,
    last_known_ground_aqi,
    merge_ground_aqi,
    summarize_ground_aqi,
)
from openlocalweather.comparison import describe_day_over_day, describe_day_rain
from openlocalweather.glossary import GLOSSARY
from openlocalweather.instability import CONVECTIVE_CAPE_THRESHOLD_JKG, summarize_instability
from openlocalweather.dates import weekday_name, add_days, prediction_row_date_for_target
from openlocalweather.baselines import climatology_prediction, persistence_prediction
from openlocalweather.cycle import aligned_cycle_at, next_aligned_window


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
from openlocalweather.models import (
    DailyActual,
    InformationMoved,
    ObservedSoFar,
    GroundAQIReading,
    ModelPrediction,
    format_temp_high_low,
)
from openlocalweather.calibration import calibrated_gust_consensus, gust_corrections
from openlocalweather.llm.prompt import _round_for_prompt
from openlocalweather.comparison import compute_day_over_day, comparison_subject, describe_extended_trend
from openlocalweather.observed import describe_observed_so_far
from openlocalweather.reasoning import LLMRefreshPolicy, llm_should_reason
from openlocalweather.disagreement import (
    low_divergence,
    StandingCall,
    observation_disagreements,
)
from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.llm.prompt import (
    build_judgment_prompt,
    build_narrative_prompt,
    build_user_prompt,
)
from openlocalweather.wind import consensus_direction, describe_wind_shift, vector_mean
from openlocalweather.defaults import WIND_DIRECTION_AGREEMENT_GATE
from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    GeminiJudgmentResponse,
    GeminiNarrativeResponse,
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
    print(f"  spec/vectors/{filename}: {len(cases)} cases")


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def export_weekday_name() -> None:
    """The day name that reaches the Overview inside a VERBATIM phrase.

    Pinned because `strftime("%A")` and a hand-written Dart list are two
    different implementations of one string, and item 61's clause is used
    unaltered — a name that disagreed would publish a different sentence on
    the app than on the site. A full week plus the boundaries, so an
    off-by-one in either direction shows up as a wrong name rather than a
    wrong date.
    """
    cases = []
    # A full Monday-to-Sunday run, so an index error in either direction lands
    # on a name rather than out of range.
    for day in range(7, 14):
        d = date(2026, 9, day)
        cases.append(
            {
                "name": f"{d.isoformat()}",
                "input": {"date": d.isoformat()},
                "expected": weekday_name(d),
            }
        )
    for target in ("2026-01-01", "2026-02-28", "2026-03-01", "2026-12-31"):
        d = date.fromisoformat(target)
        cases.append(
            {
                "name": f"boundary {target}",
                "input": {"date": target},
                "expected": weekday_name(d),
            }
        )
    write(
        "weekday_name.json",
        "weekday_name",
        "The English day name for a date already resolved in the location's "
        "timezone. Reaches the reader inside a phrase the prompt uses verbatim, "
        "so both languages must produce the same characters.",
        cases,
    )


def export_false_weekday_claims() -> None:
    """The pairings a narrative asserts, checked against the calendar.

    Every case here is drawn from something real or from a boundary that would
    produce a FALSE ALARM, which is the failure that matters most: a check
    nobody trusts is a check nobody reads. The year-end case is the one to keep
    — "Saturday, 2 January" written on 29 December is correct, and a naive
    implementation that resolved it against the run's own year would call it
    wrong.
    """
    from openlocalweather.claims import false_weekday_claims

    cases = []
    for label, today, text in [
        # The run that prompted this. 2026-09-16 is a Wednesday.
        ("the case that prompted it", "2026-09-13",
         "Monday (16 September) is expected to see scattered rainfall."),
        # Both historical forms, including the ordinal that the first scan missed.
        ("published 2026-08-11", "2026-08-11",
         "Rain clears by Sunday, August 17 and returns Monday, August 18."),
        ("ordinal suffix", "2026-08-13",
         "A drier spell arrives Sunday, August 17th."),
        # Correct pairings must pass silently.
        ("correct, day first", "2026-09-13", "Wednesday (16 September) stays dry."),
        ("correct, month first", "2026-09-13", "Expect showers Monday, September 14."),
        ("correct, ISO", "2026-09-13", "Rain on Monday, 2026-09-14."),
        # Year boundary, both directions. Neither is an error.
        ("crosses into next year", "2026-12-29", "Rain returns Saturday, 2 January."),
        ("crosses from last year", "2027-01-02", "Drier than Tuesday, 29 December."),
        # A weekday with no date attached is not checkable and must not fire.
        ("weekday alone", "2026-09-13", "Rain becomes likely by Wednesday."),
        # A date with no weekday, likewise.
        ("date alone", "2026-09-13", "Totals reach 23 mm by 20 September."),
        # 29 February in a non-leap year resolves to nothing; it must not raise.
        ("impossible date", "2026-02-25", "Snow on Sunday, 29 February."),
        ("nothing at all", "2026-09-13", "A quiet day with light winds."),
    ]:
        cases.append(
            {
                "name": label,
                "input": {"today": today, "text": text},
                "expected": false_weekday_claims(text, date.fromisoformat(today)),
            }
        )
    write(
        "false_weekday_claims.json",
        "false_weekday_claims",
        "Weekday/date pairings a narrative asserts that the calendar "
        "contradicts. Recorded against the entry and published anyway. A false "
        "alarm is worse than the defect, so the correct and the unresolvable "
        "cases matter as much as the wrong ones.",
        cases,
    )


def export_forward_calendar() -> None:
    """The date/day-name pairings the prompt hands over finished.

    Pinned because the model was deriving these and getting them wrong — 4 of
    24 published pairings false, measured 2026-09-13 — so the whole point is
    that both languages produce the SAME pairing for the same date. A port
    that was a day out would reintroduce the defect the block exists to close.

    Cases cross a month end, a leap-year February and a year end, which is
    where naive day arithmetic breaks.
    """
    from openlocalweather.dates import forward_calendar

    cases = []
    for label, start in [
        ("mid-month", "2026-09-13"),
        ("crosses a month end", "2026-08-28"),
        ("crosses a year end", "2026-12-29"),
        ("leap year February", "2028-02-26"),
        ("non-leap February", "2026-02-25"),
    ]:
        d = date.fromisoformat(start)
        cases.append(
            {
                "name": f"{label} — {start}",
                "input": {"today": start},
                "expected": forward_calendar(d),
            }
        )
    write(
        "forward_calendar.json",
        "forward_calendar",
        "Every day from today to Day+7 with its date and day name, handed to "
        "the prompt finished so the forecaster never maps one to the other "
        "itself. Both languages must agree on every pairing.",
        cases,
    )


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
        # THE SKY, from 2026-09-10. Same convention as every field above it,
        # and the case that matters is the model which saw no cloud at all on
        # a cloudy morning: that is the error the operator's own eyes caught
        # on 2026-09-10, and until this was scored no model could lose
        # standing for it.
        ("cloud error is actual minus predicted", pred(cloud_cover_pct=50.0), act(cloud_cover_pct=42.0), 0),
        ("a model that saw no cloud on a cloudy day scores worst",
         pred(cloud_cover_pct=0.0), act(cloud_cover_pct=42.0), 0),
        ("no cloud forecast is not zero error", pred(cloud_cover_pct=None), act(cloud_cover_pct=42.0), 0),
        ("no cloud observed is not zero error either",
         pred(cloud_cover_pct=50.0), act(cloud_cover_pct=None), 0),
        # THE INSTABILITY CALL — item 35. Scored against THUNDER, never
        # against rain: a day of steady frontal rain with no lightning is not
        # a hit for a model that called high instability, and a port that
        # scored this against observed_convection() would credit exactly that
        # case. Threshold is CONVECTIVE_CAPE_THRESHOLD_JKG = 1000 J/kg.
        ("high CAPE on a day it thundered is a hit", pred(peak_cape_jkg=1860.0), act(thunder=True), 0),
        ("low CAPE on a day it thundered is a miss", pred(peak_cape_jkg=390.0), act(thunder=True), 0),
        ("low CAPE on a calm day is a hit", pred(peak_cape_jkg=390.0), act(thunder=False), 0),
        ("high CAPE on a calm day is a miss", pred(peak_cape_jkg=1860.0), act(thunder=False), 0),
        # Rain without lightning must NOT credit an instability call — the
        # case the whole thunder-not-rain decision exists for.
        ("a wet day without thunder does not credit high CAPE",
         pred(peak_cape_jkg=1860.0), act(rain=True, precip_mm=14.0, thunder=False), 0),
        ("no CAPE forecast is not a stable call", pred(peak_cape_jkg=None), act(thunder=True), 0),
        ("no thunder observation settles nothing", pred(peak_cape_jkg=1860.0), act(thunder=None), 0),
        ("CAPE is never scored beyond lead 0", pred(peak_cape_jkg=1860.0), act(thunder=True), 3),
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
        # What a rain call is scored AGAINST — DailyActual.observed_convection,
        # not `rain` alone. These four pin the whole truth table across the two
        # station observations, because a port that reads only `rain` passes
        # every case above and still scores a convective day backwards.
        #
        # Neither observation was vector-locked before item 53; the thunder
        # path shipped on 2026-08-26 covered by Python tests alone.
        (
            "a rain call is credited when the station observed thunder",
            pred(rain=True, onset=None),
            act(rain=False, precip_mm=0.5, onset_hour=None, thunder=True),
            0,
        ),
        # The 2026-08-29 miss, as a number: reanalysis 0.0 mm, no thunder
        # heard, and the airport reporting -RA and RERA for two hours.
        (
            "a rain call is credited when the station observed rain the reanalysis missed",
            pred(rain=True, onset=None),
            act(rain=False, precip_mm=0.0, onset_hour=None, thunder=False, precipitation=True),
            0,
        ),
        (
            "a dry call is penalised on a day the station observed rain",
            pred(rain=False, onset=None),
            act(rain=False, precip_mm=0.0, onset_hour=None, thunder=False, precipitation=True),
            0,
        ),
        # No station, or a station that filed nothing: null is "not observed",
        # never "it stayed dry", so the reanalysis stays in charge and the
        # score is exactly what it was before either observation existed.
        (
            "an unobserved station leaves the reanalysis in charge",
            pred(rain=False, onset=None),
            act(rain=False, precip_mm=0.0, onset_hour=None, thunder=None, precipitation=None),
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
    hourly_spread = {
        "hourly": {
            "time": ["2026-08-11T12:00", "2026-08-11T13:00", "2026-08-11T14:00",
                     "2026-08-11T15:00", "2026-08-11T16:00"],
            "precipitation_gfs_seamless": [0.4, 0.4, 0.4, 0.4, 0.4],
            "windgusts_10m_gfs_seamless": [10.0, 12.0, 14.0, 12.0, 10.0],
            "temperature_2m_gfs_seamless": [21.0, 22.0, 23.0, 22.0, 21.0],
            "pressure_msl_gfs_seamless": [1013.0, 1012.8, 1012.6, 1012.4, 1012.2],
            "cloud_cover_gfs_seamless": [80.0, 85.0, 90.0, 85.0, 80.0],
        }
    }
    hourly_tie = {
        "hourly": {
            "time": ["2026-08-11T12:00", "2026-08-11T13:00"],
            "precipitation_gfs_seamless": [0.1, 0.025],
            "windgusts_10m_gfs_seamless": [10.0, 12.0],
            "temperature_2m_gfs_seamless": [21.0, 22.0],
            "pressure_msl_gfs_seamless": [1013.0, 1012.4],
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
    hourly_cloud_sum = {
        "hourly": {
            "time": ["2026-08-11T12:00", "2026-08-11T13:00", "2026-08-11T14:00"],
            "precipitation_gfs_seamless": [0.0, 0.0, 0.0],
            "cloud_cover_gfs_seamless": [1e16, 1.0, -1e16],
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

    # ROADMAP item 59. The bearing must come from the hour of THIS model's own
    # peak gust, so the two models here peak at different hours on purpose: a
    # port that took the first bearing, the last, or the bearing at a fixed
    # hour would pass on a fixture where the peak happens to sit there.
    hourly_bearings = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00"],
            "precipitation_gfs_seamless": [0.0, 0.0, 0.0],
            "windgusts_10m_gfs_seamless": [10.0, 31.0, 15.0],
            "wind_direction_10m_gfs_seamless": [10.0, 225.0, 300.0],
            "precipitation_ecmwf_ifs025": [0.0, 0.0, 0.0],
            "windgusts_10m_ecmwf_ifs025": [8.0, 12.0, 40.0],
            "wind_direction_10m_ecmwf_ifs025": [20.0, 90.0, 230.0],
            # Third model: gusts but no bearing series at all. Null, not north.
            "precipitation_icon_seamless": [0.0, 0.0, 0.0],
            "windgusts_10m_icon_seamless": [9.0, 14.0, 22.0],
        }
    }

    cases = [
        {
            "name": "the bearing comes from each model's own peak-gust hour",
            "input": {
                "hourly_multi_model": hourly_bearings,
                "models": ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"],
                "threshold": RAIN_THRESHOLD_MM,
            },
            "expected": dump(extract_day0_predictions_from_hourly(
                hourly_bearings, ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"], RAIN_THRESHOLD_MM
            )),
        },
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
            # ROUNDING TIE, item 89. The day total is round(sum(...), 2) in
            # Python and was (sum * 100).roundToDouble() / 100 in Dart, which
            # rounds half AWAY FROM ZERO. 0.125 is an exact tie at two
            # decimals, so Python gives 0.12 and the old Dart gave 0.13.
            #
            # CONSTRUCTED, NOT OBSERVED. Open-Meteo returns precipitation on a
            # 0.1/0.01 grid and a plain SUM of such values was never seen
            # landing on an exact eighth — 0 divergences in 13,500 swept days
            # and in 400,000 targeted searches. Unlike the day-over-day
            # deltas, which are MEANS and hit a tie on ~3.6% of real days,
            # this one needs an input the feed does not produce. The case is
            # here to pin the contract, not to reproduce a sighting.
            "name": "a precipitation total on an exact rounding tie",
            "input": {"hourly_multi_model": hourly_tie, "models": ["gfs_seamless"], "threshold": RAIN_THRESHOLD_MM},
            "expected": dump(extract_day0_predictions_from_hourly(hourly_tie, ["gfs_seamless"], RAIN_THRESHOLD_MM)),
        },
        {
            # ROADMAP item 97. 2.0 mm spread across five hours, none reaching
            # 0.5 — the shape that used to be `rain: false` here and
            # `rain: true` at Day+3 from the identical total. Six stored
            # predictions carried it, including today's ECMWF and Best Match.
            "name": "a day that rained without a heavy hour is still a rain day",
            "input": {"hourly_multi_model": hourly_spread, "models": ["gfs_seamless"], "threshold": RAIN_THRESHOLD_MM},
            "expected": dump(extract_day0_predictions_from_hourly(hourly_spread, ["gfs_seamless"], RAIN_THRESHOLD_MM)),
        },
        {
            "name": "cloud accumulation uses compensated summation",
            "input": {"hourly_multi_model": hourly_cloud_sum, "models": ["gfs_seamless"], "threshold": RAIN_THRESHOLD_MM},
            "expected": dump(extract_day0_predictions_from_hourly(hourly_cloud_sum, ["gfs_seamless"], RAIN_THRESHOLD_MM)),
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
    # ROADMAP item 88, divergence 7, and the one case where PYTHON carries the
    # un-fixed form. Open-Meteo returns a correctly-named array full of nulls
    # when a model does not supply a variable under that alias, and a list of
    # Nones is TRUTHY — so `d.get(per_model) or d.get(bare)` latches onto the
    # null series and never reaches the fallback. That is how ECMWF's Day+0
    # wind went unscored for the whole life of this deployment; `pick_series`
    # was written for it, the Day+0 path uses it, and the Day+N path here
    # never got it. Dart's `pickSeries` has always been correct.
    daily_all_null_alias = {
        "daily": {
            "time": ["2026-08-11", "2026-08-12"],
            "precipitation_sum_gfs_seamless": [0.0, 3.2],
            "temperature_2m_max_gfs_seamless": [28.0, 26.0],
            "temperature_2m_min_gfs_seamless": [17.0, 18.0],
            "pressure_msl_mean_gfs_seamless": [1014.0, 1012.5],
            # Present, correctly named, and empty of data. The bare key below
            # is what the model actually supplies.
            "windgusts_10m_max_gfs_seamless": [None, None],
            "windgusts_10m_max": [25.0, 31.0],
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
    day_n_cases.append(
        {
            "name": "an all-null alias falls through to the bare key",
            "input": {
                "daily_multi_model": daily_all_null_alias,
                "day_index": 1,
                "models": ["gfs_seamless"],
                "threshold": RAIN_THRESHOLD_MM,
            },
            "expected": dump(
                extract_day_n_predictions_from_daily(
                    daily_all_null_alias, 1, ["gfs_seamless"], RAIN_THRESHOLD_MM
                )
            ),
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

    older = fresh.model_copy(
        update={
            "name": "Older Station",
            "aqi": 90,
            "measured_at": now - timedelta(hours=9),
        }
    )
    same_hour = fresh.model_copy(update={"name": "Second Station", "aqi": 30})
    last_known_scenarios = [
        ("freshest reading wins", [older, fresh]),
        ("tie at the same hour resolves to the worst station", [fresh, same_hour]),
        ("stale reading is still reported, flagged stale", [stale]),
        ("undated reading cannot be the most recent", [no_ts, fresh]),
        ("no numeric aqi yields null", [no_aqi]),
        ("empty list yields null", []),
    ]
    last_known_cases = []
    for name, readings in last_known_scenarios:
        last_known_cases.append(
            {
                "name": name,
                "input": {"readings": [dump(r) for r in readings], "now": now.isoformat()},
                "expected": dump(last_known_ground_aqi(readings, now)),
            }
        )
    # Purpose-built rather than reusing the readings above, because the
    # merge is about station IDENTITY: every case here has to control
    # station_id explicitly, and reading() derives it from the name.
    stored_real = reading("Ochieng' Avenue", 160, 9.0)
    refetched_null = stored_real.model_copy(update={"aqi": None, "measured_at": fresh.measured_at})
    other_stored = reading("Dunga Beach", 27, 9.0)

    merge_scenarios = [
        (
            "a fresh value replaces the stored one",
            [stored_real],
            [stored_real.model_copy(update={"aqi": 46, "measured_at": fresh.measured_at})],
        ),
        ("a fresh null keeps the stored value AND its timestamp", [stored_real], [refetched_null]),
        ("a station missing from the refetch keeps its reading", [stored_real], []),
        (
            "neither has a value: the fresher reading wins",
            [stored_real.model_copy(update={"aqi": None})],
            [refetched_null.model_copy(update={"pm25": 41.0})],
        ),
        (
            "stations are matched on id, not display name",
            [stored_real.model_copy(update={"name": "Old Label"})],
            [refetched_null.model_copy(update={"name": "New Label"})],
        ),
        ("nothing stored yields the fresh list", [], [fresh, stale]),
        ("a station only the refetch knows about is added", [stored_real], [refetched_null, fresh]),
        ("stored-only stations are appended after the fresh ones", [stored_real, other_stored], [fresh]),
        ("both empty", [], []),
    ]
    merge_cases = []
    for name, stored, fresh_readings in merge_scenarios:
        merge_cases.append(
            {
                "name": name,
                "input": {
                    "stored": [dump(r) for r in stored],
                    "fresh": [dump(r) for r in fresh_readings],
                },
                "expected": [dump(r) for r in merge_ground_aqi(stored, fresh_readings)],
            }
        )
    write(
        "aqi_merge.json",
        "merge_ground_aqi",
        "A re-issue's readings. A fresher ABSENCE never replaces an older "
        "measurement — the kept reading keeps its own measured_at, so its age "
        "and staleness stay honest. A station missing from the refetch is the "
        "same absence, since a failed station fetch is dropped from the list. "
        "Matched on station_id; ordered by the fresh list, stored-only "
        "stations appended.",
        merge_cases,
    )

    write(
        "aqi_last_known.json",
        "last_known_ground_aqi",
        "The most recent reading any station actually took, with its age — what "
        "the narrative quotes when nothing is fresh enough for the range. "
        "Independent of staleness; `stale` carries that. Undated readings are "
        "skipped, since 'most recent' is a claim about time.",
        last_known_cases,
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
    # ROUNDING TIE, item 89. Same defect as extract_day0's tie case, in the
    # observed-side twin of the same expression: the day total is
    # round(sum(...), 2) in Python and was rounded half away from zero in
    # Dart. 0.1 + 0.025 is exactly 0.125, so Python gives 0.12 and the old
    # Dart gave 0.13. Constructed rather than observed, for the reason given
    # on the other case — a SUM of feed-resolution values was never seen
    # landing on a tie.
    rounding_tie = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00"],
            "temperature_2m": [18.0, 19.0],
            "precipitation": [0.1, 0.025],
            "windgusts_10m": [12.0, 16.0],
            "pressure_msl": [1013.0, 1013.5],
        }
    }
    # Items 87 and 65: cloud_cover was in ARCHIVE_HOURLY_VARS all along and
    # the bucket never read it. A MEAN, with absent hours skipped rather than
    # counted as clear.
    cloudy = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00", "2026-08-11T02:00"],
            "temperature_2m": [18.0, 19.0, 20.0],
            "precipitation": [0.0, 0.0, 0.0],
            "windgusts_10m": [12.0, 16.0, 14.0],
            "pressure_msl": [1013.0, 1013.5, 1013.2],
            "cloud_cover": [10.0, None, 80.0],
        }
    }
    scenarios = [
        ("multi-day split, onset and aggregates per day", multi_day),
        ("gust array present with nulls — no windspeed substitution", gusts_with_nulls),
        ("gust array absent — falls back to windspeed", speed_fallback),
        ("a precipitation total on an exact rounding tie", rounding_tie),
        ("the archive's own cloud is a mean, and an absent hour is not clear",
         cloudy),
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

    # The split's two schemas — ROADMAP item 59 step 3. Written by hand into
    # spec/vectors on 2026-09-11 because this exporter could not run; see
    # tests/test_export_vectors.py for how that went unnoticed.
    split_cases = [
        {
            "name": "judgment",
            "input": {"model": "GeminiJudgmentResponse"},
            "expected": to_gemini_schema(GeminiJudgmentResponse),
        },
        {
            "name": "narrative",
            "input": {"model": "GeminiNarrativeResponse"},
            "expected": to_gemini_schema(GeminiNarrativeResponse),
        },
    ]
    write(
        "llm_schema_split.json",
        "to_gemini_schema(GeminiJudgmentResponse | GeminiNarrativeResponse)",
        "The two response schemas of the split \u2014 ROADMAP item 59 step 3. "
        "The narrative schema is pinned because its ABSENCES are the seam: it "
        "has no today_properties and no extended_properties, so the rendering "
        "call cannot return a scored value however its prompt is later "
        "edited.",
        split_cases,
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
        "ground_stations_configured": True,
        "local_bulletin_configured": True,
        # A first issuance: no previous entry to compare against, so
        # newer_than_previous_issuance is null, not false. hours_old ends in
        # .5 deliberately — see the Dart port's rounding note in forecast.dart.
        "guidance_recency": {
            "models_last_aligned_at": "2026-08-19T00:00:00+00:00",
            "hours_old": 6.5,
            "source": "observed",
            "newer_than_previous_issuance": None,
        },
    }
    # The later-issuance case, which since 2026-09-16 differs from the first
    # ONLY by its issuance time — `earlier_today` is no longer sent, so there
    # is no longer a payload that distinguishes them. Kept because the
    # ISSUANCE block still varies by hour and that is now the whole
    # difference the vector has to pin.
    refresh = dict(
        full,
        issuance={
            "local_time": "18:15",
            "phase": "dusk",
            "minutes_since_sunrise": 695,
            "minutes_to_sunset": 32,
            "sunrise": "06:40",
            "sunset": "18:47",
            "daylight_hours_left": 0,
            "statement": "It is 18:15. Sunset is in 32 minutes.",
            "horizon": [
                "tonight (dusk, evening and overnight through to dawn)",
                "tomorrow",
            ],
        },
        # A POPULATED WINDOWS LIST, because every other case here leaves the
        # issuance out and renders the unavailable text — a port could format
        # a real list any way it liked and still pass the set. Two entries so
        # the separator is exercised, and the second is named by weekday
        # exactly as forecast_windows names it at a dusk issuance.
        # The calendar the narrative writes its Extended Outlook from. Pinned
        # populated for the same reason the windows are: every other case
        # renders the unavailable text, and a port could format a real one
        # however it liked and still pass.
        forward_calendar=[
            {"lead_time_days": 0, "date": "2026-08-19", "day_name": "Wednesday"},
            {"lead_time_days": 1, "date": "2026-08-20", "day_name": "Thursday"},
            {"lead_time_days": 2, "date": "2026-08-21", "day_name": "Friday"},
        ],
        forecast_windows=[
            {
                "name": "tonight (dusk, evening and overnight through to dawn)",
                "start": "18:15",
                "end": "06:40",
                "crosses_midnight": True,
                "label": "tonight (dusk, evening and overnight through to dawn) "
                         "(18:15 to 06:40 next day)",
            },
            {
                "name": "Thursday",
                "start": "06:40",
                "end": "24:00",
                "crosses_midnight": False,
                "label": "Thursday (06:40-24:00)",
            },
        ],
        forward_hourly={"hourly": {"time": ["2026-08-19T18:00"], "precipitation_gfs_seamless": [0.4]}},
        # A REAL CALIBRATED GUST, because every other case leaves it out and
        # renders the unavailable text — and the thing most likely to diverge
        # is how a language prints the NUMBER. Python's f-string and Dart's
        # interpolation agree on 41.5 and on 41.0, and disagree with anything
        # that formats it as an int; pinning a value is what catches that.
        calibrated_gust_kmh=41.5,
        # A re-issue whose observed cycle has moved on since the morning:
        # newer_than_previous_issuance true, real news to narrate.
        guidance_recency={
            "models_last_aligned_at": "2026-08-19T12:00:00+00:00",
            "hours_old": 2.3,
            "source": "observed",
            "newer_than_previous_issuance": True,
        },
    )
    # Same re-issue, but no new cycle has landed since the morning — the
    # case that changes what the prompt tells the model to write: stay
    # quiet rather than manufacture a change. See prompt.py's
    # "NO NEW GUIDANCE IS AN ANSWER" passage.
    refresh_no_new_cycle = dict(
        refresh,
        guidance_recency={
            "models_last_aligned_at": "2026-08-19T06:00:00+00:00",
            "hours_old": 8.3,
            "source": "derived",
            "newer_than_previous_issuance": False,
        },
    )
    no_stations = {
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
        "ground_stations_configured": False,
        "local_bulletin_configured": True,
        "guidance_recency": None,
    }
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
        "ground_stations_configured": True,
        "local_bulletin_configured": True,
        # Cold start: no basis for a recency reading either — pins the
        # "Unavailable" fallback wording.
        "guidance_recency": None,
    }

    def case(name, kwargs, *, verification_already_written=False):
        """`verification_already_written` rides on the INPUT but is not a
        `build_user_prompt` argument.

        It says which SYSTEM prompt this case pairs with, and until
        2026-09-16 that was inferred from `earlier_today` being present.
        That payload is gone (items 137/138), so the case states it —
        `replay.py` pops it before splatting the rest.
        """
        return {
            "name": name,
            "input": {
                **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in kwargs.items()},
                "verification_already_written": verification_already_written,
            },
            "expected": build_user_prompt(**kwargs),
        }

    write(
        "llm_user_prompt.json",
        "build_user_prompt",
        "The full per-run user message, verbatim. Covers a fully-populated "
        "run, a later issuance, a cold start where every optional input "
        "is absent, and a deployment with no ground stations configured. The "
        "cold start matters most, because each 'Unavailable' string is what "
        "stops a gap being read as a measurement — and the last case is its "
        "mirror: where a source was never configured, the block is absent "
        "rather than reported unavailable. The last case covers the overnight-low footnote, which is absent on every ordinary morning and would otherwise be pinned by nothing.",
        [
            case("fully populated", full),
            case("a later issuance — differs only by its issuance hour", refresh, verification_already_written=True),
            case("a later issuance — no newer model cycle since the first", refresh_no_new_cycle, verification_already_written=True),
            case("cold start — every optional input absent", empty),
            case("no ground stations configured — the blocks are absent", no_stations),
            case(
                "no local met service configured — the bulletin block is absent",
                dict(full, local_bulletin_configured=False),
            ),
            # ITEM 143. The footnote is ABSENT from every case above, which is
            # the ordinary morning and would let a port implement nothing at
            # all and still pass. This case is the one that pins the block.
            case(
                "the overnight low diverged — the footnote block appears",
                dict(
                    full,
                    low_divergence_note=(
                        "Kisumu International Airport recorded an overnight low of "
                        "20.0°C / 68.0°F against a forecast of 18.2°C / 64.8°F. That "
                        "is this one station, not the wider area: it does not say "
                        "nowhere reached the forecast low."
                    ),
                ),
            ),
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

    def build(days: int, alpha_hits: int, beta_hits: int, alpha_high_bias: float = 0.0,
              alpha_cloud_bias: float | None = None):
        logs, actuals = {}, {}
        for i in range(days):
            d = today - timedelta(days=i + 1)
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0,
                # None unless a case asks for cloud, so every case written
                # before 2026-09-10 stays byte-identical rather than gaining
                # a scored sky it was not built to test.
                cloud_cover_pct=None if alpha_cloud_bias is None else 60.0,
            )
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=(i < alpha_hits),
                                    high_c=26.0 - alpha_high_bias, low_c=18.0,
                                    cloud_cover_pct=(None if alpha_cloud_bias is None
                                                     else 60.0 - alpha_cloud_bias)),
                    ModelPrediction(model="beta", rain=(i < beta_hits), high_c=26.0, low_c=18.0,
                                    cloud_cover_pct=None if alpha_cloud_bias is None else 60.0),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )
        return logs, actuals

    def _review_vector_case(name, logs, actuals, models_here, review):
        """One case's JSON, shared by every builder below so the shape cannot
        drift between them — a case missing a field would silently stop
        testing that field while the suite stayed green."""
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
                "models": models_here,
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
                        "mean_cloud_error_pct": c.mean_cloud_error_pct,
                        "cloud_checks": c.cloud_checks,
                        "storm_days": c.storm_days,
                        "storms_called": c.storms_called,
                        "earliest": _iso(c.earliest) if c.earliest else None,
                        "latest": _iso(c.latest) if c.latest else None,
                        "mean_rain_brier": c.mean_rain_brier,
                        "brier_checks": c.brier_checks,
                        "rain_brier_skill": c.rain_brier_skill,
                        "brier_skill_checks": c.brier_skill_checks,
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

    def brier_case(name: str, with_reference: bool):
        """Ten wet days at fixed probabilities, so every expected figure is a
        hand-checkable square rather than something only the code can produce.

        alpha says 90 every day       -> (0.9-1)^2 = 0.01 on 10 checks
        beta says 10 on 5 days only   -> (0.1-1)^2 = 0.81 on 5 of 10 checks
        climatology says 50 every day -> 0.25, and IS the reference

        beta carrying a probability on half its days is the point of the
        `brier_checks` column: 10 checks and 5 Brier checks on one row, which
        is what the real record looks like for weeks after 2026-09-03.
        """
        models_here = ["alpha", "beta"] + (["climatology"] if with_reference else [])
        logs, actuals = {}, {}
        for i in range(10):
            d = today - timedelta(days=i + 1)
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            day0 = [
                ModelPrediction(model="alpha", rain=True, rain_probability_pct=90,
                                high_c=26.0, low_c=18.0),
                ModelPrediction(model="beta", rain=True,
                                rain_probability_pct=10 if i < 5 else None,
                                high_c=26.0, low_c=18.0),
            ]
            if with_reference:
                day0.append(ModelPrediction(model="climatology", rain=True,
                                            rain_probability_pct=50,
                                            high_c=26.0, low_c=18.0))
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=day0),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )

        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

    def case(name: str, days: int, a: int, b: int, bias: float = 0.0,
             cloud_bias: float | None = None):
        logs, actuals = build(days, a, b, bias, cloud_bias)
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models, review)

    def contamination_case(name: str):
        """A baseline that would WIN the ranking if it were not excluded.

        20 days. alpha calls rain right 10 times (50%), beta 9 (45%), and
        climatology 19 (95%) — so an implementation that ranked the yardstick
        as a peer would publish "climatology is the strongest rain caller
        here", and one that let it into the bias sweep would name its 3 °C
        offset as a forecasting flaw. Both are claims about a yardstick, and
        neither is a claim about a forecast system.

        What SHOULD come out: a ranking over alpha and beta alone (a 5-point
        spread, under the 15-point floor, so no winner), and a baseline
        finding saying the guidance has not earned its place.
        """
        models_here = ["alpha", "beta", "climatology"]
        logs, actuals = {}, {}
        for i in range(20):
            d = today - timedelta(days=i + 1)
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=(i < 10),
                                    high_c=26.0, low_c=18.0),
                    ModelPrediction(model="beta", rain=(i < 9),
                                    high_c=26.0, low_c=18.0),
                    # A large offset on purpose: it must NOT be reported.
                    ModelPrediction(model="climatology", rain=(i < 19),
                                    high_c=23.0, low_c=18.0),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )

        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

    def tie_case(name: str):
        """Two models clearing the baseline on the SAME percentage.

        The claim string joins their names, so their ORDER is published prose.
        Python sorts that list with `sorted`, which is stable; Dart's
        List.sort is not, so without an explicit tie-break the two surfaces
        would name the same two models in different orders. Nothing else in
        this vector file produces a tie, which is why this case exists.
        """
        models_here = ["alpha", "beta", "climatology"]
        logs, actuals = {}, {}
        for i in range(20):
            d = today - timedelta(days=i + 1)
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=(i < 18), high_c=26.0, low_c=18.0),
                    ModelPrediction(model="beta", rain=(i < 18), high_c=26.0, low_c=18.0),
                    ModelPrediction(model="climatology", rain=(i < 10),
                                    high_c=26.0, low_c=18.0),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )

        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

    def pairing_case(name: str):
        """The reference speaks on only half the days, and the weather differs
        between the halves — so a paired skill score and an unpaired one give
        materially different answers.

        alpha states 90 on all 10 days. climatology states 50 on the 5 most
        recent only, which are DRY where the older 5 are wet. alpha therefore
        scores 0.81 on the shared days and 0.01 on the rest.

        Paired:   1 - 0.81/0.25 = -2.24 over 5 days.
        Unpaired: 1 - 0.41/0.25 = -0.64, comparing alpha's ten days against
                  climatology's five. Nothing in the older cases separates
                  these two, which is why this one exists.
        """
        models_here = ["alpha", "climatology"]
        logs, actuals = {}, {}
        for i in range(10):
            d = today - timedelta(days=i + 1)
            wet = i >= 5
            actuals[d] = DailyActual(
                rain=wet, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=wet, rain_probability_pct=90,
                                    high_c=26.0, low_c=18.0),
                    ModelPrediction(model="climatology", rain=wet,
                                    rain_probability_pct=50 if i < 5 else None,
                                    high_c=26.0, low_c=18.0),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )

        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

    def thin_third_model_case(name: str):
        """ROADMAP item 85. A thin model drags the sufficiency band down while
        two well-covered models are still rankable.

        `data_sufficiency` reports the WEAKEST scored model's coverage, on
        purpose. The ranking gate excludes anything under the comparison floor
        and compares what is left. So both numbers are right and they measure
        different things — and the sufficiency SENTENCE must not therefore
        conclude that models cannot be ranked, because a ranking for that same
        lead sits in the same payload.

        Measured on the real 2026-09-08 prompt before this was fixed: Day+3
        read "9 check(s) per model — directional only, not yet enough to rank
        models against each other" beside a ranking marked usable on 25 checks.
        Locked here because no other case has a thin third model, so the two
        languages could drift on this branch without a test noticing.
        """
        models_here = ["alpha", "beta", "thin"]
        logs, actuals = build(14, 13, 4)
        # `thin` appears only in the most recent few days, so it is SCORED but
        # below the comparison floor — never unscored, which is a different
        # case the newcomer test already covers.
        for i, d in enumerate(sorted(logs, reverse=True)):
            if i < 5:
                logs[d].model_predictions.day0 = [
                    *logs[d].model_predictions.day0,
                    ModelPrediction(model="thin", rain=True, high_c=26.0, low_c=18.0),
                ]
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

    def thin_third_model_usable_case(name: str):
        """The same shape as `thin_third_model_case`, one confidence band up.

        That case sits in the `provisional` branch. The live 2026-09-11 prompt
        sat in `usable` and read "22 check(s) per model — enough to compare
        models" beside "the 31 check(s) the other models have" — a flat claim
        that the very next clause denies. Two blind readers of an archived
        prompt reported the pair as a contradiction independently.

        Three of the six confidence branches named the least-covered model and
        three asserted "per model"; no vector case exercised an UNEVEN record
        at `usable`, which is why the defect survived item 85's fix. Locked
        here so neither language can drift back.
        """
        models_here = ["alpha", "beta", "thin"]
        logs, actuals = build(30, 28, 6)
        # thin reaches 22 of 30 days, so it is scored, comfortably above the
        # comparison floor, and still behind its peers — the production shape.
        for i, d in enumerate(sorted(logs, reverse=True)):
            if i < 22:
                logs[d].model_predictions.day0 = [
                    *logs[d].model_predictions.day0,
                    ModelPrediction(model="thin", rain=True, high_c=26.0, low_c=18.0),
                ]
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

    def storm_case(name: str, storm_days: int, alpha_calls: int, beta_calls: int):
        """Item 35's incident, made checkable: a model whose CAPE stays below
        the threshold on days the station observed thunder.

        Locked because the finding is asymmetric on purpose — a false alarm
        and a missed storm are not equally costly — and a port that reduced
        it to a hit rate would average the two and go quiet on exactly the
        case the item was raised about.
        """
        logs, actuals = {}, {}
        for i in range(30):
            d = today - timedelta(days=i + 1)
            thundered = i < storm_days
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0,
                thunder=thundered,
            )
            logs[d] = DailyLogEntry(
                date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=[
                    ModelPrediction(model="alpha", rain=True, high_c=26.0, low_c=18.0,
                                    peak_cape_jkg=1800.0 if (thundered and i < alpha_calls) else 300.0),
                    ModelPrediction(model="beta", rain=True, high_c=26.0, low_c=18.0,
                                    peak_cape_jkg=1800.0 if (thundered and i < beta_calls) else 300.0),
                ]),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=["alpha", "beta"],
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, ["alpha", "beta"], review)

    def thin_sky_case(name: str):
        """Thirty scored days, three of which say anything about the sky.

        The real shape of the record from 2026-09-10 onward: cloud_cover_pct
        arrived months after rain and temperature, so every cell holds a
        thick rain row and a thin cloud one. The bias loop read the CELL's
        count, and a mean over three days was about to be published as
        "across 30 checks" at confidence "established" — a tenfold
        overstatement in the one sentence a forecaster acts on.

        Locked because the two languages share that loop, and a port that
        kept the old single count would publish a claim the site does not.
        """
        logs, actuals = build(30, 15, 15, 0.0, 35.0)
        for i, d in enumerate(sorted(logs, reverse=True)):
            if i < 3:
                continue
            actuals[d] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            for pred in logs[d].model_predictions.day0:
                pred.cloud_cover_pct = None
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=["alpha", "beta"],
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, ["alpha", "beta"], review)

    def newcomer_case(name: str):
        """A model added today, with no verified checks at all.

        Never-scored and scored-less are different claims. Python learned this
        when the local met service was added: one newcomer at zero turned an
        honest "8 checks per model" into "0 check(s) per model — not enough to
        say anything", with eight days of scored forecasts sitting right there.

        Locked because that fix was Python-only for months and NOTHING caught
        it — no vector case had a zero-check model, so the Dart port kept the
        old behaviour and would have published a different sufficiency line
        from the site's on the first day a model was added.
        """
        models_here = ["alpha", "beta", "newcomer"]
        logs, actuals = build(12, 10, 3)
        review = build_weekly_review(
            log_lookup=lambda d: logs.get(d),
            actuals=actuals,
            all_log_dates=sorted(logs),
            today=today,
            models=models_here,
            lead_times_days=[0],
        )
        return _review_vector_case(name, logs, actuals, models_here, review)

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
            case("24 checks — percentage ties round half-even", 24, 3, 0),
            case("30 checks — a narrow gap is explicitly declined", 30, 20, 18),
            case("30 checks — a systematic temperature bias is named", 30, 15, 15, 2.0),
            case("30 checks — a half-even mean bias is named", 30, 15, 15, 2.25),
            # THE SKY, from 2026-09-10. A model that forecasts 35 points less
            # cloud than there was is named, so the forecaster can discount
            # it; five points is scatter and is not. Cloud's threshold is far
            # wider than temperature's because the models disagree with each
            # other by 70 points on the same morning, and a port that copied
            # the temperature threshold would fill the review with noise.
            case("30 checks — a model that cannot read the sky is named", 30, 15, 15, 0.0, 35.0),
            case("30 checks — a few points of cloud is scatter", 30, 15, 15, 0.0, 5.0),
            thin_sky_case("a thin sky in a thick row is not reported at all"),
            thin_third_model_usable_case(
                "uneven coverage at usable does not claim a flat per-model count"
            ),
            storm_case("a model blind to this location's storms is named", 12, 11, 3),
            storm_case("two storms is not a record, and names nobody", 2, 2, 0),
            case("4 checks — insufficient for anything", 4, 4, 1),
            brier_case(
                "Brier against climatology — a winner, a loser, and a partial column",
                with_reference=True,
            ),
            brier_case(
                "no climatology among the models — Brier present, skill absent",
                with_reference=False,
            ),
            contamination_case(
                "a baseline must not be ranked or bias-checked as a peer",
            ),
            tie_case("two models clear the baseline on equal footing — order is prose"),
            pairing_case("skill is scored on shared days only, and it changes the answer"),
            thin_third_model_case(
                "a thin third model lowers the count without denying the ranking",
            ),
            newcomer_case(
                "a never-scored model is named, not used as the headline",
            ),
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
    # ROADMAP item 88, divergence 9. The gradient is rounded to one decimal,
    # and the Dart port used (v * 10).round() / 10 — the form rounding.dart's
    # own header calls "wrong twice over", because the multiply both rounds
    # half away from zero AND invents ties the value does not have.
    #
    # UNREACHABLE WITH TODAY'S DATA, and that is measured rather than assumed:
    # Open-Meteo returns pressure_msl at one decimal (the 2026-09-12 run
    # carried 1008.1 and 1016.9), and 400,000 swept pairs of 1-dp readings
    # produced ZERO disagreements, because a difference of two 1-dp values
    # rounded to 1 dp cannot separate the two forms. These pressures are at
    # two decimals, which a different provider or a forked deployment may
    # well supply — item 52 — and which is what it takes to reach the bug.
    tie_gradient = ring(
        centre=[1006.00], N=[1000.00], S=[1012.25],
    )
    gaps = ring(centre=[1013.0], N=[None, None])
    missing_tail = ring(centre=[1013.0], N=[1009.0], S=[1017.0])

    cases = []
    for name, payload in [
        ("live ring — low to the NE, high to the S, pressure falling west", live),
        ("flat field — weak gradient, nothing deepening", flat),
        ("a feature building to the west before it is the lowest quadrant", approaching),
        ("a gradient landing on a tie rounds half-even", tie_gradient),
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

    def build(days: int, alpha_wind, beta_wind, skip=()):
        logs = {}
        for i in range(days):
            if i in skip:
                continue
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

    def case(name, days, alpha_wind, beta_wind, skip=()):
        logs = build(days, alpha_wind, beta_wind, skip=skip)
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
            # ROADMAP item 88, divergence 6. The window has a HOLE in it: the
            # run five days back never happened, so counting positions in the
            # loop and counting days on the calendar stop agreeing. Python
            # carries the stored date of the last present run; the Dart port
            # rebuilt it as today - 1 - index, which lands on the missing day
            # itself — a date the record does not contain, reported to a
            # reader as when the variable was last seen.
            case("last seen carries its stored date across a gap", 12,
                 lambda i: None if i < 4 else 22.0, 25.0, skip=(4,)),
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


def export_verification() -> None:
    """The full verification pass — the credibility of the whole project.

    This is what turns "accurate" from a claim into a measurement, so the two
    implementations agreeing is not a nicety: an app whose accuracy screen
    disagreed with the site's would discredit both, and a user comparing them
    could not tell which was right.

    The cases target the properties that are easy to get subtly wrong and
    damaging when wrong: that a model with no data is not scored at all, that
    all-time is RE-DERIVED rather than carried forward, and that a shrinking
    re-derivation keeps the previous figures rather than quietly publishing a
    smaller number.
    """
    from datetime import timedelta

    from openlocalweather.models import (
        DailyLogEntry,
        LogEntryMeta,
        ModelPredictionsByLead,
        TrackRecord,
        TrackRecordEntry,
    )
    from openlocalweather.verify.pipeline import run_deterministic_verification_and_scoring

    today = date(2026, 8, 21)
    yesterday = today - timedelta(days=1)
    models = ["alpha", "beta"]

    def build(days: int, alpha_correct: int, beta_correct: int, alpha_missing=False):
        """`alpha_correct` of the most recent days are called right by alpha."""
        logs, actuals = {}, {}
        for i in range(days):
            target = yesterday - timedelta(days=i)
            actuals[target] = DailyActual(
                rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0
            )
            preds = [
                ModelPrediction(
                    model="alpha",
                    # None rain means NO DATA — must not be scored at all,
                    # never counted as a wrong dry call.
                    rain=None if alpha_missing else (i < alpha_correct),
                    high_c=26.0, low_c=18.0, mslp_trend=-1.0, wind_kmh=20.0,
                ),
                ModelPrediction(
                    model="beta", rain=(i < beta_correct),
                    high_c=24.0, low_c=18.0, mslp_trend=-1.0, wind_kmh=20.0,
                ),
            ]
            logs[target] = DailyLogEntry(
                date=target, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
                temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
                narrative_markdown="n",
                model_predictions=ModelPredictionsByLead(day0=preds),
                meta=LogEntryMeta(generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                                  llm_provider="t", llm_model="t", pipeline_version="0"),
            )
        return logs, actuals

    def case(name, days, a, b, prior=None, alpha_missing=False):
        logs, actuals = build(days, a, b, alpha_missing=alpha_missing)
        result = run_deterministic_verification_and_scoring(
            log_lookup=lambda d: logs.get(d),
            prior_track_record=TrackRecord(
                generated_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
                entries=prior or [],
            ),
            actuals_primary=actuals,
            today=today,
            yesterday=yesterday,
            models=models,
            lead_times_days=[0],
            earliest_log_date=min(logs) if logs else yesterday,
        )
        return {
            "name": name,
            "input": {
                "predictions": {
                    _iso(d): {"0": [p.model_dump() for p in e.model_predictions.day0]}
                    for d, e in sorted(logs.items())
                },
                "actuals": {_iso(d): a.model_dump() for d, a in sorted(actuals.items())},
                "today": _iso(today),
                "yesterday": _iso(yesterday),
                "earliest_record_date": _iso(min(logs)) if logs else _iso(yesterday),
                "models": models,
                "lead_times_days": [0],
                "prior_track_record": [
                    {"model": e.model, "lead_time_days": e.lead_time_days,
                     "all_time_checks": e.all_time_checks,
                     "all_time_correct": e.all_time_correct,
                     "all_time_rain_pct": e.all_time_rain_pct}
                    for e in (prior or [])
                ],
            },
            "expected": {
                "lead_time_results": [
                    {"lead_time_days": r.lead_time_days,
                     "target_date_verified": _iso(r.target_date_verified) if r.target_date_verified else None,
                     "scored_models": sorted(r.per_model_scores)}
                    for r in result.lead_time_results
                ],
                "newly_verified": [[_iso(d), k] for d, k in result.newly_verified],
                "track_record": [
                    {"model": e.model, "lead_time_days": e.lead_time_days,
                     "rolling_10_rain_pct": e.rolling_10_rain_pct,
                     "rolling_30_rain_pct": e.rolling_30_rain_pct,
                     "rain_pct_trend": e.rain_pct_trend,
                     "all_time_checks": e.all_time_checks,
                     "all_time_correct": e.all_time_correct,
                     "all_time_rain_pct": e.all_time_rain_pct,
                     "checks_in_window_10": e.checks_in_window_10,
                     "avg_temp_high_error_c_10": e.avg_temp_high_error_c_10,
                     "avg_onset_error_hrs_10": e.avg_onset_error_hrs_10}
                    for e in result.updated_track_record.entries
                ],
            },
        }

    shrink_prior = [
        TrackRecordEntry(model="alpha", lead_time_days=0, all_time_checks=99,
                         all_time_correct=90, all_time_rain_pct=90.9),
        TrackRecordEntry(model="beta", lead_time_days=0),
    ]

    write(
        "verification.json",
        "run_deterministic_verification_and_scoring",
        "The full verification pass: yesterday's per-model scores, rolling "
        "windows, and re-derived all-time counts. Covers the cold start, a "
        "model with no data at all, and the safety rail that refuses to "
        "publish a shrinking all-time figure.",
        [
            case("cold start — one day of record", 1, 1, 0),
            case("twelve days, differing skill", 12, 9, 4),
            case("a model with NO data is not scored, not scored wrong", 12, 0, 6,
                 alpha_missing=True),
            case("a shrinking all-time keeps the previous figures", 5, 3, 2,
                 prior=shrink_prior),
        ],
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
        # A fork that polls no ground stations. Every ground-station passage
        # drops out rather than being softened: a deployment cannot report a
        # station as absent when none was ever configured.
        ("no ground stations configured", plain, False, {"ground_stations_configured": False}),
        # A fork with no national met service wired. The peer-model guidance
        # and the naming rule drop out, and the absence is stated once — the
        # model knows real met services for a real place, so silence would
        # leave it free to attribute a forecast to one.
        ("no local met service configured", plain, False, {"local_bulletin_configured": False}),
        # ROADMAP item 51. A run whose seven-day fetch timed out and which
        # published today anyway. The Extended Outlook HEADING stays — the
        # structure is a contract — and its instruction is replaced, because
        # asking for a paragraph "using the daily summary data" that is not
        # there is a required section with nothing to fill it.
        ("extended outlook unavailable", plain, False, {"extended_outlook_available": False}),
    ]

    cases = []
    for name, loc, verification_already_written, overrides in scenarios:
        kwargs = {
            "historical_lookback_days": HISTORICAL_LOOKBACK_DAYS,
            "rolling_window_short": ROLLING_WINDOW_SHORT,
            "rolling_window_long": ROLLING_WINDOW_LONG,
            "ground_stations_configured": True,
            "local_bulletin_configured": True,
            "extended_outlook_available": True,
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
                    "verification_already_written": verification_already_written,
                    **kwargs,
                },
                "expected": {
                    "judgment": build_judgment_prompt(
                        loc, verification_already_written=verification_already_written, **kwargs
                    ),
                    "narrative": build_narrative_prompt(
                        loc, verification_already_written=verification_already_written, **kwargs
                    ),
                },
            }
        )
    write(
        "llm_system_prompt.json",
        "build_judgment_prompt + build_narrative_prompt",
        "The instruction sets behind every forecast, pinned verbatim so a "
        "port cannot quietly reason from different instructions. TWO PROMPTS "
        "since ROADMAP item 59 step 3: the judgment call decides the scored "
        "fields, the narrative call is handed that decision and writes the "
        "prose. Both are pinned because either one drifting changes the "
        "forecast.",
        cases,
    )



def export_low_divergence() -> None:
    """ROADMAP item 143. The station's overnight low against the standing call.

    VECTOR-LOCKED SEPARATELY FROM THE DISAGREEMENT LIST, because the two
    answer different questions and a port that merged them would be wrong in
    a way nothing would report. `observation_disagreements` decides whether an
    LLM CALL IS BOUGHT, and only a near-freezing divergence is allowed to buy
    one. This decides what is MEASURED AND STORED, and it runs on every day
    the comparison is possible — including the ordinary mornings that buy
    nothing, which are the days that will eventually answer whether the
    station runs warm or the forecast low is the problem.

    THE ASYMMETRY IS THE CASE THAT MATTERS, as it is everywhere in this
    module. A minimum only falls, so a reading BELOW the call settles it at
    any hour while one ABOVE it waits for sunrise — and a port that skipped
    the gate would print a divergence on a night that was still running.
    """
    scenarios = [
        ("the founding case: 20.0 observed against a call of 18.2", 18.2, 20.0, True),
        ("the same gap, before sunrise, is not yet a comparison", 18.2, 20.0, False),
        ("a night whose end is unknown is not a comparison either", 18.2, 20.0, None),
        ("inside the wide band, measured but not notable", 18.2, 19.0, True),
        ("exactly at the wide band fires", 18.2, 21.2, True),
        ("near freezing, the band tightens", -0.5, 2.0, True),
        ("near freezing and inside even the tight band", 0.2, 0.9, True),
        ("colder than called needs no sunrise", 2.0, -1.0, False),
        ("colder than called, far from freezing, is notable but not decisive",
         25.0, 21.0, False),
        ("no standing low is no comparison", None, 20.0, True),
        ("no station low is no comparison", 18.2, None, True),
    ]

    cases = []
    for name, called, seen, settled in scenarios:
        got = low_divergence(
            StandingCall(temp_low_c=called),
            ObservedSoFar(low_c=seen),
            low_is_settled=settled,
        )
        cases.append(
            {
                "name": name,
                "input": {
                    "standing": {"temp_low_c": called},
                    "observed": {"low_c": seen},
                    "low_is_settled": settled,
                },
                "expected": None
                if got is None
                else {
                    "forecast_c": got.forecast_c,
                    "observed_c": got.observed_c,
                    "delta_c": round(got.delta_c, 10),
                    "margin_c": got.margin_c,
                    "notable": got.notable,
                    "decisive": got.decisive,
                },
            }
        )

    write(
        "low_divergence.json",
        "low_divergence",
        "ROADMAP item 143. How far the station's overnight low sits from the "
        "standing call, measured on every run that can make the comparison "
        "and stored whether or not it is worth printing. `delta_c` is "
        "OBSERVED MINUS FORECAST, so positive means the station came in "
        "WARMER than the call. `notable` is worth a reader's footnote; "
        "`decisive` is worth an LLM call, and is `notable` AND near freezing "
        "-- two degrees is nothing at 20C and is the difference between ice "
        "and no ice at 2C. A reading ABOVE the call requires the night to be "
        "over (`low_is_settled` true, never null); one BELOW it settles at "
        "any hour, because a minimum only falls.",
        cases,
    )


def export_observation_disagreements() -> None:
    """ROADMAP item 104, C2's third trigger, and it decides whether an LLM
    call is made rather than what one says.

    That is why it is vector-locked: a port that fired on the symmetric case
    would re-forecast every dry morning of a wet day, spending the reader's
    own budget on it, and nothing about the forecast it produced would look
    wrong. The margin and the asymmetry are the cases that matter — see
    disagreement.py for why a maximum only rises.
    """
    # Each scenario is (name, (rain, high_call, onset_call, low_call),
    # (precipitation, high_obs, onset_obs, low_obs), low_is_settled). The
    # onset slot was added with item 138 and the low slot with item 143.
    scenarios = [
        ("rain observed while the call said dry", (False, 30.0, None, None), (True, None, None, None), True),
        ("no rain YET does not contradict a rain call", (True, 30.0, None, None), (False, None, None, None), True),
        ("the observed high has already passed the call", (False, 30.0, None, None), (False, 33.0, None, None), True),
        ("a high below the call is not a contradiction", (False, 30.0, None, None), (False, 24.0, None, None), True),
        ("just under the margin does not fire", (False, 30.0, None, None), (False, 31.9, None, None), True),
        ("exactly at the margin fires", (False, 30.0, None, None), (False, 32.0, None, None), True),
        ("absent observations contradict nothing", (False, 30.0, None, None), (None, None, None, None), True),
        ("absent standing call contradicts nothing", (None, None, None, None), (True, 99.0, None, None), True),
        ("both fire, in a stable order", (False, 30.0, None, None), (True, 35.0, None, None), True),
        # ITEM 138: the call is right about the day and wrong about the hour,
        # which every test above is blind to.
        ("onset already passed", (True, 30.0, "18:00", None), (True, None, "14:00", None), True),
        ("onset inside the forecast's own resolution", (True, 30.0, "18:00", None), (True, None, "17:30", None), True),
        ("rain later than called is not a contradiction", (True, 30.0, "14:00", None), (True, None, "18:00", None), True),
        ("exactly at the onset margin fires", (True, 30.0, "18:00", None), (True, None, "17:00", None), True),
        ("an unpadded hour still parses", (True, 30.0, "18:00", None), (True, None, "9:00", None), True),
        ("no called onset says nothing", (True, 30.0, None, None), (True, None, "14:00", None), True),
        # ITEM 143: the observed low. ONLY THE NEAR-FREEZING CASES REACH THIS
        # LIST — the gap is measured on every run and stored, but membership
        # here buys an LLM call, so an ordinary warm-morning divergence is
        # deliberately absent from the result. `low_divergence.json` pins the
        # measurement itself; this pins only what it is allowed to SPEND.
        ("the founding case: warmer station, far from freezing, buys nothing",
         (None, None, None, 18.2), (None, None, None, 20.0), True),
        ("the same gap near freezing is decisive",
         (None, None, None, -0.5), (None, None, None, 2.0), True),
        ("an unsettled night makes a warmer reading prove nothing",
         (None, None, None, -0.5), (None, None, None, 2.0), False),
        ("colder than called settles without waiting for sunrise",
         (None, None, None, 2.0), (None, None, None, -1.0), False),
        ("a night whose end is UNKNOWN resolves to silence",
         (None, None, None, -0.5), (None, None, None, 2.0), None),
        ("no station low says nothing", (None, None, None, -0.5), (None, None, None, None), True),
    ]

    cases = []
    for (
        name,
        (rain, high_call, onset_call, low_call),
        (precipitation, high_obs, onset_obs, low_obs),
        settled,
    ) in scenarios:
        standing = StandingCall(
            rain=rain, temp_high_c=high_call, onset_hour=onset_call, temp_low_c=low_call
        )
        observed = ObservedSoFar(
            precipitation=precipitation,
            high_c=high_obs,
            precipitation_onset=onset_obs,
            low_c=low_obs,
        )
        cases.append(
            {
                "name": name,
                "input": {
                    "standing": {
                        "rain": rain,
                        "temp_high_c": high_call,
                        "onset_hour": onset_call,
                        "temp_low_c": low_call,
                    },
                    "observed": {
                        "precipitation": precipitation,
                        "high_c": high_obs,
                        "precipitation_onset": onset_obs,
                        "low_c": low_obs,
                    },
                    "low_is_settled": settled,
                },
                "expected": observation_disagreements(
                    standing, observed, low_is_settled=settled
                ),
            }
        )

    write(
        "observation_disagreements.json",
        "observation_disagreements",
        "ROADMAP item 104, C2's third trigger: does what the station has "
        "already observed contradict the standing call? Governed throughout "
        "by an asymmetry -- a mid-day observation can only prove a forecast "
        "too LOW, never too high, because rain that has fallen has fallen "
        "and a maximum only rises. The margin is sized above the station's "
        "measured +0.43 C offset against the reanalysis.",
        cases,
    )


def export_wind_direction() -> None:
    """Circular arithmetic, which is the single easiest thing here to port
    wrong and have every test still pass.

    A port that averages bearings on a number line agrees with this one on
    every set that does not cross north, and disagrees catastrophically on
    the ones that do — 350 and 10 degrees are twenty degrees apart and average
    to due SOUTH. The wrap cases below exist to fail that port.
    """
    MODELS_HERE = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless"]

    def hourly(per_hour):
        hours = sorted(per_hour)
        out = {"time": [f"2026-09-10T{h:02d}:00" for h in hours]}
        for i, m in enumerate(MODELS_HERE):
            out[f"wind_direction_10m_{m}"] = [per_hour[h][i] for h in hours]
        return {"hourly": out}

    mean_cases = [
        ("due north, where a naive mean returns due south", [350.0, 10.0]),
        ("opposing winds cancel and have no bearing", [0.0, 180.0]),
        ("one northerly outlier against four southwesterlies", [11.0, 254.0, 264.0, 207.0, 225.0]),
        ("a tight southwesterly field", [225.0, 230.0, 220.0, 235.0]),
        ("scattered to all quarters", [22.0, 135.0, 275.0, 215.0]),
        ("a single bearing agrees with itself", [123.4]),
        ("nothing to average", []),
    ]
    write(
        "wind_vector_mean.json",
        "vector_mean",
        "Bearing and agreement for a set of compass directions, as unit "
        "vectors. The agreement figure is the resultant length: 1.0 is "
        "identical bearings, 0.0 is a set that cancels out and genuinely has "
        "no mean direction. Includes the wrap-around cases an arithmetic mean "
        "gets exactly backwards.",
        [{"name": n, "input": {"degrees": d},
          "expected": (lambda r: None if r is None else {"bearing": r[0], "agreement": r[1]})(vector_mean(d))}
         for n, d in mean_cases],
    )

    write(
        "wind_consensus_direction.json",
        "consensus_direction",
        "The gated rose point: one direction the models actually share, or "
        "null. Null is the correct and common answer in the evening at this "
        "location, where agreement falls to 0.48 as the lake breeze collapses.",
        [{"name": n, "input": {"degrees": d, "gate": WIND_DIRECTION_AGREEMENT_GATE},
          "expected": consensus_direction(d)}
         for n, d in mean_cases + [
             ("exactly at the gate is named", [0.0, 41.0, 319.0]),
             ("two bearings are not a consensus however well they agree", [225.0, 226.0]),
         ]],
    )

    # One model's own array is present and empty; the shared series carries
    # the readings. See the divergence-10 case below.
    empty_series_payload = {
        "hourly": {
            "time": ["2026-09-10T03:00", "2026-09-10T12:00", "2026-09-10T18:00"],
            # The shared series is an OUTLIER at 03:00, deliberately. With it
            # the four bearings fail the 0.75 agreement gate and the overnight
            # anchor is dropped; without it the remaining three agree on NNE.
            # That is the difference between a two-anchor clause and a
            # three-anchor one, from identical bytes.
            "wind_direction_10m": [120.0, 225.0, 270.0],
            f"wind_direction_10m_{MODELS_HERE[0]}": [],
            f"wind_direction_10m_{MODELS_HERE[1]}": [35.0, 220.0, 265.0],
            f"wind_direction_10m_{MODELS_HERE[2]}": [25.0, 230.0, 275.0],
            f"wind_direction_10m_{MODELS_HERE[3]}": [40.0, 218.0, 268.0],
        }
    }

    shift_cases = [
        ("the lake breeze, all three anchors agreed", {
            3: [30.0, 35.0, 25.0, 40.0],
            12: [225.0, 220.0, 230.0, 218.0],
            18: [270.0, 265.0, 275.0, 268.0]}, 0),
        ("the evening is scattered and is left out", {
            3: [30.0, 35.0, 25.0, 40.0],
            12: [225.0, 220.0, 230.0, 218.0],
            18: [10.0, 200.0, 100.0, 280.0]}, 0),
        ("steady all day is said, not skipped", {
            3: [225.0, 220.0, 230.0, 218.0],
            12: [223.0, 228.0, 222.0, 226.0],
            18: [220.0, 224.0, 219.0, 227.0]}, 0),
        ("one anchor is not a shift", {
            3: [10.0, 200.0, 100.0, 280.0],
            12: [225.0, 220.0, 230.0, 218.0],
            18: [10.0, 200.0, 100.0, 280.0]}, 0),
        ("no hour agrees, so nothing is claimed", {
            3: [10.0, 200.0, 100.0, 280.0],
            12: [15.0, 190.0, 95.0, 300.0],
            18: [20.0, 210.0, 110.0, 290.0]}, 0),
        ("an all-null day is not a calm one", {
            3: [None, None, None, None],
            12: [None, None, None, None]}, 0),
        # ROADMAP item 118. The same agreeing day, issued at 18:01: every
        # anchor is 03:00, 12:00 or 18:00, so none of the clause is still
        # ahead and a locked phrase in Today's Forecast would be a pure
        # retrospective. Issued at 06:00 the same day still reads normally —
        # the first case above — because the test is whether ANY of it is
        # ahead, not whether all of it is.
        ("every anchor is behind an evening issuance", {
            3: [30.0, 35.0, 25.0, 40.0],
            12: [225.0, 220.0, 230.0, 218.0],
            18: [270.0, 265.0, 275.0, 268.0]}, 18),
    ]
    write(
        "wind_describe_shift.json",
        "describe_wind_shift",
        "One finished clause for how the wind turns through the day. Measured "
        "2026-09-10: the models' agreement on a single daily bearing swings "
        "from 0.95 at midday to 0.48 at 19:00, while the DAYS agree with each "
        "other at 0.98-0.99 — so the shift is far better supported than any "
        "one bearing, and it is the shift that gets reported.",
        [{"name": n,
          "input": {"hourly_multi_model": hourly(ph), "models": MODELS_HERE,
                    "issued_hour": issued},
          "expected": describe_wind_shift(hourly(ph), MODELS_HERE, issued_hour=issued)}
         for n, ph, issued in shift_cases]
        # ROADMAP item 88, divergence 10. A per-model key that is PRESENT but
        # EMPTY: Python's `or` chain treats an empty list as falsy and falls
        # through to the shared `wind_direction_10m` series, while Dart's `??`
        # falls through only on a missing key and so contributes nothing for
        # that model. Same payload, different number of models in the
        # consensus, and the gate is a threshold on agreement — so the two can
        # publish different clauses from identical data.
        + [{"name": "an empty per-model series falls through to the shared one",
            "input": {"hourly_multi_model": empty_series_payload,
                      "models": MODELS_HERE, "issued_hour": 0},
            "expected": describe_wind_shift(
                empty_series_payload, MODELS_HERE, issued_hour=0)}],
    )


def export_comparison_for_prompt() -> None:
    """What the prompt is allowed to see of the stored comparison.

    ROADMAP item 88, divergence 5: this had no Dart counterpart at all, while
    `DayOverDayComparison.toJson()` emits all seventeen fields and reads as
    though it were ready to hand over. Two of those fields were removed from
    the prompt on 2026-09-05 because a rule telling the forecaster not to
    re-derive the comparison cannot beat a payload supplying the arithmetic to
    re-derive it with. The narrowing is the contract; the fields it drops are
    the reason it exists.
    """
    from openlocalweather.comparison import comparison_for_prompt

    stored = {
        "yesterday_high_c": 29.6,
        "yesterday_low_c": 18.8,
        "yesterday_rain": True,
        "yesterday_thunder": True,
        "today_rain_expected": True,
        "today_consensus_high_c": 29.5,
        "high_delta_c": -0.1,
        "low_delta_c": -0.8,
        "overview_comparison": "Much like yesterday.",
        "provenance": {"rain": "era5_archive", "thunder": "metar_station"},
    }
    no_provenance = {k: v for k, v in stored.items() if k != "provenance"}
    partial = dict(no_provenance, provenance={"rain": "era5_archive"})
    sparse = {"overview_comparison": "Dry again.", "provenance": {"thunder": "metar_station"}}

    cases = [
        {
            "name": name,
            "input": {"comparison": payload},
            "expected": comparison_for_prompt(payload),
        }
        for name, payload in [
            ("the four fields and both sources", stored),
            ("no provenance stored — observed_from is omitted, not empty", no_provenance),
            ("one source stamped, one not", partial),
            ("a field absent from the stored comparison is absent here", sparse),
            ("absent comparison", None),
        ]
    ]
    write(
        "comparison_for_prompt.json",
        "comparison_for_prompt",
        "The narrowing between the stored day-over-day comparison and what "
        "the prompt is shown: four fields, plus observed_from rebuilt from "
        "provenance. THE DELTAS ARE DROPPED ON PURPOSE — a rule asking the "
        "forecaster not to re-derive the comparison cannot beat a payload "
        "that hands it the arithmetic, which is why two fields were removed "
        "on 2026-09-05. observed_from is omitted rather than emitted empty, "
        "because an empty map would claim the sources were looked up and "
        "found absent.",
        cases,
    )


def export_day_over_day() -> None:
    """The Overview's opening sentence. Vector-tested because a live run got
    it wrong when the LLM was left to subtract: it called a 0.1°C difference
    "about 1°C cooler"."""
    def preds(highs, lows=None, winds=None, rains=None, mm=None, onsets=None, clouds=None):
        n = len(highs)
        lows = lows or [18.0] * n
        winds = winds or [20.0] * n
        rains = rains or [True] * n
        # Amounts and onsets default to a genuinely wet day so the older
        # temperature/wind cases keep a meaningful rain_contrast rather than
        # quietly dropping to None.
        mm = mm if mm is not None else [18.0] * n
        onsets = onsets if onsets is not None else ["08:00"] * n
        return [
            ModelPrediction(model=f"m{i}", rain=rains[i], high_c=highs[i], low_c=lows[i],
                            wind_kmh=winds[i], precip_mm=mm[i], onset=onsets[i],
                            cloud_cover_pct=(clouds[i] if clouds is not None else None))
            for i in range(n)
        ]

    def actual(**kw):
        base = dict(rain=True, high_c=29.6, low_c=18.8, peak_wind_kmh=40.7, mslp_trend=-0.5,
                    onset_hour="08:00", precip_mm=18.0)
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
        # 2026-08-27 live: forecast 33.5 against yesterday's observed 32.3.
        # The old 1.5 C band called this "about the same" while the page
        # showed 90 F yesterday and 92 F today, and the reader disagreed.
        ("1.2C warmer is a change the reader can see",
         actual(high_c=32.3), preds([33.5, 33.5, 33.5])),
        ("0.9C warmer is still about the same",
         actual(high_c=32.6), preds([33.5, 33.5, 33.5])),
        ("wind change below threshold is not remarked on", actual(peak_wind_kmh=25.0), preds([29.0], winds=[30.0])),
        ("big wind increase is called out", actual(peak_wind_kmh=15.0), preds([29.0], winds=[40.0])),
        ("a dry day following a wet one", actual(rain=True), preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        ("a wet day following a dry one", actual(rain=False, precip_mm=0.0, onset_hour=None), preds([29.0], rains=[True])),
        # THE CASE THAT PROMPTED ALL OF THIS. Kisumu, 2026-08-22: clear and
        # dry until evening convection, described to the reader as "another
        # wet day" because 0.5 mm in any hour made it one.
        ("evening showers following a wet day is NOT another wet day",
         actual(rain=True, precip_mm=19.0, onset_hour="07:00"),
         preds([29.0], rains=[True], mm=[2.4], onsets=["19:00"])),
        ("evening showers two days running",
         actual(rain=True, precip_mm=2.1, onset_hour="18:00"),
         preds([29.0], rains=[True], mm=[2.6], onsets=["19:00"])),
        ("a genuinely wet day is still called wet",
         actual(rain=False, precip_mm=0.2, onset_hour=None),
         preds([29.0], rains=[True], mm=[24.0], onsets=["07:00"])),
        ("half a millimetre at dusk is a dry day",
         actual(rain=False, precip_mm=0.0, onset_hour=None),
         preds([29.0], rains=[True], mm=[0.4], onsets=["20:00"])),
        # Item 53.1a — 2026-08-29. The reanalysis recorded 0.0 mm and so
        # recorded no onset either, while the airport reported -RA at 19:00
        # local. Without the station's onset the dry band has no timing to
        # reach its shower phrases with, and the day reaches the reader as
        # plain "dry" on a day 53.1 already scores as wet.
        ("a shower the reanalysis missed is timed from the station",
         actual(rain=False, precip_mm=0.0, onset_hour=None,
                thunder=False, precipitation=True, precipitation_onset="19:00"),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        ("the reanalysis onset still wins when it recorded one",
         actual(rain=True, precip_mm=8.0, onset_hour="13:00",
                thunder=False, precipitation=True, precipitation_onset="19:00"),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        ("no amount recorded means no comparison, not a guess",
         actual(rain=True, precip_mm=None),
         preds([29.0], rains=[True], mm=[None], onsets=["19:00"])),
        # One model calling dawn against three calling evening must not
        # average into mid-afternoon — a shape of day none of them forecast.
        ("a single outlier onset does not drag the consensus",
         actual(rain=True, precip_mm=2.0, onset_hour="19:00"),
         preds([29.0, 29.0, 29.0, 29.0], rains=[True] * 4, mm=[2.0] * 4,
               onsets=["05:00", "19:00", "19:30", "20:00"])),
        # 2026-08-24: the airport reported TS for an hour; the reanalysis
        # recorded 0.5 mm. The next morning's Overview said "dry again".
        ("thunder yesterday is never 'dry again'",
         actual(rain=False, precip_mm=0.5, onset_hour=None, thunder=True),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        ("thunder observed as absent still reads dry again",
         actual(rain=False, precip_mm=0.5, onset_hour=None, thunder=False),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        ("no thunder observation at all behaves as before",
         actual(rain=False, precip_mm=0.5, onset_hour=None, thunder=None),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        ("a wet thundery day contrasted against a dry one",
         actual(rain=True, precip_mm=8.0, onset_hour="17:00", thunder=True),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None])),
        # ITEM 83 DEFECT 5, the archived 2026-09-08 Day+0 payload. One model
        # of six carried an onset; the mean its 8.7 mm dragged to 2.12 banded
        # as "largely dry", and the median of a ONE-MEMBER list named the
        # hour. The block then read "dry until evening showers today" beside
        # "today_rain_expected": false, and the forecaster picked a side.
        ("one model of six does not get to name the day",
         actual(rain=True, precip_mm=2.0, onset_hour=None),
         [ModelPrediction(model=m, rain=r, onset=o, precip_mm=mm,
                          high_c=31.5, low_c=18.9, wind_kmh=33.0)
          for m, r, o, mm in [
              ("gfs_seamless", False, None, 0.4),
              ("ecmwf_ifs025", True, "16:00", 8.7),
              ("icon_seamless", False, None, 0.7),
              ("ukmo_seamless", False, None, 0.3),
              ("best_match", False, None, 0.5),
              ("kenya_met", True, None, None),
          ]]),
        # ITEM 83 DEFECT 4, both axes. The top band had no ceiling, so a
        # 6 degree change and a frontal passage were the same three words.
        ("a frontal passage is not merely much cooler",
         actual(high_c=50.0), preds([29.0, 29.0, 29.0])),
        ("11.9C still reads as much, not dramatically",
         actual(high_c=40.9), preds([29.0, 29.0, 29.0])),
        ("12C is where much stops being enough",
         actual(high_c=41.0), preds([29.0, 29.0, 29.0])),
        ("17 km/h is a change, not a big one", actual(peak_wind_kmh=25.0), preds([29.0], winds=[42.0])),
        ("a gale collapsing to nothing is not merely calmer",
         actual(peak_wind_kmh=55.0), preds([29.0], winds=[10.0])),
        # ROUNDING TIES, and they are here because they reach the READER.
        # Dart's `(v * 10).roundToDouble() / 10` disagreed with Python's
        # `round(v, 1)` on 600 of 32,001 swept values, and two of them crossed
        # a band edge: -11.95 read "much cooler" in Python and "dramatically
        # cooler" in Dart, -17.95 read "calmer" against "much calmer". Every
        # vector case passed throughout — which is the whole argument for
        # sweeping a function rather than trusting the cases you chose.
        ("a negative tie rounds toward Python, not away from zero",
         actual(high_c=30.0), preds([18.05])),
        ("the same tie one field over, on the wind band edge",
         actual(peak_wind_kmh=40.0), preds([29.0], winds=[22.05])),
        # An exact quarter is the ONLY value that is genuinely a tie at one
        # decimal place, and Python rounds it half to EVEN.
        ("an exact quarter rounds half to even",
         actual(high_c=30.0), preds([29.75])),
        # ITEM 83 / 2026-09-09: today's thunder is the convective flag. Without
        # it today could never be thundery while yesterday always could, so a
        # thundery yesterday manufactured a contrast — and the Overview then
        # said today was thundery one sentence later.
        ("thunder on both days is not a change",
         actual(rain=True, precip_mm=3.0, onset_hour=None, thunder=True),
         preds([29.0], rains=[False], mm=[2.0], onsets=[None]), True),
        ("thunder yesterday but not today still reads as a change",
         actual(rain=True, precip_mm=3.0, onset_hour=None, thunder=True),
         preds([29.0], rains=[False], mm=[2.0], onsets=[None]), False),
        ("thunder today but not yesterday is the actionable direction",
         actual(rain=True, precip_mm=3.0, onset_hour=None, thunder=False),
         preds([29.0], rains=[False], mm=[2.0], onsets=[None]), True),
        # The contrast frame around no contrast: characters differed, bands
        # did not, and the summary reports the band.
        ("one band on both sides is never a contrast",
         actual(rain=True, precip_mm=3.0, onset_hour="17:00", thunder=False),
         preds([29.0], rains=[False], mm=[2.0], onsets=[None]), False),
        # THE SKY — items 87, 65 and 83. Without a case carrying cloud on both
        # sides every cloud_label in this file is null and the Dart half is
        # compared against nothing, which is the empty-input trap item 90
        # recorded. Watched failing before the Dart port existed.
        ("a sky two categories clearer is news",
         actual(rain=False, precip_mm=0.0, onset_hour=None, cloud_cover_pct=90.0),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None], clouds=[10.0])),
        ("one okta of change is not",
         actual(rain=False, precip_mm=0.0, onset_hour=None, cloud_cover_pct=40.0),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None], clouds=[48.0])),
        ("a sky nobody measured withholds the sameness claim",
         actual(rain=False, precip_mm=0.0, onset_hour=None, cloud_cover_pct=None),
         preds([29.0], rains=[False], mm=[0.0], onsets=[None], clouds=[None])),
        # ITEM 98. Without a case carrying provenance every one is null and the
        # Dart half is compared against nothing — the empty-input trap, and the
        # fourth time it has come up in a day. The two sources here are the
        # real pairing: a reanalysis cell for rain, a station 3.8 km away for
        # thunder, which disagreed on 4 of 14 days.
        ("the two observations come from two different places",
         actual(rain=True, precip_mm=3.0, onset_hour=None, thunder=True,
                provenance={"rain": "era5_archive", "thunder": "metar_station"}),
         preds([29.0], rains=[False], mm=[2.0], onsets=[None]), True),
        ("no observed record yields nothing at all", None, preds([29.0])),
        ("model with no data doesn't poison the consensus", actual(), 
         [ModelPrediction(model="a", rain=True, high_c=29.5, low_c=18.0, wind_kmh=37.0),
          ModelPrediction(model="b", rain=None, high_c=None, low_c=None, wind_kmh=None)]),
        # ROADMAP item 118, end to end: the same day composed at 06:00 and at
        # 18:00. The first keeps "dry until evening thunderstorms"; the second
        # may not, because by then it is a claim about hours that have gone.
        ("evening thunder, issued in the morning", actual(),
         preds([29.0], rains=[True], mm=[8.0], onsets=["17:00"]), True, 6),
        # SINCE CONTRACT ITEM 8's GATE this one demonstrates the gate rather
        # than the onset bounding: an 18:00 issuance produces no comparison at
        # all, so there is no phrase left to bound. Kept at 18:00 deliberately
        # — it is the hour the operator named as one they do not want a
        # comparison at, and a null here is the assertion.
        ("evening thunder at 18:00 is gated off entirely", actual(),
         preds([29.0], rains=[True], mm=[8.0], onsets=["17:00"]), True, 18),
        # Item 118's bounding still has to be exercised somewhere the gate
        # lets a comparison through. An onset at 05:00 read at 08:00 has
        # already happened, so the timing phrase must go while the comparison
        # itself stays — which is the behaviour the 18:00 case used to carry.
        ("an onset already past is not a timing phrase", actual(),
         preds([29.0], rains=[True], mm=[8.0], onsets=["05:00"]), True, 8),
        # ROADMAP item 126. The gust operand is the CALIBRATED consensus, and
        # these two cases are the same weather banded from the raw and the
        # corrected number. Yesterday gusted to 40.7 and the models call 30.0
        # — "calmer" on the raw figure, and "similar winds" once the +11.5
        # km/h the record has measured is added back. That flip is the defect:
        # over 34 mornings the label read calmer fourteen times and windier
        # never, at a mean of -8.31 km/h against an 8.0 km/h band.
        ("the raw gust consensus bands as calmer", actual(),
         preds([29.6], winds=[30.0]), None, 6),
        ("the calibrated gust says the wind held still", actual(),
         preds([29.6], winds=[30.0]), None, 6, 41.5),
        # THE WARNING READS THE CALIBRATED GUST TOO, and this pair is the case
        # that proves it. Every other label here is RELATIVE, so a shared bias
        # partly cancels; a warning is a LEVEL against NOAA's absolute
        # thresholds, and the lowest is 25 knots — 46.3 km/h. A 40.0 km/h
        # consensus sits under it and the record's own correction carries it
        # over, so an uncalibrated warning is silent on the day it matters.
        ("a raw gust under the lowest band warns about nothing", actual(),
         preds([29.6], winds=[40.0]), None, 6),
        ("the calibrated gust crosses into a warning", actual(),
         preds([29.6], winds=[40.0]), None, 6, 51.5),
        # CONTRACT ITEM 8, STAGE 2 — the evening subject. 20:00 on a Monday
        # whose sun set at 18:39: the comparison is about TOMORROW, measured
        # against today, and every word of it has to say so. The baseline is
        # named twice because English names it twice — "cooler than today
        # (Monday) WAS" against "much like today (Monday)" — and the rain half
        # carries the day it is about, because "dry until evening showers"
        # read at 20:00 is about tonight to anyone not told otherwise.
        ("the evening subject is tomorrow, named and in the past tense",
         None, preds([29.6]), None, 20, None,
         {"today_actual": actual(high_c=30.5, precip_mm=12.0, onset_hour="14:00"),
          "sunset_hour": 18, "tomorrow_predictions": preds([27.0], mm=[0.0], onsets=[None],
                                                           rains=[False]),
          "today_name": "Monday", "tomorrow_name": "Tuesday"}),
        # TOMORROW'S TIMING SURVIVES A LATE ISSUANCE. Item 118 bounds a timing
        # phrase by the hours already ELAPSED and none of tomorrow's have, so
        # a 17:00 onset read at 20:00 must stay. Bounding it by today's hour
        # would suppress a phrase about a day that has not started.
        ("a late issuance does not bound tomorrow's onset",
         None, preds([29.6]), None, 20, None,
         {"today_actual": actual(high_c=30.5, precip_mm=12.0, onset_hour="14:00"),
          "sunset_hour": 18, "tomorrow_predictions": preds([27.0], mm=[8.0], onsets=["17:00"]),
          "today_name": "Monday", "tomorrow_name": "Tuesday"}),
        # THE SIMILARITY FORM TAKES NO VERB. "Much like today (Monday) was"
        # is a stammer, and without a case that reaches this branch a port can
        # add the verb and pass everything else — mutating it SURVIVED the
        # Dart vectors until this case existed. Every dimension quiet and the
        # rain band unchanged, which is what it takes to get here.
        ("the evening similarity form names today without a verb",
         None, preds([29.6]), None, 20, None,
         {"today_actual": actual(high_c=29.6, peak_wind_kmh=20.0, cloud_cover_pct=40.0,
                                 precip_mm=12.0, onset_hour="14:00"),
          "sunset_hour": 18,
          "tomorrow_predictions": preds([29.6], winds=[20.0], clouds=[44.0],
                                        mm=[12.0], onsets=["14:00"]),
          "today_name": "Monday", "tomorrow_name": "Tuesday"}),
        # NO CALENDAR, PLAINER SENTENCE. A caller without weekday names says
        # "today" and "tomorrow" rather than printing an empty parenthesis.
        ("without weekday names the evening sentence is plainer",
         None, preds([29.6]), None, 20, None,
         {"today_actual": actual(high_c=30.5, precip_mm=12.0, onset_hour="14:00"),
          "sunset_hour": 18, "tomorrow_predictions": preds([27.0], mm=[0.0], onsets=[None],
                                                           rains=[False])}),
    ]

    cases = []
    for scenario in scenarios:
        name, y, ps = scenario[0], scenario[1], scenario[2]
        convective = scenario[3] if len(scenario) > 3 else None
        # Issued at midnight unless the scenario says otherwise, so every
        # onset is still ahead and these keep the behaviour the record has.
        issued = scenario[4] if len(scenario) > 4 else 0
        # None means "no model had enough verified checks", which is the state
        # every case but one is in — see ROADMAP item 126.
        calibrated = scenario[5] if len(scenario) > 5 else None
        # The evening subject needs four more inputs than the morning one, and
        # a seventh positional slot would be unreadable at the call sites that
        # do not use it.
        extra = dict(scenario[6]) if len(scenario) > 6 else {}
        result = compute_day_over_day(
            y, ps, today_convective=convective, issued_hour=issued,
            calibrated_wind_kmh=calibrated, **extra,
        )
        cases.append({
            "name": name,
            "input": {
                "yesterday_actual": dump(y) if y is not None else None,
                "today_day0_predictions": [dump(p) for p in ps],
                "today_convective": convective,
                "issued_hour": issued,
                "calibrated_wind_kmh": calibrated,
                "sunset_hour": extra.get("sunset_hour"),
                "tomorrow_predictions": (
                    [dump(p) for p in extra["tomorrow_predictions"]]
                    if extra.get("tomorrow_predictions") else None
                ),
                "today_name": extra.get("today_name"),
                "tomorrow_name": extra.get("tomorrow_name"),
                "today_actual": (
                    dump(extra["today_actual"]) if extra.get("today_actual") else None
                ),
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



def export_extended_trend() -> None:
    """ROADMAP item 61 — the Overview's closing clause."""
    scenarios = [
        # name, today_high, day_highs, day_precip, last_day_name
        ("steady - the commonest case, and it still gets a phrase",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday"),
        ("warming past the threshold",
         30.0, [31.0, 32.0, 33.5], [0.0, 0.0, 0.0], "Friday"),
        ("cooling past the threshold",
         30.0, [29.0, 28.0, 27.5], [0.0, 0.0, 0.0], "Friday"),
        ("just under the threshold is steady, not warming",
         30.0, [30.5, 31.0, 31.9], [0.0, 0.0, 0.0], "Saturday"),
        ("exactly at the threshold names the trend",
         30.0, [30.5, 31.0, 32.0], [0.0, 0.0, 0.0], "Saturday"),
        ("rain arriving earns its own clause",
         30.0, [29.0, 28.0, 27.5], [0.0, 0.0, 8.0], "Friday"),
        ("a dry spell continuing does not - much the same already says it",
         30.0, [30.1, 30.0, 29.9], [0.2, 0.0, 0.9], "Friday"),
        ("the END of the span decides, not the mean",
         30.0, [34.0, 34.0, 30.1], [0.0, 0.0, 0.0], "Friday"),
        ("no today high - nothing to compare against",
         None, [31.0, 32.0, 33.0], [0.0, 0.0, 0.0], "Friday"),
        ("no extended highs at all",
         30.0, [None, None, None], [None, None, None], "Friday"),
        ("a gap mid-span still answers from what is there",
         30.0, [None, 33.0, None], [0.0, None, 0.0], "Sunday"),
        # 2026-09-09: wind was available at these leads and discarded, so the
        # clause could only ever be about temperature. The scope noun now
        # follows what was actually measured and found steady.
        ("everything measured is steady, so the noun widens to conditions",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", 20.0, [20.5, 21.0, 19.5]),
        ("rain arriving forbids 'conditions', which would deny its own tail",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 8.0], "Friday", 20.0, [20.5, 21.0, 19.5]),
        ("a wind build under a flat temperature is worth saying",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", 18.0, [24.0, 30.0, 34.0]),
        ("both moving names both",
         30.0, [33.0, 33.5, 34.0], [0.0, 0.0, 0.0], "Friday", 18.0, [24.0, 30.0, 34.0]),
        ("a wind drop past the threshold reads as calming",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", 34.0, [30.0, 26.0, 20.0]),
        ("no wind measured keeps the narrow noun",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", None, None),
        # A LEVEL, not a trend. Four dangerous days running are "conditions
        # much the same", which is true and useless.
        # NOAA thresholds against gusts, per the standard's own wording.
        ("a steady span above the warning floor still warns",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", 90.0, [92.0, 88.0, 91.0]),
        ("warning and rain share one 'with'",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 8.0], "Friday", 90.0, [92.0, 88.0, 91.0]),
        ("just under the 25 kt floor says nothing about level",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", 44.0, [45.0, 46.0, 45.5]),
        ("the record's windiest day is an advisory, not a gale",
         30.0, [30.2, 30.1, 29.8], [0.0, 0.0, 0.0], "Friday", 50.0, [52.6, 51.1, 49.7]),
    ]

    cases = []
    for scenario in scenarios:
        name, today, highs, precip, day_name = scenario[:5]
        wind_today = scenario[5] if len(scenario) > 5 else None
        winds = scenario[6] if len(scenario) > 6 else None
        cases.append({
            "name": name,
            "input": {
                "today_high_c": today,
                "day_highs_c": highs,
                "day_precip_mm": precip,
                "last_day_name": day_name,
                "today_wind_kmh": wind_today,
                "day_winds_kmh": winds,
            },
            "expected": describe_extended_trend(
                today, highs, precip, day_name,
                today_wind_kmh=wind_today, day_winds_kmh=winds),
        })
    write(
        "extended_trend.json",
        "describe_extended_trend",
        "ROADMAP item 61. One FINISHED phrase for the next three days, handed "
        "to the prompt to use verbatim -- a label would leave the wording to "
        "the model, which is the thing that goes wrong. The threshold is 2.0 C "
        "across the span because item 23 measured a run calling 29.6 C against "
        "29.5 C 'about 1 C cooler'. A steady spell gets real words rather than "
        "a null: the absence of change is the planning answer.",
        cases,
    )


def export_daypart() -> None:
    from datetime import datetime as _dt

    from openlocalweather.daypart import (
        daypart_without_sun,
        forecast_windows,
        forward_hours,
        reconcile_now,
        summarize_daypart,
    )

    # Kisumu's real figures for 2026-08-22. The 18:15 case is the one that
    # started all of this: sunset was 18:47, so the "evening" run fires 32
    # minutes BEFORE sunset.
    sr = "2026-08-22T06:40"
    ss = "2026-08-22T18:47"
    nsr = "2026-08-23T06:40"

    cases = []
    for label, now in [
        ("night, small hours", "2026-08-22T02:00"),
        ("before sunrise", "2026-08-22T06:20"),
        ("just after sunrise", "2026-08-22T06:50"),
        ("morning", "2026-08-22T09:00"),
        ("midday", "2026-08-22T12:30"),
        ("afternoon", "2026-08-22T15:00"),
        ("dusk — the 18:15 run", "2026-08-22T18:15"),
        ("evening, after dark", "2026-08-22T21:00"),
        ("late night", "2026-08-22T23:30"),
        # A 30-second remainder: the halfway case where Python's half-to-even
        # round() and Dart's half-away-from-zero .round() disagree.
        ("half-minute rounding", "2026-08-22T18:16:30"),
    ]:
        cases.append(
            {
                "name": label,
                "input": {"now": now, "sunrise": sr, "sunset": ss, "next_sunrise": nsr},
                "expected": summarize_daypart(
                    _dt.fromisoformat(now),
                    _dt.fromisoformat(sr),
                    _dt.fromisoformat(ss),
                    _dt.fromisoformat(nsr),
                ).to_json(),
            }
        )

    # THE SAME CLOCK TIME AT THREE DIFFERENT SUN POSITIONS.
    #
    # Without these the whole vector set can be satisfied by a table of clock
    # hours, because every other case shares one sunrise and one sunset. A
    # port could hard-code "18:00 is dusk" and pass — then be wrong at every
    # latitude this project is meant to be forked to. Verified: replacing the
    # sun-relative logic with clock hours passes the rest of the set.
    for label, now, rise, set_, why in [
        ("18:00 in Kisumu in August", "2026-08-22T18:00", "2026-08-22T06:40",
         "2026-08-22T18:47", "nearly sunset"),
        ("18:00 far north in summer", "2026-08-22T18:00", "2026-08-22T03:30",
         "2026-08-22T22:30", "sun still high"),
        ("18:00 far north in winter", "2026-08-22T18:00", "2026-08-22T09:30",
         "2026-08-22T15:15", "long dark"),
        # 05:00 is "night" on any clock table, but not where the sun rose at
        # 03:30.
        ("05:00 far north in summer", "2026-08-22T05:00", "2026-08-22T03:30",
         "2026-08-22T22:30", "well after sunrise"),
    ]:
        cases.append(
            {
                "name": f"{label} — {why}",
                "input": {"now": now, "sunrise": rise, "sunset": set_, "next_sunrise": None},
                "expected": summarize_daypart(
                    _dt.fromisoformat(now), _dt.fromisoformat(rise), _dt.fromisoformat(set_)
                ).to_json(),
            }
        )

    # Polar cases, from the shapes Open-Meteo actually returns.
    for label, now, rise, set_ in [
        ("midnight sun, 23:00", "2026-06-21T23:00", "2026-06-21T00:00", "2026-06-22T00:00"),
        ("midnight sun, 02:00", "2026-06-21T02:00", "2026-06-21T00:00", "2026-06-22T00:00"),
        ("polar night", "2026-12-21T10:00", "2026-12-21T11:30", "2026-12-21T12:30"),
    ]:
        cases.append(
            {
                "name": label,
                "input": {"now": now, "sunrise": rise, "sunset": set_, "next_sunrise": None},
                "expected": summarize_daypart(
                    _dt.fromisoformat(now), _dt.fromisoformat(rise), _dt.fromisoformat(set_)
                ).to_json(),
            }
        )

    write(
        "daypart.json",
        "summarize_daypart",
        "Where the issuance moment sits in the day. Phases are sun-relative, "
        "not clock-based, so the same clock time is a different part of the "
        "day at a different latitude or season. Every string here is written "
        "into the prompt verbatim, so the two implementations must agree "
        "character for character.",
        cases,
    )


    # --- forecast_windows — ROADMAP item 104 ---------------------------------
    #
    # THE CASES ARE CHOSEN TO BREAK A CLOCK TABLE, same discipline as the
    # summarize_daypart set above. Every bound here is sun-relative (dusk is
    # sunset minus 90 minutes, the day windows end at the coming sunrise), so a
    # port that hard-codes "the evening starts at 17:10" passes Kisumu and
    # fails both high-latitude sets.
    #
    # The midnight pair is the reason the weekday naming exists: 23:00 Monday
    # and 01:00 Tuesday describe ONE calendar day, and before this the first
    # called it "tomorrow" and the second "today".
    window_cases = []
    for label, now, rise, set_, nxt in [
        # Kisumu, across the whole day.
        ("pre-dawn", "2026-09-14T02:30", "2026-09-14T06:33", "2026-09-14T18:40",
         "2026-09-15T06:33"),
        ("dawn", "2026-09-14T06:01", "2026-09-14T06:33", "2026-09-14T18:40",
         "2026-09-15T06:33"),
        ("afternoon, three windows", "2026-09-14T15:00", "2026-09-14T06:33",
         "2026-09-14T18:40", "2026-09-15T06:33"),
        ("dusk — the 18:01 run", "2026-09-14T18:01", "2026-09-14T06:33",
         "2026-09-14T18:40", "2026-09-15T06:33"),
        ("evening", "2026-09-14T22:01", "2026-09-14T06:33", "2026-09-14T18:40",
         "2026-09-15T06:33"),
        # The pair that must agree. Monday night and Tuesday morning, one day
        # between them, named once.
        ("Monday 23:00 — names Tuesday", "2026-09-14T23:00", "2026-09-14T06:33",
         "2026-09-14T18:40", "2026-09-15T06:33"),
        ("Tuesday 01:00 — names Tuesday", "2026-09-15T01:00", "2026-09-15T06:33",
         "2026-09-15T18:40", "2026-09-16T06:33"),
        # Exactly at dusk and exactly at midnight: the two bounds the windows
        # are built from, where an inclusive/exclusive slip shows up.
        ("exactly at dusk", "2026-09-14T17:10", "2026-09-14T06:33",
         "2026-09-14T18:40", "2026-09-15T06:33"),
        ("exactly at midnight", "2026-09-15T00:00", "2026-09-15T06:33",
         "2026-09-15T18:40", "2026-09-16T06:33"),
        # Same clock hour, three sun positions — a clock table cannot pass all
        # three.
        ("18:00 far north in summer", "2026-09-14T18:00", "2026-09-14T03:30",
         "2026-09-14T22:30", "2026-09-15T03:30"),
        ("18:00 far north in winter", "2026-09-14T18:00", "2026-09-14T09:30",
         "2026-09-14T15:15", "2026-09-15T09:30"),
    ]:
        now_dt = _dt.fromisoformat(now)
        rise_dt = _dt.fromisoformat(rise)
        set_dt = _dt.fromisoformat(set_)
        nxt_dt = _dt.fromisoformat(nxt)
        horizon = summarize_daypart(now_dt, rise_dt, set_dt, nxt_dt).horizon
        window_cases.append(
            {
                "name": label,
                "input": {
                    "now": now,
                    "sunrise": rise,
                    "sunset": set_,
                    "horizon": list(horizon),
                    "next_sunrise": nxt,
                },
                "expected": [
                    w.to_json()
                    for w in forecast_windows(now_dt, rise_dt, set_dt, horizon, nxt_dt)
                ],
            }
        )

    write(
        "forecast_windows.json",
        "forecast_windows",
        "The horizon's named periods given explicit, contiguous, "
        "non-overlapping clock bounds, each starting where the previous ended "
        "and the first at the issuance itself. Labels reach the reader "
        "verbatim, and a day is named by weekday whenever it is not the "
        "issuance's own date or the run is at night — so two issuances either "
        "side of midnight name one day the same way.",
        window_cases,
    )

    no_sun = [
        {
            "name": label,
            "input": {"now": now},
            "expected": daypart_without_sun(_dt.fromisoformat(now)).to_json(),
        }
        for label, now in [
            ("dusk-ish, sun unknown", "2026-08-22T18:15"),
            ("morning, sun unknown", "2026-08-22T09:00"),
        ]
    ]
    write(
        "daypart_without_sun.json",
        "daypart_without_sun",
        "The issuance moment when sunrise and sunset could not be fetched. "
        "The phase is 'unknown' rather than guessed from the clock, but the "
        "horizon stays precise because midnight is midnight at every latitude.",
        no_sun,
    )

    clock = []
    for label, sys_local, header, offset in [
        ("agrees", "2026-08-22T07:01", "Sat, 22 Aug 2026 04:00:00 GMT", 10800),
        ("wildly wrong", "2026-08-22T19:00", "Sat, 22 Aug 2026 04:00:00 GMT", 10800),
        ("exactly at the skew limit", "2026-08-22T07:05", "Sat, 22 Aug 2026 04:00:00 GMT", 10800),
        ("one minute past the limit", "2026-08-22T07:06", "Sat, 22 Aug 2026 04:00:00 GMT", 10800),
        ("no header", "2026-08-22T07:00", None, 10800),
        ("unparseable header", "2026-08-22T07:00", "not a date", 10800),
        ("no offset", "2026-08-22T07:00", "Sat, 22 Aug 2026 04:00:00 GMT", None),
    ]:
        got, warning = reconcile_now(_dt.fromisoformat(sys_local), header, offset)
        clock.append(
            {
                "name": label,
                "input": {
                    "system_local": sys_local,
                    "server_date_header": header,
                    "utc_offset_seconds": offset,
                },
                "expected": {"now": got.isoformat(), "warned": warning is not None},
            }
        )
    write(
        "daypart_clock.json",
        "reconcile_now",
        "A second opinion on the system clock, from the Date header of a "
        "response already being fetched. Past five minutes of disagreement "
        "the server is believed. A missing or unparseable header stops the "
        "check rather than triggering it — an absent header is not evidence.",
        clock,
    )

    fw_input = {
        "hourly": {
            "time": [
                f"2026-08-{22 + (i // 24):02d}T{i % 24:02d}:00" for i in range(48)
            ],
            "temperature_2m_gfs_seamless": [float(i) for i in range(48)],
            "temperature_2m_ecmwf_ifs025": [float(47 - i) for i in range(48)],
        }
    }
    fw = [
        {
            "name": label,
            "input": {"hourly_multi_model": fw_input, "now": now},
            "expected": forward_hours(fw_input, _dt.fromisoformat(now)),
        }
        for label, now in [
            ("the 06:07 run", "2026-08-22T06:07"),
            ("the 18:15 run — must reach tomorrow", "2026-08-22T18:15"),
            ("data that does not cover now", "2030-01-01T12:00"),
        ]
    ]
    write(
        "daypart_forward_hours.json",
        "forward_hours",
        "Hourly guidance trimmed to the hours still ahead. Narrative only — "
        "nothing scored passes through here. Every model's series is trimmed "
        "in step with the time array; data that does not overlap 'now' "
        "returns an empty series rather than itself.",
        fw,
    )


# ---------------------------------------------------------------------------
# day character and instability
# ---------------------------------------------------------------------------


def export_glossary() -> None:
    """ROADMAP item 56 — the forecast's vocabulary.

    Not a function's behaviour but a body of PROSE that reaches a reader in
    two languages, so it is pinned the same way the system prompt is: exported
    whole, compared character for character. A definition that drifts between
    the site and the app is two forecasts explaining one word differently.
    """
    write(
        "glossary.json",
        "GLOSSARY",
        "The forecast's technical vocabulary, defined once. Static data rather "
        "than an LLM call: a definition has one right answer that does not "
        "depend on today's weather, and a generated one would drift. `source` "
        "names the publishing body where the numbers come from and is null "
        "where the entry is this project's own plain-English wording — an "
        "invented citation being worse than none.",
        [
            {
                "name": e.term,
                "input": {"term": e.term},
                "expected": {"definition": e.definition, "source": e.source},
            }
            for e in GLOSSARY
        ],
    )


def export_describe_day_over_day() -> None:
    """ROADMAP item 83 — the composition contract.

    Vector-tested separately from compute_day_over_day because the label
    COMBINATIONS are where it goes wrong, and most of them are awkward to
    reach through a pair of days: whether an unmoved label is dropped,
    whether "much like yesterday" is earned, and whether the rain phrase
    lands in a sentence of its own. A real Overview welded these three into
    "with dry until evening showers today; yesterday was largely dry".
    """
    scenarios = [
        ("nothing at all", None, None, None),
        ("all three quiet is one short sentence", "about the same", "similar winds", None),
        ("both labels moved", "slightly warmer", "calmer", None),
        ("only the high moved", "noticeably cooler", "similar winds", None),
        ("only the wind moved", "about the same", "much windier", None),
        ("the unmoved label is dropped, not listed", "about the same", "dramatically calmer", None),
        # Defect 1: a sentence opener that has no legal place after "with".
        ("a sentence opener gets its own sentence",
         "slightly warmer", "calmer", "dry until evening showers"),
        # The operator's complaint: three quiet vectors and one that moved
        # is not "much the same". Rain leads, and the quiet labels go.
        ("rain alone moving is not much like yesterday",
         "about the same", "similar winds", "wet, after a dry day"),
        ("rain with both labels moved", "much warmer", "much windier", "wet again"),
        ("a missing wind label does not fabricate a quiet one",
         "slightly warmer", None, None),
        # "Much like yesterday" is a claim about three measurements, so a
        # missing one withholds it: a null label is absent data, not quiet.
        ("a quiet high with no wind measured claims nothing",
         "about the same", None, None),
        ("a quiet wind with no high measured claims nothing",
         None, "similar winds", None),
        ("rain alone, with no labels at all", None, None, "dry"),
        # Nothing moved on ANY dimension, so the lead says so and the rain
        # half drops its "again" rather than saying it twice.
        ("nothing moved at all, so the lead says so",
         "about the same", "similar winds", "largely dry with thunderstorms again",
         "largely dry with thunderstorms", True),
        ("a change still leads, and the rain half keeps its own again",
         "noticeably cooler", "similar winds", "largely dry with thunderstorms again",
         "largely dry with thunderstorms", True),
        ("rain that genuinely changed forfeits the sameness claim",
         "about the same", "similar winds", "dry", "dry", False),
        ("a warning is not suppressed by sameness",
         "about the same", "similar winds", None, None, False, "gale force"),
        ("nor by a change leading",
         "noticeably cooler", "similar winds", None, None, False, "storm force"),
    ]
    cases = [
        {
            "name": s[0],
            "input": {
                "high_label": s[1], "wind_label": s[2], "rain_contrast": s[3],
                "today_character": s[4] if len(s) > 4 else None,
                "rain_unchanged": s[5] if len(s) > 5 else False,
                "wind_warning_name": s[6] if len(s) > 6 else None,
            },
            "expected": describe_day_over_day(
                s[1], s[2], s[3],
                today_character=s[4] if len(s) > 4 else None,
                rain_unchanged=s[5] if len(s) > 5 else False,
                wind_warning_name=s[6] if len(s) > 6 else None,
            ),
        }
        for s in scenarios
    ]
    write(
        "describe_day_over_day.json",
        "describe_day_over_day",
        "The day-over-day comparison composed into finished, punctuated "
        "sentences. Code does the welding because code is what knows the "
        "shape of the phrases it wrote: the rain phrase is a sentence opener "
        "and never survives a preposition, the baseline is named once, and a "
        "label that did not move is dropped rather than enumerated.",
        cases,
    )


def export_describe_day_rain() -> None:
    """The phrase that reaches the reader almost verbatim inside
    rain_contrast. Vector-tested separately from compute_day_over_day because
    the band edges and the thunder override are where it goes wrong: a live
    run described a day with an observed thunderstorm as "dry again"."""
    scenarios = [
        ("no amount is a gap", None, None, None),
        ("plain dry day", 0.0, None, None),
        ("dry with thunder is never dry", 0.5, None, True),
        ("thunder false still reads dry", 0.5, None, False),
        ("thunder unobserved still reads dry", 0.5, None, None),
        ("dry band with an evening shower", 0.9, "17:00", None),
        ("dry band with an afternoon shower", 0.9, "13:00", None),
        ("dry band with an early shower", 0.9, "07:00", None),
        ("just over the dry band, evening", 1.1, "17:00", None),
        ("largely dry, no timing", 3.0, None, None),
        ("largely dry, evening onset", 3.0, "17:00", None),
        ("showery from the afternoon", 8.0, "13:00", None),
        ("showery with evening thunder", 8.0, "17:00", True),
        ("showery with afternoon thunder", 8.0, "13:00", True),
        ("wet day from the morning", 20.0, "07:00", None),
        ("wet day with thunder", 20.0, "07:00", True),
        ("heavy evening rain", 20.0, "17:00", None),
    ]
    # ROADMAP item 118. Every case above describes a day that is OVER, which
    # is `issued_hour=None`, and those are the phrases the record has always
    # produced. The three below are the boundary the item turned on: a timing
    # qualifier says "and not before", so it may only be composed while the
    # hour it names is still ahead.
    timed = [
        ("evening onset, issued in the morning", 8.0, "17:00", True, 6),
        ("evening onset, issued at the onset hour", 8.0, "17:00", True, 17),
        ("evening onset, issued after it — the 2026-09-12 case", 8.0, "17:00", True, 18),
    ]
    cases = [
        {
            "name": name,
            "input": {
                "precip_mm": precip,
                "onset": onset,
                "thunder": thunder,
                "issued_hour": issued,
            },
            "expected": describe_day_rain(precip, onset, thunder, issued_hour=issued),
        }
        for name, precip, onset, thunder, issued in (
            [(n, p_, o, t, None) for n, p_, o, t in scenarios] + timed
        )
    ]
    write(
        "describe_day_rain.json",
        "describe_day_rain",
        "One phrase for a day's rain character: how much, when, and whether it "
        "thundered. Thunder outranks the amount — a day the airport observed a "
        "storm on is never described as dry, whatever the grid cell recorded.",
        cases,
    )


def export_prompt_rounding() -> None:
    """The payload's precision pass — ROADMAP item 73, category 4.

    VECTOR-TESTED BECAUSE ROUNDING IS WHERE PORTS PART COMPANY, and this repo
    has the measurement: a Dart one-decimal rounding passed every vector case
    and still disagreed with Python on 962 of 4801 swept values, because
    Python rounds the DECIMAL EXPANSION of a binary float and the port scaled
    and rounded the float itself.

    So the cases below are the ties and the boundaries, not a spread of
    ordinary values — spec/README.md's own instruction. 0.05 rounds UP because
    the double is 0.050000000000000003; 0.15 rounds DOWN because it is
    0.1499999999999999944; 0.25 is exact and half-to-even takes it to 0.2; and
    0.35 is 0.34999999999999997779 and goes to 0.3. A port that scales by ten
    and rounds gets three of those four wrong while looking correct.

    The STRUCTURE cases matter as much. A bool is not a number however
    `isinstance(True, int)` reads, an int is a count and must come back
    untouched, and the per-field table has to survive nesting — the worst
    offender in a real prompt, `regional_pressure`, is a list of dicts.
    """
    cases = [
        # The ties, at the default one place.
        ("0.05 rounds up: the double is above the tie", 0.05, None),
        ("0.15 rounds down: the double is below the tie", 0.15, None),
        ("0.25 is an exact tie and goes half-to-even", 0.25, None),
        ("0.35 is below its tie and rounds down", 0.35, None),
        ("negative ties keep the sign", -0.05, None),
        # The noise this pass exists to remove, from a real prompt.
        ("a mean error carrying IEEE noise", -2.380000000000001, None),
        ("a pressure trend carrying IEEE noise", 1.1999999999999318, None),
        ("a percentage from a count", 61.76470588235294, None),
        # The exceptions, keyed by field name.
        ("a Brier score keeps four places", 0.05289999999999999, "rain_brier"),
        ("a Brier skill score keeps four places", -0.1234567, "rain_brier_skill"),
        ("a coordinate keeps four places", -0.05857086, "latitude"),
        # Structure.
        ("an int is a count and is untouched", 34, None),
        ("True is not a number", True, None),
        ("False is not a number", False, None),
        ("null passes through", None, None),
        ("a string passes through", "steady", None),
    ]

    exported = []
    for name, value, field in cases:
        payload = {field: value} if field else {"value": value}
        exported.append({
            "name": name,
            "input": {"payload": payload},
            "expected": _round_for_prompt(payload),
        })

    # Nesting, with the per-field precision surviving a list of dicts — the
    # shape `regional_pressure` actually arrives in.
    nested = {
        "regional_pressure": [
            {"latitude": -0.10544816, "pressure_msl_mean": 1014.6666666666666},
            {"latitude": 34.793453, "pressure_msl_mean": 1013.3333333333334},
        ],
        "checks": 34,
        "rain_brier": 0.3721,
        "hourly": {"temperature_2m": [21.349999999999998, 22.65]},
    }
    exported.append({
        "name": "the per-field table survives a list of dicts",
        "input": {"payload": nested},
        "expected": _round_for_prompt(nested),
    })

    write(
        "prompt_rounding.json",
        "_round_for_prompt",
        "The precision pass applied to the prompt payload: one decimal place "
        "by default, because the instruments are recorded to 0.1, with a "
        "per-field table for the quantities one place would destroy.",
        exported,
    )


def export_gust_calibration() -> None:
    """The gust correction and the consensus it produces — ROADMAP item 126.

    VECTOR-TESTED BECAUSE THE SIGN IS THE WHOLE THING. `avg_wind_error_kmh_10`
    is `actual - predicted`, so a positive value means the model came in UNDER
    and the correction is ADDED. A port that subtracted would double the bias
    instead of removing it and nothing would fail — the number would simply be
    wrong in the direction the record already leans, which is the hardest kind
    of wrong to notice. 43 stored notes had exactly this direction backwards
    before prompt rule 218 existed.

    The threshold cases matter for the same reason `llm_should_reason`'s
    three-valued ones do: a model below the check count must be ABSENT from
    the correction map rather than present with a zero, and a consensus with
    no corrected member at all must be null rather than the raw mean.
    """
    class _Entry:
        def __init__(self, model, lead, err, checks):
            self.model, self.lead_time_days = model, lead
            self.avg_wind_error_kmh_10, self.checks_in_window_10 = err, checks

    # The record's own Day+0 figures on 2026-09-14, plus the cases that only
    # a constructed row can reach.
    entries = [
        _Entry("ecmwf_ifs025", 0, 12.96, 10),
        _Entry("gfs_seamless", 0, 16.47, 10),
        # Enough of a record to have an error, not enough to trust it.
        _Entry("icon_seamless", 0, 12.76, 9),
        # Verified, and nothing measured its wind — the met service files none.
        _Entry("kenya_met", 0, None, 10),
        # A different lead time never leaks into the Day+0 correction.
        _Entry("ecmwf_ifs025", 3, 99.0, 30),
    ]

    corrections = gust_corrections(entries)

    def preds(pairs):
        return [ModelPrediction(model=m, rain=False, wind_kmh=w) for m, w in pairs]

    consensus_cases = [
        ("both models corrected", preds([("ecmwf_ifs025", 30.0), ("gfs_seamless", 26.0)])),
        ("an uncorrected model is left out of the mean",
         preds([("ecmwf_ifs025", 30.0), ("icon_seamless", 5.0)])),
        ("no corrected member is null, never the raw mean",
         preds([("icon_seamless", 5.0), ("kenya_met", 5.0)])),
        ("a missing gust is skipped", preds([("ecmwf_ifs025", None), ("gfs_seamless", 26.0)])),
        # AN EMPTY CORRECTION MAP, which is what every run looks like until the
        # record has ten verified checks. Distinct from the case above: there
        # the map has members and none of them match, here there is no map at
        # all, and they reach the null by different branches. Without this the
        # early return was never executed and mutating it to the raw mean
        # SURVIVED the vector on the Dart side.
        ("no corrections at all is null", preds([("ecmwf_ifs025", 30.0)]), {}),
    ]

    write(
        "gust_calibration.json",
        "calibrated_gust_consensus",
        "The Day+0 consensus gust with each model's own measured bias added "
        "back. The correction is the record's stored actual-minus-predicted, "
        "so it is ADDED; a model without enough verified checks is absent "
        "from it rather than corrected by zero.",
        [
            {
                "name": "the correction map itself",
                "input": {
                    "entries": [
                        {
                            "model": e.model,
                            "lead_time_days": e.lead_time_days,
                            "avg_wind_error_kmh_10": e.avg_wind_error_kmh_10,
                            "checks_in_window_10": e.checks_in_window_10,
                        }
                        for e in entries
                    ]
                },
                "expected": corrections,
            }
        ]
        + [
            {
                "name": name,
                "input": {
                    "corrections": case_corrections,
                    "predictions": [dump(p) for p in ps],
                },
                "expected": calibrated_gust_consensus(ps, case_corrections),
            }
            for name, ps, case_corrections in (
                (c if len(c) > 2 else (*c, corrections)) for c in consensus_cases
            )
        ],
    )


def export_llm_should_reason() -> None:
    """Whether an issuance earns an LLM call — ROADMAP items 121 and 120.

    NOT ARITHMETIC, AND VECTOR-TESTED ANYWAY. Item 120 settles that this
    policy is declared on both sides, OLW's location.yaml and the app's
    settings, so the app will decide the same question against the same
    signals. A divergence here does not produce a wrong number; it produces a
    client that spends where the server would not, or stays silent where the
    server would speak, and neither shows up as a failure anywhere.

    THE THREE-VALUED CASES ARE THE ONES THAT MATTER. `guidance_is_newer` is
    True/False/None and None means NO BASIS — an entry written before the
    cycle was recorded, or a run that knows less than its predecessor did. It
    resolves toward spending, and a port that read it as falsey would silently
    stop refreshing exactly the entries with the least information behind
    them.
    """
    cases = [
        ("first issuance always reasons", True, None, None, LLMRefreshPolicy.NEW_CYCLE_ONLY),
        ("first issuance reasons under every policy", True, False, [],
         LLMRefreshPolicy.NEW_CYCLE_OR_CONTRADICTION),
        ("a new cycle earns the call", False, True, [], LLMRefreshPolicy.NEW_CYCLE_ONLY),
        ("same cycle, nothing bought", False, False, [], LLMRefreshPolicy.NEW_CYCLE_ONLY),
        ("no basis resolves toward spending", False, None, [], LLMRefreshPolicy.NEW_CYCLE_ONLY),
        ("always spends on the same cycle", False, False, [], LLMRefreshPolicy.ALWAYS),
        ("always spends with no basis", False, None, None, LLMRefreshPolicy.ALWAYS),
        # C2's third trigger, which only this policy acts on.
        ("a contradiction is ignored by default", False, False, ["rain_observed_while_dry_called"],
         LLMRefreshPolicy.NEW_CYCLE_ONLY),
        ("a contradiction earns the call when declared", False, False,
         ["rain_observed_while_dry_called"], LLMRefreshPolicy.NEW_CYCLE_OR_CONTRADICTION),
        ("an empty disagreement list is not a contradiction", False, False, [],
         LLMRefreshPolicy.NEW_CYCLE_OR_CONTRADICTION),
        # `None` is "the station was never looked at", `[]` is "looked and
        # found nothing". Neither is a contradiction, and a port that treated
        # None as a contradiction would re-forecast on every unreadable
        # station — the decision ROADMAP item 104 records as taken.
        ("an unreadable station is not a contradiction", False, False, None,
         LLMRefreshPolicy.NEW_CYCLE_OR_CONTRADICTION),
    ]
    write(
        "llm_should_reason.json",
        "llm_should_reason",
        "Whether this issuance buys a judgment and a narrative, or refreshes "
        "what the station has seen and stops. Observations refresh either way.",
        [
            {
                "name": name,
                "input": {
                    "first_issuance_of_day": first,
                    "guidance_is_newer": newer,
                    "observation_disagreements": disagreements,
                    "policy": str(policy),
                },
                "expected": llm_should_reason(
                    InformationMoved(
                        first_issuance_of_day=first,
                        guidance_is_newer=newer,
                        observation_disagreements=disagreements,
                    ),
                    policy,
                ),
            }
            for name, first, newer, disagreements, policy in cases
        ],
    )


def export_comparison_subject() -> None:
    """The daypart gate on the day-over-day comparison — ROADMAP item 104,
    contract item 8.

    THE OPERATOR'S FIVE SCENARIOS, pinned so both languages gate identically.
    A client that showed a comparison at 15:00, or withheld one at 06:00,
    would not fail anything else — the sentence would simply be present or
    absent, which reads as the weather being unremarkable rather than as a
    bug. That is the failure mode vectors exist for.

    The boundary cases carry the decisions: noon is AFTERNOON, and the sunset
    pivot is strictly after, so 18:00 on a day whose sun sets at 18:00 is
    still today and produces nothing.
    """
    cases = [
        ("03:00 — the whole day is ahead", 3, 18, "today"),
        ("06:00 — the day is ahead", 6, 18, "today"),
        ("11:00 — still morning", 11, 18, "today"),
        ("noon is afternoon, the comparison has gone", 12, 18, None),
        ("15:00 — lived enough of it", 15, 18, None),
        ("18:00 — at sunset is still today", 18, 18, None),
        ("19:00 — past sunset, the day ahead is tomorrow", 19, 18, "tomorrow"),
        ("20:00 — the operator's evening case", 20, 18, "tomorrow"),
        ("23:00 — still tomorrow", 23, 18, "tomorrow"),
        # No sunset: the morning half works, the evening pivot never fires.
        ("no sunset, morning still compares", 6, None, "today"),
        ("no sunset, afternoon still does not", 15, None, None),
        ("no sunset, evening does not promote", 20, None, None),
        # Absence is not permission — _issued_hour returns 24 when the moment
        # could not be established.
        ("an unestablished clock compares nothing", 24, 18, None),
        ("a null clock compares nothing", None, 18, None),
    ]
    write(
        "comparison_subject.json",
        "comparison_subject",
        "Whether a day-over-day comparison appears at this hour and what it is "
        "about: 'today', 'tomorrow', or null for not at all.",
        [
            {
                "name": name,
                "input": {"issued_hour": hour, "sunset_hour": sunset},
                "expected": comparison_subject(hour, sunset_hour=sunset),
            }
            for name, hour, sunset, _ in cases
        ],
    )


def export_observed_so_far() -> None:
    """The observed block, composed in code rather than paid for — item 121.

    Vector-tested because it is the fifth thing this project composes as a
    finished sentence and hands over to be used verbatim, and because three of
    its six dimensions round. Rounding is the divergence this repo has been
    bitten by most: item 88 found ten, and `_roundHalfEven`, `_fmt0` and the
    whole of rounding.dart exist for it. The tie cases below are chosen to be
    where Dart's half-away-from-zero `.round()` parts company with Python's
    half-to-even, not a spread of ordinary values.

    THE THREE-VALUED CASES MATTER AS MUCH AS THE ARITHMETIC. A `False` is
    reported and a `None` is omitted, and a port that collapsed the two would
    publish "no rain" on the strength of not having looked.
    """
    cases = [
        ("every dimension, contract order", ObservedSoFar(
            precipitation=True, precipitation_onset="13:00", thunder=True,
            high_c=27.4, low_c=18.1, peak_wind_kmh=31.4, cloud_oktas=5.5), "14:00"),
        ("negatives are reported, absences omitted", ObservedSoFar(
            precipitation=False, thunder=False, high_c=22.0), "09:00"),
        ("rain with no first-seen time", ObservedSoFar(
            precipitation=True, precipitation_onset=None), "12:00"),
        ("a station that measured nothing", ObservedSoFar(), "14:00"),
        ("no issuance clock", ObservedSoFar(thunder=True), None),
        ("thunder without rain is a real outcome", ObservedSoFar(
            precipitation=False, thunder=True), "16:00"),
        # The ties. Half-to-even sends .5 to the EVEN neighbour, so 32.5 goes
        # down and 33.5 goes up — the pair is the point, either alone passes
        # under half-away-from-zero too.
        ("temperature tie, rounds down to even", ObservedSoFar(high_c=32.5), "15:00"),
        ("temperature tie, rounds up to even", ObservedSoFar(high_c=33.5), "15:00"),
        ("temperature tie below zero", ObservedSoFar(high_c=-0.5), "15:00"),
        ("gust tie, rounds down to even", ObservedSoFar(peak_wind_kmh=30.5), "15:00"),
        ("gust tie, rounds up to even", ObservedSoFar(peak_wind_kmh=31.5), "15:00"),
        ("sky tie, rounds up to even", ObservedSoFar(cloud_oktas=5.5), "15:00"),
        ("sky tie, rounds down to even", ObservedSoFar(cloud_oktas=6.5), "15:00"),
        ("overcast", ObservedSoFar(cloud_oktas=8.0), "15:00"),
        ("clear", ObservedSoFar(cloud_oktas=0.0), "15:00"),
    ]
    write(
        "observed_so_far.json",
        "describe_observed_so_far",
        "One finished sentence for what the station has already measured today, "
        "or null when it has measured nothing. A False is reported because the "
        "station looked; a null is omitted because it did not.",
        [
            {
                "name": name,
                "input": {
                    "observed": {
                        "precipitation": o.precipitation,
                        "precipitation_onset": o.precipitation_onset,
                        "thunder": o.thunder,
                        "high_c": o.high_c,
                        "low_c": o.low_c,
                        "peak_wind_kmh": o.peak_wind_kmh,
                        "cloud_oktas": o.cloud_oktas,
                    },
                    "as_of": as_of,
                },
                "expected": describe_observed_so_far(o, as_of=as_of),
            }
            for name, o, as_of in cases
        ],
    )


def export_temp_high_low() -> None:
    """The headline temperature line, in both units.

    Vector-tested because it is arithmetic that used to be an LLM's job and
    drifted: a blended high of 33.5 C was published as "34C / 93F" when 33.5 C
    is 92.3 F. The .5 cases below are the cross-language edge — Python's round
    is half-to-even and Dart's is half away from zero, the same divergence that
    once put "1006 hPa" on the site and "1007 hPa" in the app."""
    scenarios = [
        ("the live case that prompted this", 33.5, 18.0),
        ("yesterday, for the comparison", 32.3, 18.0),
        ("half to EVEN rounds down here", 32.5, 17.5),
        ("half to EVEN rounds up here", 33.5, 18.5),
        ("whole numbers stay put", 30.0, 20.0),
        ("a cold low", 28.0, -1.5),
        ("below freezing both ends", -2.5, -7.5),
        ("fahrenheit crossing a ten", 37.2, 21.7),
    ]
    cases = [
        {
            "name": name,
            "input": {"high_c": high, "low_c": low},
            "expected": format_temp_high_low(high, low),
        }
        for name, high, low in scenarios
    ]
    write(
        "temp_high_low.json",
        "format_temp_high_low",
        "The headline temperature string. Each unit is rounded from the true "
        "Celsius value rather than one from the other, so the pair does not "
        "round-trip: 33.5 C gives 34C / 92F, and 34 C is 93.2 F. Rounding "
        "twice is what this replaced.",
        cases,
    )


def export_instability() -> None:
    """Whether the Overview must mention thunder. A threshold decision, so it
    belongs in code and has to agree across implementations."""
    models = ["gfs_seamless", "icon_seamless", "ukmo_seamless"]
    times = ["2026-08-26T12:00", "2026-08-26T15:00", "2026-08-26T18:00"]

    def payload(**series):
        return {"hourly": {"time": times, **series}}

    scenarios = [
        ("no data", {}),
        ("no cape series", payload()),
        ("all-null cape series", payload(cape_gfs_seamless=[None, None, None])),
        (
            "quiet afternoon",
            payload(
                cape_gfs_seamless=[10.0, 120.0, 80.0],
                cape_icon_seamless=[20.0, 200.0, 150.0],
            ),
        ),
        (
            "models disagree, two above threshold",
            payload(
                cape_gfs_seamless=[50.0, 300.0, 180.0],
                cape_icon_seamless=[100.0, 2400.0, 1900.0],
                cape_ukmo_seamless=[90.0, 2600.0, 2100.0],
            ),
        ),
        (
            "one model alone above threshold",
            payload(
                cape_gfs_seamless=[10.0, 20.0, 30.0],
                cape_icon_seamless=[10.0, 1500.0, 40.0],
            ),
        ),
        (
            "exactly at the threshold",
            payload(cape_gfs_seamless=[0.0, CONVECTIVE_CAPE_THRESHOLD_JKG, 0.0]),
        ),
        (
            "one below the threshold",
            payload(cape_gfs_seamless=[0.0, CONVECTIVE_CAPE_THRESHOLD_JKG - 1, 0.0]),
        ),
        ("nulls within a series are skipped", payload(cape_gfs_seamless=[None, 1400.0, None])),
        ("unsuffixed series for a single-model fetch", payload(cape=[0.0, 1200.0, 0.0])),
    ]
    cases = [
        {
            "name": name,
            "input": {
                "hourly_multi_model": hourly,
                "models": models,
                "threshold": CONVECTIVE_CAPE_THRESHOLD_JKG,
            },
            "expected": dump(summarize_instability(hourly, models)),
        }
        for name, hourly in scenarios
    ]
    write(
        "instability.json",
        "summarize_instability",
        "Peak CAPE per model over the hours ahead, and whether any model "
        "crosses the threshold that supports thunderstorms. Max across models, "
        "never the mean — averaging a disagreement away is the failure this "
        "exists to prevent. Absent CAPE is a gap, not a calm afternoon.",
        cases,
    )


def _dump_aligned_cycle(c) -> dict:
    """`dump()`'s dataclass branch is `asdict(value)`, which does NOT convert
    nested datetime fields to strings — `AlignedCycle` is the first exported
    dataclass to hold a raw datetime rather than an already-stringified
    field, and asdict() would hand json.dumps() a datetime object it cannot
    serialize. Converting by hand here rather than teaching dump() a general
    case for two call sites."""
    return {
        "initialised_at": c.initialised_at.isoformat(),
        "window_opened_at": c.window_opened_at.isoformat(),
        "age_hours": c.age_hours,
    }


def export_round_hours() -> None:
    # The values chosen are the ones that MATTER: every case here except the
    # plain ones is a tie or near-tie where Python's own round() disagrees
    # with this function. A port that reimplements this with its language's
    # rounding passes the plain cases and fails these.
    values = [0.0, 0.04, 0.05, 0.15, 0.35, 0.45, 0.65, 0.95, 1.05, 2.45, 6.25, 9.55, 12.25, 12.35, 23.75, 239.95]
    write(
        "round_hours_to_tenths.json",
        "round_hours_to_tenths",
        "One decimal place, half-to-even, done with identical IEEE-754 float "
        "arithmetic in both languages rather than either language's own "
        "rounding — which disagree on 20% of a 0.05-step sweep. See the "
        "function's docstring for the measurement.",
        [
            {"name": f"{v} hours", "input": {"hours": v}, "expected": round_hours_to_tenths(v)}
            for v in values
        ],
    )


def export_blend_prediction() -> None:
    """The forecaster's own Day+0 call, in the form the record scores.

    Locked because the Dart port silently disagreed. `blendPrediction`'s own
    doc comment claimed it mirrored `_blend_prediction`, and it dropped
    `rainProbabilityPct` — so on the app path item 58's probability was asked
    for in the prompt, parsed by the schema, and never scored. Nothing caught
    it: no Dart test named the function and no vector covered either builder.

    The zero case is the one a port fails quietly. `0` is falsy in both
    languages, and a builder that tests truthiness rather than nullness turns
    a forecaster who committed to "no chance of rain" into one who declined to
    answer — which Brier scores differently and the record cannot distinguish
    afterwards.
    """
    from openlocalweather.llm.schema import TodayProperties
    from openlocalweather.pipeline import _blend_prediction

    def tp(**over):
        base = dict(
            rain_expected="Evening showers", onset_window=None,
            peak_wind_primary_kmh=32.8, peak_wind_secondary_kmh=41.0,
            temp_high_c=30.0, temp_low_c=18.0, rain=True, onset_hour="16:00",
            precip_mm=2.5, rain_probability_pct=65, mslp_trend_24h="-0.4 hPa",
            synoptic_pattern="Troughing", uv_index_max="9.4", air_quality_aqi="88",
        )
        base.update(over)
        return TodayProperties(**base)

    cases = [
        ("every field the blend carries", tp()),
        ("no probability offered — absent is not 50", tp(rain_probability_pct=None)),
        ("a committed zero survives, because 0 is falsy in both languages",
         tp(rain=False, rain_probability_pct=0, precip_mm=0.0)),
        ("no onset, because nothing crossed the threshold", tp(onset_hour=None)),
        # ITEM 144. The two gusts DIFFER in every case above, and they are the
        # real pair from 2026-09-16 — the day the ashore section published the
        # Gulf's 41. A port that carried the wrong one, or carried neither,
        # would fail here and pass everywhere else.
        ("no ashore gust offered — absent, never the other point's",
         tp(peak_wind_primary_kmh=None)),
    ]

    write(
        "blend_prediction.json",
        "_blend_prediction",
        "The blended forecaster's Day+0 row. `wind_kmh` carries "
        "`peak_wind_primary_kmh` and ONLY that: item 144 split the one "
        "ambiguous wind field in two, and the ashore one describes the place "
        "the record observes, so it is scorable where the secondary point's "
        "is not. `peak_wind_secondary_kmh` and `mslp_trend_24h` are "
        "deliberately NOT carried — the first is a different place and the "
        "second is prose, and scoring either against the primary point's "
        "observations would compare two different things. The inputs here "
        "give the two points DIFFERENT gusts on purpose.",
        [
            {"name": name, "input": t.model_dump(), "expected": _blend_prediction(t).model_dump()}
            for name, t in cases
        ],
    )


def export_extended_blend_predictions() -> None:
    """The forecaster's own call at Day+3 and Day+7 — ROADMAP item 72.

    Locked because Dart has NO mirror of this at all: its day3/day7 lists
    carry the extracted models only, so the app records no forecaster call at
    exactly the leads where reconciling disagreeing models is worth the most,
    which is item 72's whole argument.

    An empty result is a legitimate answer, not a gap. A guessed boolean is
    scored wrong exactly as confidently as a real one, so a run that declined
    to call a lead should leave no row for it — and a port that emitted a
    default row instead would fill the record with calls nobody made.
    """
    from openlocalweather.llm.schema import ExtendedDayProperties
    from openlocalweather.pipeline import _extended_blend_predictions

    def ext(lead, rain, pct=None):
        return ExtendedDayProperties(lead_time_days=lead, rain=rain, rain_probability_pct=pct)

    both = [ext(3, True, 70), ext(7, False, 20)]
    cases = [
        ("Day+3 is selected out of a two-lead answer", both, 3),
        ("Day+7 is selected out of the same answer", both, 7),
        ("a lead the run declined to call yields no row at all", [ext(3, True, 70)], 7),
        ("nothing committed anywhere", [], 3),
        ("no confidence stated leaves the Brier column empty", [ext(3, True)], 3),
        ("a committed zero is a call, not a refusal", [ext(3, False, 0)], 3),
    ]

    write(
        "extended_blend_predictions.json",
        "_extended_blend_predictions",
        "Rain only. Everything else is absent rather than zero: a field the "
        "forecaster was not asked to commit to must not enter the record as a "
        "value it never gave.",
        [
            {
                "name": name,
                "input": {"extended": [e.model_dump() for e in exts], "lead_time_days": lead},
                "expected": [p.model_dump() for p in _extended_blend_predictions(exts, lead)],
            }
            for name, exts, lead in cases
        ],
    )


def export_cycle() -> None:
    def at(y, m, d, h, minute=0, second=0):
        return datetime(y, m, d, h, minute, second, tzinfo=timezone.utc)

    scenarios = [
        ("20:00 opens the 12z window, today", at(2026, 8, 11, 20, 0, 0)),
        ("14:00 opens the 06z window, today", at(2026, 8, 11, 14, 0, 0)),
        ("08:00 opens the 00z window, today", at(2026, 8, 11, 8, 0, 0)),
        ("02:00 opens the 18z window, yesterday", at(2026, 8, 11, 2, 0, 0)),
        ("00:00 is still the 12z window, yesterday", at(2026, 8, 11, 0, 0, 0)),
        ("one second before 08:00 is still 18z yesterday", at(2026, 8, 11, 7, 59, 59)),
        ("one second before 14:00 is still 00z today", at(2026, 8, 11, 13, 59, 59)),
        ("one second before 20:00 is still 06z today", at(2026, 8, 11, 19, 59, 59)),
        ("one second before 02:00 is still 12z yesterday", at(2026, 8, 11, 1, 59, 59)),
        ("23:59:59 is 12z framed as today", at(2026, 8, 11, 23, 59, 59)),
        (
            "00:00:00 the next day is the SAME 12z run, now framed as yesterday",
            at(2026, 8, 12, 0, 0, 0),
        ),
        ("03:00 UTC matches ROADMAP.md's measured 9h morning row", at(2026, 8, 11, 3, 0, 0)),
        ("15:00 UTC matches ROADMAP.md's measured 9h evening row", at(2026, 8, 11, 15, 0, 0)),
        (
            "the 00:27 incident this function exists to surface: a scored "
            "forecast built on data over 12 hours old",
            at(2026, 8, 28, 0, 27, 0),
        ),
    ]
    cases = [
        {
            "name": name,
            "input": {"now": now.isoformat()},
            "expected": _dump_aligned_cycle(aligned_cycle_at(now)),
        }
        for name, now in scenarios
    ]
    next_scenarios = [
        ("mid-window, the next one is later today", at(2026, 8, 11, 3, 0, 0)),
        ("exactly on a boundary looks forward, not at the window just opened",
         at(2026, 8, 11, 8, 0, 0)),
        ("one second after a boundary", at(2026, 8, 11, 8, 0, 1)),
        ("one second before a boundary is that same boundary", at(2026, 8, 11, 7, 59, 59)),
        ("the last window of the day rolls to tomorrow", at(2026, 8, 11, 21, 30, 0)),
        ("before the day's first window", at(2026, 8, 11, 0, 30, 0)),
        ("23:59:59 points at tomorrow's 02:00", at(2026, 8, 11, 23, 59, 59)),
        ("00:00:00 still points at today's 02:00", at(2026, 8, 12, 0, 0, 0)),
    ]
    def _a(rain, high=None, low=None, wind=None, onset=None, precip=None):
        return DailyActual(
            rain=rain, high_c=high, low_c=low, peak_wind_kmh=wind,
            onset_hour=onset, precip_mm=precip,
        )

    record = {
        date(2026, 8, 1): _a(False, high=20.0, low=10.0, wind=10.0),
        date(2026, 8, 2): _a(True, high=30.0, low=20.0, wind=30.0, onset="15:00"),
        date(2026, 8, 3): _a(True, high=28.0, low=None, wind=20.0),
    }
    tie_record = {
        date(2026, 8, 1) + timedelta(days=i): _a(i == 0, high=25.0)
        for i in range(8)
    }
    # ROADMAP item 88, divergence 8. Climatology averages the record, and
    # Python's sum() is Neumaier-compensated where a plain left-to-right
    # accumulation is not. These ten values are sums.dart's own measured case:
    # five of 0.81 then five of 0.009999999999999995 average to
    # 0.41000000000000003 compensated and 0.41 naively. Mixed magnitudes are
    # what separate the two, and a climatology mean over a real record —
    # millimetres beside tenths of a millimetre — is exactly that shape.
    mixed_magnitude_record = {
        date(2026, 8, 1) + timedelta(days=i): _a(
            i < 5, precip=0.81 if i < 5 else 0.009999999999999995
        )
        for i in range(10)
    }

    write(
        "baselines.json",
        "persistence_prediction / climatology_prediction",
        "The two trivial rules a real model has to beat (ROADMAP item 57). "
        "Persistence repeats the last observation available at issuance; "
        "climatology is the trailing base rate over the record STRICTLY "
        "BEFORE the issuance date. Neither may ever see the day it is "
        "forecasting — a baseline that could peek would score near-perfectly "
        "and make every real model look hopeless, and nothing about the page "
        "would look broken. A climatology tie breaks DRY, because that is the "
        "direction that does not manufacture a rain expectation out of a coin "
        "flip. Averages skip missing values rather than counting them as zero.",
        [
            {
                "name": "persistence repeats a wet day, onset included at Day+0",
                "input": {
                    "fn": "persistence",
                    "last_observed": record[date(2026, 8, 2)].model_dump(mode="json"),
                    "include_onset": True,
                },
                "expected": persistence_prediction(
                    record[date(2026, 8, 2)], include_onset=True
                ).model_dump(mode="json"),
            },
            {
                "name": "persistence drops onset beyond Day+0",
                "input": {
                    "fn": "persistence",
                    "last_observed": record[date(2026, 8, 2)].model_dump(mode="json"),
                    "include_onset": False,
                },
                "expected": persistence_prediction(
                    record[date(2026, 8, 2)], include_onset=False
                ).model_dump(mode="json"),
            },
            {
                "name": "persistence has nothing to say with no observation",
                "input": {"fn": "persistence", "last_observed": None, "include_onset": False},
                "expected": None,
            },
            {
                "name": "climatology over a mostly-wet record calls rain",
                "input": {
                    "fn": "climatology",
                    "actuals": {d.isoformat(): a.model_dump(mode="json") for d, a in record.items()},
                    "before": "2026-08-04",
                },
                "expected": climatology_prediction(
                    record, before=date(2026, 8, 4)
                ).model_dump(mode="json"),
            },
            {
                "name": "climatology cannot see the day it forecasts, nor later",
                "input": {
                    "fn": "climatology",
                    "actuals": {d.isoformat(): a.model_dump(mode="json") for d, a in record.items()},
                    "before": "2026-08-02",
                },
                "expected": climatology_prediction(
                    record, before=date(2026, 8, 2)
                ).model_dump(mode="json"),
            },
            {
                "name": "an exact half breaks dry",
                "input": {
                    "fn": "climatology",
                    "actuals": {
                        d.isoformat(): a.model_dump(mode="json")
                        for d, a in list(record.items())[:2]
                    },
                    "before": "2026-08-03",
                },
                "expected": climatology_prediction(
                    dict(list(record.items())[:2]), before=date(2026, 8, 3)
                ).model_dump(mode="json"),
            },
            {
                "name": "climatology on an empty record says nothing",
                "input": {"fn": "climatology", "actuals": {}, "before": "2026-08-03"},
                "expected": None,
            },
            {
                "name": "climatology's mean uses compensated summation",
                # Compared with NO tolerance: this case exists to pin a
                # last-bit difference of about 5.6e-17, and the Dart reader's
                # default 1e-9 would pass a naive reduce. See deepMatches.
                "compare": "exact",
                "input": {
                    "fn": "climatology",
                    "actuals": {
                        d.isoformat(): a.model_dump(mode="json")
                        for d, a in mixed_magnitude_record.items()
                    },
                    "before": "2026-08-11",
                },
                "expected": climatology_prediction(
                    mixed_magnitude_record, before=date(2026, 8, 11)
                ).model_dump(mode="json"),
            },
            {
                "name": "climatology's 12.5 percent base rate rounds half-even",
                "input": {
                    "fn": "climatology",
                    "actuals": {d.isoformat(): a.model_dump(mode="json") for d, a in tie_record.items()},
                    "before": "2026-08-10",
                },
                "expected": climatology_prediction(tie_record, before=date(2026, 8, 10)).model_dump(mode="json"),
            },
        ],
    )

    write(
        "next_aligned_window.json",
        "next_aligned_window",
        "When the next aligned window opens and which cycle it will carry — "
        "the forward-looking half of aligned_cycle_at, reading the same "
        "measured table. STRICTLY AFTER the given moment, including exactly "
        "on a boundary: at 08:00 the 08:00 window has just opened, and "
        "pointing a reader at it would tell them to wait for guidance they "
        "already hold. Like aligned_cycle_at it is an inference about when "
        "guidance USUALLY lands, never a promise.",
        [
            {
                "name": name,
                "input": {"now": now.isoformat()},
                "expected": {
                    "opens_at": next_aligned_window(now).opens_at.isoformat(),
                    "initialised_at": next_aligned_window(now).initialised_at.isoformat(),
                },
            }
            for name, now in next_scenarios
        ],
    )
    write(
        "aligned_cycle.json",
        "aligned_cycle_at",
        "Which model run this project INFERS is aligned across all five "
        "models at a given moment, from the measured availability table in "
        "docs-internal/ROADMAP.md (search 'Aligned windows open at'). Not an "
        "observation — nothing in an Open-Meteo response says which cycle "
        "produced it. The 12z window is the one row that spans midnight, so "
        "it is the only cycle reached by two different date-relative rows.",
        cases,
    )


def export_solar() -> None:
    from openlocalweather.dates import utc_offset_seconds
    from openlocalweather.solar import sun_times

    # Every case is checked against a source outside this project before it is
    # exported — see tests/test_solar.py, which pins the same values and says
    # where each came from. The selection is not a spread of ordinary days: it
    # is the places a port parts company with this implementation.
    cases = []
    for name, lat, lon, tz, day in [
        (
            "Kisumu, the day the Met Department bulletin covers",
            -0.0917, 34.7680, "Africa/Nairobi", "2026-08-19",
        ),
        (
            "Kisumu, the date the daypart vectors are built on",
            -0.0917, 34.7680, "Africa/Nairobi", "2026-08-22",
        ),
        (
            "London at the solstice — a 16h33m day",
            51.5072, -0.1276, "Europe/London", "2026-06-21",
        ),
        (
            "Sydney in August — the southern hemisphere runs the other way",
            -33.87, 151.21, "Australia/Sydney", "2026-08-28",
        ),
        (
            "the midnight sun: midnight to midnight, a 24h span",
            78.2232, 15.6469, "Arctic/Longyearbyen", "2026-06-21",
        ),
        (
            "polar night: both times local midnight, a span of zero",
            78.2232, 15.6469, "Arctic/Longyearbyen", "2025-12-21",
        ),
        (
            "Kiritimati, whose UTC+14 disagrees with its longitude by a day",
            1.87, -157.40, "Pacific/Kiritimati", "2026-08-28",
        ),
        (
            "London the day the clocks go back",
            51.5072, -0.1276, "Europe/London", "2025-10-26",
        ),
        (
            "Longyearbyen three days after the midnight sun ends",
            78.2232, 15.6469, "Arctic/Longyearbyen", "2026-08-27",
        ),
    ]:
        d = date.fromisoformat(day)
        offset = utc_offset_seconds(tz, d)
        cases.append(
            {
                "name": name,
                "input": {
                    "lat": lat,
                    "lon": lon,
                    "day": day,
                    "utc_offset_seconds": offset,
                },
                "expected": sun_times(lat, lon, d, offset).to_json(),
            }
        )

    write(
        "solar.json",
        "sun_times",
        "Sunrise and sunset for a LOCAL date, computed from latitude, "
        "longitude and the location's UTC offset — no network. Whole minutes, "
        "TRUNCATED, which is what Open-Meteo and the Kenya Met Department "
        "bulletin both do; rounding would publish a minute later than the "
        "sources a reader can check. Two conventions carry meaning rather "
        "than data: the midnight sun is local midnight to local midnight, a "
        "24 hour span, and polar night is local midnight to local midnight, a "
        "span of zero. daypart.classify_phase reads those spans to reach its "
        "polar phases, so an implementation that returned null instead would "
        "pass nothing here and silently disable them.",
        cases,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Exporting cross-language test vectors:")
    export_daypart()
    export_solar()
    export_dates()
    export_weekday_name()
    export_forward_calendar()
    export_false_weekday_claims()
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
    export_verification()
    export_observation_disagreements()
    export_low_divergence()
    export_wind_direction()
    export_comparison_for_prompt()
    export_day_over_day()
    export_extended_trend()
    export_describe_day_rain()
    export_observed_so_far()
    export_comparison_subject()
    export_prompt_rounding()
    export_gust_calibration()
    export_llm_should_reason()
    export_describe_day_over_day()
    export_glossary()
    export_temp_high_low()
    export_instability()
    export_cycle()
    export_round_hours()
    export_blend_prediction()
    export_extended_blend_predictions()
    print("\nDone. Commit the result — the vectors are the contract.")


if __name__ == "__main__":
    main()
