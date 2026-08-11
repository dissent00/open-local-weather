from pathlib import Path

import requests_mock

from openlocalweather.fetch.bulletin import NullBulletinFetcher
from openlocalweather.fetch.bulletin.kenya_kmd import (
    KenyaKMDBulletinFetcher,
    extract_pdf_text,
    find_latest_forecast_post_url,
    find_pdf_url,
)

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_bulletin.pdf"
LANDING_URL = "https://meteo.go.ke/our-products/7-days-forecast/"


def test_null_bulletin_fetcher_returns_skip_message():
    result = NullBulletinFetcher().fetch()
    assert "No local bulletin source configured" in result


# ---------------------------------------------------------------------------
# find_latest_forecast_post_url
# ---------------------------------------------------------------------------


def test_find_latest_forecast_post_url_takes_first_match():
    # Real landing pages link each post 3x (thumbnail/title/read-more) —
    # the newest post's link appears first in page order either way.
    html = """
    <a href="/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/">img</a>
    <a href="/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/">title</a>
    <a href="/our-products/7-days-forecast/seven-day-forecast-4th-to-10th-august-2026/">older post</a>
    """
    url = find_latest_forecast_post_url(html, LANDING_URL)
    assert url == "https://meteo.go.ke/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/"


def test_find_latest_forecast_post_url_returns_none_when_absent():
    assert find_latest_forecast_post_url("<html>nothing here</html>", LANDING_URL) is None


# ---------------------------------------------------------------------------
# find_pdf_url
# ---------------------------------------------------------------------------


def test_find_pdf_url_resolves_relative_link():
    post_url = "https://meteo.go.ke/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/"
    html = '<a href="/documents/4590/Weekly_weather_forecast.pdf">Download</a>'
    assert find_pdf_url(html, post_url) == "https://meteo.go.ke/documents/4590/Weekly_weather_forecast.pdf"


def test_find_pdf_url_returns_none_when_absent():
    assert find_pdf_url("<html>no pdf</html>", "https://meteo.go.ke/some-post/") is None


# ---------------------------------------------------------------------------
# extract_pdf_text
# ---------------------------------------------------------------------------


def test_extract_pdf_text_from_real_fixture():
    pdf_bytes = FIXTURE_PDF.read_bytes()
    text = extract_pdf_text(pdf_bytes)
    assert "KENYA METEOROLOGICAL SERVICE" in text
    assert "WEEKLY WEATHER FORECAST" in text
    assert "Page two" in text  # confirms multi-page text is joined


# ---------------------------------------------------------------------------
# KenyaKMDBulletinFetcher — full two-hop scrape, mocked HTTP
# ---------------------------------------------------------------------------


def test_fetcher_full_happy_path():
    post_url = "https://meteo.go.ke/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/"
    pdf_url = "https://meteo.go.ke/documents/4590/Weekly_weather_forecast.pdf"

    with requests_mock.Mocker() as m:
        m.get(LANDING_URL, text=f'<a href="{post_url[len("https://meteo.go.ke"):]}">post</a>')
        m.get(post_url, text=f'<a href="{pdf_url[len("https://meteo.go.ke"):]}">pdf</a>')
        m.get(pdf_url, content=FIXTURE_PDF.read_bytes())

        result = KenyaKMDBulletinFetcher(LANDING_URL).fetch()

    assert "KENYA METEOROLOGICAL SERVICE" in result


def test_fetcher_returns_message_when_no_post_link_found():
    with requests_mock.Mocker() as m:
        m.get(LANDING_URL, text="<html>redesigned page, no matching links</html>")
        result = KenyaKMDBulletinFetcher(LANDING_URL).fetch()
    assert "could not find a current forecast post link" in result


def test_fetcher_returns_message_when_no_pdf_link_found():
    post_url = "https://meteo.go.ke/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/"
    with requests_mock.Mocker() as m:
        m.get(LANDING_URL, text=f'<a href="{post_url[len("https://meteo.go.ke"):]}">post</a>')
        m.get(post_url, text="<html>no pdf link here</html>")
        result = KenyaKMDBulletinFetcher(LANDING_URL).fetch()
    assert "could not find a PDF link" in result


def test_fetcher_never_raises_on_network_error():
    with requests_mock.Mocker() as m:
        m.get(LANDING_URL, status_code=500)
        result = KenyaKMDBulletinFetcher(LANDING_URL).fetch()
    assert isinstance(result, str)
    assert "Error reading" in result


def test_fetcher_never_raises_on_malformed_pdf():
    post_url = "https://meteo.go.ke/our-products/7-days-forecast/seven-day-forecast-11th-to-17th-august-2026/"
    pdf_url = "https://meteo.go.ke/documents/4590/Weekly_weather_forecast.pdf"
    with requests_mock.Mocker() as m:
        m.get(LANDING_URL, text=f'<a href="{post_url[len("https://meteo.go.ke"):]}">post</a>')
        m.get(post_url, text=f'<a href="{pdf_url[len("https://meteo.go.ke"):]}">pdf</a>')
        m.get(pdf_url, content=b"not a real pdf")
        result = KenyaKMDBulletinFetcher(LANDING_URL).fetch()
    assert isinstance(result, str)
    assert "Error reading" in result
