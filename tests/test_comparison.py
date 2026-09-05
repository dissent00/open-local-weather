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


def test_two_quiet_days_say_nothing_about_rain_at_all():
    """Changed 2026-09-05, on the operator's call, and the direction matters.

    Two dry days running is the commonest case here, and it was producing an
    Overview clause every single day about weather that had not changed. Not
    "dry again" either: silence. The Overview's job is what a reader is
    walking into, and "the rain did not do anything, same as yesterday" is
    not news, it is the absence of news wearing a sentence.

    Rain then re-enters the Overview only when there IS something — a band
    change, thunder, a wet day — and "again" is freed for whatever is
    genuinely recurring, which on a pair of dry days is usually the
    instability rather than the rain.
    """
    result = compute_day_over_day(actual(thunder=False), preds())
    assert result.rain_contrast is None


def test_a_day_never_asked_about_thunder_is_not_a_day_that_thundered():
    """thunder=None is "never observed", not False. It must not be read as a
    reason to keep the rain clause — the three-valued field decides whether
    the record KNOWS, and an unknown is not an event."""
    result = compute_day_over_day(actual(thunder=None), preds())
    assert result.rain_contrast is None
    assert result.yesterday_thunder is None


def test_gap_when_yesterday_unobserved():
    assert compute_day_over_day(None, preds()) is None


# ---------------------------------------------------------------------------
# Item 53.1a — a day the reanalysis scored 0.0 mm and the airport rained on.
# ---------------------------------------------------------------------------


def test_station_rain_the_reanalysis_missed_is_not_described_as_dry():
    """2026-08-29, the miss that opened item 53.

    Reanalysis 0.0 mm and therefore no onset, no thunder heard, and the
    airport reporting -RA at 19:00 local. Scored as a wet day by
    observed_convection since 53.1, and still handed to the reader as "dry"
    until the description learned to take the station's onset.
    """
    yesterday = actual(
        rain=False, precip_mm=0.0, onset_hour=None,
        thunder=False, precipitation=True, precipitation_onset="19:00",
    )
    comparison = compute_day_over_day(yesterday, preds())

    # NOT in the Overview any more, and not as "dry again" either — see
    # compute_day_over_day. "dry again" would tell someone who stood in that
    # 19:00 shower that yesterday was dry, which is the false claim item
    # 53.1a was raised to stop. Silence makes no claim, and the shower stays
    # where a reader looks it up: the verification notes and the detailed
    # discussion, both of which read the same DailyActual.
    assert comparison.rain_contrast is None
    assert comparison.yesterday_rain is False
    assert describe_day_rain(
        yesterday.precip_mm, yesterday.observed_onset(), yesterday.thunder
    ) == "dry apart from a brief evening shower", "the description itself is unchanged"


def test_the_reanalysis_onset_still_wins_when_it_has_one():
    # The station is a fallback for a missing onset, never an override. A day
    # the reanalysis resolved is described from the reanalysis.
    day = actual(
        rain=True, precip_mm=8.0, onset_hour="13:00",
        thunder=False, precipitation=True, precipitation_onset="19:00",
    )
    assert day.observed_onset() == "13:00"


def test_station_onset_fills_in_only_when_the_reanalysis_had_none():
    assert actual(onset_hour=None, precipitation_onset="19:00").observed_onset() == "19:00"
    assert actual(onset_hour=None, precipitation_onset=None).observed_onset() is None
