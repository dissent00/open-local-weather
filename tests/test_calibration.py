"""ROADMAP item 126 — correcting the gust for bias the record has measured."""

from openlocalweather.calibration import calibrated_gust_consensus, gust_corrections
from openlocalweather.models import ModelPrediction


class Entry:
    """The three fields the calibration reads off a TrackRecordEntry."""

    def __init__(self, model, lead_time_days, avg_wind_error_kmh_10, checks_in_window_10):
        self.model = model
        self.lead_time_days = lead_time_days
        self.avg_wind_error_kmh_10 = avg_wind_error_kmh_10
        self.checks_in_window_10 = checks_in_window_10


def entry(model="ecmwf_ifs025", lead=0, error=12.96, checks=10):
    return Entry(model, lead, error, checks)


def test_a_model_that_came_in_under_is_corrected_upward():
    """THE SIGN IS THE WHOLE THING.

    `avg_wind_error_kmh_10` is `actual - predicted`, so a positive value is a
    model whose gusts were too LOW and the correction is ADDED. Subtracting
    would double the bias rather than remove it, in the direction the record
    already leans — the hardest kind of wrong to notice, and the direction 43
    stored notes had backwards before prompt rule 218 existed.

    Asserted as a VALUE, not a direction, so a flipped sign cannot pass by
    landing on some other plausible number.
    """
    corrections = gust_corrections([entry(error=12.96)])

    assert corrections == {"ecmwf_ifs025": 12.96}
    assert calibrated_gust_consensus(
        [ModelPrediction(model="ecmwf_ifs025", rain=False, wind_kmh=30.0)], corrections
    ) == 42.96


def test_a_model_that_came_in_over_is_corrected_downward():
    """The same rule with the sign the record has never yet produced here.

    Every model at this deployment under-forecasts the gust, so a test built
    only from real figures would pass just as well against `abs()`.
    """
    corrections = gust_corrections([entry(error=-4.0)])

    assert calibrated_gust_consensus(
        [ModelPrediction(model="ecmwf_ifs025", rain=False, wind_kmh=30.0)], corrections
    ) == 26.0


def test_too_few_checks_is_absent_rather_than_zero():
    """Absence is absence. A zero would be a claim that the model is
    unbiased, which is a measurement nobody made."""
    assert gust_corrections([entry(checks=9)]) == {}
    assert gust_corrections([entry(checks=None)]) == {}


def test_a_model_with_no_measured_wind_error_is_absent():
    """The met service files rain and temperature and no wind at all."""
    assert gust_corrections([entry(model="kenya_met", error=None)]) == {}


def test_another_lead_time_never_leaks_into_the_day0_correction():
    """A Day+3 error has a different structure and has not been validated.
    Without this guard the map would take whichever row came last."""
    corrections = gust_corrections([entry(lead=3, error=99.0, checks=30)])

    assert corrections == {}


def test_an_uncorrected_model_is_left_out_of_the_consensus():
    """A mean mixing corrected and uncorrected members is neither, and it
    would move whenever one of them crossed the check threshold."""
    corrections = gust_corrections([entry(model="a", error=10.0), entry(model="b", checks=9)])
    predictions = [
        ModelPrediction(model="a", rain=False, wind_kmh=30.0),
        ModelPrediction(model="b", rain=False, wind_kmh=10.0),
    ]

    # 40.0, not the 30.0 a raw-mean-then-correct would give.
    assert calibrated_gust_consensus(predictions, corrections) == 40.0


def test_no_corrected_member_is_none_and_not_the_raw_mean():
    """None so the caller has to decide what an uncalibrated day means,
    rather than being handed a number that is not the thing its name says."""
    predictions = [ModelPrediction(model="b", rain=False, wind_kmh=10.0)]

    assert calibrated_gust_consensus(predictions, {}) is None
    assert calibrated_gust_consensus(predictions, None) is None
    assert calibrated_gust_consensus(predictions, {"a": 10.0}) is None


def test_a_model_with_no_gust_is_skipped_rather_than_counted():
    corrections = gust_corrections([entry(model="a", error=10.0), entry(model="b", error=10.0)])
    predictions = [
        ModelPrediction(model="a", rain=False, wind_kmh=None),
        ModelPrediction(model="b", rain=False, wind_kmh=20.0),
    ]

    assert calibrated_gust_consensus(predictions, corrections) == 30.0
