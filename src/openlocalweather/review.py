"""Weekly review: what the daily loop structurally cannot see.

The daily run looks at exactly two horizons — yesterday's individual scores,
and rolling 10/30-check aggregates. Neither can answer the question this
project actually exists to answer: *which of the big models is better HERE,
and where is this forecast systematically wrong?* That needs a look across
the whole record.

TWO RULES SHAPE EVERYTHING BELOW.

First, all arithmetic in code. The LLM narrates findings; it never computes
one. Same rule as everywhere else, and for the same reason.

Second — and this is what makes a review safe to feed back into the daily
prompt — **every finding carries the evidence and confidence that produced
it, and weak evidence produces no finding at all.** A review that said
"ECMWF is the strongest model here" off eight days would be precisely the
failure it exists to prevent: an unverified claim, stated confidently,
hardening into received wisdom that later runs treat as established. So
claims are gated on check counts, and comparative claims additionally on
the gap exceeding sampling noise.

Reviews are always regenerated from the raw record, never built on top of a
previous review, for the same reason the rolling stats are stateless: an
error that can propagate forward is an error that never gets corrected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from openlocalweather.baselines import CLIMATOLOGY_MODEL_ID
from openlocalweather.dates import add_days
from openlocalweather.defaults import (
    BASELINE_MODEL_IDS,
    LEAD_TIMES_DAYS,
    MODELS,
    REVIEW_COMPARISON_MIN_GAP_PCT,
    REVIEW_CONFIDENCE_BANDS,
    REVIEW_MIN_CHECKS_FOR_COMPARISON,
    REVIEW_TEMP_BIAS_THRESHOLD_C,
    REVIEW_WIND_BIAS_THRESHOLD_KMH,
)
from openlocalweather.models import DailyActual, VerificationScore
from openlocalweather.verify.brier import brier_skill_score, mean_brier
from openlocalweather.verify.scoring import LogLookup, collect_scores, mean


def confidence_for(checks: int) -> str:
    """How much weight a figure derived from `checks` checks can carry."""
    for threshold, label in REVIEW_CONFIDENCE_BANDS:
        if checks < threshold:
            return label
    return REVIEW_CONFIDENCE_BANDS[-1][1]


@dataclass
class SkillCell:
    """One (model, lead time) pair's skill across the whole record."""

    model: str
    lead_time_days: int
    checks: int
    correct: int
    rain_pct: float | None
    confidence: str
    mean_high_error_c: float | None
    mean_low_error_c: float | None
    mean_wind_error_kmh: float | None
    mean_onset_error_hrs: float | None
    # Pressure-trend error. Scored per-day and carried in the rolling track
    # record since the beginning, but not aggregated here until now — so the
    # one variable with a genuine physical lead on convection was the one
    # variable the long-run view couldn't see.
    mean_mslp_error_hpa: float | None
    earliest: date | None
    latest: date | None
    # ROADMAP item 58. LOWER IS BETTER, alone among the figures on this row —
    # every other one is a percentage or a signed error, and this is a squared
    # error where zero is perfect. Anything rendering it has to say so.
    mean_rain_brier: float | None = None
    # Counted separately from `checks` because the two genuinely differ and
    # will for weeks: probabilities began being recorded 2026-09-03, so a cell
    # can hold thirty scored days of which three carry one. Presenting `checks`
    # beside the Brier would claim evidence that does not exist.
    brier_checks: int = 0
    # 1 - brier/climatology_brier: 1.0 is perfect, 0.0 is exactly climatology,
    # NEGATIVE is worse than simply knowing the usual chance of rain here.
    #
    # None when this cell has no Brier, when climatology is not among the
    # models being reviewed (the prompt path reviews only the forecaster's
    # models, so no reference exists there), or when the reference is a
    # perfect 0.0. Raw Brier is not interpretable without it — 0.2 is good or
    # bad entirely depending on the base rate — which is item 57's lesson
    # arriving in a second place.
    rain_brier_skill: float | None = None
    # How many days the skill score above actually rests on: those where this
    # model AND climatology both stated a probability. Smaller than
    # `brier_checks`, which is itself smaller than `checks`. Three counts on
    # one row looks like over-reporting until they diverge, and on the real
    # record at 2026-09-06 they were 26, 5 and 2.
    brier_skill_checks: int = 0


@dataclass
class Finding:
    """A single reviewed observation.

    `evidence` and `confidence` are not decoration — they travel with the
    claim into the prompt and the published review, so a reader (and the
    LLM) can weigh it rather than take it on trust.
    """

    kind: str  # coverage | ranking | bias | gap
    claim: str
    evidence: str
    confidence: str
    checks: int


@dataclass
class WeeklyReview:
    period_start: date
    period_end: date
    days_with_predictions: int
    days_verified: int
    cells: list[SkillCell] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # Plain-language statement of how much the review as a whole can be
    # trusted. Always present, including — especially — when the answer is
    # "not much yet".
    data_sufficiency: str = ""


def _paired_skill(
    scored: list[tuple[date, VerificationScore]],
    reference_by_date: dict[date, float],
) -> dict[str, float | int | None]:
    """The Brier skill score over the days this model and the reference SHARE.

    Both means are taken over the same dates, so the ratio compares two
    forecasts of the same weather rather than two samples of different
    weather. That is the whole content of a skill score; computed over
    unpaired days it is a plausible-looking number that answers nothing.

    `brier_skill_checks` is how many days survived the intersection, and it is
    reported for the same reason `brier_checks` is: it is smaller than either
    input count, and a skill score resting on two shared days should not look
    like one resting on thirty.
    """
    paired = [
        (s.rain_brier, reference_by_date[d])
        for d, s in scored
        if s.rain_brier is not None and d in reference_by_date
    ]
    if not paired:
        return {"rain_brier_skill": None, "brier_skill_checks": 0}

    return {
        "rain_brier_skill": brier_skill_score(
            mean_brier([b for b, _ in paired]),
            mean_brier([r for _, r in paired]),
        ),
        "brier_skill_checks": len(paired),
    }


def build_weekly_review(
    log_lookup: LogLookup,
    actuals: dict[date, DailyActual],
    all_log_dates: list[date],
    today: date,
    models: list[str] = MODELS,
    lead_times_days: list[int] = LEAD_TIMES_DAYS,
) -> WeeklyReview:
    """Computes the full review deterministically. No LLM, no I/O."""
    yesterday = add_days(today, -1)
    earliest = min(all_log_dates) if all_log_dates else yesterday

    cells: list[SkillCell] = []
    for k in lead_times_days:
        # Scored for every model at this lead BEFORE any cell is built,
        # because the Brier skill score is the one figure on a row that is not
        # a property of that row: it needs climatology's Brier at the same
        # lead, and climatology is just another model in this loop.
        scored_by_model = {
            model: collect_scores(model, k, yesterday, earliest, log_lookup, actuals)
            for model in models
        }
        briers = {
            model: mean_brier([s.rain_brier for _, s in scored])
            for model, scored in scored_by_model.items()
        }
        # The reference's Brier PER DAY, not as one mean.
        #
        # A skill score is a ratio of two means, and it only means anything if
        # both are taken over the SAME days. On the real record at 2026-09-06
        # they were not: the NWP models had probabilities on 5 days at Day+0
        # and climatology on 2, because backfilled baselines predate the
        # field. Dividing one mean by the other compares a model's five days
        # against the reference's two, and if those two happened to be easy
        # days the model is flattered — or damned — by nothing it did.
        #
        # Empty when climatology is not being reviewed at all, which is the
        # prompt path.
        reference_by_date = {
            d: s.rain_brier
            for d, s in scored_by_model.get(CLIMATOLOGY_MODEL_ID, [])
            if s.rain_brier is not None
        }

        for model in models:
            scored = scored_by_model[model]
            checks = len(scored)
            correct = sum(1 for _, s in scored if s.rain_correct)
            cells.append(
                SkillCell(
                    model=model,
                    lead_time_days=k,
                    checks=checks,
                    correct=correct,
                    rain_pct=(100 * correct / checks) if checks else None,
                    confidence=confidence_for(checks),
                    mean_high_error_c=mean([s.high_error_c for _, s in scored]),
                    mean_low_error_c=mean([s.low_error_c for _, s in scored]),
                    mean_wind_error_kmh=mean([s.wind_error_kmh for _, s in scored]),
                    mean_onset_error_hrs=mean([s.onset_error_hrs for _, s in scored]),
                    mean_mslp_error_hpa=mean([s.mslp_error_hpa for _, s in scored]),
                    earliest=scored[-1][0] if scored else None,
                    latest=scored[0][0] if scored else None,
                    mean_rain_brier=briers[model],
                    brier_checks=sum(1 for _, s in scored if s.rain_brier is not None),
                    **_paired_skill(scored, reference_by_date),
                )
            )

    days_verified = len({d for d in actuals if earliest <= d <= yesterday})
    review = WeeklyReview(
        period_start=earliest,
        period_end=yesterday,
        days_with_predictions=len(all_log_dates),
        days_verified=days_verified,
        cells=cells,
    )
    review.findings = _derive_findings(cells, lead_times_days)
    review.data_sufficiency = _describe_sufficiency(review, cells, lead_times_days)
    return review


def _derive_findings(cells: list[SkillCell], lead_times_days: list[int]) -> list[Finding]:
    findings: list[Finding] = []

    for k in lead_times_days:
        every_cell_at_lead = [c for c in cells if c.lead_time_days == k]

        # The yardsticks are scored in the same ledger and must not be
        # RANKED in it. "best_match is the strongest rain caller and
        # climatology the weakest" compares guidance against a yardstick as
        # though they were peers, and on this record climatology sits at the
        # bottom of Day+0, so that finding would have started appearing the
        # moment the backfill landed. Same for bias: "persistence
        # under-forecasts peak wind" is a statement about yesterday's
        # weather, not about a forecast system.
        at_lead = [c for c in every_cell_at_lead if c.model not in BASELINE_MODEL_IDS]
        baseline_cells = [c for c in every_cell_at_lead if c.model in BASELINE_MODEL_IDS]

        # --- Comparative ranking, heavily gated ---------------------------
        # Two independent gates. Both models need enough checks to be worth
        # comparing at all, AND the gap has to clear the noise floor: at
        # n=10 a binary hit rate carries ~15 points of binomial scatter, so
        # a 10-point "lead" is not evidence of anything.
        eligible = [
            c for c in at_lead
            if c.checks >= REVIEW_MIN_CHECKS_FOR_COMPARISON and c.rain_pct is not None
        ]
        if len(eligible) >= 2:
            best = max(eligible, key=lambda c: c.rain_pct)
            worst = min(eligible, key=lambda c: c.rain_pct)
            gap = best.rain_pct - worst.rain_pct
            if gap >= REVIEW_COMPARISON_MIN_GAP_PCT:
                findings.append(Finding(
                    kind="ranking",
                    claim=(
                        f"At Day+{k}, {best.model} is the strongest rain caller here "
                        f"and {worst.model} the weakest."
                    ),
                    evidence=(
                        f"{best.model} {best.correct}/{best.checks} ({best.rain_pct:.0f}%) "
                        f"vs {worst.model} {worst.correct}/{worst.checks} ({worst.rain_pct:.0f}%); "
                        f"a {gap:.0f}-point gap, above the {REVIEW_COMPARISON_MIN_GAP_PCT:.0f}-point noise floor."
                    ),
                    confidence=min(best.confidence, worst.confidence, key=_confidence_rank),
                    checks=min(best.checks, worst.checks),
                ))
            else:
                findings.append(Finding(
                    kind="ranking",
                    claim=f"At Day+{k}, no model is meaningfully better than the others here yet.",
                    evidence=(
                        f"Best-to-worst spread is only {gap:.0f} points across "
                        f"{len(eligible)} models with enough checks to compare, "
                        f"within the {REVIEW_COMPARISON_MIN_GAP_PCT:.0f}-point noise floor."
                    ),
                    confidence=min((c.confidence for c in eligible), key=_confidence_rank),
                    checks=min(c.checks for c in eligible),
                ))

        # --- Systematic bias ----------------------------------------------
        for c in at_lead:
            if c.checks < REVIEW_MIN_CHECKS_FOR_COMPARISON:
                continue
            for value, threshold, label, unit in (
                (c.mean_high_error_c, REVIEW_TEMP_BIAS_THRESHOLD_C, "daytime highs", "°C"),
                (c.mean_low_error_c, REVIEW_TEMP_BIAS_THRESHOLD_C, "overnight lows", "°C"),
                (c.mean_wind_error_kmh, REVIEW_WIND_BIAS_THRESHOLD_KMH, "peak wind", " km/h"),
            ):
                if value is None or abs(value) < threshold:
                    continue
                # Errors are actual - predicted, so a positive mean means the
                # model came in UNDER what actually happened.
                direction = "under-forecasts" if value > 0 else "over-forecasts"
                findings.append(Finding(
                    kind="bias",
                    claim=f"At Day+{k}, {c.model} systematically {direction} {label} here.",
                    evidence=f"Mean error {value:+.1f}{unit} across {c.checks} checks.",
                    confidence=c.confidence,
                    checks=c.checks,
                ))

        # --- Does being best mean anything? --------------------------------
        # ROADMAP item 57. A ranking says which model is best of those
        # present; it cannot say whether being best is worth having. On this
        # project's own record at Day+0, repeating yesterday's weather beat
        # two of the five numerical models and the project's own blend, so
        # "ECMWF is the strongest rain caller here" was a claim a reader had
        # no way to weigh.
        #
        # Gated on the SAME noise floor as the ranking, and for the same
        # reason: clearing a yardstick by three points at n=20 is scatter.
        eligible_baselines = [
            c for c in baseline_cells
            if c.checks >= REVIEW_MIN_CHECKS_FOR_COMPARISON and c.rain_pct is not None
        ]
        if eligible and eligible_baselines:
            bar = max(eligible_baselines, key=lambda c: c.rain_pct)
            clearing = sorted(
                (c for c in eligible if c.rain_pct - bar.rain_pct >= REVIEW_COMPARISON_MIN_GAP_PCT),
                key=lambda c: -c.rain_pct,
            )
            bar_evidence = (
                f"{bar.model} {bar.correct}/{bar.checks} ({bar.rain_pct:.0f}%), the best of "
                f"{len(eligible_baselines)} trivial baseline(s); a model has to clear it by "
                f"more than the {REVIEW_COMPARISON_MIN_GAP_PCT:.0f}-point noise floor to count."
            )
            if clearing:
                findings.append(Finding(
                    kind="baseline",
                    claim=(
                        f"At Day+{k}, {', '.join(c.model for c in clearing)} "
                        f"beat{'s' if len(clearing) == 1 else ''} the best trivial baseline."
                    ),
                    evidence=(
                        f"{bar_evidence} Clearing it: "
                        + ", ".join(f"{c.model} {c.rain_pct:.0f}%" for c in clearing)
                        + "."
                    ),
                    confidence=min(
                        (c.confidence for c in (*clearing, bar)), key=_confidence_rank
                    ),
                    checks=min(c.checks for c in (*clearing, bar)),
                ))
            else:
                # The finding this item exists for. Deliberately phrased as
                # what the models FAILED to do rather than as praise for the
                # baseline: nobody should come away thinking persistence is a
                # forecast worth using, only that the guidance did not earn
                # its place here at this lead.
                findings.append(Finding(
                    kind="baseline",
                    claim=(
                        f"At Day+{k}, no model here beats {bar.model} by more than "
                        "noise — the guidance is not yet earning its place at this "
                        "lead time."
                    ),
                    evidence=(
                        f"{bar_evidence} Best model: "
                        f"{max(eligible, key=lambda c: c.rain_pct).model} "
                        f"{max(c.rain_pct for c in eligible):.0f}%."
                    ),
                    confidence=min(
                        (c.confidence for c in (*eligible, bar)), key=_confidence_rank
                    ),
                    checks=min(c.checks for c in (*eligible, bar)),
                ))

        # --- Gaps worth naming ---------------------------------------------
        unscored = [c for c in at_lead if c.checks == 0]
        if len(unscored) == len(at_lead) and at_lead:
            findings.append(Finding(
                kind="gap",
                claim=f"Day+{k} has never been verified here.",
                evidence="No stored prediction at this lead time has yet had an observation to score against.",
                confidence="insufficient",
                checks=0,
            ))

    return findings


_CONFIDENCE_ORDER = {label: i for i, (_, label) in enumerate(REVIEW_CONFIDENCE_BANDS)}


def _confidence_rank(label: str) -> int:
    return _CONFIDENCE_ORDER.get(label, 0)


def _describe_sufficiency(
    review: WeeklyReview, cells: list[SkillCell], lead_times_days: list[int]
) -> str:
    """The "how much data do I have, and how much do I trust it" statement.

    Always produced, and deliberately per-lead-time rather than one blanket
    number: Day+0 accumulates a check every day, while Day+7 cannot produce
    its first until seven days in and needs roughly five weeks to fill a
    30-check window. A single overall confidence figure would badly
    overstate the extended outlook.
    """
    parts = [
        f"Reviewed {review.days_with_predictions} day(s) of stored forecasts "
        f"({review.period_start} to {review.period_end}), of which "
        f"{review.days_verified} have observations to score against."
    ]
    for k in lead_times_days:
        at_lead = [c for c in cells if c.lead_time_days == k]
        if not at_lead:
            continue
        # The weakest SCORED model sets the confidence, not the best-covered
        # one. Models do not all reach every lead time — UKMO's horizon ends
        # around 7.2 days and ICON's around 7.5 — so at Day+7 some genuinely
        # have fewer checks, visible on real data from the first week.
        # Reporting the maximum as "per model" would overstate coverage for
        # exactly the models that have least of it.
        #
        # Models with NO checks are excluded from setting that number, and
        # named separately instead. A model that has never been scored is a
        # different thing from one that has been scored less, and letting it
        # set the headline erases the record of every other model: when the
        # local met service was added, one newcomer at zero turned an honest
        # "8 checks per model" into "0 check(s) per model — not enough to say
        # anything", with eight days of scored forecasts sitting right there.
        scored = [c.checks for c in at_lead if c.checks > 0]
        checks = min(scored, default=0)
        richest = max((c.checks for c in at_lead), default=0)
        behind = sorted(c.model for c in at_lead if 0 < c.checks < richest)
        unscored = sorted(c.model for c in at_lead if c.checks == 0)
        conf = confidence_for(checks)
        if conf == "insufficient":
            need = REVIEW_CONFIDENCE_BANDS[0][0] - checks
            parts.append(
                f"Day+{k}: {checks} check(s) per model — not enough to say anything; "
                f"roughly {need} more day(s) before even a provisional read."
            )
        elif conf == "provisional":
            parts.append(
                f"Day+{k}: {checks} check(s) per model — directional only, "
                "not yet enough to rank models against each other."
            )
        elif conf == "usable":
            parts.append(
                f"Day+{k}: {checks} check(s) per model — enough to compare models, "
                "though differences smaller than about 15 points remain noise."
            )
        else:
            parts.append(f"Day+{k}: {checks} check(s) per model — a settled picture.")
        if behind:
            parts.append(
                f"(Coverage at Day+{k} is uneven: {', '.join(behind)} "
                f"{'has' if len(behind) == 1 else 'have'} fewer than the "
                f"{richest} check(s) the other models have, so any comparison "
                "at this lead time is not like-for-like.)"
            )
        if unscored:
            parts.append(
                f"({', '.join(unscored)} {'has' if len(unscored) == 1 else 'have'} "
                f"no verified checks at Day+{k} yet and {'is' if len(unscored) == 1 else 'are'} "
                "not included in the figure above.)"
            )
    return " ".join(parts)
