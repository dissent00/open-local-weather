from datetime import datetime, timedelta, timezone

from openlocalweather.aqi import (
    STALE_THRESHOLD_HOURS,
    hours_old,
    is_stale,
    last_known_ground_aqi,
    merge_ground_aqi,
    summarize_ground_aqi,
)
from openlocalweather.models import GroundAQIReading

NOW = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)


def reading(**overrides) -> GroundAQIReading:
    defaults = dict(name="Station", station_id="A1", aqi=50, pm25=20.0, pm10=10.0, measured_at=NOW)
    defaults.update(overrides)
    return GroundAQIReading(**defaults)


# ---------------------------------------------------------------------------
# hours_old / is_stale
# ---------------------------------------------------------------------------


def test_hours_old_none_when_no_timestamp():
    assert hours_old(reading(measured_at=None), NOW) is None


def test_hours_old_computes_correctly():
    r = reading(measured_at=NOW - timedelta(hours=2, minutes=30))
    assert hours_old(r, NOW) == 2.5


def test_is_stale_true_when_no_timestamp():
    # Unknown freshness is treated the same as stale — never assumed fresh.
    assert is_stale(reading(measured_at=None), NOW) is True


def test_is_stale_false_within_threshold():
    r = reading(measured_at=NOW - timedelta(hours=STALE_THRESHOLD_HOURS - 0.1))
    assert is_stale(r, NOW) is False


def test_is_stale_true_beyond_threshold():
    r = reading(measured_at=NOW - timedelta(hours=STALE_THRESHOLD_HOURS + 0.1))
    assert is_stale(r, NOW) is True

    # The real incident this was built for: 7.2h old.
    real_case = reading(measured_at=NOW - timedelta(hours=7.2), aqi=None)
    assert is_stale(real_case, NOW) is True


# ---------------------------------------------------------------------------
# summarize_ground_aqi
# ---------------------------------------------------------------------------


def test_summarize_empty_list_returns_none():
    assert summarize_ground_aqi([], NOW) is None


def test_summarize_all_stations_missing_aqi_returns_none():
    readings = [reading(aqi=None), reading(aqi=None)]
    assert summarize_ground_aqi(readings, NOW) is None


def test_summarize_computes_range_and_worst_station():
    readings = [
        reading(name="Kisumu Airport", aqi=42),
        reading(name="Ochieng' Avenue", aqi=168),
        reading(name="Dunga Beach", aqi=90),
    ]
    summary = summarize_ground_aqi(readings, NOW)
    assert summary.aqi_min == 42
    assert summary.aqi_max == 168
    assert summary.highest_station_name == "Ochieng' Avenue"
    assert summary.stations_with_aqi == 3
    assert summary.stations_stale == 0
    assert summary.stations_total == 3


def test_summarize_excludes_stations_with_no_aqi_from_range_but_counts_total():
    readings = [
        reading(name="A", aqi=50),
        reading(name="B", aqi=None),  # e.g. WAQI's "-" sentinel, sanitized upstream
        reading(name="C", aqi=100),
    ]
    summary = summarize_ground_aqi(readings, NOW)
    assert summary.aqi_min == 50
    assert summary.aqi_max == 100
    assert summary.highest_station_name == "C"
    assert summary.stations_with_aqi == 2
    assert summary.stations_total == 3


def test_summarize_single_station_min_equals_max():
    summary = summarize_ground_aqi([reading(name="Only Station", aqi=77)], NOW)
    assert summary.aqi_min == summary.aqi_max == 77
    assert summary.highest_station_name == "Only Station"


# ---------------------------------------------------------------------------
# Staleness exclusion (regression: the real incident this was built for —
# all three configured stations serving 7.2h-old readings simultaneously,
# with no signal of that on WAQI's own site beyond a quiet "updated Xh ago")
# ---------------------------------------------------------------------------


def test_summarize_excludes_stale_reading_from_range_even_with_numeric_aqi():
    fresh = reading(name="Fresh Station", aqi=50, measured_at=NOW - timedelta(minutes=30))
    stale = reading(name="Stale Station", aqi=200, measured_at=NOW - timedelta(hours=7.2))
    summary = summarize_ground_aqi([fresh, stale], NOW)

    assert summary.aqi_min == summary.aqi_max == 50  # the 200 must NOT appear
    assert summary.highest_station_name == "Fresh Station"
    assert summary.stations_with_aqi == 1
    assert summary.stations_stale == 1
    assert summary.stations_total == 2


def test_summarize_all_stale_returns_none_not_a_misleading_range():
    readings = [
        reading(name="A", aqi=42, measured_at=NOW - timedelta(hours=7.2)),
        reading(name="B", aqi=168, measured_at=NOW - timedelta(hours=7.2)),
    ]
    assert summarize_ground_aqi(readings, NOW) is None


def test_summarize_unknown_freshness_excluded_like_stale():
    unknown = reading(name="Unknown", aqi=50, measured_at=None)
    fresh = reading(name="Fresh", aqi=80, measured_at=NOW)
    summary = summarize_ground_aqi([unknown, fresh], NOW)
    assert summary.stations_with_aqi == 1
    assert summary.highest_station_name == "Fresh"


def test_summarize_stations_stale_only_counts_stale_stations_that_had_a_numeric_aqi():
    # A station with NO aqi at all (WAQI's "-" sentinel) is not "stale",
    # it's just absent — stations_stale should reflect staleness
    # specifically, not double up with the no-data case.
    no_data = reading(name="No Data", aqi=None, measured_at=NOW - timedelta(hours=7.2))
    stale_with_data = reading(name="Stale With Data", aqi=90, measured_at=NOW - timedelta(hours=7.2))
    fresh = reading(name="Fresh", aqi=50, measured_at=NOW)

    summary = summarize_ground_aqi([no_data, stale_with_data, fresh], NOW)
    assert summary.stations_stale == 1
    assert summary.stations_with_aqi == 1
    assert summary.stations_total == 3


# ---------------------------------------------------------------------------
# last_known_ground_aqi — what to say when nothing is fresh
# ---------------------------------------------------------------------------


def test_last_known_none_when_no_station_has_a_number():
    assert last_known_ground_aqi([reading(aqi=None), reading(aqi=None)], NOW) is None


def test_last_known_none_when_no_readings_at_all():
    assert last_known_ground_aqi([], NOW) is None


def test_last_known_picks_the_freshest_reading():
    old = reading(name="Old", aqi=90, measured_at=NOW - timedelta(hours=9))
    new = reading(name="New", aqi=40, measured_at=NOW - timedelta(hours=4))
    result = last_known_ground_aqi([old, new], NOW)
    assert result.station_name == "New"
    assert result.aqi == 40
    assert result.hours_old == 4.0
    assert result.measured_at == (NOW - timedelta(hours=4)).isoformat()


def test_last_known_reports_staleness_so_the_prompt_can_say_so():
    stale = reading(aqi=60, measured_at=NOW - timedelta(hours=STALE_THRESHOLD_HOURS + 1))
    assert last_known_ground_aqi([stale], NOW).stale is True

    fresh = reading(aqi=60, measured_at=NOW - timedelta(minutes=30))
    assert last_known_ground_aqi([fresh], NOW).stale is False


def test_last_known_ties_break_to_the_worst_station():
    # The live case: all three Kisumu stations report on the same hour. The
    # highest reading is the one a reader needs to act on.
    same_time = NOW - timedelta(hours=8)
    readings = [
        reading(name="Kisumu Airport", aqi=63, measured_at=same_time),
        reading(name="Ochieng' Avenue", aqi=54, measured_at=same_time),
        reading(name="Dunga Beach", aqi=49, measured_at=same_time),
    ]
    result = last_known_ground_aqi(readings, NOW)
    assert result.station_name == "Kisumu Airport"
    assert result.aqi == 63
    assert result.stations_reporting == 3


def test_last_known_counts_only_stations_sharing_that_timestamp():
    same_time = NOW - timedelta(hours=8)
    readings = [
        reading(name="A", aqi=63, measured_at=same_time),
        reading(name="B", aqi=54, measured_at=same_time),
        reading(name="C", aqi=99, measured_at=NOW - timedelta(hours=20)),
    ]
    assert last_known_ground_aqi(readings, NOW).stations_reporting == 2


def test_last_known_skips_readings_with_no_timestamp():
    # Unknown freshness cannot be "the most recent" — there is nothing to
    # anchor the claim to, and the whole point is quoting a time.
    undated = reading(name="Undated", aqi=95, measured_at=None)
    dated = reading(name="Dated", aqi=40, measured_at=NOW - timedelta(hours=6))
    assert last_known_ground_aqi([undated, dated], NOW).station_name == "Dated"
    assert last_known_ground_aqi([undated], NOW) is None


# ---------------------------------------------------------------------------
# merge_ground_aqi
# ---------------------------------------------------------------------------


def test_merge_prefers_the_fresh_reading_when_it_has_a_value():
    stored = [reading(aqi=30, measured_at=NOW - timedelta(hours=9))]
    fresh = [reading(aqi=90)]
    merged = merge_ground_aqi(stored, fresh)
    assert [r.aqi for r in merged] == [90]
    assert merged[0].measured_at == NOW


def test_merge_keeps_a_stored_value_when_the_fresh_one_is_null():
    # The 2026-08-22 incident: three stations re-fetched at 11:00Z came back
    # with aqi: null and replaced the morning's real readings.
    stored = [reading(name="Ochieng' Avenue", aqi=160, measured_at=NOW - timedelta(hours=9))]
    fresh = [reading(name="Ochieng' Avenue", aqi=None)]
    merged = merge_ground_aqi(stored, fresh)
    assert merged[0].aqi == 160
    assert merged[0].measured_at == NOW - timedelta(hours=9), (
        "the kept reading must carry its ORIGINAL timestamp, or it reads as current"
    )


def test_merge_keeps_a_station_the_refetch_did_not_return():
    # fetch_ground_aqi_stations drops a station that errored, so absence is
    # indistinguishable from "offline" — and neither erases a measurement.
    stored = [reading(name="Dunga Beach", station_id="A2", aqi=27)]
    merged = merge_ground_aqi(stored, [])
    assert [r.name for r in merged] == ["Dunga Beach"]
    assert merged[0].aqi == 27


def test_merge_takes_the_fresh_reading_when_neither_has_a_value():
    # Nothing to protect, and the fresh one is at least newer — its pm25 may
    # be present even when the composite AQI is not.
    stored = [reading(aqi=None, pm25=None, measured_at=NOW - timedelta(hours=9))]
    fresh = [reading(aqi=None, pm25=41.0)]
    merged = merge_ground_aqi(stored, fresh)
    assert merged[0].pm25 == 41.0
    assert merged[0].measured_at == NOW


def test_merge_matches_stations_by_id_not_by_name():
    # name is our display label and an operator can rename one; station_id is
    # the identity WAQI answers to.
    stored = [reading(name="Old Label", station_id="A1", aqi=160, measured_at=NOW - timedelta(hours=9))]
    fresh = [reading(name="New Label", station_id="A1", aqi=None)]
    merged = merge_ground_aqi(stored, fresh)
    assert len(merged) == 1
    assert merged[0].aqi == 160


def test_merge_of_nothing_stored_is_the_fresh_list():
    fresh = [reading(aqi=44)]
    assert merge_ground_aqi([], fresh) == fresh
