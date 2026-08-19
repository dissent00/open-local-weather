"""Kenya Met DAILY forecast fetcher — bulletin text AND a scoreable prediction.

Replaces the weekly fetcher (kenya_kmd.py) as this deployment's configured
source. Three reasons, all verified against live bulletins:

1. **Lead time.** The daily bulletin is issued around 3pm for 9pm-to-9pm the
   following day, so the forecast this pipeline reads at ~06:07 is a Day+0
   prediction for today, directly comparable to the numerical models. The
   weekly bulletin issues once for seven days, so its entry for a Thursday
   is a four-day-old forecast being compared against models' fresh
   same-morning runs — a comparison that would flatter the models and
   understate the met service for reasons that have nothing to do with
   skill.
2. **Extractability.** The weekly PDF is sometimes published as scanned
   images (18-24 Aug 2026 had 0 extractable characters against ~8,600 in
   each of the five preceding weeks). The daily bulletins have carried
   extractable text throughout.
3. **Numbers.** The daily bulletin's per-county table gives max/min
   temperature as figures, so temperature is scored from published values
   rather than inferred from prose.

One HTTP fetch serves both consumers — the narrative blurb handed to the
LLM, and the structured prediction scored in code — so adding met-service
verification costs no extra API call of any kind. See kmd_daily_parse for
why the structured half needs no LLM either.

Never raises, matching BulletinFetcher's contract: a failure here degrades
the run (missing bulletin section, no met-service score that day) rather
than aborting it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from urllib.parse import urljoin

import pdfplumber
import requests

from openlocalweather.fetch.bulletin.kmd_daily_parse import CountyOutlook, parse_county_outlook
from openlocalweather.models import ModelPrediction

SOURCE_NAME = "Kenya Meteorological Department (KMD)"
REQUEST_TIMEOUT_S = 30

_POST_LINK_RE = re.compile(r'href="(/our-products/daily-forecast/daily-weather-forecast[^"]*)"')
_PDF_LINK_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
_VALIDITY_RE = re.compile(r"VALIDITY:.*?(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})", re.I | re.S)


@dataclass
class MetServiceForecast:
    """What one bulletin yields: prose for the prompt, numbers for scoring."""

    text: str
    prediction: ModelPrediction | None
    outlook: CountyOutlook | None
    valid_for: date | None


def find_latest_daily_post_url(landing_html: str, base_url: str) -> str | None:
    """Posts are listed newest-first, one per day."""
    match = _POST_LINK_RE.search(landing_html)
    return urljoin(base_url, match.group(1)) if match else None


def find_pdf_url(post_html: str, post_url: str) -> str | None:
    match = _PDF_LINK_RE.search(post_html)
    return urljoin(post_url, match.group(1)) if match else None


def extract_text_and_tables(pdf_bytes: bytes) -> tuple[str, list]:
    with pdfplumber.open(BytesIO(pdf_bytes)) as doc:
        text = "\n".join((page.extract_text() or "") for page in doc.pages)
        tables = [t for page in doc.pages for t in (page.extract_tables() or [])]
    return text, tables


def parse_validity_date(text: str) -> date | None:
    """The date the forecast is FOR, taken from its own VALIDITY line rather
    than assumed to be "today".

    Load-bearing for correctness: a run on a day KMD hasn't published yet
    would otherwise silently score the previous day's bulletin as today's
    prediction, quietly crediting or blaming the met service for a forecast
    it made about a different day.
    """
    match = _VALIDITY_RE.search(text)
    if not match:
        return None
    day, month_name, year = match.groups()
    for month in range(1, 13):
        if date(2000, month, 1).strftime("%B").lower() == month_name.lower():
            try:
                return date(int(year), month, int(day))
            except ValueError:
                return None
    return None


def outlook_to_prediction(outlook: CountyOutlook, model_id: str) -> ModelPrediction:
    """Deliberately leaves wind and pressure as None. KMD's county table
    carries neither, and a None reads correctly as "not forecast" — the
    accuracy page already renders that as a dash rather than a miss."""
    return ModelPrediction(
        model=model_id,
        rain=outlook.rain,
        high_c=outlook.high_c,
        low_c=outlook.low_c,
    )


class KenyaKMDDailyFetcher:
    """BulletinFetcher plus a structured-prediction accessor.

    Caches the parsed result for the lifetime of the instance so the two
    consumers share one HTTP round trip; the pipeline builds a fresh
    instance per run, so there is no staleness across days.
    """

    def __init__(self, landing_url: str, area_name: str = "", model_id: str = "local_met_service"):
        self.landing_url = landing_url
        self.area_name = area_name
        self.model_id = model_id
        self._cached: MetServiceForecast | None = None

    def _headers(self) -> dict:
        return {"User-Agent": "open-local-weather/1.0 (+https://github.com/dissent00/open-local-weather)"}

    def fetch_forecast(self) -> MetServiceForecast:
        if self._cached is not None:
            return self._cached
        self._cached = self._fetch_uncached()
        return self._cached

    def _fetch_uncached(self) -> MetServiceForecast:
        def failed(message: str) -> MetServiceForecast:
            return MetServiceForecast(text=f"{SOURCE_NAME}: {message}", prediction=None, outlook=None, valid_for=None)

        try:
            landing = requests.get(self.landing_url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
            landing.raise_for_status()
            post_url = find_latest_daily_post_url(landing.text, self.landing_url)
            if not post_url:
                return failed("no daily forecast post found on the landing page.")

            post = requests.get(post_url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
            post.raise_for_status()
            pdf_url = find_pdf_url(post.text, post_url)
            if not pdf_url:
                return failed("latest daily forecast post has no PDF link.")

            pdf = requests.get(pdf_url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
            pdf.raise_for_status()
            text, tables = extract_text_and_tables(pdf.content)
        except requests.RequestException as exc:
            return failed(f"fetch failed ({type(exc).__name__}).")
        except Exception as exc:  # pdfplumber raises a wide variety on bad input
            return failed(f"could not read the forecast PDF ({type(exc).__name__}).")

        if not text.strip():
            return failed("PDF fetched but no text could be extracted.")

        valid_for = parse_validity_date(text)
        outlook = parse_county_outlook(tables, self.area_name) if self.area_name else None
        prediction = outlook_to_prediction(outlook, self.model_id) if outlook is not None else None
        return MetServiceForecast(text=text, prediction=prediction, outlook=outlook, valid_for=valid_for)

    def fetch(self) -> str:
        """BulletinFetcher protocol — the human-readable half."""
        return self.fetch_forecast().text
