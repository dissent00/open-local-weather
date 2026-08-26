import requests
import requests_mock

from datetime import date

from openlocalweather.fetch.metar import (
    METAR_ARCHIVE_URL,
    METAR_URL,
    fetch_metar,
    observed_thunder_by_date,
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
        assert observed_thunder_by_date("", DAY, DAY, "Africa/Nairobi") is None
        assert m.call_count == 0


def test_observed_thunder_non_200_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, status_code=500)
        assert observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi") is None


def test_observed_thunder_network_error_returns_none():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, exc=requests.exceptions.ConnectionError("boom"))
        assert observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi") is None


def test_observed_thunder_header_only_returns_none():
    # No reports at all is not the same as "no thunder" — it is no
    # observation, and must not be recorded as a quiet day.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text="station,valid,metar\n")
        assert observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi") is None


def test_observed_thunder_reports_without_thunder_are_false_not_absent():
    # A day that reported and saw nothing IS evidence, and must read as False
    # so a dry call can be scored against it.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 09:00,HKKI 240900Z 22008KT 9999 FEW029 31/12 Q1016",
        ))
        assert observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {DAY: False}


def test_observed_thunder_plain_ts():
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 09:00,HKKI 240900Z 22008KT 9999 FEW029 31/12 Q1016",
            "HKKI,2026-08-24 13:30,HKKI 241330Z 18005KT 9999 TS FEW029CB BKN030 31/14 Q1015",
        ))
        assert observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {DAY: True}


def test_observed_thunder_recognises_every_thunder_form():
    # -TSRA / +TSRA / VCTS / RETS all mean thunder happened at or beside the
    # station. RETS in particular is the only trace left when a storm ends
    # between two routine reports.
    for group in ("TS", "TSRA", "-TSRA", "+TSRA", "TSGR", "VCTS", "RETS", "RETSRA"):
        with requests_mock.Mocker() as m:
            m.get(METAR_ARCHIVE_URL, text=csv_rows(
                f"HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 {group} FEW029CB 31/14 Q1015",
            ))
            result = observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi")
            assert result == {DAY: True}, f"{group} was not read as thunder"


def test_observed_thunder_ignores_lookalikes():
    # Cloud groups and remarks must not be mistaken for present weather.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 13:00,HKKI 241300Z 18005KT 9999 FEW029CB BKN030 31/14 Q1015 RMK TS DISTANT",
        ))
        assert observed_thunder_by_date("HKKI", DAY, DAY, "Africa/Nairobi") == {DAY: False}


def test_observed_thunder_buckets_by_local_date_not_utc():
    # 21:30Z is 00:30 the NEXT day in Nairobi. Scoring a local calendar day
    # against UTC-bucketed observations would put this storm on the wrong day.
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=csv_rows(
            "HKKI,2026-08-24 21:30,HKKI 242130Z 18005KT 9999 TS FEW029CB 22/16 Q1015",
        ))
        result = observed_thunder_by_date("HKKI", DAY, date(2026, 8, 25), "Africa/Nairobi")
        assert result == {date(2026, 8, 25): True}
