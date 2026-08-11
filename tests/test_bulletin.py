from openlocalweather.fetch.bulletin import NullBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd import KenyaKMDBulletinFetcher


def test_null_bulletin_fetcher_returns_skip_message():
    result = NullBulletinFetcher().fetch()
    assert "No local bulletin source configured" in result


def test_kenya_kmd_bulletin_fetcher_never_raises_and_explains_status():
    fetcher = KenyaKMDBulletinFetcher("https://meteo.go.ke/our-products/7-days-forecast/")
    result = fetcher.fetch()
    assert isinstance(result, str)
    assert "not yet implemented" in result
    assert "Kenya Meteorological Department" in result
