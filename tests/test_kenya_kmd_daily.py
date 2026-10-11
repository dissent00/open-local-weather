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
    """Since 2026-10-11 an unlinked PDF is probed for by document id
    first; when the probe finds nothing the day says so."""
    with requests_mock.Mocker() as m:
        m.get(LANDING, text=_landing_html())
        m.get(POST, text="<p>Post with no attachment yet</p>")
        m.head(requests_mock.ANY, status_code=404)
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


def test_stores_a_location_relevant_extract_not_the_whole_document():
    """The daily PDF is ~8,700 characters covering 47 counties plus
    letterhead and glossary. Storing that every day puts megabytes of
    irrelevant text into git and spends prompt budget diluting the handful
    of lines that concern this location."""
    from openlocalweather.fetch.bulletin.kenya_kmd_daily import compose_extract
    from openlocalweather.fetch.bulletin.kmd_daily_parse import parse_county_outlook

    outlook = parse_county_outlook(FIXTURE["tables"], "Kisumu")
    extract = compose_extract(
        "Kisumu", date(2026, 8, 19), outlook,
        national_lines=[], five_day=[],
    )
    assert len(extract) < 1000, "an extract, not the document"
    assert len(FIXTURE["text"]) > 8000, "fixture really is the whole document"
    # The met service's own words survive — the decoded booleans alone would
    # make the decoding unauditable and the narrative poorer.
    assert "light rains expected over few places" in extract
    assert "Max 30.0C / Min 19.0C" in extract
    # Other counties do not.
    assert "Mombasa" not in extract
    assert "Dagoretti Corner" not in extract, "letterhead dropped"


def test_extract_names_the_date_the_forecast_is_for():
    from openlocalweather.fetch.bulletin.kenya_kmd_daily import compose_extract

    extract = compose_extract("Kisumu", date(2026, 8, 19), None, [], [])
    assert "Valid for 2026-08-19" in extract


def test_five_day_failure_does_not_cost_the_day0_prediction(monkeypatch):
    """The five-day bulletin is a bonus lead time. Losing it must not take
    the same-day forecast or the narrative extract down with it.

    Patched at the fetch-hop level rather than the HTTP level because the
    mocked byte payloads aren't parseable PDFs — mocking HTTP here would
    break the daily fetch too, and the test would then pass for the wrong
    reason.
    """
    from openlocalweather.fetch.bulletin import kenya_kmd_daily as mod

    calls = []

    def fake_hop(self, landing_url, post_pattern):
        calls.append(landing_url)
        if "5-day" in landing_url:
            raise requests.exceptions.ConnectTimeout("five-day down")
        return FIXTURE["text"], FIXTURE["tables"]

    monkeypatch.setattr(mod.KenyaKMDDailyFetcher, "_fetch_pdf_tables", fake_hop)
    result = mod.KenyaKMDDailyFetcher(
        LANDING, "Kisumu", "kenya_met", day3_target=date(2026, 8, 22)
    ).fetch_forecast()

    assert any("5-day" in c for c in calls), "the five-day fetch was attempted"
    assert result.prediction_day3 is None, "and failed"
    # ...while everything that didn't depend on it survived.
    assert result.prediction is not None
    assert result.prediction.rain is True
    assert result.prediction.high_c == 30.0
    assert result.valid_for == date(2026, 8, 19)
    assert "light rains expected over few places" in result.text


def test_the_bulletins_window_opens_at_nine_the_evening_before():
    """"From 9:00 p.m. Tonight to 9:00 p.m. Tomorrow, 19th August": the claim
    for the 19th opens at 21:00 on the 18th. Item 139 scores it there."""
    from datetime import datetime

    fetcher = KenyaKMDDailyFetcher("https://meteo.go.ke/our-products/daily-forecast")

    assert fetcher.validity_window_opens(date(2026, 8, 19)) == datetime(2026, 8, 18, 21, 0)


# ---------------------------------------------------------------------------
# The unlinked PDF — 2026-10-11
# ---------------------------------------------------------------------------

POST_11TH = LANDING + "daily-weather-forecast-valid-11th-october-2026/"
UNLINKED_PDF = "https://meteo.go.ke/documents/5261/Daily_Weather_Forecast_Valid_11th_October_2026.pdf"


def test_candidate_filenames_follow_the_slug():
    """KMD's CMS serves a document only under its exact filename, which
    follows the post's slug title-cased: "Valid" on 2026-10-07 and 10-11,
    "valid" on 08-19, so both cases are tried; a five-day post's name
    carries "from" and a lowercase "to"."""
    from openlocalweather.fetch.bulletin.kenya_kmd_daily import candidate_filenames

    assert candidate_filenames(POST_11TH) == [
        "Daily_Weather_Forecast_Valid_11th_October_2026.pdf",
        "Daily_Weather_Forecast_valid_11th_October_2026.pdf",
    ]
    five_day = "https://meteo.go.ke/our-products/5-day-forecast/5-day-weather-forecast-valid-11th-to-15th-october-2026/"
    assert candidate_filenames(five_day) == [
        "5_Day_Weather_Forecast_Valid_from_11th_to_15th_October_2026.pdf",
        "5_Day_Weather_Forecast_Valid_11th_to_15th_October_2026.pdf",
        "5_Day_Weather_Forecast_valid_from_11th_to_15th_October_2026.pdf",
        "5_Day_Weather_Forecast_valid_11th_to_15th_October_2026.pdf",
    ]


def test_an_unlinked_pdf_is_found_by_document_id():
    """Since 2026-10-08 KMD's daily post is a shell — a title, no body, no
    link — while the PDF is uploaded as before, one document id further
    on each day. The fetcher probes ids after the last one it saw, under
    the slug's filename, and records the id it found so the next run
    starts there."""
    with requests_mock.Mocker() as m:
        m.get(LANDING, text='<a href="/our-products/daily-forecast/daily-weather-forecast-valid-11th-october-2026/">x</a>')
        m.get(POST_11TH, text="<h1>Daily weather forecast valid 11th October 2026</h1>")
        m.head(requests_mock.ANY, status_code=404)
        m.head(UNLINKED_PDF, status_code=200)
        m.get(UNLINKED_PDF, content=b"%PDF-1.4 fake")
        fetcher = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met", document_id_hint=5255)
        result = fetcher.fetch_forecast()

        probed = [r.url for r in m.request_history if r.method == "HEAD"]
    assert "no PDF link" not in result.text
    assert result.document_id == 5261
    assert probed[0].startswith("https://meteo.go.ke/documents/5256/"), "the probe starts after the hint"
    assert probed[-1] == UNLINKED_PDF


def test_a_linked_pdf_still_records_its_document_id():
    with requests_mock.Mocker() as m:
        _mock_full_fetch(m)
        result = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met").fetch_forecast()
    assert result.document_id == 4690
    assert not any(r.method == "HEAD" for r in m.request_history), "nothing to probe when the post links its PDF"


def test_the_probe_gives_up_after_its_span():
    from openlocalweather.fetch.bulletin.kenya_kmd_daily import DOCUMENT_PROBE_SPAN

    with requests_mock.Mocker() as m:
        m.get(LANDING, text='<a href="/our-products/daily-forecast/daily-weather-forecast-valid-11th-october-2026/">x</a>')
        m.get(POST_11TH, text="<h1>shell</h1>")
        m.head(requests_mock.ANY, status_code=404)
        result = KenyaKMDDailyFetcher(LANDING, "Kisumu", "kenya_met", document_id_hint=5255).fetch_forecast()
        heads = sum(1 for r in m.request_history if r.method == "HEAD")
    assert result.prediction is None and "no PDF link" in result.text
    assert heads == DOCUMENT_PROBE_SPAN * 2


def test_the_five_day_post_pattern_matches_both_slugs():
    """0 of 60 days held a met Day+3 by 2026-10-11: the pattern of 08-19
    looked for "five-day-forecast" and KMD slugs the posts
    "five-day-weather-forecast-..." and, since October, "5-day-weather-forecast-..."."""
    from openlocalweather.fetch.bulletin.kenya_kmd_daily import _FIVE_DAY_POST_RE

    for slug in ("five-day-weather-forecast-3rd-to-7th-october-2026", "5-day-weather-forecast-valid-11th-to-15th-october-2026"):
        assert _FIVE_DAY_POST_RE.search(f'<a href="/our-products/5-day-forecast/{slug}/">x</a>'), slug
