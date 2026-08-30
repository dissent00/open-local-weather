import requests
import requests_mock

from datetime import date

from openlocalweather.fetch.metar import (
    METAR_ARCHIVE_URL,
    METAR_URL,
    StationWeather,
    fetch_metar,
    observed_weather_by_date,
)

DAY = date(2026, 8, 24)


def test_fetch_metar_blank_icao_returns_none_without_request():
    with requests_mock.Mocker() as m:
        assert fetch_metar("") is None
        assert m.call_count == 0


def test_fetch_metar_success():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, json=[{"icaoId": "HKKI", "temp": 24}])
        result = fetch_metar("HKKI")
        assert result == [{"icaoId": "HKKI", "temp": 24}]


def test_fetch_metar_non_200_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, status_code=503)
        assert fetch_metar("HKKI") is None


def test_fetch_metar_network_error_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, exc=requests.exceptions.ConnectionError("boom"))
        assert fetch_metar("HKKI") is None


def test_fetch_metar_malformed_json_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, text="not json")
        assert fetch_metar("HKKI") is None


def test_fetch_metar_empty_response_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_URL, json=[])
        assert fetch_metar("HKKI") is None


# ---------------------------------------------------------------------------
# Archive observations — "did thunder occur", the domain concept the accuracy
# loop needs. See metar.py for why this uses a different endpoint.
# ---------------------------------------------------------------------------


def csv_rows(*rows: str) -> str:
    return "station,valid,metar\n" + "\n".join(rows) + "\n"


def test_observed_thunder_blank_icao_returns_none_without_request():
    with requests_mock.Mocker() as m:
        assert observed_weather_by_date("", DAY, DAY, "Africa/Nairobi") is None
        assert m.call_count == 0


def test_observed_thunder_non_200_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, status_code=500)
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") is None


def test_observed_thunder_network_error_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, exc=requests.exceptions.ConnectionError("boom"))
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") is None


def test_observed_thunder_header_only_returns_none():
    # No reports at all is not the same as "no thunder" — it is no
    # observation, and must not be recorded as a quiet day.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text="station,valid,metar\n")
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") is None


def test_observed_thunder_reports_without_thunder_are_false_not_absent():
    # A day that reported and saw nothing IS evidence, and must read as False
    # so a dry call can be scored against it.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 09:00,HKKI 240900Z 22008KT 9999 FEW029 31/12 Q1016",
        ))
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {
            DAY: StationWeather(thunder=False, precipitation=False)
        }


def test_observed_thunder_plain_ts():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 09:00,HKKI 240900Z 22008KT 9999 FEW029 31/12 Q1016",
            "HKKI,2026-08-24 13:30,HKKI 241330Z 18005KT 9999 TS FEW029CB BKN030 31/14 Q1015",
        ))
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {
            DAY: StationWeather(thunder=True, precipitation=False)
        }


def test_observed_thunder_recognises_every_thunder_form():
    # -TSRA / +TSRA / VCTS / RETS all mean thunder happened at or beside the
    # station. RETS in particular is the only trace left when a storm ends
    # between two routine reports.
    for group in ("TS", "TSRA", "-TSRA", "+TSRA", "TSGR", "VCTS", "RETS", "RETSRA"):
        with requests_mock.Mocker() as m:
            m.get(METAR_ARCHIVE_URL, text=csv_rows(
                f"HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 {group} FEW029CB 31/14 Q1015",
            ))
            result = observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi")
            # .thunder alone: TSRA and friends are precipitation as well, and
            # that half is asserted by the precipitation tests below.
            assert result[DAY].thunder is True, f"{group} was not read as thunder"


def test_observed_thunder_ignores_lookalikes():
    # Cloud groups and remarks must not be mistaken for present weather.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 FEW029CB BKN030 31/14 Q1015 RMK TS DISTANT",
        ))
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {
            DAY: StationWeather(thunder=False, precipitation=False)
        }


def test_observed_thunder_buckets_by_local_date_not_utc():
    # 21:30Z is 00:30 the NEXT day in Nairobi. Scoring a local calendar day
    # against UTC-bucketed observations would put this storm on the wrong day.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 21:30,HKKI 242130Z 18005KT 9999 TS FEW029CB 22/16 Q1015",
        ))
        result = observed_weather_by_date("HKKI", DAY, date(2026, 8, 25), "Africa/Nairobi")
        assert result == {date(2026, 8, 25): StationWeather(thunder=True, precipitation=False)}


# ---------------------------------------------------------------------------
# Precipitation, which is NOT thunder. Item 53: the 2026-08-29 miss was rain
# with no TS group anywhere, so the thunder-only parser scored it as a dry day.
# ---------------------------------------------------------------------------


# The real reports, verbatim, from the day this gap was found. No TS anywhere
# — the reader confirmed no thunder was heard — and the reanalysis recorded
# 0.0 mm for the whole day.
AUG_29 = date(2026, 8, 29)
KISUMU_2026_08_29 = (
    "HKKI,2026-08-29 13:00,HKKI 291300Z 22007KT 9999 FEW029 32/10 Q1015",
    "HKKI,2026-08-29 15:00,HKKI 291500Z 24010KT 9999 FEW027CB SCT028 30/16 Q1015",
    "HKKI,2026-08-29 16:00,HKKI 291600Z 15010KT 9999 -RA FEW024CB SCT090 27/18 Q1017",
    "HKKI,2026-08-29 17:00,HKKI 291700Z 34005KT 9999 FEW020CB BKN080 22/18 Q1018 RERA",
    "HKKI,2026-08-29 19:00,HKKI 291900Z 03004KT 9999 FEW020 BKN080 23/18 Q1019",
)


def test_observed_weather_reads_the_2026_08_29_miss_as_rain_without_thunder():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(*KISUMU_2026_08_29))
        result = observed_weather_by_date("HKKI", AUG_29, AUG_29, "Africa/Nairobi")

    assert result == {
        AUG_29: StationWeather(
            thunder=False, precipitation=True, precipitation_onset="19:00"
        )
    }


def test_observed_weather_recognises_every_precipitation_form():
    for group in (
        "RA", "-RA", "+RA", "SHRA", "-SHRA", "+SHRA", "RERA",
        "DZ", "-DZ", "FZDZ", "SN", "-SHSN", "GR", "GS", "SHRAGS", "PL", "UP",
    ):
        with requests_mock.Mocker() as m:
            m.get(METAR_ARCHIVE_URL, text=csv_rows(
                f"HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 {group} FEW029 31/14 Q1015",
            ))
            result = observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi")

        assert result is not None, f"{group} produced no observation"
        assert result[DAY].precipitation is True, f"{group} was not read as precipitation"


def test_observed_weather_ignores_precipitation_lookalikes():
    # Cloud groups, obscurations and vicinity showers must not be read as
    # precipitation AT the station. VCSH is deliberately excluded — see
    # PRECIPITATION_GROUP for why vicinity differs from VCTS.
    for token in ("FEW029CB", "SCT028", "BKN080", "VCSH", "MIFG", "BR", "CAVOK", "VRB02KT"):
        with requests_mock.Mocker() as m:
            m.get(METAR_ARCHIVE_URL, text=csv_rows(
                f"HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 {token} 31/14 Q1015",
            ))
            result = observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi")

        assert result[DAY].precipitation is False, f"{token} was misread as precipitation"


def test_observed_weather_ignores_forecast_precipitation():
    # TEMPO and BECMG are the aerodrome forecasting rain it has not seen.
    # Reading them as observations would manufacture wet days.
    for section in ("TEMPO -SHRA", "BECMG RA", "NOSIG RA", "RMK RA EARLIER"):
        with requests_mock.Mocker() as m:
            m.get(METAR_ARCHIVE_URL, text=csv_rows(
                f"HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 FEW029 31/14 Q1015 {section}",
            ))
            result = observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi")

        assert result[DAY].precipitation is False, f"{section} was misread as an observation"


def test_observed_weather_keeps_thunder_and_precipitation_separate():
    # A dry thunderstorm and a rain shower are different observations, and
    # both matter. TSRA is both at once.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 TS FEW029CB 31/14 Q1015",
        ))
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {
            DAY: StationWeather(thunder=True, precipitation=False)
        }

    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 +TSRA FEW029CB 31/14 Q1015",
        ))
        assert observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {
            DAY: StationWeather(
                thunder=True, precipitation=True, precipitation_onset="16:00"
            )
        }


def test_observed_weather_buckets_precipitation_by_local_date_not_utc():
    # 21:30Z is 00:30 the NEXT day in Nairobi, same as the thunder case.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 21:30,HKKI 242130Z 18005KT 9999 -RA FEW029 22/16 Q1015",
        ))
        result = observed_weather_by_date("HKKI", DAY, date(2026, 8, 25), "Africa/Nairobi")

    # 21:30Z is 00:30 local, so the onset is stamped on the NEXT day too.
    assert result == {
        date(2026, 8, 25): StationWeather(
            thunder=False, precipitation=True, precipitation_onset="00:30"
        )
    }


def test_observed_weather_records_when_precipitation_was_first_seen():
    # The onset the day-over-day description falls back to (item 53.1a), in
    # LOCAL time: -RA lands at 16:00Z, which is 19:00 in Nairobi and therefore
    # an evening shower rather than an afternoon one.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(*KISUMU_2026_08_29))
        result = observed_weather_by_date("HKKI", AUG_29, AUG_29, "Africa/Nairobi")

    assert result[AUG_29].precipitation_onset == "19:00"


def test_precipitation_onset_is_the_first_report_not_the_last():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 09:00,HKKI 240900Z 22008KT 9999 -RA FEW029 24/18 Q1016",
            "HKKI,2026-08-24 13:00,HKKI 241300Z 22008KT 9999 RA FEW029 23/18 Q1015",
        ))
        result = observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi")

    assert result[DAY].precipitation_onset == "12:00"


def test_a_dry_day_has_no_precipitation_onset():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 13:00,HKKI 241300Z 22008KT 9999 FEW029 31/12 Q1015",
        ))
        result = observed_weather_by_date("HKKI", DAY, DAY, "Africa/Nairobi")

    assert result[DAY].precipitation_onset is None
