import re
from datetime import date, datetime, timezone

from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.models import LogEntryMeta, ModelPredictionsByLead
from openlocalweather.models import DailyLogEntry
from openlocalweather.publish.pages import (
    GitHubPagesPublisher,
    build_nav_links,
    render_archive_index_page,
    render_forecast_page,
)

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


def test_render_forecast_page_archived_banner_only_when_not_latest():
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")

    latest_html = render_forecast_page(entry, LOCATION, nav, is_latest=True)
    assert "archived forecast" not in latest_html

    archived_html = render_forecast_page(entry, LOCATION, nav, is_latest=False)
    assert "archived forecast" in archived_html


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
    nav = build_nav_links("https://example.com", "owner/repo")
    dates = [date(2026, 8, 9), date(2026, 8, 11), date(2026, 8, 10)]
    html = render_archive_index_page(dates, LOCATION, nav)

    idx_11 = html.index("2026-08-11")
    idx_10 = html.index("2026-08-10")
    idx_09 = html.index("2026-08-09")
    assert idx_11 < idx_10 < idx_09


def test_render_archive_index_page_empty_state():
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_archive_index_page([], LOCATION, nav)
    assert "No forecasts published yet." in html


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
