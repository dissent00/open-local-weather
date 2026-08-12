import requests
import requests_mock

from openlocalweather.config import WaqiStation
from openlocalweather.fetch.waqi import (
    WAQI_URL_TEMPLATE,
    fetch_ground_aqi_reading,
    fetch_ground_aqi_stations,
)


def url_for(station_id: str) -> str:
    return WAQI_URL_TEMPLATE.format(station_id=station_id)


# ---------------------------------------------------------------------------
# fetch_ground_aqi_reading (single station)
# ---------------------------------------------------------------------------


def test_fetch_ground_aqi_reading_missing_config_returns_none_without_request():
    with requests_mock.Mocker() as m:
        assert fetch_ground_aqi_reading("Downtown", "", "token") is None
        assert fetch_ground_aqi_reading("Downtown", "A418534", "") is None
        assert m.call_count == 0


def test_fetch_ground_aqi_reading_success():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("A418534"),
            json={
                "status": "ok",
                "data": {
                    "aqi": 42,
                    "iaqi": {"pm25": {"v": 18.0}, "pm10": {"v": 30.0}},
                    "city": {"name": "WAQI's own verbose station name"},
                },
            },
        )
        result = fetch_ground_aqi_reading("Kisumu Airport", "A418534", "tok")
        assert result.aqi == 42
        assert result.pm25 == 18.0
        assert result.pm10 == 30.0
        # OUR configured name is authoritative, not WAQI's own city.name.
        assert result.name == "Kisumu Airport"
        assert result.station_id == "A418534"


def test_fetch_ground_aqi_reading_status_not_ok_returns_none():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "error", "data": "Invalid station"})
        assert fetch_ground_aqi_reading("Downtown", "A418534", "tok") is None


def test_fetch_ground_aqi_reading_non_200_returns_none():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), status_code=500)
        assert fetch_ground_aqi_reading("Downtown", "A418534", "tok") is None


def test_fetch_ground_aqi_reading_network_error_returns_none():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), exc=requests.exceptions.ConnectionError("boom"))
        assert fetch_ground_aqi_reading("Downtown", "A418534", "tok") is None


def test_fetch_ground_aqi_reading_missing_iaqi_fields_degrade_gracefully():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "ok", "data": {"aqi": 50, "iaqi": {}}})
        result = fetch_ground_aqi_reading("Downtown", "A418534", "tok")
        assert result.aqi == 50
        assert result.pm25 is None
        assert result.pm10 is None


# ---------------------------------------------------------------------------
# WAQI's "-" sentinel (regression: crashed a real production run)
#
# Confirmed live against this project's own configured station: WAQI
# returned aqi="-" (the composite index unavailable) while iaqi.pm25.v/
# iaqi.pm10.v were present and numeric — the station clearly had data, just
# no composite score at that moment. GroundAQIReading.aqi is strictly
# int|None, so constructing it with the raw string crashed pydantic
# validation, which propagated all the way up through run_daily_pipeline()
# and aborted an entire day's forecast run. This module's whole contract is
# to degrade to None on failure, never to raise into the pipeline — this
# was a real violation of that, not a hypothetical one.
# ---------------------------------------------------------------------------


def test_fetch_ground_aqi_reading_dash_sentinel_for_aqi_does_not_crash():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("A418534"),
            json={
                "status": "ok",
                "data": {
                    "aqi": "-",  # the exact real-world value that crashed a production run
                    "iaqi": {"pm25": {"v": 157}, "pm10": {"v": 15}},
                },
            },
        )
        result = fetch_ground_aqi_reading("Kisumu Airport", "A418534", "tok")

    assert result is not None, "a partial reading must still degrade to a usable object, not None-the-whole-thing"
    assert result.aqi is None
    assert result.pm25 == 157
    assert result.pm10 == 15


def test_fetch_ground_aqi_reading_dash_sentinel_for_pollutants_does_not_crash():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("A418534"),
            json={"status": "ok", "data": {"aqi": 42, "iaqi": {"pm25": {"v": "-"}, "pm10": {"v": "-"}}}},
        )
        result = fetch_ground_aqi_reading("Downtown", "A418534", "tok")

    assert result.aqi == 42
    assert result.pm25 is None
    assert result.pm10 is None


def test_fetch_ground_aqi_reading_numeric_string_aqi_is_parsed():
    # WAQI has been observed to send numbers as strings in some feeds —
    # make sure a real value doesn't get needlessly discarded too.
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "ok", "data": {"aqi": "77", "iaqi": {}}})
        result = fetch_ground_aqi_reading("Downtown", "A418534", "tok")
    assert result.aqi == 77


def test_fetch_ground_aqi_reading_completely_unexpected_shape_returns_none_not_raises():
    # Belt-and-suspenders path: something we didn't anticipate shouldn't
    # crash the pipeline either.
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "ok", "data": {"aqi": {"nested": "garbage"}}})
        result = fetch_ground_aqi_reading("Downtown", "A418534", "tok")
    assert result is None or result.aqi is None  # must not raise either way


# ---------------------------------------------------------------------------
# fetch_ground_aqi_stations (multi-station batch)
# ---------------------------------------------------------------------------


def test_fetch_ground_aqi_stations_fetches_all_configured_stations():
    stations = [
        WaqiStation(name="Airport", station_id="A1"),
        WaqiStation(name="Downtown", station_id="A2"),
        WaqiStation(name="Industrial Area", station_id="A3"),
    ]
    with requests_mock.Mocker() as m:
        m.get(url_for("A1"), json={"status": "ok", "data": {"aqi": 42, "iaqi": {}}})
        m.get(url_for("A2"), json={"status": "ok", "data": {"aqi": 80, "iaqi": {}}})
        m.get(url_for("A3"), json={"status": "ok", "data": {"aqi": 120, "iaqi": {}}})
        results = fetch_ground_aqi_stations(stations, "tok")

    assert len(results) == 3
    assert {r.name for r in results} == {"Airport", "Downtown", "Industrial Area"}
    assert {r.aqi for r in results} == {42, 80, 120}


def test_fetch_ground_aqi_stations_one_failure_does_not_drop_the_others():
    stations = [
        WaqiStation(name="Working Station", station_id="A1"),
        WaqiStation(name="Broken Station", station_id="A2"),
    ]
    with requests_mock.Mocker() as m:
        m.get(url_for("A1"), json={"status": "ok", "data": {"aqi": 42, "iaqi": {}}})
        m.get(url_for("A2"), status_code=500)
        results = fetch_ground_aqi_stations(stations, "tok")

    assert len(results) == 1
    assert results[0].name == "Working Station"


def test_fetch_ground_aqi_stations_empty_list_returns_empty_list():
    assert fetch_ground_aqi_stations([], "tok") == []
