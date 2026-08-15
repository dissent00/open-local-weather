"""GitHub Pages publishing: renders docs/ from the latest DailyLogEntry plus
an append-only per-day archive.

GitHub Pages serves directly from the committed docs/ folder (Settings ->
Pages -> Deploy from branch -> /docs) — no separate deploy action or build
step needed; this module's output IS the site. daily.yml already commits
docs/ alongside data/ each run (see that workflow's "Commit and push" step).

All internal links are built as ABSOLUTE URLs off `base_url` rather than
relative paths. This deliberately sidesteps the classic GitHub Pages
project-site gotcha where relative paths resolve differently depending on
how deep the current page is nested (docs/index.html vs
docs/archive/2026-08-11.html) — an absolute base_url makes every page's nav
links identical and easy to reason about, at the minor cost of the site not
being trivially relocatable to a different URL without a regenerate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from openlocalweather.aqi import hours_old, is_stale, summarize_ground_aqi
from openlocalweather.config import LocationConfig
from openlocalweather.dates import format_date
from openlocalweather.models import DailyLogEntry

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _narrative_html(entry: DailyLogEntry) -> str:
    return markdown.markdown(entry.narrative_markdown, extensions=["extra"])


def _morning_narrative_html(entry: DailyLogEntry) -> str | None:
    if entry.morning_issuance is None:
        return None
    return markdown.markdown(entry.morning_issuance.narrative_markdown, extensions=["extra"])


@dataclass
class NavLinks:
    home: str
    archive: str
    # Not yet linked from any template — there's no subscribe.html to point
    # to yet (see publish/email_gmail.py's module docstring for why: no
    # safe self-serve form without a verified-domain ESP). Kept here so
    # re-adding the nav link later is a one-line template change, not a
    # NavLinks/build_nav_links rework.
    subscribe: str
    css: str
    github: str


def build_nav_links(base_url: str, github_repo: str) -> NavLinks:
    base = base_url if base_url.endswith("/") else base_url + "/"
    return NavLinks(
        home=base,
        archive=base + "archive/",
        subscribe=base + "subscribe.html",
        css=base + "assets/style.css",
        github=f"https://github.com/{github_repo}",
    )


def render_forecast_page(
    entry: DailyLogEntry, location: LocationConfig, nav: NavLinks, is_latest: bool
) -> str:
    # Staleness is judged relative to when the FORECAST was generated, not
    # when this page happens to be rendered — the archive backfill (see
    # GitHubPagesPublisher.publish) can re-render an old entry's page long
    # after the fact, and real wall-clock "now" would then mark every
    # archived reading as impossibly stale relative to whoever's viewing it
    # today. generated_at_utc is the correct fixed reference point.
    reference_time = entry.meta.generated_at_utc
    template = _env().get_template("forecast.html.jinja")
    return template.render(
        entry=entry,
        location=location,
        nav=nav,
        is_latest=is_latest,
        narrative_html=_narrative_html(entry),
        morning_narrative_html=_morning_narrative_html(entry),
        # Rendered deterministically from the raw per-station readings, not
        # trusted to LLM narrative — same "code does the data, LLM does the
        # prose" split as everywhere else in this project. See aqi.py.
        ground_aqi_summary=summarize_ground_aqi(entry.ground_aqi, now=reference_time),
        ground_aqi_stations=[
            (station, hours_old(station, reference_time), is_stale(station, reference_time))
            for station in entry.ground_aqi
        ],
    )


def render_archive_index_page(dates: list[date], location: LocationConfig, nav: NavLinks) -> str:
    template = _env().get_template("archive_index.html.jinja")
    return template.render(dates=sorted(dates, reverse=True), location=location, nav=nav)


class GitHubPagesPublisher:
    """Publisher implementation for pipeline.py's Publisher Protocol."""

    def __init__(
        self,
        docs_dir: str | Path,
        location: LocationConfig,
        base_url: str,
        github_repo: str,
        all_dates_provider: Callable[[], list[date]],
        entry_provider: Callable[[date], DailyLogEntry | None] | None = None,
    ):
        self.docs_dir = Path(docs_dir)
        self.location = location
        self.nav = build_nav_links(base_url, github_repo)
        # Callable returning every date with a published log entry —
        # injected so this module doesn't need its own store dependency
        # beyond what the caller (pipeline.py, via cli.py's wiring) already
        # has, matching the same pattern as verify/scoring's LogLookup.
        self.all_dates_provider = all_dates_provider
        # Same injection pattern, for backfilling archive pages — see
        # publish()'s backfill step for why that's needed.
        self.entry_provider = entry_provider

    def publish(self, entry: DailyLogEntry) -> None:
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.docs_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        index_html = render_forecast_page(entry, self.location, self.nav, is_latest=True)
        (self.docs_dir / "index.html").write_text(index_html)

        entry_html = render_forecast_page(entry, self.location, self.nav, is_latest=False)
        (archive_dir / f"{format_date(entry.date)}.html").write_text(entry_html)

        all_dates = self.all_dates_provider()

        # Backfill any archive page that's missing. The index is generated
        # from data/log/ (the source of truth), but only TODAY's page is
        # written above — so any date whose page was never rendered would
        # otherwise be listed in the index as a dead link. That isn't
        # hypothetical: it happens to every entry written by a run where
        # publishing was skipped (no --public-url configured), which is
        # exactly how this repo's own first forecast was created before
        # Pages was wired up. Cheap: a no-op once every page exists.
        if self.entry_provider is not None:
            for d in all_dates:
                page = archive_dir / f"{format_date(d)}.html"
                if page.exists():
                    continue
                past_entry = self.entry_provider(d)
                if past_entry is None:
                    continue
                page.write_text(
                    render_forecast_page(past_entry, self.location, self.nav, is_latest=False)
                )

        archive_index_html = render_archive_index_page(all_dates, self.location, self.nav)
        (archive_dir / "index.html").write_text(archive_index_html)
