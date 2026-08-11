"""Local-bulletin fetcher for Kenya Meteorological Department (KMD) — the
location this project was originally built for (local_bulletin_url in the
shipped config/location.yaml points at KMD's 7-day forecast page).

Confirmed live (2026-08) by downloading an actual bulletin: KMD's weekly
forecast PDFs are digitally generated with real, extractable text — NOT
scanned images. pdfplumber alone suffices; no OCR dependency needed. This
resolves the open question the earlier stub was waiting on.

The site structure has also changed since the original Apps Script
reference was written — the landing page no longer links a PDF directly.
It now lists dated weekly posts (confirmed newest-first, in page order),
and each post page links its own PDF. So this is a two-hop scrape: landing
page -> latest dated post -> PDF link -> extracted text. Each hop is a
small, independently testable function, so a future KMD site change breaks
one function, not the whole fetcher — this is exactly the kind of
location-specific fragility the module docstring in
fetch/bulletin/__init__.py warns forks about.

Never raises — always returns an explanatory string (real bulletin text,
or an "unavailable"/error message), matching BulletinFetcher's contract.
"""

from __future__ import annotations

import re
from io import BytesIO
from urllib.parse import urljoin

import pdfplumber
import requests

SOURCE_NAME = "Kenya Meteorological Department (KMD)"
REQUEST_TIMEOUT_S = 30

# Matches KMD's current WordPress post-slug pattern for weekly forecast
# posts, e.g. ".../7-days-forecast/seven-day-forecast-11th-to-17th-august-
# 2026/". If KMD changes this slug pattern, this is the one place to update.
_POST_LINK_RE = re.compile(r'href="(/our-products/7-days-forecast/seven-day-forecast[^"]*)"')
_PDF_LINK_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)


def find_latest_forecast_post_url(landing_html: str, base_url: str) -> str | None:
    """The landing page lists posts newest-first in page order (each
    post's thumbnail/title/"read more" all link the same URL, confirmed on
    the live page) — the first match is reliably the current week's post.
    """
    match = _POST_LINK_RE.search(landing_html)
    return urljoin(base_url, match.group(1)) if match else None


def find_pdf_url(post_html: str, base_url: str) -> str | None:
    match = _PDF_LINK_RE.search(post_html)
    return urljoin(base_url, match.group(1)) if match else None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n\n".join(p for p in pages if p).strip()


class KenyaKMDBulletinFetcher:
    def __init__(self, bulletin_url: str):
        self.bulletin_url = bulletin_url

    def fetch(self) -> str:
        try:
            landing_resp = requests.get(self.bulletin_url, timeout=REQUEST_TIMEOUT_S)
            landing_resp.raise_for_status()
            post_url = find_latest_forecast_post_url(landing_resp.text, self.bulletin_url)
            if not post_url:
                return f"{SOURCE_NAME}: could not find a current forecast post link on the landing page."

            post_resp = requests.get(post_url, timeout=REQUEST_TIMEOUT_S)
            post_resp.raise_for_status()
            pdf_url = find_pdf_url(post_resp.text, post_url)
            if not pdf_url:
                return f"{SOURCE_NAME}: could not find a PDF link on the forecast post page."

            pdf_resp = requests.get(pdf_url, timeout=REQUEST_TIMEOUT_S)
            pdf_resp.raise_for_status()
            text = extract_pdf_text(pdf_resp.content)
            return text if text else f"{SOURCE_NAME}: PDF fetched but no text could be extracted."
        except requests.RequestException as e:
            return f"Error reading {SOURCE_NAME} bulletin: {e}"
        except Exception as e:  # pdfplumber parse failures, malformed PDFs, etc.
            return f"Error reading {SOURCE_NAME} bulletin: {e}"
