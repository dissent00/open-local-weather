"""The accuracy record has to be re-derivable from raw inputs.

verify/ never accumulates a running total, precisely so a correction to
what was OBSERVED propagates through every stored figure rather than only
affecting days scored after the fix. `rebuild-record` is the command that
exercises that property, and these are the tests that prove it does.
"""

from argparse import Namespace
from datetime import date, datetime, timezone

import pytest

from openlocalweather import cli
from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)
from openlocalweather.store import actuals_cache as actuals_cache_store
from openlocalweather.store import log_store
from openlocalweather.store import track_record as track_record_store

THUNDER_DAY = date(2026, 8, 24)


def seed(tmp_path, *, called_rain: bool) -> None:
    """One stored day: a model prediction, and a reanalysis actual that says
    the day was dry when the airport in fact reported thunder."""
    entry = DailyLogEntry(
        date=THUNDER_DAY,
        rain_expected="Isolated Evening Showers & Thunderstorms",
        temp_high_c=31.5,
        temp_low_c=18.9,
        temp_high_low_display="31/19",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="narrative",
        model_predictions=ModelPredictionsByLead(
            day0=[
                ModelPrediction(
                    model="gfs_seamless",
                    rain=called_rain,
                    onset="17:00" if called_rain else None,
                    wind_kmh=15.0,
                    high_c=31.0,
                    low_c=19.0,
                    mslp_trend=-1.0,
                )
            ]
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="test",
            llm_model="test",
            pipeline_version="0",
        ),
    )
    log_store.write_log_entry(tmp_path, entry)

    cache = actuals_cache_store.empty_actuals_cache()
    actuals_cache_store.upsert_day(
        cache.primary,
        THUNDER_DAY,
        DailyActual(rain=False, precip_mm=0.5, high_c=31.5, low_c=18.9, peak_wind_kmh=33.1),
    )
    actuals_cache_store.write_actuals_cache(tmp_path, cache)


def args_for(tmp_path, dry_run: bool = False) -> Namespace:
    return Namespace(
        config="config/location.example.yaml",
        data_dir=str(tmp_path),
        dry_run=dry_run,
    )


@pytest.fixture
def observed_thunder(monkeypatch):
    monkeypatch.setattr(
        cli.metar_fetch, "observed_thunder_by_date", lambda *a, **k: {THUNDER_DAY: True}
    )


def test_rebuild_stamps_thunder_onto_the_stored_actuals(tmp_path, observed_thunder):
    seed(tmp_path, called_rain=True)
    assert cli._run_rebuild_record(args_for(tmp_path)) == 0

    cache = actuals_cache_store.read_actuals_cache(tmp_path)
    stored = actuals_cache_store.as_date_dict(cache.primary)
    assert stored[THUNDER_DAY].thunder is True
    # The reanalysis reading itself is untouched — the correction is additive.
    assert stored[THUNDER_DAY].rain is False
    assert stored[THUNDER_DAY].precip_mm == 0.5


def test_rebuild_credits_a_rain_call_on_a_thunder_day(tmp_path, observed_thunder):
    seed(tmp_path, called_rain=True)
    cli._run_rebuild_record(args_for(tmp_path))

    record = track_record_store.read_track_record(tmp_path)
    day0 = [e for e in record.entries if e.model == "gfs_seamless" and e.lead_time_days == 0]
    assert day0[0].all_time_correct == 1


def test_rebuild_penalises_a_dry_call_on_a_thunder_day(tmp_path, observed_thunder):
    # The uncomfortable half of the same correction.
    seed(tmp_path, called_rain=False)
    cli._run_rebuild_record(args_for(tmp_path))

    record = track_record_store.read_track_record(tmp_path)
    day0 = [e for e in record.entries if e.model == "gfs_seamless" and e.lead_time_days == 0]
    assert day0[0].all_time_correct == 0
    assert day0[0].all_time_checks == 1


def test_dry_run_writes_nothing(tmp_path, observed_thunder):
    seed(tmp_path, called_rain=True)
    assert cli._run_rebuild_record(args_for(tmp_path, dry_run=True)) == 0

    stored = actuals_cache_store.as_date_dict(
        actuals_cache_store.read_actuals_cache(tmp_path).primary
    )
    assert stored[THUNDER_DAY].thunder is None
    # read_track_record seeds a fresh, unscored record when the file is
    # absent, so the meaningful assertion is that no file was written at all.
    assert not (tmp_path / "track_record.json").exists()


def test_rebuild_without_metar_falls_back_to_the_reanalysis(tmp_path, monkeypatch):
    # A fork with no station configured must still be able to rebuild.
    monkeypatch.setattr(cli.metar_fetch, "observed_thunder_by_date", lambda *a, **k: None)
    seed(tmp_path, called_rain=True)
    assert cli._run_rebuild_record(args_for(tmp_path)) == 0

    stored = actuals_cache_store.as_date_dict(
        actuals_cache_store.read_actuals_cache(tmp_path).primary
    )
    assert stored[THUNDER_DAY].thunder is None
    record = track_record_store.read_track_record(tmp_path)
    day0 = [e for e in record.entries if e.model == "gfs_seamless" and e.lead_time_days == 0]
    # Scored against the reanalysis alone: the rain call reads as wrong.
    assert day0[0].all_time_correct == 0


def test_rebuild_refuses_when_there_are_no_actuals(tmp_path, observed_thunder):
    assert cli._run_rebuild_record(args_for(tmp_path)) == 1
