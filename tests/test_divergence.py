"""ROADMAP item 45 — how far apart the two instruments actually are.

Written before there is data to point it at, deliberately. The sequencing
says store the readings, let divergence accumulate, then decide with numbers;
this is the thing that produces the numbers, and writing it now means the
decision later is a reading rather than a fresh piece of analysis by whoever
happens to be there.

It answers, and refuses to answer beyond, exactly one question: where do the
station and the reanalysis disagree, and by how much. It never says which is
right — item 45's whole point is that there is no yardstick to decide that.
"""

from datetime import date

from openlocalweather.divergence import compare_sources
from openlocalweather.models import DailyActual


def _day(**kw) -> DailyActual:
    base = dict(rain=False)
    base.update(kw)
    return DailyActual(**base)


def test_a_variable_reports_signed_and_absolute_error():
    """Signed AND absolute, because they answer different questions. A
    systematic +2 degree offset is a calibration difference worth correcting
    for; the same magnitude scattered either way is noise, and a mean of zero
    would hide it."""
    actuals = {
        date(2026, 8, 1): _day(high_c=28.0, station_high_c=30.0),
        date(2026, 8, 2): _day(high_c=30.0, station_high_c=28.0),
    }
    d = compare_sources(actuals)
    high = next(v for v in d.variables if v.variable == "high_c")

    assert high.days == 2
    assert high.mean_signed == 0.0, "the two offsets cancel"
    assert high.mean_absolute == 2.0, "but they were 2 degrees apart both days"
    assert high.max_absolute == 2.0


def test_the_sign_is_station_minus_archive():
    """Stated in one direction and asserted, because a report whose sign is
    ambiguous is worse than no report — the reader would have to go and check
    the source to use it."""
    actuals = {date(2026, 8, 1): _day(high_c=28.0, station_high_c=30.0)}
    high = next(v for v in compare_sources(actuals).variables if v.variable == "high_c")
    assert high.mean_signed == 2.0


def test_only_days_where_both_reported_are_compared():
    """A day the station missed is not evidence of agreement or disagreement,
    and counting it as either would move the mean toward zero for free."""
    actuals = {
        date(2026, 8, 1): _day(high_c=28.0, station_high_c=30.0),
        date(2026, 8, 2): _day(high_c=30.0, station_high_c=None),
        date(2026, 8, 3): _day(high_c=None, station_high_c=29.0),
    }
    high = next(v for v in compare_sources(actuals).variables if v.variable == "high_c")
    assert high.days == 1


def test_a_variable_with_no_overlap_reports_zero_days_not_agreement():
    actuals = {date(2026, 8, 1): _day(high_c=28.0)}
    high = next(v for v in compare_sources(actuals).variables if v.variable == "high_c")
    assert high.days == 0
    assert high.mean_signed is None
    assert high.mean_absolute is None


def test_rain_is_a_contingency_table_not_a_mean():
    """Averaging booleans would produce a number that reads like an error
    magnitude and is not one. What matters is WHICH WAY they disagree: the
    station seeing rain the reanalysis missed is item 42's whole finding, and
    the reverse is a different event with a different explanation."""
    actuals = {
        date(2026, 8, 1): _day(rain=True, precipitation=True),
        date(2026, 8, 2): _day(rain=False, precipitation=False, thunder=False),
        date(2026, 8, 3): _day(rain=False, precipitation=True),
        date(2026, 8, 4): _day(rain=True, precipitation=False, thunder=False),
    }
    o = compare_sources(actuals).occurrence

    assert o.days == 4
    assert o.both_wet == 1
    assert o.both_dry == 1
    assert o.station_only == 1
    assert o.archive_only == 1


def test_thunder_counts_as_the_station_seeing_weather():
    """A dry thunderstorm is the station observing convection the reanalysis
    scored as nothing — the exact case item 42 exists for."""
    actuals = {date(2026, 8, 1): _day(rain=False, precipitation=False, thunder=True)}
    assert compare_sources(actuals).occurrence.station_only == 1


def test_a_day_the_station_did_not_report_is_outside_the_table():
    """Three-valued, as everywhere else here. `precipitation is None` means no
    observation, and reading it as "saw nothing" would manufacture agreement
    with every dry reanalysis day."""
    actuals = {
        date(2026, 8, 1): _day(rain=False, precipitation=None, thunder=None),
        date(2026, 8, 2): _day(rain=False, precipitation=False, thunder=False),
    }
    assert compare_sources(actuals).occurrence.days == 1


def test_an_empty_record_reports_nothing_rather_than_agreement():
    d = compare_sources({})
    assert d.occurrence.days == 0
    assert all(v.days == 0 for v in d.variables)
