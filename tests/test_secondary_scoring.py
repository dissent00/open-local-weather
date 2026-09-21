"""The second point's forecast, scored — ROADMAP item 6.

The no-op this closes: the pipeline has fetched the secondary point's actuals
every day since the project started, `actuals_cache` stored them faithfully,
and nothing ever read them. Meanwhile the secondary's peak gust is published
in every forecast, in the section a boater acts on.

Measured before building, over the 10 days where a published gulf gust and a
cached gulf actual both exist: mean error +4.99 km/h and MAE 9.33, against
4.68 for the primary. The sign is the record's own, observed minus predicted,
so the lake blew harder than the forecast said. Twice as wrong as the town,
and biased toward under-forecasting, which for wind on water is the dangerous
direction.
"""

import pytest

from datetime import date, datetime, timezone

from openlocalweather.models import (
    DailyActual,
    DailyLogEntry,
    IssuancePredictions,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)
from openlocalweather.verify.scoring import verify_secondary_predictions


def entry_with(predictions, day=date(2026, 9, 20)) -> DailyLogEntry:
    return DailyLogEntry(
        date=day,
        rain_expected="Unlikely",
        temp_high_c=28.0,
        temp_low_c=18.0,
        temp_high_low_display="28°C / 82°F",
        mslp_trend_24h="steady",
        synoptic_pattern="ridge",
        narrative_markdown="## Overview\n\nWarm.",
        model_predictions=ModelPredictionsByLead(
            day0=[ModelPrediction(model="gfs_seamless", rain=False)]
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime(2026, 9, 20, 3, 6),
            llm_provider="gemini",
            llm_model="gemini-3.6-flash",
            pipeline_version="0.1.0",
        ),
        prediction_rows=[
            IssuancePredictions(
                issued_at=datetime(2026, 9, 20, 3, 1, tzinfo=timezone.utc),
                secondary_predictions=predictions,
            )
        ],
    )


LAKE = {date(2026, 9, 20): DailyActual(rain=False, peak_wind_kmh=34.9, high_c=29.2)}
TOWN = {date(2026, 9, 20): DailyActual(rain=False, peak_wind_kmh=37.4, high_c=32.5)}


def test_a_finished_day_is_scored_against_the_lakes_own_actual():
    entry = entry_with([ModelPrediction(model="gfs_seamless", rain=False, wind_kmh=26.5)])

    assert verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 21))

    row = entry.prediction_rows[0]
    assert row.secondary_verified_at is not None
    # 26.5 published against 34.9 observed. The record's sign convention is
    # OBSERVED MINUS PREDICTED — see `_diff` — so a positive error is a wind
    # the forecast did not see coming, which is the direction the ten-day
    # measurement found the lake erring in.
    assert row.secondary_scores["gfs_seamless"].wind_error_kmh == pytest.approx(8.4)


def test_it_is_the_lakes_actual_and_not_the_towns():
    """The mutation this exists for. Both dicts are keyed by the same dates
    and hold the same shape, so scoring the lake against the town's weather
    would produce numbers that look entirely plausible and are about the
    wrong place — which is the failure item 45 spent a page on for a
    different pair."""
    entry = entry_with([ModelPrediction(model="gfs_seamless", rain=False, wind_kmh=26.5)])
    verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 21))

    # 34.9 is the lake's observed gust; the town's was 37.4, which would give
    # 10.9 and look just as plausible.
    assert entry.prediction_rows[0].secondary_scores[
        "gfs_seamless"
    ].wind_error_kmh == pytest.approx(8.4), "scored against the primary point's actual"


def test_a_day_that_has_not_finished_is_not_scored():
    """A Day+0 claim covers the whole local day. Scoring it at midday scores
    a forecast against a fraction of its own target."""
    entry = entry_with([ModelPrediction(model="gfs_seamless", rain=False, wind_kmh=26.5)])

    assert not verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 20))
    assert entry.prediction_rows[0].secondary_scores == {}


def test_an_already_scored_row_is_left_alone():
    entry = entry_with([ModelPrediction(model="gfs_seamless", rain=False, wind_kmh=26.5)])
    assert verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 21))
    stamp = entry.prediction_rows[0].secondary_verified_at

    assert not verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 21))
    assert entry.prediction_rows[0].secondary_verified_at == stamp


def test_no_actual_for_that_day_writes_nothing():
    """A day the lake's archive has not answered for is unscored, not scored
    as zero error."""
    entry = entry_with([ModelPrediction(model="gfs_seamless", rain=False, wind_kmh=26.5)])

    assert not verify_secondary_predictions(entry, {}, today=date(2026, 9, 21))
    assert entry.prediction_rows[0].secondary_scores == {}


def test_a_row_written_before_this_existed_is_not_scoreable():
    """Every row committed before 2026-09-21 carries no secondary
    predictions. It must be skipped rather than scored as an empty set, or
    the record would show the lake verified on days nothing was stored."""
    entry = entry_with([])

    assert not verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 21))
    assert entry.prediction_rows[0].secondary_verified_at is None


def test_predictions_with_no_model_name_do_not_stamp_the_row_verified():
    """The guard the mutation pass found untested. `model` is a required
    string but an empty one validates, and a row whose predictions all carry
    one would score to an empty dict. Stamping `secondary_verified_at` over
    that would mark the day verified with nothing in it, and no later run
    would revisit it — the same failure as scoring a row that was never
    given predictions, reached by a different road."""
    entry = entry_with([ModelPrediction(model="", rain=False, wind_kmh=26.5)])

    assert not verify_secondary_predictions(entry, LAKE, today=date(2026, 9, 21))
    assert entry.prediction_rows[0].secondary_verified_at is None
    assert entry.prediction_rows[0].secondary_scores == {}
