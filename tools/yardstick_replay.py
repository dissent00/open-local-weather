"""What the persistence and climatology yardsticks did to a consensus —
ROADMAP items 183 and 126.

WHY THIS EXISTS. The yardsticks joined `day0_predictions` to be scored, and
from item 57 until item 183 every consumer after that line averaged them in
as if they were models. Item 183 measured what that changed by replaying the
archive both ways. Item 126 had validated the gust calibration out of sample
and kept only the table; its script was lost, and the table was the only way
to rebuild the method. Both are kept here so the next question does not
start from a table.

THREE REPLAYS, all read-only:

  comparison  The day-over-day comparison of each morning issuance, from
              its stored Day+0 row, against the actuals cache and the track
              record committed with that day's forecast. Reports which list
              each STORED comparison was built from, then what excluding the
              yardsticks changes: tile modifiers, labels, the rain vote.
  trend       The NEXT THREE DAYS phrase of every archived prompt, days 1-3
              re-extracted from the daily block the prompt carried, today
              from the matching stored row.
  gust        Item 126's validation — each member's bias from its last N
              errors strictly before the day — models alone against models
              with the yardsticks. Then the as-shipped path: the corrections
              each day's run actually read, through calibrated_gust_consensus.

THE REPLAY READS TODAY'S ACTUALS CACHE, so a reanalysis revised since a run is
invisible to it. The fidelity lines are the check: they compare the replay
with what the record stored.

Usage:
  python tools/yardstick_replay.py comparison
  python tools/yardstick_replay.py trend
  python tools/yardstick_replay.py gust [--through 2026-09-13] [--split 2026-09-14]
"""
import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.calibration import calibrated_gust_consensus, gust_corrections  # noqa: E402
from openlocalweather.comparison import (  # noqa: E402
    COMPARISON_SUBJECT_TODAY,
    _band_label,
    _consensus_onset,
    comparison_subject,
    day_rain_band,
    describe_extended_trend,
)
from openlocalweather.config import load_location_config  # noqa: E402
from openlocalweather.dates import add_days, weekday_name  # noqa: E402
from openlocalweather.defaults import (  # noqa: E402
    BASELINE_MODEL_IDS,
    BEST_MATCH_MODEL_ID,
    BLEND_MODEL_ID,
    CLOUD_CHANGE_BANDS_PCT,
    CODE_BLEND_MODEL_ID,
    MODELS,
    TEMP_CHANGE_BANDS_C,
    WIND_CHANGE_BANDS_KMH,
)
from openlocalweather.extract import extract_day_n_predictions_from_daily  # noqa: E402
from openlocalweather.instability import convective_tier  # noqa: E402
from openlocalweather.pipeline import DAY_AFTER_SPAN_LEAD, EXTENDED_SPAN_LEADS  # noqa: E402
from openlocalweather.store.actuals_cache import as_date_dict, read_actuals_cache  # noqa: E402
from openlocalweather.store.log_store import read_log_entry  # noqa: E402
from openlocalweather.tiles import comparison_modifiers, notable_moves  # noqa: E402
from openlocalweather.verify.scoring import mean, scored_predictions  # noqa: E402

DATA = ROOT / "data"

# Added at storage, after every consumer ran. Neither list includes them.
STORAGE_ONLY = {BLEND_MODEL_ID, CODE_BLEND_MODEL_ID}

# Item 126's windows, and the day its calibration reached the pipeline — the
# split that separates what item 126 saw from what it did not.
GUST_WINDOWS = (5, 10, 20)
GUST_CALIBRATION_SHIPPED = date(2026, 9, 14)

# Tile modifiers were first stored on this day (item 159 step 6).
TILES_FIRST_STORED = date(2026, 9, 22)

# The stored comparison fields a replay of the Day+0 list can reach.
COMPARISON_FIELDS = (
    "high_delta_c", "wind_delta_kmh", "cloud_delta_pct", "today_calibrated_peak_wind_kmh",
    "today_rain_expected", "high_label", "wind_label", "cloud_label",
)

TREND_PHRASE = re.compile(r"NEXT THREE DAYS \(pre-computed by code[^\n]*\n(.*)\n")


def models_only(model: str) -> bool:
    return model not in BASELINE_MODEL_IDS


def with_yardsticks(model: str) -> bool:
    return True


VARIANTS = (("with", with_yardsticks), ("without", models_only))


def _committed_corrections() -> dict[str, dict[str, float]]:
    """Per day, the gust corrections the day's first run read — the
    track_record.json committed with that day's forecast."""
    log = subprocess.run(
        ["git", "log", "--format=%h %s", "--", "data/track_record.json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout

    # Newest first, so the last assignment for a day is its first run.
    commits = {}
    for line in log.splitlines():
        sha, subject = line.split(" ", 1)
        if subject.startswith("forecast: "):
            commits[subject.removeprefix("forecast: ").strip()] = sha

    out = {}
    for day, sha in commits.items():
        raw = subprocess.run(
            ["git", "show", f"{sha}:data/track_record.json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        out[day] = gust_corrections([SimpleNamespace(**e) for e in json.loads(raw)["entries"]])

    return out


def _live(predictions) -> list:
    return [p for p in predictions if p.model not in STORAGE_ONLY]


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 1)


def _count(rows, key) -> int:
    return sum(1 for r in rows if key(r["with"]) != key(r["without"]))


# --- comparison ------------------------------------------------------------


def _comparison(preds, yesterday, corrections, notable) -> dict:
    """The parts of `compute_day_over_day` the Day+0 list reaches, and the
    tile modifiers built from them."""
    wind = mean([p.wind_kmh for p in preds])
    calibrated = calibrated_gust_consensus(preds, corrections) if corrections else None
    wind_for_label = wind if calibrated is None else calibrated

    high_delta = _delta(mean([p.high_c for p in preds]), yesterday.high_c)
    wind_delta = _delta(wind_for_label, yesterday.peak_wind_kmh)
    cloud_delta = _delta(mean([p.cloud_cover_pct for p in preds]), yesterday.cloud_cover_pct)

    votes = [p.rain for p in preds if p.rain is not None]
    rain = sum(votes) > len(votes) / 2 if votes else None

    return {
        "high_delta_c": high_delta,
        "wind_delta_kmh": wind_delta,
        "cloud_delta_pct": cloud_delta,
        "today_calibrated_peak_wind_kmh": round(calibrated, 1) if calibrated is not None else None,
        "today_rain_expected": rain,
        "high_label": _band_label(high_delta, TEMP_CHANGE_BANDS_C, "warmer", "cooler"),
        "wind_label": _band_label(wind_delta, WIND_CHANGE_BANDS_KMH, "windier", "calmer"),
        "cloud_label": _band_label(cloud_delta, CLOUD_CHANGE_BANDS_PCT, "cloudier", "clearer"),
        "precip_band": day_rain_band(mean([p.precip_mm for p in preds])),
        "onset": _consensus_onset(preds) if rain else None,
        "tiles": comparison_modifiers(
            {"temp": high_delta, "wind": wind_delta, "cloud": cloud_delta}, notable
        ),
    }


def _stored_matches(stored, replayed: dict) -> bool:
    for field in COMPARISON_FIELDS:
        value = getattr(stored, field)
        # Cloud was not stored before 2026-09-23; absent is not a mismatch.
        if field == "cloud_delta_pct" and value is None:
            continue
        if value != replayed[field]:
            return False

    return True


def run_comparison() -> None:
    location = load_location_config(ROOT / "config/location.yaml")
    zone = ZoneInfo(location.timezone)
    cache = read_actuals_cache(DATA)
    actuals = as_date_dict(cache.primary)
    corrections = _committed_corrections()

    rows = []
    for path in sorted((DATA / "log").glob("*.json")):
        today = date.fromisoformat(path.stem)
        entry = read_log_entry(DATA, today)
        yesterday = actuals.get(add_days(today, -1))
        if yesterday is None:
            continue

        sunset_hour = int(entry.sunset.split(":")[0]) if entry.sunset else None
        history = [cache.primary[k] for k in sorted(cache.primary) if date.fromisoformat(str(k)) < today]
        notable = notable_moves(history)

        for i, row in enumerate(entry.prediction_rows or []):
            local = row.window_opened_local or row.issued_at.astimezone(zone)
            if comparison_subject(local.hour, sunset_hour=sunset_hour) != COMPARISON_SUBJECT_TODAY:
                continue

            live = _live(row.predictions.day0)
            if not any(p.model in BASELINE_MODEL_IDS for p in live):
                continue

            replayed = {
                name: _comparison([p for p in live if keep(p.model)], yesterday, corrections.get(path.stem), notable)
                for name, keep in VARIANTS
            }
            rows.append({
                "date": today, "row": i, "local": local.strftime("%H:%M"), **replayed,
                "stored": row.day_over_day,
                "stored_tiles": entry.comparison if i == 0 and today >= TILES_FIRST_STORED else None,
                "gated": bool(notable),
            })

    stored = [r for r in rows if r["stored"] is not None]
    built = {name: sum(1 for r in stored if _stored_matches(r["stored"], r[name])) for name, _ in VARIANTS}
    neither = sum(1 for r in stored if not any(_stored_matches(r["stored"], r[name]) for name, _ in VARIANTS))
    print(f"stored comparisons {len(stored)}: built with yardsticks {built['with']}, "
          f"without {built['without']}, neither {neither}")

    tiled = [r for r in rows if r["stored_tiles"] is not None]
    tiles = {name: sum(1 for r in tiled if r["stored_tiles"] == r[name]["tiles"]) for name, _ in VARIANTS}
    print(f"stored tile maps {len(tiled)}: match with {tiles['with']}, without {tiles['without']}")

    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], r)
    first = list(by_day.values())
    print(f"\nfirst morning issuance, {first[0]['date']}..{first[-1]['date']}: {len(first)} days, "
          f"{sum(r['gated'] for r in first)} with a tile gate. Changed without the yardsticks:")
    for label, key in (
        ("tile modifiers", lambda x: x["tiles"]),
        ("  temp", lambda x: x["tiles"].get("temp")),
        ("  wind", lambda x: x["tiles"].get("wind")),
        ("  cloud", lambda x: x["tiles"].get("cloud")),
        ("high label", lambda x: x["high_label"]),
        ("wind label", lambda x: x["wind_label"]),
        ("cloud label", lambda x: x["cloud_label"]),
        ("rain vote", lambda x: x["today_rain_expected"]),
        ("rain band", lambda x: x["precip_band"]),
        ("rain onset", lambda x: x["onset"]),
    ):
        print(f"  {label:15s} {_count(first, key)}")

    for r in first:
        if r["with"]["tiles"] != r["without"]["tiles"]:
            print(f"    {r['date']} {r['local']}  with {r['with']['tiles']}  without {r['without']['tiles']}")

    for field in ("high_delta_c", "wind_delta_kmh", "cloud_delta_pct"):
        pairs = [(r["with"][field], r["without"][field]) for r in first]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        print(f"  {field:16s} mean with {statistics.mean(a for a, _ in pairs):+.2f} without "
              f"{statistics.mean(b for _, b in pairs):+.2f}; mean |delta| with "
              f"{statistics.mean(abs(a) for a, _ in pairs):.2f} without {statistics.mean(abs(b) for _, b in pairs):.2f}")


# --- trend -----------------------------------------------------------------


def _trend(today_list, extended, daily, today) -> str | None:
    """`_locked_blocks`' call, with today's side supplied."""
    return describe_extended_trend(
        today_high_c=mean([p.high_c for p in today_list]),
        day_highs_c=[mean([p.high_c for p in day]) for day in extended],
        day_precip_mm=[mean([p.precip_mm for p in day]) for day in extended],
        day_names=[weekday_name(add_days(today, n)) for n in EXTENDED_SPAN_LEADS],
        day_thunder=[
            convective_tier([p.peak_cape_jkg for p in day if p.model != BEST_MATCH_MODEL_ID])
            for day in extended
        ],
        day_after_precip_mm=mean([
            p.precip_mm for p in extract_day_n_predictions_from_daily(daily, DAY_AFTER_SPAN_LEAD, MODELS)
        ]),
        today_wind_kmh=mean([p.wind_kmh for p in today_list]),
        day_winds_kmh=[mean([p.wind_kmh for p in day]) for day in extended],
    )


def _head(phrase: str | None) -> str | None:
    """The trend words, which are all today's side can move. The tail names
    days 1-3 alone, and its wording has changed since the early prompts."""
    if phrase is None:
        return None

    head = phrase.split(", with")[0]
    for scope in ("temperatures and winds ", "temperatures ", "conditions "):
        if head.startswith(scope):
            return head[len(scope):]

    return head


def run_trend() -> None:
    rows = []
    for path in sorted((DATA / "prompts").glob("*.json")):
        today = date.fromisoformat(path.stem)
        stored_rows = read_log_entry(DATA, today).prediction_rows or []

        for issuance in json.loads(path.read_text())["issuances"]:
            prompt = issuance["user_prompt"]
            at = issuance["issued_at"].replace("Z", "+00:00")

            # Every issuance has its own row since 2026-09-16; before that a
            # later issuance reused row 0, which is what its prompt was built
            # from.
            match = [
                r for r in stored_rows
                if abs((r.issued_at - datetime.fromisoformat(at)).total_seconds()) < 2
            ]
            row = match[0] if match else stored_rows[0]

            key = prompt.find('"primary_extended_daily"')
            daily = json.JSONDecoder().raw_decode(prompt, prompt.find("{", key))[0]
            found = TREND_PHRASE.search(prompt)
            archived = found.group(1) if found else None

            live = _live(row.predictions.day0)
            extended = [extract_day_n_predictions_from_daily(daily, n, MODELS) for n in EXTENDED_SPAN_LEADS]
            today_lists = {name: [p for p in live if keep(p.model)] for name, keep in VARIANTS}

            rows.append({
                "date": today, "at": at[11:16], "archived": archived,
                **{name: _trend(lst, extended, daily, today) for name, lst in today_lists.items()},
                "wind": {name: mean([p.wind_kmh for p in lst]) for name, lst in today_lists.items()},
                "day3_wind": mean([p.wind_kmh for p in extended[-1]]),
            })

    archived = [r for r in rows if r["archived"] and not r["archived"].startswith("Unavailable")]
    built = {name: sum(1 for r in archived if _head(r["archived"]) == _head(r[name])) for name, _ in VARIANTS}
    print(f"archived phrases {len(archived)}: trend words reproduced with yardsticks {built['with']}, "
          f"without {built['without']}")

    changed = [r for r in rows if r["with"] != r["without"]]
    print(f"prompts {len(rows)}: phrase changes without the yardsticks {len(changed)}")
    for r in changed:
        print(f"  {r['date']} {r['at']}Z  with: {r['with']}\n  {'':17s}without: {r['without']}")

    lean = {name: [r["day3_wind"] - r["wind"][name] for r in rows] for name, _ in VARIANTS}
    for name, _ in VARIANTS:
        print(f"  day+3 minus today wind, {name:7s}: mean {statistics.mean(lean[name]):+.2f} km/h, "
              f"negative on {sum(x < 0 for x in lean[name])}/{len(rows)}")
    print(f"  today's wind, without minus with: mean "
          f"{statistics.mean(r['wind']['without'] - r['wind']['with'] for r in rows):+.2f} km/h")


# --- gust ------------------------------------------------------------------


def _gust_days(through: date) -> list[tuple[date, dict[str, float], float | None]]:
    actuals = as_date_dict(read_actuals_cache(DATA).primary)
    days = []
    for path in sorted((DATA / "log").glob("*.json")):
        day = date.fromisoformat(path.stem)
        if day > through:
            continue

        predictions = {
            p.model: p.wind_kmh
            for p in _live(scored_predictions(read_log_entry(DATA, day)).day0)
            if p.wind_kmh is not None
        }
        observed = actuals.get(day).peak_wind_kmh if actuals.get(day) else None
        days.append((day, predictions, observed))

    return days


def _method_rows(days, keep, window: int) -> list[tuple[date, float, float, float]]:
    """Item 126's method: (day, raw, corrected, observed) for each day on
    which at least one member has a full window of errors before it."""
    errors: dict[str, list[tuple[date, float]]] = {}
    for day, predictions, observed in days:
        if observed is None:
            continue
        for model, wind in predictions.items():
            errors.setdefault(model, []).append((day, observed - wind))

    rows = []
    for day, predictions, observed in days:
        if observed is None:
            continue

        members = {m: w for m, w in predictions.items() if keep(m)}
        corrected = []
        for model, wind in members.items():
            prior = [e for d, e in errors.get(model, []) if d < day]
            if len(prior) < window:
                continue
            corrected.append(wind + mean(prior[-window:]))

        if not corrected:
            continue
        rows.append((day, mean(list(members.values())), mean(corrected), observed))

    return rows


def _paired(a, b) -> str:
    """Models-only against with-yardsticks, day by day: mean absolute error
    each way and the mean of the per-day difference with one standard error."""
    ours = {d: abs(c - o) for d, _, c, o in a}
    theirs = {d: abs(c - o) for d, _, c, o in b if d in ours}
    diffs = [ours[d] - theirs[d] for d in theirs]
    if len(diffs) < 2:
        return f"n={len(diffs)}"

    se = statistics.stdev(diffs) / math.sqrt(len(diffs))
    return (
        f"n={len(diffs):2d}  MAE models only {statistics.mean(ours[d] for d in theirs):.2f}"
        f" / with yardsticks {statistics.mean(theirs.values()):.2f}"
        f"  diff {statistics.mean(diffs):+.2f} ± {se:.2f}"
        f"  models only closer {sum(x < 0 for x in diffs)}, yardsticks {sum(x > 0 for x in diffs)}"
    )


def _periods(split: date):
    return (
        (f"before {split}", lambda d: d < split),
        (f"from {split}  ", lambda d: d >= split),
        ("all              ", lambda d: True),
    )


def run_gust(through: date, split: date) -> None:
    days = _gust_days(through)
    print(f"record {days[0][0]}..{days[-1][0]}")

    for window in GUST_WINDOWS:
        a, b = _method_rows(days, models_only, window), _method_rows(days, with_yardsticks, window)
        print(f"\nwindow {window}")
        for name, rows in (("models only    ", a), ("with yardsticks", b)):
            raw = [abs(r - o) for _, r, _, o in rows]
            cor = [abs(c - o) for _, _, c, o in rows]
            print(f"  {name} {len(rows):2d} days  MAE raw {statistics.mean(raw):5.2f} -> corrected "
                  f"{statistics.mean(cor):5.2f}  improved {sum(1 for x, y in zip(raw, cor) if y < x)}/{len(rows)}")
        for label, keep in _periods(split):
            print(f"  {label} {_paired([r for r in a if keep(r[0])], [r for r in b if keep(r[0])])}")

    # As shipped: the corrections each day's run read, through the function
    # the pipeline calls. A yardstick with no correction yet drops out of the
    # mean, which is why the early days tie.
    corrections = _committed_corrections()
    shipped = []
    for day, predictions, observed in days:
        corr = corrections.get(day.isoformat())
        if observed is None or corr is None:
            continue

        values = []
        for keep in (models_only, with_yardsticks):
            members = [SimpleNamespace(model=m, wind_kmh=w) for m, w in predictions.items() if keep(m)]
            values.append(calibrated_gust_consensus(members, corr))
        if None in values:
            continue
        shipped.append((day, values[0], values[1], observed))

    print(f"\nas shipped, {shipped[0][0]}..{shipped[-1][0]}")
    a = [(d, None, m, o) for d, m, _, o in shipped]
    b = [(d, None, y, o) for d, _, y, o in shipped]
    for label, keep in _periods(split):
        sel = [r for r in shipped if keep(r[0])]
        if not sel:
            continue
        print(f"  {label} {_paired([r for r in a if keep(r[0])], [r for r in b if keep(r[0])])}"
              f"  signed {statistics.mean(m - o for _, m, _, o in sel):+.2f} / {statistics.mean(y - o for _, _, y, o in sel):+.2f}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("comparison")
    sub.add_parser("trend")
    gust = sub.add_parser("gust")
    gust.add_argument("--through", type=date.fromisoformat, default=date.max,
                      help="last day of the record to use; 2026-09-13 reproduces item 126")
    gust.add_argument("--split", type=date.fromisoformat, default=GUST_CALIBRATION_SHIPPED)
    args = parser.parse_args(argv)

    if args.command == "comparison":
        run_comparison()
    elif args.command == "trend":
        run_trend()
    else:
        run_gust(args.through, args.split)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
