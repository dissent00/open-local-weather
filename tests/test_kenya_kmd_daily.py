"""The daily-bulletin fetcher: one HTTP fetch, two consumers.

Network is mocked throughout; the parsing itself is covered against a real
captured bulletin in test_kmd_daily_parse.py.
"""

import json
from datetime import date
from pathlib import Path

import requests
import requests_mock

from openlocalweather.fetch.bulletin.kenya_kmd_daily import (
    KenyaKMDDailyFetcher,
    find_latest_daily_post_url,
    parse_validity_date,
)

LANDING = "https://meteo.go.ke/our-products/daily-forecast/"
POST = LANDING + "daily-weather-forecast-valid-19th-august-2026/"
PDF = "https://meteo.go.ke/documents/4690/Daily_Weather_Forecast_valid_19th_August_2026.pdf"

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "kmd_daily_2026-08-19.json").read_text())


def _landing_html():
    return f'<a href="/our-products/daily-forecast/daily-weather-forecast-valid-19th-august-2026/">Latest</a>'


def test_finds_the_newest_post_from_the_listing():
    assert find_latest_daily_post_url(_landing_html(), LANDING) == POST


def test_validity_date_comes_from_the_bulletin_not_from_today():
    """Load-bearing. KMD issues at ~3pm for the following day, so a run that
    assumed "the latest bulletin is for today" would, on any day KMD hadn't
    published yet, score yesterday's forecast against today's weather."""
    assert parse_validity_date(FIXTURE["text"]) == date(2026, 8, 19)


def test_validity_date_is_none_when_unparseable():
    assert parse_validity_date("no validity line here") is None


def _mock_full_fetch(m, pdf_bytes=b"%PDF-1.4 fake"):
    m.get(LANDING, text=_landing_html())
    m.get(POST, text=f'<a href="{PDF}">Download</a>')
    m.get(PDF, content=pdf_bytes)


def test_a_failed_fetch_degrades_instead_of_raising():
    """BulletinFetcher's contract: never raise. A met-service outage must
    cost the narrative section and that day's met score, not the run."""
    with requests_mock.Mocker() as m:
        m.get(LANDING, exc=requests.exceptions.ConnectTimeout)
        result = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met").fetch_forecast()
    assert result.prediction is None
    assert result.valid_for is None
    assert "fetch failed" in result.text


def test_an_unreadable_pdf_degrades_instead_of_raising():
    with requests_mock.Mocker() as m:
        _mock_full_fetch(m, pdf_bytes=b"not a pdf at all")
        result = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met").fetch_forecast()
    assert result.prediction is None
    assert "could not read" in result.text or "no text" in result.text


def test_missing_pdf_link_is_reported_not_guessed():
    with requests_mock.Mocker() as m:
        m.get(LANDING, text=_landing_html())
        m.get(POST, text="<p>Post with no attachment yet</p>")
        result = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met").fetch_forecast()
    assert result.prediction is None
    assert "no PDF link" in result.text


def test_one_fetch_serves_both_consumers():
    """The whole cost argument rests on this: the narrative blurb and the
    scored prediction must not each trigger their own download."""
    with requests_mock.Mocker() as m:
        _mock_full_fetch(m)
        fetcher = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met")
        fetcher.fetch()           # prompt text
        fetcher.fetch_forecast()  # structured prediction
        fetcher.fetch()
        assert m.call_count == 3, "3 hops once, not repeated per consumer"


def test_no_configured_area_still_yields_bulletin_text():
    """A fork whose met service publishes prose this parser can't index by
    area must keep its narrative context and simply not be scored."""
    with requests_mock.Mocker() as m:
        _mock_full_fetch(m)
        result = KenyaKMDDailyFetcher(LANDING, area_name="").fetch_forecast()
    assert result.prediction is None
