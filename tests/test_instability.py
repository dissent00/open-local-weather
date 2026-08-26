"""Hand-computed expectations for the convective-instability flag.

The Overview is a one- or two-sentence slot that opens with the day-over-day
comparison, so nothing else ever reached it. On 2026-08-26 afternoon CAPE
peaked between 1100 and 2600 J/kg across models and the Overview said
"similar warmth, calmer winds, and dry again". Whether that belongs in the
Overview is a threshold decision, and thresholds are code's job here.
"""

from openlocalweather.instability import (
    CONVECTIVE_CAPE_THRESHOLD_JKG,
    summarize_instability,
)

MODELS = ["gfs_seamless", "icon_seamless", "ukmo_seamless"]


def hourly(**series) -> dict:
    times = ["2026-08-26T12:00", "2026-08-26T15:00", "2026-08-26T18:00"]
    return {"hourly": {"time": times, **series}}


def test_no_data_returns_none():
    assert summarize_instability({}, MODELS) is None
    assert summarize_instability({"hourly": {}}, MODELS) is None


def test_no_cape_series_returns_none():
    # Absent CAPE is a gap, not a quiet day.
    assert summarize_instability(hourly(), MODELS) is None


def test_all_null_cape_series_returns_none():
    # The pick_series hazard: a correctly-named array full of nulls.
    assert summarize_instability(
        hourly(cape_gfs_seamless=[None, None, None]), MODELS
    ) is None


def test_quiet_day_is_not_convective():
    result = summarize_instability(
        hourly(
            cape_gfs_seamless=[10.0, 120.0, 80.0],
            cape_icon_seamless=[20.0, 200.0, 150.0],
        ),
        MODELS,
    )
    assert result.convective is False
    assert result.peak_cape_jkg == 200.0
    assert result.peak_model == "icon_seamless"
    assert result.models_above_threshold == []


def test_the_2026_08_26_case():
    # UKMO and ICON above 2000, GFS almost nothing — the disagreement is the
    # story, and the Overview said nothing at all.
    result = summarize_instability(
        hourly(
            cape_gfs_seamless=[50.0, 300.0, 180.0],
            cape_icon_seamless=[100.0, 2400.0, 1900.0],
            cape_ukmo_seamless=[90.0, 2600.0, 2100.0],
        ),
        MODELS,
    )
    assert result.convective is True
    assert result.peak_cape_jkg == 2600.0
    assert result.peak_model == "ukmo_seamless"
    assert result.peak_hour == "15:00"
    assert result.models_above_threshold == ["icon_seamless", "ukmo_seamless"]
    assert result.peak_cape_by_model == {
        "gfs_seamless": 300.0,
        "icon_seamless": 2400.0,
        "ukmo_seamless": 2600.0,
    }


def test_one_model_alone_above_threshold_still_counts():
    # Averaging the disagreement into silence is the one thing not to do.
    result = summarize_instability(
        hourly(
            cape_gfs_seamless=[10.0, 20.0, 30.0],
            cape_icon_seamless=[10.0, 1500.0, 40.0],
        ),
        MODELS,
    )
    assert result.convective is True
    assert result.models_above_threshold == ["icon_seamless"]


def test_threshold_is_inclusive_at_the_boundary():
    at = summarize_instability(
        hourly(cape_gfs_seamless=[0.0, CONVECTIVE_CAPE_THRESHOLD_JKG, 0.0]), MODELS
    )
    assert at.convective is True

    below = summarize_instability(
        hourly(cape_gfs_seamless=[0.0, CONVECTIVE_CAPE_THRESHOLD_JKG - 1, 0.0]), MODELS
    )
    assert below.convective is False


def test_nulls_within_a_series_are_skipped_not_treated_as_zero():
    result = summarize_instability(
        hourly(cape_gfs_seamless=[None, 1400.0, None]), MODELS
    )
    assert result.peak_cape_jkg == 1400.0
    assert result.peak_hour == "15:00"


def test_falls_back_to_the_unsuffixed_series_for_a_single_model_fetch():
    result = summarize_instability(hourly(cape=[0.0, 1200.0, 0.0]), ["gfs_seamless"])
    assert result.peak_cape_jkg == 1200.0
