"""The served call, built from code — ROADMAP item 189.

The numbers a reader is shown are the code blend's where it calls, and the
models' equal-weight consensus where the record is too thin for it to; the
LLM decides none of them. These pin the arithmetic, the labels the tiles
print, and the fallbacks, case by case, before any run-level test reads the
value back off disk.
"""

from openlocalweather.code_blend import code_blend_prediction
from openlocalweather.code_call import (
    CALL_SOURCE_CODE_BLEND,
    CALL_SOURCE_CONSENSUS,
    NoTemperatureToServe,
    RAIN_LABEL_MAX_CHARS,
    cams_peak_aqi,
    onset_window_label,
    rain_label,
    served_call,
    verification_summary,
)
from openlocalweather.defaults import CODE_BLEND_MODEL_ID
from openlocalweather.models import ModelPrediction, ModelPredictionsByLead
from openlocalweather.synoptic import SynopticSnapshot

import pytest


def pred(model, rain=None, onset=None, precip=None, high=None, low=None, wind=None, mslp=None, prob=None):
    return ModelPrediction(
        model=model, rain=rain, onset=onset, precip_mm=precip, high_c=high, low_c=low,
        wind_kmh=wind, mslp_trend=mslp, rain_probability_pct=prob,
    )


INPUTS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless"]


def _call(**overrides):
    base = dict(
        day0_models=[
            pred("gfs_seamless", rain=True, onset="14:00", precip=3.0, high=29.0, low=18.0, wind=30.0, mslp=-1.0),
            pred("ecmwf_ifs025", rain=True, onset="16:00", precip=5.0, high=30.0, low=19.0, wind=34.0, mslp=-2.0),
            pred("icon_seamless", rain=False, onset=None, precip=0.2, high=31.0, low=20.0, wind=26.0, mslp=-1.5),
        ],
        day3_models=[pred("gfs_seamless", rain=False), pred("ecmwf_ifs025", rain=True)],
        day7_models=[pred("ecmwf_ifs025", rain=True)],
        code_blend=ModelPredictionsByLead(),
        secondary_day0=[pred("gfs_seamless", wind=40.0), pred("ecmwf_ifs025", wind=44.0)],
        calibrated_gust_kmh=None,
        synoptic=None,
        air_quality=None,
        convective=False,
        thunder_when=None,
        onset_word_for=lambda hhmm: "evening" if hhmm and int(hhmm[:2]) >= 16 else "afternoon",
        issued_hour=6,
        inputs=INPUTS,
    )
    base.update(overrides)
    return served_call(**base)


# --- the code blend row carries what the reader is shown -------------------


def test_the_blend_row_carries_the_wet_voters_median_onset():
    weights = {"gfs_seamless": 30.0, "ecmwf_ifs025": 25.0, "icon_seamless": 10.0}
    predictions = [
        pred("gfs_seamless", rain=True, onset="14:00"),
        pred("ecmwf_ifs025", rain=True, onset="18:00"),
        pred("icon_seamless", rain=False, onset="09:00"),  # dry voter: its onset must not vote
    ]
    row = code_blend_prediction(predictions, weights)
    assert row.rain is True
    assert row.onset == "18:00", "the median of the WET voters' onsets, hour-floored"


def test_a_dry_blend_has_no_onset():
    weights = {"gfs_seamless": 30.0, "ecmwf_ifs025": 25.0}
    row = code_blend_prediction([pred("gfs_seamless", rain=False, onset="14:00"), pred("ecmwf_ifs025", rain=True, onset="16:00")], weights)
    assert row.rain is False
    assert row.onset is None


def test_the_blend_amount_is_the_record_weighted_mean_to_one_decimal():
    weights = {"gfs_seamless": 30.0, "ecmwf_ifs025": 10.0}
    row = code_blend_prediction([pred("gfs_seamless", rain=True, precip=4.0), pred("ecmwf_ifs025", rain=True, precip=1.0)], weights)
    # (4.0*30 + 1.0*10) / 40 = 3.25 -> 3.2 under Python's round-half-even on the decimal expansion
    assert row.precip_mm == 3.2


def test_a_voter_without_an_amount_does_not_vote_on_it():
    weights = {"gfs_seamless": 30.0, "ecmwf_ifs025": 10.0}
    row = code_blend_prediction([pred("gfs_seamless", rain=True, precip=4.0), pred("ecmwf_ifs025", rain=True)], weights)
    assert row.precip_mm == 4.0
    row = code_blend_prediction([pred("gfs_seamless", rain=True), pred("ecmwf_ifs025", rain=True)], weights)
    assert row.precip_mm is None


def test_the_served_gust_rides_on_the_blend_row():
    weights = {"gfs_seamless": 30.0}
    assert code_blend_prediction([pred("gfs_seamless", rain=True)], weights, wind_kmh=36.4).wind_kmh == 36.4
    assert code_blend_prediction([pred("gfs_seamless", rain=True)], weights).wind_kmh is None


# --- the served call: the blend where it calls ------------------------------


def test_the_served_call_is_the_blend_row_where_it_exists():
    blend = ModelPredictionsByLead(
        day0=[pred(CODE_BLEND_MODEL_ID, rain=True, onset="16:00", precip=4.1, high=29.4, low=18.6, wind=36.0, prob=64)],
        day3=[pred(CODE_BLEND_MODEL_ID, rain=False, prob=40)],
        day7=[],
    )
    call = _call(code_blend=blend)
    tp = call.judgment.today_properties

    assert call.source == CALL_SOURCE_CODE_BLEND
    assert (tp.rain, tp.rain_probability_pct, tp.onset_hour, tp.precip_mm) == (True, 64, "16:00", 4.1)
    assert (tp.temp_high_c, tp.temp_low_c, tp.peak_wind_primary_kmh) == (29.4, 18.6, 36.0)
    assert tp.peak_wind_secondary_kmh == 42.0, "the secondary point's gust is the models' mean"
    # Day+3 from the blend, Day+7 from the models' consensus (one wet model of one).
    assert [(e.lead_time_days, e.rain, e.rain_probability_pct) for e in call.judgment.extended_properties] == [
        (3, False, 40), (7, True, 100),
    ]


def test_without_a_blend_row_the_call_is_the_equal_weight_consensus():
    call = _call()
    tp = call.judgment.today_properties

    assert call.source == CALL_SOURCE_CONSENSUS
    assert tp.rain is True, "two of three models call rain"
    assert tp.rain_probability_pct == 67
    assert tp.onset_hour == "16:00", "the median of the wet models' onsets"
    assert tp.precip_mm == 2.7, "the plain mean of 3.0, 5.0 and 0.2, to one decimal"
    assert (tp.temp_high_c, tp.temp_low_c) == (30.0, 19.0)
    assert tp.peak_wind_primary_kmh == 30.0, "uncalibrated: the models' mean gust"


def test_a_consensus_tie_breaks_dry_like_the_blend():
    call = _call(day0_models=[
        pred("gfs_seamless", rain=True, high=28.0, low=18.0), pred("ecmwf_ifs025", rain=False, high=28.0, low=18.0),
    ])
    assert call.judgment.today_properties.rain is False
    assert call.judgment.today_properties.rain_probability_pct == 50


def test_the_blend_temperatures_fall_back_to_the_models_mean_when_uncorrected():
    blend = ModelPredictionsByLead(day0=[pred(CODE_BLEND_MODEL_ID, rain=False, prob=10)])  # no corrected temps
    tp = _call(code_blend=blend).judgment.today_properties
    assert (tp.temp_high_c, tp.temp_low_c) == (30.0, 19.0)


def test_the_calibrated_gust_is_the_primary_wind_when_the_record_has_one():
    assert _call(calibrated_gust_kmh=41.37).judgment.today_properties.peak_wind_primary_kmh == 41.4


def test_a_day_with_no_temperature_anywhere_cannot_be_served():
    with pytest.raises(NoTemperatureToServe):
        _call(day0_models=[pred("gfs_seamless", rain=False)])


def test_only_the_blends_inputs_vote_in_the_consensus():
    call = _call(day0_models=[
        pred("gfs_seamless", rain=False, high=28.0, low=18.0),
        pred("persistence", rain=True, high=35.0, low=10.0),
        pred("olw_blend", rain=True, high=35.0, low=10.0),
    ])
    tp = call.judgment.today_properties
    assert tp.rain is False and (tp.temp_high_c, tp.temp_low_c) == (28.0, 18.0)


# --- the display fields -----------------------------------------------------


def test_the_pressure_trend_is_the_models_mean_as_the_tile_prints_it():
    assert _call().judgment.today_properties.mslp_trend_24h == "-1.5 hPa"
    assert _call(day0_models=[pred("gfs_seamless", rain=False, high=28.0, low=18.0)]).judgment.today_properties.mslp_trend_24h is None


def test_the_synoptic_pattern_reads_the_ring():
    snapshot = SynopticSnapshot(
        centre_mslp_hpa=1012.0, lowest_label="NE", lowest_mslp_hpa=1006.0, highest_label="S",
        highest_mslp_hpa=1020.0, gradient_hpa=14.0, gradient_strength="strong",
    )
    pattern = _call(synoptic=snapshot).judgment.today_properties.synoptic_pattern
    assert pattern.startswith("Strong") and "northeast" in pattern
    assert _call(synoptic=None).judgment.today_properties.synoptic_pattern is None


def test_the_air_quality_is_the_days_peak_us_aqi():
    aq = {"hourly": {"time": ["a", "b", "c"], "us_aqi": [61, None, 84.4]}}
    assert cams_peak_aqi(aq) == 84
    assert cams_peak_aqi({"hourly": {"us_aqi": [None]}}) is None
    assert cams_peak_aqi(None) is None
    assert _call(air_quality=aq).judgment.today_properties.air_quality_aqi == 84


# --- the tile labels ----------------------------------------------------------


@pytest.mark.parametrize(
    "rain, precip, convective, when, expected",
    [
        (False, 0.0, False, None, "Dry / No Rain"),
        (False, 0.0, True, "Evening", "Dry / Evening Thunder Possible"),
        (False, 0.0, True, None, "Dry / Thunder Possible"),
        # The bands are comparison.DAY_RAIN_BANDS_MM: under 1 dry, under 5
        # largely dry, under 15 showery, 15 and over wet.
        (True, 8.0, False, "Evening", "Evening Showers"),
        (True, 8.0, True, "Afternoon", "Afternoon Showers & Thunderstorms"),
        (True, 0.6, False, "Evening", "Isolated Evening Showers"),
        (True, 4.0, True, "Evening", "Isolated Evening Showers & Thunderstorms"),
        (True, 20.0, False, "Morning", "Morning Rain"),
        (True, 8.0, False, None, "Showers Likely"),
        (True, 8.0, True, None, "Showers & Thunderstorms Likely"),
        (True, 0.6, False, None, "Isolated Showers"),
    ],
)
def test_the_rain_label_names_the_call_and_roughly_when(rain, precip, convective, when, expected):
    label = rain_label(rain=rain, precip_mm=precip, convective=convective, when=when)
    assert label == expected
    assert len(label) <= RAIN_LABEL_MAX_CHARS and not label.endswith(".")


def test_the_onset_window_is_the_wet_models_spread_of_onsets():
    assert onset_window_label(["16:00", "14:00", "19:00"], issued_hour=6) == "14:00 – 19:00"
    assert onset_window_label(["16:00", "16:00"], issued_hour=6) == "From 16:00"
    assert onset_window_label([], issued_hour=6) is None
    # An onset already behind the issuance is not a window ahead.
    assert onset_window_label(["14:00", "16:00"], issued_hour=15) == "From 16:00"
    assert onset_window_label(["14:00"], issued_hour=15) is None


def test_the_served_labels_agree_with_the_served_call():
    blend = ModelPredictionsByLead(day0=[pred(CODE_BLEND_MODEL_ID, rain=False, prob=20, high=30.0, low=19.0)])
    tp = _call(code_blend=blend, convective=True, thunder_when="this evening").judgment.today_properties
    assert tp.rain_expected == "Dry / Evening Thunder Possible"
    assert tp.onset_window is None

    tp = _call().judgment.today_properties  # consensus: wet, 2.7 mm, onsets 14:00 and 16:00
    assert tp.rain_expected == "Isolated Evening Showers", "named for the band and the served onset's word"
    assert tp.onset_window == "14:00 – 16:00"


# --- the verification summary, from the table --------------------------------


class _Score:
    def __init__(self, rain_correct):
        self.rain_correct = rain_correct


class _Lead:
    def __init__(self, lead, target, scores):
        self.lead_time_days = lead
        self.target_date_verified = target
        self.per_model_scores = scores


def test_the_verification_summary_names_who_was_right():
    from datetime import date

    text = verification_summary(
        [
            _Lead(0, date(2026, 8, 10), {"ecmwf_ifs025": _Score(True), "gfs_seamless": _Score(False), "olw_blend": _Score(True)}),
            _Lead(3, None, {}),
        ],
        visible_models=["gfs_seamless", "ecmwf_ifs025"],
    )
    assert text == "Day+0 for 2026-08-10: ecmwf_ifs025 called the rain right; gfs_seamless did not."
    assert verification_summary([_Lead(0, None, {})], visible_models=["gfs_seamless"]) == "No verification was possible this run."
