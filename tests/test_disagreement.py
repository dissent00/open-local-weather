"""ROADMAP item 104, C2's third trigger."""

import pytest

from openlocalweather.models import DeviationBands
from openlocalweather.disagreement import (
    DISAGREEMENT_GUST_EXCEEDED,
    DISAGREEMENT_HIGH_EXCEEDED,
    DISAGREEMENT_LOW_DIVERGES,
    DISAGREEMENT_ONSET_ALREADY_PASSED,
    DISAGREEMENT_RAIN_WHILE_DRY,
    ObservedSoFar,
    StandingCall,
    low_divergence,
    observation_disagreements,
)


def test_rain_observed_while_the_call_said_dry():
    """The clearest contradiction available, and the one that matters most:
    `rain` is the scored boolean."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=True, high_c=None),
    )

    assert out == [DISAGREEMENT_RAIN_WHILE_DRY]


def test_no_rain_YET_does_not_contradict_a_rain_call():
    """THE ASYMMETRY, and it governs every test in this module.

    A mid-day observation can only ever prove a forecast too LOW, never too
    high — rain that has fallen has fallen, and a day that has not rained yet
    may still. Treating "no rain so far" as a contradiction would re-forecast
    every dry morning of every wet day.
    """
    out = observation_disagreements(
        StandingCall(rain=True, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=None),
    )

    assert out == []


def test_the_observed_high_has_already_passed_the_forecast_high():
    """A maximum only rises, so an observation above the call settles it."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=33.0),
    )

    assert out == [DISAGREEMENT_HIGH_EXCEEDED]


def test_a_high_below_the_call_is_not_a_contradiction():
    """Same asymmetry: the day is not over."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=24.0),
    )

    assert out == []


def test_the_margin_must_clear_the_station_offset():
    """Sized against two MEASURED quantities rather than picked.

    The station reads +0.43 C against the reanalysis on average (item 44),
    and the Day+0 high error is a few tenths. A margin at or below either
    would fire on the instrument rather than on the weather.
    """
    from openlocalweather.disagreement import TEMP_CONTRADICTION_MARGIN_C

    assert TEMP_CONTRADICTION_MARGIN_C > 0.43

    just_under = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=False, high_c=30.0 + TEMP_CONTRADICTION_MARGIN_C - 0.1),
    )
    assert just_under == []


def test_absent_observations_contradict_nothing():
    """A station that reported nothing is not a station reporting agreement —
    the same rule every absent input in this project follows."""
    assert observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=None, high_c=None),
    ) == []


def test_absent_standing_call_contradicts_nothing():
    """Nothing to disagree WITH. A first issuance has no standing call, and
    that is C2's first trigger rather than this one."""
    assert observation_disagreements(
        StandingCall(rain=None, temp_high_c=None),
        ObservedSoFar(precipitation=True, high_c=99.0),
    ) == []


def test_both_can_fire_and_the_order_is_stable():
    """Stored and compared across two languages, so the order is part of the
    contract rather than an implementation detail."""
    out = observation_disagreements(
        StandingCall(rain=False, temp_high_c=30.0),
        ObservedSoFar(precipitation=True, high_c=35.0),
    )

    assert out == [DISAGREEMENT_RAIN_WHILE_DRY, DISAGREEMENT_HIGH_EXCEEDED]


# --- The onset that has already happened (ROADMAP item 138) ----------------


def test_an_onset_already_observed_contradicts_a_later_called_one():
    """THE OPERATOR'S CASE, 2026-09-16: "we forecast dry morning,
    thunderstorms starting at 1800 ... sensor data showing rain already
    started at 1400. I don't want us to call that dry until 1800 again."

    Nothing caught this. `RAIN_WHILE_DRY` needs `standing.rain is False`, and
    here the call says rain IS coming — it is right about the day and wrong
    about the hour. Onset is scored at Day+0, so the run is graded on the
    wrong number while the page contradicts itself.
    """
    found = observation_disagreements(
        StandingCall(rain=True, onset_hour="18:00"),
        ObservedSoFar(precipitation=True, precipitation_onset="14:00"),
    )

    assert DISAGREEMENT_ONSET_ALREADY_PASSED in found


def test_an_onset_inside_the_forecast_resolution_is_not_a_contradiction():
    """`onset_hour` is a point taken from a multi-hour WINDOW, so a
    difference smaller than that window is agreement, not disagreement.
    Firing here would re-forecast on the instrument."""
    found = observation_disagreements(
        StandingCall(rain=True, onset_hour="18:00"),
        ObservedSoFar(precipitation=True, precipitation_onset="17:30"),
    )

    assert DISAGREEMENT_ONSET_ALREADY_PASSED not in found


def test_rain_observed_later_than_called_is_not_a_contradiction():
    """THE ASYMMETRY THIS MODULE IS BUILT ON. A forecast can be proved too
    LOW by an observation and never too high: rain that arrived late has
    still arrived, and a call whose hour has passed with no rain only means
    the day is not over."""
    found = observation_disagreements(
        StandingCall(rain=True, onset_hour="14:00"),
        ObservedSoFar(precipitation=True, precipitation_onset="18:00"),
    )

    assert DISAGREEMENT_ONSET_ALREADY_PASSED not in found


def test_no_called_onset_or_no_observed_onset_says_nothing():
    """Absence is absence. A station that saw rain without catching the clock
    still reports rain, and that is not evidence about timing."""
    for standing, observed in (
        (StandingCall(rain=True), ObservedSoFar(precipitation=True, precipitation_onset="14:00")),
        (StandingCall(rain=True, onset_hour="18:00"), ObservedSoFar(precipitation=True)),
    ):
        assert DISAGREEMENT_ONSET_ALREADY_PASSED not in observation_disagreements(standing, observed)


# --- the observed low against the standing call — ROADMAP item 143 -----------


def test_a_warmer_station_low_is_recorded_but_is_not_a_contradiction():
    """The founding case: OBSERVED SO FAR said "low so far 20C" beside a
    standing temp_low_c of 18.2, at 06:01 with the night over.

    RECORDED, NOT SPENT. The operator's framing is that this is "more of a
    footnote" and "isn't a key item to read about in the morning" — and every
    code in `observation_disagreements` buys an LLM call under
    `new_cycle_or_contradiction`. So a divergence this far from freezing is
    measured and stored and buys nothing.
    """
    standing = StandingCall(temp_low_c=18.2)
    observed = ObservedSoFar(low_c=20.0)

    div = low_divergence(standing, observed, low_is_settled=True)
    assert div is not None
    assert div.delta_c == pytest.approx(1.8)
    assert div.notable is False, "1.8C at 20C is inside the wide band"
    assert div.decisive is False

    assert observation_disagreements(standing, observed, low_is_settled=True) == []


def test_the_same_gap_near_freezing_is_decisive():
    """Two degrees is nothing at 20C and decisive at 2C, because it is the
    difference between ice and no ice — the operator's words, and the whole
    reason the band is not one number."""
    standing = StandingCall(temp_low_c=-0.5)
    observed = ObservedSoFar(low_c=2.0)

    div = low_divergence(standing, observed, low_is_settled=True)
    assert div is not None
    assert div.notable is True
    assert div.decisive is True
    assert DISAGREEMENT_LOW_DIVERGES in observation_disagreements(
        standing, observed, low_is_settled=True
    )


def test_an_unsettled_night_makes_a_warmer_reading_prove_nothing():
    """Before sunrise the night may still get colder, so a station sitting
    ABOVE the called low has settled nothing — the same asymmetry that governs
    the high, pointed the other way."""
    standing = StandingCall(temp_low_c=-0.5)
    observed = ObservedSoFar(low_c=2.0)

    assert low_divergence(standing, observed, low_is_settled=False) is None
    assert observation_disagreements(standing, observed, low_is_settled=False) == []


def test_a_colder_reading_settles_the_call_at_any_hour():
    """A minimum only falls. A station already BELOW the called low has proved
    the call too high whether or not the night is over, so this one does not
    wait for sunrise."""
    standing = StandingCall(temp_low_c=2.0)
    observed = ObservedSoFar(low_c=-1.0)

    div = low_divergence(standing, observed, low_is_settled=False)
    assert div is not None, "colder than called is settled without a sunrise gate"
    assert div.delta_c == pytest.approx(-3.0)
    assert div.decisive is True


def test_no_divergence_without_both_numbers():
    """Absence is absence. A station that did not report a low, and a day with
    no standing call, both yield None rather than a zero divergence."""
    assert low_divergence(StandingCall(temp_low_c=18.2), ObservedSoFar(), low_is_settled=True) is None
    assert low_divergence(StandingCall(), ObservedSoFar(low_c=20.0), low_is_settled=True) is None


# --- the reporting/spending split — ROADMAP item 145 -------------------------


def test_tuning_the_reporting_band_can_never_change_what_is_spent():
    """THE SAFETY PROPERTY OF ITEM 145, and the reason the bands are two
    things rather than one.

    `reasoning.llm_should_reason` buys a judgment call and a narrative for any
    member of `observation_disagreements`. A reader who tightens what they
    want to be TOLD about must not thereby start paying for calls they never
    asked for — this project's readers are the ones who will not be buying an
    API key.

    Swept rather than sampled: the reporting bands are driven across their
    whole plausible range against a case that sits near freezing, where the
    old coupling bit hardest.
    """
    standing = StandingCall(temp_low_c=-0.5)
    observed = ObservedSoFar(low_c=2.0)

    baseline = observation_disagreements(standing, observed, low_is_settled=True)
    for tenth in range(1, 101):
        band = tenth / 10
        got = observation_disagreements(
            standing,
            observed,
            low_is_settled=True,
            bands=DeviationBands(low_c=band, low_freezing_c=band),
        )
        assert got == baseline, (
            f"a reporting band of {band} C changed what this run SPENDS: "
            f"{baseline} became {got}"
        )


def test_the_reporting_band_does_change_what_is_reported():
    """The other half, and without it the test above passes on a band that is
    wired to nothing at all."""
    standing = StandingCall(temp_low_c=18.2)
    observed = ObservedSoFar(low_c=20.0)

    wide = low_divergence(standing, observed, low_is_settled=True,
                          bands=DeviationBands(low_c=3.0))
    tight = low_divergence(standing, observed, low_is_settled=True,
                           bands=DeviationBands(low_c=1.0))

    assert wide is not None and tight is not None
    assert wide.notable is False, "1.8 C is inside the shipped default"
    assert tight.notable is True, "and outside a band the reader tightened"
    assert wide.decisive is tight.decisive is False, "neither may spend"


# ---------------------------------------------------------------------------
# The sustained-wind gap — ROADMAP item 146, step 3. A MEASUREMENT, not a test.
# ---------------------------------------------------------------------------


def _day0(**sustained):
    from openlocalweather.models import ModelPrediction

    return [ModelPrediction(model=m, rain=False, sustained_wind_kmh=v) for m, v in sustained.items()]


def test_the_gap_is_the_station_above_the_models_sustained_consensus():
    """Observed minus forecast, the sign every other error here uses: positive
    means the station's sustained maximum so far sits ABOVE what the models
    said the day's sustained maximum would be. Measured 2026-09-17 over
    thirteen days: +14.7 on average, and above on every one of them — which
    is why this is stored and reported nowhere."""
    from openlocalweather.disagreement import sustained_wind_gap

    got = sustained_wind_gap(
        ObservedSoFar(peak_wind_kmh=25.93),
        _day0(gfs_seamless=19.3, ecmwf_ifs025=11.5, icon_seamless=15.6),
    )
    assert got is not None
    assert got.consensus_kmh == pytest.approx((19.3 + 11.5 + 15.6) / 3)
    assert got.observed_kmh == pytest.approx(25.93)
    assert got.delta_kmh == pytest.approx(25.93 - (19.3 + 11.5 + 15.6) / 3)
    assert got.model_count == 3


def test_a_model_without_a_sustained_wind_is_not_in_the_consensus():
    """A model that does not forecast it (the met service) or has no series
    is absent from the mean, not a zero in it — the same rule `mean` follows
    everywhere in the record."""
    from openlocalweather.disagreement import sustained_wind_gap

    got = sustained_wind_gap(
        ObservedSoFar(peak_wind_kmh=30.0),
        _day0(gfs_seamless=20.0, kenya_met=None, ecmwf_ifs025=10.0),
    )
    assert got.consensus_kmh == pytest.approx(15.0)
    assert got.model_count == 2


def test_no_station_reading_is_no_gap():
    from openlocalweather.disagreement import sustained_wind_gap

    assert sustained_wind_gap(ObservedSoFar(peak_wind_kmh=None), _day0(gfs_seamless=20.0)) is None


def test_no_sustained_forecast_at_all_is_no_gap():
    """Three-valued: None is "no basis", never a gap of the whole observation."""
    from openlocalweather.disagreement import sustained_wind_gap

    assert sustained_wind_gap(ObservedSoFar(peak_wind_kmh=30.0), _day0(kenya_met=None)) is None
    assert sustained_wind_gap(ObservedSoFar(peak_wind_kmh=30.0), []) is None


# ---------------------------------------------------------------------------
# Reporting bands for the high and the onset — ROADMAP item 145, next step
# ---------------------------------------------------------------------------


def test_the_new_bands_default_to_the_spend_margins_so_nothing_changes_on_shipping():
    """A decoupling, not a retune — item 143's rule for the low, applied to the
    other two. The day this ships, the reporting list and the spending list
    agree on every run they ever disagreed on: nowhere."""
    from openlocalweather.disagreement import (
        ONSET_CONTRADICTION_MARGIN_MIN,
        TEMP_CONTRADICTION_MARGIN_C,
    )

    assert DeviationBands().high_c == TEMP_CONTRADICTION_MARGIN_C
    assert DeviationBands().onset_min == ONSET_CONTRADICTION_MARGIN_MIN


def test_a_tightened_high_band_reports_what_the_spend_margin_ignores():
    from openlocalweather.disagreement import notable_disagreements

    standing = StandingCall(rain=False, temp_high_c=30.0)
    observed = ObservedSoFar(precipitation=False, high_c=31.0)

    assert observation_disagreements(standing, observed) == [], "one degree is under the spend margin"
    assert notable_disagreements(standing, observed, low_is_settled=True) == [], "and under the default band"
    assert notable_disagreements(
        standing, observed, low_is_settled=True, bands=DeviationBands(high_c=1.0)
    ) == [DISAGREEMENT_HIGH_EXCEEDED]


def test_a_tightened_onset_band_reports_what_the_spend_margin_ignores():
    from openlocalweather.disagreement import notable_disagreements

    standing = StandingCall(rain=True, temp_high_c=30.0, onset_hour="18:00")
    observed = ObservedSoFar(precipitation=True, precipitation_onset="17:30")

    assert observation_disagreements(standing, observed) == []
    assert notable_disagreements(
        standing, observed, low_is_settled=True, bands=DeviationBands(onset_min=15)
    ) == [DISAGREEMENT_ONSET_ALREADY_PASSED]


def test_the_notable_list_carries_rain_and_the_low_in_the_spend_list_s_order():
    """Rain has no magnitude to band, so it is reported whenever it fires; the
    low is reported by `notable`, spent by `decisive`. Order is the spend
    list's, because both are stored and compared across two languages."""
    from openlocalweather.disagreement import notable_disagreements

    standing = StandingCall(rain=False, temp_high_c=30.0, onset_hour=None, temp_low_c=18.2)
    observed = ObservedSoFar(precipitation=True, high_c=35.0, low_c=20.0)

    got = notable_disagreements(
        standing, observed, low_is_settled=True, bands=DeviationBands(low_c=1.0)
    )
    assert got == [DISAGREEMENT_RAIN_WHILE_DRY, DISAGREEMENT_HIGH_EXCEEDED, DISAGREEMENT_LOW_DIVERGES]
    assert observation_disagreements(standing, observed, low_is_settled=True) == [
        DISAGREEMENT_RAIN_WHILE_DRY, DISAGREEMENT_HIGH_EXCEEDED,
    ], "the 1.8 C low is notable under the reader's band and far from freezing, so never decisive"


def test_tuning_the_high_and_onset_bands_can_never_change_what_is_spent():
    """Item 145's safety property, swept over the two new bands: a case that
    sits exactly on both spend margins, driven across every reporting band a
    reader could set, must spend identically throughout."""
    standing = StandingCall(rain=False, temp_high_c=30.0, onset_hour="18:00", temp_low_c=-0.5)
    observed = ObservedSoFar(precipitation=True, high_c=32.0, precipitation_onset="17:00", low_c=2.0)

    baseline = observation_disagreements(standing, observed, low_is_settled=True)
    assert DISAGREEMENT_HIGH_EXCEEDED in baseline and DISAGREEMENT_ONSET_ALREADY_PASSED in baseline
    for tenth in range(1, 101):
        for minutes in (5, 15, 30, 45, 60, 90, 120, 180, 240):
            got = observation_disagreements(
                standing, observed, low_is_settled=True,
                bands=DeviationBands(high_c=tenth / 10, onset_min=minutes),
            )
            assert got == baseline, f"high band {tenth / 10} / onset band {minutes} changed what is SPENT"


# ---------------------------------------------------------------------------
# A gust that has already beaten the forecast — 2026-09-21
# ---------------------------------------------------------------------------


def test_a_gust_above_the_called_peak_is_a_contradiction():
    """The forecast publishes one calibrated peak gust for the day, and the
    Winam Gulf section is what a boater acts on. A station gust already above
    it by mid-morning settles the question the day was supposed to answer."""
    found = observation_disagreements(
        StandingCall(peak_gust_kmh=41.5),
        ObservedSoFar(peak_gust_kmh=48.2),
    )
    assert DISAGREEMENT_GUST_EXCEEDED in found


def test_a_gust_below_the_called_peak_settles_nothing():
    """One-directional, like every other test here. The day is not over, so a
    gust under the called peak is not evidence the peak will not come."""
    found = observation_disagreements(
        StandingCall(peak_gust_kmh=41.5),
        ObservedSoFar(peak_gust_kmh=30.0),
    )
    assert DISAGREEMENT_GUST_EXCEEDED not in found


def test_the_sustained_wind_cannot_trigger_the_gust_code():
    """The whole point of naming the two apart. A sustained reading above the
    called GUST is not a gust above the called gust — that comparison is the
    one item 146 exists to stop, and it used to be invited by a prompt that
    called the sustained figure a gust."""
    found = observation_disagreements(
        StandingCall(peak_gust_kmh=41.5),
        ObservedSoFar(peak_wind_kmh=48.2),
    )
    assert DISAGREEMENT_GUST_EXCEEDED not in found


def test_no_gust_filed_is_not_a_contradiction():
    """Almost every day. An absent gust group is the station reporting none,
    and a check that read it as zero would be silent; one that read it as
    contradiction would fire daily."""
    found = observation_disagreements(
        StandingCall(peak_gust_kmh=41.5), ObservedSoFar(peak_wind_kmh=7.4)
    )
    assert DISAGREEMENT_GUST_EXCEEDED not in found
