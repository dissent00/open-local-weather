"""ROADMAP item 45, trap 2 — which instrument supplied which value, per day.

The prerequisite for everything else in that item, and item 44 needs it too.
Today the METAR/archive split is implicit in `_apply_station_observations`,
so an unexplained dip in the accuracy record cannot be traced: nothing stored
says whether a given day was scored against a station that was reporting or
one that was down.

It also matters for a reason the item found later: the record's rain figures
move when the SOURCE SET changes, not only when the weather does. Item 53.1
moved every model about five points in a day by adding one source. Without a
per-day stamp, that is indistinguishable from the models getting worse.
"""

from datetime import date

import pytest

from openlocalweather.models import (
    SOURCE_REANALYSIS,
    SOURCE_STATION,
    DailyActual,
    confidence_of,
)


def test_a_reanalysis_only_day_says_so():
    a = DailyActual(rain=True, high_c=28.0, provenance={"rain": SOURCE_REANALYSIS})
    assert a.provenance["rain"] == SOURCE_REANALYSIS


def test_an_entry_written_before_this_existed_is_unrecorded_not_empty():
    """Three-valued, the same rule as degradations and thunder. `None` means
    the run was never asked; an empty dict would claim we looked and found no
    sources, which is never true of a stored day — every one of them has at
    least a reanalysis `rain`."""
    assert DailyActual(rain=False).provenance is None


def test_confidence_is_derived_from_the_source_not_stored():
    """Item 45's ladder is a property of the INSTRUMENT, not of the day, so
    storing it per day would duplicate a fact and invite the two copies to
    disagree. The record stores which source; confidence is looked up."""
    assert confidence_of(SOURCE_STATION) == "reliable"
    assert confidence_of(SOURCE_REANALYSIS) == "possible"


def test_an_unknown_source_is_not_silently_trusted():
    """A fork adding its own source must not have it default to gold. Unknown
    is its own answer and the weakest one."""
    assert confidence_of("somebody_elses_sensor") == "unknown"


def test_the_stamp_is_per_variable_not_per_day():
    """A day is not scored against one instrument. Rain can come from the
    station while the temperature comes from the reanalysis, and trap 2 is
    about being able to see exactly that."""
    a = DailyActual(
        rain=True,
        high_c=28.0,
        thunder=True,
        provenance={
            "rain": SOURCE_REANALYSIS,
            "high_c": SOURCE_REANALYSIS,
            "thunder": SOURCE_STATION,
        },
    )
    assert a.provenance["thunder"] != a.provenance["high_c"]


def test_it_survives_a_json_round_trip():
    a = DailyActual(rain=True, provenance={"rain": SOURCE_REANALYSIS})
    assert DailyActual(**a.model_dump(mode="json")).provenance == {"rain": SOURCE_REANALYSIS}


def test_a_day_the_station_did_not_cover_carries_no_station_stamp():
    """The case the whole trap exists for: the station is truth for 300 days
    and down for 5, and those 5 must be identifiable rather than guessed at."""
    a = DailyActual(rain=True, provenance={"rain": SOURCE_REANALYSIS})
    assert SOURCE_STATION not in a.provenance.values()


# ---------------------------------------------------------------------------
# Stamped where the values are actually produced
# ---------------------------------------------------------------------------


def test_the_archive_stamps_every_field_it_supplied():
    from openlocalweather.fetch.open_meteo import bucket_hourly_by_date

    payload = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T01:00"],
            "temperature_2m": [20.0, 24.0],
            "precipitation": [0.0, 1.5],
            "wind_gusts_10m": [10.0, 30.0],
            "pressure_msl": [1010.0, 1008.0],
        }
    }
    actual = bucket_hourly_by_date(payload)[date(2026, 8, 11)]

    assert actual.provenance is not None
    for field in ("rain", "high_c", "low_c", "peak_wind_kmh", "mslp_trend", "precip_mm"):
        assert actual.provenance[field] == SOURCE_REANALYSIS, field
    # And nothing the archive cannot see is claimed by it.
    assert "thunder" not in actual.provenance


def test_a_field_the_archive_had_no_data_for_is_not_stamped():
    """A stamp says "this source supplied this value". Stamping a field that
    came back None would record an observation that was never made — the same
    absence-is-not-evidence rule the rest of this project follows."""
    from openlocalweather.fetch.open_meteo import bucket_hourly_by_date

    payload = {
        "hourly": {
            "time": ["2026-08-11T00:00"],
            "precipitation": [0.0],
        }
    }
    actual = bucket_hourly_by_date(payload)[date(2026, 8, 11)]
    assert "rain" in actual.provenance
    assert "high_c" not in actual.provenance


def test_the_station_stamps_only_the_days_it_covered(monkeypatch):
    """The case trap 2 exists for. Two days, one covered by the station and
    one not, and the record must be able to tell them apart afterwards."""
    from openlocalweather import pipeline
    from openlocalweather.fetch.metar import StationWeather

    covered, uncovered = date(2026, 8, 11), date(2026, 8, 12)
    actuals = {
        covered: DailyActual(rain=False, provenance={"rain": SOURCE_REANALYSIS}),
        uncovered: DailyActual(rain=False, provenance={"rain": SOURCE_REANALYSIS}),
    }
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz: (
            {covered: StationWeather(thunder=True, precipitation=False)},
            None,
        ),
    )

    class _Loc:
        metar_station_icao = "HKKI"
        timezone = "Africa/Nairobi"

    pipeline._apply_station_observations(actuals, _Loc())

    assert actuals[covered].provenance["thunder"] == SOURCE_STATION
    assert "thunder" not in actuals[uncovered].provenance
    # The archive's own stamps are not disturbed by the station's arrival.
    assert actuals[covered].provenance["rain"] == SOURCE_REANALYSIS


# ---------------------------------------------------------------------------
# ASOS structured readings, stored and NOT scored — item 45's sequencing
# ---------------------------------------------------------------------------


def test_the_station_temperature_and_wind_are_bucketed_per_local_day():
    """Item 45's sequencing: cross-check before replacement. Store the extra
    readings alongside, change nothing that is scored, and let divergence
    accumulate before deciding what precedence would earn.

    Fahrenheit and knots in, Celsius and km/h out — the record's units, so a
    later comparison against the reanalysis is not a unit conversion away from
    being wrong."""
    from openlocalweather.fetch.metar import station_readings_by_date

    rows = [
        # station, valid (UTC), tmpf, sknt
        ("HKKI", "2026-08-11 03:00", "68.0", "10.0"),
        ("HKKI", "2026-08-11 09:00", "86.0", "20.0"),
        ("HKKI", "2026-08-11 21:30", "60.8", "5.0"),
    ]
    by_date = station_readings_by_date(
        rows, date(2026, 8, 11), date(2026, 8, 12), "Africa/Nairobi"
    )

    day = by_date[date(2026, 8, 11)]
    assert day.high_c == 30.0      # 86F
    assert day.low_c == 20.0       # 68F
    assert day.peak_wind_kmh == pytest.approx(37.04, abs=0.01)  # 20 kt


def test_a_late_evening_report_belongs_to_the_local_day():
    """21:30Z is 00:30 the next day in Nairobi. The record is keyed on the
    reader's calendar day, and the existing weather bucketing already does
    this — the readings must agree with it or the two halves of one station's
    output would describe different days."""
    from openlocalweather.fetch.metar import station_readings_by_date

    rows = [("HKKI", "2026-08-11 21:30", "50.0", "1.0")]
    by_date = station_readings_by_date(
        rows, date(2026, 8, 11), date(2026, 8, 13), "Africa/Nairobi"
    )
    assert date(2026, 8, 11) not in by_date
    assert by_date[date(2026, 8, 12)].low_c == 10.0


def test_missing_and_trace_markers_are_absences_not_values():
    """`M` is the service's missing marker. Read as a number it becomes a
    confident reading, which is the p01i failure in another costume."""
    from openlocalweather.fetch.metar import station_readings_by_date

    rows = [
        ("HKKI", "2026-08-11 03:00", "M", "M"),
        ("HKKI", "2026-08-11 09:00", "77.0", "M"),
    ]
    day = station_readings_by_date(
        rows, date(2026, 8, 11), date(2026, 8, 11), "Africa/Nairobi"
    )[date(2026, 8, 11)]

    assert day.high_c == 25.0
    assert day.peak_wind_kmh is None, "no usable wind is None, never 0"


def test_a_day_with_nothing_usable_is_absent_entirely():
    from openlocalweather.fetch.metar import station_readings_by_date

    rows = [("HKKI", "2026-08-11 03:00", "M", "M")]
    assert station_readings_by_date(
        rows, date(2026, 8, 11), date(2026, 8, 11), "Africa/Nairobi"
    ) == {}


def test_station_readings_are_stored_beside_the_scored_values_not_instead():
    """The rule from `precipitation_onset`: a station quantity never
    overwrites a scored reanalysis one, because changing what a stored field
    means makes every stored day incomparable with every other."""
    a = DailyActual(
        rain=True, high_c=28.0, station_high_c=29.4,
        provenance={"high_c": SOURCE_REANALYSIS, "station_high_c": SOURCE_STATION},
    )
    assert a.high_c == 28.0
    assert a.station_high_c == 29.4
    assert a.provenance["high_c"] == SOURCE_REANALYSIS


def test_one_request_carries_both_the_reports_and_the_readings(monkeypatch):
    """The archive request has a 90-second timeout and is the slowest call in
    the verification pass. Asking it twice — once for raw reports, once for
    structured columns — would double that for nothing, which is the same
    reasoning observed_weather_by_date already gives for getting two flags out
    of one fetch."""
    from openlocalweather.fetch import metar as metar_fetch

    seen = {}

    class _Resp:
        status_code = 200
        text = (
            "station,valid,metar,tmpf,sknt\n"
            "HKKI,2026-08-11 09:00,HKKI 110900Z 24010KT -RA,86.0,20.0\n"
        )

    def _fake_get(url, params=None, timeout=None):
        seen["params"] = params
        seen["calls"] = seen.get("calls", 0) + 1
        return _Resp()

    monkeypatch.setattr(metar_fetch.requests, "get", _fake_get)

    weather, readings = metar_fetch.observed_station_data(
        "HKKI", date(2026, 8, 11), date(2026, 8, 11), "Africa/Nairobi"
    )

    assert seen["calls"] == 1, "one fetch, not two"
    assert "tmpf" in seen["params"]["data"]
    assert "p01i" not in seen["params"]["data"], "p01i is a constant here — see item 45"
    assert weather[date(2026, 8, 11)].precipitation is True
    assert readings[date(2026, 8, 11)].high_c == 30.0


def test_a_station_that_does_not_answer_yields_neither(monkeypatch):
    from openlocalweather.fetch import metar as metar_fetch

    monkeypatch.setattr(
        metar_fetch, "fetch_metar_archive_rows", lambda *a, **k: None
    )
    weather, readings = metar_fetch.observed_station_data(
        "HKKI", date(2026, 8, 11), date(2026, 8, 11), "Africa/Nairobi"
    )
    assert weather is None
    assert readings is None


def test_the_pipeline_stores_readings_and_stamps_them(monkeypatch):
    from openlocalweather import pipeline
    from openlocalweather.fetch.metar import StationReadings, StationWeather

    covered, uncovered = date(2026, 8, 11), date(2026, 8, 12)
    actuals = {
        covered: DailyActual(rain=False, high_c=28.0, provenance={"high_c": SOURCE_REANALYSIS}),
        uncovered: DailyActual(rain=False, high_c=27.0, provenance={"high_c": SOURCE_REANALYSIS}),
    }
    monkeypatch.setattr(
        pipeline.metar_fetch, "observed_station_data",
        lambda icao, s, e, tz: (
            {covered: StationWeather(thunder=False, precipitation=False)},
            {covered: StationReadings(high_c=29.4, low_c=18.1, peak_wind_kmh=33.0)},
        ),
    )

    class _Loc:
        metar_station_icao = "HKKI"
        timezone = "Africa/Nairobi"

    pipeline._apply_station_observations(actuals, _Loc())

    a = actuals[covered]
    assert a.station_high_c == 29.4
    assert a.provenance["station_high_c"] == SOURCE_STATION
    # The scored value is untouched — item 45's sequencing.
    assert a.high_c == 28.0
    assert a.provenance["high_c"] == SOURCE_REANALYSIS
    # And an uncovered day gains nothing.
    assert actuals[uncovered].station_high_c is None
    assert "station_high_c" not in actuals[uncovered].provenance
