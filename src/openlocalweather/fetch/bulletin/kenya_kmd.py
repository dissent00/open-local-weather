"""Reference local-bulletin fetcher for Kenya Meteorological Department
(KMD) — the location this project was originally built for
(local_bulletin_url in the shipped config/location.yaml points at KMD's
7-day forecast page).

STATUS: stubbed pending a decision on whether KMD's forecast PDFs are
digitally-generated text (pdfplumber would suffice) or scanned images
(needs real OCR — pytesseract + poppler, a heavier dependency). The
original Apps Script version sidestepped this entirely by uploading the PDF
to Google Drive and using Drive's built-in OCR-on-convert
(fetchLocalBulletinText() in the reference script), which has no direct
Python equivalent. Never raises — always returns an explanatory
"unavailable" message, so a fork using this as-is never breaks its pipeline
run over a missing bulletin section.
"""

from __future__ import annotations

SOURCE_NAME = "Kenya Meteorological Department (KMD)"


class KenyaKMDBulletinFetcher:
    """Not yet implemented — see module docstring for what's needed before
    porting the PDF-scrape logic for real."""

    def __init__(self, bulletin_url: str):
        self.bulletin_url = bulletin_url

    def fetch(self) -> str:
        return (
            f"{SOURCE_NAME}: bulletin fetching not yet implemented in this "
            "Python port (the original Apps Script version relied on Google "
            "Drive's OCR-on-upload — see module docstring for the pending "
            "text-vs-scanned-image decision needed to port this properly)."
        )
