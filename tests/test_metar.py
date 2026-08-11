import requests
import requests_mock

from openlocalweather.fetch.metar import METAR_URL, fetch_metar


def test_fetch_metar_blank_icao_returns_none_without_request():
    with requests_mock.Mocker() as m:
        assert fetch_metar("") is None
        assert m.call_count == 0


def test_fetch_metar_success():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, json=[{"icaoId": "HKKI", "temp": 24}])
        result = fetch_metar("HKKI")
        assert result == [{"icaoId": "HKKI", "temp": 24}]


def test_fetch_metar_non_200_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, status_code=503)
        assert fetch_metar("HKKI") is None


def test_fetch_metar_network_error_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, exc=requests.exceptions.ConnectionError("boom"))
        assert fetch_metar("HKKI") is None


def test_fetch_metar_malformed_json_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, text="not json")
        assert fetch_metar("HKKI") is None


def test_fetch_metar_empty_response_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, json=[])
        assert fetch_metar("HKKI") is None
