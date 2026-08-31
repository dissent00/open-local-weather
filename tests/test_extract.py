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


# ---------------------------------------------------------------------------
# Missing-data must never become a confident "no rain" (regression)
# ---------------------------------------------------------------------------


def test_day_n_missing_precip_yields_rain_none_not_false():
    """UKMO's horizon stops around 7.2 days, so it has no Day+7 value at
    all. Recording that as rain=False would manufacture a confident dry
    forecast from a gap and accrue fake accuracy — dry days outnumber wet
    ones, so it would score well for no reason."""
    daily = {
        "daily": {
            # 8 slots, but nothing at index 7 — exactly what UKMO returns.
            "precipitation_sum_ukmo_seamless": [0.0] * 7 + [None],
            "temperature_2m_max_ukmo_seamless": [27.0] * 7 + [None],
        }
    }
    day7 = extract_day_n_predictions_from_daily(daily, 7, ["ukmo_seamless"])[0]
    assert day7.rain is None, "missing data must be None, never False"

    day6 = extract_day_n_predictions_from_daily(daily, 6, ["ukmo_seamless"])[0]
    assert day6.rain is False, "a real 0.0mm reading IS a genuine no-rain call"


def test_day_n_index_beyond_array_yields_rain_none():
    daily = {"daily": {"precipitation_sum_gfs_seamless": [0.0, 0.0]}}
    p = extract_day_n_predictions_from_daily(daily, 7, ["gfs_seamless"])[0]
    assert p.rain is None


def test_day0_all_null_precip_series_yields_rain_none():
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00"],
            "precipitation_ukmo_seamless": [None, None],
            "temperature_2m_ukmo_seamless": [18.0, 22.0],
        }
    }
    p = extract_day0_predictions_from_hourly(hourly, ["ukmo_seamless"])[0]
    assert p.rain is None


def test_day0_real_dry_series_still_yields_rain_false():
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00"],
            "precipitation_gfs_seamless": [0.0, 0.0],
            "temperature_2m_gfs_seamless": [18.0, 22.0],
        }
    }
    p = extract_day0_predictions_from_hourly(hourly, ["gfs_seamless"])[0]
    assert p.rain is False


def test_all_null_series_falls_through_to_the_alternate_key():
    """The silent-failure shape that cost this deployment every Day+0 ECMWF
    wind score.

    Open-Meteo returns a correctly-NAMED array full of nulls when a model
    doesn't publish a variable under a given alias. A list of Nones is
    truthy, so the previous `a or b` lookup latched onto the empty series and
    never tried the working key — no exception, no warning, just one model
    with no wind in the record for months.
    """
    hourly = {
        "hourly": {
            "time": ["2026-08-19T00:00", "2026-08-19T01:00"],
            # Legacy alias present but empty, exactly as the live API returns
            # it for ecmwf_ifs025.
            "windgusts_10m_ecmwf_ifs025": [None, None],
            "wind_gusts_10m_ecmwf_ifs025": [11.2, 18.4],
            "temperature_2m_ecmwf_ifs025": [19.0, 21.0],
            "precipitation_ecmwf_ifs025": [0.0, 0.0],
            "pressure_msl_ecmwf_ifs025": [1013.0, 1011.0],
        }
    }
    (pred,) = extract_day0_predictions_from_hourly(hourly, ["ecmwf_ifs025"])
    assert pred.wind_kmh == 18.4, "must skip the all-null alias, not fall silent"


def test_a_genuinely_absent_variable_still_reads_as_absent():
    """The complement: skipping all-null series must not invent a value when
    the model really doesn't publish one."""
    hourly = {
        "hourly": {
            "time": ["2026-08-19T00:00"],
            "windgusts_10m_ukmo_seamless": [None],
            "temperature_2m_ukmo_seamless": [19.0],
            "precipitation_ukmo_seamless": [0.0],
        }
    }
    (pred,) = extract_day0_predictions_from_hourly(hourly, ["ukmo_seamless"])
    assert pred.wind_kmh is None


# ---------------------------------------------------------------------------
# ROADMAP item 58, storage half — start recording the probability now
# ---------------------------------------------------------------------------


def test_day_n_carries_the_models_own_rain_probability():
    """Open-Meteo has been sending precipitation_probability_max on every
    daily request since before this project scored anything, and nothing read
    it. Stored from now on because a calibration check needs history and
    history only accrues forwards — see ROADMAP item 58."""
    daily = {
        "daily": {
            "time": ["2026-08-11", "2026-08-12"],
            "precipitation_sum_gfs_seamless": [0.0, 5.0],
            "precipitation_probability_max_gfs_seamless": [10, 80],
        }
    }
    preds = extract_day_n_predictions_from_daily(daily, 1, ["gfs_seamless"])
    assert preds[0].rain_probability_pct == 80


def test_a_model_with_no_probability_records_none_not_zero():
    """Zero is a confident claim of no rain. Absent is not — the same
    distinction ModelPrediction.rain already keeps, and the reason a
    boolean-only ledger could not tell them apart."""
    daily = {
        "daily": {
            "time": ["2026-08-11"],
            "precipitation_sum_gfs_seamless": [0.0],
        }
    }
    preds = extract_day_n_predictions_from_daily(daily, 0, ["gfs_seamless"])
    assert preds[0].rain_probability_pct is None


def test_day0_derives_the_probability_from_the_hourly_series():
    """The hourly endpoint has no daily maximum, so Day+0's is the highest
    hour of the day — the same quantity precipitation_probability_max serves
    at Day+3/+7, derived rather than served. Stated because a difference in
    how the two leads are computed would be invisible in the ledger."""
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00", "2026-08-11T02:00"],
            "precipitation_gfs_seamless": [0.0, 0.0, 0.0],
            "precipitation_probability_gfs_seamless": [10, 65, 30],
        }
    }
    preds = extract_day0_predictions_from_hourly(hourly, ["gfs_seamless"])
    assert preds[0].rain_probability_pct == 65


def test_day0_records_no_probability_rather_than_zero():
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00"],
            "precipitation_gfs_seamless": [0.0],
        }
    }
    preds = extract_day0_predictions_from_hourly(hourly, ["gfs_seamless"])
    assert preds[0].rain_probability_pct is None


def test_an_all_null_probability_series_is_absent_not_zero():
    """pick_series' own hazard: a series present but entirely null is no
    data, and max() over it must not become a confident 0%."""
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00"],
            "precipitation_gfs_seamless": [0.0, 0.0],
            "precipitation_probability_gfs_seamless": [None, None],
        }
    }
    preds = extract_day0_predictions_from_hourly(hourly, ["gfs_seamless"])
    assert preds[0].rain_probability_pct is None
