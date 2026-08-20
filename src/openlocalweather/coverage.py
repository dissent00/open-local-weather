"""Notice when a data source quietly stops supplying something.

The companion to tolerant parsing, and the half this project was missing.

`ecmwf_ifs025` returned no Day+0 wind for the entire life of this deployment
and nothing raised — because every layer behaved *correctly*. Open-Meteo
served a correctly-named all-null array, extraction recorded `wind_kmh=None`,
`score_prediction` declined to score a None, and the rolling stats excluded
it. Absence propagated cleanly as absence, exactly as designed. It surfaced
only months later, when the weekly review happened to aggregate wind per
model and one model had a dash where four had numbers.

Tolerance keeps the system RUNNING through an upstream change. This module
makes the change VISIBLE. They are different properties, and the second is
what turns a months-long silent gap into a one-day one.

Everything here is derived from the committed log — no new storage, no extra
fetch. A run that stored a prediction also stored, implicitly, which fields
that prediction could and couldn't fill.

THREE KINDS, because the obvious two are not enough — and the ECMWF case is
exactly what proves it.

- **regression**: present before, absent now. An upstream rename or a
  retired model.
- **peer_gap**: never present for this model, but peers at the same lead
  time DO supply it. This is the one that matters. The ECMWF wind gap had
  no before-and-after transition to detect — it was absent from the very
  first run — so a regression check alone would have missed it forever.
  What was visible from day one is that four other models reported wind and
  ECMWF did not.
- **never_published**: absent for this model AND for every peer. A property
  of the data, not a fault — no model supplies it, so there is nothing to
  investigate.

Reporting all three at equal volume is how monitoring stops being read, so
only the first two are actionable; the third exists to be counted, not
alerted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from openlocalweather.dates import add_days
from openlocalweather.defaults import COVERAGE_ABSENT_RUNS, COVERAGE_WINDOW_DAYS
from openlocalweather.verify.scoring import LogLookup

# The fields worth watching. `onset` is deliberately absent: it is only ever
# populated when rain is forecast, so its absence is a legitimate forecast
# outcome rather than a data gap, and it carries no data at Day+3/Day+7 by
# design. Watching it would generate an alert on every dry spell.
WATCHED_VARIABLES = ("rain", "wind_kmh", "high_c", "low_c", "mslp_trend")


@dataclass(frozen=True)
class CoverageFinding:
    """One (model, lead time, variable) worth reporting."""

    kind: str  # "regression" | "never_published"
    model: str
    lead_time_days: int
    variable: str
    last_seen: date | None
    absent_runs: int
    checked_runs: int
    peers_with_value: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        where = f"{self.model} Day+{self.lead_time_days} {self.variable}"
        if self.kind == "peer_gap":
            return (
                f"{where}: never supplied in {self.checked_runs} run(s), while "
                f"{len(self.peers_with_value)} other model(s) do supply it "
                f"({', '.join(self.peers_with_value)}). Either this model genuinely "
                "does not publish it, or it is being requested under a name that "
                "returns nothing — the shape of the ECMWF wind gap. Worth checking "
                "once, then recording the answer."
            )
        if self.kind == "never_published":
            return (
                f"{where}: not supplied by any model in {self.checked_runs} run(s) "
                "— a property of the data, not a fault."
            )
        return (
            f"{where}: absent for the last {self.absent_runs} run(s), "
            f"last seen {self.last_seen}. This is the signature of an upstream "
            "rename or a retired model — the value is being recorded as unknown, "
            "so nothing is wrong with the forecast, but a variable that used to "
            "be scored no longer is."
        )


def _value_present(prediction, variable: str) -> bool:
    return getattr(prediction, variable, None) is not None


def detect_coverage(
    log_lookup: LogLookup,
    today: date,
    models: list[str],
    lead_times_days: list[int],
    window_days: int = COVERAGE_WINDOW_DAYS,
    absent_runs_threshold: int = COVERAGE_ABSENT_RUNS,
) -> list[CoverageFinding]:
    """Walks the stored log backwards, newest first.

    Reads the predictions as they were STORED rather than re-fetching, so a
    finding always reflects what actually went into the record — which is the
    thing that matters, since that is what got scored (or didn't).
    """
    # Newest-first list of (date, {model: prediction}) per lead time.
    by_lead: dict[int, list[tuple[date, dict]]] = {k: [] for k in lead_times_days}
    cursor = add_days(today, -1)
    earliest = add_days(today, -window_days)
    while cursor >= earliest:
        entry = log_lookup(cursor)
        if entry is not None:
            for k in lead_times_days:
                preds = {p.model: p for p in entry.model_predictions.for_lead(k)}
                if preds:
                    by_lead[k].append((cursor, preds))
        cursor = add_days(cursor, -1)

    findings: list[CoverageFinding] = []
    for k in lead_times_days:
        runs = by_lead[k]
        if not runs:
            continue
        for model in models:
            # Runs in which this model appeared at all. A model absent
            # entirely is a different problem (a config change, or a model
            # added partway through) and is not what this watches.
            model_runs = [(d, preds[model]) for d, preds in runs if model in preds]
            if not model_runs:
                continue
            for variable in WATCHED_VARIABLES:
                present = [(d, p) for d, p in model_runs if _value_present(p, variable)]
                if not present:
                    # Do any OTHER models supply this at this lead time? If so
                    # the gap belongs to this model, not to the variable — the
                    # distinction that makes the ECMWF case detectable at all.
                    peers = {
                        m
                        for _, preds in runs
                        for m, p in preds.items()
                        if m != model and _value_present(p, variable)
                    }
                    findings.append(
                        CoverageFinding(
                            kind="peer_gap" if peers else "never_published",
                            model=model,
                            lead_time_days=k,
                            variable=variable,
                            last_seen=None,
                            absent_runs=len(model_runs),
                            checked_runs=len(model_runs),
                            peers_with_value=sorted(peers),
                        )
                    )
                    continue
                # Consecutive absences from the newest run backwards.
                absent = 0
                for _, p in model_runs:
                    if _value_present(p, variable):
                        break
                    absent += 1
                if absent >= absent_runs_threshold:
                    findings.append(
                        CoverageFinding(
                            kind="regression",
                            model=model,
                            lead_time_days=k,
                            variable=variable,
                            last_seen=present[0][0],
                            absent_runs=absent,
                            checked_runs=len(model_runs),
                        )
                    )
    return findings


def actionable(
    findings: list[CoverageFinding], acknowledged: list = ()
) -> list[CoverageFinding]:
    """Findings a human should look at.

    Something changed, or one model is alone in not supplying what its peers
    do. Excluded: `never_published` (nothing supplies it, so there is nothing
    to chase) and anything matching an acknowledged gap in config.

    The acknowledgement list is what keeps this readable over time. Run
    against the live record it initially returned eleven items, ten of which
    were the documented ICON/UKMO Day+7 horizon limit and one of which was
    the real ECMWF wind gap. A check that buries its one true finding in ten
    expected ones is a check nobody reads by month two.
    """
    return [
        f
        for f in findings
        if f.kind in ("regression", "peer_gap")
        and not any(a.covers(f.model, f.lead_time_days, f.variable) for a in acknowledged)
    ]
