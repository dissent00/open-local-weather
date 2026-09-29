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


def test_every_forecasts_day_n_claim_is_scored_in_its_period():
    """Day+3 and Day+7 name a day, so they stay calendar claims; but EVERY
    forecast's is scored, not row 0's, grouped by the date it was issued."""
    from openlocalweather.models import DailyActual, ModelPrediction, ModelPredictionsByLead
    from openlocalweather.verify.scoring import calendar_scores_by_period

    def row(high):
        return IssuancePredictions(
            issued_at=datetime(2026, 9, 20, 3, 1, tzinfo=timezone.utc),
            predictions=ModelPredictionsByLead(day3=[ModelPrediction(model="gfs", rain=False, high_c=high)]),
        )

    issued, later = date(2026, 9, 20), date(2026, 9, 21)
    entries = {
        issued: DailyLogEntry.model_construct(date=issued, prediction_rows=[row(30.0), row(28.0)]),
        later: DailyLogEntry.model_construct(date=later, prediction_rows=[row(31.0)]),
    }
    # 09-23 observed; 09-24, the later forecast's target, not yet.
    actuals = {date(2026, 9, 23): DailyActual(rain=False, high_c=29.0)}

    got = calendar_scores_by_period(entries.get, [issued, later], actuals, "gfs", 3)

    assert [d for d, _ in got] == [issued], "a target not yet observed is no score"
    assert [s.high_error_c for s in got[0][1]] == [-1.0, 1.0], "both of the day's forecasts"


# --- Stage 3a: the track record over periods ------------------------------


def _row(window: dict[str, VerificationScore] | None = None, day3_high: float | None = None):
    """One forecast: its scored window, and a Day+3 high for gfs."""
    from openlocalweather.models import ModelPrediction, ModelPredictionsByLead

    stamp = datetime(2026, 9, 28, 3, 1, tzinfo=timezone.utc)
    return IssuancePredictions(
        issued_at=stamp,
        window_scores=window or {},
        window_verified_at=stamp if window is not None else None,
        predictions=ModelPredictionsByLead(
            day3=[ModelPrediction(model="gfs", rain=False, high_c=day3_high)] if day3_high is not None else [],
        ),
    )


def _period_record(entries, actuals=None, prior=None):
    from openlocalweather.models import TrackRecord
    from openlocalweather.verify.pipeline import derive_period_track_record

    return derive_period_track_record(
        log_lookup=entries.get,
        log_dates=sorted(entries),
        actuals_primary=actuals or {},
        prior_track_record=prior or TrackRecord(generated_at_utc=datetime(2026, 9, 28, tzinfo=timezone.utc), entries=[]),
        today=date(2026, 9, 29),
        models=["gfs"],
    )


def _gfs(record, lead):
    return next(e for e in record.entries if e.model == "gfs" and e.lead_time_days == lead)


def test_the_day0_record_is_the_windows_counted_each_period_once():
    d1, d2 = date(2026, 9, 20), date(2026, 9, 21)
    entries = {
        d1: DailyLogEntry.model_construct(date=d1, prediction_rows=[_row({"gfs": _score(True, 1.0)})]),
        d2: DailyLogEntry.model_construct(date=d2, prediction_rows=[
            _row({"gfs": _score(True, 0.0)}), _row({"gfs": _score(False, 2.0)}),
        ]),
    }

    got = _gfs(_period_record(entries), 0)

    assert got.checks_in_window_10 == 2
    assert got.all_time_checks == 2
    assert got.rolling_10_rain_pct == pytest.approx(100 * (1 + 0.5) / 2)
    assert got.avg_temp_high_error_c_10 == pytest.approx((1.0 + 1.0) / 2)
    assert got.all_time_correct == pytest.approx(1.5), "the sum of each period's share"
    assert got.all_time_earliest_target_date == d1
    assert got.last_verified_target_date == d2


def test_the_day3_record_scores_every_forecasts_claim_against_its_named_day():
    from openlocalweather.models import DailyActual

    d1 = date(2026, 9, 20)
    entries = {d1: DailyLogEntry.model_construct(date=d1, prediction_rows=[_row(day3_high=30.0), _row(day3_high=28.0)])}
    actuals = {date(2026, 9, 23): DailyActual(rain=False, high_c=29.0)}

    got = _gfs(_period_record(entries, actuals), 3)

    assert got.all_time_checks == 1
    assert got.avg_temp_high_error_c_10 == pytest.approx(0.0), "-1 and +1, one period"
    assert got.all_time_earliest_target_date == date(2026, 9, 23)
    assert got.avg_onset_error_hrs_10 is None, "onset is Day+0's alone"


def test_more_forecasts_in_a_period_move_no_figure_of_the_record():
    d1, d2 = date(2026, 9, 20), date(2026, 9, 21)

    def entries(copies):
        return {
            d1: DailyLogEntry.model_construct(date=d1, prediction_rows=[_row({"gfs": _score(True, 1.0, 20.0, 0.1)})] * copies),
            d2: DailyLogEntry.model_construct(date=d2, prediction_rows=[_row({"gfs": _score(False, -2.0, None, 0.6)})] * copies),
        }

    one = _gfs(_period_record(entries(1)), 0).model_dump(exclude={"last_updated"})
    many = _gfs(_period_record(entries(7)), 0).model_dump(exclude={"last_updated"})

    assert many == one


def test_the_written_summary_is_carried_and_a_shrinking_all_time_is_refused():
    from openlocalweather.models import TrackRecord, TrackRecordEntry

    d1 = date(2026, 9, 20)
    entries = {d1: DailyLogEntry.model_construct(date=d1, prediction_rows=[_row({"gfs": _score(True)})])}
    prior = TrackRecord(generated_at_utc=datetime(2026, 9, 28, tzinfo=timezone.utc), entries=[TrackRecordEntry(
        model="gfs", lead_time_days=0, skill_profile_summary="At Day+0, runs warm.", notes="n",
        all_time_checks=5, all_time_correct=4, all_time_rain_pct=80.0,
    )])

    got = _gfs(_period_record(entries, prior=prior), 0)

    assert got.skill_profile_summary == "At Day+0, runs warm."
    assert got.notes == "n"
    assert (got.all_time_checks, got.all_time_correct, got.all_time_rain_pct) == (5, 4, 80.0)
