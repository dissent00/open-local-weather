from datetime import date, datetime, timedelta, timezone

import pytest

from openlocalweather.config import LocationConfig, Point, RegionPoint, SecondaryPoint
from openlocalweather.dates import now_in_tz
from openlocalweather.defaults import MODELS, BLEND_MODEL_ID
from openlocalweather.fetch import metar as metar_fetch
import requests

from openlocalweather.fetch import model_run as model_run_fetch
from openlocalweather.fetch import open_meteo
from openlocalweather.fetch import waqi as waqi_fetch
from openlocalweather.fetch.bulletin import NullBulletinFetcher
from openlocalweather.llm.schema import GeminiForecastResponse, TodayProperties, VerificationNote
from openlocalweather.models import (
    DailyLogEntry,
    GroundAQIReading,
    LogEntryMeta,
    ModelPredictionsByLead,
)
from openlocalweather import pipeline
from openlocalweather.pipeline import PipelineDeps, run_daily_pipeline
from openlocalweather.store import actuals_cache as actuals_cache_store
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
                rain=False,
                rain_expected="Unlikely",
                temp_high_c=27.0,
                temp_low_c=18.0,
                temp_high_low="27°C / 81°F",
            ),
            today_narrative="## Overview\nDry and warm.",
            whatsapp_summary=None,
        )

    # Real providers call this before EVERY request, retries included, and
    # that is how the spend cap counts. A stub that skips it silently exempts
    # every pipeline test from the cap — which is how the undercounting bug
    # survived: the tests could not see the seam they were meant to cover.
    before_attempt = None

    def generate(self, system_prompt, user_prompt, response_schema):
        if self.before_attempt is not None:
            self.before_attempt()
        self.calls.append((system_prompt, user_prompt))
        return self.response


def sun_fixture():
    """Kisumu's real figures for the fixture date — sunset at 18:47 is the
    number that started this: the "evening" run fires 32 minutes before it."""
    return {
        "daily": {
            "time": ["2026-08-11", "2026-08-12"],
            "sunrise": ["2026-08-11T06:40", "2026-08-12T06:40"],
            "sunset": ["2026-08-11T18:47", "2026-08-12T18:46"],
        }
    }


def forward_hourly_fixture():
    """Two days of hourly data, so a run at any hour has hours still ahead."""
    times, precip = [], []
    for i in range(48):
        day = 11 + i // 24
        times.append(f"2026-08-{day:02d}T{i % 24:02d}:00")
        precip.append(0.0)
    return {"hourly": {"time": times, "precipitation_gfs_seamless": precip}}


@pytest.fixture(autouse=True)
def patch_fetches(monkeypatch):
    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today", lambda *a, **k: hourly_fixture())
    monkeypatch.setattr(open_meteo, "fetch_forecast_daily_extended", lambda *a, **k: daily_fixture())
    monkeypatch.setattr(open_meteo, "fetch_regional_pressure", lambda *a, **k: {"daily": {}})
    monkeypatch.setattr(open_meteo, "fetch_synoptic_pressure", lambda *a, **k: {"points": []})
    monkeypatch.setattr(open_meteo, "fetch_air_quality", lambda *a, **k: {"hourly": {}})
    # Time-of-day context. Mocked rather than left to degrade, because the
    # pipeline treats these as best-effort — an unmocked failure here is
    # invisible, and every prompt assertion below would then be checking the
    # DEGRADED path while appearing to check the real one.
    monkeypatch.setattr(open_meteo, "fetch_sun_times", lambda *a, **k: sun_fixture())
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: forward_hourly_fixture()
    )
    monkeypatch.setattr(
        open_meteo, "fetch_archive_single_day", lambda lat, lon, day, tz: archive_fixture(day)
    )
    monkeypatch.setattr(
        open_meteo, "fetch_archive_range", lambda lat, lon, start, end, tz: archive_fixture(end)
    )
    monkeypatch.setattr(metar_fetch, "fetch_metar", lambda icao: None)
    monkeypatch.setattr(waqi_fetch, "fetch_ground_aqi_stations", lambda stations, token: [])
    # Default: no observed run available, same as the four blend models'
    # real HTTP 500 — every test not specifically about guidance recency
    # exercises the DERIVED fallback path, not a live request.
    monkeypatch.setattr(model_run_fetch, "fetch_model_run", lambda model: None)

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


def predictions_block(user_prompt: str) -> str:
    """Just the EXTRACTED PER-MODEL PREDICTIONS section. The raw guidance
    arrays above it carry the same numbers, so an assertion against the whole
    prompt cannot tell which block a value came from."""
    start = user_prompt.index("EXTRACTED PER-MODEL PREDICTIONS")
    return user_prompt[start : user_prompt.index("\nCONVECTIVE INSTABILITY", start)]


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
    day0 = result.log_entry.model_predictions.day0

    # Every extracted model, PLUS our own blended call — the forecast the
    # reader actually gets, scored as a peer of the guidance that fed it.
    assert {p.model for p in day0} == {*MODELS, BLEND_MODEL_ID}
    assert len(result.log_entry.model_predictions.day3) == len(MODELS)
    assert len(result.log_entry.model_predictions.day7) == len(MODELS)


def test_the_blend_is_scored_on_what_it_committed_to(tmp_path):
    # Built from today_properties' structured fields, not parsed back out of
    # the prose. What gets scored is what the forecaster committed to.
    deps = make_deps(tmp_path)
    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)
    blend = next(
        p for p in result.log_entry.model_predictions.day0 if p.model == BLEND_MODEL_ID
    )

    assert blend.high_c == result.log_entry.temp_high_c
    assert blend.low_c == result.log_entry.temp_low_c
    # Absent, never zero: peak_wind_kmh in today_properties is the SECONDARY
    # point's and mslp_trend_24h is prose, so scoring either against the
    # primary point's observations would compare two different things.
    assert blend.wind_kmh is None
    assert blend.mslp_trend is None


def test_the_forecaster_is_never_shown_its_own_record(tmp_path):
    """The blend is scored, stored and published, and withheld from the
    prompt. Seeing another model's record adjusts how an external input is
    weighed; seeing its OWN closes a loop, and the cheap way to protect a
    score you can see is to stop making independent calls.

    Asserted on the prompt text because that is the only place the rule can
    actually be broken, and it leaks through two separate blocks — the track
    record and the review findings, which name models."""
    deps = make_deps(tmp_path)
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    system_prompt, user_prompt = deps.llm_provider.calls[-1]
    assert BLEND_MODEL_ID not in user_prompt, (
        "the forecaster can see its own track record or review finding"
    )
    assert BLEND_MODEL_ID not in system_prompt


def test_a_re_issue_is_never_shown_the_blend_either(tmp_path):
    """A THIRD block the rule leaks through, after the track record and the
    review findings: a re-issue is handed the day's stored predictions so its
    narrative describes the numbers the record holds — and the stored Day+0
    list has the blend in it, because the blend is scored."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    llm = FakeLLMProvider()
    pipeline.run_refresh_pipeline(
        make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False
    )

    system_prompt, user_prompt = llm.calls[-1]
    assert BLEND_MODEL_ID not in user_prompt, (
        "the re-issue can see its own prediction in the model predictions block"
    )
    assert BLEND_MODEL_ID not in system_prompt


def test_the_blend_has_no_extended_range_entry(tmp_path):
    # today_properties is a call about today. Emitting a Day+3 row for it
    # would put an unscoreable placeholder into the record and give the
    # accuracy page a model that appears to forecast a range it never did.
    deps = make_deps(tmp_path)
    result = run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    assert BLEND_MODEL_ID not in {p.model for p in result.log_entry.model_predictions.day3}
    assert BLEND_MODEL_ID not in {p.model for p in result.log_entry.model_predictions.day7}


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
                rain=False,
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


def test_a_forced_re_run_does_not_rewrite_yesterdays_verification_note(tmp_path):
    """A later issuance returns a PLACEHOLDER for the verification fields, by
    design. Those must not reach a historical row that was scored this
    morning — the note on yesterday's entry is the record of what was checked,
    not of what the last run of today happened to say."""
    today, yesterday = date(2026, 8, 11), date(2026, 8, 10)
    _seed_yesterday_log_entry(tmp_path, yesterday)

    morning = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="Correct no-rain call.",
            verification_notes=[VerificationNote(lead_time_days=0, note="Rain correctly not predicted.")],
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain=False,
                rain_expected="Unlikely", temp_high_c=27.0, temp_low_c=18.0, temp_high_low="27°C / 81°F"
            ),
            today_narrative="## Overview\nDry.",
        )
    )
    run_daily_pipeline(make_deps(tmp_path, llm=morning), today=today, dry_run=False)

    forced = FakeLLMProvider(
        morning.response.model_copy(
            update={
                "yesterday_verification": "No new verification this run.",
                "verification_notes": [
                    VerificationNote(lead_time_days=0, note="No new verification this run.")
                ],
            }
        )
    )
    run_daily_pipeline(make_deps(tmp_path, llm=forced), today=today, dry_run=False)

    assert log_store.read_log_entry(tmp_path, yesterday).verification.day0.note == (
        "Rain correctly not predicted."
    )
    assert log_store.read_log_entry(tmp_path, today).yesterday_verification_summary == (
        "Correct no-rain call."
    )


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


def test_no_stations_configured_says_nothing_about_ground_stations(tmp_path):
    """A fork that polls no stations must not be told about a source it does
    not have. LOCATION has waqi_stations=[].

    The blocks used to be rendered as "Unavailable — no ground station
    reported data today", which is a fetch failure being reported for
    stations that were never configured, every single day. Absent
    instructions beat instructions saying "ignore this": the model cannot
    mention what it was never told about, and CAMS is then simply the source.
    """
    llm = FakeLLMProvider()
    deps = PipelineDeps(
        location=LOCATION,
        data_dir=tmp_path,
        llm_provider=llm,
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = llm.calls[0]
    assert "GROUND AQI" not in user_prompt
    assert "no ground station reported data" not in user_prompt
    assert "GROUND AQI" not in system_prompt
    assert "cross-reference ground sensor data" not in system_prompt
    assert "Ground AQI stations may occasionally be offline" not in system_prompt
    assert "model (CAMS) data alone" in system_prompt, (
        "the model still has to be told where air quality comes from"
    )


def test_stations_configured_still_get_their_blocks(tmp_path, monkeypatch):
    """The other half of the same switch — the shipped deployment's path."""
    from openlocalweather.config import WaqiStation

    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(
        update={"waqi_stations": [WaqiStation(name="Kisumu Airport", station_id="A1")]}
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = llm.calls[0]
    assert "GROUND AQI STATIONS" in user_prompt
    assert "no ground station reported data" in user_prompt, "configured but silent today"
    assert "Ground AQI stations may occasionally be offline" in system_prompt


def test_no_met_service_configured_is_a_state_not_a_missing_bulletin(tmp_path):
    """LOCATION has no local_bulletin_source_name.

    "LOCAL BULLETIN ():" with nothing under it is a fetch that failed, and the
    prompt went on to demand the service be named EVERY TIME. A fork with no
    met service wired would either report a daily failure or attribute a
    forecast to a service it never consulted.

    Unlike the ground stations, the absence is still stated once — the model
    knows real met services for a real place, so silence here prevents a
    report of a failure but not an invention.
    """
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = llm.calls[0]
    assert "LOCAL BULLETIN" not in user_prompt
    assert "NAME THE LOCAL MET SERVICE" not in system_prompt
    assert "LOCAL MET SERVICE AS A MODEL" not in system_prompt
    assert "No national met service is configured" in system_prompt


def test_a_configured_met_service_is_named_and_carried(tmp_path):
    """The shipped deployment's path, and the one that must not regress: a
    configured service is a peer model the narrative has to name."""
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(
        update={"local_bulletin_source_name": "Kenya Meteorological Department (KMD)"}
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = llm.calls[0]
    assert "LOCAL BULLETIN (Kenya Meteorological Department (KMD)):" in user_prompt
    assert "NAME THE LOCAL MET SERVICE EVERY TIME" in system_prompt
    assert "No national met service is configured" not in system_prompt


def test_a_configured_service_whose_fetch_failed_still_says_so(tmp_path):
    """The third state, and the reason the other two are worth separating: a
    service that IS configured and did not answer is a real absence, and the
    bulletin block has to carry it."""
    class _DownFetcher:
        def fetch(self) -> str:
            return "Bulletin unavailable this run — the source did not respond."

    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(
        update={"local_bulletin_source_name": "Kenya Meteorological Department (KMD)"}
    )
    deps.bulletin_fetcher = _DownFetcher()
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = llm.calls[0]
    assert "LOCAL BULLETIN (Kenya Meteorological Department (KMD)):" in user_prompt
    assert "Bulletin unavailable this run" in user_prompt


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
                rain=False,
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
                rain=False,
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
                rain=False,
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
                rain=False,
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


def test_a_later_issuance_is_told_it_is_one_and_shown_what_was_published(tmp_path):
    morning_llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=morning_llm), today=date(2026, 8, 11), dry_run=False)

    evening_llm = FakeLLMProvider()
    run_refresh_pipeline(make_deps(tmp_path, llm=evening_llm), today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = evening_llm.calls[0]
    assert "LATER ISSUANCE" in system_prompt
    assert "EARLIER TODAY" in user_prompt
    assert "Dry and warm" in user_prompt  # the morning FakeLLMProvider's default narrative


def test_a_third_run_is_shown_both_earlier_narratives(tmp_path):
    """_earlier_issuances used to return only entry.narrative_markdown — one
    element — so a third run was shown the second issuance and had no idea
    the first one existed, even though morning_issuance still held it. The
    prompt must carry every issuance published today, not just the last."""
    run_daily_pipeline(make_deps(tmp_path, llm=FakeLLMProvider()), today=date(2026, 8, 11), dry_run=False)

    second = FakeLLMProvider()
    second.response = second.response.model_copy(
        update={"today_narrative": "## Overview\nSECOND issuance."}
    )
    run_refresh_pipeline(make_deps(tmp_path, llm=second), today=date(2026, 8, 11), dry_run=False)

    third = FakeLLMProvider()
    run_refresh_pipeline(make_deps(tmp_path, llm=third), today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = third.calls[0]
    assert "Dry and warm" in user_prompt, "the first issuance"
    assert "SECOND issuance" in user_prompt, "the second issuance"


def test_every_run_is_told_what_time_it_is(tmp_path):
    """The bug this whole change exists for: the prompt carried a date and
    nothing else, so a run could not tell 06:00 from 18:00 and wrote as though
    the whole day were still ahead."""
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "ISSUED:" in user_prompt
    assert "WHAT MATTERS NOW:" in user_prompt
    assert "sunset 18:47" in user_prompt


def test_the_hours_ahead_are_supplied_separately_from_the_calendar_day(tmp_path):
    """A run issued in the evening was being asked to talk about tonight while
    holding only 00:00-23:00 of today."""
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "HOURS AHEAD" in user_prompt
    assert "TODAY'S MULTI-MODEL GUIDANCE" in user_prompt, (
        "the full calendar day is still needed for daily totals and the "
        "day-over-day comparison"
    )


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


MORNING_AQI_AT = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
AFTERNOON_AQI_AT = datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc)


def _station_location():
    from openlocalweather.config import WaqiStation

    return LOCATION.model_copy(
        update={"waqi_stations": [WaqiStation(name="Ochieng' Avenue", station_id="A1")]}
    )


def _run_morning_then_refetching(tmp_path, monkeypatch, refetched, llm=None):
    """A morning run that captures a real 160, then a refresh whose re-fetch
    returns `refetched`. Returns the refreshed entry."""
    location = _station_location()
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(
                name="Ochieng' Avenue", station_id="A1", aqi=160, measured_at=MORNING_AQI_AT
            )
        ],
    )
    morning_deps = make_deps(tmp_path)
    morning_deps.location = location
    run_daily_pipeline(morning_deps, today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(waqi_fetch, "fetch_ground_aqi_stations", lambda stations, token: refetched)
    refresh_deps = make_deps(tmp_path, llm=llm)
    refresh_deps.location = location
    return pipeline.run_refresh_pipeline(refresh_deps, today=date(2026, 8, 11), dry_run=True).log_entry


def test_a_refetched_null_does_not_erase_the_mornings_reading(tmp_path, monkeypatch):
    """2026-08-22, live: the morning captured Ochieng' Avenue at 160 —
    Unhealthy for Sensitive Groups, the most actionable number in that day's
    forecast. The 11:00Z re-fetch returned the same station with aqi: null and
    the entry kept the absence. Fresher is not better when the fresher value
    is "unknown"; the sun times already work this way."""
    entry = _run_morning_then_refetching(
        tmp_path,
        monkeypatch,
        [
            GroundAQIReading(
                name="Ochieng' Avenue", station_id="A1", aqi=None, measured_at=AFTERNOON_AQI_AT
            )
        ],
    )

    assert entry.ground_aqi[0].aqi == 160
    assert entry.ground_aqi[0].measured_at == MORNING_AQI_AT, (
        "the kept reading must keep its own timestamp, or hours_old and stale lie about it"
    )


def test_a_station_missing_from_the_refetch_keeps_its_reading(tmp_path, monkeypatch):
    """fetch_ground_aqi_stations drops a station that failed, so a station
    that is simply absent is a fetch failure — the same absence as a null, and
    it must not erase a measurement either."""
    entry = _run_morning_then_refetching(tmp_path, monkeypatch, [])

    assert [r.aqi for r in entry.ground_aqi] == [160]


def test_the_kept_reading_reaches_the_prompt_with_its_age(tmp_path, monkeypatch):
    """The point of keeping it. The narrative can say the last real reading
    was 160 at midnight and is now hours old, which is far more useful than
    the silence a null produces."""
    llm = FakeLLMProvider()
    _run_morning_then_refetching(
        tmp_path,
        monkeypatch,
        [
            GroundAQIReading(
                name="Ochieng' Avenue", station_id="A1", aqi=None, measured_at=AFTERNOON_AQI_AT
            )
        ],
        llm=llm,
    )

    _, user_prompt = llm.calls[-1]
    assert '"aqi": 160' in user_prompt
    assert '"stale": true' in user_prompt
    assert "GROUND AQI LAST KNOWN" in user_prompt, (
        "the re-issue must quote the last real reading, not report nothing"
    )


# ---------------------------------------------------------------------------
# The hard spend cap, exercised through a real pipeline run
# ---------------------------------------------------------------------------


def test_the_cap_refuses_a_run_and_the_llm_is_never_called(tmp_path, monkeypatch):
    """The guard has to stop the call, not merely count it.

    A cap that records an attempt and then lets the request through would
    look correct in the ledger and cost exactly as much money.
    """
    from dataclasses import replace

    from openlocalweather.spend import SpendCapExceeded, record_attempt

    called = []

    class RefusingProvider(FakeLLMProvider):
        model = "fake-model"

        def generate(self, *a, **kw):
            called.append(1)
            return super().generate(*a, **kw)

    deps = make_deps(tmp_path, llm=RefusingProvider())
    # LocationConfig is a pydantic model, so model_copy rather than replace.
    deps = replace(
        deps, location=LOCATION.model_copy(update={"max_llm_calls_per_24h": 1})
    )

    # Burn the single allowed call.
    record_attempt(
        tmp_path, provider="x", model="y", purpose="test",
        max_calls=1,
    )

    with pytest.raises(SpendCapExceeded):
        run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    assert called == [], "the provider must never be reached once the cap is hit"


def test_a_normal_run_records_exactly_one_call(tmp_path):
    """Counting has to be accurate in the ordinary case too — an
    over-counting cap would refuse legitimate forecasts."""
    from openlocalweather.spend import read_ledger

    deps = make_deps(tmp_path)
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    ledger = read_ledger(tmp_path)
    assert len(ledger) == 1
    assert ledger[0].purpose == "forecast"
    assert ledger[0].model


def test_a_dry_run_still_counts_because_it_still_calls_the_llm(tmp_path):
    """--dry-run skips writes, commit and email, but it DOES call the model
    (that is the point of it), so it costs real money and must be counted.
    Exempting it would leave a loophole that spends without accounting."""
    from openlocalweather.spend import read_ledger

    deps = make_deps(tmp_path)
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=True)
    assert len(read_ledger(tmp_path)) == 1


def test_a_failed_sun_lookup_still_tells_the_model_the_time(tmp_path, monkeypatch):
    """The clock is not the sun.

    now_in_tz reads the system clock and cannot fail over the network;
    sunrise and sunset can. An earlier version put both in one try block, so a
    failed astronomical lookup made the prompt say "time of day unavailable"
    for a run that knew perfectly well it was 18:15. Knowing the time is most
    of the value here — knowing where the sun is only refines it.
    """
    monkeypatch.setattr(
        open_meteo, "fetch_sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "ISSUED:" in user_prompt
    assert "It is " in user_prompt, "the local time survives a failed sun lookup"
    assert "part of day as unknown" in user_prompt, (
        "and the phase is declared unknown rather than guessed from the clock"
    )


def test_a_failed_sun_lookup_does_not_also_lose_the_hours_ahead(tmp_path, monkeypatch):
    """They are separate fetches and fail separately. Sharing one try block
    meant an astronomical lookup could silently cost the forward window —
    which is the more useful of the two."""
    monkeypatch.setattr(
        open_meteo, "fetch_sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "HOURS AHEAD" in user_prompt
    assert "Unavailable this run." not in user_prompt.split("HOURS AHEAD")[1][:60]


def test_a_failed_forward_window_does_not_lose_the_sun_or_the_time(tmp_path, monkeypatch):
    """The mirror of the above. Neither failure should take the other down."""
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "sunset 18:47" in user_prompt, "the sun times are unaffected"
    assert "TODAY'S MULTI-MODEL GUIDANCE" in user_prompt, "the calendar day still stands"


def test_neither_failure_stops_a_forecast_being_produced(tmp_path, monkeypatch):
    """A forecast without sun times is worse. A forecast that does not happen
    because an astronomical lookup failed is much worse."""
    for name in ("fetch_sun_times", "fetch_forecast_hourly_forward"):
        monkeypatch.setattr(
            open_meteo, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        )
    llm = FakeLLMProvider()
    result = run_daily_pipeline(
        make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True
    )
    assert result.log_entry is not None
    assert len(llm.calls) == 1


def test_sunrise_and_sunset_are_stored_from_code_not_the_narrative(tmp_path):
    """Facts, not prose. Asking a language model to restate a computed time is
    how a wrong one gets published — and these are checkable by anyone who
    looks out of a window, so being wrong is expensive."""
    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=True)
    assert result.log_entry.sunrise == "06:40"
    assert result.log_entry.sunset == "18:47"


def test_missing_sun_times_are_stored_as_absent_not_as_an_empty_clock(tmp_path, monkeypatch):
    """Polar night has no sunrise. An empty string would render as a blank
    value next to the label, which reads as a broken page rather than as the
    correct answer."""
    monkeypatch.setattr(
        open_meteo, "fetch_sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=True)
    assert result.log_entry.sunrise is None
    assert result.log_entry.sunset is None


def test_three_issuances_are_all_recoverable_from_the_stored_entry(tmp_path):
    """Storage used to keep the FIRST issuance and the LATEST, not the ones
    between: morning_issuance was written only if not already set, so run 3
    preserved run 1 and overwrote run 2 with nothing left to recover it
    from. With two runs a day nothing was lost; with three or more, the
    middle ones were gone from the committed record. See ROADMAP item 32.

    Now every issuance before the current one is kept, oldest first, in
    earlier_issuances — the current one stays where it always has, at the
    top level. morning_issuance still tracks the first, unchanged.
    """
    deps1 = make_deps(tmp_path, llm=FakeLLMProvider())
    run_daily_pipeline(deps1, today=date(2026, 8, 11), dry_run=False)

    second = FakeLLMProvider()
    second.response = second.response.model_copy(
        update={"today_narrative": "## Overview\nSECOND issuance."}
    )
    run_refresh_pipeline(make_deps(tmp_path, llm=second), today=date(2026, 8, 11), dry_run=False)

    third = FakeLLMProvider()
    third.response = third.response.model_copy(
        update={"today_narrative": "## Overview\nTHIRD issuance."}
    )
    run_refresh_pipeline(make_deps(tmp_path, llm=third), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert "THIRD" in entry.narrative_markdown, "the latest stays at the top level"
    assert len(entry.earlier_issuances) == 2
    assert "Dry and warm" in entry.earlier_issuances[0].narrative_markdown, "first, oldest first"
    assert "SECOND" in entry.earlier_issuances[1].narrative_markdown, "second, no longer lost"
    assert entry.morning_issuance is not None
    assert "Dry and warm" in entry.morning_issuance.narrative_markdown, "still the first"

    log = entry.issuance_log()
    assert [i.narrative_markdown for i in log] == [
        entry.earlier_issuances[0].narrative_markdown,
        entry.earlier_issuances[1].narrative_markdown,
        entry.narrative_markdown,
    ]


def test_a_later_issuance_never_changes_what_gets_scored(tmp_path):
    """The accuracy record measures MODELS, not issuances.

    A later run has a fresher model cycle and less lead time, so scoring it
    would flatter every model. The day's predictions belong to the first run
    and must survive any number of re-issues untouched — otherwise adding a
    midday run would silently improve the published accuracy figures.
    """
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    before = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).model_predictions

    for _ in range(3):
        run_refresh_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    after = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).model_predictions
    assert after == before, "three re-issues must leave the scored numbers identical"


def test_run_daily_a_SECOND_time_KEEPS_the_days_predictions(tmp_path, monkeypatch):
    """Why a second full run cannot be allowed to re-extract them.

    run-daily stores the day's model_predictions. Running it again in the
    evening re-extracts them from evening-cycle data — predictions made with
    ~12 hours less lead time, then scored tomorrow as though they were the
    06:00 call. Every model's Day+0 accuracy would improve, and nothing in
    the record would show why.

    The guards that used to prevent this both live OUTSIDE the pipeline: a
    YAML already_done condition and a cron line on a machine this repo cannot
    see or test, neither of which survives someone ticking force on a manual
    dispatch. The trigger is untrusted input — a caller must be able to
    invoke this with any combination of flags and be unable to corrupt the
    record.
    """
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    first = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).model_predictions

    # A genuinely different cycle. With the shared fixture a rewrite and a
    # refusal look identical, which is why the old version of this test could
    # only document the hazard rather than catch it.
    def evening_cycle_hourly(*args, **kwargs):
        fixture = hourly_fixture()
        for model in MODELS:
            fixture["hourly"][f"temperature_2m_{model}"] = [19.0, 23.0, 31.0]
        return fixture

    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today", evening_cycle_hourly)
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    second = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).model_predictions

    assert second == first, "a second run rewrote the numbers tomorrow scores"


def test_a_second_run_still_writes_a_fresh_narrative(tmp_path):
    """The other half of the rule: force forces the NARRATIVE, and can never
    reach the scored numbers. A guard that also froze the prose would make a
    forced re-run pointless."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    evening = FakeLLMProvider()
    evening.response = evening.response.model_copy(
        update={"today_narrative": "## Overview\nStorms arrived after all."}
    )
    run_daily_pipeline(
        make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False
    )

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.narrative_markdown == "## Overview\nStorms arrived after all."


def test_a_second_run_describes_the_numbers_the_record_holds(tmp_path, monkeypatch):
    """A run whose predictions are kept must be told the KEPT ones. Handing it
    the fresher extraction would leave the narrative quoting values the record
    does not contain — the same reason the refresh path passes the stored
    predictions rather than re-deriving them."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    def evening_cycle_hourly(*args, **kwargs):
        fixture = hourly_fixture()
        for model in MODELS:
            fixture["hourly"][f"temperature_2m_{model}"] = [19.0, 23.0, 31.0]
        return fixture

    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today", evening_cycle_hourly)
    evening = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = evening.calls[-1]
    block = predictions_block(user_prompt)
    assert '"high_c": 26.0' in block, "the morning's Day+0 call is what got stored"
    assert "31.0" not in block, "the narrative was shown a prediction the record does not hold"


def _forced_rerun(tmp_path, narrative, after_refresh: bool):
    """A day that has already been forecast, then run-daily again — the shape
    `force: true` on a manual dispatch produces."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    if after_refresh:
        evening = FakeLLMProvider()
        evening.response = evening.response.model_copy(
            update={"today_narrative": "## Overview\nEvening refresh."}
        )
        run_refresh_pipeline(make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False)

    forced = FakeLLMProvider()
    forced.response = forced.response.model_copy(update={"today_narrative": narrative})
    deps = make_deps(tmp_path, llm=forced)
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)
    return log_store.read_log_entry(tmp_path, date(2026, 8, 11)), forced


def test_a_forced_re_run_keeps_the_days_history(tmp_path):
    """The scored numbers already survive a forced re-run; the day's own
    history did not.

    A second run-daily built a brand-new entry, which wiped morning_issuance
    — the only copy of what was published this morning — reset
    generated_at_utc to the evening, so the entry claimed to have been created
    at a time it was not, and cleared refreshed_at, which re-opened
    evening_refresh's gate so the NEXT refresh would snapshot the forced
    narrative as that day's morning issuance.
    """
    entry, _ = _forced_rerun(tmp_path, "## Overview\nForced re-run.", after_refresh=True)

    assert entry.morning_issuance is not None
    assert entry.morning_issuance.narrative_markdown == "## Overview\nDry and warm.", (
        "the morning issuance must stay the MORNING's, not the last run's"
    )
    assert entry.meta.generated_at_utc == entry.morning_issuance.generated_at_utc, (
        "generated_at_utc records when this entry first existed"
    )
    assert entry.meta.refreshed_at is not None, (
        "a later run IS a narrative refresh; clearing this re-opens the evening gate"
    )
    assert entry.narrative_markdown == "## Overview\nForced re-run."


def test_a_forced_re_run_snapshots_a_morning_that_was_never_refreshed(tmp_path):
    """The same hazard without an evening refresh in between: run-daily twice
    and the morning's narrative is the one being overwritten."""
    entry, _ = _forced_rerun(tmp_path, "## Overview\nSecond run.", after_refresh=False)

    assert entry.morning_issuance is not None
    assert entry.morning_issuance.narrative_markdown == "## Overview\nDry and warm."


def test_a_forced_re_run_is_told_it_is_a_later_issuance(tmp_path):
    """Otherwise it writes a fresh morning-style forecast over one its readers
    have already read, and emails it as though it were the day's first."""
    _, forced = _forced_rerun(tmp_path, "## Overview\nForced re-run.", after_refresh=True)

    system_prompt, user_prompt = forced.calls[-1]
    assert "LATER ISSUANCE" in system_prompt
    assert "Evening refresh." in user_prompt, "shown what has already been published"


def test_refresh_without_a_prior_run_fails_loudly(tmp_path):
    """The current safety net. If the morning run never happened, the evening
    refresh does not quietly become the day's first forecast — which would
    mean model_predictions came from evening-cycle data."""
    with pytest.raises(RefreshWithoutMorningRunError):
        run_refresh_pipeline(make_deps(tmp_path), today=date(2026, 8, 11))


def test_a_refresh_keeps_the_sun_times(tmp_path):
    """They were only set on the day's first run, so any refreshed day
    carried nulls from that point — the site would simply stop showing sun
    times after the evening run, with nothing to flag it."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    run_refresh_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.sunrise == "06:40"
    assert entry.sunset == "18:47"


def test_a_refresh_with_no_sun_data_keeps_the_mornings(tmp_path, monkeypatch):
    """A failed sun fetch on a re-issue must not erase a good value the first
    run captured. Fresher is not automatically better when the fresher value
    is 'unknown'."""
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    monkeypatch.setattr(
        open_meteo, "fetch_sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    run_refresh_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.sunrise == "06:40", "the morning's value survives a failed re-fetch"


# ---------------------------------------------------------------------------
# The Overview's convective flag, the last-known AQI reading, and observed
# thunder all reach the places that use them. Each of these defaults to None,
# so a wiring mistake would degrade silently into "Unavailable" forever
# rather than failing — which is exactly the failure mode these assert away.
# ---------------------------------------------------------------------------


def forward_hourly_from_now(**extra_series):
    """Forward guidance anchored to the real clock.

    daypart.forward_hours trims against wall-clock now, not against `today`,
    so a fixture pinned to 2026-08-11 trims to nothing and every instability
    assertion below would pass by testing the empty path.
    """
    start = now_in_tz(LOCATION.timezone).replace(minute=0, second=0, microsecond=0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(48)]
    series = {name: values(times) for name, values in extra_series.items()}
    return {
        "hourly": {
            "time": times,
            "precipitation_gfs_seamless": [0.0] * len(times),
            **series,
        }
    }


def convective_forward_hourly():
    # A single convective afternoon spike, well above the threshold, placed
    # a few hours ahead so it always lands inside the forward window.
    def cape(times):
        return [2400.0 if i == 3 else 50.0 for i in range(len(times))]

    return forward_hourly_from_now(cape_gfs_seamless=cape)


def test_user_prompt_carries_the_convective_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: convective_forward_hourly()
    )
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert "CONVECTIVE INSTABILITY" in user_prompt
    assert '"convective": true' in user_prompt
    assert "2400.0" in user_prompt


def test_user_prompt_says_when_there_is_no_instability_data(tmp_path, monkeypatch):
    # Hours ahead are present; a CAPE series is not. That gap must read as a
    # gap rather than as a calm afternoon.
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: forward_hourly_from_now()
    )
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert "no model supplied a CAPE series" in user_prompt


def test_quiet_cape_does_not_set_the_convective_flag(tmp_path, monkeypatch):
    def calm(times):
        return [120.0] * len(times)

    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        lambda *a, **k: forward_hourly_from_now(cape_gfs_seamless=calm),
    )
    llm = FakeLLMProvider()
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert '"convective": false' in user_prompt


def test_user_prompt_quotes_the_last_known_aqi_when_all_stale(tmp_path, monkeypatch):
    from openlocalweather.config import WaqiStation

    stale_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="Kisumu Airport", station_id="A1", aqi=63, measured_at=stale_at),
            GroundAQIReading(name="Dunga Beach", station_id="A2", aqi=49, measured_at=stale_at),
        ],
    )
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(
        update={
            "waqi_stations": [
                WaqiStation(name="Kisumu Airport", station_id="A1"),
                WaqiStation(name="Dunga Beach", station_id="A2"),
            ]
        }
    )
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert "GROUND AQI LAST KNOWN" in user_prompt
    # The worst station at the newest timestamp, with its age attached.
    assert '"station_name": "Kisumu Airport"' in user_prompt
    assert '"aqi": 63' in user_prompt
    assert '"stale": true' in user_prompt
    assert '"stations_reporting": 2' in user_prompt


def test_observed_thunder_reaches_the_stored_actuals(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_thunder_by_date",
        lambda icao, start, end, tz: {d: True for d in (start, end)},
    )
    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    run_daily_pipeline(deps, today=date(2026, 8, 11), dry_run=False)

    cache = actuals_cache_store.read_actuals_cache(tmp_path)
    stored = actuals_cache_store.as_date_dict(cache.primary)
    assert stored, "no actuals were written"
    assert all(a.thunder is True for a in stored.values())


# ---------------------------------------------------------------------------
# olw forecast — one verb that reads the day and dispatches
# ---------------------------------------------------------------------------


def test_forecast_runs_the_full_pipeline_when_the_day_is_empty(tmp_path):
    result = pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11))

    assert isinstance(result, pipeline.PipelineRunResult)
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.model_predictions.day0, "the day's first run owns the predictions"
    assert entry.meta.refreshed_at is None


def test_forecast_re_issues_when_the_day_already_has_an_entry(tmp_path):
    """The real distinction has nothing to do with the clock: the first run of
    a day owns verification and the scored numbers, every later run is an
    update. An operator picking a verb by time of day is the last place the
    morning/evening split survives."""
    pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11))
    before = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    evening = FakeLLMProvider()
    evening.response = evening.response.model_copy(
        update={"today_narrative": "## Overview\nEvening update."}
    )
    result = pipeline.run_forecast(
        make_deps(tmp_path, llm=evening),
        today=date(2026, 8, 11),
        now=before.meta.generated_at_utc + timedelta(hours=12),
    )

    assert isinstance(result, pipeline.RefreshRunResult)
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.narrative_markdown == "## Overview\nEvening update."
    assert entry.model_predictions == before.model_predictions
    assert entry.meta.refreshed_at is not None


def test_forecast_skips_a_trigger_that_repeats_one_just_run(tmp_path):
    """The backup schedule slots are +15/+30/+45 minutes behind the primary,
    and a crontab may dispatch the same workflow the same minute. The guard
    lives HERE rather than in a YAML condition, because YAML on a machine this
    repo cannot test is exactly what item 34a was about.
    """
    pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11))
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    llm = FakeLLMProvider()
    result = pipeline.run_forecast(
        make_deps(tmp_path, llm=llm),
        today=date(2026, 8, 11),
        now=entry.meta.generated_at_utc + timedelta(minutes=45),
    )

    assert isinstance(result, pipeline.ForecastSkipped)
    assert llm.calls == [], "a repeat trigger must not reach the model"


def test_forecast_force_overrides_the_skip_but_not_the_predictions(tmp_path):
    """force forces the NARRATIVE. After 34a it cannot reach the scored
    numbers whatever it does."""
    pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11))
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    forced = FakeLLMProvider()
    forced.response = forced.response.model_copy(
        update={"today_narrative": "## Overview\nForced."}
    )
    result = pipeline.run_forecast(
        make_deps(tmp_path, llm=forced),
        today=date(2026, 8, 11),
        now=entry.meta.generated_at_utc + timedelta(minutes=5),
        force=True,
    )

    assert isinstance(result, pipeline.RefreshRunResult)
    after = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert after.narrative_markdown == "## Overview\nForced."
    assert after.model_predictions == entry.model_predictions


def test_forecast_dry_run_writes_nothing(tmp_path):
    pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=True)

    assert log_store.read_log_entry(tmp_path, date(2026, 8, 11)) is None


# ---------------------------------------------------------------------------
# Guidance recency — observed (fetch/model_run.py) vs derived
# (cycle.aligned_cycle_at), and the settle rule between them. See
# pipeline.py's guidance-cycle resolution and cycle.py's module docstring.
# ---------------------------------------------------------------------------


def test_a_settled_observation_is_recorded_as_observed(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    observed_initialised = now - timedelta(hours=9)
    observed_available = now - timedelta(minutes=30)  # well past RUN_SETTLE_MINUTES
    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=observed_initialised, available_at=observed_available
        ),
    )

    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = result.log_entry

    assert entry.guidance_source == "observed"
    assert entry.guidance_initialised_at == observed_initialised
    assert entry.guidance_age_hours == pytest.approx(
        (now - observed_initialised).total_seconds() / 3600, abs=0.05
    )


def test_an_unsettled_observation_is_ignored_and_derived_is_used(tmp_path, monkeypatch):
    """Open-Meteo recommends waiting ~10 minutes after a run becomes
    available before relying on it (its servers are eventually consistent).
    A run that became available seconds ago fails that check, so the
    observation must be discarded in favour of the conservative derived
    floor — not merely "used but noted as fresh"."""
    from openlocalweather.cycle import aligned_cycle_at

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=now - timedelta(hours=1), available_at=now - timedelta(seconds=5)
        ),
    )

    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = result.log_entry
    expected = aligned_cycle_at(now).initialised_at

    assert entry.guidance_source == "derived"
    assert entry.guidance_initialised_at == expected


def test_a_metadata_fetch_returning_none_costs_the_run_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(model_run_fetch, "fetch_model_run", lambda model: None)

    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = result.log_entry

    assert entry.guidance_source == "derived"
    assert entry.guidance_initialised_at is not None
    assert entry.guidance_age_hours is not None


def test_a_metadata_fetch_that_raises_costs_the_run_nothing(tmp_path, monkeypatch):
    """Not a mock of fetch_model_run itself — this drives the REAL driver
    (fetch/model_run.py) through the pipeline with only requests.get made to
    raise, proving the driver's own try/except (not a pipeline-side guard)
    is what keeps a network failure here from costing the run anything."""

    def _raise(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", _raise)

    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = result.log_entry

    assert entry.guidance_source == "derived"
    assert entry.guidance_initialised_at is not None


def test_disagreement_between_observed_and_derived_warns_and_keeps_observed(tmp_path, monkeypatch, capsys):
    """The rot detector: the derived table is a hand-measured snapshot, and
    nothing else would ever tell us it had drifted. A disagreement must be
    surfaced, not silently resolved — but the run still uses the
    observation, since it is the more trustworthy of the two answers."""
    from openlocalweather.cycle import aligned_cycle_at

    now = datetime.now(timezone.utc)
    derived_initialised = aligned_cycle_at(now).initialised_at
    observed_initialised = derived_initialised - timedelta(hours=6)  # deliberately different cycle
    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=observed_initialised, available_at=now - timedelta(minutes=30)
        ),
    )

    result = run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "ROADMAP.md" in captured.err
    assert observed_initialised.isoformat() in captured.err
    assert derived_initialised.isoformat() in captured.err
    assert result.log_entry.guidance_source == "observed"
    assert result.log_entry.guidance_initialised_at == observed_initialised


def test_a_reissue_archives_the_first_issuances_guidance_recency(tmp_path, monkeypatch):
    """The whole point of storing this per-issuance: a re-issue must not
    let the evening's fresher cycle overwrite the morning's, since the
    morning issuance is what actually went out at 6 AM."""
    now = datetime.now(timezone.utc)
    morning_initialised = now - timedelta(hours=10)
    evening_initialised = now - timedelta(hours=4)

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=morning_initialised, available_at=now - timedelta(minutes=30)
        ),
    )
    run_daily_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=evening_initialised, available_at=now - timedelta(minutes=30)
        ),
    )
    refresh_result = run_refresh_pipeline(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = refresh_result.log_entry

    assert entry.guidance_source == "observed"
    assert entry.guidance_initialised_at == evening_initialised
    assert len(entry.earlier_issuances) == 1
    assert entry.earlier_issuances[0].guidance_source == "observed"
    assert entry.earlier_issuances[0].guidance_initialised_at == morning_initialised
