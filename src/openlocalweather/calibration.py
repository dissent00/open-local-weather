"""Correcting a forecast for bias the record has already measured.

WHY THIS EXISTS. This project's founding principle is that arithmetic an LLM
can get wrong belongs in code. The gust is the case where that principle was
stated and then not applied: the prompt TELLS the forecaster the models
under-forecast peak wind — `skill_profile_summary` for gfs says "a strong
tendency to under-forecast peak surface wind speeds", the per-model figures
sit beside it in MODEL TRACK RECORD, and prompt rule 218 spells out the sign
convention in terms — and the published gust still came in **12.09 km/h below
the observed gust, averaged over 32 days** (median 10.15). On 2026-09-13 the
page published 28.6 km/h against an observed 55.1.

Telling a model about a bias is not the same as removing it. The information
was in the payload and the correction did not happen, which is the same
finding `comparison.py` records about labels: a rule cannot win against a
payload that supplies its own counter-example. So code does the subtraction.

THE CORRECTION IS THE RECORD'S OWN NUMBER. `avg_wind_error_kmh_10` is
`actual - predicted` over the last ROLLING_WINDOW_SHORT checks, recomputed
every run by verify/scoring, already stored, and already shown to the
forecaster. Nothing new is measured here; this reads what the project has
been computing daily and applies it.

MEASURED OUT OF SAMPLE, which is the only way a correction like this earns
its place. For each day, the bias was taken from the ten days STRICTLY BEFORE
it and applied to that day's consensus:

    window   days   mean |error| raw -> corrected   days improved
    5        29        12.26 -> 4.48               27/29
    10       24        11.16 -> 3.80               23/24
    20       14        11.69 -> 3.23               14/14

PER MODEL RATHER THAN POOLED, by a hair on the numbers — 3.70 against 3.80
mean absolute error — and by a wide margin on the reasoning: the per-model
figure is already computed, already stored and already in the prompt, so code
and forecaster correct from ONE number rather than two that can drift. The
spread justifies it on its own terms: over the record gfs runs +19.99 and
best_match +4.40.

NO CAP ON THE CORRECTION, and that is a decision rather than an omission. A
cap is a threshold, and ROADMAP item 100 is the record of one sized from two
convenient samples that landed 45 characters from refusing legitimate output.
The rolling-10 bias has been strikingly stable per model — ecmwf +8.57..+13.35,
gfs +16.00..+23.91, icon +5.98..+12.50, ukmo +11.47..+16.86, best_match
+0.93..+9.41, over 15 to 24 windows — so there is nothing yet to size a cap
against. `min_checks` is the guard instead: a correction appears only once the
record has enough verified checks to have measured one.

WHAT THIS MUST NEVER TOUCH: the SCORED rows. `prediction_rows` stores what
each model actually said, and verify/scoring measures that against the
observation to produce the very number used here. Correcting the stored
prediction would close that loop on itself — the measured error would collapse
toward zero, the correction would vanish with it, and the bias would come back
uncorrected while the record claimed it had been fixed. So this module returns
a SEPARATE consensus and never a modified ModelPrediction.
"""

from __future__ import annotations

from openlocalweather.defaults import GUST_CALIBRATION_MIN_CHECKS
from openlocalweather.models import ModelPrediction
from openlocalweather.verify.scoring import mean

# The lead time the correction is defined for. Day+0 only, because that is
# where it has been validated and because the bias is not assumed to be the
# same three days out — a longer lead has a different error structure and
# deserves its own measurement before it gets its own correction.
GUST_CALIBRATION_LEAD_DAYS = 0


def gust_corrections(
    entries,
    *,
    lead_days: int = GUST_CALIBRATION_LEAD_DAYS,
    min_checks: int = GUST_CALIBRATION_MIN_CHECKS,
) -> dict[str, float]:
    """Per-model km/h to ADD to a forecast gust, keyed by model id.

    ADD, not subtract: `avg_wind_error_kmh_10` is `actual - predicted`, so a
    positive value is a model that came in under what happened. Prompt rule
    218 states the same convention for the same field, and stored notes
    written before it existed had the direction backwards — 43 of them were
    mechanically corrected on 2026-09-10. Getting it wrong here would double
    the bias rather than remove it, so the sign is asserted by a test that
    would fail on a flipped one.

    A model with too few checks, or none recorded, is simply absent from the
    result. Absence is absence: a model nothing has measured gets no
    correction rather than a zero, and zero would be a claim that it is
    unbiased.
    """
    corrections: dict[str, float] = {}

    for entry in entries:
        if entry.lead_time_days != lead_days:
            continue

        error = getattr(entry, "avg_wind_error_kmh_10", None)
        if error is None:
            continue

        checks = getattr(entry, "checks_in_window_10", None)
        if checks is None or checks < min_checks:
            continue

        corrections[entry.model] = error

    return corrections


def calibrated_gust_consensus(
    predictions: list[ModelPrediction],
    corrections: dict[str, float] | None,
) -> float | None:
    """The consensus gust with each model's measured bias added back, or None
    when no model in the list has a correction.

    ONLY CORRECTED MODELS COUNT. A consensus mixing corrected and uncorrected
    members is neither: it would be pulled toward whichever models happen to
    lack a record, and it would move whenever one of them crossed the check
    threshold. Better a mean of two calibrated models than a mean of five on
    two different footings.

    None, NOT A FALLBACK TO THE RAW CONSENSUS, so the caller has to decide
    what an uncalibrated day means rather than being handed a number that
    silently is not the thing its name says.
    """
    if not corrections:
        return None

    adjusted = [
        p.wind_kmh + corrections[p.model]
        for p in predictions
        if p.wind_kmh is not None and p.model in corrections
    ]

    return mean(adjusted)
