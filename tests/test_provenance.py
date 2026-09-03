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
        "observed_weather_by_date",
        lambda icao, start, end, tz: {
            covered: StationWeather(thunder=True, precipitation=False)
        },
    )

    class _Loc:
        metar_station_icao = "HKKI"
        timezone = "Africa/Nairobi"

    pipeline._apply_station_observations(actuals, _Loc())

    assert actuals[covered].provenance["thunder"] == SOURCE_STATION
    assert "thunder" not in actuals[uncovered].provenance
    # The archive's own stamps are not disturbed by the station's arrival.
    assert actuals[covered].provenance["rain"] == SOURCE_REANALYSIS
