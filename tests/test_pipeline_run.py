from datetime import date, datetime, timedelta, timezone

import pytest

from openlocalweather.config import LocationConfig, Point, RegionPoint, SecondaryPoint
from openlocalweather.defaults import MODELS
from openlocalweather.fetch import metar as metar_fetch
import requests

from openlocalweather.fetch import open_meteo
from openlocalweather.fetch import waqi as waqi_fetch
from openlocalweather.fetch.bulletin import NullBulletinFetcher
from openlocalweather.llm.schema import GeminiForecastResponse, TodayProperties, VerificationNote
from openlocalweather.models import DailyLogEntry, LogEntryMeta, ModelPredictionsByLead
from openlocalweather.pipeline import PipelineDeps, run_daily_pipeline
from openlocalweather.store import log_store

LOCATION = LocationConfig(
    region_name="Test Region",
    primary_place_name="Test Town",
    timezone="UTC",
    primary_point=Point(lat=1.0, lon=2.0),
    secondary_point=SecondaryPoint(),  # disabled — keeps fixtures simpler
    region_points=[RegionPoint(name="Neighbor", lat=1.5, lon=2.5)],
    metar_station_icao="",  # skip METAR
    waqi_stations=[],  # skip WAQI
    local_bulletin_url="",  # NullBulletinFetcher
)


def hourly_fixture() -> dict:
    fields: dict[str, list] = {"time": ["2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00"]}
    for model in MODELS:
        fields[f"precipitation_{model}"] = [0.0, 0.0, 0.0]
        fields[f"windgusts_10m_{model}"] = [10.0, 12.0, 15.0]
        fields[f"temperature_2m_{model}"] = [18.0, 22.0, 26.0]
        fields[f"pressure_msl_{model}"] = [1012.0, 1011.0, 1010.0]
    return {"hourly": fields}


def daily_fixture() -> dict:
    fields: dict[str, list] = {}
    for model in MODELS:
        fields[f"precipitation_sum_{model}"] = [0.0] * 8
        fields[f"windgusts_10m_max_{model}"] = [15.0] * 8
        fields[f"temperature_2m_max_{model}"] = [27.0] * 8
        fields[f"temperature_2m_min_{model}"] = [18.0] * 8
        fields[f"pressure_msl_mean_{model}"] = [1010.0] * 8
    return {"daily": fields}


def archive_fixture(day: date) -> dict:
    return {
        "hourly": {
            "time": [f"{day.isoformat()}T00:00", f"{day.isoformat()}T12:00"],
            "temperature_2m": [18.0, 26.0],
            "precipitation": [0.0, 0.0],
            "windgusts_10m": [10.0, 15.0],
            "pressure_msl": [1012.0, 1010.0],
        }
    }


class FakeLLMProvider:
    model = "fake-model"

    def __init__(self, response: GeminiForecastResponse | None = None):
        self.response = response or self._default_response()
        self.calls: list[tuple[str, str]] = []

    def _default_response(self) -> GeminiForecastResponse:
        return GeminiForecastResponse(
            yesterday_verification="All models did fine yesterday.",
            verification_notes=[VerificationNote(lead_time_days=0, note="Rain call was accurate.")],
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain_expected="Unlikely",
                temp_high_c=27.0,
                temp_low_c=18.0,
                temp_high_low="27°C / 81°F",
            ),
            today_narrative="## Overview\nDry and warm.",
            whatsapp_summary=None,
        )

    def generate(self, system_prompt, user_prompt, response_schema):
        self.calls.append((system_prompt, user_prompt))
        return self.response


@pytest.fixture(autouse=True)
def patch_fetches(monkeypatch):
    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today", lambda *a, **k: hourly_fixture())
    monkeypatch.setattr(open_meteo, "fetch_forecast_daily_extended", lambda *a, **k: daily_fixture())
    monkeypatch.setattr(open_meteo, "fetch_regional_pressure", lambda *a, **k: {"daily": {}})
    monkeypatch.setattr(open_meteo, "fetch_synoptic_pressure", lambda *a, **k: {"points": []})
    monkeypatch.setattr(open_meteo, "fetch_air_quality", lambda *a, **k: {"hourly": {}})
    monkeypatch.setattr(
        open_meteo, "fetch_archive_single_day", lambda lat, lon, day, tz: archive_fixture(day)
    )
    monkeypatch.setattr(
        open_meteo, "fetch_archive_range", lambda lat, lon, start, end, tz: archive_fixture(end)
    )
    monkeypatch.setattr(metar_fetch, "fetch_metar", lambda icao: None)
    monkeypatch.setattr(waqi_fetch, "fetch_ground_aqi_stations", lambda stations, token: [])

    # Backstop: anything NOT patched above must fail loudly rather than reach
    # the internet. Adding fetch_synoptic_pressure to the pipeline silently
    # sent this suite to the live API — two tests went from ~1s to 30s each,
    # and the pipeline's own try/except swallowed any sign of it. A test that
    # quietly depends on a network is a test that fails on a plane, in CI
    # behind a proxy, or when the upstream is down, and blames the wrong code.
    def _no_network(*args, **kwargs):
        raise AssertionError(
            f"unmocked HTTP call in a test: {args[:1]} — patch it in patch_fetches"
        )

    monkeypatch.setattr(requests, "get", _no_network)
    monkeypatch.setattr(requests, "post", _no_network)


def make_deps(tmp_path, llm=None) -> PipelineDeps:
    return PipelineDeps(
        location=LOCATION,
        data_dir=tmp_path,
        llm_provider=llm or FakeLLMProvider(),
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )


def test_dry_run_does_not_write_any_files(tmp_path):
    deps = make_deps(tmp_path)
    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    assert result.log_entry.rain_expected == "Unlikely"
    assert log_store.read_log_entry(tmp_path, date(2026, 8, 11)) is None
    assert not (tmp_path / "track_record.json").exists()
    assert not (tmp_path / "actuals_cache" / "actuals.json").exists()
    assert result.published is False
    assert result.emailed is False


def test_real_run_writes_log_entry_and_track_record(tmp_path):
    deps = make_deps(tmp_path)
    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    written = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert written is not None
    assert written.rain_expected == "Unlikely"
    assert written.temp_high_c == 27.0
    assert (tmp_path / "track_record.json").exists()
    assert (tmp_path / "actuals_cache" / "actuals.json").exists()
    assert result.published is False  # no publisher configured
    assert result.emailed is False  # no email_sender configured


def test_today_entry_carries_extracted_model_predictions(tmp_path):
    deps = make_deps(tmp_path)
    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)
    assert len(result.log_entry.model_predictions.day0) == len(MODELS)
    assert len(result.log_entry.model_predictions.day3) == len(MODELS)
    assert len(result.log_entry.model_predictions.day7) == len(MODELS)


def _seed_yesterday_log_entry(tmp_path, d: date) -> None:
    entry = DailyLogEntry(
        date=d,
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26/18",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="narrative",
        model_predictions=ModelPredictionsByLead(
            day0=[
                {
                    "model": m,
                    "rain": False,
                    "onset": None,
                    "wind_kmh": 15.0,
                    "high_c": 27.0,
                    "low_c": 18.0,
                    "mslp_trend": -1.0,
                }
                for m in MODELS
            ]
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc), llm_provider="test", llm_model="test", pipeline_version="0"
        ),
    )
    log_store.write_log_entry(tmp_path, entry)


def test_yesterdays_prediction_gets_verified_and_noted(tmp_path):
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    _seed_yesterday_log_entry(tmp_path, yesterday)

    llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="Correct no-rain call.",
            verification_notes=[VerificationNote(lead_time_days=0, note="Rain correctly not predicted.")],
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain_expected="Unlikely", temp_high_c=27.0, temp_low_c=18.0, temp_high_low="27°C / 81°F"
            ),
            today_narrative="## Overview\nDry.",
        )
    )
    deps = make_deps(tmp_path, llm=llm)
    result = run_daily_pipeline(deps, today=today, dry_run=False)

    assert (yesterday, 0) in result.newly_verified

    patched = log_store.read_log_entry(tmp_path, yesterday)
    assert patched.verification.day0.verified is True
    assert patched.verification.day0.note == "Rain correctly not predicted."


def test_publisher_and_email_sender_invoked_when_configured(tmp_path):
    published_entries = []
    emailed_entries = []

    class FakePublisher:
        def publish(self, entry):
            published_entries.append(entry)

    class FakeEmailSender:
        def send(self, entry):
            emailed_entries.append(entry)

    deps = make_deps(tmp_path)
    deps.publisher = FakePublisher()
    deps.email_sender = FakeEmailSender()

    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    assert result.published is True
    assert result.emailed is True
    assert len(published_entries) == 1
    assert len(emailed_entries) == 1


def test_publisher_not_invoked_on_dry_run_even_if_configured(tmp_path):
    class FakePublisher:
        def publish(self, entry):
            raise AssertionError("publish() must not be called during --dry-run")

    deps = make_deps(tmp_path)
    deps.publisher = FakePublisher()

    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)
    assert result.published is False


def test_weekly_batch_day_triggers_full_archive_refetch(tmp_path, monkeypatch):
    calls = {"range": 0, "single": 0}
    monkeypatch.setattr(
        open_meteo,
        "fetch_archive_range",
        lambda *a, **k: (calls.__setitem__("range", calls["range"] + 1), archive_fixture(date(2026, 8, 10)))[1],
    )
    monkeypatch.setattr(
        open_meteo,
        "fetch_archive_single_day",
        lambda *a, **k: (calls.__setitem__("single", calls["single"] + 1), archive_fixture(date(2026, 8, 10)))[1],
    )

    # 2026-08-10 is a Monday (WEEKLY_BATCH_WEEKDAY default).
    monday = date(2026, 8, 10)
    assert monday.weekday() == 0
    deps = make_deps(tmp_path)
    run_daily_pipeline(deps, today=monday, dry_run=False)

    assert calls["range"] == 1
    assert calls["single"] == 0


def test_non_weekly_day_uses_single_day_upsert(tmp_path, monkeypatch):
    calls = {"range": 0, "single": 0}
    monkeypatch.setattr(
        open_meteo,
        "fetch_archive_range",
        lambda *a, **k: (calls.__setitem__("range", calls["range"] + 1), archive_fixture(date(2026, 8, 10)))[1],
    )
    monkeypatch.setattr(
        open_meteo,
        "fetch_archive_single_day",
        lambda *a, **k: (calls.__setitem__("single", calls["single"] + 1), archive_fixture(date(2026, 8, 10)))[1],
    )

    tuesday = date(2026, 8, 11)
    assert tuesday.weekday() == 1
    deps = make_deps(tmp_path)
    run_daily_pipeline(deps, today=tuesday, dry_run=False)

    assert calls["range"] == 0
    assert calls["single"] == 1


def test_llm_receives_system_and_user_prompt(tmp_path):
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    assert len(llm.calls) == 1
    system_prompt, user_prompt = llm.calls[0]
    assert "Test Town" in system_prompt
    assert "2026-08-11" in user_prompt


# ---------------------------------------------------------------------------
# Multi-station ground AQI, end-to-end through the pipeline
# ---------------------------------------------------------------------------


def test_multi_station_aqi_readings_flow_through_to_log_entry(tmp_path, monkeypatch):
    from openlocalweather.config import WaqiStation
    from openlocalweather.models import GroundAQIReading

    location_with_stations = LOCATION.model_copy(
        update={
            "waqi_stations": [
                WaqiStation(name="Kisumu Airport", station_id="A418534"),
                WaqiStation(name="Dunga Beach", station_id="A418504"),
            ]
        }
    )
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="Kisumu Airport", station_id="A418534", aqi=42, pm25=18.0, pm10=30.0),
            GroundAQIReading(name="Dunga Beach", station_id="A418504", aqi=171, pm25=171.0, pm10=37.0),
        ],
    )

    deps = PipelineDeps(
        location=location_with_stations,
        data_dir=tmp_path,
        llm_provider=FakeLLMProvider(),
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )
    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    assert len(result.log_entry.ground_aqi) == 2
    names = {r.name for r in result.log_entry.ground_aqi}
    assert names == {"Kisumu Airport", "Dunga Beach"}

    written = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert len(written.ground_aqi) == 2


def test_llm_receives_precomputed_aqi_range_and_worst_station(tmp_path, monkeypatch):
    from openlocalweather.config import WaqiStation
    from openlocalweather.models import GroundAQIReading

    location_with_stations = LOCATION.model_copy(
        update={"waqi_stations": [WaqiStation(name="A", station_id="A1"), WaqiStation(name="B", station_id="A2")]}
    )
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="A", station_id="A1", aqi=42, measured_at=datetime.now(timezone.utc)),
            GroundAQIReading(name="B", station_id="A2", aqi=168, measured_at=datetime.now(timezone.utc)),
        ],
    )

    llm = FakeLLMProvider()
    deps = PipelineDeps(
        location=location_with_stations,
        data_dir=tmp_path,
        llm_provider=llm,
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = llm.calls[0]
    assert '"aqi_min": 42' in user_prompt
    assert '"aqi_max": 168' in user_prompt
    assert '"highest_station_name": "B"' in user_prompt


def test_llm_receives_stale_flag_and_hours_old_per_reading(tmp_path, monkeypatch):
    from openlocalweather.config import WaqiStation
    from openlocalweather.models import GroundAQIReading

    location_with_stations = LOCATION.model_copy(
        update={"waqi_stations": [WaqiStation(name="Stale Station", station_id="A1")]}
    )
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(
                name="Stale Station",
                station_id="A1",
                aqi=200,
                measured_at=datetime.now(timezone.utc) - timedelta(hours=7.2),
            ),
        ],
    )

    llm = FakeLLMProvider()
    deps = PipelineDeps(
        location=location_with_stations,
        data_dir=tmp_path,
        llm_provider=llm,
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = llm.calls[0]
    assert '"stale": true' in user_prompt
    assert '"hours_old": 7.2' in user_prompt
    # A single stale-only station has nothing fresh to summarize a range
    # from — GROUND AQI SUMMARY must fall back to "Not applicable", not
    # present the stale 200 reading as if it were current.
    assert "Not applicable" in user_prompt


def test_no_stations_configured_yields_empty_ground_aqi_and_no_summary(tmp_path):
    # LOCATION already has waqi_stations=[] — confirms the pre-existing
    # zero-station path (fetch_ground_aqi_stations mocked to [] by the
    # autouse fixture) still degrades cleanly, not just the new multi path.
    llm = FakeLLMProvider()
    deps = PipelineDeps(
        location=LOCATION,
        data_dir=tmp_path,
        llm_provider=llm,
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = llm.calls[0]
    assert "no ground station reported data" in user_prompt
    assert "Not applicable" in user_prompt


# ---------------------------------------------------------------------------
# run_refresh_pipeline — the evening second run
# ---------------------------------------------------------------------------


from openlocalweather.pipeline import (
    RefreshWithoutMorningRunError,
    run_refresh_pipeline,
)


def test_refresh_requires_existing_morning_entry(tmp_path):
    deps = make_deps(tmp_path)
    with pytest.raises(RefreshWithoutMorningRunError):
        run_refresh_pipeline(deps, today=date(2026, 8, 11), dry_run=True)


def test_refresh_preserves_model_predictions_from_morning_run(tmp_path):
    # First, a real morning run.
    morning_deps = make_deps(tmp_path)
    morning_result = run_daily_pipeline(morning_deps, today=date(2026, 8, 11), dry_run=False)
    original_predictions = morning_result.log_entry.model_predictions

    # Then an evening refresh with DIFFERENT fresh model data.
    evening_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a — refresh",
            verification_notes=[],
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain_expected="Now raining", temp_high_c=25.0, temp_low_c=17.0, temp_high_low="25°C / 77°F"
            ),
            today_narrative="## Overview\nRain has moved in this evening.",
        )
    )
    refresh_deps = make_deps(tmp_path, llm=evening_llm)
    refresh_result = run_refresh_pipeline(refresh_deps, today=date(2026, 8, 11), dry_run=False)

    # Narrative/properties changed...
    assert refresh_result.log_entry.rain_expected == "Now raining"
    assert refresh_result.log_entry.temp_high_c == 25.0
    assert "Rain has moved in" in refresh_result.log_entry.narrative_markdown
    # ...but model_predictions (what tomorrow's verification scores) did NOT.
    assert refresh_result.log_entry.model_predictions == original_predictions


def test_refresh_snapshots_morning_issuance_before_overwriting(tmp_path):
    """Real bug fixed: the morning narrative used to be silently gone the
    moment a refresh landed — recoverable only from git history, not from
    anything the site or data file exposed. morning_issuance must capture
    exactly what the morning run actually published, before the refresh
    overwrites the top-level fields with the evening's new values."""
    morning_deps = make_deps(tmp_path)
    morning_result = run_daily_pipeline(morning_deps, today=date(2026, 8, 11), dry_run=False)
    assert morning_result.log_entry.morning_issuance is None, "a fresh morning entry has nothing to snapshot yet"

    evening_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a — refresh",
            verification_notes=[],
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain_expected="Now raining", temp_high_c=25.0, temp_low_c=17.0, temp_high_low="25°C / 77°F"
            ),
            today_narrative="## Overview\nRain has moved in this evening.",
        )
    )
    refresh_result = run_refresh_pipeline(make_deps(tmp_path, llm=evening_llm), today=date(2026, 8, 11), dry_run=False)

    snapshot = refresh_result.log_entry.morning_issuance
    assert snapshot is not None, "morning_issuance must be populated once a refresh has happened"
    assert snapshot.rain_expected == "Unlikely"  # FakeLLMProvider's default morning response
    assert snapshot.temp_high_c == 27.0
    assert "Dry and warm" in snapshot.narrative_markdown
    assert snapshot.generated_at_utc == morning_result.log_entry.meta.generated_at_utc
    # And the top-level fields really did move on to the evening's values —
    # the snapshot is an addition, not a substitute for the overwrite.
    assert refresh_result.log_entry.rain_expected == "Now raining"
    assert "Rain has moved in" in refresh_result.log_entry.narrative_markdown


def test_refresh_does_not_resnapshot_on_a_second_same_day_refresh(tmp_path):
    """Defensive case, shouldn't normally happen (evening_refresh.yml's
    check job gates on meta.refreshed_at already being set) but must be
    correct if it ever does: a second refresh the same day must not
    replace the true morning snapshot with an already-refreshed version."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    first_refresh_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a", verification_notes=[], skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain_expected="Light rain", temp_high_c=24.0, temp_low_c=16.0, temp_high_low="24°C / 75°F"
            ),
            today_narrative="## Overview\nFirst refresh.",
        )
    )
    run_refresh_pipeline(make_deps(tmp_path, llm=first_refresh_llm), today=date(2026, 8, 11), dry_run=False)

    second_refresh_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a", verification_notes=[], skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain_expected="Heavy rain", temp_high_c=22.0, temp_low_c=15.0, temp_high_low="22°C / 72°F"
            ),
            today_narrative="## Overview\nSecond refresh.",
        )
    )
    second_result = run_refresh_pipeline(make_deps(tmp_path, llm=second_refresh_llm), today=date(2026, 8, 11), dry_run=False)

    # Still the TRUE morning values (FakeLLMProvider's default), not the
    # first refresh's "Light rain" — that would mean the real morning
    # issuance got silently replaced by an intermediate refreshed state.
    assert second_result.log_entry.morning_issuance.rain_expected == "Unlikely"
    assert "Dry and warm" in second_result.log_entry.morning_issuance.narrative_markdown
    # And the top-level fields reflect the LATEST (second) refresh.
    assert second_result.log_entry.rain_expected == "Heavy rain"


def test_refresh_preserves_verification_and_meta_generated_at(tmp_path):
    morning_deps = make_deps(tmp_path)
    morning_result = run_daily_pipeline(morning_deps, today=date(2026, 8, 11), dry_run=False)
    original_generated_at = morning_result.log_entry.meta.generated_at_utc
    original_verification = morning_result.log_entry.verification

    refresh_deps = make_deps(tmp_path)
    refresh_result = run_refresh_pipeline(refresh_deps, today=date(2026, 8, 11), dry_run=False)

    assert refresh_result.log_entry.meta.generated_at_utc == original_generated_at
    assert refresh_result.log_entry.verification == original_verification
    assert refresh_result.log_entry.meta.refreshed_at is not None
    assert refresh_result.log_entry.meta.refreshed_at > original_generated_at


def test_refresh_dry_run_does_not_write_or_publish(tmp_path):
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    before = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    class FailingPublisher:
        def publish(self, entry):
            raise AssertionError("publish() must not be called during --dry-run")

    deps = make_deps(tmp_path)
    deps.publisher = FailingPublisher()
    result = run_refresh_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    assert result.published is False
    after = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert after == before  # nothing written to disk


def test_refresh_real_run_writes_and_publishes(tmp_path):
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    published_entries = []

    class FakePublisher:
        def publish(self, entry):
            published_entries.append(entry)

    deps = make_deps(tmp_path)
    deps.publisher = FakePublisher()
    result = run_refresh_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    assert result.published is True
    assert len(published_entries) == 1
    on_disk = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert on_disk.meta.refreshed_at is not None


def test_refresh_never_emails_even_when_email_sender_configured(tmp_path):
    # Web-only by design in this first version — see pipeline.py's comment.
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    emailed_entries = []

    class FakeEmailSender:
        def send(self, entry):
            emailed_entries.append(entry)

    deps = make_deps(tmp_path)
    deps.email_sender = FakeEmailSender()
    run_refresh_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    assert emailed_entries == []


def test_refresh_llm_receives_refresh_mode_prompt_and_morning_narrative(tmp_path):
    morning_llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=morning_llm), today=date(2026, 8, 11), dry_run=False)

    evening_llm = FakeLLMProvider()
    run_refresh_pipeline(make_deps(tmp_path, llm=evening_llm), today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = evening_llm.calls[0]
    assert "REFRESH MODE" in system_prompt
    assert "MORNING NARRATIVE" in user_prompt
    assert "Dry and warm" in user_prompt  # the morning FakeLLMProvider's default narrative


def test_refresh_updates_ground_aqi_with_fresh_readings(tmp_path, monkeypatch):
    from openlocalweather.config import WaqiStation
    from openlocalweather.models import GroundAQIReading

    location_with_station = LOCATION.model_copy(
        update={"waqi_stations": [WaqiStation(name="Test Station", station_id="A1")]}
    )
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="Test Station", station_id="A1", aqi=30, measured_at=datetime.now(timezone.utc))
        ],
    )
    morning_deps = make_deps(tmp_path)
    morning_deps.location = location_with_station
    run_daily_pipeline(morning_deps, today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="Test Station", station_id="A1", aqi=90, measured_at=datetime.now(timezone.utc))
        ],
    )
    refresh_deps = make_deps(tmp_path)
    refresh_deps.location = location_with_station
    result = run_refresh_pipeline(refresh_deps, today=date(2026, 8, 11), dry_run=True)

    assert result.log_entry.ground_aqi[0].aqi == 90  # the fresh evening reading, not the morning's 30
