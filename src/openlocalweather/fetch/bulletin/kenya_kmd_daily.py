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
from datetime import date, datetime, time, timedelta
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import pdfplumber
import requests

from openlocalweather.fetch.bulletin.kmd_5day_parse import (
    outlook_for_date as five_day_outlook_for_date,
)
from openlocalweather.fetch.bulletin.kmd_5day_parse import (
    outlook_to_prediction as five_day_to_prediction,
)
from openlocalweather.fetch.bulletin.kmd_daily_parse import CountyOutlook, parse_county_outlook
from openlocalweather.models import ModelPrediction

SOURCE_NAME = "Kenya Meteorological Department (KMD)"
REQUEST_TIMEOUT_S = 30
HTTP_OK = 200

# "VALIDITY: From 9:00 p.m. Tonight to 9:00 p.m. Tomorrow, 19th August" —
# the bulletin's own 24 hours, local time, closing on the date it names.
VALIDITY_OPENS_HOUR = 21

_POST_LINK_RE = re.compile(r'href="(/our-products/daily-forecast/daily-weather-forecast[^"]*)"')
# Both slugs KMD has used: "five-day-weather-forecast-..." through September
# and "5-day-weather-forecast-..." since October 2026. The pattern written
# on 2026-08-19 looked for "five-day-forecast", which matched neither, so
# no run before 2026-10-11 ever held a met Day+3 (0 of 60 days).
_FIVE_DAY_POST_RE = re.compile(r'href="(/our-products/5-day-forecast/(?:five-day|5-day)-[^"]*)"')
_DOCUMENT_ID_RE = re.compile(r"/documents/(\d+)/")
# THE UNLINKED PDF — 2026-10-11. Since 2026-10-08 KMD's daily post is a
# shell: a title, no body, no link. The PDF is still uploaded, one document
# id further on each day (5246 on the 8th, 5261 on the 11th), and the CMS
# serves it only under its exact filename, which follows the post's slug.
# The fetcher probes ids after the last one it saw. Ninety, since
# 2026-10-14: the daily PDFs sat at 5263, 5266 and 5297 on the 12th, 13th
# and 14th, about seventeen uploads a day across all of KMD's products,
# and a span of thirty from a two-day-old id missed the 14th's by four. A
# miss costs the day's met row; the HEADs cost nothing.
DOCUMENT_PROBE_SPAN = 90
# Where a deployment with no stored id starts: the 11th's, dated here.
DOCUMENT_ID_SEED = 5261
# Words the filenames keep lowercase.
_LOWERCASE_WORDS = {"to", "from"}
_PDF_LINK_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
_VALIDITY_RE = re.compile(r"VALIDITY:.*?(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})", re.I | re.S)


@dataclass
class MetServiceForecast:
    """What the bulletins yield: prose for the prompt, numbers for scoring.

    `text` is a compact, location-relevant EXTRACT, not the whole document.
    The full daily PDF runs ~8,700 characters covering all 47 counties plus
    letterhead and glossary, of which a handful of lines concern any one
    location. Storing and prompting the lot would put ~3MB a year of mostly
    irrelevant text into git and spend prompt budget diluting the few lines
    that matter.
    """

    text: str
    prediction: ModelPrediction | None
    prediction_day3: ModelPrediction | None
    outlook: CountyOutlook | None
    valid_for: date | None
    five_day_valid_for: date | None = None
    # The id KMD's CMS gave the daily PDF, linked or found by probing; the
    # next run's probe starts after it.
    document_id: int | None = None


def find_latest_daily_post_url(landing_html: str, base_url: str) -> str | None:
    """Posts are listed newest-first, one per day."""
    match = _POST_LINK_RE.search(landing_html)
    return urljoin(base_url, match.group(1)) if match else None


def find_pdf_url(post_html: str, post_url: str) -> str | None:
    match = _PDF_LINK_RE.search(post_html)
    return urljoin(post_url, match.group(1)) if match else None


def candidate_filenames(post_url: str) -> list[str]:
    """The filenames a post's PDF may carry, from its slug: each word
    title-cased and joined by underscores ("Daily_Weather_Forecast_Valid_
    11th_October_2026.pdf"), "Valid" then "valid" since the record holds
    both; a five-day post's name carries "from" after "Valid" (2026-10-02
    and 10-03), tried first, with the plain form after it."""
    slug = post_url.rstrip("/").rsplit("/", 1)[-1]
    words = [w if w in _LOWERCASE_WORDS else w[:1].upper() + w[1:] for w in slug.split("-")]
    names = []
    for valid in ("Valid", "valid"):
        base = [valid if w.lower() == "valid" else w for w in words]
        if "to" in words and "Valid" in words:
            at = base.index(valid)
            names.append("_".join([*base[: at + 1], "from", *base[at + 1 :]]) + ".pdf")
        names.append("_".join(base) + ".pdf")
    return names


def document_id_of(pdf_url: str) -> int | None:
    match = _DOCUMENT_ID_RE.search(pdf_url)
    return int(match.group(1)) if match else None


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


def national_lines_mentioning(text: str, area_name: str, limit: int = 4) -> list[str]:
    """Lines from the bulletin's national outlook that name this area.

    KMD's Part I lists which counties expect rain, cloud or strong winds in
    long comma-separated sentences. The sentences that name this location
    are worth keeping; the rest describe the other 46 counties.
    """
    if not area_name:
        return []
    needle = area_name.lower()
    kept = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or needle not in line.lower():
            continue
        # Skip the county-table row for this area: it is the same content
        # the structured section above already carries, and it arrives as
        # run-together column text ("Kisumu 30 19 Partly cloudy...").
        if re.match(rf"^{re.escape(area_name)}\s+\d", line, re.IGNORECASE):
            continue
        # Skip fragments too short to be a real outlook sentence.
        if len(line) < 40:
            continue
        kept.append(line)
        if len(kept) >= limit:
            break
    return kept


def compose_extract(
    area_name: str,
    valid_for: date | None,
    outlook: CountyOutlook | None,
    national_lines: list[str],
    five_day: list[tuple[date, list[str], float | None, float | None]],
) -> str:
    """The location-relevant slice, as readable text.

    This is what gets stored in the log and handed to the LLM, in place of
    the full document. Deliberately keeps the met service's OWN WORDS rather
    than only the decoded booleans: the narrative benefits from "moderate
    showers" over `rain=True`, and a stored record of the actual phrasing is
    what makes the decoding auditable after the fact.
    """
    parts = [f"{SOURCE_NAME} — forecast for {area_name or 'the region'}"]
    if valid_for:
        parts.append(f"Valid for {valid_for.isoformat()}.")
    if outlook is not None:
        if outlook.high_c is not None or outlook.low_c is not None:
            parts.append(f"Max {outlook.high_c}C / Min {outlook.low_c}C.")
        for label, period in zip(("Tonight", "Morning", "Afternoon"), outlook.periods):
            parts.append(f"{label}: {period}")
    if national_lines:
        parts.append("National outlook mentions this area:")
        parts.extend(f"  - {line}" for line in national_lines)
    if five_day:
        parts.append("Five-day outlook:")
        for target, periods, high, low in five_day:
            temps = f" (max {high}C / min {low}C)" if high is not None or low is not None else ""
            parts.append(f"  {target.isoformat()}{temps}: {' '.join(periods)}")
    return "\n".join(parts)


class KenyaKMDDailyFetcher:
    """BulletinFetcher plus a structured-prediction accessor.

    Caches the parsed result for the lifetime of the instance so the two
    consumers share one HTTP round trip; the pipeline builds a fresh
    instance per run, so there is no staleness across days.
    """

    def __init__(
        self,
        landing_url: str,
        area_name: str = "",
        model_id: str = "local_met_service",
        five_day_url: str = "",
        day3_target: date | None = None,
        document_id_hint: int | None = None,
    ):
        self.landing_url = landing_url
        # The last document id a run stored, or None; see DOCUMENT_PROBE_SPAN.
        self.document_id_hint = document_id_hint
        self.document_id: int | None = None
        self.area_name = area_name
        self.model_id = model_id
        # KMD publishes the five-day bulletin under a sibling path. Derived
        # rather than configured because this class is already KMD-specific;
        # a fork writes its own fetcher, not a different URL here.
        self.five_day_url = five_day_url or landing_url.replace("daily-forecast", "5-day-forecast")
        # Which date the Day+3 slot wants. Injected rather than computed from
        # the clock so the fetcher stays testable and timezone decisions stay
        # in the pipeline, which already owns them.
        self.day3_target = day3_target
        self._cached: MetServiceForecast | None = None

    def validity_window_opens(self, valid_for: date) -> datetime:
        """Where the 24 hours a bulletin valid for `valid_for` claims begin:
        21:00 local the evening before. Item 139 scores its Day+0 on them."""
        return datetime.combine(valid_for - timedelta(days=1), time(VALIDITY_OPENS_HOUR))

    def _headers(self) -> dict:
        return {"User-Agent": "open-local-weather/1.0 (+https://github.com/dissent00/open-local-weather)"}

    def fetch_forecast(self) -> MetServiceForecast:
        if self._cached is not None:
            return self._cached
        self._cached = self._fetch_uncached()
        return self._cached

    def _fetch_pdf_tables(self, landing_url: str, post_pattern: re.Pattern) -> tuple[str, list]:
        """landing -> newest post -> PDF -> (text, tables). Raises on failure;
        callers convert that into a graceful message."""
        landing = requests.get(landing_url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
        landing.raise_for_status()
        match = post_pattern.search(landing.text)
        if not match:
            raise ValueError("no forecast post found")
        post_url = urljoin(landing_url, match.group(1))

        # The post page may fail on its own (HTTP 500 on 2026-10-13 while
        # the PDF sat at the next id): its link is read when it answers,
        # and the probe needs only the slug the landing page gave.
        post = requests.get(post_url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
        linked = find_pdf_url(post.text, post_url) if post.ok else None
        pdf_url = linked or self._probe_unlinked_pdf(post_url)
        if not pdf_url:
            raise ValueError("post has no PDF link")
        found = document_id_of(pdf_url)
        if found is not None:
            self.document_id = max(found, self.document_id or 0)

        pdf = requests.get(pdf_url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
        pdf.raise_for_status()
        return extract_text_and_tables(pdf.content)

    def _probe_unlinked_pdf(self, post_url: str) -> str | None:
        """The PDF's URL by document id when the post does not link it —
        ids after the hint, each under the slug's candidate names."""
        start = (self.document_id_hint or DOCUMENT_ID_SEED) + 1
        names = candidate_filenames(post_url)
        for document_id in range(start, start + DOCUMENT_PROBE_SPAN):
            for name in names:
                url = f"{self._site_root(post_url)}/documents/{document_id}/{name}"
                try:
                    head = requests.head(url, timeout=REQUEST_TIMEOUT_S, headers=self._headers())
                except requests.RequestException:
                    continue
                if head.status_code == HTTP_OK:
                    return url
        return None

    @staticmethod
    def _site_root(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _fetch_five_day(self) -> tuple[ModelPrediction | None, list, date | None]:
        """The Day+3 half. Failures here are silent by design: the five-day
        bulletin is a bonus lead time, and losing it must not cost the day's
        Day+0 prediction or the narrative extract."""
        if not (self.area_name and self.day3_target):
            return None, [], None
        try:
            _, tables = self._fetch_pdf_tables(self.five_day_url, _FIVE_DAY_POST_RE)
        except Exception:
            return None, [], None
        outlook = five_day_outlook_for_date(tables, self.area_name, self.day3_target)
        if outlook is None:
            return None, [], None
        from openlocalweather.fetch.bulletin.kmd_5day_parse import parse_county_days

        rows = [(d.target_date, d.periods, d.high_c, d.low_c) for d in parse_county_days(tables, self.area_name)]
        return five_day_to_prediction(outlook, self.model_id), rows, outlook.target_date

    def _fetch_uncached(self) -> MetServiceForecast:
        def failed(message: str) -> MetServiceForecast:
            return MetServiceForecast(
                text=f"{SOURCE_NAME}: {message}",
                prediction=None, prediction_day3=None, outlook=None, valid_for=None,
                document_id=self.document_id,
            )

        try:
            text, tables = self._fetch_pdf_tables(self.landing_url, _POST_LINK_RE)
        except requests.RequestException as exc:
            return failed(f"fetch failed ({type(exc).__name__}).")
        except ValueError as exc:
            return failed(f"{exc}.")
        except Exception as exc:  # pdfplumber raises widely on bad input
            return failed(f"could not read the forecast PDF ({type(exc).__name__}).")

        if not text.strip():
            return failed("PDF fetched but no text could be extracted.")

        valid_for = parse_validity_date(text)
        outlook = parse_county_outlook(tables, self.area_name) if self.area_name else None
        prediction = outlook_to_prediction(outlook, self.model_id) if outlook is not None else None

        day3_prediction, five_day_rows, five_day_valid_for = self._fetch_five_day()

        return MetServiceForecast(
            text=compose_extract(
                self.area_name,
                valid_for,
                outlook,
                national_lines_mentioning(text, self.area_name),
                five_day_rows,
            ),
            prediction=prediction,
            prediction_day3=day3_prediction,
            outlook=outlook,
            valid_for=valid_for,
            document_id=self.document_id,
            five_day_valid_for=five_day_valid_for,
        )

    def fetch(self) -> str:
        """BulletinFetcher protocol — the human-readable half."""
        return self.fetch_forecast().text
