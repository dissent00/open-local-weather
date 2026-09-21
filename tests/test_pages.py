import re
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

from openlocalweather.config import LocationConfig, Point, SecondaryPoint
from openlocalweather.models import (
    IssuanceSnapshot,
    LogEntryMeta,
    ModelPredictionsByLead,
    RunDegradation,
)
from openlocalweather.models import DailyLogEntry, ObservedSoFar
from openlocalweather.publish.pages import (
    ArchiveItem,
    GitHubPagesPublisher,
    build_archive_items,
    build_nav_links,
    render_archive_index_page,
    render_forecast_page,
)
from openlocalweather.publish.pages import _entry_as_morning_view, _issuance_label
from openlocalweather.models import ObservedSoFar

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


def test_the_page_shows_what_the_station_had_already_seen():
    """ROADMAP item 121's whole point on the reader's side: at 18:00 the page
    says what actually happened today, without a forecast having to describe
    it and without an LLM call to produce it."""
    entry = make_entry(date(2026, 8, 11)).model_copy(
        update={
            "observed_so_far": ObservedSoFar(
                precipitation=True, precipitation_onset="13:00", thunder=True,
                high_c=27.4, low_c=18.1, peak_wind_kmh=31.4, cloud_oktas=5.5,
            )
        }
    )
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "Observed so far today" in html
    assert "rain from 13:00; thunder; high so far 27°C / 81°F" in html
    assert "peak sustained 31 km/h; sky 6/8." in html
    # The withheld dimension is named rather than silently missing — C9.
    assert "never how much" in html


def test_a_silent_station_gets_no_section_rather_than_a_quiet_day():
    """No "no observations today" line, deliberately. A station that did not
    answer has not reported a quiet day, and saying so is the error that cost
    a published forecast on 2026-08-29."""
    entry = make_entry(date(2026, 8, 11))
    assert entry.observed_so_far is None, "fixture must have no reading"
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "Observed so far today" not in html


def test_render_forecast_page_no_morning_issuance_shows_single_section():
    # No evening refresh has happened for this entry — nothing to
    # disambiguate, no issuance label at all.
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "Issued" not in html
    assert "Updated" not in html


def _refreshed_entry(d=date(2026, 8, 11), **overrides):
    defaults = dict(
        rain_expected="Heavy rain now",
        narrative_markdown="## Overview\nEvening: rain has arrived.",
        morning_issuance=IssuanceSnapshot(
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
    assert "Issued" not in html
    assert "Updated" in html  # the label IS shown, just not the old content


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
    assert "Issued" in html
    assert "Updated" not in html


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
    items = [ArchiveItem(date=date(2026, 8, 11), slug="2026-08-11-morning", label="Issued 06:07 UTC")]
    html = render_archive_index_page(items, LOCATION, nav)

    assert 'href="2026-08-11-morning.html"' in html
    assert "Issued" in html


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
    assert items[0].label is not None and "Updated" in items[0].label
    assert items[1].label is not None and "Issued" in items[1].label
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
    assert "Updated" in archive_index_text
    assert "Issued" in archive_index_text


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


# ---------------------------------------------------------------------------
# Accuracy page
# ---------------------------------------------------------------------------


def _review(findings=(), cells=(), sufficiency="8 check(s) per model — directional only."):
    from openlocalweather.review import WeeklyReview

    return WeeklyReview(
        period_start=date(2026, 8, 11),
        period_end=date(2026, 8, 18),
        days_with_predictions=9,
        days_verified=8,
        cells=list(cells),
        findings=list(findings),
        data_sufficiency=sufficiency,
    )


def _cell(model, checks, correct, high_err=None, conf="provisional", lead=0):
    from openlocalweather.review import SkillCell

    return SkillCell(
        model=model, lead_time_days=lead, checks=checks, correct=correct,
        rain_pct=(100 * correct / checks) if checks else None, confidence=conf,
        mean_high_error_c=high_err, mean_low_error_c=None,
        mean_wind_error_kmh=None, mean_onset_error_hrs=None,
        mean_cloud_error_pct=None, cloud_checks=0, storm_days=0, storms_called=0,
        mean_mslp_error_hpa=None,
        earliest=None, latest=None,
    )


def test_accuracy_page_publishes_no_ranking_when_the_record_supports_none():
    """The published page has to hold the same line the prompt does. A
    percentage column with no ranking is fine; a page that *reads* as a
    league table is not."""
    from openlocalweather.publish.pages import render_accuracy_page

    html = render_accuracy_page(
        _review(cells=[_cell("best_match", 8, 7), _cell("ecmwf_ifs025", 8, 4)]),
        LOCATION,
        build_nav_links("https://example.com/", "owner/repo"),
    )
    assert "No conclusions yet" in html
    assert "purely by chance" in html
    # The raw figures are still shown — withholding a ranking is not the
    # same as hiding the data it would have been drawn from.
    assert "7/8" in html and "4/8" in html
    # ...but never without the caveat attached.
    assert "not as a ranking" in html
    assert "provisional" in html


def test_accuracy_page_states_findings_with_their_evidence():
    from openlocalweather.publish.pages import render_accuracy_page
    from openlocalweather.review import Finding

    html = render_accuracy_page(
        _review(
            findings=[Finding(
                kind="ranking",
                claim="At Day+0, best_match is the strongest rain caller here.",
                evidence="best_match 27/30 (90%) vs ecmwf_ifs025 9/30 (30%).",
                confidence="established",
                checks=30,
            )],
            cells=[_cell("best_match", 30, 27, conf="established")],
        ),
        LOCATION,
        build_nav_links("https://example.com/", "owner/repo"),
    )
    assert "strongest rain caller" in html
    # The evidence and confidence travel with the claim on the page too.
    assert "27/30" in html
    assert "established" in html
    assert "30 checks" in html


def test_accuracy_page_never_renders_negative_zero():
    """A "-0.0°C" on the page that argues its numbers are careful reads as
    a defect, whatever the float actually holds."""
    from openlocalweather.publish.pages import render_accuracy_page

    html = render_accuracy_page(
        _review(cells=[_cell("ecmwf_ifs025", 8, 4, high_err=-0.04)]),
        LOCATION,
        build_nav_links("https://example.com/", "owner/repo"),
    )
    assert "-0.0" not in html
    assert "+0.0" in html


def test_accuracy_page_explains_a_blank_row_is_not_a_missed_call():
    """Models whose horizon stops short of Day+7 leave empty cells. Without
    saying so, a blank reads as a failure rather than an absent forecast."""
    from openlocalweather.publish.pages import render_accuracy_page

    html = render_accuracy_page(
        _review(cells=[_cell("ukmo_seamless", 0, 0, lead=7)]),
        LOCATION,
        build_nav_links("https://example.com/", "owner/repo"),
    )
    assert "never a missed call" in html


# ---------------------------------------------------------------------------
# ROADMAP item 53.4 — a degraded run says it is degraded, on the page
# ---------------------------------------------------------------------------


def _degraded_meta(**overrides):
    defaults = dict(
        generated_at_utc=datetime(2026, 8, 11, 6, 7, tzinfo=timezone.utc),
        llm_provider="gemini",
        llm_model="gemini-3.6-flash",
        pipeline_version="0.1.0",
        degradations=[
            RunDegradation(
                code="hours_ahead_narrowed",
                summary="Part of tonight's data did not arrive. This forecast covers "
                "the rest of today only.",
                detail="The forward hourly window did not arrive; the hours-ahead "
                "guidance and the convective outlook came from the day-0 fetch instead.",
            )
        ],
    )
    defaults.update(overrides)
    return LogEntryMeta(**defaults)


def test_a_degraded_run_says_so_on_the_page():
    entry = make_entry(date(2026, 8, 11), meta=_degraded_meta())
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    # Framed as a gap rather than as reassurance — the whole point of item 53.
    assert "less than usual" in html.lower()
    # Apostrophes render as &#39; now that autoescape is on, so assert on the
    # half that has none — and separately that the quote really is escaped,
    # which is the check that would have caught the 2026-09-10 page.
    assert "data did not arrive" in html
    assert "tonight&#39;s" in html
    assert "tonight's data" not in html


def test_the_banner_is_plain_and_the_jargon_is_at_the_end():
    """The top of a forecast is where somebody decides whether to go outside.
    "The forward hourly window did not arrive" tells that person nothing they
    can act on, so it belongs in the notes and not in the banner."""
    entry = make_entry(date(2026, 8, 11), meta=_degraded_meta())
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    banner = html[html.index("run-degraded") : html.index("<h1>")]
    assert "data did not arrive" in banner
    assert "forward hourly window" not in banner

    notes = html[html.index('id="run-notes"') :]
    assert "forward hourly window" in notes


def test_the_notes_section_is_absent_on_a_clean_run():
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    assert 'id="run-notes"' not in render_forecast_page(entry, LOCATION, nav, is_latest=True)


def test_a_clean_run_carries_no_degradation_notice():
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "did not arrive" not in html
    assert "run-degraded" not in html


def test_the_morning_view_shows_the_morning_s_own_degradation():
    """A degraded morning refreshed by a clean evening. The evening's page
    must be clean and the morning's archived page must still show the gap —
    the same split the record keeps, carried through to what a reader sees."""
    entry = _refreshed_entry(
        morning_issuance=IssuanceSnapshot(
            rain_expected="Dry all day",
            temp_high_c=26.0,
            temp_low_c=18.0,
            temp_high_low_display="26°C / 79°F",
            mslp_trend_24h="falling",
            synoptic_pattern="trough",
            narrative_markdown="## Overview\nMorning: dry and warm expected.",
            generated_at_utc=datetime(2026, 8, 11, 6, 7, tzinfo=timezone.utc),
            degradations=[
                RunDegradation(
                    code="hours_ahead_narrowed",
                    summary="Part of tonight's data did not arrive.",
                    detail="The forward hourly window did not arrive.",
                )
            ],
        ),
    )
    nav = build_nav_links("https://example.com", "owner/repo")

    current = render_forecast_page(entry, LOCATION, nav, is_latest=True)
    assert "did not arrive" not in current

    morning = render_forecast_page(
        _entry_as_morning_view(entry), LOCATION, nav, is_latest=False
    )
    assert "did not arrive" in morning


def test_the_glossary_page_is_written_without_a_record_to_score(tmp_path):
    """Item 56. Static data, so it does not wait on a review the way the
    accuracy page must — a reader on day one still gets the definitions."""
    from openlocalweather.publish.pages import render_glossary_page, build_nav_links
    from openlocalweather.glossary import GLOSSARY

    nav = build_nav_links("https://example.org/", "owner/repo")
    html = render_glossary_page(LOCATION, nav)

    assert "{{" not in html and "{%" not in html, "unrendered template syntax"
    for entry in GLOSSARY:
        assert entry.term in html, f"{entry.term} missing from the page"
    # Sources are shown where claimed and nowhere else.
    assert html.count('class="source"') == sum(1 for e in GLOSSARY if e.source)


def test_every_page_can_reach_the_glossary(tmp_path):
    """A page nothing links to is a page nobody reads."""
    from openlocalweather.publish.pages import build_nav_links

    nav = build_nav_links("https://example.org/", "owner/repo")
    assert nav.glossary.endswith("/glossary.html")

    templates = Path(__file__).resolve().parents[1] / "src/openlocalweather/publish/templates"
    for name in ("forecast", "accuracy", "archive_index", "glossary"):
        body = (templates / f"{name}.html.jinja").read_text()
        assert "nav.glossary" in body, f"{name} has no link to the glossary"


# ---------------------------------------------------------------------------
# Autoescape — 2026-09-10
# ---------------------------------------------------------------------------
#
# The evening run put a 15,930-character repetition loop into uv_index_max,
# and it reached docs/index.html carrying five raw </em> tags and a <td>. The
# page had ZERO escaped entities in it: `&lt;` appeared not once.
#
# `_env()` reads `autoescape=select_autoescape(["html"])`, which looks like
# the control is on. select_autoescape matches the template FILENAME's
# extension, and every template here is `*.html.jinja` — extension `.jinja`.
# So it resolved to False for all four templates, and had done since they
# were written. The templates themselves show the intent: `narrative_html`
# carries `| safe`, which is only meaningful if everything else is escaped.
#
# What reached the page this time was nonsense. What reaches it is whatever
# the model emits, on a public site.


def test_a_value_from_the_model_cannot_put_markup_on_the_page():
    entry = make_entry(date(2026, 8, 11), uv_index_max='<script>alert("xss")</script>')
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_the_narrative_is_still_rendered_as_html():
    """The other half. Autoescape must not break the one value that IS
    markup — the narrative is markdown converted to HTML in code, and it is
    the reason `| safe` exists in the template."""
    entry = make_entry(date(2026, 8, 11))
    nav = build_nav_links("https://example.com", "owner/repo")
    html = render_forecast_page(entry, LOCATION, nav, is_latest=True)

    assert "<h2>Overview</h2>" in html
    assert "&lt;h2&gt;" not in html


def test_every_template_actually_escapes():
    """Pins the mechanism rather than one page, because the failure was that
    the setting LOOKED right. A template added later gets this for free; a
    future change to `_env()` that silently disables it does not."""
    from openlocalweather.publish.pages import TEMPLATES_DIR, _env

    env = _env()
    names = sorted(p.name for p in TEMPLATES_DIR.glob("*.jinja"))
    assert names, "no templates found — this test would pass against nothing"

    for name in names:
        resolved = env.autoescape(name) if callable(env.autoescape) else env.autoescape
        assert resolved is True, f"{name} renders unescaped"


def test_the_forecast_and_the_observations_are_stamped_in_the_same_zone():
    """ROADMAP item 121. The observed block is stamped in LOCAL time and the
    issuance label used to be stamped in UTC.

    That was cosmetic while both described the same moment. Since the
    observation-only refresh they are different moments AND the reader is
    meant to compare them, so a unit mismatch can invert the comparison:
    west of Greenwich, observations genuinely newer than the forecast would
    render as older than it. Two numbers a reader is asked to subtract have
    to be in the same units.
    """
    entry = _refreshed_entry()
    entry.meta.issued_local_time = "14:28"
    entry.meta.observations_local_time = "16:45"
    entry.observed_so_far = ObservedSoFar(precipitation=True, precipitation_onset="15:10")

    label = _issuance_label(entry, morning=False)

    assert label == "Updated 14:28"
    assert "UTC" not in label, "a local clock must not be labelled UTC"

    html = render_forecast_page(
        entry,
        LOCATION,
        build_nav_links("https://example.com", "owner/repo"),
        is_latest=True,
        issuance_label=label,
    )
    assert "Updated 14:28" in html
    assert "As of 16:45" in html


def test_an_entry_with_no_local_clock_keeps_the_utc_label():
    """Entries written before `issued_local_time` existed. The suffix stays
    because the number really is UTC — the failure to avoid is a local clock
    wearing a UTC label, not a UTC clock wearing its own."""
    entry = _refreshed_entry()
    entry.meta.issued_local_time = None

    label = _issuance_label(entry, morning=False)

    assert label is not None and label.endswith("UTC")


def test_an_issuance_is_not_labelled_by_a_time_of_day_it_did_not_happen_at():
    """ROADMAP item 104 removed the two-runs-a-day structure; the labels kept
    it. "Morning Issuance" named whatever the day's FIRST issuance was and
    "Evening Update" whatever the LATEST was, regardless of the hour on the
    clock — accurate only while the schedule was 03:01Z and 15:01Z.

    A run can now happen at any time, and item 121's refresh path makes an
    hourly cron the expected shape. A first issuance at 14:00 is not a
    morning, and an update at 09:00 is not an evening.
    """
    entry = _refreshed_entry()
    # A day that began at 14:00 and was updated at 09:00 the way no
    # two-runs-a-day schedule ever produced, and every later one can.
    entry.morning_issuance = entry.morning_issuance.model_copy(
        update={"generated_at_utc": datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)}
    )
    entry.meta.issued_local_time = "09:12"

    first = _issuance_label(entry, morning=True)
    latest = _issuance_label(entry, morning=False)

    assert "Morning" not in first, f"a 14:00 issuance is not a morning: {first!r}"
    assert "Evening" not in latest, f"an 09:12 update is not an evening: {latest!r}"
    assert "14:00" in first and "09:12" in latest, "each still names its own clock"


def test_both_issuance_labels_are_in_the_same_zone():
    """The archive index prints one label per issuance, so the first and the
    latest sit side by side. Until `IssuanceSnapshot` recorded its own local
    clock the first was the only reader-facing time on the site still in UTC,
    and two times in two zones cannot be ordered by a reader — the same defect
    ROADMAP item 121 had to fix on the forecast page.
    """
    entry = _refreshed_entry()
    entry.morning_issuance = entry.morning_issuance.model_copy(
        update={"issued_local_time": "06:07"}
    )
    entry.meta.issued_local_time = "18:45"

    first = _issuance_label(entry, morning=True)
    latest = _issuance_label(entry, morning=False)

    assert first == "Issued 06:07"
    assert latest == "Updated 18:45"
    assert "UTC" not in first and "UTC" not in latest


def test_a_snapshot_with_no_local_clock_still_says_which_zone_it_is_in():
    """Every snapshot taken before the field existed. The number really is
    UTC, so the suffix is honest — the failure to avoid is a local clock
    wearing a UTC label, not a UTC clock wearing its own."""
    entry = _refreshed_entry()
    entry.morning_issuance = entry.morning_issuance.model_copy(
        update={"issued_local_time": None}
    )

    assert _issuance_label(entry, morning=True).endswith("UTC")


def test_a_snapshot_records_the_local_clock_of_the_issuance_that_made_it():
    """Captured at the source rather than derived later: converting the
    stored UTC instant with today's configured zone would assume the
    deployment has never moved, and reconcile_now can override the system
    clock so the two are not the same instant."""
    entry = _refreshed_entry()
    entry.meta.issued_local_time = "22:14"

    assert entry.to_issuance_snapshot().issued_local_time == "22:14"


# --- the first issuance, read from either record shape — ROADMAP item 137 ----


def _modern_refreshed_entry(d=date(2026, 8, 11)):
    """The shape a run writes once `morning_issuance` stops being set: the
    first issuance lives ONLY in `earlier_issuances`."""
    entry = _refreshed_entry(d)
    first = entry.morning_issuance
    assert first is not None
    return entry.model_copy(
        update={"morning_issuance": None, "earlier_issuances": [first]}
    )


def test_the_first_issuance_page_does_not_need_the_legacy_field():
    """ROADMAP item 137. `morning_issuance` is a leftover of the dead
    morning/evening model and is written on every later issuance, duplicating
    `earlier_issuances[0]`. The publisher must read the day's first issuance
    through `issuance_log()` — the accessor that already handles both record
    shapes — so the duplicate can stop being written without orphaning the
    page it feeds.
    """
    from openlocalweather.publish.pages import _first_issuance

    modern = _modern_refreshed_entry()
    assert modern.morning_issuance is None

    first = _first_issuance(modern)
    assert first is not None, "a day with two issuances has a first one"
    assert first.rain_expected == "Dry all day", "and it is the EARLIER content"

    view = _entry_as_morning_view(modern)
    assert view.rain_expected == "Dry all day"
    assert "Morning: dry and warm" in view.narrative_markdown
    assert _issuance_label(modern, morning=True) is not None


def test_a_legacy_entry_still_finds_its_first_issuance():
    """The archive is this project's record and is never migrated — see
    `issuance_log`'s docstring. Every entry committed before this change
    carries the first issuance ONLY under the legacy name, and must keep
    rendering exactly as it did."""
    from openlocalweather.publish.pages import _first_issuance

    legacy = _refreshed_entry()
    assert legacy.earlier_issuances == []

    first = _first_issuance(legacy)
    assert first is not None and first.rain_expected == "Dry all day"


def test_a_day_issued_once_has_no_first_issuance_page():
    """One issuance is not a first issuance: there is nothing to disambiguate
    it from, and a second page would duplicate the main one."""
    from openlocalweather.publish.pages import _first_issuance

    assert _first_issuance(make_entry(date(2026, 8, 11))) is None


def test_the_morning_view_carries_no_value_the_snapshot_never_recorded():
    """An archived issuance's page must not borrow the CURRENT run's data.

    `_entry_as_morning_view` rebuilds a full `DailyLogEntry` from an
    `IssuanceSnapshot` so one template renders both, and it overwrites every
    field the snapshot carries. The fields it does NOT carry are the problem:
    `cloud_anchors`, `wind_anchors`, `uv_index` and `air_quality_index` were
    added to the day record in ROADMAP item 159 steps 2-4 and deliberately
    left off the snapshot, because the tiles always show the current run.

    Left alone, the rebuild would hand a page labelled "Morning" the evening
    run's sky, its wind and the halves of its UV and AQI, beside that
    morning's own display strings. The page template does not render them
    YET — step 6 does — so this is the cheap moment to make the rule true.
    """
    entry = _refreshed_entry().model_copy(
        update={
            "cloud_anchors": [{"when": "evening", "cover": "Overcast"}],
            "wind_anchors": [{"when": "evening", "sustained_kmh": 30.0}],
            "uv_index": 9.1,
            "air_quality_index": 85,
        }
    )

    morning_view = _entry_as_morning_view(entry)

    assert morning_view.cloud_anchors == []
    assert morning_view.wind_anchors == []
    assert morning_view.uv_index is None
    assert morning_view.air_quality_index is None

    # and the snapshot's own display strings still win, which is the point of
    # the rebuild
    from openlocalweather.publish.pages import _first_issuance

    assert morning_view.uv_index_max == _first_issuance(entry).uv_index_max
