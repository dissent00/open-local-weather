from datetime import date

from openlocalweather.dates import (
    add_days,
    format_date,
    parse_date,
    prediction_row_date_for_target,
)


def test_add_days_forward_and_backward():
    d = date(2026, 8, 11)
    assert add_days(d, 1) == date(2026, 8, 12)
    assert add_days(d, -1) == date(2026, 8, 10)
    assert add_days(d, 0) == d


def test_format_and_parse_date_round_trip():
    d = date(2026, 1, 5)
    assert parse_date(format_date(d)) == d
    assert format_date(d) == "2026-01-05"


def test_prediction_row_date_for_target_day0():
    # A Day+0 prediction targeting `target` was made ON `target`.
    target = date(2026, 8, 11)
    assert prediction_row_date_for_target(target, 0) == target


def test_prediction_row_date_for_target_lead_time():
    # A Day+3 prediction targeting Aug 11 was made on Aug 8.
    target = date(2026, 8, 11)
    assert prediction_row_date_for_target(target, 3) == date(2026, 8, 8)
    # A Day+7 prediction targeting Aug 11 was made on Aug 4.
    assert prediction_row_date_for_target(target, 7) == date(2026, 8, 4)


def test_prediction_row_date_across_month_boundary():
    target = date(2026, 3, 2)
    assert prediction_row_date_for_target(target, 7) == date(2026, 2, 23)
