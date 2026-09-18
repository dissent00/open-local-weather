"""The station's reports, stored as committed data — ROADMAP item 151.

Three readers of the archive (the same-day snapshot, the day aggregates,
the +24 h window) used to fetch and forget; the window's scores were not
recomputable from the record and every reader depended on the archive
answering at that moment. The store holds the rows as the archive serves
them, one file per station per UTC day, merged on every fetch so a day
fills up across runs and a re-fetch can add or correct but never lose.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from openlocalweather.store import station_reports as store

ROW_A = ["HKKI", "2026-09-17 00:00", "HKKI 170000Z 09004KT 9999 FEW020 20/18 Q1017", "68.00", "4.00"]
ROW_B = ["HKKI", "2026-09-17 01:00", "HKKI 170100Z 00000KT 9999 FEW020 20/18 Q1017", "68.00", "0.00"]
ROW_C = ["HKKI", "2026-09-18 00:00", "HKKI 180000Z 09003KT 9999 SCT020 21/20 Q1016", "69.80", "3.00"]


def test_rows_are_filed_by_the_utc_day_of_the_report(tmp_path):
    store.merge_rows(tmp_path, "HKKI", [ROW_A, ROW_C])

    assert (tmp_path / "station" / "HKKI" / "2026-09-17.json").exists()
    assert (tmp_path / "station" / "HKKI" / "2026-09-18.json").exists()


def test_a_read_returns_the_rows_as_the_archive_served_them(tmp_path):
    store.merge_rows(tmp_path, "HKKI", [ROW_A, ROW_C])

    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 18)) == [ROW_A, ROW_C]


def test_a_second_fetch_adds_and_never_loses(tmp_path):
    # The 06:01 run stores the night; the 18:01 run brings the day. And a
    # later fetch that no longer returns an early row (the archive has
    # dropped a row before) leaves the stored one in place.
    store.merge_rows(tmp_path, "HKKI", [ROW_A])
    added = store.merge_rows(tmp_path, "HKKI", [ROW_B])

    assert added == 1
    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 17)) == [ROW_A, ROW_B]


def test_the_same_report_twice_is_stored_once(tmp_path):
    store.merge_rows(tmp_path, "HKKI", [ROW_A, ROW_B])
    added = store.merge_rows(tmp_path, "HKKI", [ROW_B, ROW_A])

    assert added == 0
    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 17)) == [ROW_A, ROW_B]


def test_rows_come_back_in_time_order_whatever_order_they_arrived(tmp_path):
    store.merge_rows(tmp_path, "HKKI", [ROW_B])
    store.merge_rows(tmp_path, "HKKI", [ROW_A])

    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 17)) == [ROW_A, ROW_B]


def test_a_speci_at_the_same_minute_as_a_metar_is_a_second_row(tmp_path):
    # Two reports can share a minute; the text tells them apart. Keying on
    # the time alone would drop the SPECI that carries the storm.
    speci = [ROW_A[0], ROW_A[1], "HKKI 170000Z 09015G25KT 3000 TSRA BKN015 20/18 Q1015", "68.00", "15.00"]
    store.merge_rows(tmp_path, "HKKI", [ROW_A, speci])

    assert len(store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 17))) == 2


def test_nothing_stored_reads_as_none_not_as_a_quiet_station(tmp_path):
    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 17)) is None


def test_a_range_with_a_missing_day_returns_what_exists(tmp_path):
    store.merge_rows(tmp_path, "HKKI", [ROW_A, ROW_C])

    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 16), date(2026, 9, 19)) == [ROW_A, ROW_C]


def test_a_row_of_the_wrong_width_is_refused(tmp_path):
    # The daily parse is positional (item 152): a row with a column too many
    # or too few would put a wind where a temperature belongs once read back.
    with pytest.raises(ValueError):
        store.merge_rows(tmp_path, "HKKI", [ROW_A + ["M"]])
    with pytest.raises(ValueError):
        store.merge_rows(tmp_path, "HKKI", [ROW_A[:4]])


def test_the_file_names_its_columns_so_a_reader_cannot_guess(tmp_path):
    import json

    store.merge_rows(tmp_path, "HKKI", [ROW_A])
    doc = json.loads((tmp_path / "station" / "HKKI" / "2026-09-17.json").read_text())

    assert doc["station"] == "HKKI"
    assert doc["columns"] == ["valid", "metar", "tmpf", "sknt"]
    assert doc["rows"] == [ROW_A[1:]]


def test_a_stored_file_with_other_columns_is_refused_on_merge(tmp_path):
    import json

    path = tmp_path / "station" / "HKKI" / "2026-09-17.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"station": "HKKI", "columns": ["valid", "metar", "tmpf", "sknt", "gust"], "rows": []}))

    with pytest.raises(ValueError):
        store.merge_rows(tmp_path, "HKKI", [ROW_A])


# ---------------------------------------------------------------------------
# Through the store: what the pipeline's readers actually get.
# ---------------------------------------------------------------------------


def _archive_csv(*rows: Sequence[str]) -> str:
    header = "station,valid,metar,tmpf,sknt\n"
    return header + "".join(",".join(f'"{c}"' if "," in c else c for c in r) + "\n" for r in rows)


def test_observed_station_data_reads_the_union_of_every_fetch(tmp_path):
    """The evening fetch answers with the day; the night rows the morning
    stored are still in what the reader gets, even though this fetch did
    not return them."""
    import requests_mock

    from openlocalweather.fetch.metar import METAR_ARCHIVE_URL, observed_station_data

    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_A))
        observed_station_data("HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi", data_dir=tmp_path)

        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_B))
        _, readings = observed_station_data(
            "HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi", data_dir=tmp_path
        )

    # ROW_A carries 4 kt, ROW_B 0 kt: a peak of 4 kt proves the stored row
    # was read, since the second fetch alone would give 0.
    assert readings[date(2026, 9, 17)].peak_wind_kmh == pytest.approx(4 * 1.852, abs=0.01)
    assert store.read_rows(tmp_path, "HKKI", date(2026, 9, 17), date(2026, 9, 17)) == [ROW_A, ROW_B]


def test_station_reports_come_through_the_same_store(tmp_path):
    import requests_mock

    from openlocalweather.fetch.metar import METAR_ARCHIVE_URL, station_reports

    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_A))
        station_reports("HKKI", date(2026, 9, 17), date(2026, 9, 17), data_dir=tmp_path)
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_B))
        reports = station_reports("HKKI", date(2026, 9, 17), date(2026, 9, 17), data_dir=tmp_path)

    assert [r[1] for r in reports] == [ROW_A[2], ROW_B[2]]


def test_without_a_store_the_fetch_is_still_read_directly():
    import requests_mock

    from openlocalweather.fetch.metar import METAR_ARCHIVE_URL, observed_station_data

    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_A))
        _, readings = observed_station_data("HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi")

    assert readings[date(2026, 9, 17)].high_c == pytest.approx(20.0, abs=0.01)


def test_the_store_and_the_fetch_agree_on_the_columns():
    from openlocalweather.fetch.metar import ARCHIVE_DATA_COLUMNS

    assert store.COLUMNS == ("valid", *ARCHIVE_DATA_COLUMNS)


def test_every_production_reader_goes_through_the_store():
    """STRUCTURAL, like item 145's guard: a reader that forgot `data_dir`
    would fetch and forget exactly as before, and every test would pass,
    because the store is additive. So the callers are walked."""
    import ast

    src = Path(__file__).resolve().parents[1] / "src" / "openlocalweather"
    readers = {"observed_station_data", "station_reports"}
    missing = []
    for path in (src / "pipeline.py", src / "cli.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in readers:
                continue
            if not any(k.arg == "data_dir" for k in node.keywords):
                missing.append(f"{path.name}:{node.lineno} {name}")
    assert not missing, f"readers that bypass the store: {missing}"


# ---------------------------------------------------------------------------
# The fallback — item 151, step 3. A fetch that fails no longer leaves the
# reader with nothing when the store holds the range: the stored rows are
# read, and the caller is told the archive's reason so it can say so beside
# the reach. Without a store, or with nothing stored, the failure propagates
# exactly as before.
# ---------------------------------------------------------------------------


def test_a_failed_fetch_falls_back_to_the_stored_rows_and_says_why(tmp_path):
    import requests_mock

    from openlocalweather.fetch.metar import METAR_ARCHIVE_URL, observed_station_data

    reasons: list[str] = []
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_A, ROW_B))
        observed_station_data("HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi", data_dir=tmp_path)

        m.get(METAR_ARCHIVE_URL, status_code=503, text="Service Unavailable")
        weather, readings = observed_station_data(
            "HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi",
            data_dir=tmp_path, on_fallback=reasons.append,
        )

    assert readings[date(2026, 9, 17)].peak_wind_kmh == pytest.approx(4 * 1.852, abs=0.01)
    assert weather[date(2026, 9, 17)].reported_through == "04:00"
    assert len(reasons) == 1 and "HTTP 503" in reasons[0]


def test_a_failed_fetch_with_nothing_stored_still_raises(tmp_path):
    import requests_mock

    from openlocalweather.fetch.metar import ArchiveUnavailable, METAR_ARCHIVE_URL, observed_station_data

    reasons: list[str] = []
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, status_code=503, text="Service Unavailable")
        with pytest.raises(ArchiveUnavailable):
            observed_station_data(
                "HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi",
                data_dir=tmp_path, on_fallback=reasons.append,
            )
    assert reasons == []


def test_a_successful_fetch_never_reports_a_fallback(tmp_path):
    import requests_mock

    from openlocalweather.fetch.metar import METAR_ARCHIVE_URL, observed_station_data

    reasons: list[str] = []
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_A))
        observed_station_data(
            "HKKI", date(2026, 9, 17), date(2026, 9, 17), "Africa/Nairobi",
            data_dir=tmp_path, on_fallback=reasons.append,
        )
    assert reasons == []


def test_the_window_reports_fall_back_the_same_way(tmp_path):
    import requests_mock

    from openlocalweather.fetch.metar import METAR_ARCHIVE_URL, station_reports

    reasons: list[str] = []
    with requests_mock.Mocker() as m:
        m.get(METAR_ARCHIVE_URL, text=_archive_csv(ROW_A))
        station_reports("HKKI", date(2026, 9, 17), date(2026, 9, 17), data_dir=tmp_path)
        m.get(METAR_ARCHIVE_URL, exc=__import__("requests").exceptions.ConnectTimeout("slow"))
        reports = station_reports(
            "HKKI", date(2026, 9, 17), date(2026, 9, 17), data_dir=tmp_path, on_fallback=reasons.append
        )

    assert [r[1] for r in reports] == [ROW_A[2]]
    assert len(reasons) == 1 and "ConnectTimeout" in reasons[0]
