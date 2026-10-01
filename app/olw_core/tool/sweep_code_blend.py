"""Cases and Python's answers for sweep_code_blend.dart — upstream item 189.

    .venv/bin/python app/olw_core/tool/sweep_code_blend.py /tmp/cases.json /tmp/want.json
    (cd app/olw_core && dart run tool/sweep_code_blend.dart /tmp/cases.json /tmp/want.json)

Random voters over the real models, weights and corrections of the shape
`rain_weights` and `temperature_corrections` produce, onsets on the hour and
the half hour, amounts to three decimals so the one-decimal rounding meets
every tie, and the served gust handed in or absent.
"""

import json
import random
import sys

from openlocalweather.code_blend import code_blend_prediction
from openlocalweather.models import ModelPrediction

MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless", "jma_seamless"]
CASES = 20_000


def _case(rng: random.Random) -> dict:
    models = rng.sample(MODELS, rng.randint(1, len(MODELS)))
    predictions = []
    for m in models:
        rain = rng.choice([True, False, None])
        predictions.append(
            ModelPrediction(
                model=m,
                rain=rain,
                onset=rng.choice([None, f"{rng.randint(0, 23):02d}:{rng.choice(['00', '30'])}"]),
                precip_mm=rng.choice([None, round(rng.uniform(0, 40), rng.randint(0, 3))]),
                high_c=rng.choice([None, round(rng.uniform(15, 40), 1)]),
                low_c=rng.choice([None, round(rng.uniform(5, 25), 1)]),
                rain_probability_pct=None,
            )
        )
    weights = {m: round(rng.uniform(0.05, 1.0), 4) for m in models if rng.random() < 0.9}
    highs = {m: round(rng.uniform(-3, 3), 2) for m in models if rng.random() < 0.5}
    lows = {m: round(rng.uniform(-3, 3), 2) for m in models if rng.random() < 0.5}
    wind = rng.choice([None, round(rng.uniform(5, 80), 2)])
    row = code_blend_prediction(predictions, weights, highs, lows, wind_kmh=wind)
    return {
        "case": {
            "predictions": [p.model_dump(mode="json") for p in predictions],
            "weights": weights,
            "highs": highs,
            "lows": lows,
            "wind_kmh": wind,
        },
        "want": None if row is None else row.model_dump(mode="json"),
    }


def main(cases_path: str, want_path: str) -> None:
    rng = random.Random(189)
    pairs = [_case(rng) for _ in range(CASES)]
    with open(cases_path, "w") as f:
        json.dump([p["case"] for p in pairs], f)
    with open(want_path, "w") as f:
        json.dump([p["want"] for p in pairs], f)
    print(f"wrote {CASES} cases")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
