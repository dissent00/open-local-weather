import requests
import requests_mock

from openlocalweather.fetch.waqi import WAQI_URL_TEMPLATE, fetch_ground_aqi


def url_for(station_id: str) -> str:
    return WAQI_URL_TEMPLATE.format(station_id=station_id)


def test_fetch_ground_aqi_missing_config_returns_none_without_request():
    with requests_mock.Mocker() as m:
        assert fetch_ground_aqi("", "token") is None
        assert fetch_ground_aqi("A418534", "") is None
        assert m.call_count == 0


def test_fetch_ground_aqi_success():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("A418534"),
            json={
                "status": "ok",
                "data": {
                    "aqi": 42,
                    "iaqi": {"pm25": {"v": 18.0}, "pm10": {"v": 30.0}},
                    "city": {"name": "Kisumu Station"},
                },
            },
        )
        result = fetch_ground_aqi("A418534", "tok")
        assert result.aqi == 42
        assert result.pm25 == 18.0
        assert result.pm10 == 30.0
        assert result.station == "Kisumu Station"


def test_fetch_ground_aqi_status_not_ok_returns_none():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "error", "data": "Invalid station"})
        assert fetch_ground_aqi("A418534", "tok") is None


def test_fetch_ground_aqi_non_200_returns_none():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), status_code=500)
        assert fetch_ground_aqi("A418534", "tok") is None


def test_fetch_ground_aqi_network_error_returns_none():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), exc=requests.exceptions.ConnectionError("boom"))
        assert fetch_ground_aqi("A418534", "tok") is None


def test_fetch_ground_aqi_missing_iaqi_fields_degrade_gracefully():
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "ok", "data": {"aqi": 50, "iaqi": {}}})
        result = fetch_ground_aqi("A418534", "tok")
        assert result.aqi == 50
        assert result.pm25 is None
        assert result.pm10 is None
        assert result.station is None


# ---------------------------------------------------------------------------
# WAQI's "-" sentinel (regression: crashed a real production run)
#
# Confirmed live against this project's own configured station: WAQI
# returned aqi="-" (the composite index unavailable) while iaqi.pm25.v/
# iaqi.pm10.v were present and numeric — the station clearly had data, just
# no composite score at that moment. GroundAQI.aqi is strictly int|None, so
# constructing it with the raw string crashed pydantic validation, which
# propagated all the way up through run_daily_pipeline() and aborted an
# entire day's forecast run. This module's whole contract is to degrade to
# None on failure, never to raise into the pipeline — this was a real
# violation of that, not a hypothetical one.
# ---------------------------------------------------------------------------


def test_fetch_ground_aqi_dash_sentinel_for_aqi_does_not_crash():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("A418534"),
            json={
                "status": "ok",
                "data": {
                    "aqi": "-",  # the exact real-world value that crashed a production run
                    "iaqi": {"pm25": {"v": 157}, "pm10": {"v": 15}},
                    "city": {"name": "Kisumu International Airport"},
                },
            },
        )
        result = fetch_ground_aqi("A418534", "tok")

    assert result is not None, "a partial reading must still degrade to a usable object, not None-the-whole-thing"
    assert result.aqi is None
    assert result.pm25 == 157
    assert result.pm10 == 15
    assert result.station == "Kisumu International Airport"


def test_fetch_ground_aqi_dash_sentinel_for_pollutants_does_not_crash():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("A418534"),
            json={"status": "ok", "data": {"aqi": 42, "iaqi": {"pm25": {"v": "-"}, "pm10": {"v": "-"}}}},
        )
        result = fetch_ground_aqi("A418534", "tok")

    assert result.aqi == 42
    assert result.pm25 is None
    assert result.pm10 is None


def test_fetch_ground_aqi_numeric_string_aqi_is_parsed():
    # WAQI has been observed to send numbers as strings in some feeds —
    # make sure a real value doesn't get needlessly discarded too.
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "ok", "data": {"aqi": "77", "iaqi": {}}})
        result = fetch_ground_aqi("A418534", "tok")
    assert result.aqi == 77


def test_fetch_ground_aqi_completely_unexpected_shape_returns_none_not_raises():
    # Belt-and-suspenders path: something we didn't anticipate shouldn't
    # crash the pipeline either.
    with requests_mock.Mocker() as m:
        m.get(url_for("A418534"), json={"status": "ok", "data": {"aqi": {"nested": "garbage"}}})
        result = fetch_ground_aqi("A418534", "tok")
    assert result is None or result.aqi is None  # must not raise either way
