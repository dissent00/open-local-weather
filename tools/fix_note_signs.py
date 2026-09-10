#!/usr/bin/env python3
"""Correct the error-sign direction words in stored verification notes.

ROADMAP item 92, the half it could not fix. The convention is OBSERVED MINUS
FORECAST, so a POSITIVE error means the model came in UNDER what happened.
The prompt now states that; every note written before it did not know, and an
unknown subset of them describes the direction backwards.

Found by a cold reading on 2026-09-10, which noticed the long-run review
saying GFS "under-forecasts peak wind... +20.6 km/h" while the stored notes
for the same model said "overpredicted surface wind speeds by 38.9 km/h".

NOTHING IS REWRITTEN ON THE STRENGTH OF ITS WORDING. Every correction is
checked against the stored prediction and the stored observation for that
exact (date, model, lead, field): the magnitude in the prose must match the
recomputed error to within a rounding step, and the direction word must
contradict its sign. A claim that cannot be attributed to one model and one
field is left alone and reported, because a note edited on a guess is worse
than a note known to be suspect.

THE MAGNITUDE IS NEVER TOUCHED. It was right all along — only the word for
its direction was wrong, and that is the whole of the edit.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODEL_NAMES = {
    "gfs_seamless": ["GFS"],
    "ukmo_seamless": ["UKMO", "UK Met Office"],
    "icon_seamless": ["ICON"],
    "ecmwf_ifs025": ["ECMWF"],
    "best_match": ["Best Match"],
    "kenya_met": ["Kenya Met", "KMD"],
}

# (prose unit, prediction field, observation field)
UNITS = (("km/h", "wind_kmh", "peak_wind_kmh"),)

_OVER = re.compile(r"\bover[- ]?(forecast|forecasted|predicted|estimated|estimating|estimation|prediction)\b", re.I)
_UNDER = re.compile(r"\bunder[- ]?(forecast|forecasted|predicted|estimated|estimating|estimation|prediction)\b", re.I)
# "cold bias" on a temperature is the same claim in different words: it says
# the model ran COLDER than reality, which is a POSITIVE error under this
# convention. A negative error is a model that ran warm.
_COLD_BIAS = re.compile(r"\bcold bias\b", re.I)
_WARM_BIAS = re.compile(r"\bwarm bias\b", re.I)

# What each wrong word becomes. Chosen to match the phrasing the prompt now
# uses, so a future reader sees one vocabulary rather than two.
_FLIP = [
    (re.compile(r"\bover-forecasted\b", re.I), "under-forecast"),
    (re.compile(r"\bover-forecast\b", re.I), "under-forecast"),
    (re.compile(r"\bover[- ]?predicted\b", re.I), "under-forecast"),
    (re.compile(r"\boverestimated\b", re.I), "under-forecast"),
    (re.compile(r"\boverestimation\b", re.I), "under-forecasting"),
    (re.compile(r"\boverestimating\b", re.I), "under-forecasting"),
    (re.compile(r"\bover[- ]?prediction\b", re.I), "under-forecasting"),
    (re.compile(r"\boverpredicting\b", re.I), "under-forecasting"),
    (re.compile(r"\bunder-forecasted\b", re.I), "over-forecast"),
    (re.compile(r"\bunder-forecast\b", re.I), "over-forecast"),
    (re.compile(r"\bunder[- ]?predicted\b", re.I), "over-forecast"),
    (re.compile(r"\bunderestimated\b", re.I), "over-forecast"),
    (re.compile(r"\bunderestimating\b", re.I), "over-forecasting"),
    (re.compile(r"\bunderestimation\b", re.I), "over-forecasting"),
    (re.compile(r"\bunder[- ]?prediction\b", re.I), "over-forecasting"),
    (re.compile(r"\bunderpredicting\b", re.I), "over-forecasting"),
    (re.compile(r"\bcold bias\b", re.I), "warm bias"),
    (re.compile(r"\bwarm bias\b", re.I), "cold bias"),
]


def clauses(note: str):
    """Sentences, then the connectives that separate independent claims.

    A direction word governs its own clause and no other. An earlier version
    took a fixed character window and attributed "over-forecasted" from a wind
    clause to a temperature figure beside it — three of the first four hits
    were that mistake.
    """
    for sentence in re.split(r"(?<=[.!?])\s+", note):
        for clause in re.split(r",\s+(?:while|whereas|though|although|but|and)\s+|;\s*", sentence):
            # A bare "and" joins two independent claims only when the SECOND
            # half carries its own direction word. "a cold bias (-2.6C) and
            # wind overestimation (+17.2 km/h)" is two claims and splits;
            # "overestimated winds by 25.9 km/h and high temperatures by
            # -4.5C" is one verb spanning both and must NOT, because that
            # verb was right about one quantity and wrong about the other.
            # Splitting it flipped the wind and broke the temperature, which
            # is the trade this whole tool exists to avoid.
            halves = re.split(r"\s+and\s+", clause)
            second_has_verb = len(halves) == 2 and any(
                rx.search(halves[1]) for rx in (_OVER, _UNDER, _COLD_BIAS, _WARM_BIAS)
            )
            if second_has_verb and all(re.search(r"\d\s*(km/h|°C)", h) for h in halves):
                # ATTRIBUTION AND REPLACEMENT ARE DIFFERENT STRINGS. The second
                # half carries the verb but not the noun governing it — "GFS
                # showed a cold bias (-2.6C) and wind overestimation (+17.2
                # km/h)" — so it inherits the subject for ATTRIBUTION only.
                # An earlier version prepended the model name to the clause
                # itself, and the augmented text then matched nothing in the
                # note, so the wind half was found and silently not replaced.
                subject = models_in(halves[0])
                for i, half in enumerate(halves):
                    yield half, (subject if i and not models_in(half) else models_in(half))
            else:
                yield clause, models_in(clause)


def models_in(clause: str) -> list[str]:
    return [m for m, names in MODEL_NAMES.items() if any(n.lower() in clause.lower() for n in names)]


def find_corrections(entry: dict, actual: dict, lead: int, note: str) -> list[dict]:
    """Every reversed direction word in one note, verified against the record.

    `actual` must be the observation for the row's date PLUS the lead. A note
    at lead k sits on the row that MADE the prediction — see
    prediction_row_date_for_target — so a Day+3 note on 2026-08-28 describes
    that row's Day+3 forecast against what happened on 2026-08-31. Checked
    against the record rather than assumed: the note claiming ECMWF -1.8 C
    matches that pairing exactly and matches neither of the other two.
    """
    preds = {p["model"]: p for p in entry.get("model_predictions", {}).get(f"day{lead}", [])}
    found: list[dict] = []
    ambiguous: list[str] = []
    for clause, named in clauses(note):
        over, under = bool(_OVER.search(clause)), bool(_UNDER.search(clause))
        cold, warm = bool(_COLD_BIAS.search(clause)), bool(_WARM_BIAS.search(clause))
        # Exactly one directional claim per clause, or the attribution is a
        # guess. Both words present, or neither, means skip.
        claims = sum((over, under, cold, warm))
        if claims != 1:
            continue

        # ONE VERB, TWO QUANTITIES — refuse. "GFS overestimated winds by 25.9
        # km/h and high temperatures by -4.5C" is a single direction word
        # governing both, and on that day it was WRONG about the wind and
        # RIGHT about the temperature. Flipping the verb fixes one and breaks
        # the other.
        #
        # POSITION DECIDES IT. A figure that appears BEFORE the verb cannot be
        # governed by it: "high temperature within -1.2C, compared to GFS
        # which overpredicted wind speed by 34.5 km/h" is one wind claim with
        # a temperature figure sitting in front of it, and refusing that was
        # the guard being blunt rather than careful.
        verb = (_OVER.search(clause) or _UNDER.search(clause)
                or _COLD_BIAS.search(clause) or _WARM_BIAS.search(clause))
        after = clause[verb.end():] if verb else clause
        if re.search(r"\d\s*km/h", after) and re.search(r"\d\s*°C", after):
            ambiguous.append(" ".join(clause.split()))
            continue

        if not named:
            continue

        if over or under:
            fields = [(u, pf, af) for u, pf, af in UNITS if re.search(re.escape(u), clause)]
        else:
            # WHICH TEMPERATURE, said explicitly by the clause or not at all.
            # Trying both and taking whichever magnitude happened to match is
            # how this tool's first run "confirmed" a claim against the wrong
            # field — the clause said daytime high and the number matched the
            # overnight low to two decimal places.
            if re.search(r"\b(high|maximum|max|daytime|daily)\b", clause, re.I):
                fields = [("°C", "high_c", "high_c")]
            elif re.search(r"\b(low|minimum|min|overnight|nocturnal)\b", clause, re.I):
                fields = [("°C", "low_c", "low_c")]
            else:
                fields = []
        for unit, pf, af in fields:
            nums = [abs(float(x)) for x in re.findall(rf"([+-]?\d+\.?\d*)\s*{re.escape(unit)}", clause)]
            # ONE VERB CAN GOVERN SEVERAL MODELS — "GFS and UKMO substantially
            # overpredicted surface wind speeds (by 27.4 km/h and 16.6 km/h
            # respectively)" is one claim about two models, and it is the shape
            # of the very note that started this. Flipping its verb is only
            # safe if EVERY model it names is wrong in the same direction, so
            # each is checked and one dissenter abandons the whole clause.
            # ONE FIGURE, SEVERAL NAMES — attribute to the model nearest
            # BEFORE the verb. "GFS and ECMWF missed the rain event, with GFS
            # significantly overestimating surface winds by 26.7 km/h" names
            # two models and makes one claim, about the one standing next to
            # the verb. Requiring exactly one name left these untouched.
            pairs = None
            if len(nums) == len(named):
                pairs = list(zip(named, nums))
            elif len(nums) == 1 and verb is not None:
                before = clause[: verb.start()].lower()
                nearest = max(
                    ((m, before.rfind(n.lower())) for m in named for n in MODEL_NAMES[m]),
                    key=lambda t: t[1], default=(None, -1),
                )
                if nearest[1] >= 0:
                    pairs = [(nearest[0], nums[0])]
            if pairs is None:
                continue

            matched = []
            for model, mag in pairs:
                pred = preds.get(model)
                if pred is None or pred.get(pf) is None or actual.get(af) is None:
                    break
                err = round(actual[af] - pred[pf], 2)
                if abs(abs(err) - mag) > 0.15:
                    break
                says_under = under or cold
                if says_under == (err > 0):
                    break                                  # this one is right
                matched.append({
                    "lead": lead, "model": model, "field": pf,
                    "error": err, "clause": " ".join(clause.split()),
                })
            else:
                found.extend(matched)
    return found, ambiguous


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the corrections; otherwise dry-run")
    args = ap.parse_args()

    actuals = json.loads((ROOT / "data/actuals_cache/actuals.json").read_text())["primary"]
    touched = 0
    skipped: list[tuple[str, int, str]] = []
    unflippable: list[tuple[str, int, str]] = []
    for path in sorted((ROOT / "data/log").glob("*.json")):
        entry = json.loads(path.read_text())
        changed = False
        for lead in (0, 3, 7):
            block = (entry.get("verification") or {}).get(f"day{lead}")
            if not block or not block.get("note"):
                continue
            # The row made the prediction; the observation is `lead` days later.
            target = date.fromisoformat(path.stem) + timedelta(days=lead)
            actual = actuals.get(str(target))
            if actual is None:
                continue
            corrections, unclear = find_corrections(entry, actual, lead, block["note"])
            skipped.extend((path.stem, lead, c) for c in unclear)
            if not corrections:
                continue
            new = block["note"]
            for c in corrections:
                for pattern, replacement in _FLIP:
                    if pattern.search(c["clause"]):
                        fixed = pattern.sub(replacement, c["clause"])
                        new = new.replace(c["clause"], fixed)
                        break
                else:
                    unflippable.append((path.stem, lead, c["clause"]))
            if new == block["note"]:
                continue
            print(f"{path.stem} day{lead}")
            for c in corrections:
                print(f"    {c['model']} {c['field']} error {c['error']:+.1f} — note said the opposite")
            print(f"  - {block['note']}")
            print(f"  + {new}\n")
            if args.apply:
                block["note"] = new
                block["note_sign_corrected_on"] = str(date.today())
            changed = True
            touched += 1
        if changed and args.apply:
            path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")

    if unflippable:
        print(f"\n{len(unflippable)} clause(s) VERIFIED BACKWARDS but with no known "
              f"wording to flip — these need a word added to _FLIP:")
        for stem, lead, c in unflippable:
            print(f"  {stem} day{lead}: {c[:120]}")
    if skipped:
        print(f"\n{len(skipped)} clause(s) left alone — one direction word governing two "
              f"quantities cannot be flipped for both:")
        for stem, lead, c in skipped:
            print(f"  {stem} day{lead}: {c[:120]}")
    print(f"\n{touched} note(s) {'corrected' if args.apply else 'would be corrected'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
