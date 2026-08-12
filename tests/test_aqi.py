from datetime import datetime, timedelta, timezone

from openlocalweather.aqi import STALE_THRESHOLD_HOURS, hours_old, is_stale, summarize_ground_aqi
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
