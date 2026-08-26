"""Hand-computed expectations for the day-over-day summary.

comparison.py was covered only by exported vectors, which pin what Python
does rather than assert it is right (see spec/README.md). These are the
missing layer: every expectation below was worked out by hand.
"""

from openlocalweather.comparison import compute_day_over_day, describe_day_rain
from openlocalweather.models import DailyActual, ModelPrediction


def actual(**overrides) -> DailyActual:
    defaults = dict(rain=False, high_c=31.5, low_c=18.9, peak_wind_kmh=33.1,
                    mslp_trend=-2.3, onset_hour=None, precip_mm=0.5)
    defaults.update(overrides)
    return DailyActual(**defaults)


def preds(**overrides) -> list[ModelPrediction]:
    defaults = dict(rain=False, onset=None, precip_mm=0.0, wind_kmh=33.0,
                    high_c=31.5, low_c=18.9)
    defaults.update(overrides)
    return [ModelPrediction(model=f"m{i}", **defaults) for i in range(4)]


# ---------------------------------------------------------------------------
# describe_day_rain — amount, timing, and now thunder
# ---------------------------------------------------------------------------


def test_no_amount_reads_as_a_gap():
    assert describe_day_rain(None, None) is None


def test_plain_dry_day():
    assert describe_day_rain(0.0, None) == "dry"


def test_thunder_never_reads_as_dry():
    # 2026-08-24: 0.5 mm in the reanalysis, TS at the airport for an hour.
    # The forecast told readers the following morning it had been "dry again".
    assert describe_day_rain(0.5, None, thunder=True) == "dry but thundery"


def test_thunder_false_still_reads_as_dry():
    assert describe_day_rain(0.5, None, thunder=False) == "dry"


def test_thunder_none_still_reads_as_dry():
    # No observation available must behave exactly as before thunder existed.
    assert describe_day_rain(0.5, None, thunder=None) == "dry"


def test_evening_thunder_on_a_showery_day():
    assert describe_day_rain(8.0, "17:00", thunder=True) == "dry until evening thunderstorms"


def test_afternoon_thunder_names_the_band():
    assert describe_day_rain(8.0, "13:00", thunder=True) == "showery with afternoon thunderstorms"


def test_morning_thunder_names_the_band():
    assert describe_day_rain(20.0, "07:00", thunder=True) == "wet with thunderstorms"


def test_dry_band_no_longer_swallows_a_timed_shower():
    # The band edge used to be a cliff: 0.9 mm at 17:00 read "dry" while
    # 1.1 mm at 17:00 read "dry until evening showers". A fifth of a
    # millimetre changed the whole description of the day.
    assert describe_day_rain(0.9, "17:00") == "dry apart from a brief evening shower"
    assert describe_day_rain(1.1, "17:00") == "dry until evening showers"


def test_dry_band_timed_shower_afternoon_and_morning():
    assert describe_day_rain(0.9, "13:00") == "dry apart from a brief afternoon shower"
    assert describe_day_rain(0.9, "07:00") == "dry apart from an early shower"


def test_existing_bands_unchanged():
    assert describe_day_rain(3.0, None) == "largely dry"
    assert describe_day_rain(3.0, "17:00") == "dry until evening showers"
    assert describe_day_rain(8.0, "17:00") == "dry until evening showers"
    assert describe_day_rain(20.0, "17:00") == "dry until heavy evening rain"
    assert describe_day_rain(8.0, "13:00") == "showery from the afternoon"
    assert describe_day_rain(20.0, "07:00") == "wet"


# ---------------------------------------------------------------------------
# compute_day_over_day — the sentence the reader actually gets
# ---------------------------------------------------------------------------


def test_the_2026_08_24_regression():
    # Yesterday thundered; today is genuinely dry. The old code said
    # "dry again" because both sides landed in the sub-1 mm band.
    result = compute_day_over_day(actual(thunder=True), preds())
    assert result.rain_contrast == "dry today; yesterday was dry but thundery"
    assert result.yesterday_thunder is True


def test_two_quiet_days_still_collapse_to_again():
    result = compute_day_over_day(actual(thunder=False), preds())
    assert result.rain_contrast == "dry again"


def test_no_thunder_observation_behaves_as_before():
    result = compute_day_over_day(actual(thunder=None), preds())
    assert result.rain_contrast == "dry again"
    assert result.yesterday_thunder is None


def test_gap_when_yesterday_unobserved():
    assert compute_day_over_day(None, preds()) is None
