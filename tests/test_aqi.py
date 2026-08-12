from openlocalweather.aqi import summarize_ground_aqi
from openlocalweather.models import GroundAQIReading


def reading(**overrides) -> GroundAQIReading:
    defaults = dict(name="Station", station_id="A1", aqi=50, pm25=20.0, pm10=10.0)
    defaults.update(overrides)
    return GroundAQIReading(**defaults)


def test_summarize_empty_list_returns_none():
    assert summarize_ground_aqi([]) is None


def test_summarize_all_stations_missing_aqi_returns_none():
    readings = [reading(aqi=None), reading(aqi=None)]
    assert summarize_ground_aqi(readings) is None


def test_summarize_computes_range_and_worst_station():
    readings = [
        reading(name="Kisumu Airport", aqi=42),
        reading(name="Ochieng' Avenue", aqi=168),
        reading(name="Dunga Beach", aqi=90),
    ]
    summary = summarize_ground_aqi(readings)
    assert summary.aqi_min == 42
    assert summary.aqi_max == 168
    assert summary.highest_station_name == "Ochieng' Avenue"
    assert summary.stations_with_aqi == 3
    assert summary.stations_total == 3


def test_summarize_excludes_stations_with_no_aqi_from_range_but_counts_total():
    readings = [
        reading(name="A", aqi=50),
        reading(name="B", aqi=None),  # e.g. WAQI's "-" sentinel, sanitized upstream
        reading(name="C", aqi=100),
    ]
    summary = summarize_ground_aqi(readings)
    assert summary.aqi_min == 50
    assert summary.aqi_max == 100
    assert summary.highest_station_name == "C"
    assert summary.stations_with_aqi == 2
    assert summary.stations_total == 3


def test_summarize_single_station_min_equals_max():
    summary = summarize_ground_aqi([reading(name="Only Station", aqi=77)])
    assert summary.aqi_min == summary.aqi_max == 77
    assert summary.highest_station_name == "Only Station"
