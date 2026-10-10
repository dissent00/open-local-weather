"""The write-up's second chance — `olw write-up`, 2026-09-30.

One run a day (the operator's decision). When its write-up was refused, the
same job tries once more about an hour later, for the prose alone: the
scored call is stored and immutable, so a repair is one request, not two.
"""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from openlocalweather import cli
from openlocalweather.config import load_location_config
from openlocalweather.dates import today_in_tz
from openlocalweather.llm.errors import LLMUnavailableError
from openlocalweather.llm.prompt import build_narrative_prompt
from openlocalweather.llm.schema import GeminiNarrativeResponse, WriteUpResponse
from openlocalweather.models import (
    DEGRADATION_NARRATIVE,
    DailyLogEntry,
    IssuancePredictions,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
    RunDegradation,
)
from openlocalweather.store import log_store

CONFIG = "config/location.yaml"
PLACEHOLDER = "## Write-up unavailable"
# A section the audit accepts: its figures are the brief's (the stored call's
# 31/19 and the Fahrenheit code makes of 31). Item 191 step (b).
TODAY = "Showers likely this afternoon with a high of 31°C / 88°F."
# A section the audit refuses: 30.7 is nowhere in the brief.
THIN = "Afternoon showers and thunderstorms, with a high of 30.7°C."


class FakeWriter:
    model = "gemini-3.6-flash"
    before_attempt = None
    after_attempt = None
    after_response = None

    def __init__(self, refuse: bool = False, thin: bool = False):
        self.refuse = refuse
        self.thin = thin
        self.calls = []

    def generate(self, system_prompt, user_prompt, response_schema):
        if self.before_attempt is not None:
            self.before_attempt()
        self.calls.append((system_prompt, user_prompt, response_schema.__name__))
        if self.refuse:
            raise LLMUnavailableError("Gemini request failed after 3 attempts: Gemini returned HTTP 503")
        if response_schema is WriteUpResponse:
            return WriteUpResponse(today=THIN if self.thin else TODAY)
        return GeminiNarrativeResponse(yesterday_verification="Checked.", today_narrative=TODAY)


def _day():
    return today_in_tz(load_location_config(CONFIG).timezone)


def _store(tmp_path, *, missing: bool):
    day = _day()
    entry = DailyLogEntry(
        date=day, rain_expected="Dry / No Rain", temp_high_c=31.0, temp_low_c=19.0,
        temp_high_low_display="31/19", mslp_trend_24h="steady", synoptic_pattern="weak gradient",
        narrative_markdown=PLACEHOLDER if missing else "## Today's Forecast\nWritten.",
        # The served call the brief is written from — the writer has nothing
        # to write without one.
        served_call={"today_properties": {
            "rain": True, "onset_hour": "14:00", "precip_mm": 4.2, "temp_high_c": 31.0, "temp_low_c": 19.0,
            "rain_probability_pct": 70, "peak_wind_primary_kmh": 30.0,
        }, "extended_properties": []},
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc), llm_provider="test", llm_model="test",
            pipeline_version="0",
            degradations=[RunDegradation(code=DEGRADATION_NARRATIVE, summary="s", detail="d")] if missing else [],
        ),
        prediction_rows=[IssuancePredictions(
            issued_at=datetime.now(timezone.utc),
            predictions=ModelPredictionsByLead(
                day0=[ModelPrediction(model="olw_blend", rain=False, rain_probability_pct=10)],
            ),
        )],
    )
    log_store.write_log_entry(tmp_path, entry)

    flags = dict(verification_already_written=False, ground_stations_configured=True, local_bulletin_configured=True)
    sha = hashlib.sha256(build_narrative_prompt(load_location_config(CONFIG), **flags).encode()).hexdigest()
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / f"{day}.json").write_text(json.dumps({
        "date": str(day), "note": "",
        "issuances": [{"issued_at": "x", "llm_model": "x", "system_prompt_sha256": "",
                       "judgment_prompt_sha256": "", "narrative_prompt_sha256": sha,
                       "user_prompt": "THE ARCHIVED USER PROMPT"}],
    }))
    return day


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """The command with its model, pages and clock faked; returns what each saw."""
    seen = {"slept": [], "providers": [], "published": []}
    writer = FakeWriter()

    def build(**kwargs):
        seen["providers"].append(kwargs.get("providers"))
        return writer

    class Publisher:
        def publish(self, entry):
            seen["published"].append(entry.narrative_markdown)

    monkeypatch.setattr(cli, "_build_llm_provider", build)
    monkeypatch.setattr(cli, "_build_pages_publisher", lambda *a, **k: Publisher())
    monkeypatch.setattr(cli.time, "sleep", seen["slept"].append)
    seen["writer"] = writer
    return seen


def _run(tmp_path):
    return cli.main([
        "write-up", "--config", CONFIG, "--data-dir", str(tmp_path), "--docs-dir", str(tmp_path / "docs"),
        "--public-url", "https://example.test/", "--wait-s", "3600",
    ])


def test_a_day_with_its_write_up_spends_nothing_and_waits_for_nothing(tmp_path, wired):
    _store(tmp_path, missing=False)

    assert _run(tmp_path) == 0
    assert wired["providers"] == [], "no provider built, so no request"
    assert wired["slept"] == []


def test_a_missing_write_up_is_written_after_the_wait_and_republished(tmp_path, wired):
    day = _store(tmp_path, missing=True)

    assert _run(tmp_path) == 0

    entry = log_store.read_log_entry(tmp_path, day)
    assert wired["slept"] == [3600]
    assert f"## Today's Forecast\n\n{TODAY}" in entry.narrative_markdown
    assert entry.write_up_sources["today"] == "llm", "the model's section, the rest code's"
    assert entry.narrative_markdown.rstrip().endswith("Today's Forecast by Gemini 3.6 Flash; the rest written by code.")
    assert not [d for d in entry.meta.degradations or [] if d.code == DEGRADATION_NARRATIVE]
    assert entry.meta.narrative_llm_model == "gemini-3.6-flash"
    assert wired["published"] == [entry.narrative_markdown]
    [(system_prompt, user_prompt, schema)] = wired["writer"].calls
    assert schema == "WriteUpResponse"
    assert "THE CALL" in user_prompt and "31/19" in user_prompt, "the brief, from the stored call"
    assert '"today"' in system_prompt


def test_every_link_may_write_under_the_live_config(tmp_path, wired):
    """`llm_fallback_calls: both_calls` since 2026-10-02: the gateway writes
    when Gemini refuses, because the forecast is already published and on
    the first live day seven 503s left the day without prose. Two Gemini
    links since 2026-10-08, the version test (ROADMAP item 132)."""
    _store(tmp_path, missing=True)

    _run(tmp_path)

    [providers] = wired["providers"]
    assert [getattr(p, "kind", p) for p in providers] == ["gemini", "gemini", "openai", "openai"]


def test_a_thin_answer_is_refused_and_the_floor_stays(tmp_path, wired, capsys):
    """2026-10-09, the fourth day running: the gateway's free model answered
    613 characters with none of the seven headings, and it replaced a floor
    that carried the Extended Outlook. An answer missing the headings it was
    asked for is refused like a 503: the day keeps code's write-up, nothing
    is published, and the verdict lands on the ledger row (ROADMAP item 192
    orders routes by audit pass rate, which needs it there)."""
    from openlocalweather.spend import read_ledger

    day = _store(tmp_path, missing=True)
    wired["writer"].thin = True

    assert _run(tmp_path) == 0

    entry = log_store.read_log_entry(tmp_path, day)
    assert entry.narrative_markdown == PLACEHOLDER
    assert [d.code for d in entry.meta.degradations] == [DEGRADATION_NARRATIVE]
    assert entry.meta.narrative_llm_model is None
    assert wired["published"] == []
    err = capsys.readouterr().err
    assert "refused" in err and "30.7" in err
    [row] = [r for r in read_ledger(tmp_path) if r.purpose == "write-up"]
    assert row.audit.startswith("refused: today (figures not in the brief: 30.7°C")


def test_a_thin_first_link_falls_through_to_the_next(tmp_path, wired, monkeypatch, capsys):
    """Item 192: a failed audit falls through like a refusal, for the
    write-up only, and spends one call. The next link gets the same ask,
    and its answer is the one credited."""
    from openlocalweather.llm.fallback import FallbackProvider
    from openlocalweather.spend import read_ledger

    thin, good = FakeWriter(thin=True), FakeWriter()
    good.model = "gemini-3.8-flash"
    monkeypatch.setattr(cli, "_build_llm_provider", lambda **k: FallbackProvider([thin, good]))
    day = _store(tmp_path, missing=True)

    assert _run(tmp_path) == 0

    entry = log_store.read_log_entry(tmp_path, day)
    assert f"## Today's Forecast\n\n{TODAY}" in entry.narrative_markdown
    assert entry.meta.narrative_llm_model == "gemini-3.8-flash"
    assert (len(thin.calls), len(good.calls)) == (1, 1)
    assert "refused" in capsys.readouterr().err
    audits = [(r.model, r.audit) for r in read_ledger(tmp_path) if r.purpose == "write-up"]
    assert audits[0][0] == "gemini-3.6-flash" and audits[0][1].startswith("refused: today")
    assert audits[1][0] == "gemini-3.8-flash" and audits[1][1].startswith("passed: today; unanswered:")


def test_scored_call_keeps_the_write_up_on_the_first_link(tmp_path, wired, monkeypatch):
    """Item 180's rule, kept for a deployment whose gateway cannot write."""
    from openlocalweather.llm.provider import FallbackCalls

    real = cli.load_location_config
    monkeypatch.setattr(
        cli, "load_location_config",
        lambda path: real(path).model_copy(update={"llm_fallback_calls": FallbackCalls.SCORED_CALL}),
    )
    _store(tmp_path, missing=True)

    _run(tmp_path)

    [providers] = wired["providers"]
    assert [getattr(p, "kind", p) for p in providers] == ["gemini"]


def test_a_refused_second_chance_leaves_the_day_as_it_was(tmp_path, wired):
    day = _store(tmp_path, missing=True)
    wired["writer"].refuse = True

    assert _run(tmp_path) == 0

    entry = log_store.read_log_entry(tmp_path, day)
    assert entry.narrative_markdown == PLACEHOLDER
    assert [d for d in entry.meta.degradations or [] if d.code == DEGRADATION_NARRATIVE]
    assert wired["published"] == []


def test_the_write_up_reads_the_stored_call_as_the_forecasters_call(tmp_path, wired):
    """ROADMAP item 189: the entry carries the call as served, so the
    narrative is rendered around exactly what was published rather than a
    rebuild from the page's fields and the blend's row."""
    day = _store(tmp_path, missing=True)
    entry = log_store.read_log_entry(tmp_path, day)
    entry.served_call = {
        "today_properties": {"rain": True, "rain_expected": "Evening Showers", "temp_high_c": 31.0,
                             "temp_low_c": 19.0, "onset_hour": "17:00", "precip_mm": 4.4, "rain_probability_pct": 71},
        "extended_properties": [{"lead_time_days": 3, "rain": False, "rain_probability_pct": 30}],
    }
    entry.call_source = "code_blend"
    log_store.write_log_entry(tmp_path, entry)

    assert cli.main(["write-up", "--config", CONFIG, "--data-dir", str(tmp_path), "--docs-dir", str(tmp_path / "docs"),
                     "--public-url", "https://example.test/", "--wait-s", "0"]) == 0

    # The brief carries the served call as the page shows it — item 191.
    _system, user, _schema = wired["writer"].calls[-1]
    assert "rain: yes, Evening Showers; onset 17:00; 4.4 mm; 71%" in user
    assert "Day+3" in user and "dry, 30%" in user
