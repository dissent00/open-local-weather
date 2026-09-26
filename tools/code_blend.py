"""Score the record-weighted code blend beside the LLM — ROADMAP item 173.

For every archived day on which the LLM's call (`olw_blend`) was scored, this
builds the code blend from that day's FIRST issuance — the row the record
scores — with weights from the record as it stood before the issuance, and
scores both with production's `score_prediction` against the same actuals.

SAME DAYS OR NOT AT ALL. Item 182's all-time table put olw_blend's 27 checks
beside the models' 44, over different days. Every figure
here is over the days olw_blend was scored, and every Brier over the days it
gave a probability, so no row is flattered by the days it happened to cover.

`follow_leader` is a reference, not a candidate: whichever input had the best
rolling-30 hit rate that morning, called as-is. Item 182 asked whether the
LLM beats "simply trusting the best-scoring model"; this is that model.

Usage:
  python tools/code_blend.py backtest
"""
import math
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.code_blend import (  # noqa: E402
    RAIN_WEIGHT_MIN_CHECKS,
    blend_inputs,
    code_blend_predictions,
    windows_as_of,
)
from openlocalweather.config import load_location_config  # noqa: E402
from openlocalweather.defaults import (  # noqa: E402
    BASELINE_MODEL_IDS,
    BEST_MATCH_MODEL_ID,
    BLEND_MODEL_ID,
    CODE_BLEND_MODEL_ID,
    LEAD_TIMES_DAYS,
    ROLLING_WINDOW_LONG,
)
from openlocalweather.instability import convective_tier  # noqa: E402
from openlocalweather.models import ModelPrediction  # noqa: E402
from openlocalweather.store.actuals_cache import as_date_dict, read_actuals_cache  # noqa: E402
from openlocalweather.store.log_store import list_log_dates, make_log_lookup  # noqa: E402
from openlocalweather.verify.scoring import scored_predictions, score_prediction  # noqa: E402

LEADER_ID = "follow_leader"


def _leader(predictions: dict[str, ModelPrediction], windows: dict) -> ModelPrediction | None:
    """The best rolling-30 hit rate among inputs with a call; ties go to the
    earlier input, which is max()'s rule. Rain and its probability only."""
    ranked = [
        m
        for m, w in windows.items()
        if w.rain_pct is not None
        and w.checks_found >= RAIN_WEIGHT_MIN_CHECKS
        and m in predictions
        and predictions[m].rain is not None
    ]
    if not ranked:
        return None

    best = predictions[max(ranked, key=lambda m: windows[m].rain_pct)]

    return ModelPrediction(model=LEADER_ID, rain=best.rain, rain_probability_pct=best.rain_probability_pct)


def _sign_test_p(a: int, b: int) -> float:
    """Exact two-sided binomial p for a split of a against b at even odds."""
    n = a + b
    if n == 0:
        return 1.0

    tail = sum(math.comb(n, k) for k in range(min(a, b) + 1)) / 2**n

    return min(1.0, 2 * tail)


def _call(p: ModelPrediction | None) -> str:
    if p is None or p.rain is None:
        return "  -   "

    pct = "--" if p.rain_probability_pct is None else f"{p.rain_probability_pct:>2}"

    return f"{'wet' if p.rain else 'dry'} {pct}"


def _mean(values) -> str:
    values = [v for v in values if v is not None]

    return f"{sum(values) / len(values):.3f}" if values else "  -  "


def _backtest() -> int:
    data = ROOT / "data"
    location = load_location_config(ROOT / "config/location.yaml")
    inputs = blend_inputs(location.local_bulletin_model_id)
    shown = [BLEND_MODEL_ID, CODE_BLEND_MODEL_ID, LEADER_ID, *inputs, BEST_MATCH_MODEL_ID, *BASELINE_MODEL_IDS]
    look = make_log_lookup(data)
    actuals = as_date_dict(read_actuals_cache(data).primary)

    for lead in LEAD_TIMES_DAYS:
        days = []

        for issued in list_log_dates(data):
            actual = actuals.get(issued + timedelta(days=lead))
            if actual is None:
                continue

            stored = list(scored_predictions(look(issued)).for_lead(lead))
            by_model = {p.model: p for p in stored}
            llm = by_model.get(BLEND_MODEL_ID)
            if llm is None or llm.rain is None:
                continue

            # Recomputed rather than read back, so a day stored before the
            # backfill scores exactly like one stored after it.
            blend = code_blend_predictions(
                scored_predictions(look(issued)), issued, look, actuals, inputs
            ).for_lead(lead)
            long = windows_as_of(inputs, lead, ROLLING_WINDOW_LONG, issued, look, actuals)

            rows = dict(by_model)
            rows[CODE_BLEND_MODEL_ID] = blend[0] if blend else None
            rows[LEADER_ID] = _leader(by_model, long)
            # Forecast side only, so the split cannot lean on the outcome.
            # Day+0 alone carries CAPE, stored from 2026-09-11; best_match is
            # out, as in the pipeline. "n/a" is no CAPE, "-" is no model over.
            capes = [p.peak_cape_jkg for p in stored if p.model in inputs]
            tier = (convective_tier(capes) or "-") if any(c is not None for c in capes) else "n/a"

            scores = {
                m: s
                for m, p in rows.items()
                if p is not None and (s := score_prediction(p, actual, lead)) is not None
            }
            days.append((issued, actual.observed_convection(), tier, rows, scores))

        _report(lead, days, shown)

    return 0


def _report(lead: int, days: list, shown: list[str]) -> None:
    print(f"\n=== Day+{lead} — the {len(days)} days olw_blend was scored")
    print("  model            rain         brier (n)     |high| (n)    |low| (n)")

    llm_prob = [d for d in days if d[3][BLEND_MODEL_ID].rain_probability_pct is not None]
    llm_high = [d for d in days if d[3][BLEND_MODEL_ID].high_c is not None]
    llm_low = [d for d in days if d[3][BLEND_MODEL_ID].low_c is not None]

    for model in shown:
        scored = [d[4][model] for d in days if model in d[4]]
        if not scored:
            continue

        hits = sum(s.rain_correct for s in scored)
        brier = [d[4][model].rain_brier for d in llm_prob if model in d[4]]
        high = [abs(e) for d in llm_high if model in d[4] and (e := d[4][model].high_error_c) is not None]
        low = [abs(e) for d in llm_low if model in d[4] and (e := d[4][model].low_error_c) is not None]
        print(
            f"  {model:15s}  {hits:2d}/{len(scored):<2d} {100 * hits / len(scored):3.0f}%"
            f"   {_mean(brier):>6} ({sum(b is not None for b in brier):2d})"
            f"   {_mean(high):>6} ({len(high):2d})   {_mean(low):>6} ({len(low):2d})"
        )

    both = [d for d in days if CODE_BLEND_MODEL_ID in d[4]]
    declined = len(days) - len(both)
    split = [d for d in both if d[3][BLEND_MODEL_ID].rain != d[3][CODE_BLEND_MODEL_ID].rain]
    llm_right = sum(d[4][BLEND_MODEL_ID].rain_correct for d in split)
    code_right = len(split) - llm_right
    print(
        f"\n  olw_blend vs olw_code_blend: {len(split)} disagreements in {len(both)} days"
        f" — LLM right {llm_right}, code right {code_right}"
        f" (sign test p={_sign_test_p(llm_right, code_right):.2f})"
        + (f"; code declined {declined}" if declined else "")
    )

    paired = [
        (d[4][BLEND_MODEL_ID].rain_brier, d[4][CODE_BLEND_MODEL_ID].rain_brier)
        for d in both
        if d[4][BLEND_MODEL_ID].rain_brier is not None
    ]
    if paired:
        better = sum(1 for a, b in paired if a < b)
        worse = sum(1 for a, b in paired if a > b)
        diff = sum(a - b for a, b in paired) / len(paired)
        print(
            f"  Brier, {len(paired)} paired days: LLM minus code {diff:+.3f} (lower is better);"
            f" LLM lower on {better}, code lower on {worse}"
            f" (sign test p={_sign_test_p(better, worse):.2f})"
        )

    if not split:
        return

    print("\n  issued      target      LLM     code    observed  thunder   right")
    for issued, observed, tier, rows, scores in split:
        target = issued + timedelta(days=lead)
        who = "LLM" if scores[BLEND_MODEL_ID].rain_correct else "code"
        print(
            f"  {issued}  {target}  {_call(rows[BLEND_MODEL_ID])}  {_call(rows[CODE_BLEND_MODEL_ID])}"
            f"  {'wet' if observed else 'dry':8s}  {tier:8s}  {who}"
        )


def main(argv: list[str]) -> int:
    if argv == ["backtest"]:
        return _backtest()

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
