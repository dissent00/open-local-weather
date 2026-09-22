"""GitHub Pages publishing: renders docs/ from the latest DailyLogEntry plus
an append-only per-day archive.

GitHub Pages serves directly from the committed docs/ folder (Settings ->
Pages -> Deploy from branch -> /docs) — no separate deploy action or build
step needed; this module's output IS the site. forecast.yml already commits
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

import re

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader

from openlocalweather.observed import describe_observed_so_far
from openlocalweather.publish.narrative import narrative_to_html
from openlocalweather.tiles import compose_tiles

from openlocalweather.aqi import hours_old, is_stale, summarize_ground_aqi
from openlocalweather.config import LocationConfig
from openlocalweather.dates import format_date
from openlocalweather.glossary import GLOSSARY
from openlocalweather.models import DailyLogEntry, IssuanceSnapshot
from openlocalweather.review import WeeklyReview

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    # AUTOESCAPE IS UNCONDITIONAL, and that is a fix rather than a preference.
    #
    # This read `select_autoescape(["html"])` until 2026-09-10, which looks
    # like the control is on and was not. select_autoescape matches the
    # template FILENAME's extension, and every template here is
    # `*.html.jinja` — extension `.jinja`, which is not in that list. It
    # resolved to False for all four, and had done since the templates were
    # written.
    #
    # It surfaced when a run put a 15,930-character repetition loop into
    # uv_index_max and the published page came out carrying five raw </em>
    # tags and a <td>, with not one escaped entity anywhere in it. What
    # reached the page that day was nonsense; what reaches it in general is
    # whatever the model emits, onto a public site.
    #
    # `True` rather than a corrected extension list because every template in
    # this directory is HTML and always will be — a list is one more thing
    # that can be right-looking and wrong. The single value that IS markup,
    # the markdown-rendered narrative, carries `| safe` in the template, which
    # is the shape the templates were written for.
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _narrative_html(entry: DailyLogEntry) -> str:
    # Sanitised, because this is the one value on the page that autoescape
    # cannot protect — see publish/narrative.py.
    return narrative_to_html(entry.narrative_markdown)


def _first_issuance(entry: DailyLogEntry) -> IssuanceSnapshot | None:
    """The day's FIRST issuance, or None when the day was issued once —
    ROADMAP item 137.

    READ THROUGH `issuance_log()`, WHICH IS WHY THIS EXISTS. Every call site
    here used to test `entry.morning_issuance` directly, which is a leftover
    of the dead morning/evening model and was duplicated into
    `earlier_issuances` on every later run. `issuance_log()` already reads
    both record shapes and prefers the newer one, so going through it lets
    the duplicate stop being written without orphaning the pages it feeds.

    THE ARCHIVE IS NOT MIGRATED. Entries committed before `earlier_issuances`
    existed carry the first issuance only under the legacy name, and they
    keep rendering through `issuance_log()`'s fallback branch — see its
    docstring for why editing history is the one fix not available here.

    ONE ISSUANCE IS NOT A FIRST ISSUANCE. A day with a single run has nothing
    to disambiguate itself from, and a second page would duplicate the main
    one. That is exactly what `morning_issuance is None` used to mean, so the
    guard is unchanged in behaviour for both shapes.

    NOT NAMED "morning". The first issuance of the day is the morning one in
    THIS deployment because of when its crons fire; a deployment whose first
    run lands in the evening would be described wrongly by that name. The
    published URL keeps its `-morning` suffix for now — those links are live
    and renaming them orphans the archive — which is a separate decision from
    what the code calls it.
    """
    log = entry.issuance_log()
    return log[0] if len(log) > 1 else None


def _entry_as_morning_view(entry: DailyLogEntry) -> DailyLogEntry:
    """Reconstructs what `entry` looked like right before an evening
    refresh overwrote it, as a full DailyLogEntry — not just the raw
    IssuanceSnapshot fields — so the exact same forecast_page
    template renders it with zero morning/evening-aware branching baked
    into the template itself. Only ever called when `_first_issuance` returns
    something. Both issuance stores are cleared on the result (nothing to
    nest — a page showing the first issuance has no "first within the first"
    concept)."""
    m = _first_issuance(entry)
    assert m is not None
    return entry.model_copy(
        update={
            "rain_expected": m.rain_expected,
            "onset_window": m.onset_window,
            "peak_wind_primary_kmh": m.peak_wind_primary_kmh,
            "peak_wind_secondary_kmh": m.peak_wind_secondary_kmh,
            "temp_high_c": m.temp_high_c,
            "temp_low_c": m.temp_low_c,
            "temp_high_low_display": m.temp_high_low_display,
            "mslp_trend_24h": m.mslp_trend_24h,
            "synoptic_pattern": m.synoptic_pattern,
            "uv_index_max": m.uv_index_max,
            "air_quality_aqi": m.air_quality_aqi,
            # CLEARED, NOT COPIED: an IssuanceSnapshot does not carry these,
            # so this page has no record of them and must not borrow the
            # CURRENT run's. Leaving them alone would hand a page labelled
            # "Morning" the evening run's sky, its wind and the halves of its
            # UV and AQI, sitting beside that morning's own display strings.
            # ROADMAP item 159 steps 2-4 put all four on the day record
            # deliberately, because the tiles always show the current run —
            # which is exactly why an archived page cannot have them.
            "cloud_anchors": [],
            "wind_anchors": [],
            "uv_index": None,
            "air_quality_index": None,
            "ground_aqi": m.ground_aqi,
            "narrative_markdown": m.narrative_markdown,
            "whatsapp_summary": m.whatsapp_summary,
            "morning_issuance": None,
            "earlier_issuances": [],
            # This issuance's own gaps, not the current one's — see
            # RunDegradation. Without the override an archived morning page
            # would report whatever the EVENING run happened to be missing,
            # which is the same class of lie the snapshot exists to prevent.
            "meta": entry.meta.model_copy(
                update={
                    "generated_at_utc": m.generated_at_utc,
                    "refreshed_at": None,
                    "degradations": m.degradations,
                }
            ),
        }
    )


def _issuance_label(entry: DailyLogEntry, *, first: bool) -> str | None:
    """Small "which issuance is this" tag shown in a page's meta line —
    e.g. "Updated 15:02". None for a page with nothing to disambiguate (a day
    that was never refreshed has only one issuance, and doesn't need a label
    saying so).

    THE PARAMETER IS `first`, NOT `morning`. It was `morning` until
    2026-09-22, which is the same claim the labels below dropped in 09-14 —
    a first issuance at 14:00 is not a morning — argued against by this very
    docstring while the signature went on making it. `morning_issuance` and
    the `-morning` URL slug keep their names because they are stored data and
    live links; a private parameter has no such obligation.

    IT NAMES THE CLOCK, NOT A TIME OF DAY. These read "Morning Issuance" and
    "Evening Update" until 2026-09-14, which was accurate only while the
    schedule was exactly two runs, at 03:01Z and 15:01Z. ROADMAP item 104
    removed that structure — a run is now whatever the day's business makes
    it, at any hour — and item 121's refresh path makes an hourly cron the
    expected shape rather than an exotic one. The labels did not follow, so a
    first issuance at 14:00 rendered as "Morning Issuance — 14:00" and an
    update at 09:00 as "Evening Update — 09:00", each contradicting the clock
    printed beside it.

    "Issued" and "Updated" carry the distinction that is still true — which
    issuance of the day this is — and drop the claim that stopped being true.

    LOCAL WHERE THE ENTRY KNOWS ITS LOCAL CLOCK, and this stopped being
    cosmetic with ROADMAP item 121's observation-only refresh. The observed
    block beside this label is stamped in LOCAL time, and until item 121 both
    described the same moment — so a reader comparing "15:02 UTC" against
    "As of 18:02" was reading one instant written two ways, and the worst
    outcome was mild confusion.

    The two are now genuinely different moments and the reader is MEANT to
    compare them: that is the whole point of stamping them separately. A unit
    mismatch then does not merely confuse, it can invert the comparison. At
    UTC+3 a forecast at 15:01 UTC beside observations "As of 15:45" reads as
    44 minutes of elapsed day when it is three hours and 44; west of
    Greenwich it is worse, and observations genuinely newer than the forecast
    render as older than it.

    Falls back to UTC for entries and snapshots written before
    `issued_local_time` existed, where the zone suffix is still honest because
    the number really is UTC. That fallback is the ONLY place a UTC clock now
    reaches a reader, and the two labels sit side by side in the archive
    index — which is why `IssuanceSnapshot` records its own local clock rather
    than having one derived from its UTC stamp here. Deriving would assume the
    deployment has never moved zones, and `reconcile_now` means the two are
    not always the same instant anyway.
    """
    if first:
        first = _first_issuance(entry)
        assert first is not None
        if first.issued_local_time:
            return f"Issued {first.issued_local_time}"
        return f"Issued {first.generated_at_utc.strftime('%H:%M UTC')}"

    if entry.meta.refreshed_at is None:
        return None

    if entry.meta.issued_local_time:
        return f"Updated {entry.meta.issued_local_time}"

    return f"Updated {entry.meta.refreshed_at.strftime('%H:%M UTC')}"


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
    glossary: str


def build_nav_links(base_url: str, github_repo: str) -> NavLinks:
    base = base_url if base_url.endswith("/") else base_url + "/"
    return NavLinks(
        home=base,
        archive=base + "archive/",
        subscribe=base + "subscribe.html",
        css=base + "assets/style.css",
        github=f"https://github.com/{github_repo}",
        accuracy=base + "accuracy.html",
        glossary=base + "glossary.html",
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
        # THE SAME COMPOSER THE APP AND THE EMAIL USE — item 159 step 6.
        # Metric here because the page has no reader setting to consult; the
        # unit lives in each tile's header, so adding one later is a flag on
        # this call and no change to any value string.
        tiles=compose_tiles(entry.model_dump(mode="json"), metric=True),
        # Composed through the same function the prompt uses, from the reading
        # stored on the entry — item 121. Not stored pre-composed, so the
        # wording is fixable for every day already written.
        # THE CLOCK THE RUN USED, read off the entry rather than re-derived
        # from the instant and the zone. Those differ whenever reconcile_now
        # overrode a wrong system clock, and the page must not then show a
        # time the forecaster was never given.
        #
        # STAMPED WITH THE OBSERVATION'S OWN CLOCK, not the forecast's — item
        # 121's no-LLM refresh path lets the two come apart, so a page can
        # carry a narrative reasoned at 06:00 beside a station reading taken
        # at 14:00. Showing the issuance time on both would date the reading
        # eight hours early and tell a reader the day had been quiet since
        # dawn. Falls back to the issuance time for entries written before
        # the field existed, which is what those entries meant.
        observed_so_far=describe_observed_so_far(
            entry.observed_so_far,
            as_of=entry.meta.observations_local_time or entry.meta.issued_local_time,
        ),
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
        if entry is None or _first_issuance(entry) is None:
            # No entry yet (shouldn't normally happen — all_dates_provider
            # is derived from what's on disk) or never refreshed: exactly
            # one issuance, no label needed to disambiguate it.
            items.append(ArchiveItem(date=d, slug=slug, label=None))
            continue
        items.append(ArchiveItem(date=d, slug=slug, label=_issuance_label(entry, first=False)))
        items.append(ArchiveItem(date=d, slug=f"{slug}-morning", label=_issuance_label(entry, first=True)))
    return items


def render_glossary_page(location: LocationConfig, nav: NavLinks) -> str:
    """ROADMAP item 56. Static, so it takes no forecast and no review — the
    page is identical every run and re-rendering it costs nothing.

    The slug is built here rather than stored on the entry: it is a rendering
    concern, and putting it in glossary.py would put an HTML detail into the
    file the app also reads.
    """
    rows = [
        {
            "term": e.term,
            "definition": e.definition,
            "source": e.source,
            "slug": re.sub(r"[^a-z0-9]+", "-", e.term.lower()).strip("-"),
        }
        for e in GLOSSARY
    ]
    template = _env().get_template("glossary.html.jinja")

    return template.render(glossary=rows, location=location, nav=nav)


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
                    entry, self.location, self.nav, is_latest=False, issuance_label=_issuance_label(entry, first=False)
                )
            )

        if _first_issuance(entry) is not None:
            morning_page = archive_dir / f"{format_date(entry.date)}-morning.html"
            if force or not morning_page.exists():
                morning_page.write_text(
                    render_forecast_page(
                        _entry_as_morning_view(entry),
                        self.location,
                        self.nav,
                        is_latest=False,
                        issuance_label=_issuance_label(entry, first=True),
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
        # actually want. See IssuanceSnapshot's doc comment
        # (models.py) for the fuller history.
        index_html = render_forecast_page(
            entry, self.location, self.nav, is_latest=True, issuance_label=_issuance_label(entry, first=False)
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

        # Static, so it is written unconditionally and needs no provider —
        # unlike the accuracy page, which cannot exist before there is a
        # record to score. Rewritten every run because that is cheaper than
        # deciding whether a definition changed.
        (self.docs_dir / "glossary.html").write_text(
            render_glossary_page(self.location, self.nav)
        )

        if self.review_provider is not None:
            review = self.review_provider()
            if review is not None:
                (self.docs_dir / "accuracy.html").write_text(
                    render_accuracy_page(review, self.location, self.nav)
                )
