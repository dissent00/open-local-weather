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
from openlocalweather.review import WeeklyReview

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


def _entry_as_morning_view(entry: DailyLogEntry) -> DailyLogEntry:
    """Reconstructs what `entry` looked like right before an evening
    refresh overwrote it, as a full DailyLogEntry — not just the raw
    MorningIssuanceSnapshot fields — so the exact same forecast_page
    template renders it with zero morning/evening-aware branching baked
    into the template itself. Only ever called when entry.morning_issuance
    is not None. `morning_issuance` is cleared on the result (nothing to
    nest — a page showing the morning issuance has no "morning within the
    morning" concept)."""
    m = entry.morning_issuance
    assert m is not None
    return entry.model_copy(
        update={
            "rain_expected": m.rain_expected,
            "onset_window": m.onset_window,
            "peak_wind_kmh": m.peak_wind_kmh,
            "temp_high_c": m.temp_high_c,
            "temp_low_c": m.temp_low_c,
            "temp_high_low_display": m.temp_high_low_display,
            "mslp_trend_24h": m.mslp_trend_24h,
            "synoptic_pattern": m.synoptic_pattern,
            "uv_index_max": m.uv_index_max,
            "air_quality_aqi": m.air_quality_aqi,
            "ground_aqi": m.ground_aqi,
            "narrative_markdown": m.narrative_markdown,
            "whatsapp_summary": m.whatsapp_summary,
            "morning_issuance": None,
            "meta": entry.meta.model_copy(update={"generated_at_utc": m.generated_at_utc, "refreshed_at": None}),
        }
    )


def _issuance_label(entry: DailyLogEntry, *, morning: bool) -> str | None:
    """Small "which issuance is this" tag shown in a page's meta line —
    e.g. "Evening Update — 15:02 UTC". None for a page with nothing to
    disambiguate (a day that was never refreshed has only one issuance,
    and doesn't need a label saying so)."""
    if morning:
        assert entry.morning_issuance is not None
        return f"Morning Issuance — {entry.morning_issuance.generated_at_utc.strftime('%H:%M UTC')}"
    if entry.meta.refreshed_at is not None:
        return f"Evening Update — {entry.meta.refreshed_at.strftime('%H:%M UTC')}"
    return None


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
    accuracy: str


def build_nav_links(base_url: str, github_repo: str) -> NavLinks:
    base = base_url if base_url.endswith("/") else base_url + "/"
    return NavLinks(
        home=base,
        archive=base + "archive/",
        subscribe=base + "subscribe.html",
        css=base + "assets/style.css",
        github=f"https://github.com/{github_repo}",
        accuracy=base + "accuracy.html",
    )


@dataclass(frozen=True)
class SkillGroup:
    """One lead time's rows, so the template doesn't filter cells itself."""

    lead_time_days: int
    cells: list


def _signed(value: float) -> str:
    """Formats a signed error, without ever printing "-0.0".

    Rounding a small negative toward zero leaves the sign bit intact, so
    plain formatting renders -0.04 as "-0.0" — which on a page whose entire
    argument is that its numbers are careful reads as a typo.
    """
    rounded = round(value, 1) + 0.0
    return f"{rounded:+.1f}"


def render_accuracy_page(review: WeeklyReview, location: LocationConfig, nav: NavLinks) -> str:
    """The public face of the accuracy claim.

    Deliberately renders the deterministic review directly, with no LLM
    narration anywhere on the page. Everything here is either a count or a
    sentence composed in code from counts, so the page cannot overstate the
    record even by accident — which is the whole point of publishing it.
    """
    groups = []
    for k in sorted({c.lead_time_days for c in review.cells}):
        groups.append(SkillGroup(lead_time_days=k, cells=[c for c in review.cells if c.lead_time_days == k]))
    env = _env()
    env.filters["signed"] = _signed
    template = env.get_template("accuracy.html.jinja")
    return template.render(review=review, skill_groups=groups, location=location, nav=nav)


def render_forecast_page(
    entry: DailyLogEntry, location: LocationConfig, nav: NavLinks, is_latest: bool, issuance_label: str | None = None
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
        issuance_label=issuance_label,
        narrative_html=_narrative_html(entry),
        # Rendered deterministically from the raw per-station readings, not
        # trusted to LLM narrative — same "code does the data, LLM does the
        # prose" split as everywhere else in this project. See aqi.py.
        ground_aqi_summary=summarize_ground_aqi(entry.ground_aqi, now=reference_time),
        ground_aqi_stations=[
            (station, hours_old(station, reference_time), is_stale(station, reference_time))
            for station in entry.ground_aqi
        ],
    )


@dataclass
class ArchiveItem:
    """One row in the archive listing — one per issuance, not one per
    date. A day that was never refreshed gets exactly one item (slug ==
    the bare date, no label needed since there's nothing to disambiguate);
    a refreshed day gets two, evening listed before morning to match the
    listing's overall most-recent-first ordering."""

    date: date
    slug: str  # archive/<slug>.html
    label: str | None


def build_archive_items(
    dates: list[date], entry_provider: Callable[[date], DailyLogEntry | None]
) -> list[ArchiveItem]:
    items: list[ArchiveItem] = []
    for d in sorted(dates, reverse=True):
        entry = entry_provider(d)
        slug = format_date(d)
        if entry is None or entry.morning_issuance is None:
            # No entry yet (shouldn't normally happen — all_dates_provider
            # is derived from what's on disk) or never refreshed: exactly
            # one issuance, no label needed to disambiguate it.
            items.append(ArchiveItem(date=d, slug=slug, label=None))
            continue
        items.append(ArchiveItem(date=d, slug=slug, label=_issuance_label(entry, morning=False)))
        items.append(ArchiveItem(date=d, slug=f"{slug}-morning", label=_issuance_label(entry, morning=True)))
    return items


def render_archive_index_page(items: list[ArchiveItem], location: LocationConfig, nav: NavLinks) -> str:
    template = _env().get_template("archive_index.html.jinja")
    return template.render(items=items, location=location, nav=nav)


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
        review_provider: Callable[[], WeeklyReview | None] | None = None,
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
        # Same again, for the accuracy page. Optional so a caller that
        # doesn't supply one simply doesn't get the page, rather than
        # publishing an empty or misleading one.
        self.review_provider = review_provider

    def _write_archive_pages_for(self, entry: DailyLogEntry, archive_dir: Path, *, force: bool = True) -> None:
        """Writes the archive page(s) for one entry: always the current
        (most-recent) issuance at `<date>.html`, plus `<date>-morning.html`
        when a refresh happened. `force=False` (used by the backfill loop
        below) only writes a page that doesn't already exist yet, so it
        never clobbers something a more specific call already wrote this
        run."""
        current_page = archive_dir / f"{format_date(entry.date)}.html"
        if force or not current_page.exists():
            current_page.write_text(
                render_forecast_page(
                    entry, self.location, self.nav, is_latest=False, issuance_label=_issuance_label(entry, morning=False)
                )
            )

        if entry.morning_issuance is not None:
            morning_page = archive_dir / f"{format_date(entry.date)}-morning.html"
            if force or not morning_page.exists():
                morning_page.write_text(
                    render_forecast_page(
                        _entry_as_morning_view(entry),
                        self.location,
                        self.nav,
                        is_latest=False,
                        issuance_label=_issuance_label(entry, morning=True),
                    )
                )

    def publish(self, entry: DailyLogEntry) -> None:
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = self.docs_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Landing page: always the CURRENT (most-recent) issuance only —
        # never stacked alongside an earlier one. Tried and reverted: it
        # buried whichever issuance was current below the other, forcing a
        # scroll past stale content to reach the thing most readers
        # actually want. See MorningIssuanceSnapshot's doc comment
        # (models.py) for the fuller history.
        index_html = render_forecast_page(
            entry, self.location, self.nav, is_latest=True, issuance_label=_issuance_label(entry, morning=False)
        )
        (self.docs_dir / "index.html").write_text(index_html)

        self._write_archive_pages_for(entry, archive_dir, force=True)

        all_dates = self.all_dates_provider()

        # Backfill any archive page that's missing — the current entry
        # (possibly including its morning issuance) is force-written above;
        # every OTHER date's pages get backfilled only if actually absent.
        # The index is generated from data/log/ (the source of truth), but
        # only today's pages are force-written above — so any date whose
        # pages were never rendered would otherwise be listed in the index
        # as a dead link. That isn't hypothetical: it happens to every
        # entry written by a run where publishing was skipped (no
        # --public-url configured), which is exactly how this repo's own
        # first forecast was created before Pages was wired up — and now
        # also to any already-committed entry that gained a
        # morning_issuance after the fact (e.g. a backfill script) without
        # its own -morning.html existing yet. Cheap: a no-op once every
        # page exists.
        if self.entry_provider is not None:
            for d in all_dates:
                if d == entry.date:
                    continue
                past_entry = self.entry_provider(d)
                if past_entry is None:
                    continue
                self._write_archive_pages_for(past_entry, archive_dir, force=False)

        archive_items = build_archive_items(all_dates, self.entry_provider or (lambda d: None))
        archive_index_html = render_archive_index_page(archive_items, self.location, self.nav)
        (archive_dir / "index.html").write_text(archive_index_html)

        if self.review_provider is not None:
            review = self.review_provider()
            if review is not None:
                (self.docs_dir / "accuracy.html").write_text(
                    render_accuracy_page(review, self.location, self.nav)
                )
