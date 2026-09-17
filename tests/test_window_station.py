"""ROADMAP item 139: the +24 h window is scored against the station too."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from openlocalweather.fetch.metar import _weather_from_reports, station_weather_within
from openlocalweather.fetch.open_meteo import bucket_hourly_window
from openlocalweather.models import IssuancePredictions, ModelPrediction, ModelPredictionsByLead
from openlocalweather.verify.scoring import verify_closed_windows

from tests.test_pipeline import log_entry

TZ = "Africa/Nairobi"


def _utc(y, mo, d, h, mi=0):
    # A local Nairobi wall-clock instant, as the archive would stamp it (UTC).
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc)


def _reports(*items):
    return [(_utc(*when), raw) for when, raw in items]


def test_a_wet_report_inside_the_window_makes_it_wet_and_sets_the_onset():
    reports = _reports(((2026, 9, 15, 15), "HKKI 151200Z 09010KT 9999 -RA SCT020 28/18 Q1013"))
    seen = station_weather_within(reports, datetime(2026, 9, 15, 6), 24, TZ)
    assert seen is not None
    assert seen.precipitation is True
    assert seen.precipitation_onset == "15:00"
    assert seen.thunder is False


def test_a_wet_report_before_the_window_opened_does_not_count():
    reports = _reports(
        ((2026, 9, 15, 3), "HKKI 150000Z 09010KT 9999 -RA SCT020 22/18 Q1013"),
        ((2026, 9, 15, 9), "HKKI 150600Z 09010KT 9999 SCT020 26/18 Q1013"),
    )
    seen = station_weather_within(reports, datetime(2026, 9, 15, 6), 24, TZ)
    assert seen is not None and seen.precipitation is False and seen.precipitation_onset is None


def test_no_report_in_the_window_is_not_observed():
    reports = _reports(((2026, 9, 13, 9), "HKKI 130600Z 09010KT 9999 -RA SCT020 26/18 Q1013"))
    assert station_weather_within(reports, datetime(2026, 9, 15, 6), 24, TZ) is None
    assert station_weather_within(None, datetime(2026, 9, 15, 6), 24, TZ) is None


def test_a_window_over_a_whole_day_sees_what_the_calendar_day_sees():
    # The property the comparison rests on, on the station side: identical
    # hours, identical station weather, whichever reduction produced it.
    reports = _reports(
        ((2026, 9, 15, 2), "HKKI 142300Z 09010KT 9999 SCT020 22/18 Q1013"),
        ((2026, 9, 15, 14), "HKKI 151100Z 09010KT 9999 TSRA SCT020 28/18 Q1013"),
        ((2026, 9, 15, 20), "HKKI 151700Z 09010KT 9999 -RA SCT020 24/18 Q1013"),
        ((2026, 9, 16, 1), "HKKI 152200Z 09010KT 9999 SCT020 22/18 Q1013"),
    )
    by_day = _weather_from_reports(reports, date(2026, 9, 15), date(2026, 9, 15), TZ)
    window = station_weather_within(reports, datetime(2026, 9, 15, 0), 24, TZ)
    day = by_day[date(2026, 9, 15)]
    assert (window.thunder, window.precipitation, window.precipitation_onset) == (
        day.thunder, day.precipitation, day.precipitation_onset
    )


def _dry_archive_window():
    # 0.4 mm over the window: under the rain threshold, exactly the real
    # 2026-09-15 shape that scored every model wrong.
    times = [f"2026-09-15T{h:02d}:00" for h in range(24)] + [f"2026-09-16T{h:02d}:00" for h in range(24)]
    precip = [0.0] * 48
    precip[15] = 0.1
    precip[16] = 0.3
    return {"hourly": {
        "time": times,
        "temperature_2m": [24.0] * 48,
        "precipitation": precip,
        "windgusts_10m": [20.0] * 48,
        "pressure_msl": [1013.0] * 48,
    }}


def _entry_with_window(rain: bool):
    e = log_entry(date(2026, 9, 15), day0=[ModelPrediction(model="gfs_seamless", rain=rain)])
    e.prediction_rows = [IssuancePredictions(
        issued_at=datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc),
        predictions=ModelPredictionsByLead(day0=[ModelPrediction(model="gfs_seamless", rain=rain)]),
        window_predictions=[ModelPrediction(model="gfs_seamless", rain=rain)],
        window_opened_local=datetime(2026, 9, 15, 6),
    )]
    return e


def test_the_window_scores_rain_against_the_station_like_the_calendar_day():
    archive = _dry_archive_window()
    assert bucket_hourly_window(archive, start=datetime(2026, 9, 15, 6)).rain is False
    reports = _reports(((2026, 9, 15, 15), "HKKI 151200Z 09010KT 9999 -RA SCT020 28/18 Q1013"))

    without = _entry_with_window(rain=True)
    verify_closed_windows(without, archive, today=date(2026, 9, 17))
    assert without.prediction_rows[0].window_scores["gfs_seamless"].rain_correct is False

    with_station = _entry_with_window(rain=True)
    verify_closed_windows(
        with_station, archive, today=date(2026, 9, 17), station_reports=reports, timezone_name=TZ
    )
    assert with_station.prediction_rows[0].window_scores["gfs_seamless"].rain_correct is True


def test_a_scored_row_is_left_alone_unless_forced():
    archive = _dry_archive_window()
    reports = _reports(((2026, 9, 15, 15), "HKKI 151200Z 09010KT 9999 -RA SCT020 28/18 Q1013"))
    e = _entry_with_window(rain=True)
    verify_closed_windows(e, archive, today=date(2026, 9, 17))
    stamp = e.prediction_rows[0].window_verified_at
    assert e.prediction_rows[0].window_scores["gfs_seamless"].rain_correct is False

    assert verify_closed_windows(
        e, archive, today=date(2026, 9, 17), station_reports=reports, timezone_name=TZ
    ) is False, "idempotent by stamp: a scored row does not move on its own"
    assert e.prediction_rows[0].window_verified_at == stamp

    assert verify_closed_windows(
        e, archive, today=date(2026, 9, 17), station_reports=reports, timezone_name=TZ, force=True
    ) is True
    assert e.prediction_rows[0].window_scores["gfs_seamless"].rain_correct is True
