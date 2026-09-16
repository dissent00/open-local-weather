"""Hand-computed expectations for the domain models.

Layer one of the three-layer contract in spec/README.md: these say Python is
RIGHT, where the vectors only say the two implementations AGREE.
"""

import re

from openlocalweather.models import DailyActual, format_temp_high_low


def test_temp_high_low_is_computed_not_transcribed():
    """The live regression. A blended high of 33.5 C was published as
    "34C / 93F" by the model, which rounded to 34 and converted that. 33.5 C
    is 92.3 F, so a third of the apparent jump from yesterday's 90 F was
    invented in the rounding."""
    assert format_temp_high_low(33.5, 18.0) == "34\u00b0C / 92\u00b0F high, 18\u00b0C / 64\u00b0F low"


def test_each_unit_is_rounded_from_the_true_value():
    """And so the pair deliberately does not round-trip: 34 C is 93.2 F, but
    the true 33.5 C is 92.3 F and 92 is the closest whole number to it.
    Rounding twice is the bug this replaced, not a property to restore."""
    assert "34\u00b0C / 92\u00b0F" in format_temp_high_low(33.5, 18.0)


def test_format_is_fixed_rather_than_reinvented_each_day():
    """Two consecutive live days produced two different formats, because
    nothing had ever fixed one: "32C / 90F (High) | 18C / 64F (Low)" on the
    26th and "34C / 93F high, 18C / 64F low" on the 27th. The shape is now the
    same whatever the numbers are."""
    shape = re.compile(
        r"^-?\d+\u00b0C / -?\d+\u00b0F high, -?\d+\u00b0C / -?\d+\u00b0F low$"
    )
    for high, low in [(32.3, 18.0), (33.5, 18.5), (-2.5, -7.5), (40.0, 25.0)]:
        assert shape.match(format_temp_high_low(high, low)), (high, low)


# ---------------------------------------------------------------------------
# observed_convection — what a rain forecast is scored against (item 53).
# ---------------------------------------------------------------------------


def test_observed_convection_counts_station_precipitation_the_reanalysis_missed():
    # The 2026-08-29 shape: reanalysis 0.0 mm, no thunder heard, and the
    # airport reporting -RA. Scored dry before item 53.
    actual = DailyActual(rain=False, precip_mm=0.0, thunder=False, precipitation=True)
    assert actual.observed_convection() is True


def test_observed_convection_unobserved_precipitation_is_not_a_dry_day():
    # None means "no observation", never "no rain" — same three-valued rule
    # as thunder. A deployment with no METAR station scores as it always did.
    actual = DailyActual(rain=False, precip_mm=0.0, thunder=None, precipitation=None)
    assert actual.observed_convection() is False


def test_observed_convection_still_honours_thunder_alone():
    actual = DailyActual(rain=False, precip_mm=0.0, thunder=True, precipitation=False)
    assert actual.observed_convection() is True


def test_observed_convection_station_reporting_a_genuinely_dry_day():
    actual = DailyActual(rain=False, precip_mm=0.0, thunder=False, precipitation=False)
    assert actual.observed_convection() is False


# --- summaries that contradict their own row — ROADMAP item 142, finding 3 ---


def test_a_cold_bias_claim_against_a_warm_running_row_is_caught():
    """ICON at Day+0 carries `avg_temp_low_error_c_10: -2.8` — negative, so by
    this project's convention (observed minus forecast) the model predicted
    HIGHER than happened, i.e. it ran WARM — beside a stored summary reading
    "a persistent nocturnal cold bias on minimum temperatures". LONG-RUN
    REVIEW agrees with the sign and contradicts the summary.

    Still stored and still sent on 2026-09-16, which is why this is a check
    and not a one-off correction: item 149 changed how summaries are WRITTEN,
    and a stale one that stops being refreshed would otherwise be frozen.
    """
    from openlocalweather.models import summary_contradicts_its_row

    assert summary_contradicts_its_row(
        "At Day+0, solid on precipitation detection with a persistent nocturnal "
        "cold bias on minimum temperatures.",
        low_error_c=-2.8,
    ) is True


def test_the_same_words_with_the_matching_sign_are_left_alone():
    """The direction words are only wrong against a sign. A model that really
    does run cold on lows has a POSITIVE error, and that summary is correct."""
    from openlocalweather.models import summary_contradicts_its_row

    assert summary_contradicts_its_row(
        "At Day+0, a persistent nocturnal cold bias on minimum temperatures.",
        low_error_c=+2.8,
    ) is False


def test_a_summary_making_no_directional_claim_is_not_touched():
    """Conservative by design: this withholds a summary from the forecaster,
    so a false positive silences a true statement. Anything it cannot read
    confidently is left alone."""
    from openlocalweather.models import summary_contradicts_its_row

    for text in (
        "At Day+0, solid on precipitation detection and timing.",
        "No findings established at this lead.",
        "",
        None,
    ):
        assert summary_contradicts_its_row(text, low_error_c=-2.8) is False


def test_no_sign_means_no_judgement():
    """A row with no measured error cannot contradict anything."""
    from openlocalweather.models import summary_contradicts_its_row

    assert summary_contradicts_its_row(
        "a persistent nocturnal cold bias", low_error_c=None
    ) is False


def test_one_wrong_clause_beside_a_right_one_still_counts():
    """UKMO Day+3, stored: "over-forecasts overnight minimum temperatures and
    under-forecasts wind", against low_err +1.63 and wind_err +15.41.

    The WIND half is correct — a positive error is under-forecasting. The
    TEMPERATURE half is backwards. Reading direction and quantity across the
    whole summary would pair the first direction with the second quantity and
    get the wrong answer twice, which is why the check reads clause by clause.
    """
    from openlocalweather.models import summary_contradicts_its_row

    assert summary_contradicts_its_row(
        "At Day+3, reliable on extended rainfall trends, but over-forecasts "
        "overnight minimum temperatures and under-forecasts wind.",
        low_error_c=1.63, wind_error_kmh=15.41,
    ) is True


def test_a_wind_claim_is_checked_against_the_wind_error():
    """best_match Day+3: "peak wind speeds are occasionally over-forecasted"
    against a wind error of +0.78, which is UNDER-forecasting.

    The first version of this check looked only at temperature and missed it.
    Found by running the check against the real stored record rather than
    against the examples it was written from.
    """
    from openlocalweather.models import summary_contradicts_its_row

    assert summary_contradicts_its_row(
        "At Day+3, strong rain verification, though peak wind speeds are "
        "occasionally over-forecasted.",
        low_error_c=-0.55, wind_error_kmh=0.78,
    ) is True


def test_a_summary_right_about_both_quantities_survives():
    """The negative control. 15 of the record's 18 summaries are kept, and a
    check that withheld them all would pass every test above."""
    from openlocalweather.models import summary_contradicts_its_row

    assert summary_contradicts_its_row(
        "At Day+0, under-forecasts peak wind and runs cold on overnight lows.",
        low_error_c=+2.0, wind_error_kmh=+12.0,
    ) is False
