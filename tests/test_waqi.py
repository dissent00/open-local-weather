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
