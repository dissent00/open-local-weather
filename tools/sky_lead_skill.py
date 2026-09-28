"""How often the SKY BY DAY word names the reanalysis's sky, by lead — item 187.

The forecast side is `tiles.sky_by_day`, the shipped function, run on each
archived issuance's own `primary_extended_daily`; daily `cloud_cover_mean`
exists there from 2026-09-18. Lead 0 is the same daily mean at today's
index, as a like-for-like baseline. The observed side is actuals.json's
`cloud_cover_pct`, the reanalysis daily mean that scoring uses, through
`sky_word`. A per-model breakdown follows, first issuances only.

Usage: python tools/sky_lead_skill.py
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from openlocalweather.defaults import MODELS  # noqa: E402
from openlocalweather.extract import extract_day_n_predictions_from_daily  # noqa: E402
from openlocalweather.tiles import SKY_COVER_BANDS_PCT, OVERCAST_LABEL, sky_by_day, sky_word  # noqa: E402

FIRST_DAY_WITH_DAILY_CLOUD = date(2026, 9, 18)
LEADS = range(0, 8)
WORDS = [word for _, word in SKY_COVER_BANDS_PCT] + [OVERCAST_LABEL]


def _daily_block(user_prompt: str) -> dict:
    lines = user_prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("TODAY'S MULTI-MODEL GUIDANCE"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    extended = json.loads("\n".join(lines[start + 1:end + 1])).get("primary_extended_daily") or {}
    return extended if "daily" in extended else {"daily": extended}


def _samples(observed: dict) -> list[dict]:
    out = []
    for path in sorted((ROOT / "data/prompts").glob("*.json")):
        issued = date.fromisoformat(path.stem)
        if issued < FIRST_DAY_WITH_DAILY_CLOUD:
            continue

        for index, issuance in enumerate(json.loads(path.read_text())["issuances"]):
            daily = _daily_block(issuance["user_prompt"])
            times = daily["daily"].get("time") or []
            shipped = {r["lead_time_days"]: r["sky"] for r in sky_by_day(daily, MODELS, today=issued)}

            for lead in LEADS:
                target = (issued + timedelta(days=lead)).isoformat()
                if target not in observed or target not in times:
                    continue

                predictions = extract_day_n_predictions_from_daily(daily, times.index(target), MODELS)
                by_model = {p.model: p.cloud_cover_pct for p in predictions if p.cloud_cover_pct is not None}
                if not by_model:
                    continue

                mean = sum(by_model.values()) / len(by_model)
                # The measurement is of the shipped function, not a re-derivation.
                assert lead == 0 or shipped[lead] == sky_word(mean), (issued, lead)
                out.append({
                    "issued": issued, "first": index == 0, "lead": lead, "target": target,
                    "mean": mean, "word": sky_word(mean), "by_model": by_model,
                    "observed": observed[target], "observed_word": sky_word(observed[target]),
                })
    return out


def _by_lead(label: str, samples: list[dict]) -> None:
    print(f"\n{label}")
    print("lead   n  word right  within one band  mean abs error  bias fc-obs (points)")
    for lead in LEADS:
        group = [s for s in samples if s["lead"] == lead]
        if not group:
            continue

        right = sum(s["word"] == s["observed_word"] for s in group)
        near = sum(abs(WORDS.index(s["word"]) - WORDS.index(s["observed_word"])) <= 1 for s in group)
        mae = sum(abs(s["mean"] - s["observed"]) for s in group) / len(group)
        bias = sum(s["mean"] - s["observed"] for s in group) / len(group)
        print(f"{lead:>4} {len(group):>3}  {right:>4}/{len(group):<4}  {near:>6}/{len(group):<6}  {mae:>12.1f}  {bias:>+10.1f}")


def main() -> None:
    actuals = json.loads((ROOT / "data/actuals_cache/actuals.json").read_text())["primary"]
    observed = {d: v["cloud_cover_pct"] for d, v in actuals.items() if v.get("cloud_cover_pct") is not None}
    samples = _samples(observed)
    first = [s for s in samples if s["first"]]

    _by_lead("FIRST ISSUANCE OF EACH DAY", first)
    _by_lead("EVERY ISSUANCE", samples)

    targets = sorted({s["target"] for s in samples})
    words = [sky_word(observed[t]) for t in targets]
    common = max(set(words), key=words.count)
    print(f"\n{len(targets)} target days, {targets[0]} to {targets[-1]}: "
          + ", ".join(f"{w} {words.count(w)}" for w in WORDS if words.count(w)))
    print(f"baseline 'always {common}' (in-sample, so generous): {words.count(common)}/{len(targets)}")

    errors = defaultdict(list)
    for s in first:
        group = "0" if s["lead"] == 0 else ("1-3" if s["lead"] <= 3 else "4-7")
        for model, cover in s["by_model"].items():
            errors[(model, group)].append(cover - s["observed"])

    print("\nper-model bias, forecast minus reanalysis (points), first issuances, n in brackets")
    print(f"{'model':<16}{'lead 0':>14}{'leads 1-3':>14}{'leads 4-7':>14}")
    for model in MODELS:
        cells = []
        for group in ("0", "1-3", "4-7"):
            values = errors.get((model, group), [])
            cells.append(f"{sum(values) / len(values):+6.1f} ({len(values)})" if values else "-")
        print(f"{model:<16}" + "".join(f"{c:>14}" for c in cells))


if __name__ == "__main__":
    main()
