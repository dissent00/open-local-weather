from datetime import date

import pytest
import requests
import requests_mock

from openlocalweather.fetch import open_meteo
from openlocalweather.fetch.open_meteo import (
    OpenMeteoFetchError,
    bucket_hourly_by_date,
    fetch_air_quality,
    fetch_archive_range,
    fetch_archive_single_day,
    fetch_forecast_daily_extended,
    fetch_forecast_hourly_today,
    fetch_regional_pressure,
    get_onset_hour,
)


def test_fetch_forecast_hourly_today_builds_correct_request():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, json={"hourly": {}})
        fetch_forecast_hourly_today(1.0, 2.0, ["gfs_seamless", "ecmwf_ifs025"], "Africa/Nairobi")
        req = m.last_request
        assert req.qs["latitude"] == ["1.0"]
        assert req.qs["longitude"] == ["2.0"]
        assert req.qs["forecast_days"] == ["1"]
        assert req.qs["models"] == ["gfs_seamless,ecmwf_ifs025"]
        assert req.qs["timezone"] == ["africa/nairobi"]  # query strings lowercased by the mock lib


def test_fetch_forecast_daily_extended_defaults_to_8_days():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, json={"daily": {}})
        fetch_forecast_daily_extended(1.0, 2.0, ["gfs_seamless"], "UTC")
        assert m.last_request.qs["forecast_days"] == ["8"]


def test_fetch_regional_pressure_joins_all_points_and_uses_best_match():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, json={"daily": {}})
        fetch_regional_pressure((1.0, 2.0), [(3.0, 4.0), (5.0, 6.0)], "UTC")
        req = m.last_request
        assert req.qs["latitude"] == ["1.0,3.0,5.0"]
        assert req.qs["longitude"] == ["2.0,4.0,6.0"]
        assert req.qs["models"] == ["best_match"]


def test_fetch_air_quality_builds_correct_request():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.AIR_QUALITY_URL, json={"hourly": {}})
        fetch_air_quality(1.0, 2.0, "UTC")
        assert m.last_request.qs["latitude"] == ["1.0"]


def test_fetch_archive_range_formats_dates():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.ARCHIVE_URL, json={"hourly": {}})
        fetch_archive_range(1.0, 2.0, date(2026, 8, 1), date(2026, 8, 10), "UTC")
        req = m.last_request
        assert req.qs["start_date"] == ["2026-08-01"]
        assert req.qs["end_date"] == ["2026-08-10"]


def test_fetch_archive_single_day_uses_same_start_and_end():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.ARCHIVE_URL, json={"hourly": {}})
        fetch_archive_single_day(1.0, 2.0, date(2026, 8, 5), "UTC")
        req = m.last_request
        assert req.qs["start_date"] == ["2026-08-05"]
        assert req.qs["end_date"] == ["2026-08-05"]


def test_fetch_raises_on_non_200():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, status_code=500, text="server error")
        with pytest.raises(OpenMeteoFetchError):
            fetch_forecast_hourly_today(1.0, 2.0, ["gfs_seamless"], "UTC")


def test_fetch_raises_on_network_error():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, exc=requests.exceptions.ConnectionError("boom"))
        with pytest.raises(OpenMeteoFetchError):
            fetch_forecast_hourly_today(1.0, 2.0, ["gfs_seamless"], "UTC")


# ---------------------------------------------------------------------------
# get_onset_hour / bucket_hourly_by_date
# ---------------------------------------------------------------------------


def test_get_onset_hour_returns_first_hour_crossing_threshold():
    times = ["2026-08-11T00:00", "2026-08-11T01:00", "2026-08-11T02:00"]
    precip = [0.0, 0.6, 1.0]
    assert get_onset_hour(times, precip) == "01:00"


def test_get_onset_hour_returns_none_when_never_crosses():
    times = ["2026-08-11T00:00", "2026-08-11T01:00"]
    precip = [0.0, 0.1]
    assert get_onset_hour(times, precip) is None


def test_bucket_hourly_by_date_empty_input():
    assert bucket_hourly_by_date({}) == {}
    assert bucket_hourly_by_date({"hourly": None}) == {}


def test_bucket_hourly_by_date_aggregates_correctly():
    hourly_json = {
        "hourly": {
            "time": [
                "2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00", "2026-08-11T18:00",
                "2026-08-12T00:00", "2026-08-12T06:00",
            ],
            "temperature_2m": [18.0, 20.0, 26.0, 22.0, 17.0, 19.0],
            "precipitation": [0.0, 0.8, 0.0, 0.0, 0.0, 0.0],
            "windgusts_10m": [10.0, 15.0, 30.0, 12.0, 8.0, 9.0],
            "pressure_msl": [1012.0, 1011.0, 1009.0, 1008.0, 1015.0, 1014.0],
        }
    }
    result = bucket_hourly_by_date(hourly_json)

    d1 = date(2026, 8, 11)
    assert result[d1].rain is True
    assert result[d1].high_c == pytest.approx(26.0)
    assert result[d1].low_c == pytest.approx(18.0)
    assert result[d1].peak_wind_kmh == pytest.approx(30.0)
    assert result[d1].mslp_trend == pytest.approx(1008.0 - 1012.0)
    assert result[d1].onset_hour == "06:00"

    d2 = date(2026, 8, 12)
    assert result[d2].rain is False
    assert result[d2].onset_hour is None


def test_bucket_hourly_by_date_wind_falls_back_to_windspeed_when_gusts_array_absent():
    hourly_json = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00"],
            "temperature_2m": [18.0, 20.0],
            "precipitation": [0.0, 0.0],
            "windspeed_10m": [5.0, 25.0],  # no windgusts_10m key at all
            "pressure_msl": [1010.0, 1010.0],
        }
    }
    result = bucket_hourly_by_date(hourly_json)
    assert result[date(2026, 8, 11)].peak_wind_kmh == pytest.approx(25.0)


def test_bucket_hourly_by_date_handles_missing_values_in_arrays():
    hourly_json = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00"],
            "temperature_2m": [18.0, None],
            "precipitation": [None, 0.6],
            "windgusts_10m": [None, None],
            "pressure_msl": [1010.0],  # short array
        }
    }
    result = bucket_hourly_by_date(hourly_json)
    entry = result[date(2026, 8, 11)]
    assert entry.high_c == pytest.approx(18.0)
    assert entry.low_c == pytest.approx(18.0)
    assert entry.peak_wind_kmh is None
    assert entry.rain is True  # the 0.6 at hour 2 crosses threshold
    assert entry.mslp_trend is None  # fewer than 2 non-null pressure readings


# ---------------------------------------------------------------------------
# Transient-failure retries
# ---------------------------------------------------------------------------


def test_a_transient_failure_is_retried_rather_than_aborting_the_run(requests_mock):
    """The asymmetry this fixes: the LLM providers retry and those calls cost
    money, while this one is free and its failure is more expensive — the LLM
    is never reached, so the run yields no forecast at all.

    Observed live: a run succeeded and an identical one 30 seconds later could
    not reach the API, with the service healthy either side.
    """
    requests_mock.get(
        open_meteo.FORECAST_URL,
        [
            {"status_code": 503, "text": "upstream hiccup"},
            {"json": {"hourly": {"time": ["2026-08-21T00:00"]}}, "status_code": 200},
        ],
    )
    result = open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert result["hourly"]["time"] == ["2026-08-21T00:00"]


def test_a_bad_request_is_not_retried(requests_mock, monkeypatch):
    """A 4xx means the REQUEST is wrong — a misspelled variable, an impossible
    coordinate. Retrying repeats the mistake more slowly and hides it behind a
    longer wait."""
    slept = []
    monkeypatch.setattr(open_meteo.time, "sleep", lambda s: slept.append(s))
    requests_mock.get(
        open_meteo.FORECAST_URL,
        status_code=400,
        text="Data corrupted at path ''. Cannot initialize ForecastVariable",
    )
    with pytest.raises(open_meteo.OpenMeteoFetchError, match="400"):
        open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert slept == [], "a malformed request must fail immediately"


def test_rate_limiting_IS_retried(requests_mock):
    """429 is the one 4xx worth waiting out — the request is fine, the pace
    isn't."""
    requests_mock.get(
        open_meteo.FORECAST_URL,
        [
            {"status_code": 429, "text": "slow down"},
            {"json": {"hourly": {"time": ["2026-08-21T00:00"]}}, "status_code": 200},
        ],
    )
    result = open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert result["hourly"]["time"] == ["2026-08-21T00:00"]


def test_persistent_failure_still_raises_after_exhausting_attempts(requests_mock):
    """Retrying must not turn a real outage into a silent hang or a bogus
    empty result — the caller has to learn the data is unavailable."""
    requests_mock.get(open_meteo.FORECAST_URL, status_code=503, text="down")
    with pytest.raises(open_meteo.OpenMeteoFetchError):
        open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert requests_mock.call_count == open_meteo.MAX_ATTEMPTS
