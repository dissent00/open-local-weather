"""The narrative is model output rendered as HTML on purpose — ROADMAP 100.

Autoescape protects every OTHER value on the page. It cannot protect this
one: the narrative is markdown converted to HTML in code and marked `| safe`
in the template precisely so that its `<h2>` renders as a heading. Which
means whatever else the model puts in it renders too.

`markdown.markdown(..., extensions=["extra"])` does not strip embedded HTML —
it passes it through untouched — so a `<script>` in a narrative reached the
published page and the subscriber email intact. Verified before the fix, not
assumed.
"""

import json

import markdown

from openlocalweather.publish.narrative import narrative_to_html

HOSTILE = """## Overview
Warm and dry today.

<script>alert("xss")</script>
<img src=x onerror="alert(1)">

Check [the forecast](javascript:alert(1)) or [the site](https://example.org).

### Detailed Discussion
Winds **light** and variable.
"""


def test_markdown_alone_really_does_pass_html_through():
    """The premise, pinned. If a future version of Markdown starts escaping
    this by itself, the sanitiser below stops being load-bearing and someone
    should know that rather than guess."""
    raw = markdown.markdown(HOSTILE, extensions=["extra"])
    assert "<script>" in raw
    assert "onerror" in raw


def test_a_script_in_the_narrative_does_not_survive():
    html = narrative_to_html(HOSTILE)
    assert "<script" not in html
    assert "alert(" not in html
    assert "onerror" not in html


def test_a_javascript_url_is_defanged_but_the_text_stays():
    html = narrative_to_html(HOSTILE)
    assert "javascript:" not in html
    assert "the forecast" in html, "the link text is content, and content is kept"
    assert 'href="https://example.org"' in html, "a real link still works"


def test_the_forecast_itself_is_untouched():
    """GUARD THE GUARD. A sanitiser that stripped everything would pass every
    assertion above and destroy the product."""
    html = narrative_to_html(HOSTILE)
    assert "<h2>Overview</h2>" in html
    assert "<h3>Detailed Discussion</h3>" in html
    assert "<strong>light</strong>" in html
    assert "Warm and dry today." in html


def test_the_real_stored_narrative_is_unchanged_by_sanitising():
    """Measured against the record rather than a fixture: whatever the
    sanitiser does, it must not alter a single published forecast."""
    from pathlib import Path

    logs = sorted(Path("data/log").glob("*.json"))
    assert logs, "no stored logs — this test would pass against nothing"

    checked = 0
    for path in logs:
        narrative = json.loads(path.read_text()).get("narrative_markdown")
        if not narrative:
            continue
        raw = markdown.markdown(narrative, extensions=["extra"])
        assert narrative_to_html(narrative) == raw, f"{path.name} would render differently"
        checked += 1

    assert checked >= 10, f"only {checked} narratives checked"


def test_the_email_is_sanitised_too():
    """The email is the more exposed of the two call sites: the page at least
    has autoescape around everything else, while `render_email_html` builds
    an f-string and interpolates the narrative into it raw. Asserted at the
    email's own level rather than trusting that it calls the right helper."""
    from datetime import date, datetime, timezone

    from openlocalweather.models import DailyLogEntry, LogEntryMeta
    from openlocalweather.publish.email_gmail import render_email_html

    entry = DailyLogEntry(
        date=date(2026, 9, 10),
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26°C / 79°F",
        mslp_trend_24h="steady",
        synoptic_pattern="Equatorial low",
        narrative_markdown=HOSTILE,
        meta=LogEntryMeta(
            generated_at_utc=datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc),
            llm_provider="p",
            llm_model="m",
            pipeline_version="0.1.0",
        ),
    )

    html = render_email_html(entry, "Test Town")
    assert "<script" not in html
    assert "javascript:" not in html
    assert "onerror" not in html
    assert "<h2>Overview</h2>" in html, "the forecast still renders"
