from datetime import date, datetime, timezone

from openlocalweather.defaults import LEAD_TIMES_DAYS, MODELS
from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
    TrackRecordEntry,
)
from openlocalweather.store import actuals_cache, log_store, track_record


def make_log_entry(d: date, **overrides) -> DailyLogEntry:
    defaults = dict(
        date=d,
        rain_expected="Likely",
        onset_window="14:00-16:00",
        peak_wind_kmh=23.0,
        temp_high_c=26.1,
        temp_low_c=18.4,
        temp_high_low_display="26°C / 79°F — 18°C / 65°F",
        mslp_trend_24h="-1.2 hPa, falling slowly",
        synoptic_pattern="Weak low pressure trough",
        narrative_markdown="## Overview\nRain likely.",
        model_predictions=ModelPredictionsByLead(
            day0=[
                ModelPrediction(
                    model="gfs_seamless", rain=True, onset="14:00",
                    wind_kmh=23.0, high_c=26.1, low_c=18.4, mslp_trend=-1.2,
                )
            ]
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="gemini",
            llm_model="gemini-test",
            pipeline_version="0.1.0",
        ),
    )
    defaults.update(overrides)
    return DailyLogEntry(**defaults)


# ---------------------------------------------------------------------------
# log_store
# ---------------------------------------------------------------------------


def test_write_read_log_entry_round_trip(tmp_path):
    d = date(2026, 8, 11)
    entry = make_log_entry(d)
    log_store.write_log_entry(tmp_path, entry)

    loaded = log_store.read_log_entry(tmp_path, d)
    assert loaded is not None
    assert loaded == entry


def test_read_missing_log_entry_returns_none(tmp_path):
    assert log_store.read_log_entry(tmp_path, date(2026, 1, 1)) is None


def test_log_path_uses_date_as_filename(tmp_path):
    d = date(2026, 8, 11)
    entry = make_log_entry(d)
    path = log_store.write_log_entry(tmp_path, entry)
    assert path.name == "2026-08-11.json"


def test_list_log_dates_sorted(tmp_path):
    for d in [date(2026, 8, 11), date(2026, 8, 9), date(2026, 8, 10)]:
        log_store.write_log_entry(tmp_path, make_log_entry(d))
    assert log_store.list_log_dates(tmp_path) == [
        date(2026, 8, 9), date(2026, 8, 10), date(2026, 8, 11),
    ]


def test_list_log_dates_empty_when_no_dir(tmp_path):
    assert log_store.list_log_dates(tmp_path) == []


def test_make_log_lookup(tmp_path):
    d = date(2026, 8, 11)
    entry = make_log_entry(d)
    log_store.write_log_entry(tmp_path, entry)

    lookup = log_store.make_log_lookup(tmp_path)
    assert lookup(d) == entry
    assert lookup(date(2099, 1, 1)) is None


# ---------------------------------------------------------------------------
# track_record
# ---------------------------------------------------------------------------


def test_empty_track_record_has_15_entries():
    tr = track_record.empty_track_record()
    assert len(tr.entries) == len(MODELS) * len(LEAD_TIMES_DAYS)
    keys = {(e.model, e.lead_time_days) for e in tr.entries}
    assert keys == {(m, lt) for m in MODELS for lt in LEAD_TIMES_DAYS}


def test_read_track_record_returns_empty_when_missing(tmp_path):
    tr = track_record.read_track_record(tmp_path)
    assert len(tr.entries) == 15


def test_write_read_track_record_round_trip(tmp_path):
    tr = track_record.empty_track_record()
    tr.entries[0] = TrackRecordEntry(
        model="gfs_seamless", lead_time_days=0,
        rolling_10_rain_pct=80.0, all_time_checks=5, all_time_correct=4,
    )
    track_record.write_track_record(tmp_path, tr)

    loaded = track_record.read_track_record(tmp_path)
    entry = loaded.get("gfs_seamless", 0)
    assert entry is not None
    assert entry.rolling_10_rain_pct == 80.0
    assert entry.all_time_checks == 5


def test_track_record_get_missing_returns_none():
    tr = track_record.empty_track_record()
    assert tr.get("nonexistent_model", 0) is None


# ---------------------------------------------------------------------------
# actuals_cache
# ---------------------------------------------------------------------------


def test_actuals_cache_upsert_and_round_trip(tmp_path):
    cache = actuals_cache.empty_actuals_cache()
    actual = DailyActual(rain=True, high_c=26.0, low_c=18.0, peak_wind_kmh=20.0, mslp_trend=-1.0)
    actuals_cache.upsert_day(cache.primary, date(2026, 8, 10), actual)
    actuals_cache.write_actuals_cache(tmp_path, cache)

    loaded = actuals_cache.read_actuals_cache(tmp_path)
    assert loaded.primary["2026-08-10"] == actual


def test_actuals_cache_missing_file_returns_empty(tmp_path):
    cache = actuals_cache.read_actuals_cache(tmp_path)
    assert cache.primary == {}
    assert cache.secondary == {}


def test_actuals_cache_replace_all():
    cache = actuals_cache.empty_actuals_cache()
    actuals_cache.upsert_day(cache.primary, date(2026, 1, 1), DailyActual(rain=False))

    fresh = {date(2026, 8, d): DailyActual(rain=True) for d in range(1, 4)}
    actuals_cache.replace_all(cache, "primary", fresh)

    assert "2026-01-01" not in cache.primary
    assert set(cache.primary) == {"2026-08-01", "2026-08-02", "2026-08-03"}


def test_actuals_cache_prune_older_than():
    bucket = {}
    actuals_cache.upsert_day(bucket, date(2026, 1, 1), DailyActual(rain=False))
    actuals_cache.upsert_day(bucket, date(2026, 8, 1), DailyActual(rain=True))
    actuals_cache.prune_older_than(bucket, date(2026, 6, 1))
    assert set(bucket) == {"2026-08-01"}


def test_actuals_cache_as_date_dict():
    bucket = {"2026-08-01": DailyActual(rain=True)}
    result = actuals_cache.as_date_dict(bucket)
    assert result == {date(2026, 8, 1): DailyActual(rain=True)}
