from datetime import date, datetime, timezone

from openlocalweather.defaults import LEAD_TIMES_DAYS, MODELS
from openlocalweather.models import (
    IssuanceSnapshot,
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
# DailyLogEntry.issuance_log
# ---------------------------------------------------------------------------


def test_issuance_log_has_one_element_for_a_day_never_reissued():
    entry = make_log_entry(date(2026, 8, 11))
    log = entry.issuance_log()
    assert len(log) == 1
    assert log[0].narrative_markdown == entry.narrative_markdown


def test_issuance_log_reads_the_old_shape_via_morning_issuance():
    """Every entry committed before earlier_issuances existed carries only
    morning_issuance, with no earlier_issuances key in the JSON at all —
    not an empty list, an ABSENT key. issuance_log() must read that shape
    forever: data/log/ is the project's record, and rewriting an old entry
    to carry the new field would edit history to look like it always did.
    Built via model_validate on a plain dict so the missing key is actually
    exercised, not a Python default standing in for it.
    """
    old_shape = dict(
        date=date(2026, 8, 11),
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26°C / 79°F",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="## Overview\nSecond issuance.",
        morning_issuance=dict(
            rain_expected="Unlikely",
            temp_high_c=27.0,
            temp_low_c=19.0,
            temp_high_low_display="27°C / 81°F",
            mslp_trend_24h="steady",
            synoptic_pattern="ridge",
            narrative_markdown="## Overview\nFirst issuance.",
            generated_at_utc=datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc),
        ),
        meta=dict(
            generated_at_utc=datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc),
            llm_provider="gemini",
            llm_model="gemini-test",
            pipeline_version="0.1.0",
        ),
    )
    entry = DailyLogEntry.model_validate(old_shape)

    log = entry.issuance_log()
    assert len(log) == 2
    assert "First issuance" in log[0].narrative_markdown
    assert "Second issuance" in log[1].narrative_markdown


def test_a_later_issuance_carries_its_own_time_not_the_first_runs():
    """An issuance's time is when the entry last SAID something, not when it
    first existed.

    meta.generated_at_utc is deliberately frozen at the day's first run — it
    is the audit trail for when the entry came into being, and a later run
    must not move it. So a snapshot that reads generated_at_utc stamps every
    issuance of the day with the first one's clock: an update published at
    22:00 lands in the archive, and in the next run's prompt, claiming 06:07.
    Wrong quietly, and more wrongly than the "earlier today" it replaced,
    because it is confident.
    """
    first = datetime(2026, 8, 11, 6, 7, tzinfo=timezone.utc)
    latest = datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc)
    entry = make_log_entry(date(2026, 8, 11)).model_copy(
        update={
            "narrative_markdown": "## Overview\nThe 22:00 update.",
            "earlier_issuances": [
                IssuanceSnapshot(
                    rain_expected="Unlikely",
                    temp_high_c=27.0,
                    temp_low_c=19.0,
                    temp_high_low_display="27°C / 81°F",
                    mslp_trend_24h="steady",
                    synoptic_pattern="ridge",
                    narrative_markdown="## Overview\nThe 06:07 issuance.",
                    generated_at_utc=first,
                )
            ],
        }
    )
    entry.meta.generated_at_utc = first
    entry.meta.refreshed_at = latest

    log = entry.issuance_log()
    assert [i.generated_at_utc for i in log] == [first, latest]
    assert entry.to_issuance_snapshot().generated_at_utc == latest


def test_old_shape_entry_has_no_guidance_fields():
    """Every entry committed before guidance_initialised_at/
    guidance_age_hours/guidance_source existed carries none of those keys at
    all — not present as null, ABSENT. Must parse and read as None forever,
    same dual-read discipline as morning_issuance/earlier_issuances above; no
    migration ever touches data/log/*.json to backfill this. Built via
    model_validate on a plain dict so the missing keys are actually
    exercised, not a Python default standing in for them.
    """
    old_shape = dict(
        date=date(2026, 8, 11),
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26°C / 79°F",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="## Overview\nNo guidance fields at all.",
        meta=dict(
            generated_at_utc=datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc),
            llm_provider="gemini",
            llm_model="gemini-test",
            pipeline_version="0.1.0",
        ),
    )
    entry = DailyLogEntry.model_validate(old_shape)

    assert entry.guidance_initialised_at is None
    assert entry.guidance_age_hours is None
    assert entry.guidance_source is None

    # A snapshot taken of an old-shape entry must carry the same absence
    # through rather than substituting a value that was never really there.
    snapshot = entry.to_issuance_snapshot()
    assert snapshot.guidance_initialised_at is None
    assert snapshot.guidance_age_hours is None
    assert snapshot.guidance_source is None


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


def test_local_bulletin_round_trips_and_is_optional(tmp_path):
    """Entries written before this field existed must still load — the log is
    an append-only historical record, so a schema addition that broke old
    files would break the accuracy history the project's claims rest on."""
    from datetime import datetime, timezone

    from openlocalweather.models import LocalBulletinRecord

    entry = make_log_entry(date(2026, 8, 19))
    assert entry.local_bulletin is None, "absent by default"

    entry.local_bulletin = LocalBulletinRecord(
        source_name="Kenya Meteorological Department (KMD)",
        text="Occasional rains are expected over the Lake Victoria Basin.",
        fetched_at_utc=datetime(2026, 8, 19, 3, 7, tzinfo=timezone.utc),
    )
    log_store.write_log_entry(tmp_path, entry)
    loaded = log_store.read_log_entry(tmp_path, date(2026, 8, 19))

    assert loaded.local_bulletin is not None
    assert loaded.local_bulletin.text == entry.local_bulletin.text
    assert loaded.local_bulletin.source_name == "Kenya Meteorological Department (KMD)"


def test_an_unavailable_bulletin_is_stored_not_dropped(tmp_path):
    """The "no text could be extracted" case is itself the record of what
    happened that day. Filtering it at write time would leave a silent hole
    indistinguishable from a day the fetcher never ran."""
    from datetime import datetime, timezone

    from openlocalweather.models import LocalBulletinRecord

    entry = make_log_entry(date(2026, 8, 19))
    entry.local_bulletin = LocalBulletinRecord(
        source_name="Kenya Meteorological Department (KMD)",
        text="Kenya Meteorological Department (KMD): PDF fetched but no text could be extracted.",
        fetched_at_utc=datetime(2026, 8, 19, 3, 7, tzinfo=timezone.utc),
    )
    log_store.write_log_entry(tmp_path, entry)
    loaded = log_store.read_log_entry(tmp_path, date(2026, 8, 19))
    assert "no text could be extracted" in loaded.local_bulletin.text
