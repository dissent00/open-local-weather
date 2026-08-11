"""Local met-service bulletin fetching — the one fetch source designed to be
swapped by writing new code per fork, not just reconfigured.

Bulletin format varies wildly across local met services (HTML, a PDF behind
an HTML landing page, scanned images needing OCR, sometimes nothing usable
at all), so there is no one implementation that works for every location.
`local_bulletin_url` in location.yaml selects WHETHER a bulletin is fetched
at all (empty = skip via NullBulletinFetcher); WHICH fetcher class actually
runs for a non-empty URL is a small wiring decision made where the pipeline
constructs its fetchers (see pipeline.py), not something this module tries
to auto-detect from the URL shape.

A fork with a different local met service should add a new module here
implementing the BulletinFetcher protocol (kenya_kmd.py is the reference
example) and wire it up in pipeline.py.
"""

from __future__ import annotations

from typing import Protocol


class BulletinFetcher(Protocol):
    def fetch(self) -> str:
        """Returns human-readable bulletin text, or an explanatory
        "unavailable" message. Must never raise — a failure here should
        degrade the run gracefully (missing bulletin section), not abort
        it, matching the original pipeline's try/catch-and-continue
        behavior for this specific source.
        """
        ...


class NullBulletinFetcher:
    """Default when location.local_bulletin_url is empty. Matches the
    original pipeline's graceful-skip behavior for locations with no
    configured local bulletin source."""

    def fetch(self) -> str:
        return "No local bulletin source configured for this location."
