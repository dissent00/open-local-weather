"""Hand-computed expectations for the day-over-day summary.

comparison.py was covered only by exported vectors, which pin what Python
does rather than assert it is right (see spec/README.md). These are the
missing layer: every expectation below was worked out by hand.
"""

from openlocalweather.comparison import (
    compute_day_over_day,
    describe_day_over_day,
    describe_day_rain,
    describe_extended_trend,
)
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
    #
    # Reworded by item 83's one-statement rule, from "dry today; yesterday
    # was dry but thundery". What the regression is ABOUT is unchanged and
    # is what this asserts: the storm the reader stood in is still on the
    # page the next morning.
    result = compute_day_over_day(actual(thunder=True), preds())
    assert result.rain_contrast == "dry, after a thundery day"
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


# ---------------------------------------------------------------------------
# Item 83 defect 5 — the block contradicting its own prose.
# ---------------------------------------------------------------------------


def real_2026_09_08_day0() -> list[ModelPrediction]:
    """The archived Day+0 payload that produced the contradiction, verbatim.

    Copied from data/prompts/2026-09-08.json rather than invented, because
    the shape is the whole point: ONE model of six carries the onset.
    """
    rows = [
        ("gfs_seamless", False, None, 0.4),
        ("ecmwf_ifs025", True, "16:00", 8.7),
        ("icon_seamless", False, None, 0.7),
        ("ukmo_seamless", False, None, 0.3),
        ("best_match", False, None, 0.5),
        ("kenya_met", True, None, None),
    ]
    return [
        ModelPrediction(model=m, rain=r, onset=o, precip_mm=p,
                        wind_kmh=33.0, high_c=31.5, low_c=18.9)
        for m, r, o, p in rows
    ]


def test_one_model_of_six_does_not_get_to_name_the_day():
    """A minority onset must not be spoken as the day's shape.

    Measured 2026-09-08. Four of the five models carrying an amount forecast
    0.3-0.7 mm; ecmwf alone forecast 8.7 mm from 16:00. The mean it dragged
    to 2.12 mm banded as "largely dry", and _consensus_onset took the median
    of a one-member list, so the phrase read "dry until evening showers
    today" while today_rain_expected — a straight majority vote — read False.

    The payload contradicted itself and the worker model resolved toward the
    prose, saying so in as many words. That is the forecaster quietly
    choosing, which is the judgement this whole design exists to remove.
    """
    result = compute_day_over_day(actual(precip_mm=2.0), real_2026_09_08_day0())

    assert result.today_rain_expected is False
    assert "evening showers" not in result.rain_contrast


# ---------------------------------------------------------------------------
# Item 83 defect 4 — the top band had no ceiling.
# ---------------------------------------------------------------------------


def test_a_frontal_passage_does_not_read_like_a_mild_afternoon():
    """6 degrees and 25 degrees were the same three words.

    TEMP_CHANGE_BANDS_C topped out at "much", so every change from 6 C
    upward produced identical wording. Raised by the operator 2026-09-08:
    "Some places will see temps swing 20-30 degrees in a day as a front
    passes. Kisumu is not the best place for this." The deployment hid the
    defect — it has never once fired here — which is exactly why it survived.
    """
    mild = compute_day_over_day(actual(high_c=31.5), preds(high_c=25.0))
    front = compute_day_over_day(actual(high_c=31.5), preds(high_c=6.5))

    assert mild.high_delta_c == -6.5
    assert front.high_delta_c == -25.0
    assert mild.high_label != front.high_label


def test_a_gale_does_not_read_like_a_freshening_breeze():
    """wind_label had the same missing ceiling, one field over.

    Above WIND_CHANGE_THRESHOLD_KMH everything was "windier" or "calmer",
    so the archived -13.5 and -11.0 km/h changes and a 40 km/h collapse were
    all one word. Not named in item 83; it is the same defect and shipped in
    the same pass rather than paying the two-language port twice.
    """
    ordinary = compute_day_over_day(actual(peak_wind_kmh=49.3), preds(wind_kmh=35.8))
    collapse = compute_day_over_day(actual(peak_wind_kmh=49.3), preds(wind_kmh=9.0))

    assert ordinary.wind_delta_kmh == -13.5
    assert collapse.wind_delta_kmh == -40.3
    assert ordinary.wind_label == "calmer"
    assert collapse.wind_label != ordinary.wind_label


# ---------------------------------------------------------------------------
# Item 83 defects 1-3 — the composition contract.
# ---------------------------------------------------------------------------


def test_a_sentence_opener_is_never_welded_after_a_preposition():
    """Defect 1, the one with no legal move.

    describe_day_rain returns "dry until evening showers" — correct as
    "Dry until evening showers." and broken after "with". The prompt told the
    model to open with the comparison AND to use the phrase verbatim, so it
    produced "with dry until evening showers today". Code now does the
    placing, because code is what knows the phrase's shape.
    """
    text = describe_day_over_day("slightly warmer", "calmer", "dry until evening showers")

    assert "with dry" not in text
    assert text == "Slightly warmer and calmer than yesterday. Dry until evening showers."


def test_the_comparison_names_what_it_is_measured_against():
    """Defect 2. The labels measure today against YESTERDAY; the extended
    trend measures the next three days against TODAY. Welded, they read as a
    contradiction — warmer, and also much the same. The baseline is now said
    out loud, once, in the sentence that owns it."""
    assert describe_day_over_day("slightly warmer", "calmer", None) == (
        "Slightly warmer and calmer than yesterday."
    )


def test_nothing_moved_is_one_short_sentence():
    """The anti-enumeration rule, now structural rather than instructed.
    "much like yesterday, with similar warmth, similar winds, and dry again"
    is four statements of one fact, and was a real Overview."""
    assert describe_day_over_day("about the same", "similar winds", None) == (
        "Much like yesterday."
    )


def test_rain_alone_changing_does_not_get_called_much_like_yesterday():
    """The operator's complaint, in miniature: three quiet vectors and one
    that moved is not "much the same". When temperature and wind are the
    non-news, they are dropped and the rain leads."""
    assert describe_day_over_day("about the same", "similar winds", "wet, after a dry day") == (
        "Wet, after a dry day."
    )


def test_one_label_moving_drops_the_other_rather_than_listing_it():
    assert describe_day_over_day("noticeably cooler", "similar winds", None) == (
        "Noticeably cooler than yesterday."
    )
    assert describe_day_over_day("about the same", "much windier", None) == (
        "Much windier than yesterday."
    )


def test_no_comparison_at_all_is_a_gap_not_a_sentence():
    assert describe_day_over_day(None, None, None) is None


def test_yesterday_is_said_once():
    text = describe_day_over_day("slightly warmer", "calmer", "wet, after a dry day")
    assert text.count("yesterday") == 1


def test_the_tail_is_gone_and_yesterday_keeps_its_thunder():
    """Defect 3. "X today; yesterday was Y" was two statements in a slot that
    allows one, and it spent the reader's first sentence on weather that had
    already happened.

    Yesterday now contributes one word. THUNDER OUTRANKS THE BAND, the same
    rule describe_day_rain already uses and for the same measured reason —
    2026-08-24 thundered and was reported the next morning as "dry again".
    """
    changed = compute_day_over_day(
        actual(precip_mm=20.0, thunder=False), preds(rain=True, precip_mm=0.1)
    )
    assert changed.rain_contrast == "dry, after a wet day"

    thundery = compute_day_over_day(
        actual(precip_mm=20.0, thunder=True), preds(rain=True, precip_mm=0.1)
    )
    assert thundery.rain_contrast == "dry, after a thundery day"


def test_much_like_yesterday_is_not_claimed_on_a_missing_measurement():
    """It is a claim about THREE measurements. A null wind label is missing
    data, not a quiet wind, so the claim is not available — found reading the
    diff rather than by a test, and the same class of defect item 83 is
    about: a phrase asserting a baseline it does not have."""
    assert describe_day_over_day("about the same", None, None) is None
    assert describe_day_over_day(None, "similar winds", None) is None


# ---------------------------------------------------------------------------
# The comparison must be symmetric, and must test what it reports.
# ---------------------------------------------------------------------------


def test_today_can_be_thundery_too(preds_thunder=None):
    """Raised by the operator 2026-09-09 from a live Overview: "Largely dry,
    after a thundery day" followed by "Thunderstorms are possible this
    evening" — a contrast against yesterday's storms, then an admission that
    today has them too.

    Today's side passed thunder=None ALWAYS, because today has no thunder
    OBSERVATION. But it has a forecast — the convective flag, already
    pre-computed and already in the payload. So yesterday could be thundery
    and today never could, and any thundery yesterday manufactured a change.
    """
    y = actual(precip_mm=3.0, onset_hour=None, thunder=True)
    today = preds(rain=False, precip_mm=2.0)

    same = compute_day_over_day(y, today, today_convective=True)
    assert same.rain_contrast == "largely dry with thunderstorms again"

    # A genuine change still reads as one — the 2026-08-24 lesson survives.
    changed = compute_day_over_day(y, today, today_convective=False)
    assert changed.rain_contrast == "largely dry, after a thundery day"

    # And the actionable direction, which was unreachable before.
    quiet_yesterday = actual(precip_mm=3.0, onset_hour=None, thunder=False)
    news = compute_day_over_day(quiet_yesterday, today, today_convective=True)
    assert news.rain_contrast == "largely dry with thunderstorms, after a largely dry day"


def test_the_contrast_frame_needs_an_actual_contrast():
    """"Largely dry, after a largely dry day" was reachable: the
    same-or-different test compared full CHARACTER phrases while the summary
    reported the BAND, so two days in one band could be framed as a change.

    The test now runs on exactly what the summary reports."""
    y = actual(precip_mm=3.0, onset_hour="17:00", thunder=False)
    result = compute_day_over_day(y, preds(rain=False, precip_mm=2.0), today_convective=False)

    assert "after a largely dry day" not in (result.rain_contrast or "")
    assert result.rain_contrast == "largely dry again"


def test_when_nothing_moved_at_all_say_so_once():
    """The operator's actual ask, 2026-09-09: "the first two sentences should
    actually be reading that it's the same as yesterday, since there's no new
    info and thunderstorms are still likely."

    "Largely dry with thunderstorms again" says the RAIN is unchanged. It
    says nothing about temperature or wind, which were also unchanged. The
    lead now carries that — and the rain sentence then drops its "again",
    because a reader told the day is like yesterday has already been told."""
    assert describe_day_over_day(
        "about the same", "similar winds", "largely dry with thunderstorms again",
        today_character="largely dry with thunderstorms", rain_unchanged=True,
    ) == "Much like yesterday. Largely dry with thunderstorms."

    # A change still leads, and the rain half keeps its own comparison.
    assert describe_day_over_day(
        "noticeably cooler", "similar winds", "largely dry with thunderstorms again",
        today_character="largely dry with thunderstorms", rain_unchanged=True,
    ) == "Noticeably cooler than yesterday. Largely dry with thunderstorms again."

    # Rain genuinely changed: no sameness claim is available.
    assert describe_day_over_day(
        "about the same", "similar winds", "dry, after a thundery day",
        today_character="dry", rain_unchanged=False,
    ) == "Dry, after a thundery day."


# ---------------------------------------------------------------------------
# The extended clause names the scope it actually measured.
# ---------------------------------------------------------------------------


def test_the_extended_clause_says_conditions_when_it_measured_conditions():
    """Operator, 2026-09-09: "if it's temps/wind/cloudcover/precip/chance of
    thunderstorms that are all the same, let's call it conditions. If we only
    have temps to compare, fine."

    Wind is present at Day+1..3 and was being discarded — the clause used
    highs and precipitation only. With wind in, "conditions" is honest when
    all three are steady, and the narrower wording is used when it is not.
    """
    steady_highs = [30.5, 30.2, 30.4]
    steady_winds = [20.0, 21.0, 19.5]
    dry = [0.0, 0.0, 0.0]

    # All three steady: the broad noun is earned.
    assert describe_extended_trend(30.0, steady_highs, dry, "Saturday",
                                   today_wind_kmh=20.0, day_winds_kmh=steady_winds) == (
        "conditions much the same through Saturday")

    # Rain arriving: "conditions much the same" would contradict its own tail.
    assert describe_extended_trend(30.0, steady_highs, [0.0, 0.0, 6.0], "Saturday",
                                   today_wind_kmh=20.0, day_winds_kmh=steady_winds) == (
        "temperatures and winds much the same through Saturday, with rain becoming more likely")

    # No wind measured at all: say only what was compared.
    assert describe_extended_trend(30.0, steady_highs, dry, "Saturday") == (
        "temperatures much the same through Saturday")


def test_a_wind_trend_is_worth_saying_even_when_the_heat_holds():
    """Wind was discarded entirely, so a three-day build in gusts under a flat
    temperature reads as "much the same" — the operator's point that this
    must not be a temperature-only clause."""
    assert describe_extended_trend(30.0, [30.5, 30.2, 30.4], [0.0, 0.0, 0.0], "Saturday",
                                   today_wind_kmh=18.0, day_winds_kmh=[24.0, 30.0, 34.0]) == (
        "becoming windier through Saturday")

    assert describe_extended_trend(30.0, [33.0, 33.5, 34.0], [0.0, 0.0, 0.0], "Saturday",
                                   today_wind_kmh=18.0, day_winds_kmh=[24.0, 30.0, 34.0]) == (
        "warming and becoming windier through Saturday")
