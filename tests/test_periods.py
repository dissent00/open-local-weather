"""Each 24-hour period counts once — ROADMAP item 139, stage 1.

A deployment may run 1 forecast a day or 100. Every forecast is scored on
its own window, and the record counts each PERIOD (the local date a forecast
was issued) once, averaging that period's scores, so running more forecasts
cannot move a published figure.
"""

from datetime import date, datetime, timezone

import pytest

from openlocalweather.models import DailyLogEntry, IssuancePredictions, VerificationScore
from openlocalweather.verify.scoring import summarize_periods, window_scores_by_period


def _score(rain: bool, high: float | None = None, cloud: float | None = None, brier: float | None = None):
    return VerificationScore(rain_correct=rain, high_error_c=high, cloud_error_pct=cloud, rain_brier=brier)


def test_one_forecast_per_period_gives_exactly_the_per_check_figures():
    """Continuity: a deployment running once a day sees no change. The rain
    percentage is formed as the old one was, 100 * count / n, because
    100 * (count / n) differs in the last bit (1 of 3)."""
    scores = [_score(True, 1.0, 10.0), _score(False, -0.5, None), _score(False, 2.0, 30.0)]

    got = summarize_periods([[s] for s in scores])

    assert got.checks_found == 3
    assert got.rain_pct == 100 * 1 / 3
    assert got.high_err == pytest.approx((1.0 - 0.5 + 2.0) / 3, abs=0)
    assert got.cloud_err == 20.0
    assert got.cloud_checks == 2


def test_adding_forecasts_to_a_period_cannot_move_a_figure_unless_their_scores_differ():
    one = summarize_periods([[_score(True, 1.0)], [_score(False, 3.0)]])
    many = summarize_periods([[_score(True, 1.0)] * 100, [_score(False, 3.0)] * 7])

    assert many == one


def test_a_busy_period_weighs_what_a_quiet_one_does():
    """Three forecasts on one day, one on the next: each DAY is half."""
    got = summarize_periods([
        [_score(True, 0.0), _score(True, 0.0), _score(False, 3.0)],
        [_score(False, 1.0)],
    ])

    assert got.checks_found == 2
    assert got.rain_pct == pytest.approx(100 * (2 / 3 + 0) / 2)
    assert got.high_err == pytest.approx((1.0 + 1.0) / 2)


def test_the_window_takes_the_newest_periods_and_skips_empty_ones():
    got = summarize_periods([[_score(True, 1.0)], [], [_score(False, 5.0)], [_score(False, 9.0)]], window_size=2)

    assert got.checks_found == 2
    assert got.high_err == 3.0


def _entry(day: date, *rows: dict[str, VerificationScore] | None) -> DailyLogEntry:
    """An entry whose prediction rows carry these window scores; None is a
    row whose window has not been scored."""
    stamp = datetime(2026, 9, 28, 3, 1, tzinfo=timezone.utc)
    # Constructed, not validated: the function reads the rows and nothing else.
    return DailyLogEntry.model_construct(
        date=day,
        prediction_rows=[
            IssuancePredictions(
                issued_at=stamp,
                window_scores=scores or {},
                window_verified_at=stamp if scores is not None else None,
            )
            for scores in rows
        ],
    )


def test_every_scored_window_of_a_day_is_in_its_period_newest_period_first():
    day1, day2, day3 = date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22)
    entries = {
        day1: _entry(day1, {"gfs": _score(True)}),
        day2: _entry(day2, {"gfs": _score(True)}, None, {"gfs": _score(False), "icon": _score(True)}),
        day3: _entry(day3, None),
    }

    got = window_scores_by_period(entries.get, [day1, day2, day3], "gfs")

    assert [d for d, _ in got] == [day2, day1], "newest first; a day with nothing scored is no period"
    assert [s.rain_correct for s in got[0][1]] == [True, False], "every scored row of the day, not row 0"
