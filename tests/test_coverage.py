"""Noticing when a source quietly stops supplying something.

The motivating case is real: ecmwf_ifs025 supplied no Day+0 wind for the
entire life of this deployment, and nothing raised, because every layer
handled the absence correctly.
"""

from datetime import date, datetime, timezone

from openlocalweather.coverage import CoverageFinding, actionable, detect_coverage
from openlocalweather.models import (
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)

TODAY = date(2026, 8, 21)
MODELS = ["gfs_seamless", "ecmwf_ifs025", "best_match"]


def _entry(d: date, day0: list[ModelPrediction]) -> DailyLogEntry:
    return DailyLogEntry(
        date=d, rain_expected="x", temp_high_c=26.0, temp_low_c=18.0,
        temp_high_low_display="26/18", mslp_trend_24h="", synoptic_pattern="",
        narrative_markdown="n",
        model_predictions=ModelPredictionsByLead(day0=day0),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="t", llm_model="t", pipeline_version="0",
        ),
    )


def _history(days: int, wind_for: dict[str, object]):
    """`wind_for` maps model -> wind value, or a callable(index) -> value."""
    logs = {}
    for i in range(days):
        d = date(2026, 8, 20) - __import__("datetime").timedelta(days=i)
        preds = []
        for m in MODELS:
            w = wind_for.get(m)
            preds.append(ModelPrediction(
                model=m, rain=True, high_c=27.0, low_c=18.0, mslp_trend=-1.0,
                wind_kmh=w(i) if callable(w) else w,
            ))
        logs[d] = _entry(d, preds)
    return lambda d: logs.get(d)


def _find(findings, model, variable, kind=None):
    for f in findings:
        if f.model == model and f.variable == variable and (kind is None or f.kind == kind):
            return f
    return None


def test_flags_a_model_alone_in_not_supplying_what_its_peers_do():
    """The ECMWF case, and the reason a regression check alone is not enough:
    the gap was there from the very first run, so there is no before-and-after
    transition to detect. What IS visible on day one is that four models
    reported wind and one did not."""
    lookup = _history(10, {"gfs_seamless": 25.0, "best_match": 24.0, "ecmwf_ifs025": None})
    findings = detect_coverage(lookup, TODAY, MODELS, [0])

    gap = _find(findings, "ecmwf_ifs025", "wind_kmh")
    assert gap is not None
    assert gap.kind == "peer_gap"
    assert gap.peers_with_value == ["best_match", "gfs_seamless"]
    assert gap in actionable(findings)
    assert "requested under a name that returns nothing" in gap.message


def test_a_variable_no_model_supplies_is_a_property_not_a_fault():
    """Nothing to chase, so it must not compete for attention with the
    findings that do need chasing."""
    lookup = _history(10, {m: None for m in MODELS})
    findings = detect_coverage(lookup, TODAY, MODELS, [0])

    gap = _find(findings, "ecmwf_ifs025", "wind_kmh")
    assert gap.kind == "never_published"
    assert gap not in actionable(findings)
    assert "a property of the data, not a fault" in gap.message


def test_flags_a_variable_that_used_to_arrive_and_stopped():
    """The upstream-rename signature."""
    # Newest 4 runs absent (i is 0 for the newest), older ones present.
    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: None if i < 4 else 22.0,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])

    reg = _find(findings, "ecmwf_ifs025", "wind_kmh", kind="regression")
    assert reg is not None
    assert reg.absent_runs == 4
    assert reg.last_seen == date(2026, 8, 16)
    assert reg in actionable(findings)


def test_one_missed_run_is_noise_not_a_finding():
    """A single failed fetch must not page anyone."""
    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: None if i < 1 else 22.0,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert _find(findings, "ecmwf_ifs025", "wind_kmh", kind="regression") is None


def test_a_healthy_record_produces_nothing_actionable():
    """The property that keeps this readable: silence when all is well."""
    lookup = _history(10, {m: 25.0 for m in MODELS})
    assert actionable(detect_coverage(lookup, TODAY, MODELS, [0])) == []


def test_onset_is_not_watched():
    """Onset is populated only when rain is forecast, so its absence is a
    legitimate forecast outcome. Watching it would fire on every dry spell —
    and at Day+3/Day+7 it is never populated by design."""
    lookup = _history(10, {m: 25.0 for m in MODELS})
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert all(f.variable != "onset" for f in findings)


def test_an_empty_record_yields_nothing_rather_than_alarms():
    """A brand-new fork has no history. That is not a coverage problem."""
    assert detect_coverage(lambda d: None, TODAY, MODELS, [0]) == []


def test_an_acknowledged_gap_stops_competing_for_attention():
    """The property that keeps this readable past month one.

    Run against the live record this check first returned eleven items: ten
    were the documented ICON/UKMO Day+7 horizon limit, one was the real ECMWF
    wind gap. Burying one true finding in ten expected ones is how a check
    stops being read.
    """
    from openlocalweather.config import AcknowledgedGap

    lookup = _history(10, {"gfs_seamless": 25.0, "best_match": 24.0, "ecmwf_ifs025": None})
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert actionable(findings), "unacknowledged, it reports"

    acked = [AcknowledgedGap(
        model="ecmwf_ifs025", lead_time_days=0, variable="wind_kmh",
        reason="Investigated: genuinely not published at this lead time.",
    )]
    assert actionable(findings, acked) == []


def test_acknowledging_one_variable_does_not_silence_the_others():
    from openlocalweather.config import AcknowledgedGap

    lookup = _history(10, {
        "gfs_seamless": 25.0, "best_match": 24.0, "ecmwf_ifs025": None,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    # Acknowledge a DIFFERENT variable on the same model.
    acked = [AcknowledgedGap(
        model="ecmwf_ifs025", lead_time_days=0, variable="mslp_trend", reason="n/a",
    )]
    still = actionable(findings, acked)
    assert any(f.variable == "wind_kmh" for f in still)


def test_an_acknowledgement_without_a_variable_covers_the_whole_lead_time():
    """How the ICON/UKMO Day+7 horizon is recorded: the model has no data at
    all at that range, so naming five variables would be noise."""
    from openlocalweather.config import AcknowledgedGap

    lookup = _history(10, {"gfs_seamless": 25.0, "best_match": 24.0, "ecmwf_ifs025": None})
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    acked = [AcknowledgedGap(model="ecmwf_ifs025", lead_time_days=0, reason="horizon")]
    assert actionable(findings, acked) == []
