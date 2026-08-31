"""ROADMAP item 57 — putting the yardsticks into days already stored.

NOT hindsight. Both baselines are deterministic functions of data that
existed at each issuance, so computing them now yields exactly what they
would have produced then. What makes this delicate is not the arithmetic but
that it writes into the permanent archive, so every test here is about what
it must NOT touch.
"""

from datetime import date

from openlocalweather.backfill import backfill_entry_baselines
from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)


def _actual(rain: bool) -> DailyActual:
    return DailyActual(rain=rain, high_c=28.0, low_c=18.0, peak_wind_kmh=20.0)


def _entry(d: date, **overrides) -> DailyLogEntry:
    defaults = dict(
        date=d,
        rain_expected="Unlikely",
        temp_high_c=28.0,
        temp_low_c=18.0,
        temp_high_low_display="28°C / 82°F",
        mslp_trend_24h="steady",
        synoptic_pattern="ridge",
        narrative_markdown="## Overview\n\nWarm.",
        model_predictions=ModelPredictionsByLead(
            day0=[ModelPrediction(model="gfs_seamless", rain=False)],
            day3=[ModelPrediction(model="gfs_seamless", rain=False)],
            day7=[ModelPrediction(model="gfs_seamless", rain=False)],
        ),
        meta=LogEntryMeta(
            generated_at_utc=__import__("datetime").datetime(2026, 8, 11, 3, 6),
            llm_provider="gemini",
            llm_model="gemini-3.6-flash",
            pipeline_version="0.1.0",
        ),
    )
    defaults.update(overrides)
    return DailyLogEntry(**defaults)


_RECORD = {
    date(2026, 8, 8): _actual(True),
    date(2026, 8, 9): _actual(True),
    date(2026, 8, 10): _actual(False),
}


def test_both_baselines_are_added_at_every_lead():
    out = backfill_entry_baselines(_entry(date(2026, 8, 11)), _RECORD)
    assert out is not None

    for lead in (out.model_predictions.day0, out.model_predictions.day3,
                 out.model_predictions.day7):
        names = {p.model for p in lead}
        assert "persistence" in names
        assert "climatology" in names


def test_persistence_repeats_the_day_before_the_ISSUANCE():
    """For an entry dated D, at every lead — the lead is a property of the
    target, not of what the forecaster could see. 08-10 was dry."""
    out = backfill_entry_baselines(_entry(date(2026, 8, 11)), _RECORD)
    for lead in (out.model_predictions.day0, out.model_predictions.day3,
                 out.model_predictions.day7):
        p = next(x for x in lead if x.model == "persistence")
        assert p.rain is False


def test_climatology_reads_only_days_before_the_issuance():
    """08-08 and 08-09 wet, 08-10 dry: 2 of 3 wet, so rain."""
    out = backfill_entry_baselines(_entry(date(2026, 8, 11)), _RECORD)
    c = next(x for x in out.model_predictions.day0 if x.model == "climatology")
    assert c.rain is True


def test_onset_is_carried_only_at_day_zero():
    record = {date(2026, 8, 10): DailyActual(rain=True, onset_hour="15:00")}
    out = backfill_entry_baselines(_entry(date(2026, 8, 11)), record)

    day0 = next(x for x in out.model_predictions.day0 if x.model == "persistence")
    day3 = next(x for x in out.model_predictions.day3 if x.model == "persistence")
    assert day0.onset == "15:00"
    assert day3.onset is None


def test_running_it_twice_changes_nothing():
    """Idempotent, because a backfill that doubles its own rows on a second
    run would corrupt every figure derived from them and would look like the
    baselines being unusually well covered."""
    once = backfill_entry_baselines(_entry(date(2026, 8, 11)), _RECORD)
    assert once is not None
    twice = backfill_entry_baselines(once, _RECORD)
    assert twice is None, "a second pass must report nothing to do"


def test_it_touches_nothing_but_the_predictions():
    """The one rule. This writes into the permanent archive, and an entry's
    narrative, verification and meta are not this command's business."""
    before = _entry(date(2026, 8, 11))
    after = backfill_entry_baselines(before, _RECORD)

    assert after.narrative_markdown == before.narrative_markdown
    assert after.meta == before.meta
    assert after.verification == before.verification
    assert after.rain_expected == before.rain_expected
    assert after.date == before.date

    # And the real models' own predictions are untouched, in place and count.
    gfs_before = [p for p in before.model_predictions.day0 if p.model == "gfs_seamless"]
    gfs_after = [p for p in after.model_predictions.day0 if p.model == "gfs_seamless"]
    assert gfs_before == gfs_after


def test_an_entry_with_no_prior_observation_is_left_alone():
    """The first day of the record has nothing to persist from and nothing to
    average. Neither baseline may invent a dry call out of that."""
    assert backfill_entry_baselines(_entry(date(2026, 8, 11)), {}) is None


def test_persistence_alone_is_still_worth_adding():
    """Yesterday exists but nothing before it: climatology has one day to
    average, which is thin but real. Both are added; the sample-size gate in
    review.py is what decides whether they may be cited."""
    out = backfill_entry_baselines(
        _entry(date(2026, 8, 11)), {date(2026, 8, 10): _actual(True)}
    )
    names = {p.model for p in out.model_predictions.day0}
    assert {"persistence", "climatology"} <= names


def test_a_gap_before_the_issuance_means_no_persistence():
    """Yesterday missing, older days present. Persistence has nothing to
    repeat; climatology still does, and must not be blocked by the other's
    absence."""
    record = {date(2026, 8, 8): _actual(True), date(2026, 8, 9): _actual(True)}
    out = backfill_entry_baselines(_entry(date(2026, 8, 11)), record)
    assert out is not None

    names = {p.model for p in out.model_predictions.day0}
    assert "persistence" not in names
    assert "climatology" in names
