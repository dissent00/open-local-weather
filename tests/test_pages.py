import re
from datetime import date, datetime, timedelta, timezone

from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.models import LogEntryMeta, ModelPredictionsByLead, MorningIssuanceSnapshot
from openlocalweather.models import DailyLogEntry
from openlocalweather.publish.pages import (
    ArchiveItem,
    GitHubPagesPublisher,
    build_archive_items,
    build_nav_links,
    render_archive_index_page,
    render_forecast_page,
)
from openlocalweather.publish.pages import _entry_as_morning_view, _issuance_label

LOCATION = LocationConfig(
    region_name="Test Region",
    primary_place_name="Test Town",
    timezone="UTC",
    primary_point=Point(lat=1.0, lon=2.0),
    secondary_point=SecondaryPoint(),
)


def make_entry(d: date, **overrides) -> DailyLogEntry:
    defaults = dict(
        date=d,
        rain_expected="Likely",
        onset_window="14:00-16:00",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26°C / 79°F",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        uv_index_max="8 (Very High)",
        air_quality_aqi="42 (Good)",
        narrative_markdown="## Overview\nRain **likely** today.\n\n## Today's Forecast\nDetails here.",
        model_predictions=ModelPredictionsByLead(),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="gemini",
            llm_model="gemini-3.6-flash",
            pipeline_version="0.1.0",
        ),
    )
    defaults.update(overrides)
    return DailyLogEntry(**defaults)


# ---------------------------------------------------------------------------
# build_nav_links
# ---------------------------------------------------------------------------


def test_build_nav_links_adds_trailing_slash():
    nav = build_nav_links("https://example.github.io/open-local-weather", "owner/repo")
    assert nav.home == "https://example.github.io/open-local-weather/"
    assert nav.archive == "https://example.github.io/open-local-weather/archive/"
    assert nav.subscribe == "https://example.github.io/open-local-weather/subscribe.html"
    assert nav.css == "https://example.github.io/open-local-weather/assets/style.css"


def test_build_nav_links_preserves_existing_trailing_slash():
    nav = build_nav_links("https://example.github.io/open-local-weather/", "owner/repo")
    assert nav.home == "https://example.github.io/open-local-weather/"


def test_build_nav_links_github_url():
    nav = build_nav_links("https://example.com/", "dissent00/open-local-weather")
    assert nav.github == "https://github.com/dissent00/open-local-weather"


# ---------------------------------------------------------------------------
# render_forecast_page
# ---------------------------------------------------------------------------


def test_render_forecast_page_includes_key_stats_and_narrative():
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "Test Town" in html
    assert "26°C / 79°F" in html
    assert "14:00-16:00" in html
    assert "<h2>Overview</h2>" in html
    assert "<strong>likely</strong>" in html
    assert nav.css in html
    assert nav.archive in html
    # nav.subscribe deliberately isn't linked from any template yet — see
    # NavLinks' docstring comment.


def test_render_forecast_page_no_morning_issuance_shows_single_section():
    # No evening refresh has happened for this entry — nothing to
    # disambiguate, no issuance label at all.
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "Morning Issuance" not in html
    assert "Evening Update" not in html


def _refreshed_entry(d=date(2026, 8, 11), **overrides):
    defaults = dict(
        rain_expected="Heavy rain now",
        narrative_markdown="## Overview\nEvening: rain has arrived.",
        morning_issuance=MorningIssuanceSnapshot(
            rain_expected="Dry all day",
            temp_high_c=26.0,
            temp_low_c=18.0,
            temp_high_low_display="26°C / 79°F",
            mslp_trend_24h="falling",
            synoptic_pattern="trough",
            narrative_markdown="## Overview\nMorning: dry and warm expected.",
            generated_at_utc=datetime(2026, 8, 11, 6, 7, tzinfo=timezone.utc),
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime(2026, 8, 11, 6, 7, tzinfo=timezone.utc),
            refreshed_at=datetime(2026, 8, 11, 18, 20, tzinfo=timezone.utc),
            llm_provider="gemini", llm_model="gemini-3.6-flash", pipeline_version="0.1.0",
        ),
    )
    defaults.update(overrides)
    return make_entry(d, **defaults)


def test_render_forecast_page_shows_only_current_issuance_even_when_morning_exists():
    """Real regression this guards against: an earlier version stacked
    both issuances on one page, forcing readers to scroll past stale
    morning content to reach the current forecast. A single call to
    render_forecast_page(entry, ...) — the landing page's own call — must
    show ONLY the current (evening) content, never the morning content
    alongside it, regardless of whether entry.morning_issuance is set."""
    entry = _refreshed_entry()
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(
        entry, LOCATION, nav, is_latest=True, issuance_label=_issuance_label(entry, morning=False)
    )

    assert "Evening: rain has arrived" in html
    assert "Heavy rain now" in html
    assert "Morning: dry and warm expected" not in html
    assert "Dry all day" not in html
    assert "Morning Issuance" not in html
    assert "Evening Update" in html  # the label IS shown, just not the old content


def test_render_forecast_page_morning_view_shows_only_morning_content():
    """The complementary page — _entry_as_morning_view() — must show ONLY
    the morning issuance's content, on its own URL, not the evening
    content that's since overwritten the entry's top-level fields."""
    entry = _refreshed_entry()
    nav = build_nav_links("https://example.com", "owner/repo")
    morning_view = _entry_as_morning_view(entry)
    html = render_forecast_page(
        morning_view, LOCATION, nav, is_latest=False, issuance_label=_issuance_label(entry, morning=True)
    )

    assert "Morning: dry and warm expected" in html
    assert "Dry all day" in html
    assert "Evening: rain has arrived" not in html
    assert "Heavy rain now" not in html
    assert "Morning Issuance" in html
    assert "Evening Update" not in html


def test_render_forecast_page_archived_banner_only_when_not_latest():
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")

    latest_html = render_forecast_page(entry, LOCATION, nav, is_latest=True)
    assert "archived forecast" not in latest_html

    archived_html = render_forecast_page(entry, LOCATION, nav, is_latest=False)
    assert "archived forecast" in archived_html


def test_render_forecast_page_always_shows_disclaimer():
    """Every forecast page — not just the email — must carry the beta/
    not-for-life-safety disclaimer, since the site is public and shareable
    in a way the manually-managed subscriber list isn't."""
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "disclaimer-banner" in html
    assert "not an official government product" in html
    assert "life-safety decisions" in html


def test_render_forecast_page_disclaimer_omits_official_source_when_unconfigured():
    # LOCATION has no local_bulletin_source_name/url set (a fork that
    # hasn't configured a local met service) — the disclaimer must degrade
    # gracefully, not reference a source that doesn't exist.
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "consult" not in html


def test_render_forecast_page_disclaimer_links_official_source_when_configured():
    location = LOCATION.model_copy(
        update={
            "local_bulletin_source_name": "Kenya Meteorological Department (KMD)",
            "local_bulletin_url": "https://meteo.go.ke/weather-warnings/",
        }
    )
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, location, nav, is_latest=True)

    assert "consult" in html
    assert "Kenya Meteorological Department (KMD)" in html
    assert '<a href="https://meteo.go.ke/weather-warnings/">Kenya Meteorological Department (KMD)</a>' in html


def test_render_forecast_page_omits_optional_stats_when_absent():
    entry = make_entry(date(2026, 8, 11), onset_window=None, uv_index_max=None, air_quality_aqi=None)
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)
    assert "Onset Window" not in html
    assert "UV Index" not in html


# ---------------------------------------------------------------------------
# render_archive_index_page
# ---------------------------------------------------------------------------


def test_render_archive_index_page_lists_dates_newest_first():
    # render_archive_index_page renders items in the order given — sorting
    # is build_archive_items()'s job (tested separately below), so this
    # passes already-sorted items to test the rendering, not the sort.
    nav = build_nav_links("https://example.com", "owner/repo")
    items = [
        ArchiveItem(date=date(2026, 8, 11), slug="2026-08-11", label=None),
        ArchiveItem(date=date(2026, 8, 10), slug="2026-08-10", label=None),
        ArchiveItem(date=date(2026, 8, 9), slug="2026-08-09", label=None),
    ]
    html = render_archive_index_page(items, LOCATION, nav)

    idx_11 = html.index("2026-08-11")
    idx_10 = html.index("2026-08-10")
    idx_09 = html.index("2026-08-09")
    assert idx_11 < idx_10 < idx_09


def test_render_archive_index_page_empty_state():
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_archive_index_page([], LOCATION, nav)
    assert "No forecasts published yet." in html


def test_render_archive_index_page_always_shows_disclaimer():
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_archive_index_page(
        [ArchiveItem(date=date(2026, 8, 11), slug="2026-08-11", label=None)], LOCATION, nav
    )

    assert "disclaimer-banner" in html
    assert "not an official government product" in html
    assert "life-safety decisions" in html


def test_render_archive_index_page_shows_label_and_links_slug():
    nav = build_nav_links("https://example.com", "owner/repo")
    items = [ArchiveItem(date=date(2026, 8, 11), slug="2026-08-11-morning", label="Morning Issuance — 06:07 UTC")]
    html = render_archive_index_page(items, LOCATION, nav)

    assert 'href="2026-08-11-morning.html"' in html
    assert "Morning Issuance" in html


# ---------------------------------------------------------------------------
# build_archive_items
# ---------------------------------------------------------------------------


def test_build_archive_items_one_entry_per_unrefreshed_day():
    entries = {date(2026, 8, 10): make_entry(date(2026, 8, 10)), date(2026, 8, 11): make_entry(date(2026, 8, 11))}
    items = build_archive_items(list(entries), entries.get)

    assert [i.slug for i in items] == ["2026-08-11", "2026-08-10"]
    assert all(i.label is None for i in items)


def test_build_archive_items_two_entries_for_a_refreshed_day():
    refreshed = _refreshed_entry()
    entries = {date(2026, 8, 10): make_entry(date(2026, 8, 10)), date(2026, 8, 11): refreshed}
    items = build_archive_items(list(entries), entries.get)

    assert [i.slug for i in items] == ["2026-08-11", "2026-08-11-morning", "2026-08-10"]
    assert items[0].label is not None and "Evening Update" in items[0].label
    assert items[1].label is not None and "Morning Issuance" in items[1].label
    assert items[2].label is None


# ---------------------------------------------------------------------------
# GitHubPagesPublisher
# ---------------------------------------------------------------------------


def test_publisher_writes_index_archive_entry_and_archive_index(tmp_path):
    entry = make_entry(date(2026, 8, 11))
    publisher = GitHubPagesPublisher(
        docs_dir=tmp_path,
        location=LOCATION,
        base_url="https://example.com",
        github_repo="owner/repo",
        all_dates_provider=lambda: [date(2026, 8, 10), date(2026, 8, 11)],
    )
    publisher.publish(entry)

    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "archive" / "2026-08-11.html").exists()
    assert (tmp_path / "archive" / "index.html").exists()

    index_text = (tmp_path / "index.html").read_text()
    archive_entry_text = (tmp_path / "archive" / "2026-08-11.html").read_text()
    assert "Rain" in index_text
    assert "archived forecast" not in index_text  # index.html is always "latest"
    assert "archived forecast" in archive_entry_text


def test_publisher_backfills_missing_archive_pages(tmp_path):
    """The archive index is generated from data/log/ (the source of truth),
    but only today's page is rendered directly — so any earlier entry whose
    page was never rendered would be listed as a dead link. Regression test
    for exactly that."""
    entries = {
        date(2026, 8, 9): make_entry(date(2026, 8, 9)),
        date(2026, 8, 10): make_entry(date(2026, 8, 10)),
        date(2026, 8, 11): make_entry(date(2026, 8, 11)),
    }
    publisher = GitHubPagesPublisher(
        docs_dir=tmp_path,
        location=LOCATION,
        base_url="https://example.com",
        github_repo="owner/repo",
        all_dates_provider=lambda: sorted(entries),
        entry_provider=lambda d: entries.get(d),
    )
    publisher.publish(entries[date(2026, 8, 11)])

    for d in entries:
        assert (tmp_path / "archive" / f"{d.isoformat()}.html").exists(), f"missing page for {d}"

    index_html = (tmp_path / "archive" / "index.html").read_text()
    linked = re.findall(r'href="(\d{4}-\d{2}-\d{2}\.html)"', index_html)
    on_disk = {p.name for p in (tmp_path / "archive").glob("*.html")}
    assert linked, "archive index should link to at least one page"
    assert not [l for l in linked if l not in on_disk], "archive index has dead links"


def test_publisher_backfill_is_a_noop_without_entry_provider(tmp_path):
    # entry_provider is optional — omitting it must not crash, just skip
    # backfill (the pre-existing behavior).
    publisher = GitHubPagesPublisher(
        docs_dir=tmp_path,
        location=LOCATION,
        base_url="https://example.com",
        github_repo="owner/repo",
        all_dates_provider=lambda: [date(2026, 8, 10), date(2026, 8, 11)],
    )
    publisher.publish(make_entry(date(2026, 8, 11)))
    assert (tmp_path / "archive" / "2026-08-11.html").exists()
    assert not (tmp_path / "archive" / "2026-08-10.html").exists()


def test_publisher_backfill_skips_dates_with_no_entry(tmp_path):
    publisher = GitHubPagesPublisher(
        docs_dir=tmp_path,
        location=LOCATION,
        base_url="https://example.com",
        github_repo="owner/repo",
        all_dates_provider=lambda: [date(2026, 8, 10), date(2026, 8, 11)],
        entry_provider=lambda d: None,  # entry vanished / unreadable
    )
    publisher.publish(make_entry(date(2026, 8, 11)))  # must not raise
    assert (tmp_path / "archive" / "2026-08-11.html").exists()


def test_publisher_writes_both_pages_for_a_refreshed_day(tmp_path):
    """The actual fix this session was about: a refreshed day must get TWO
    separate archive pages, not one page with both stacked, and the
    landing page must show only the current (evening) content."""
    entry = _refreshed_entry()
    publisher = GitHubPagesPublisher(
        docs_dir=tmp_path,
        location=LOCATION,
        base_url="https://example.com",
        github_repo="owner/repo",
        all_dates_provider=lambda: [date(2026, 8, 11)],
        entry_provider=lambda d: entry if d == date(2026, 8, 11) else None,
    )
    publisher.publish(entry)

    assert (tmp_path / "archive" / "2026-08-11.html").exists()
    assert (tmp_path / "archive" / "2026-08-11-morning.html").exists()

    index_text = (tmp_path / "index.html").read_text()
    assert "Evening: rain has arrived" in index_text
    assert "Morning: dry and warm expected" not in index_text

    current_page_text = (tmp_path / "archive" / "2026-08-11.html").read_text()
    assert "Evening: rain has arrived" in current_page_text
    assert "Morning: dry and warm expected" not in current_page_text

    morning_page_text = (tmp_path / "archive" / "2026-08-11-morning.html").read_text()
    assert "Morning: dry and warm expected" in morning_page_text
    assert "Evening: rain has arrived" not in morning_page_text

    archive_index_text = (tmp_path / "archive" / "index.html").read_text()
    assert 'href="2026-08-11.html"' in archive_index_text
    assert 'href="2026-08-11-morning.html"' in archive_index_text
    assert "Evening Update" in archive_index_text
    assert "Morning Issuance" in archive_index_text


def test_publisher_backfills_missing_morning_page_for_an_other_date(tmp_path):
    """A date that already had its current page rendered can still be
    missing its -morning.html — e.g. an entry that gained morning_issuance
    after the fact (a backfill script, matching what actually happened on
    this repo). The backfill loop must fill in just the missing piece,
    not skip the whole date because the base page already exists."""
    today = make_entry(date(2026, 8, 11))
    yesterday_refreshed = _refreshed_entry(d=date(2026, 8, 10))
    entries = {date(2026, 8, 10): yesterday_refreshed, date(2026, 8, 11): today}

    publisher = GitHubPagesPublisher(
        docs_dir=tmp_path,
        location=LOCATION,
        base_url="https://example.com",
        github_repo="owner/repo",
        all_dates_provider=lambda: list(entries),
        entry_provider=entries.get,
    )
    # Simulate 2026-08-10 already having its current page (from before it
    # gained morning_issuance) but not yet its morning page.
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True)
    (archive_dir / "2026-08-10.html").write_text("stale placeholder")

    publisher.publish(today)

    assert (archive_dir / "2026-08-10-morning.html").exists()
    # The pre-existing current page for 08-10 was left alone (force=False
    # in the backfill path) — only the missing morning page got written.
    assert (archive_dir / "2026-08-10.html").read_text() == "stale placeholder"


# ---------------------------------------------------------------------------
# Ground AQI stations block (deterministic, not LLM-dependent)
# ---------------------------------------------------------------------------


def test_render_forecast_page_omits_aqi_section_when_no_stations():
    entry = make_entry(date(2026, 8, 11), ground_aqi=[])
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)
    assert "Ground AQI Stations" not in html


def test_render_forecast_page_lists_each_station_by_name():
    from openlocalweather.models import GroundAQIReading

    generated_at = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)
    entry = make_entry(
        date(2026, 8, 11),
        meta=LogEntryMeta(
            generated_at_utc=generated_at, llm_provider="gemini", llm_model="test", pipeline_version="0"
        ),
        ground_aqi=[
            GroundAQIReading(
                name="Kisumu Airport", station_id="A418534", aqi=42, pm25=18.0, pm10=30.0, measured_at=generated_at
            ),
            GroundAQIReading(
                name="Dunga Beach", station_id="A418504", aqi=171, pm25=171.0, pm10=37.0, measured_at=generated_at
            ),
        ],
    )
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "Ground AQI Stations" in html
    assert "Kisumu Airport" in html
    assert "Dunga Beach" in html
    assert "AQI 42" in html
    assert "AQI 171" in html
    # Deterministic range/highest-station summary, computed in code —
    # must be correct regardless of what the LLM wrote in the narrative.
    assert "42" in html and "171" in html
    assert "highest at" in html
    assert "<strong>Dunga Beach</strong>" in html
    assert "measured 0.0h ago" in html


def test_render_forecast_page_excludes_stale_reading_from_summary_but_still_shows_it():
    from openlocalweather.models import GroundAQIReading

    generated_at = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)
    entry = make_entry(
        date(2026, 8, 12),
        meta=LogEntryMeta(
            generated_at_utc=generated_at, llm_provider="gemini", llm_model="test", pipeline_version="0"
        ),
        ground_aqi=[
            GroundAQIReading(
                name="Stale Station",
                station_id="A1",
                aqi=200,
                measured_at=generated_at - timedelta(hours=7.2),
            ),
        ],
    )
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    # Reading is still shown, transparently, but flagged stale...
    assert "Stale Station" in html
    assert "AQI 200" in html
    assert "stale" in html
    assert "measured 7.2h ago" in html
    # ...and must NOT be presented as a trustworthy "current" range.
    assert "highest at" not in html
    assert "No station has a sufficiently current" in html


def test_render_forecast_page_shows_aqi_unavailable_for_missing_composite():
    from openlocalweather.models import GroundAQIReading

    generated_at = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)
    entry = make_entry(
        date(2026, 8, 11),
        meta=LogEntryMeta(
            generated_at_utc=generated_at, llm_provider="gemini", llm_model="test", pipeline_version="0"
        ),
        ground_aqi=[
            GroundAQIReading(
                name="Kisumu Airport", station_id="A418534", aqi=None, pm25=157.0, pm10=15.0, measured_at=generated_at
            )
        ],
    )
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)
    assert "AQI unavailable" in html
    assert "PM2.5 157.0" in html
    # A single station with no composite AQI has nothing to summarize a
    # range from — the summary line must not appear.
    assert "highest at" not in html
