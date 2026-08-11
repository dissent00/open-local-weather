import pytest

from openlocalweather.extract import (
    extract_day0_predictions_from_hourly,
    extract_day_n_predictions_from_daily,
)

MODELS = ["gfs_seamless", "ecmwf_ifs025"]


def test_extract_day0_empty_input_returns_empty_list():
    assert extract_day0_predictions_from_hourly({}, MODELS) == []
    assert extract_day0_predictions_from_hourly({"hourly": None}, MODELS) == []


def test_extract_day0_per_model_fields_and_onset():
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00"],
            "precipitation_gfs_seamless": [0.0, 0.8, 0.0],
            "windgusts_10m_gfs_seamless": [10.0, 25.0, 15.0],
            "temperature_2m_gfs_seamless": [18.0, 20.0, 26.0],
            "pressure_msl_gfs_seamless": [1012.0, 1010.0, 1008.0],
            "precipitation_ecmwf_ifs025": [0.0, 0.0, 0.0],
            "windgusts_10m_ecmwf_ifs025": [8.0, 12.0, 10.0],
            "temperature_2m_ecmwf_ifs025": [17.0, 19.0, 24.0],
            "pressure_msl_ecmwf_ifs025": [1013.0, 1013.0, 1013.0],
        }
    }
    predictions = extract_day0_predictions_from_hourly(hourly, MODELS)
    by_model = {p.model: p for p in predictions}

    gfs = by_model["gfs_seamless"]
    assert gfs.rain is True
    assert gfs.onset == "06:00"
    assert gfs.wind_kmh == pytest.approx(25.0)
    assert gfs.high_c == pytest.approx(26.0)
    assert gfs.low_c == pytest.approx(18.0)
    assert gfs.mslp_trend == pytest.approx(1008.0 - 1012.0)

    ecmwf = by_model["ecmwf_ifs025"]
    assert ecmwf.rain is False
    assert ecmwf.onset is None  # no onset when no rain, even though data exists


def test_extract_day0_falls_back_to_unsuffixed_key_when_model_specific_missing():
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00"],
            "precipitation": [0.6],  # no per-model suffix at all
            "windgusts_10m": [20.0],
            "temperature_2m": [22.0],
            "pressure_msl": [1010.0],
        }
    }
    predictions = extract_day0_predictions_from_hourly(hourly, ["some_model"])
    assert predictions[0].rain is True
    assert predictions[0].wind_kmh == pytest.approx(20.0)


def test_extract_day_n_empty_input_returns_empty_list():
    assert extract_day_n_predictions_from_daily({}, 3, MODELS) == []


def test_extract_day_n_never_sets_onset():
    daily = {
        "daily": {
            "precipitation_sum_gfs_seamless": [0, 0, 0, 5.0],
            "windgusts_10m_max_gfs_seamless": [10, 10, 10, 30.0],
            "temperature_2m_max_gfs_seamless": [25, 25, 25, 27.5],
            "temperature_2m_min_gfs_seamless": [17, 17, 17, 19.0],
            "pressure_msl_mean_gfs_seamless": [1012, 1011, 1010, 1005.0],
        }
    }
    predictions = extract_day_n_predictions_from_daily(daily, 3, ["gfs_seamless"])
    p = predictions[0]
    assert p.onset is None
    assert p.rain is True
    assert p.high_c == pytest.approx(27.5)
    assert p.low_c == pytest.approx(19.0)
    assert p.wind_kmh == pytest.approx(30.0)
    assert p.mslp_trend == pytest.approx(1005.0 - 1010.0)  # index 3 minus index 2


def test_extract_day_n_mslp_trend_none_at_index_zero():
    daily = {
        "daily": {
            "pressure_msl_mean_gfs_seamless": [1012.0, 1010.0],
            "precipitation_sum_gfs_seamless": [0.0, 0.0],
        }
    }
    predictions = extract_day_n_predictions_from_daily(daily, 0, ["gfs_seamless"])
    assert predictions[0].mslp_trend is None  # no "previous day" to diff against


def test_extract_day_n_falls_back_to_unsuffixed_key():
    daily = {
        "daily": {
            "precipitation_sum": [0.0, 0.0, 0.0, 1.0],
        }
    }
    predictions = extract_day_n_predictions_from_daily(daily, 3, ["some_model"])
    assert predictions[0].rain is True
