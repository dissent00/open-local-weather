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

FOUR KINDS, because the obvious two are not enough — and the ECMWF case is
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
- **became_available**: absent throughout the older part of the window,
  present in every run since. The mirror of `regression`, and the one nothing
  looked for — ROADMAP item 152. An exclusion made on a real measurement
  (ECMWF publishes no Day+0 wind, ICON stops at Day+6) becomes permanent by
  default, because the day it stops being true every layer handles the new
  value correctly and silently, exactly as it handled the absence. Good news
  that needs a human decision, not a page.

Reporting all four at equal volume is how monitoring stops being read, so
only the first two are actionable; the third exists to be counted, not
alerted on; the fourth is reported separately, as a notice, and — unlike the
first two — is NOT silenced by an acknowledgement, because an acknowledged gap
closing is precisely the event the acknowledgement's author needs to hear.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from openlocalweather.dates import add_days
from openlocalweather.defaults import COVERAGE_ABSENT_RUNS, COVERAGE_WINDOW_DAYS
from openlocalweather.verify.scoring import LogLookup, scored_predictions

# The fields worth watching. `onset` is deliberately absent: it is only ever
# populated when rain is forecast, so its absence is a legitimate forecast
# outcome rather than a data gap, and it carries no data at Day+3/Day+7 by
# design. Watching it would generate an alert on every dry spell.
WATCHED_VARIABLES = ("rain", "wind_kmh", "high_c", "low_c", "mslp_trend")


@dataclass(frozen=True)
class CoverageFinding:
    """One (model, lead time, variable) worth reporting."""

    kind: str  # "regression" | "peer_gap" | "never_published" | "became_available"
    model: str
    lead_time_days: int
    variable: str
    last_seen: date | None
    absent_runs: int
    checked_runs: int
    peers_with_value: list[str] = field(default_factory=list)
    # became_available only. `first_seen` is the oldest run of the unbroken
    # present stretch; `absent_runs` is the stretch before it. Separate fields
    # rather than `last_seen` reused with the opposite meaning — item 154.
    first_seen: date | None = None
    present_runs: int = 0
    # Set by `newly_available` when config acknowledges this (model, lead,
    # variable) as a known gap: that acknowledgement is now stale, and the
    # message must say so and quote it.
    acknowledged_reason: str | None = None

    @property
    def message(self) -> str:
        where = f"{self.model} Day+{self.lead_time_days} {self.variable}"
        if self.kind == "became_available":
            text = (
                f"{where}: started arriving — present in the last {self.present_runs} "
                f"run(s) since {self.first_seen}, after {self.absent_runs} run(s) "
                "without it. Nothing is wrong; a source is supplying something it "
                "did not. Worth deciding once whether it should now be read, and "
                "recording the answer."
            )
            if self.acknowledged_reason is not None:
                text += (
                    " This pair is acknowledged in config as a known gap "
                    f"(\"{self.acknowledged_reason}\"); that acknowledgement is now "
                    "stale."
                )
            return text
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
                # Row 0 — coverage describes the SCORED record, and contract
                # item 4 has not yet made every issuance scored.
                preds = {p.model: p for p in scored_predictions(entry).for_lead(k)}
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
                    continue
                # The mirror: consecutive presences from the newest run
                # backwards, and NOTHING present before them.
                #
                # THE SAME THRESHOLD IS APPLIED ON BOTH SIDES, and the prior
                # side is the one that matters. Measured 2026-09-17 against the
                # live record with no floor on the prior stretch: ECMWF Day+0
                # wind read as "present 28, absent 2" — the August fix leaving
                # the 30-day window — and kenya_met Day+0 low as "present 26,
                # absent 1". Both would have been reported as arrivals. A prior
                # stretch shorter than what counts as a regression is not an
                # absence anything can be said to have ended.
                arrived = 0
                for _, p in model_runs:
                    if not _value_present(p, variable):
                        break
                    arrived += 1
                prior = model_runs[arrived:]
                if arrived < absent_runs_threshold or len(prior) < absent_runs_threshold:
                    continue
                if any(_value_present(p, variable) for _, p in prior):
                    continue
                findings.append(
                    CoverageFinding(
                        kind="became_available",
                        model=model,
                        lead_time_days=k,
                        variable=variable,
                        last_seen=None,
                        absent_runs=len(prior),
                        checked_runs=len(model_runs),
                        first_seen=model_runs[arrived - 1][0],
                        present_runs=arrived,
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


def newly_available(
    findings: list[CoverageFinding], acknowledged: list = ()
) -> list[CoverageFinding]:
    """The arrivals, each carrying the acknowledgement it makes stale, if any.

    Deliberately the inverse of `actionable`'s filter. An acknowledgement
    says "this gap is understood, stop reporting it"; an arrival says the gap
    has closed, which is the one thing the person who wrote that
    acknowledgement needs to hear. Silencing it would leave a config entry
    describing a source that no longer exists — the same failure item 152 was
    raised on, one layer along.
    """
    out: list[CoverageFinding] = []
    for f in findings:
        if f.kind != "became_available":
            continue
        ack = next(
            (a for a in acknowledged if a.covers(f.model, f.lead_time_days, f.variable)),
            None,
        )
        out.append(replace(f, acknowledged_reason=ack.reason) if ack else f)
    return out


# --- Operational coverage: is the reliable trigger still firing? ----------


@dataclass(frozen=True)
class TriggerFinding:
    """Reported when the dispatch trigger appears to have stopped."""

    last_dispatch: date | None
    runs_checked: int
    days_since_dispatch: int | None

    @property
    def message(self) -> str:
        if self.last_dispatch is None:
            return (
                f"No run in the last {self.runs_checked} was triggered by "
                "workflow_dispatch — every one came from the cron schedule. If an "
                "external trigger is configured, it is not reaching GitHub: check "
                "the token, the token file, and the cron entry on the trigger host. "
                "The scheduled slots are still firing, which is why nothing else "
                "has complained."
            )
        return (
            f"The external trigger last fired {self.days_since_dispatch} day(s) ago "
            f"({self.last_dispatch}); every run since came from the cron schedule. "
            "The forecasts are still being produced, but by the unreliable path the "
            "dispatch trigger exists to replace."
        )


def detect_trigger_regression(
    log_lookup: LogLookup,
    today: date,
    window_days: int = COVERAGE_WINDOW_DAYS,
    absent_runs_threshold: int = COVERAGE_ABSENT_RUNS,
) -> TriggerFinding | None:
    """Notices that `workflow_dispatch` has stopped while cron carries on.

    Deliberately reports nothing when NO run records a trigger at all: that
    is a deployment which never set TRIGGER_SOURCE (a local runner, a fork on
    another CI, or entries predating the field), not a regression. Inferring
    a fault from the absence of evidence is the mistake this whole module
    exists to avoid.
    """
    runs: list[tuple[date, str | None]] = []
    cursor = add_days(today, -1)
    earliest = add_days(today, -window_days)
    while cursor >= earliest:
        entry = log_lookup(cursor)
        if entry is not None:
            runs.append((cursor, entry.meta.trigger_source))
        cursor = add_days(cursor, -1)

    if not runs:
        return None
    # Any trigger recorded at all? If the field is universally unset, this
    # deployment simply doesn't report one.
    if not any(source for _, source in runs):
        return None

    dispatches = [d for d, source in runs if source == "workflow_dispatch"]
    consecutive_without = 0
    for _, source in runs:
        if source == "workflow_dispatch":
            break
        consecutive_without += 1
    if consecutive_without < absent_runs_threshold:
        return None

    last = dispatches[0] if dispatches else None
    return TriggerFinding(
        last_dispatch=last,
        runs_checked=len(runs),
        days_since_dispatch=(today - last).days if last else None,
    )


# --- Coverage of the fields the forecaster writes ------------------------
#
# Everything above watches values CODE derives from the fetched arrays. This
# watches the ones the MODEL writes, and it exists because the two fail
# differently and only the first was covered.
#
# Measured 2026-09-11, and the count is what makes the case: THREE of these
# went absent together on 09-09 and stayed absent for three runs, after 29
# entries in which all three were filled.
#
#   peak_wind_kmh    37.4 on 09-08, None on 09-09, 09-10, 09-11
#                    (now peak_wind_secondary_kmh — item 144)
#   mslp_trend_24h   "-0.2 hPa (Steady)" on 09-08, "" after
#   air_quality_aqi  "88 US AQI (Moderate, CAMS Model)" on 09-08, None after
#
# Nothing raised for any of them. `air_quality_aqi` renders behind `{% if %}`,
# so its tile LEFT THE PAGE rather than rendering empty. `peak_wind_kmh` is
# the one that matters most — item 6 calls it "exactly the number boaters
# would act on", and it is never scored, so an absence reaches no other
# check. ROADMAP item 102.
#
# What is NOT here, and why. The scored fields — `rain`, `temp_high_c`,
# `temp_low_c` — need no watching: an absence already surfaces as an unscored
# day. `onset_window` is excluded for the reason `onset` is excluded above:
# it is populated only when rain onset is forecast, and was null on 19 of the
# record's first 32 entries, so watching it would alert on every dry spell.
#
# `peak_wind_primary_kmh` is excluded BY THAT SAME RULE — ROADMAP item 144.
# The 2026-09-16 split gave the ashore wind its own field and `_blend_prediction`
# now scores it, so an absence surfaces as an unscored day exactly like the
# other scored fields. The secondary point's stays here because nothing scores
# it: it is still the number "boaters would act on" and still reaches no other
# check. The name changed in the split; the 09-09..11 gap above is this field's.
NARRATED_FIELDS = (
    "rain_expected",
    "peak_wind_secondary_kmh",
    "mslp_trend_24h",
    "synoptic_pattern",
    "uv_index_max",
    "air_quality_aqi",
)


@dataclass(frozen=True)
class NarratedFieldFinding:
    """One forecaster-written field worth reporting."""

    kind: str  # "regression" | "never_published"
    field: str
    last_seen: date | None
    absent_runs: int
    checked_runs: int

    @property
    def message(self) -> str:
        if self.kind == "never_published":
            return (
                f"`{self.field}` has not been filled in any of the last "
                f"{self.checked_runs} runs. The forecaster is being asked for it "
                "and has never supplied it — either the prompt does not really "
                "ask, or the field is not wanted."
            )
        return (
            f"`{self.field}` was last filled on {self.last_seen} and has been "
            f"absent for {self.absent_runs} runs since. The forecaster stopped "
            "supplying a field it used to supply; nothing else will notice, "
            "because an absent display value renders as nothing rather than as "
            "an error."
        )


def _narrated_present(entry, field: str) -> bool:
    """Absent means None OR blank.

    `pipeline.py` stores `tp.<field> or ""`, so a field the model declined to
    answer reaches the record as an empty string, not as None. Treating "" as
    present is the one mistake that would make this blind to the case it was
    built for.
    """
    value = getattr(entry, field, None)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    # Numeric fields are present at any value. `bool(value)` would read a
    # a gust of 0.0 as absent, which is a real reading and not a gap.
    return True


def detect_narrated_coverage(
    log_lookup: LogLookup,
    today: date,
    window_days: int = COVERAGE_WINDOW_DAYS,
    absent_runs_threshold: int = COVERAGE_ABSENT_RUNS,
) -> list[NarratedFieldFinding]:
    """Walks the stored log backwards, newest first.

    Reads the entries as they were STORED rather than re-deriving, for the
    same reason `detect_coverage` does: what matters is what actually reached
    the record, because that is what the page rendered from.
    """
    runs: list[tuple[date, object]] = []
    cursor = add_days(today, -1)
    earliest = add_days(today, -window_days)
    while cursor >= earliest:
        entry = log_lookup(cursor)
        if entry is not None:
            runs.append((cursor, entry))
        cursor = add_days(cursor, -1)

    if not runs:
        return []

    findings: list[NarratedFieldFinding] = []
    for field in NARRATED_FIELDS:
        present = [d for d, entry in runs if _narrated_present(entry, field)]
        if not present:
            findings.append(
                NarratedFieldFinding(
                    kind="never_published",
                    field=field,
                    last_seen=None,
                    absent_runs=len(runs),
                    checked_runs=len(runs),
                )
            )
            continue

        # Consecutive absences from the newest run backwards.
        absent = 0
        for _, entry in runs:
            if _narrated_present(entry, field):
                break
            absent += 1

        if absent >= absent_runs_threshold:
            findings.append(
                NarratedFieldFinding(
                    kind="regression",
                    field=field,
                    last_seen=present[0],
                    absent_runs=absent,
                    checked_runs=len(runs),
                )
            )

    return findings


def actionable_narrated(
    findings: list[NarratedFieldFinding],
) -> list[NarratedFieldFinding]:
    """Findings a human should look at — something changed.

    `never_published` is excluded for the reason it is excluded above: there
    is nothing to chase, and reporting both at equal volume is how monitoring
    stops being read.
    """
    return [f for f in findings if f.kind == "regression"]
