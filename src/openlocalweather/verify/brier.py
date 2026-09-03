"""Proper scoring for probabilistic rain calls — ROADMAP item 58.

WHY A BOOLEAN IS NOT ENOUGH. `rain` is true or false, so a model that said
"60% chance" and one that said "certainly" score identically whichever way
the day goes. The ledger therefore cannot tell a confidently wrong forecast
from an honestly uncertain one, which is the distinction item 53's whole
incident turns on — and it means system-prompt rule 7 has to ask for
restraint in English that the scoring actively fails to pay for.

A proper scoring rule pays for it arithmetically. Under Brier, claiming
certainty you do not have is the most expensive thing you can do.

LOWER IS BETTER, unlike every other figure this project publishes. Each of
those is a percentage where higher is better; this is a squared error where
zero is perfect. That inversion is the single most likely thing to be
rendered backwards, so anything that displays it should say so in words.

RAW BRIER IS NOT INTERPRETABLE ON ITS OWN, which is item 57's lesson arriving
in a second place. 0.2 is good or bad entirely depending on the base rate, so
a Brier with no reference beside it is not a claim — exactly as "ECMWF 75%"
was not. `brier_skill_score` is the interpretable form, and the reference is
climatology, which item 57 already put in the ledger.
"""

from __future__ import annotations


def brier_score(probability: float, occurred: bool) -> float:
    """The squared error of one probabilistic call: `(p - outcome)**2`.

    `probability` is a PROBABILITY, in [0, 1] — not a percentage. A 70 passed
    here would score 4761, which is a number that would pass silently through
    every mean it touched and poison the whole column, so it raises instead.
    The record stores percentages (`rain_probability_pct`), and converting is
    the caller's job precisely so the conversion happens once and visibly.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(
            f"brier_score expects a probability in [0, 1], got {probability!r} — "
            "if this came from rain_probability_pct, divide by 100 first"
        )

    return (probability - (1.0 if occurred else 0.0)) ** 2


def mean_brier(scores: list[float | None]) -> float | None:
    """The mean over the days that HAVE a score, or None when none do.

    Days with no probability are skipped rather than counted. Most stored days
    have none — the field started being recorded on 2026-09-03 — and treating
    an absent probability as an implicit 0.5 would manufacture a hedge nobody
    made, which is the same absence-is-not-evidence rule everything else here
    follows.
    """
    present = [s for s in scores if s is not None]
    if not present:
        return None

    return sum(present) / len(present)


def brier_skill_score(brier: float | None, reference_brier: float | None) -> float | None:
    """How much better than the reference forecast: `1 - brier/reference`.

    1.0 is perfect, 0.0 is exactly as good as the reference, and NEGATIVE
    means worse than it. Deliberately not clamped at zero: item 57 measured
    two of five models losing to persistence on the boolean, and a negative
    skill score states that same result in a form that cannot be misread as
    merely "less good".

    None when either side is missing, and also when the reference is a perfect
    0.0 — a reference that is never wrong leaves nothing to improve on, and
    the division is undefined rather than infinite.
    """
    if brier is None or reference_brier is None or reference_brier == 0.0:
        return None

    return 1.0 - (brier / reference_brier)
