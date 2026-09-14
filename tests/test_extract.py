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


def test_rain_means_the_same_thing_at_every_lead():
    """ROADMAP item 97, found by the prompt harness 2026-09-09.

    Day+0 set `rain` from whether ANY HOUR crossed the threshold; Day+3 and
    Day+7 set it from the DAILY TOTAL crossing the same threshold. So the same
    2.0 mm day was false at one lead and true at another — and that boolean is
    what every Brier score, rain percentage and ranking finding is built on.

    The daily-total rule wins because it is the only one that CAN apply at
    every lead: the extended forecast comes from a daily endpoint and has no
    hours to take a peak over. It is also what the prompt already tells the
    forecaster — "whether measurable rain falls at the location during the
    day".
    """
    hourly = {"hourly": {
        "time": [f"2026-09-09T{h:02d}:00" for h in range(24)],
        # 2.0 mm across the day, no single hour reaching 0.5 — the shape that
        # scored differently at different leads. Six stored predictions had it.
        "precipitation_m": [0.0] * 10 + [0.4] * 5 + [0.0] * 9,
        "windgusts_10m_m": [20.0] * 24, "temperature_2m_m": [25.0] * 24,
        "pressure_msl_m": [1013.0] * 24, "cloud_cover_m": [50.0] * 24,
        "precipitation_probability_m": [80] * 24,
    }}
    daily = {"daily": {"time": ["2026-09-09"], "precipitation_sum_m": [2.0],
        "windgusts_10m_max_m": [20.0], "temperature_2m_max_m": [25.0],
        "temperature_2m_min_m": [18.0], "pressure_msl_mean_m": [1013.0],
        "precipitation_probability_max_m": [80]}}

    day0 = extract_day0_predictions_from_hourly(hourly, ["m"])[0]
    day_n = extract_day_n_predictions_from_daily(daily, 0, ["m"])[0]

    assert day0.precip_mm == day_n.precip_mm == 2.0
    assert day0.rain is True
    assert day0.rain == day_n.rain, "the same day scores differently at two leads"


def test_a_day_that_rained_without_a_heavy_hour_has_no_onset():
    """Onset is unchanged and still asks when an HOUR crossed the threshold.
    A day whose rain never concentrated has no onset to report, and None is
    the honest answer rather than the first damp hour."""
    hourly = {"hourly": {
        "time": [f"2026-09-09T{h:02d}:00" for h in range(24)],
        "precipitation_m": [0.0] * 10 + [0.4] * 5 + [0.0] * 9,
        "windgusts_10m_m": [20.0] * 24, "temperature_2m_m": [25.0] * 24,
        "pressure_msl_m": [1013.0] * 24, "cloud_cover_m": [50.0] * 24,
        "precipitation_probability_m": [80] * 24,
    }}
    p = extract_day0_predictions_from_hourly(hourly, ["m"])[0]
    assert p.rain is True and p.onset is None


def test_day0_stores_each_model_s_peak_cape():
    """ROADMAP items 35 and 87. `cape` has been fetched in
    HOURLY_FORECAST_VARS from the beginning and reaches the prompt every run,
    where `summarize_instability` shows the forecaster the full per-model
    spread. NOTHING EVER STORED IT, so the peak was recomputed each run and
    thrown away, and no model could be checked against whether a storm
    actually arrived.

    The peak, not a mean, matching summarize_instability — an afternoon that
    touches 2000 J/kg for one hour is convective, and averaging it against a
    calm morning hides exactly the hour that matters.

    Whole-day here rather than the forward window that summarize_instability
    trims to. Every other Day+0 field is taken over the whole day, and the
    record has to be comparable between a morning run and an evening one.
    """
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00"],
            "precipitation_gfs_seamless": [0.0, 0.0, 0.0],
            "cape_gfs_seamless": [50.0, 180.0, 1450.0],
            "precipitation_ecmwf_ifs025": [0.0, 0.0, 0.0],
            "cape_ecmwf_ifs025": [20.0, 40.0, 310.0],
        }
    }
    by_model = {p.model: p for p in extract_day0_predictions_from_hourly(hourly, MODELS)}

    assert by_model["gfs_seamless"].peak_cape_jkg == pytest.approx(1450.0)
    assert by_model["ecmwf_ifs025"].peak_cape_jkg == pytest.approx(310.0)


def test_a_model_with_no_cape_series_stores_none_not_zero():
    """Zero CAPE is a confident claim that the atmosphere is stable. A
    missing series is no claim at all, and scoring it as stable would credit
    a model for a call it never made — the same rule `rain` already follows.
    """
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T12:00"],
            "precipitation_gfs_seamless": [0.0, 0.0],
            "cape_gfs_seamless": [None, None],
            "precipitation_ecmwf_ifs025": [0.0, 0.0],
        }
    }
    by_model = {p.model: p for p in extract_day0_predictions_from_hourly(hourly, MODELS)}

    assert by_model["gfs_seamless"].peak_cape_jkg is None, "an all-null series is not 0 J/kg"
    assert by_model["ecmwf_ifs025"].peak_cape_jkg is None, "an absent series is not 0 J/kg"


def test_day0_stores_the_bearing_at_each_model_s_own_peak_gust():
    """ROADMAP item 59. `wind_kmh` is each model's OWN day-maximum gust, so
    its bearing has to be the one at that model's own peak hour. Pairing a
    speed from one hour with a bearing from another describes a wind that
    never blew.

    That choice costs agreement and is still right: measured 2026-09-10,
    sampling every model at its own peak gives a median cross-model agreement
    of 0.56 against 0.83 at a common hour — because the models put the peak
    at different hours. The gate is what handles that, not a quietly
    mismatched pairing.
    """
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00"],
            "precipitation_gfs_seamless": [0.0, 0.0, 0.0],
            "windgusts_10m_gfs_seamless": [10.0, 31.0, 15.0],
            "wind_direction_10m_gfs_seamless": [10.0, 225.0, 300.0],
            "precipitation_ecmwf_ifs025": [0.0, 0.0, 0.0],
            "windgusts_10m_ecmwf_ifs025": [8.0, 12.0, 40.0],
            "wind_direction_10m_ecmwf_ifs025": [20.0, 90.0, 230.0],
        }
    }
    by_model = {p.model: p for p in extract_day0_predictions_from_hourly(hourly, MODELS)}

    # GFS peaks at 06:00, so its bearing is 06:00's — not 00:00's or 12:00's.
    assert by_model["gfs_seamless"].wind_kmh == pytest.approx(31.0)
    assert by_model["gfs_seamless"].wind_direction_deg == pytest.approx(225.0)

    # ECMWF peaks at a different hour, and takes the bearing from ITS peak.
    assert by_model["ecmwf_ifs025"].wind_kmh == pytest.approx(40.0)
    assert by_model["ecmwf_ifs025"].wind_direction_deg == pytest.approx(230.0)


def test_a_missing_bearing_is_none_not_north():
    """0 degrees is due north, a real and confident bearing. An absent series
    is no bearing at all, and the two must never be confused — the same rule
    cloud, CAPE and rain already follow."""
    hourly = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T12:00"],
            "precipitation_gfs_seamless": [0.0, 0.0],
            "windgusts_10m_gfs_seamless": [10.0, 20.0],
            "wind_direction_10m_gfs_seamless": [None, None],
            "precipitation_ecmwf_ifs025": [0.0, 0.0],
            "windgusts_10m_ecmwf_ifs025": [8.0, 12.0],
        }
    }
    by_model = {p.model: p for p in extract_day0_predictions_from_hourly(hourly, MODELS)}
    assert by_model["gfs_seamless"].wind_direction_deg is None, "all-null is not north"
    assert by_model["ecmwf_ifs025"].wind_direction_deg is None, "absent is not north"


# --- ROADMAP item 104, contract item 2: the issuance window -----------------


def _two_days_hourly(models):
    """48 hours from 2026-08-11T00:00, one model per name, rain only at 20:00
    on the FIRST day and 02:00 on the second.

    Built so the two candidate windows disagree: a calendar day sees the 20:00
    rain and not the 02:00, a +24 h window from 12:00 sees both.
    """
    times, precip, temp = [], [], []
    for day, d in ((11, 0), (12, 24)):
        for h in range(24):
            times.append(f"2026-08-{day:02d}T{h:02d}:00")
            precip.append(0.9 if (day, h) in ((11, 20), (12, 2)) else 0.0)
            # A clean ramp so high/low are unambiguous per window.
            temp.append(10.0 + d + h)
    hourly = {"time": times}
    for m in models:
        hourly[f"precipitation_{m}"] = list(precip)
        hourly[f"temperature_2m_{m}"] = list(temp)
        hourly[f"windgusts_10m_{m}"] = [5.0] * 48
        hourly[f"pressure_msl_{m}"] = [1010.0] * 48
    return {"hourly": hourly}


def test_the_window_is_the_next_24_hours_from_the_issuance():
    """Contract item 2. At 12:00 the claim covers 12:00 today to 11:00
    tomorrow — so it must see the 02:00 rain the calendar day cannot, and the
    overnight low that a calendar day would attribute to the wrong date."""
    from datetime import datetime
    from openlocalweather.extract import extract_window_predictions

    got = extract_window_predictions(
        _two_days_hourly(MODELS), MODELS, issued_local=datetime(2026, 8, 11, 12, 30)
    )
    by_model = {p.model: p for p in got}
    gfs = by_model["gfs_seamless"]

    assert gfs.rain is True
    # 20:00 on day one is inside the window and is the FIRST wet hour in it.
    assert gfs.onset == "20:00"
    # The window runs 12:00 (temp 22) to 11:00 next day (temp 45).
    assert gfs.low_c == pytest.approx(22.0)
    assert gfs.high_c == pytest.approx(45.0)


def test_a_window_that_cannot_be_filled_is_not_a_claim():
    """The guard that makes this safe. `forward_hours`'s own docstring records
    the hazard: scoring a partial window against a full one quietly rewards a
    model for hours it was never asked about. A run holding only today's
    hours at 18:00 has six, not twenty-four, and must decline rather than
    publish a short window dressed as a full one."""
    from datetime import datetime
    from openlocalweather.extract import extract_window_predictions

    one_day = {"hourly": {k: v[:24] for k, v in _two_days_hourly(MODELS)["hourly"].items()}}

    assert extract_window_predictions(
        one_day, MODELS, issued_local=datetime(2026, 8, 11, 18, 0)
    ) == []


def test_the_window_uses_the_same_arithmetic_as_day_zero():
    """Not a second implementation. Handed the SAME hours, the two must agree
    field for field — the window differs only in which hours go in, and that
    is the whole of the reframe."""
    from datetime import datetime
    from openlocalweather.extract import extract_window_predictions

    midnight = {"hourly": {k: v[:24] for k, v in _two_days_hourly(MODELS)["hourly"].items()}}
    # A window opening at 00:00 over a 24-hour series IS the calendar day.
    window = extract_window_predictions(
        midnight, MODELS, issued_local=datetime(2026, 8, 11, 0, 0)
    )
    day0 = extract_day0_predictions_from_hourly(midnight, MODELS)

    assert [p.model_dump() for p in window] == [p.model_dump() for p in day0]


def test_the_forecast_window_and_the_observed_window_cover_the_same_hours():
    """THE PROPERTY THE WHOLE SCORE RESTS ON, and it spans two modules.

    `extract_window_predictions` slices the forecast with
    `daypart.forward_hours`; `open_meteo.bucket_hourly_window` slices the
    observation itself. If those two disagree by even one hour — a different
    flooring rule, a half-open interval on one side and a closed one on the
    other — then a 24-hour claim is scored against 23 or 25 hours of weather
    and every figure derived from it is quietly wrong.

    Proved by extremes rather than by reading either function's internals: the
    series gives every hour a distinct temperature, so identical high and low
    on both sides can only come from an identical set of hours.
    """
    from datetime import datetime
    from openlocalweather.extract import extract_window_predictions
    from openlocalweather.fetch.open_meteo import bucket_hourly_window

    times, temp = [], []
    for i in range(48):
        times.append(f"2026-08-{11 + i // 24:02d}T{i % 24:02d}:00")
        temp.append(10.0 + i)  # unique per hour
    series = {
        "hourly": {
            "time": times,
            "temperature_2m": temp,
            "precipitation": [0.0] * 48,
            "cloud_cover": [50.0] * 48,
            "wind_gusts_10m": [20.0] * 48,
            "pressure_msl": [1010.0] * 48,
        }
    }

    # An issuance at 06:50 — deliberately not on the hour, which is where a
    # flooring disagreement would show.
    issued = datetime(2026, 8, 11, 6, 50)

    forecast = extract_window_predictions(series, ["gfs_seamless"], issued_local=issued)[0]
    observed = bucket_hourly_window(series, start=issued, hours=24)

    assert observed is not None
    assert forecast.high_c == observed.high_c, "the two sides picked different hours"
    assert forecast.low_c == observed.low_c, "the two sides picked different hours"
