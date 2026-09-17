"""Noticing when a source quietly stops supplying something.

The motivating case is real: ecmwf_ifs025 supplied no Day+0 wind for the
entire life of this deployment, and nothing raised, because every layer
handled the absence correctly.
"""

from datetime import date, datetime, timezone

from openlocalweather.coverage import (
    NARRATED_FIELDS,
    CoverageFinding,
    actionable,
    actionable_narrated,
    detect_coverage,
    detect_narrated_coverage,
)
from openlocalweather.dates import add_days
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


# ---------------------------------------------------------------------------
# became_available: the mirror of regression — ROADMAP item 152 step 2
# ---------------------------------------------------------------------------


def test_flags_a_variable_that_was_absent_and_started_arriving():
    """The kind nothing looked for. An exclusion is measured once — ECMWF
    publishes no Day+0 wind, ICON stops at Day+6 — and the day it stops being
    true, every layer keeps handling the new value correctly and silently."""
    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: 22.0 if i < 3 else None,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])

    new = _find(findings, "ecmwf_ifs025", "wind_kmh", kind="became_available")
    assert new is not None
    assert new.present_runs == 3
    assert new.absent_runs == 9
    assert new.first_seen == date(2026, 8, 18)
    assert "started arriving" in new.message


def test_a_short_absence_at_the_window_edge_is_not_an_arrival():
    """Measured 2026-09-17 on the live record: with no floor on the prior
    stretch, ECMWF Day+0 wind read as 'present 28, absent 2' — the August fix
    leaving the 30-day window — and kenya_met Day+0 low as 'present 26,
    absent 1'. A prior stretch shorter than the noise threshold is not an
    absence anything can be said to have ended."""
    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: 22.0 if i < 10 else None,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert _find(findings, "ecmwf_ifs025", "wind_kmh", kind="became_available") is None


def test_two_present_runs_are_not_yet_an_arrival():
    """Mirror of one missed run being noise: a value that appears once or
    twice may be a fluke of the fetch, not a change of practice."""
    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: 22.0 if i < 2 else None,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert _find(findings, "ecmwf_ifs025", "wind_kmh", kind="became_available") is None


def test_an_intermittent_variable_is_not_an_arrival():
    """Present, then absent, then present again is a flaky field, not an
    exclusion being overturned. Only an unbroken prior absence counts."""
    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: 22.0 if i < 3 or i > 7 else None,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert _find(findings, "ecmwf_ifs025", "wind_kmh", kind="became_available") is None


def test_an_arrival_is_news_not_a_fault_and_ignores_acknowledgements():
    """Two rules. It is never in `actionable`, because nothing is wrong. And
    an acknowledgement does NOT silence it — the opposite: an acknowledged
    gap closing is exactly the event that needs a human decision, and the
    finding must say the acknowledgement is now stale."""
    from openlocalweather.config import AcknowledgedGap
    from openlocalweather.coverage import newly_available

    lookup = _history(12, {
        "gfs_seamless": 25.0, "best_match": 24.0,
        "ecmwf_ifs025": lambda i: 22.0 if i < 3 else None,
    })
    findings = detect_coverage(lookup, TODAY, MODELS, [0])
    assert all(f.kind != "became_available" for f in actionable(findings))

    acked = [AcknowledgedGap(
        model="ecmwf_ifs025", lead_time_days=0, reason="horizon ends at Day+6",
    )]
    arrivals = newly_available(findings, acked)
    assert len(arrivals) == 1
    assert arrivals[0].acknowledged_reason == "horizon ends at Day+6"
    assert "stale" in arrivals[0].message
    assert "horizon ends at Day+6" in arrivals[0].message

    unacked = newly_available(findings)
    assert unacked[0].acknowledged_reason is None
    assert "stale" not in unacked[0].message


def test_a_healthy_record_has_no_arrivals():
    from openlocalweather.coverage import newly_available

    lookup = _history(10, {m: 25.0 for m in MODELS})
    assert newly_available(detect_coverage(lookup, TODAY, MODELS, [0])) == []


# ---------------------------------------------------------------------------
# Operational coverage: has the reliable trigger stopped?
# ---------------------------------------------------------------------------


def _trigger_history(sources: list[str | None]):
    """`sources` newest-first, one per day back from 2026-08-20."""
    import datetime as _dt

    logs = {}
    for i, source in enumerate(sources):
        d = date(2026, 8, 20) - _dt.timedelta(days=i)
        e = _entry(d, [ModelPrediction(model="gfs_seamless", rain=True)])
        e.meta.trigger_source = source
        logs[d] = e
    return lambda d: logs.get(d)


def test_notices_when_dispatch_stops_but_cron_carries_on():
    """The failure the whole thing exists for. Forecasts keep appearing, so
    every other check stays quiet, while the system has silently reverted to
    the unreliable path the dispatch trigger was added to replace."""
    from openlocalweather.coverage import detect_trigger_regression

    lookup = _trigger_history(
        ["schedule", "schedule", "schedule", "schedule", "workflow_dispatch", "workflow_dispatch"]
    )
    finding = detect_trigger_regression(lookup, TODAY)
    assert finding is not None
    assert finding.last_dispatch == date(2026, 8, 16)
    assert "still being produced" in finding.message
    assert "unreliable path" in finding.message


def test_healthy_dispatch_is_silent():
    from openlocalweather.coverage import detect_trigger_regression

    lookup = _trigger_history(["workflow_dispatch"] * 6)
    assert detect_trigger_regression(lookup, TODAY) is None


def test_one_or_two_cron_days_are_not_a_regression():
    """A dispatch can miss occasionally without the path being broken."""
    from openlocalweather.coverage import detect_trigger_regression

    lookup = _trigger_history(["schedule", "schedule", "workflow_dispatch", "workflow_dispatch"])
    assert detect_trigger_regression(lookup, TODAY) is None


def test_a_deployment_that_never_reports_a_trigger_is_not_a_fault():
    """A local runner, a fork on different CI, or entries predating the field.
    Inferring a fault from absent evidence is the exact mistake this module
    exists to avoid."""
    from openlocalweather.coverage import detect_trigger_regression

    assert detect_trigger_regression(_trigger_history([None] * 8), TODAY) is None


def test_reports_when_no_dispatch_has_ever_been_seen_but_some_run_reports_one():
    """Distinct from the case above: triggers ARE being recorded, and none of
    them is a dispatch."""
    from openlocalweather.coverage import detect_trigger_regression

    lookup = _trigger_history(["schedule"] * 5 + ["schedule"])
    # No run reports a dispatch, but the field is populated — so the field is
    # working and the dispatch genuinely is not firing.
    finding = detect_trigger_regression(lookup, TODAY)
    assert finding is not None
    assert finding.last_dispatch is None
    assert "not reaching GitHub" in finding.message


# --- The fields the forecaster writes ------------------------------------
#
# The model half of this module watches values CODE computes from the fetched
# arrays. Those never broke. mslp_trend_24h and air_quality_aqi both went
# absent on 2026-09-09 and stayed absent for three runs, and the second is
# published — the Air Quality tile vanished from the page and nothing said so.
# See ROADMAP item 102.

NARRATED_TODAY = date(2026, 9, 12)


def _narrated_entry(d: date, **overrides) -> DailyLogEntry:
    fields = {
        "rain_expected": "Isolated Evening Thunderstorms",
        "peak_wind_secondary_kmh": 22.4,
        "mslp_trend_24h": "-0.4 hPa",
        "synoptic_pattern": "Troughing to the northeast",
        "uv_index_max": "9.4",
        "air_quality_aqi": "88",
        "onset_window": None,
    }
    fields.update(overrides)
    return DailyLogEntry(
        date=d, temp_high_c=26.0, temp_low_c=18.0, temp_high_low_display="26/18",
        narrative_markdown="n",
        model_predictions=ModelPredictionsByLead(day0=[]),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="t", llm_model="t", pipeline_version="0",
        ),
        **fields,
    )


def _narrated_history(days: int, per_run: dict):
    """`per_run` maps field -> value, or callable(i) -> value. i=0 is newest."""
    logs = {}
    for i in range(days):
        d = add_days(NARRATED_TODAY, -(i + 1))
        overrides = {k: (v(i) if callable(v) else v) for k, v in per_run.items()}
        logs[d] = _narrated_entry(d, **overrides)
    return lambda dd: logs.get(dd)


def _narrated(findings, field, kind=None):
    for f in findings:
        if f.field == field and (kind is None or f.kind == kind):
            return f
    return None


def test_flags_a_narrated_field_that_used_to_arrive_and_stopped():
    """The mslp_trend_24h case. Filled on 29 entries, then three runs of
    nothing, and no layer raised."""
    lookup = _narrated_history(10, {"mslp_trend_24h": lambda i: "" if i < 3 else "-0.4 hPa"})
    findings = detect_narrated_coverage(lookup, NARRATED_TODAY)

    gap = _narrated(findings, "mslp_trend_24h")
    assert gap is not None
    assert gap.kind == "regression"
    assert gap.absent_runs == 3
    assert gap.last_seen == add_days(NARRATED_TODAY, -4)
    assert gap in actionable_narrated(findings)
    assert "mslp_trend_24h" in gap.message


def test_an_empty_string_is_absent_because_the_pipeline_writes_one():
    """`pipeline.py` stores `tp.mslp_trend_24h or ""`, so a field the model
    declined to answer arrives as an empty string rather than None. Treating
    "" as present is the single mistake that would make this whole detector
    blind to the case it was built for."""
    lookup = _narrated_history(10, {"mslp_trend_24h": "   "})
    findings = detect_narrated_coverage(lookup, NARRATED_TODAY)

    assert _narrated(findings, "mslp_trend_24h") is not None


def test_flags_a_published_field_going_quiet():
    """air_quality_aqi is rendered behind `{% if %}`, so a null removes the
    tile rather than showing an empty one. Stored as None, not ""."""
    lookup = _narrated_history(10, {"air_quality_aqi": lambda i: None if i < 3 else "88"})
    findings = detect_narrated_coverage(lookup, NARRATED_TODAY)

    gap = _narrated(findings, "air_quality_aqi")
    assert gap is not None and gap.kind == "regression"


def test_onset_window_is_not_watched():
    """Null on 19 of the record's first 32 entries, because a day with no
    forecast rain has no onset. Watching it would alert on every dry spell —
    the same reason `onset` is absent from WATCHED_VARIABLES."""
    assert "onset_window" not in NARRATED_FIELDS

    lookup = _narrated_history(10, {"onset_window": None})
    assert detect_narrated_coverage(lookup, NARRATED_TODAY) == []


def test_two_missed_runs_are_noise_not_a_finding():
    lookup = _narrated_history(10, {"mslp_trend_24h": lambda i: "" if i < 2 else "-0.4 hPa"})

    assert _narrated(detect_narrated_coverage(lookup, NARRATED_TODAY), "mslp_trend_24h") is None


def test_a_healthy_narrated_record_produces_nothing_actionable():
    lookup = _narrated_history(10, {})

    assert actionable_narrated(detect_narrated_coverage(lookup, NARRATED_TODAY)) == []


def test_a_field_never_filled_is_counted_not_alerted():
    """Nothing to chase — the forecaster has never supplied it, so this is a
    standing property to count rather than a change to investigate."""
    lookup = _narrated_history(10, {"uv_index_max": None})
    findings = detect_narrated_coverage(lookup, NARRATED_TODAY)

    gap = _narrated(findings, "uv_index_max")
    assert gap is not None
    assert gap.kind == "never_published"
    assert gap not in actionable_narrated(findings)


def test_an_empty_record_yields_no_narrated_findings():
    assert detect_narrated_coverage(lambda d: None, NARRATED_TODAY) == []


def test_flags_the_wind_number_nobody_scores():
    """peak_wind_secondary_kmh went None on the same three days as the other two, and it
    is never scored — item 5 — so no other check in this project would ever
    see it go. A float, not a string: the third shape this has to handle."""
    lookup = _narrated_history(10, {"peak_wind_secondary_kmh": lambda i: None if i < 3 else 22.4})
    findings = detect_narrated_coverage(lookup, NARRATED_TODAY)

    gap = _narrated(findings, "peak_wind_secondary_kmh")
    assert gap is not None and gap.kind == "regression"


def test_a_zero_reading_is_present_not_absent():
    """`bool(0.0)` is False, so the obvious emptiness test would report a dead
    calm as a data gap."""
    lookup = _narrated_history(10, {"peak_wind_secondary_kmh": 0.0})

    assert _narrated(detect_narrated_coverage(lookup, NARRATED_TODAY), "peak_wind_secondary_kmh") is None


# ---------------------------------------------------------------------------
# The OBSERVATION side — ROADMAP item 152 step 3
# ---------------------------------------------------------------------------


def _actuals(days: int, cloud_for, today=TODAY):
    """One DailyActual per day back from yesterday. `cloud_for(i)` gives
    `cloud_cover_pct` for the i-th newest day, None for absent. Every other
    watched field is filled."""
    import datetime as _dt

    from openlocalweather.models import DailyActual

    out = {}
    for i in range(days):
        d = today - _dt.timedelta(days=i + 1)
        out[d] = DailyActual(
            rain=False, high_c=30.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=0.1,
            precip_mm=0.0, cloud_cover_pct=cloud_for(i),
        )
    return out


def _obs_sources():
    from openlocalweather.coverage import OBSERVED_FIELDS
    from openlocalweather.models import SOURCE_REANALYSIS

    return {SOURCE_REANALYSIS: OBSERVED_FIELDS[SOURCE_REANALYSIS]}


def _obs(findings, field, kind=None):
    for f in findings:
        if f.field == field and (kind is None or f.kind == kind):
            return f
    return None


def _detect_obs(actuals, remembered=None):
    from openlocalweather.coverage import detect_observation_coverage

    return detect_observation_coverage(
        actuals, point="primary", sources=_obs_sources(), today=TODAY,
        remembered=remembered or {},
    )


def test_onset_and_lightning_are_not_watched_on_the_observation_side():
    """Onset is absent on every dry day, so watching it fires on every dry
    spell — measured 2026-09-17: the secondary point's `onset_hour` read as a
    five-day regression because it had not rained there since 09-11. Lightning
    has no source at all (item 65), so its absence is not a source's
    behaviour; whoever adds a detector adds it here."""
    from openlocalweather.coverage import OBSERVED_FIELDS

    watched = {f for fields in OBSERVED_FIELDS.values() for f in fields}
    assert "onset_hour" not in watched
    assert "precipitation_onset" not in watched
    assert "lightning" not in watched


def test_a_fully_populated_record_produces_no_observation_findings():
    """Measured 2026-09-17: every watched field present on all 30 days of
    both buckets. Day one has to be quiet, or the check is not read by day
    thirty."""
    assert _detect_obs(_actuals(12, lambda i: 55.0)) == []


def test_a_field_that_stops_arriving_between_refetches_is_a_regression():
    """Tuesday to Sunday the cache is upserted one day at a time, so a loss
    shows as the newest days absent with older ones intact."""
    findings = _detect_obs(_actuals(12, lambda i: None if i < 4 else 55.0))
    reg = _obs(findings, "cloud_cover_pct", kind="regression")
    assert reg is not None
    assert reg.absent_runs == 4
    assert reg.last_seen == date(2026, 8, 16)
    assert reg.point == "primary"
    assert "last seen 2026-08-16" in reg.message


def test_one_absent_day_is_noise():
    findings = _detect_obs(_actuals(12, lambda i: None if i < 1 else 55.0))
    assert _obs(findings, "cloud_cover_pct") is None


def test_a_field_absent_everywhere_that_memory_says_was_present_is_a_regression():
    """THE CASE THE MEMORY EXISTS FOR. Monday's first issuance replaces the
    whole cache from a 40-day refetch, and the health check runs four hours
    later — so a field a rename removed is absent on EVERY cached day at the
    only moment anyone looks. Without memory that is `never_published`,
    counted and not reported, forever."""
    from openlocalweather.coverage import observation_status_key

    remembered = {observation_status_key("primary", "cloud_cover_pct"): "present 2026-08-14"}
    findings = _detect_obs(_actuals(12, lambda i: None), remembered)
    reg = _obs(findings, "cloud_cover_pct", kind="regression")
    assert reg is not None
    assert reg.last_seen == date(2026, 8, 14)
    assert reg.absent_runs == 12
    assert "refetch" in reg.message


def test_a_loss_older_than_the_window_becomes_never_published():
    """The same bound the prediction side has: a gap that rolls out of the
    window stops being a regression. A permanent loss is reported for about
    a month and then goes quiet, rather than red every Monday for a year."""
    from openlocalweather.coverage import observation_status_key

    remembered = {observation_status_key("primary", "cloud_cover_pct"): "present 2026-07-01"}
    findings = _detect_obs(_actuals(12, lambda i: None), remembered)
    assert _obs(findings, "cloud_cover_pct", kind="regression") is None
    assert _obs(findings, "cloud_cover_pct", kind="never_published") is not None


def test_a_field_absent_everywhere_with_no_memory_is_never_published():
    """Three-valued, like the CAP feed: no memory means no run was present
    for a transition, and inferring one is the mistake this module exists to
    avoid."""
    findings = _detect_obs(_actuals(12, lambda i: None))
    gap = _obs(findings, "cloud_cover_pct")
    assert gap.kind == "never_published"


def test_memory_carries_the_last_seen_date_through_weeks_of_absence():
    """Week two of a loss: the status recorded `absent <date>` last week and
    the cache is still empty. The date must be carried, not reset to `never`,
    or the regression would silently become never_published after one week
    instead of after the window."""
    from openlocalweather.coverage import observation_status, observation_status_key

    key = observation_status_key("primary", "cloud_cover_pct")
    actuals = _actuals(12, lambda i: None)
    remembered = {key: "absent 2026-08-14"}
    reg = _obs(_detect_obs(actuals, remembered), "cloud_cover_pct", kind="regression")
    assert reg is not None
    assert reg.last_seen == date(2026, 8, 14)

    status = observation_status(actuals, point="primary", sources=_obs_sources(), today=TODAY, remembered=remembered)
    assert status[key] == "absent 2026-08-14"


def test_status_records_present_with_the_newest_date_and_absent_never_without_memory():
    from openlocalweather.coverage import observation_status, observation_status_key

    key = observation_status_key("primary", "cloud_cover_pct")
    present = observation_status(_actuals(12, lambda i: 55.0), point="primary", sources=_obs_sources(), today=TODAY, remembered={})
    assert present[key] == "present 2026-08-20"
    assert present[observation_status_key("primary", "rain")] == "present 2026-08-20"

    absent = observation_status(_actuals(12, lambda i: None), point="primary", sources=_obs_sources(), today=TODAY, remembered={})
    assert absent[key] == "absent never"


def test_a_field_that_starts_arriving_between_refetches_is_an_arrival():
    findings = _detect_obs(_actuals(12, lambda i: 55.0 if i < 3 else None))
    new = _obs(findings, "cloud_cover_pct", kind="became_available")
    assert new is not None
    assert new.present_runs == 3
    assert new.absent_runs == 9
    assert new.first_seen == date(2026, 8, 18)


def test_a_field_memory_says_was_lost_and_is_back_everywhere_is_an_arrival():
    """The refetch heals a temporary outage wholesale: after the fix, Monday's
    cache carries the field on every day again, and nothing in the cache says
    it was ever gone. Memory does, and the recovery is reported once."""
    from openlocalweather.coverage import observation_status, observation_status_key

    key = observation_status_key("primary", "cloud_cover_pct")
    actuals = _actuals(12, lambda i: 55.0)
    remembered = {key: "absent 2026-08-01"}
    new = _obs(_detect_obs(actuals, remembered), "cloud_cover_pct", kind="became_available")
    assert new is not None
    assert new.present_runs == 12
    assert new.last_seen == date(2026, 8, 1)
    assert "last checked it was absent" in new.message

    # And the status moves on, so it is reported once.
    status = observation_status(actuals, point="primary", sources=_obs_sources(), today=TODAY, remembered=remembered)
    assert status[key] == "present 2026-08-20"


def test_a_field_memory_says_was_present_and_is_present_is_nothing():
    """Steady state, week after week: no finding, status refreshed."""
    from openlocalweather.coverage import observation_status_key

    remembered = {observation_status_key("primary", "cloud_cover_pct"): "present 2026-08-13"}
    assert _detect_obs(_actuals(12, lambda i: 55.0), remembered) == []


def test_only_the_sources_asked_for_are_watched():
    """The secondary point has no station, so its six station fields are
    never published by construction — measured 2026-09-17 — and asking would
    add six permanent count lines. The caller names the sources per point."""
    from openlocalweather.coverage import OBSERVED_FIELDS, detect_observation_coverage
    from openlocalweather.models import SOURCE_REANALYSIS, SOURCE_STATION

    actuals = _actuals(12, lambda i: 55.0)  # no station fields filled
    findings = detect_observation_coverage(
        actuals, point="secondary", sources={SOURCE_REANALYSIS: OBSERVED_FIELDS[SOURCE_REANALYSIS]},
        today=TODAY, remembered={},
    )
    assert findings == []

    both = detect_observation_coverage(
        actuals, point="primary", sources=OBSERVED_FIELDS, today=TODAY, remembered={},
    )
    assert {f.field for f in both} == set(OBSERVED_FIELDS[SOURCE_STATION])
    assert all(f.kind == "never_published" and f.source == SOURCE_STATION for f in both)


def test_an_empty_bucket_yields_nothing():
    assert _detect_obs({}) == []
