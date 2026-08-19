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

from openlocalweather.dates import add_days
from openlocalweather.defaults import (
    LEAD_TIMES_DAYS,
    MODELS,
    REVIEW_COMPARISON_MIN_GAP_PCT,
    REVIEW_CONFIDENCE_BANDS,
    REVIEW_MIN_CHECKS_FOR_COMPARISON,
    REVIEW_TEMP_BIAS_THRESHOLD_C,
    REVIEW_WIND_BIAS_THRESHOLD_KMH,
)
from openlocalweather.models import DailyActual
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
        for model in models:
            scored = collect_scores(model, k, yesterday, earliest, log_lookup, actuals)
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
        at_lead = [c for c in cells if c.lead_time_days == k]

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
        # The WEAKEST model sets the confidence, not the best-covered one.
        # Models do not all reach every lead time — UKMO's horizon ends around
        # 7.2 days and ICON's around 7.5 — so at Day+7 some models genuinely
        # have fewer checks than others, and this shows up on real data from
        # the very first week. Reporting the maximum as "per model" would
        # overstate coverage for exactly the models that have least of it.
        checks = min((c.checks for c in at_lead), default=0)
        richest = max((c.checks for c in at_lead), default=0)
        behind = sorted(c.model for c in at_lead if c.checks < richest)
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
    return " ".join(parts)
