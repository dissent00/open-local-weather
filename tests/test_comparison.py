"""Hand-computed expectations for the day-over-day summary.

comparison.py was covered only by exported vectors, which pin what Python
does rather than assert it is right (see spec/README.md). These are the
missing layer: every expectation below was worked out by hand.
"""

from openlocalweather.comparison import (
    comparison_for_prompt,
    compute_day_over_day,
    describe_day_over_day,
    describe_day_rain,
    describe_extended_trend,
    wind_warning,
)
from openlocalweather.models import DailyActual, ModelPrediction
import pytest


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
    assert describe_day_rain(None, None, issued_hour=None) is None


def test_plain_dry_day():
    assert describe_day_rain(0.0, None, issued_hour=None) == "dry"


def test_thunder_never_reads_as_dry():
    # 2026-08-24: 0.5 mm in the reanalysis, TS at the airport for an hour.
    # The forecast told readers the following morning it had been "dry again".
    assert describe_day_rain(0.5, None, thunder=True, issued_hour=None) == "dry but thundery"


def test_thunder_false_still_reads_as_dry():
    assert describe_day_rain(0.5, None, thunder=False, issued_hour=None) == "dry"


def test_thunder_none_still_reads_as_dry():
    # No observation available must behave exactly as before thunder existed.
    assert describe_day_rain(0.5, None, thunder=None, issued_hour=None) == "dry"


def test_evening_thunder_on_a_showery_day():
    assert describe_day_rain(8.0, "17:00", thunder=True, issued_hour=None) == "dry until evening thunderstorms"



def test_a_timing_phrase_is_not_composed_once_its_hour_has_passed():
    """ROADMAP item 118, hand-computed.

    "dry until evening thunderstorms" asserts the hours before the evening
    were dry. Composed at 06:00 that is a forecast; composed at 18:00 it is a
    claim about a day the reader has already lived, and on 2026-09-12 it was
    published to someone who had been rained on since mid-afternoon.
    """
    # Still ahead: unchanged, which is every run this project has published.
    assert (
        describe_day_rain(8.0, "17:00", thunder=True, issued_hour=6)
        == "dry until evening thunderstorms"
    )
    # The boundary is the onset hour itself. At 17:00 the evening has begun,
    # so the phrase can no longer claim what came before it.
    assert (
        describe_day_rain(8.0, "17:00", thunder=True, issued_hour=17)
        == "showery with thunderstorms"
    )
    assert (
        describe_day_rain(8.0, "17:00", thunder=True, issued_hour=18)
        == "showery with thunderstorms"
    )
    # The dry band loses its shower qualifier for the same reason: "dry apart
    # from a brief evening shower" says the rest of the day was dry.
    assert describe_day_rain(0.9, "17:00", issued_hour=18) == "dry"
    assert (
        describe_day_rain(0.9, "17:00", issued_hour=6)
        == "dry apart from a brief evening shower"
    )


def test_a_completed_day_keeps_its_timing_because_it_was_observed():
    """`issued_hour=None` is yesterday's side of the comparison.

    The distinction is not cosmetic: yesterday's phrase is built from what the
    station and the reanalysis actually recorded, so its timing is a report.
    Today's is built from a forecast, and the elapsed part of it is unverified
    by anything this module can see.
    """
    assert (
        describe_day_rain(8.0, "17:00", thunder=True, issued_hour=None)
        == "dry until evening thunderstorms"
    )

def test_afternoon_thunder_names_the_band():
    assert describe_day_rain(8.0, "13:00", thunder=True, issued_hour=None) == "showery with afternoon thunderstorms"


def test_morning_thunder_names_the_band():
    assert describe_day_rain(20.0, "07:00", thunder=True, issued_hour=None) == "wet with thunderstorms"


def test_dry_band_no_longer_swallows_a_timed_shower():
    # The band edge used to be a cliff: 0.9 mm at 17:00 read "dry" while
    # 1.1 mm at 17:00 read "dry until evening showers". A fifth of a
    # millimetre changed the whole description of the day.
    assert describe_day_rain(0.9, "17:00", issued_hour=None) == "dry apart from a brief evening shower"
    assert describe_day_rain(1.1, "17:00", issued_hour=None) == "dry until evening showers"


def test_dry_band_timed_shower_afternoon_and_morning():
    assert describe_day_rain(0.9, "13:00", issued_hour=None) == "dry apart from a brief afternoon shower"
    assert describe_day_rain(0.9, "07:00", issued_hour=None) == "dry apart from an early shower"


def test_existing_bands_unchanged():
    assert describe_day_rain(3.0, None, issued_hour=None) == "largely dry"
    assert describe_day_rain(3.0, "17:00", issued_hour=None) == "dry until evening showers"
    assert describe_day_rain(8.0, "17:00", issued_hour=None) == "dry until evening showers"
    assert describe_day_rain(20.0, "17:00", issued_hour=None) == "dry until heavy evening rain"
    assert describe_day_rain(8.0, "13:00", issued_hour=None) == "showery from the afternoon"
    assert describe_day_rain(20.0, "07:00", issued_hour=None) == "wet"


# ---------------------------------------------------------------------------
# compute_day_over_day — the sentence the reader actually gets
# ---------------------------------------------------------------------------


def test_the_2026_08_24_regression():
    # Yesterday thundered; today is genuinely dry. The old code said
    # "dry again" because both sides landed in the sub-1 mm band.
    #
    # Reworded twice since: by item 83's one-statement rule, and again on
    # 2026-09-10 when the operator asked for the backward glance to go.
    #
    # WHAT THE REGRESSION IS ABOUT IS THE WORD "again", and that is what
    # this asserts. "dry again" is a false claim that yesterday was dry, to
    # a reader who stood in the storm. "dry" claims nothing about yesterday
    # at all — the same trade the both-dry branch already makes, for the
    # same reason: silence makes no false claim, and yesterday's thunder is
    # still carried by the structured field, the verification notes and the
    # detailed discussion, which is where a reader goes to look it up.
    result = compute_day_over_day(actual(thunder=True), preds(), issued_hour=0)
    assert result.rain_contrast == "dry"
    assert "again" not in result.rain_contrast
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
    result = compute_day_over_day(actual(thunder=False), preds(), issued_hour=0)
    assert result.rain_contrast is None


def test_a_day_never_asked_about_thunder_is_not_a_day_that_thundered():
    """thunder=None is "never observed", not False. It must not be read as a
    reason to keep the rain clause — the three-valued field decides whether
    the record KNOWS, and an unknown is not an event."""
    result = compute_day_over_day(actual(thunder=None), preds(), issued_hour=0)
    assert result.rain_contrast is None
    assert result.yesterday_thunder is None


def test_gap_when_yesterday_unobserved():
    assert compute_day_over_day(None, preds(), issued_hour=0) is None


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
    comparison = compute_day_over_day(yesterday, preds(), issued_hour=0)

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
    , issued_hour=None) == "dry apart from a brief evening shower", "the description itself is unchanged"


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
    result = compute_day_over_day(actual(precip_mm=2.0), real_2026_09_08_day0(), issued_hour=0)

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
    mild = compute_day_over_day(actual(high_c=31.5), preds(high_c=25.0), issued_hour=0)
    front = compute_day_over_day(actual(high_c=31.5), preds(high_c=6.5), issued_hour=0)

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
    ordinary = compute_day_over_day(actual(peak_wind_kmh=49.3), preds(wind_kmh=35.8), issued_hour=0)
    collapse = compute_day_over_day(actual(peak_wind_kmh=49.3), preds(wind_kmh=9.0), issued_hour=0)

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
    # "similar cloud" is required for the claim now: it covers every measured
    # dimension, and the sky is one since items 87 and 65.
    assert describe_day_over_day(
        "about the same", "similar winds", None, cloud_label="similar cloud"
    ) == "Much like yesterday."


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


def test_a_changed_day_describes_today_and_stops():
    """Defect 3, twice over. "X today; yesterday was Y" was two statements in
    a slot that allows one; ", after a Y day" was one statement that still
    looked backwards. Both are gone — see
    test_the_backward_glance_is_gone_from_the_rain_sentence for the live
    Overview that settled it.

    THE PHRASE IS THE SAME WHATEVER YESTERDAY DID, which is the point: this
    branch is reached only when the days differ, and how they differed is
    the lead sentence's job.
    """
    changed = compute_day_over_day(
        actual(precip_mm=20.0, thunder=False), preds(rain=True, precip_mm=0.1)
    , issued_hour=0)
    assert changed.rain_contrast == "dry"

    thundery = compute_day_over_day(
        actual(precip_mm=20.0, thunder=True), preds(rain=True, precip_mm=0.1)
    , issued_hour=0)
    assert thundery.rain_contrast == "dry"


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

    same = compute_day_over_day(y, today, today_convective=True, issued_hour=0)
    assert same.rain_contrast == "largely dry with thunderstorms again"

    # A genuine change no longer says "again", which is the whole of the
    # 2026-08-24 lesson. It no longer names yesterday either, since 2026-09-10.
    changed = compute_day_over_day(y, today, today_convective=False, issued_hour=0)
    assert changed.rain_contrast == "largely dry"

    # And the actionable direction, which was unreachable before: today's
    # storms are today's news, and they are stated as such rather than as a
    # contrast against a quiet yesterday.
    quiet_yesterday = actual(precip_mm=3.0, onset_hour=None, thunder=False)
    news = compute_day_over_day(quiet_yesterday, today, today_convective=True, issued_hour=0)
    assert news.rain_contrast == "largely dry with thunderstorms"


def test_the_contrast_frame_needs_an_actual_contrast():
    """"Largely dry, after a largely dry day" was reachable: the
    same-or-different test compared full CHARACTER phrases while the summary
    reported the BAND, so two days in one band could be framed as a change.

    The test now runs on exactly what the summary reports."""
    y = actual(precip_mm=3.0, onset_hour="17:00", thunder=False)
    result = compute_day_over_day(y, preds(rain=False, precip_mm=2.0), today_convective=False, issued_hour=0)

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
        cloud_label="similar cloud",
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


# ---------------------------------------------------------------------------
# Absolute wind level — the thing neither half of the Overview could say.
# ---------------------------------------------------------------------------


def test_the_warning_ladder_is_noaa_and_needs_no_local_conversion():
    """NOAA defines a Gale Warning as "sustained surface winds, OR FREQUENT
    GUSTS, in the range of 34 knots to 47 knots" — the standard already
    covers gusts, so nothing is converted and nothing is deployment-specific.

    A first attempt converted Beaufort's SUSTAINED boundaries with a gust
    factor measured at this site, which baked a Kisumu constant into a
    published scale. The operator caught it; the constant is gone.
    """
    assert wind_warning(45.0) is None                       # under 25 kt
    assert wind_warning(25 * 1.852) == "strong breeze"      # Small Craft Advisory
    assert wind_warning(34 * 1.852) == "gale force"         # Gale Warning
    assert wind_warning(48 * 1.852) == "storm force"        # Storm Warning
    assert wind_warning(64 * 1.852) == "hurricane force"    # Hurricane Force
    assert wind_warning(None) is None

    # The windiest day in the stored record is 52.6 km/h — 28.4 kt, which is a
    # Small Craft Advisory and nothing more. Nothing here has ever reached
    # gale force, so the upper bands are dormant at this site by fact rather
    # than by design.
    assert wind_warning(52.6) == "strong breeze"


def test_a_warning_survives_much_like_yesterday():
    """Half one of the Overview. Two gale days running compared as "similar
    winds" and the sameness lead then swallowed them whole."""
    assert describe_day_over_day(
        "about the same", "similar winds", None, cloud_label="similar cloud",
        wind_warning_name="gale force",
    ) == "Much like yesterday. Gusting to gale force."

    # And it is not suppressed by a change leading either.
    assert describe_day_over_day(
        "noticeably cooler", "similar winds", None, wind_warning_name="storm force",
    ) == "Noticeably cooler than yesterday. Gusting to storm force."

    assert describe_day_over_day(
        "about the same", "similar winds", None, cloud_label="similar cloud"
    ) == "Much like yesterday."


def test_a_warning_survives_conditions_much_the_same():
    """Half two. Four dangerous days running read "conditions much the same
    through Saturday", which is true and is the least useful thing to say."""
    steady = [30.2, 30.1, 29.8]
    # Note the scope narrows to "temperatures": "winds much the same, with
    # gusts reaching near gale" would deny its own tail, even though steady
    # and dangerous are both true of that wind.
    assert describe_extended_trend(
        30.0, steady, [0.0, 0.0, 0.0], "Saturday",
        today_wind_kmh=90.0, day_winds_kmh=[92.0, 88.0, 91.0],
    ) == "temperatures much the same through Saturday, with gusts reaching storm force"

    # Below the floor the clause is unchanged.
    assert describe_extended_trend(
        30.0, steady, [0.0, 0.0, 0.0], "Saturday",
        today_wind_kmh=20.0, day_winds_kmh=[21.0, 20.0, 19.0],
    ) == "conditions much the same through Saturday"


# ---------------------------------------------------------------------------
# The sky — items 87, 65 and 83. "Much like yesterday" covered three
# measurements and the operator's original complaint was about the fourth.
# ---------------------------------------------------------------------------


def test_a_changed_sky_is_a_changed_day():
    """The operator's founding objection, 2026-09-08: "a cloudy/rainy day with
    the same temps, wind speed, and AQI or whatever is not 'much the same'
    even though 3/4 vectors may be the same."

    Until now the comparison had no sky at all, so a clear day following an
    overcast one read as "much like yesterday" and was, on three of the four
    things a reader notices."""
    overcast = actual(cloud_cover_pct=90.0)
    clear = preds(cloud_cover_pct=10.0)

    result = compute_day_over_day(overcast, clear, issued_hour=0)
    assert result.cloud_label == "much clearer"
    assert result.overview_comparison == "Much clearer than yesterday."


def test_a_sky_that_held_still_is_not_news():
    """One okta, 12.5 percentage points, is the smallest change the standard's
    own bands distinguish — below it the sky did not change category and
    saying so would be enumeration."""
    result = compute_day_over_day(actual(cloud_cover_pct=40.0), preds(cloud_cover_pct=48.0), issued_hour=0)
    assert result.cloud_label == "similar cloud"
    assert result.overview_comparison == "Much like yesterday."


def test_the_sky_joins_the_other_measurements_rather_than_replacing_them():
    result = compute_day_over_day(
        actual(high_c=25.0, cloud_cover_pct=20.0), preds(high_c=29.0, cloud_cover_pct=70.0)
    , issued_hour=0)
    assert result.overview_comparison == "Noticeably warmer and much cloudier than yesterday."


def test_no_sky_measurement_withholds_the_sameness_claim():
    """"Much like yesterday" is a claim about every measured dimension, so a
    missing one withholds it — the same rule the wind label already follows."""
    result = compute_day_over_day(actual(cloud_cover_pct=None), preds(cloud_cover_pct=None), issued_hour=0)
    assert result.cloud_label is None
    assert result.overview_comparison is None


# ---------------------------------------------------------------------------
# Item 98 — the block mixes point sources and said so nowhere.
# ---------------------------------------------------------------------------


def test_the_block_says_where_each_observation_came_from():
    """Four independent careful readers called `yesterday_rain: true` beside a
    "largely dry" phrase a contradiction. It is not — but it took the
    operator, living here, to say why: the rain is a grid cell and the thunder
    is a station 3.8 km away, and on a convective day those answer questions
    about different places.

    Measured over the fortnight to 2026-09-08: the two disagreed on 4 of 14
    days. Nothing labelled them, so a reader had to guess whether they were
    looking at a defect or at two places.
    """
    view = comparison_for_prompt({
        "yesterday_rain": True,
        "yesterday_thunder": False,
        "today_rain_expected": False,
        "overview_comparison": "Largely dry again.",
        "provenance": {"rain": "era5_archive", "thunder": "metar_station"},
    })

    assert view["observed_from"] == {
        "yesterday_rain": "era5_archive",
        "yesterday_thunder": "metar_station",
    }
    # The numbers behind the labels stay withheld — this adds a source, not an
    # operand.
    assert "provenance" not in view
    assert "yesterday_high_c" not in view


def test_a_record_with_no_provenance_carries_no_claim_about_sources():
    view = comparison_for_prompt({
        "yesterday_rain": True, "yesterday_thunder": None,
        "today_rain_expected": False, "overview_comparison": "Dry again.",
    })
    assert "observed_from" not in view


def test_the_backward_glance_is_gone_from_the_rain_sentence():
    """Raised by the operator 2026-09-10 from that morning's live Overview:
    "Much calmer than yesterday. Dry until evening thunderstorms, after a
    thundery day." — "not so useful".

    THE TAIL WAS ALSO SAYING THE WRONG THING. It is reached only when the
    two days differ, and on this day they differed on the BAND — yesterday
    measurably wet, today dry until the evening. But `yesterday_summary`
    puts thunder ahead of the band, so the word it printed was the one
    dimension where the two days AGREED. The sentence drew a contrast out
    of the only thing that had not changed.

    Fixing the word choice was the smaller option and was not taken. The
    lead sentence already carries "than yesterday", and a second backward
    reference in the next breath spends the Overview's opening on a day the
    reader has already lived. The rain half describes TODAY.
    """
    yesterday_thundery = actual(precip_mm=12.0, onset_hour="15:00", thunder=True)
    today_dry_until_storms = preds(rain=True, precip_mm=3.0, onset="18:00")

    c = compute_day_over_day(
        yesterday_thundery, today_dry_until_storms, today_convective=True
    , issued_hour=0)
    assert "after a" not in (c.rain_contrast or "")
    assert "yesterday" not in (c.rain_contrast or "")

    # The whole sentence, as the reader gets it: ONE backward glance, in the
    # clause that owns the comparison.
    text = describe_day_over_day(
        "about the same", "much calmer", c.rain_contrast, cloud_label="similar cloud"
    )
    assert text.count("yesterday") == 1
    assert "after a" not in text


# --- ROADMAP item 104, contract item 8: the daypart gate -------------------


@pytest.mark.parametrize(
    "issued_hour,sunset_hour,expected,why",
    [
        (3,  18, "today",    "03:00 — the whole day is ahead"),
        (6,  18, "today",    "06:00 — the day is ahead"),
        # NOON ITSELF IS AFTERNOON. Half the day is lived by then, and the
        # operator's rule is "only useful early in the local day" — so the
        # boundary is exclusive. Asserted rather than left to `<` versus
        # `<=`, because nothing else in their five scenarios lands on it.
        (12, 18, None,       "12:00 — half lived, the comparison has gone"),
        (11, 18, "today",    "11:00 — still morning"),
        (15, 18, None,       "15:00 — lived enough of it to stop caring"),
        (18, 18, None,       "18:00 — still before sunset, still today"),
        (20, 18, "tomorrow", "20:00 — after sunset, the day ahead is tomorrow"),
    ],
)
def test_the_comparison_is_gated_by_the_daypart(issued_hour, sunset_hour, expected, why):
    """THE OPERATOR'S OWN FIVE SCENARIOS, 2026-09-14, named one per case.

    Asked what they wanted at each hour, the answer was not a recast window
    but a gate: a comparison is worth reading while the day is mostly ahead,
    and by mid-afternoon the reader has lived it — "I've already lived enough
    of it that I don't care how it compares to yesterday."

    Two boundaries, and both are the day's own rather than round numbers.
    NOON separates a day mostly ahead from one mostly lived. SUNSET is where
    "the day ahead" stops meaning today and starts meaning tomorrow, which is
    why 18:00 is suppressed and 20:00 is not on a day whose sun sets at 18:39.
    """
    from openlocalweather.comparison import comparison_subject

    assert comparison_subject(issued_hour, sunset_hour=sunset_hour) == expected, why


def test_an_unknown_clock_makes_no_comparison():
    """`_issued_hour` returns 24 when the moment could not be established, and
    absence is not permission — the same rule item 118 applies to every other
    clock-dependent phrase."""
    from openlocalweather.comparison import comparison_subject

    assert comparison_subject(24, sunset_hour=18) is None
    assert comparison_subject(None, sunset_hour=18) is None


def test_a_day_with_no_sunset_still_gates_on_noon():
    """Polar latitudes, or a sun computation that threw. Without a sunset the
    evening pivot cannot be placed, so the morning half still works and the
    tomorrow-subject simply never fires — a missing boundary must not promote
    an afternoon into a comparison."""
    from openlocalweather.comparison import comparison_subject

    assert comparison_subject(6, sunset_hour=None) == "today"
    assert comparison_subject(15, sunset_hour=None) is None
    assert comparison_subject(20, sunset_hour=None) is None


def test_the_tomorrow_subject_describes_tomorrow():
    """ROADMAP item 104, contract item 8, stage 2 piece 1.

    At 20:00 the subject is tomorrow, so the numbers must be TOMORROW'S. Until
    now the gate said "tomorrow" while `compute_day_over_day` went on averaging
    today's Day+0 predictions — a comparison labelled one thing and computed
    from another.

    Day+1 was already being extracted for `describe_extended_trend`; this
    threads the same predictions to a second consumer rather than adding a
    lead.
    """
    from openlocalweather.comparison import compute_day_over_day

    yesterday = DailyActual(rain=False, high_c=20.0, low_c=15.0, peak_wind_kmh=10.0)
    today = [ModelPrediction(model="a", rain=False, high_c=21.0, low_c=15.0, wind_kmh=10.0)]
    tomorrow = [ModelPrediction(model="a", rain=False, high_c=30.0, low_c=15.0, wind_kmh=10.0)]

    evening = compute_day_over_day(
        # `yesterday` is not the baseline at this hour — stage 2 moved it to
        # today, which is the day the operator's 20:00 scenario asks about.
        None, today, issued_hour=20, sunset_hour=18, tomorrow_predictions=tomorrow,
        today_actual=yesterday,
    )

    assert evening is not None
    # Tomorrow is 10 C above the baseline; today is only 1 C above. A
    # comparison built from today's numbers would read "about the same".
    assert evening.high_label is not None and "much" in evening.high_label


def test_a_tomorrow_subject_without_tomorrow_says_nothing():
    """The honest absence while stage 2 is half-built, and afterwards whenever
    the extended fetch has failed. A comparison that cannot describe its own
    subject is not a comparison — and "Tuesday will be cooler than Sunday",
    which is what today's numbers against yesterday's would mean here, is
    worse than silence."""
    from openlocalweather.comparison import compute_day_over_day

    yesterday = DailyActual(rain=False, high_c=20.0, low_c=15.0, peak_wind_kmh=10.0)
    today = [ModelPrediction(model="a", rain=False, high_c=21.0, low_c=15.0, wind_kmh=10.0)]

    assert compute_day_over_day(
        None, today, issued_hour=20, sunset_hour=18, tomorrow_predictions=None,
        today_actual=yesterday,
    ) is None


def test_the_morning_subject_still_uses_today():
    """Piece 1 must not disturb the hours that already worked."""
    from openlocalweather.comparison import compute_day_over_day

    yesterday = DailyActual(rain=False, high_c=20.0, low_c=15.0, peak_wind_kmh=10.0)
    today = [ModelPrediction(model="a", rain=False, high_c=30.0, low_c=15.0, wind_kmh=10.0)]
    tomorrow = [ModelPrediction(model="a", rain=False, high_c=21.0, low_c=15.0, wind_kmh=10.0)]

    morning = compute_day_over_day(
        yesterday, today, issued_hour=6, sunset_hour=18, tomorrow_predictions=tomorrow
    )

    assert morning is not None
    assert morning.high_label is not None and "much" in morning.high_label


# ---------------------------------------------------------------------------
# Item 126 — the gust operand is the calibrated one.
# ---------------------------------------------------------------------------


def test_the_label_bands_the_calibrated_gust_not_the_raw_consensus():
    """The defect, and the same weather banded both ways.

    Yesterday gusted to 40.7 and the models call 30.0. Raw, that is -10.7 and
    reads "calmer"; with the +11.5 km/h the record has measured added back it
    is +0.8 and the wind held still. Over 34 mornings the raw operand read
    "calmer" fourteen times and "windier" not once, at a mean of -8.31 km/h
    against an 8.0 km/h no-change band.
    """
    yesterday = actual(peak_wind_kmh=40.7)

    raw = compute_day_over_day(yesterday, preds(wind_kmh=30.0), issued_hour=6)
    calibrated = compute_day_over_day(
        yesterday, preds(wind_kmh=30.0), issued_hour=6, calibrated_wind_kmh=41.5
    )

    assert raw.wind_label == "calmer"
    assert calibrated.wind_label == "similar winds"


def test_the_record_stores_both_gusts():
    """A delta computed from one number and stored beside another cannot be
    checked afterwards, and checking afterwards is most of what the record is
    for. The raw consensus stays because it is what the models said."""
    result = compute_day_over_day(
        actual(peak_wind_kmh=40.7), preds(wind_kmh=30.0), issued_hour=6,
        calibrated_wind_kmh=41.5,
    )

    assert result.today_consensus_peak_wind_kmh == 30.0
    assert result.today_calibrated_peak_wind_kmh == 41.5
    assert result.wind_delta_kmh == 0.8


def test_no_calibration_falls_back_to_what_the_models_said():
    """Not a fallback so much as the honest operand: on a day with too little
    verified history nothing has measured a bias to remove."""
    result = compute_day_over_day(actual(peak_wind_kmh=40.7), preds(wind_kmh=30.0), issued_hour=6)

    assert result.today_calibrated_peak_wind_kmh is None
    assert result.wind_delta_kmh == -10.7


def test_the_gale_warning_reads_the_calibrated_gust():
    """The consumer where a low gust costs most. Every other label here is
    relative, so a shared bias partly cancels; a warning is a LEVEL against
    NOAA's absolute thresholds, and 12 km/h low is a whole band low on the day
    it matters."""
    yesterday = actual(peak_wind_kmh=40.7)

    # The lowest band is 25 knots, 46.3 km/h. A 40.0 km/h consensus sits
    # under it and the record's own correction carries it over.
    quiet = compute_day_over_day(yesterday, preds(wind_kmh=40.0), issued_hour=6)
    warned = compute_day_over_day(
        yesterday, preds(wind_kmh=40.0), issued_hour=6, calibrated_wind_kmh=51.5
    )

    assert "strong breeze" not in (quiet.overview_comparison or "")
    assert "strong breeze" in warned.overview_comparison


# ---------------------------------------------------------------------------
# Contract item 8, stage 2 — a comparison about tomorrow has to say so.
# ---------------------------------------------------------------------------


def _evening(**kw):
    """20:00 on a Monday whose sun set at 18:39, with Tuesday forecast.

    The baseline here is a RICH DailyActual, which is not what the pipeline
    supplies — `observed.observed_baseline` narrows today's to temperature,
    because only the station observes a day in progress and it measures
    sustained wind, eighths of sky and no rainfall amount. These cases are
    about the WORDING contract; the instrument contract is tested where it
    is decided.
    """
    today_observed = kw.pop("today_observed", actual(high_c=30.5, precip_mm=12.0))
    tomorrow = kw.pop("tomorrow", preds(high_c=27.0, precip_mm=0.0))
    return compute_day_over_day(
        # Yesterday is not the baseline at this hour and is deliberately
        # absent: a 20:00 run with today in hand and no record for yesterday
        # is still a comparison that can be made.
        None, [], issued_hour=20, sunset_hour=18, today_actual=today_observed,
        tomorrow_predictions=tomorrow, today_name="Monday", tomorrow_name="Tuesday",
        **kw,
    )


def test_the_evening_lead_names_today_in_the_past_tense():
    """The operator's own sentence, 2026-09-14: "It will be cooler and
    breezier than today (Monday) was."

    "Cooler than today" at 20:00 is ambiguous about which today — the one
    ending or the one the reader wakes into — and the verb is what places it.
    """
    result = _evening()

    assert result.overview_comparison.startswith("Noticeably cooler than today (Monday) was.")


def test_the_similarity_form_takes_no_verb():
    """"Much like today (Monday) was" is a stammer. English names the same
    baseline two different ways depending on the clause, which is why the two
    fragments are passed separately rather than derived from one by a rule."""
    steady = _evening(today_observed=actual(high_c=31.5, peak_wind_kmh=33.1, cloud_cover_pct=40.0),
                      tomorrow=preds(high_c=31.5, wind_kmh=33.0, cloud_cover_pct=44.0))

    assert steady.overview_comparison.startswith("Much like today (Monday).")


def test_the_rain_half_names_the_day_it_is_about():
    """"Dry until evening showers." read at 20:00 on Monday is about Monday
    night to anyone who has not been told otherwise."""
    result = _evening(tomorrow=preds(rain=True, precip_mm=8.0, onset="17:00", high_c=27.0))

    # "again" because today was showery too, which at 20:00 is a true and
    # useful thing to say about tomorrow.
    assert "Tuesday will be dry until evening showers again." in result.overview_comparison


def test_tomorrows_timing_survives_a_late_issuance():
    """Item 118 bounds a timing phrase by the hours already ELAPSED, and none
    of tomorrow's have. Without this a 17:00 onset read at 20:00 would be
    suppressed as a claim about the past — on a day that has not started."""
    result = _evening(tomorrow=preds(rain=True, precip_mm=8.0, onset="17:00", high_c=27.0))

    assert "evening showers" in result.overview_comparison


def test_without_a_calendar_the_sentence_is_plainer_and_still_true():
    """A caller with no weekday names gets "today" and "tomorrow" rather than
    an empty parenthesis. Unambiguous in every case but the one the names
    exist for: a reader opening the page the next morning."""
    result = compute_day_over_day(
        None, [], issued_hour=20, sunset_hour=18,
        today_actual=actual(high_c=30.5, precip_mm=12.0),
        tomorrow_predictions=preds(high_c=27.0, precip_mm=0.0),
    )

    assert "than today was." in result.overview_comparison
    assert "Tomorrow will be" in result.overview_comparison
    assert "(" not in result.overview_comparison


def test_the_morning_sentence_is_untouched():
    """Every default is yesterday, so the hour that already worked cannot
    have moved."""
    result = compute_day_over_day(actual(high_c=27.0), preds(high_c=29.0), issued_hour=6)

    assert "than yesterday" in result.overview_comparison
    assert "today (" not in result.overview_comparison


def test_an_evening_with_nothing_observed_of_today_says_nothing():
    """The baseline moves with the subject, so its absence does too.

    Falling back to yesterday would publish "Tuesday will be cooler than
    Sunday" — the comparison piece 1 returns null rather than compute, one
    operand over.
    """
    tomorrow = preds(high_c=27.0)

    assert compute_day_over_day(
        actual(high_c=30.5), [], issued_hour=20, sunset_hour=18,
        tomorrow_predictions=tomorrow, today_actual=None,
    ) is None


def test_a_morning_with_nothing_observed_of_today_is_unaffected():
    """today_actual is the EVENING's baseline and must not gate the morning,
    where yesterday is still the day being measured against."""
    result = compute_day_over_day(
        actual(high_c=27.0), preds(high_c=29.0), issued_hour=6, today_actual=None
    )

    assert result is not None
    assert "than yesterday" in result.overview_comparison


def test_an_evening_needs_no_record_for_yesterday():
    """A 20:00 run with today in hand and nothing stored for yesterday is a
    comparison that CAN be made. Checking the parameter rather than the chosen
    baseline would have refused it — the two were the same thing until the
    evening subject existed."""
    result = compute_day_over_day(
        None, [], issued_hour=20, sunset_hour=18,
        tomorrow_predictions=preds(high_c=27.0), today_actual=actual(high_c=30.5),
        today_name="Monday", tomorrow_name="Tuesday",
    )

    assert result is not None
    assert "than today (Monday) was" in result.overview_comparison


# --- ROADMAP item 158, step 1: the thunder's timing, and the station's word ---

from openlocalweather.daypart import ConvectiveTiming
from openlocalweather.models import ObservedSoFar

_AFTERNOON = ConvectiveTiming(onset="from the afternoon", peak="overnight", onset_passed=False)
_EVENING = ConvectiveTiming(onset="from the evening", peak="overnight", onset_passed=False)
_STATION = "Kisumu Airport"


def test_a_dry_day_with_timed_thunder_is_no_longer_a_contradiction():
    assert describe_day_rain(0.3, None, True, issued_hour=6, thunder_timing=_AFTERNOON) == (
        "dry, with thunder possible from the afternoon, peaking overnight"
    )


def test_thunder_from_the_evening_leaves_the_day_dry_by_day():
    assert describe_day_rain(0.3, None, True, issued_hour=6, thunder_timing=_EVENING) == (
        "dry by day, with thunder possible from the evening, peaking overnight"
    )


def test_a_showery_day_keeps_its_shape_and_gains_the_timing():
    assert describe_day_rain(8.0, "13:00", True, issued_hour=6, thunder_timing=_EVENING) == (
        "showery from the afternoon, with thunder possible from the evening, peaking overnight"
    )


def test_without_a_timing_the_old_words_stand():
    assert describe_day_rain(0.3, None, True, issued_hour=6) == "dry but thundery"


def test_showers_the_station_has_reported_are_stated_as_a_report():
    observed = ObservedSoFar(precipitation=True, thunder=False, reported_through="15:00")
    assert describe_day_rain(0.3, "18:00", True, issued_hour=16, thunder_timing=_EVENING,
                             observed=observed, station_label=_STATION) == (
        "showers reported at Kisumu Airport as of 15:00, with thunder possible from the evening, peaking overnight"
    )


def test_more_thunder_only_when_the_station_has_reported_thunder():
    observed = ObservedSoFar(precipitation=True, thunder=True, reported_through="15:00")
    assert describe_day_rain(0.3, "18:00", True, issued_hour=16, thunder_timing=_EVENING,
                             observed=observed, station_label=_STATION) == (
        "showers reported at Kisumu Airport as of 15:00, with more thunder possible from the evening, peaking overnight"
    )


def test_thunder_the_station_has_reported_names_the_station_and_the_time():
    observed = ObservedSoFar(precipitation=False, thunder=True, reported_through="15:00")
    assert describe_day_rain(0.3, None, True, issued_hour=16, thunder_timing=_EVENING,
                             observed=observed, station_label=_STATION) == (
        "thunder reported at Kisumu Airport as of 15:00, more possible overnight"
    )


def test_reported_thunder_with_no_more_forecast_is_just_the_report():
    observed = ObservedSoFar(precipitation=False, thunder=True, reported_through="15:00")
    assert describe_day_rain(0.3, None, False, issued_hour=16, observed=observed, station_label=_STATION) == (
        "thunder reported at Kisumu Airport as of 15:00"
    )


def test_a_station_report_without_a_reach_is_not_used():
    # A report needs its time; without one the phrase falls back to the forecast.
    observed = ObservedSoFar(precipitation=True, thunder=False, reported_through=None)
    assert describe_day_rain(0.3, None, True, issued_hour=16, thunder_timing=_EVENING,
                             observed=observed, station_label=_STATION) == (
        "dry by day, with thunder possible from the evening, peaking overnight"
    )


def test_the_comparison_hands_the_station_and_the_timing_to_todays_side():
    yesterday = DailyActual(rain=False, high_c=29.1, low_c=18.6, peak_wind_kmh=35.3, precip_mm=0.3,
                            cloud_cover_pct=91.0, thunder=False)
    today = [ModelPrediction(model="m", rain=False, high_c=29.5, low_c=18.0, wind_kmh=35.0,
                             precip_mm=0.3, cloud_cover_pct=91.0)]
    observed = ObservedSoFar(precipitation=True, thunder=False, reported_through="15:00")
    got = compute_day_over_day(yesterday, today, True, issued_hour=6, observed_so_far=observed,
                               station_label=_STATION, convective_timing=_EVENING)
    assert got.overview_comparison == (
        "Showers reported at Kisumu Airport as of 15:00, with thunder possible from the evening, peaking overnight."
    )
