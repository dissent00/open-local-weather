"""Cases and Python's answers for sweep_brief.dart — the brief, item 191.

    .venv/bin/python app/olw_core/tool/sweep_brief.py /tmp/cases.json /tmp/want.json
    (cd app/olw_core && dart run tool/sweep_brief.dart /tmp/cases.json /tmp/want.json)

Random briefs over every block that formats a number: the served call, the
day table, the per-model tables, the basin pressure reduced from raw points,
the record, the air quality, the recency, the CAPE line. Values at every
precision the pipeline produces, exact halves where Python rounds to even,
a three-day change that rounds to negative zero, whole numbers as strings,
and review claims that tie on their sort key.
"""

import json
import random
import sys

from openlocalweather.brief import SECTIONS, TIER_FULL, TIER_MINI, BriefInputs, basin_pressure, render_brief

MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless", "best_match"]
DAYS = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
HALVES = [0.5, 1.5, 22.5, 23.5, 0.25, 0.35, 2.45, 2.675, 1012.85, 99.5, 100.5, 0.05, 0.15]
CLAIMS = [
    "At Day+0, gfs_seamless systematically over-forecasts daytime highs here.",
    "At Day+0, no model is meaningfully better than the others here yet.",
    "At Day+3, ecmwf_ifs025 slightly under-forecasts peak wind here.",
    "At Day+7, best_match and ecmwf_ifs025 share one rain probability here.",
    "At Day+0, kenya_met runs warm on highs here.",
]
CASES = 3_000


def _case(rng: random.Random) -> dict:
    def f(lo, hi, nd=None):
        if rng.random() < 0.12:
            return rng.choice(HALVES)
        return round(rng.uniform(lo, hi), rng.randint(0, 3) if nd is None else nd)

    def maybe(value, p=0.15):
        return None if rng.random() < p else value

    def num_or_str(value):
        return rng.choice([value, str(value), str(float(value)) if isinstance(value, int) else value])

    call = {
        "today_properties": {
            "rain": rng.choice([True, False, None]),
            "rain_expected": rng.choice(["Showers", "Dry / No Rain", ""]),
            "onset_window": rng.choice([None, "13:00 – 15:00", "From 16:00"]),
            "onset_hour": rng.choice([None, "14:00"]),
            "precip_mm": maybe(f(0, 40)),
            "rain_probability_pct": maybe(rng.choice([f(0, 100, 0), f(0, 100, 1), rng.randint(0, 100)])),
            "peak_wind_primary_kmh": maybe(f(0, 80)),
            "peak_wind_secondary_kmh": maybe(f(0, 80)),
            "mslp_trend_24h": rng.choice([None, "+0.5 hPa", "-1.2 hPa", ""]),
            "synoptic_pattern": rng.choice([None, "Weak gradient", ""]),
            "air_quality_aqi": maybe(rng.choice([rng.randint(0, 300), f(0, 300, 1)])),
        },
        "extended_properties": [
            {"lead_time_days": lead, "rain": rng.choice([True, False, None]),
             "rain_probability_pct": maybe(rng.choice([rng.randint(0, 100), f(0, 100, 1)]))}
            for lead in rng.sample([3, 7], rng.randint(0, 2))
        ],
    }
    days = []
    for lead in range(1, rng.randint(1, 8)):
        models = rng.sample(MODELS, rng.randint(0, 4))
        days.append({
            "lead_time_days": lead, "date": f"2026-10-{10 + lead:02d}", "day_name": DAYS[lead % 7],
            "models": models, "wet_votes": rng.randint(0, max(len(models), 1)),
            "precip_mm": maybe(f(0, 30, 1)),
            "precip_by_model": {m: maybe(f(0, 30), 0.2) for m in models},
            "high_c": maybe(f(20, 36, 1)), "high_min_c": maybe(f(20, 36)), "high_max_c": maybe(f(20, 36)),
            "low_c": maybe(f(10, 25)), "wind_kmh": maybe(f(5, 60)),
            "thunder": rng.choice([None, "possible", "likely"]), "sky": rng.choice([None, "Mostly cloudy", "Clear"]),
        })
    instability = rng.choice([None, {
        "convective": rng.choice([True, False]),
        "timing": rng.choice([None, "thunder possible from midday, peaking this evening"]),
        "peak_hour": rng.choice([None, "17:00"]),
        "peak_cape_by_model": {m: maybe(rng.choice([f(0, 5000, 0), f(0, 5000, 1), rng.randint(0, 5000)])) for m in rng.sample(MODELS, rng.randint(0, 4))},
        "models_above_threshold": rng.sample(MODELS, rng.randint(0, 2)),
    }])
    points = [
        {"daily": {"pressure_msl_mean": [maybe(f(995, 1030), 0.1) for _ in range(rng.randint(0, 4))]}}
        for _ in range(rng.randint(0, 6))
    ]
    if rng.random() < 0.1:
        # A change that rounds to negative zero.
        points = [{"daily": {"pressure_msl_mean": [1012.8, 1012.5, 1012.76]}}]
    track = [
        {"model": m, "lead_time_days": str(lead),
         "rolling_30_rain_pct": maybe(rng.choice([str(f(0, 100, 1)), str(rng.randint(0, 100)), str(float(rng.randint(0, 100)))])),
         "all_time_checks": maybe(str(rng.randint(0, 80)))}
        for m in MODELS + ["olw_blend", "kenya_met"] for lead in (0, 3, 7) if rng.random() < 0.7
    ]
    predictions = [
        {"lead": lead, "model": m, "rain": rng.choice(["true", "false", None]), "onset": rng.choice([None, "13:00"]),
         "precip_mm": maybe(str(f(0, 20))), "rain_probability_pct": maybe(str(rng.randint(0, 100))),
         "wind_kmh": maybe(str(f(0, 60, 1))), "high_c": maybe(str(f(20, 36, 1))), "low_c": maybe(str(f(10, 25, 1))),
         "peak_cape_jkg": maybe(rng.choice([str(f(0, 5000, 1)), str(float(rng.randint(0, 5000)))]))}
        for lead in ("day0", "day3", "day7") for m in rng.sample(MODELS + ["kenya_met"], rng.randint(0, 5))
    ]
    stations = [
        {"name": f"S{k}", "aqi": maybe(str(rng.randint(0, 200))), "pm25": maybe(str(f(0, 200, 1))),
         "pm10": maybe(str(f(0, 200, 1))), "stale": rng.choice(["true", "false"])}
        for k in range(rng.randint(0, 3))
    ]
    findings = [{"kind": rng.choice(["ranking", "bias", "tendency"]), "checks": rng.randint(5, 60), "claim": rng.choice(CLAIMS)}
                for _ in range(rng.randint(0, 16))]
    inputs = BriefInputs(
        calendar=[{"lead_time_days": str(lead), "date": f"2026-10-{10 + lead:02d}", "day_name": DAYS[lead % 7]} for lead in range(8)],
        recency=rng.choice([None, {"models_last_aligned_at": "2026-10-08T18:00:00+00:00", "hours_old": rng.choice([9.0, 9, 10.5, f(0, 30, 2)])}]),
        instability=instability,
        peak_uv=rng.choice([None, {"index": rng.choice([9.4, 9, 10.0, f(0, 12, 2)]), "date": "2026-10-10", "source": "gfs_seamless"}]),
        ground_aqi_stations=stations,
        ground_aqi_summary=rng.choice([None, {"highest_station_name": "S0", "aqi_max": rng.randint(0, 200), "stations_with_aqi": rng.randint(0, 3), "stations_total": 3}]),
        ground_aqi_last_known=rng.choice([None, {"station_name": "S1", "aqi": rng.randint(0, 200), "hours_old": rng.choice([3.0, 3, 27.5])}]),
        predictions=predictions,
        basin_pressure=basin_pressure(points),
        review_findings=findings,
        data_sufficiency=rng.choice([None, "Reviewed 56 day(s); best_match repeats ecmwf_ifs025; kenya_met is thin."]),
        track_record=track,
        served_call=call,
        temp_display=rng.choice([None, "31°C / 88°F high, 19°C / 67°F low"]),
        extended_days=days,
        secondary_name=rng.choice([None, "Winam Gulf"]),
        met_service_name=rng.choice([None, "Kenya Met"]),
        met_service_model_id="kenya_met",
    )
    return {
        "points": points,
        "inputs": inputs.to_json(),
        "basin": basin_pressure(points),
        "full": render_brief(inputs, tier=TIER_FULL, sections=SECTIONS),
        "mini": render_brief(inputs, tier=TIER_MINI, sections=SECTIONS),
    }


def main() -> None:
    rng = random.Random(20261010)
    cases = [_case(rng) for _ in range(CASES)]
    with open(sys.argv[1], "w") as f:
        json.dump([{"points": c["points"], "inputs": c["inputs"]} for c in cases], f, ensure_ascii=False)
    with open(sys.argv[2], "w") as f:
        json.dump([{"basin": c["basin"], "full": c["full"], "mini": c["mini"]} for c in cases], f, ensure_ascii=False)
    print(f"wrote {len(cases)} cases; {sum(len(c['full']) for c in cases) // 1000:,}K chars of full briefs")


if __name__ == "__main__":
    main()
