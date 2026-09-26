from datetime import date, datetime, time, timedelta, timezone

import pytest

from openlocalweather.config import LocationConfig, Point, RegionPoint, SecondaryPoint, WaqiStation
from openlocalweather import dates as dates_module
from openlocalweather.dates import now_in_tz
from openlocalweather.defaults import BASELINE_MODEL_IDS, CODE_BLEND_MODEL_ID, MODELS, BLEND_MODEL_ID
from openlocalweather.claims import CLAIM_DISPLAY_TOO_LONG
from openlocalweather.disagreement import DISAGREEMENT_RAIN_WHILE_DRY
from openlocalweather.llm.gemini import LLMResponseError
from openlocalweather.llm.schema import (
    GeminiJudgmentResponse,
    GeminiNarrativeResponse,
)
from openlocalweather.models import (
    DEGRADATION_COMPOSED_PHRASE,
    DEGRADATION_EXTENDED_OUTLOOK,
    DEGRADATION_NARRATIVE,
    DEGRADATION_SECONDARY_EXTENDED_OUTLOOK,
)
from openlocalweather.fetch.metar import StationReadings, StationWeather
from openlocalweather.fetch import metar as metar_fetch
import requests

from openlocalweather.fetch import model_run as model_run_fetch
from openlocalweather.fetch import open_meteo
from openlocalweather.fetch import waqi as waqi_fetch
from openlocalweather.fetch.bulletin import NullBulletinFetcher
from openlocalweather.llm.provider import ResponseMeta
from openlocalweather.llm.schema import GeminiForecastResponse, TodayProperties
from openlocalweather.models import (
    DailyLogEntry,
    DailyActual,
    GroundAQIReading,
    IssuanceSnapshot,
    LogEntryMeta,
    ModelPredictionsByLead,
)
from openlocalweather import pipeline
from openlocalweather import solar
from openlocalweather.solar import SunTimes, sun_times as real_sun_times
from openlocalweather.pipeline import PipelineDeps
from openlocalweather.store import actuals_cache as actuals_cache_store
from openlocalweather.store import log_store
from openlocalweather.store import track_record as track_record_store
from openlocalweather.verify.scoring import mean, scored_predictions

LOCATION = LocationConfig(
    region_name="Test Region",
    primary_place_name="Test Town",
    timezone="UTC",
    primary_point=Point(lat=1.0, lon=2.0),
    secondary_point=SecondaryPoint(),  # disabled — keeps fixtures simpler
    region_points=[RegionPoint(name="Neighbor", lat=1.5, lon=2.5)],
    metar_station_icao="",  # skip METAR
    # A STATION IS CONFIGURED so the driver actually reaches the ground-AQI
    # blocks. It carried none until 2026-09-22, which meant
    # `ground_stations_configured` was False and the whole air-quality
    # apparatus — the summary, the last-known block and their rule — was
    # omitted from every driven prompt. A change to any of them diffed clean
    # against a control and proved nothing. The fetch is still stubbed.
    waqi_stations=[WaqiStation(name="Kisumu Airport", station_id="A418534")],
    local_bulletin_url="",  # NullBulletinFetcher
)


def hourly_fixture() -> dict:
    """The PRIMARY block: one whole local day, 00:00 to 23:00.

    WHY IT IS A WHOLE DAY. The real call is `forecast_days=1` and returns 24
    rows; this carried three (00:00, 06:00, 12:00) until 2026-09-21, which
    meant every anchor-sampling composer — the wind shift, and the tile
    anchors at 03:00, 12:00 and 18:00 — found nothing here and returned empty
    through the whole suite AND through the driver. The tile work shipped
    against a fixture that could not exercise it. Widening it to 24 hours
    broke no test, which says how little was resting on the three.

    WIND AND CLOUD UNDER THE FORECAST ENDPOINT'S OWN NAMES. `open_meteo.py`
    asks that endpoint for `wind_speed_10m`, `wind_gusts_10m` (underscored,
    and the NOTE there says why), `wind_direction_10m` and `cloud_cover`. The
    legacy `windgusts_10m` below is kept because parts of this suite were
    written against it; the real forecast response does not carry that
    spelling, so a test that depends on it is testing a shape the API does not
    return. Not chased here.

    THE DAY HAS A SHAPE: clear morning building to overcast under afternoon
    convection, wind backing from southwest to south and easing after its
    late-afternoon peak. A flat day cannot tell a working composer from one
    that reads the wrong hour.
    """
    cloud = [5] * 6 + [10, 20, 30, 40, 55, 60] + [70, 80, 95, 100, 100, 100] + [100, 80, 60, 40, 20, 10]
    speed = [8.0] * 6 + [12.0] * 6 + [22.0] * 6 + [18.0] * 6
    bearing = [225] * 12 + [200] * 6 + [190] * 6

    fields: dict[str, list] = {"time": [f"2026-08-11T{h:02d}:00" for h in range(24)]}
    for model in MODELS:
        fields[f"precipitation_{model}"] = [0.0] * 24
        fields[f"windgusts_10m_{model}"] = [10.0 + h / 4 for h in range(24)]
        fields[f"wind_gusts_10m_{model}"] = [s * 1.6 for s in speed]
        fields[f"wind_speed_10m_{model}"] = list(speed)
        fields[f"wind_direction_10m_{model}"] = list(bearing)
        fields[f"cloud_cover_{model}"] = list(cloud)
        fields[f"temperature_2m_{model}"] = [18.0 + h / 3 for h in range(24)]
        fields[f"pressure_msl_{model}"] = [1012.0 - h * 0.1 for h in range(24)]
    return {"hourly": fields}


def daily_fixture() -> dict:
    fields: dict[str, list] = {}
    for model in MODELS:
        fields[f"precipitation_sum_{model}"] = [0.0] * 8
        fields[f"windgusts_10m_max_{model}"] = [15.0] * 8
        fields[f"temperature_2m_max_{model}"] = [27.0] * 8
        fields[f"temperature_2m_min_{model}"] = [18.0] * 8
        fields[f"pressure_msl_mean_{model}"] = [1010.0] * 8
        # TODAY AND TOMORROW DIFFER, so a test can tell which day the horizon
        # pointed at — item 161. Only gfs and best_match serve a UV index in
        # the real feed; the others are null here for the same reason.
        fields[f"uv_index_max_{model}"] = (
            [9.1, 7.4] + [8.0] * 6
            if model in ("gfs_seamless", "best_match")
            else [None] * 8
        )
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
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain=False,
                rain_expected="Unlikely",
                # ROADMAP item 144. BOTH POINTS, AND THEY DIFFER — the real
                # pair from 2026-09-16, the day the ashore section published
                # the Gulf's 41. The fixture carried no gust at all before,
                # which meant every assertion about the scored wind passed on
                # a null and would have passed with the wiring deleted. This
                # is also the model stub `tools/drive_forecast_cli.py` drives,
                # so the harness could not exercise the split either.
                peak_wind_primary_kmh=32.8,
                peak_wind_secondary_kmh=41.0,
                temp_high_c=27.0,
                temp_low_c=18.0,
                temp_high_low="27°C / 81°F",
                # ROADMAP item 159 step 4, and the same fault as the wind
                # pair above: the stub supplied NEITHER of these, so every
                # assertion about them passed on a null and would have passed
                # with the composition deleted. Both are plain numbers now —
                # the band word is code's — and these two are the real
                # blended values from 2026-09-21, which land one band short of
                # their own ceilings: 9.1 is "Very high" and not "Extreme",
                # 85 is "Moderate" and not "Unhealthy for sensitive groups".
                uv_index_max=9.1,
                air_quality_aqi=85,
            ),
            today_narrative="## Overview\nDry and warm.",
            whatsapp_summary=None,
        )

    # Real providers call this before EVERY request, retries included, and
    # that is how the spend cap counts. A stub that skips it silently exempts
    # every pipeline test from the cap — which is how the undercounting bug
    # survived: the tests could not see the seam they were meant to cover.
    before_attempt = None

    # Same reasoning as before_attempt, for the other seam. Real providers
    # report how the call ended once the body parses, and the pipeline stores
    # it on the entry — ROADMAP item 100. A stub that stayed silent would
    # leave every pipeline test blind to that field, so this reports the
    # shape a real one does.
    after_response = None
    finish_reason = "STOP"
    input_tokens = 41_000
    output_tokens = 2_100
    # The other half of what a real provider reports — ROADMAP items 59/102.
    # Same reasoning as the three above: a stub that stayed silent would make
    # every pipeline test blind to the field.
    response_schema_sha256 = "a" * 64
    nullable_fields = ("/today_properties/mslp_trend_24h",)
    # Which half of the split to fail, for ROADMAP item 59 step 3's
    # degraded-write-up path. None means answer normally.
    fail_judgment: Exception | None = None
    fail_narrative: Exception | None = None

    @property
    def system_prompts(self) -> str:
        """Every system prompt this run sent, joined.

        A forecast is two calls since ROADMAP item 59 step 3, and most tests
        here ask whether the forecaster was TOLD something — not which of the
        two calls told it. Which call carries a rule is
        tests/test_prompt_seam.py's question.
        """
        return "\n".join(system for system, _user in self.calls)

    @property
    def user_prompts(self) -> str:
        """Every user prompt this run sent, joined. The narrative call's is
        the judgment call's with THE FORECASTER'S CALL appended."""
        return "\n".join(user for _system, user in self.calls)

    def generate(self, system_prompt, user_prompt, response_schema):
        if self.before_attempt is not None:
            self.before_attempt()
        self.calls.append((system_prompt, user_prompt))
        if self.fail_judgment is not None and response_schema is GeminiJudgmentResponse:
            raise self.fail_judgment
        if self.fail_narrative is not None and response_schema is GeminiNarrativeResponse:
            raise self.fail_narrative
        if self.after_response is not None:
            self.after_response(
                ResponseMeta(
                    finish_reason=self.finish_reason,
                    input_tokens=self.input_tokens,
                    output_tokens=self.output_tokens,
                    response_schema_sha256=self.response_schema_sha256,
                    nullable_fields=self.nullable_fields,
                )
            )
        # Return the SHAPE that was asked for. The canned response is the
        # merged GeminiForecastResponse, a superset of both halves of the
        # split, so each call gets exactly the fields its own schema declares
        # — which is what a real provider does, and what makes these tests
        # exercise the split rather than route around it.
        return response_schema.model_validate(self.response.model_dump())


def sun_fixture(lat, lon, day, utc_offset_seconds):
    """A fixed, known pair of sun times, on whatever date is asked for.

    Pinned to the DATE REQUESTED rather than to 2026-08-11, because the
    pipeline asks for the date it is actually running on — `now_local.date()`,
    the real clock — while these tests pass `today=2026-08-11` for the
    forecast date. The old fixture answered 2026-08-11 to every question, so
    every daypart in this suite was computed with a sunset seventeen days in
    the past and came out "night" whatever time the suite ran.

    The values are Kisumu's for 2026-08-22, not this LOCATION's — LOCATION
    sits at 1N 2E in UTC. They are here because 18:47 is the number that
    started all of this: the real "evening" run fires 32 minutes before it.
    """
    return SunTimes(
        datetime.combine(day, time(6, 40)),
        datetime.combine(day, time(18, 47)),
    )


def forward_hourly_fixture():
    """Two days of hourly data, so a run at any hour has hours still ahead.

    CARRIES EVERY VARIABLE `hourly_fixture` DOES, for all five models, since
    ROADMAP item 104's contract item 2. It used to hold precipitation for one
    model only — enough for the narrative's forward window, which is all it
    fed. The issuance window is extracted from this series and would have come
    back with a rain call and null temperatures, which is a shape no
    assertion would have noticed and the driver's own output did not.

    The temperature ramps across the two days rather than repeating, so a
    window opening at 06:00 and one opening at 18:00 have different highs and
    a test can tell them apart.
    """
    times = []
    fields: dict[str, list] = {}
    for i in range(48):
        day = 11 + i // 24
        times.append(f"2026-08-{day:02d}T{i % 24:02d}:00")
    fields["time"] = times
    for model in MODELS:
        fields[f"precipitation_{model}"] = [0.0] * 48
        fields[f"windgusts_10m_{model}"] = [10.0 + (i % 24) / 4 for i in range(48)]
        fields[f"temperature_2m_{model}"] = [16.0 + (i % 24) * 0.5 + (i // 24) for i in range(48)]
        fields[f"pressure_msl_{model}"] = [1012.0 - i * 0.05 for i in range(48)]
    return {"hourly": fields}


@pytest.fixture(autouse=True)
def patch_fetches(monkeypatch):
    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today", lambda *a, **k: hourly_fixture())
    monkeypatch.setattr(open_meteo, "fetch_forecast_daily_extended", lambda *a, **k: daily_fixture())
    monkeypatch.setattr(open_meteo, "fetch_regional_pressure", lambda *a, **k: {"daily": {}})
    monkeypatch.setattr(open_meteo, "fetch_synoptic_pressure", lambda *a, **k: {"points": []})
    monkeypatch.setattr(open_meteo, "fetch_air_quality", lambda *a, **k: {"hourly": {}})
    # Time-of-day context. The sun is computed, not fetched, so nothing here
    # can fail — it is pinned so that assertions on "sunset 18:47" keep
    # meaning something as the calendar moves. See
    # test_the_computed_sun_times_reach_the_prompt_and_the_entry for the one
    # that runs the real thing.
    monkeypatch.setattr(solar, "sun_times", sun_fixture)
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


def issue(deps, today=None, dry_run=False):
    """One issuance of the day's forecast, whichever of the day it is.

    What `run_daily_pipeline` and `run_refresh_pipeline` did when a test
    called them directly, before item 104 step 4 collapsed them. `force`
    because a direct call never went through the repeat-trigger interval
    guard that `run_forecast` applies — the tests that are ABOUT that guard
    call `run_forecast` themselves.
    """
    return pipeline.run_forecast(deps, today=today, dry_run=dry_run, force=True)


def refresh(deps, today, hours_later=2):
    """A LATER trigger that forces nothing — the hourly-cron case.

    `issue` passes force, which since ROADMAP item 121 also overrides the
    reasoning gate. A test about that gate has to arrive the way cron does:
    past the repeat interval, with nothing overridden.
    """
    stored = log_store.read_log_entry(deps.data_dir, today)
    return pipeline.run_forecast(
        deps,
        today=today,
        dry_run=False,
        now=stored.last_issued_at + timedelta(hours=hours_later),
    )


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


def table_column(block: str, column: str) -> list[str]:
    """Every value in one column of a tab-separated block — ROADMAP item 176.

    Worth a helper rather than a substring: in a table `31.0` on its own can
    match any column of the row, so an assertion written that way would pass
    for the right number in the wrong place. This reads the header, finds the
    column's index, and returns that cell from each row.
    """
    lines = [l for l in block.splitlines() if "\t" in l]
    header = lines[0].split("\t")
    i = header.index(column)
    return [l.split("\t")[i] for l in lines[1:]]


def test_dry_run_does_not_write_any_files(tmp_path):
    deps = make_deps(tmp_path)
    result = issue(deps, today=date(2026, 8, 11), dry_run=True)

    assert result.log_entry.rain_expected == "Unlikely"
    assert log_store.read_log_entry(tmp_path, date(2026, 8, 11)) is None
    assert not (tmp_path / "track_record.json").exists()
    assert not (tmp_path / "actuals_cache" / "actuals.json").exists()
    assert result.published is False
    assert result.emailed is False


def test_real_run_writes_log_entry_and_track_record(tmp_path):
    deps = make_deps(tmp_path)
    result = issue(deps, today=date(2026, 8, 11), dry_run=False)

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
    result = issue(deps, today=date(2026, 8, 11), dry_run=False)
    day0 = scored_predictions(result.log_entry).day0

    # Every extracted model, PLUS our own blended call — the forecast the
    # reader actually gets, scored as a peer of the guidance that fed it —
    # PLUS the two baselines a real model has to beat (item 57).
    assert {p.model for p in day0} == {*MODELS, BLEND_MODEL_ID, *BASELINE_MODEL_IDS}
    # No blend at the extended range: today_properties is a call about today
    # and there is no structured equivalent to score further out. The
    # baselines reach every lead, because a yardstick that stops at Day+0
    # cannot say whether the extended outlook is worth anything.
    assert {p.model for p in scored_predictions(result.log_entry).day3} == {
        *MODELS,
        *BASELINE_MODEL_IDS,
    }
    assert {p.model for p in scored_predictions(result.log_entry).day7} == {
        *MODELS,
        *BASELINE_MODEL_IDS,
    }


def test_the_blend_is_scored_on_what_it_committed_to(tmp_path):
    # Built from today_properties' structured fields, not parsed back out of
    # the prose. What gets scored is what the forecaster committed to.
    deps = make_deps(tmp_path)
    result = issue(deps, today=date(2026, 8, 11), dry_run=False)
    blend = next(
        p for p in scored_predictions(result.log_entry).day0 if p.model == BLEND_MODEL_ID
    )

    assert blend.high_c == result.log_entry.temp_high_c
    assert blend.low_c == result.log_entry.temp_low_c
    # ITEM 144: the PRIMARY point's gust is scored and the secondary's is not.
    # This asserted `is None` until 2026-09-16 with a reason the split made
    # false, and it passed because the fixture supplied no gust — so it would
    # have gone on passing with the wiring deleted.
    assert blend.wind_kmh == 32.8, "the ashore gust is what the record observes"
    assert blend.wind_kmh != 41.0, "the Gulf's gust must never reach the scored row"
    # mslp_trend_24h is prose, so it stays absent for the original reason.
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
    issue(deps, today=date(2026, 8, 11), dry_run=False)

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
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    llm = FakeLLMProvider()
    issue(
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
    result = issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert BLEND_MODEL_ID not in {p.model for p in scored_predictions(result.log_entry).day3}
    assert BLEND_MODEL_ID not in {p.model for p in scored_predictions(result.log_entry).day7}


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


def test_yesterdays_prediction_gets_verified(tmp_path):
    today = date(2026, 8, 11)
    yesterday = date(2026, 8, 10)
    _seed_yesterday_log_entry(tmp_path, yesterday)

    llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="Correct no-rain call.",
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain=False,
                rain_expected="Unlikely", temp_high_c=27.0, temp_low_c=18.0, temp_high_low="27°C / 81°F"
            ),
            today_narrative="## Overview\nDry.",
        )
    )
    deps = make_deps(tmp_path, llm=llm)
    result = issue(deps, today=today, dry_run=False)

    assert (yesterday, 0) in result.newly_verified

    patched = log_store.read_log_entry(tmp_path, yesterday)
    assert patched.verification.day0.verified is True
    # The NOTE is no longer written — ROADMAP item 147. `verified` is the fact
    # this test is about; the prose that used to accompany it was the learning
    # loop, and the loop moved to the review.
    assert patched.verification.day0.note is None


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

    result = issue(deps, today=date(2026, 8, 11), dry_run=False)

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

    result = issue(deps, today=date(2026, 8, 11), dry_run=True)
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
    issue(deps, today=monday, dry_run=False)

    # TWO range calls since ROADMAP item 104's contract item 2, and they are
    # different jobs: one is the weekly full re-fetch into the actuals cache,
    # the other is `_verify_recent_windows`, which needs two days at once
    # because a window straddles midnight and which never writes the cache.
    # Counted rather than loosened — if the cache path started re-fetching
    # twice this would be three.
    assert calls["range"] == 2
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
    issue(deps, today=tuesday, dry_run=False)

    # The CACHE path is the single-day upsert, which is what this test is
    # about. The one range call is `_verify_recent_windows` — contract item 2
    # — which never touches the cache; two would mean the cache had started
    # doing a full re-fetch on an ordinary day.
    assert calls["range"] == 1
    assert calls["single"] == 1


def test_llm_receives_system_and_user_prompt(tmp_path):
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=True)

    # Two calls since ROADMAP item 59 step 3: judgment, then narrative.
    assert len(llm.calls) == 2
    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts
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
    result = issue(deps, today=date(2026, 8, 11), dry_run=False)

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
    issue(deps, today=date(2026, 8, 11), dry_run=True)

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
    issue(deps, today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = llm.calls[0]
    assert '"stale": true' in user_prompt
    assert '"hours_old": 7.2' in user_prompt
    # A single stale-only station has nothing fresh to summarize a range
    # from — GROUND AQI SUMMARY must fall back to "Not applicable", not
    # present the stale 200 reading as if it were current.
    assert "Not applicable" in user_prompt


def test_no_stations_configured_says_nothing_about_ground_stations(tmp_path):
    """A fork that polls no stations must not be told about a source it does
    not have.

    BUILDS ITS OWN STATIONLESS CONFIG rather than relying on LOCATION having
    none. It used to say "LOCATION has waqi_stations=[]" and depend on that —
    so when the shared fixture gained a station on 2026-09-22, to make the
    driver reach the ground-AQI blocks at all, this test broke. The break was
    in the test: a check that depends on what a fixture happens to contain is
    a check that fires on the wrong thing.

    The blocks used to be rendered as "Unavailable — no ground station
    reported data today", which is a fetch failure being reported for
    stations that were never configured, every single day. Absent
    instructions beat instructions saying "ignore this": the model cannot
    mention what it was never told about, and CAMS is then simply the source.
    """
    llm = FakeLLMProvider()
    deps = PipelineDeps(
        location=LOCATION.model_copy(update={"waqi_stations": []}),
        data_dir=tmp_path,
        llm_provider=llm,
        public_webpage_url="https://example.org",
        bulletin_fetcher=NullBulletinFetcher(),
    )
    issue(deps, today=date(2026, 8, 11), dry_run=True)

    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts
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
    issue(deps, today=date(2026, 8, 11), dry_run=True)

    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts
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
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)

    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts
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
    issue(deps, today=date(2026, 8, 11), dry_run=True)

    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts
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
    issue(deps, today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = llm.calls[0]
    assert "LOCAL BULLETIN (Kenya Meteorological Department (KMD)):" in user_prompt
    assert "Bulletin unavailable this run" in user_prompt


# ---------------------------------------------------------------------------
# A LATER RUN OF THE SAME DAY. Named for order, not for the clock:
# `run_refresh_pipeline` was merged into `run_forecast` by item 104, and
# nothing in these fixtures depends on the hour.
# ---------------------------------------------------------------------------


# A run on a day with no entry IS the day's first issuance, at whatever hour
# it happens — item 104's contract, and the reason RefreshWithoutMorningRunError
# is gone rather than merely unused. There is no verb left that can ask for an
# evening-style run on an empty day. See
# test_forecast_runs_the_full_pipeline_when_the_day_is_empty.


def test_a_later_run_preserves_the_first_runs_model_predictions(tmp_path):
    # NAMED FOR FIRST AND LATER, not morning and evening — nothing in these
    # fixtures is clock-dependent, and the day does not have exactly two runs.
    # First, the day's first run.
    first_deps = make_deps(tmp_path)
    first_result = issue(first_deps, today=date(2026, 8, 11), dry_run=False)
    original_predictions = scored_predictions(first_result.log_entry)

    # Then a later run with DIFFERENT fresh model data.
    later_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a — refresh",
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain=False,
                rain_expected="Now raining", temp_high_c=25.0, temp_low_c=17.0, temp_high_low="25°C / 77°F"
            ),
            today_narrative="## Overview\nRain has moved in this evening.",
        )
    )
    refresh_deps = make_deps(tmp_path, llm=later_llm)
    refresh_result = issue(refresh_deps, today=date(2026, 8, 11), dry_run=False)

    # Narrative/properties changed...
    assert refresh_result.log_entry.rain_expected == "Now raining"
    assert refresh_result.log_entry.temp_high_c == 25.0
    assert "Rain has moved in" in refresh_result.log_entry.narrative_markdown
    # ...but model_predictions (what tomorrow's verification scores) did NOT.
    assert scored_predictions(refresh_result.log_entry) == original_predictions


def test_a_later_run_keeps_what_the_first_one_published(tmp_path):
    """Real bug fixed: the first run's narrative used to be silently gone the
    moment a later one landed — recoverable only from git history, not from
    anything the site or data file exposed.

    ASSERTED THROUGH `issuance_log()`, NOT A FIELD — ROADMAP item 137. The
    store moved from `morning_issuance` to `earlier_issuances` and this
    property did not; an assertion naming either one tests the storage rather
    than the guarantee, and would have to be rewritten again next time."""
    first_deps = make_deps(tmp_path)
    first_result = issue(first_deps, today=date(2026, 8, 11), dry_run=False)
    assert len(first_result.log_entry.issuance_log()) == 1, (
        "a day's first entry has exactly one issuance and nothing to preserve"
    )
    assert first_result.log_entry.morning_issuance is None, (
        "and no day written from here on gains the legacy field"
    )

    later_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a — refresh",
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain=False,
                rain_expected="Now raining", temp_high_c=25.0, temp_low_c=17.0, temp_high_low="25°C / 77°F"
            ),
            today_narrative="## Overview\nRain has moved in this evening.",
        )
    )
    refresh_result = issue(make_deps(tmp_path, llm=later_llm), today=date(2026, 8, 11), dry_run=False)

    log = refresh_result.log_entry.issuance_log()
    assert len(log) == 2, "a second issuance must not replace the first"
    snapshot = log[0]
    assert refresh_result.log_entry.morning_issuance is None, (
        "and it is preserved WITHOUT the legacy duplicate — item 137"
    )
    assert snapshot.rain_expected == "Unlikely"  # FakeLLMProvider's default morning response
    assert snapshot.temp_high_c == 27.0
    assert "Dry and warm" in snapshot.narrative_markdown
    assert snapshot.generated_at_utc == first_result.log_entry.meta.generated_at_utc
    # And the top-level fields really did move on to the evening's values —
    # the snapshot is an addition, not a substitute for the overwrite.
    assert refresh_result.log_entry.rain_expected == "Now raining"
    assert "Rain has moved in" in refresh_result.log_entry.narrative_markdown


def test_refresh_does_not_resnapshot_on_a_second_same_day_refresh(tmp_path):
    """Defensive case, shouldn't normally happen (forecast.yml's
    check job gates on meta.refreshed_at already being set) but must be
    correct if it ever does: a second refresh the same day must not
    replace the true morning snapshot with an already-refreshed version."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    first_refresh_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a",            today_properties=TodayProperties(
                rain=False,
                rain_expected="Light rain", temp_high_c=24.0, temp_low_c=16.0, temp_high_low="24°C / 75°F"
            ),
            today_narrative="## Overview\nFirst refresh.",
        )
    )
    issue(make_deps(tmp_path, llm=first_refresh_llm), today=date(2026, 8, 11), dry_run=False)

    second_refresh_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a",            today_properties=TodayProperties(
                rain=False,
                rain_expected="Heavy rain", temp_high_c=22.0, temp_low_c=15.0, temp_high_low="22°C / 72°F"
            ),
            today_narrative="## Overview\nSecond refresh.",
        )
    )
    second_result = issue(make_deps(tmp_path, llm=second_refresh_llm), today=date(2026, 8, 11), dry_run=False)

    # Still the TRUE morning values (FakeLLMProvider's default), not the
    # first refresh's "Light rain" — that would mean the real morning
    # issuance got silently replaced by an intermediate refreshed state.
    first = second_result.log_entry.issuance_log()[0]
    assert first.rain_expected == "Unlikely"
    assert "Dry and warm" in first.narrative_markdown
    # And the top-level fields reflect the LATEST (second) refresh.
    assert second_result.log_entry.rain_expected == "Heavy rain"


def test_refresh_preserves_verification_and_meta_generated_at(tmp_path):
    first_deps = make_deps(tmp_path)
    first_result = issue(first_deps, today=date(2026, 8, 11), dry_run=False)
    original_generated_at = first_result.log_entry.meta.generated_at_utc
    original_verification = first_result.log_entry.verification

    refresh_deps = make_deps(tmp_path)
    refresh_result = issue(refresh_deps, today=date(2026, 8, 11), dry_run=False)

    assert refresh_result.log_entry.meta.generated_at_utc == original_generated_at
    assert refresh_result.log_entry.verification == original_verification
    assert refresh_result.log_entry.meta.refreshed_at is not None
    assert refresh_result.log_entry.meta.refreshed_at > original_generated_at


def test_refresh_dry_run_does_not_write_or_publish(tmp_path):
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    before = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    class FailingPublisher:
        def publish(self, entry):
            raise AssertionError("publish() must not be called during --dry-run")

    deps = make_deps(tmp_path)
    deps.publisher = FailingPublisher()
    result = issue(deps, today=date(2026, 8, 11), dry_run=True)

    assert result.published is False
    after = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert after == before  # nothing written to disk


def test_refresh_real_run_writes_and_publishes(tmp_path):
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    published_entries = []

    class FakePublisher:
        def publish(self, entry):
            published_entries.append(entry)

    deps = make_deps(tmp_path)
    deps.publisher = FakePublisher()
    result = issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert result.published is True
    assert len(published_entries) == 1
    on_disk = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert on_disk.meta.refreshed_at is not None


def test_refresh_never_emails_even_when_email_sender_configured(tmp_path):
    # Web-only by design in this first version — see pipeline.py's comment.
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    emailed_entries = []

    class FakeEmailSender:
        def send(self, entry):
            emailed_entries.append(entry)

    deps = make_deps(tmp_path)
    deps.email_sender = FakeEmailSender()
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert emailed_entries == []


def test_a_later_issuance_is_told_it_is_one_and_shown_what_was_published(tmp_path):
    morning_llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=morning_llm), today=date(2026, 8, 11), dry_run=False)

    later_llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=later_llm), today=date(2026, 8, 11), dry_run=True)

    system_prompt, user_prompt = later_llm.system_prompts, later_llm.user_prompts
    assert "VERIFICATION IS ALREADY WRITTEN" in system_prompt
    # AND THE DAY'S EARLIER NARRATIVE IS NOT SENT — items 137/138,
    # 2026-09-16. The system prompt still says verification is written,
    # because that is a fact about the day; the USER prompt no longer carries
    # what was published, because every run is a fresh forecast.
    assert "EARLIER TODAY" not in user_prompt
    assert "Dry and warm" not in user_prompt  # the morning provider's narrative


def test_a_third_run_is_shown_no_earlier_narrative_at_all(tmp_path):
    """INVERTED 2026-09-16, and the history is the point.

    This test was `..._is_shown_both_earlier_narratives`, guarding a real bug:
    `_earlier_issuances` once returned only `entry.narrative_markdown`, so a
    third run saw the second and had no idea the first existed. The fix was
    to send every issuance.

    The whole payload is now gone (items 137/138). Every run is a fresh
    forecast and the reader "doesn't care when the last forecast was run", so
    a third run is shown none of them — and the bug this once guarded cannot
    recur because there is nothing to get wrong.

    Kept, inverted, rather than deleted: three issuances is the case where a
    reintroduced payload would show up first, and this is the only test that
    drives three.
    """
    issue(make_deps(tmp_path, llm=FakeLLMProvider()), today=date(2026, 8, 11), dry_run=False)

    second = FakeLLMProvider()
    second.response = second.response.model_copy(
        update={"today_narrative": "## Overview\nSECOND issuance."}
    )
    issue(make_deps(tmp_path, llm=second), today=date(2026, 8, 11), dry_run=False)

    third = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=third), today=date(2026, 8, 11), dry_run=True)

    _, user_prompt = third.calls[0]
    assert "Dry and warm" not in user_prompt, "the first issuance must not be sent"
    assert "SECOND issuance" not in user_prompt, "nor the second"
    assert "EARLIER TODAY" not in user_prompt


def test_every_run_is_told_what_time_it_is(tmp_path):
    """The bug this whole change exists for: the prompt carried a date and
    nothing else, so a run could not tell 06:00 from 18:00 and wrote as though
    the whole day were still ahead."""
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "ISSUED:" in user_prompt
    assert "WHAT MATTERS NOW:" in user_prompt
    assert "sunset 18:47" in user_prompt


def test_the_hours_ahead_are_supplied_separately_from_the_calendar_day(tmp_path):
    """A run issued in the evening was being asked to talk about tonight while
    holding only 00:00-23:00 of today."""
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
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
    first_deps = make_deps(tmp_path)
    first_deps.location = location_with_station
    issue(first_deps, today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="Test Station", station_id="A1", aqi=90, measured_at=datetime.now(timezone.utc))
        ],
    )
    refresh_deps = make_deps(tmp_path)
    refresh_deps.location = location_with_station
    result = issue(refresh_deps, today=date(2026, 8, 11), dry_run=True)

    assert result.log_entry.ground_aqi[0].aqi == 90  # the fresh evening reading, not the morning's 30


def test_a_re_issue_keeps_a_stored_reading_when_the_refetch_is_empty(tmp_path, monkeypatch):
    """The eighth divergence item 104 step 3 found and step 4 closed.

    `_with_merged_ground_aqi` is applied to the GUIDANCE, before the prompt is
    built, so it was not part of the entry construction step 3 merged — and
    only the old refresh path applied it. A re-issue reached through the other
    path got the raw fetch, so a station that failed to answer this hour
    erased a real reading the day's first run had captured.

    Now there is one path, so there is one answer."""
    from openlocalweather.config import WaqiStation
    from openlocalweather.models import GroundAQIReading

    location_with_station = LOCATION.model_copy(
        update={"waqi_stations": [WaqiStation(name="Test Station", station_id="A1")]}
    )
    monkeypatch.setattr(
        waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(
                name="Test Station", station_id="A1", aqi=42,
                measured_at=datetime.now(timezone.utc),
            )
        ],
    )
    first = make_deps(tmp_path)
    first.location = location_with_station
    issue(first, today=date(2026, 8, 11), dry_run=False)

    # The station does not answer this hour. Absence is not a reading.
    monkeypatch.setattr(waqi_fetch, "fetch_ground_aqi_stations", lambda stations, token: [])
    later = make_deps(tmp_path)
    later.location = location_with_station
    result = issue(later, today=date(2026, 8, 11), dry_run=False)

    assert [r.aqi for r in result.log_entry.ground_aqi] == [42], (
        "a failed re-fetch must not erase the reading the first issuance captured"
    )


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
    first_deps = make_deps(tmp_path)
    first_deps.location = location
    issue(first_deps, today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(waqi_fetch, "fetch_ground_aqi_stations", lambda stations, token: refetched)
    refresh_deps = make_deps(tmp_path, llm=llm)
    refresh_deps.location = location
    return issue(refresh_deps, today=date(2026, 8, 11), dry_run=True).log_entry


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
    # TWO, so a run fits exactly and the burn below is what tips it over.
    deps = replace(
        deps, location=LOCATION.model_copy(update={"max_llm_calls_per_24h": 2})
    )

    # Burn one of the two — ON THE CREDENTIAL THE RUN WILL CALL. This used to
    # burn `provider="x", model="y"` and passed only because the pre-flight
    # counted the whole ledger. Once it counted per link (ROADMAP item 178) a
    # row for "x" stopped counting against this run, and at a cap of 1 the run
    # was refused anyway for needing 2 — so the burn did nothing and the test
    # would have passed with it deleted. Now it is load-bearing again.
    record_attempt(
        tmp_path, provider="RefusingProvider", model="fake-model",
        purpose="test", max_calls=2,
    )

    with pytest.raises(SpendCapExceeded):
        issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert called == [], "the provider must never be reached once the cap is hit"


def test_a_normal_run_records_exactly_two_calls(tmp_path):
    """Counting has to be accurate in the ordinary case too — an
    over-counting cap would refuse legitimate forecasts.

    TWO SINCE ROADMAP ITEM 59 STEP 3, and the number is the point of the
    test rather than an incidental. A forecast is a judgment call and then a
    rendering call, so a cap sized for one forecast a day must be sized for
    two calls a day — see item 26, where the reader's own cap is what this
    doubling actually spends.
    """
    from openlocalweather.spend import read_ledger

    deps = make_deps(tmp_path)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    ledger = read_ledger(tmp_path)
    assert len(ledger) == 2
    assert [e.purpose for e in ledger] == ["forecast", "forecast"]
    assert all(e.model for e in ledger)


def test_a_dry_run_still_counts_because_it_still_calls_the_llm(tmp_path):
    """--dry-run skips writes, commit and email, but it DOES call the model
    (that is the point of it), so it costs real money and must be counted.
    Exempting it would leave a loophole that spends without accounting."""
    from openlocalweather.spend import read_ledger

    deps = make_deps(tmp_path)
    issue(deps, today=date(2026, 8, 11), dry_run=True)
    assert len(read_ledger(tmp_path)) == 2


def test_a_failed_sun_lookup_still_tells_the_model_the_time(tmp_path, monkeypatch):
    """The clock is not the sun.

    An earlier version put both in one try block, so a failed astronomical
    lookup made the prompt say "time of day unavailable" for a run that knew
    perfectly well it was 18:15. Knowing the time is most of the value here —
    knowing where the sun is only refines it.

    The sun no longer fails over the network, so this now stands for a defect
    in the computation rather than a timeout. The guarantee is the same one
    and worth keeping either way.
    """
    monkeypatch.setattr(
        solar, "sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "ISSUED:" in user_prompt
    assert "It is " in user_prompt, "the local time survives a failed sun lookup"
    assert "part of day as unknown" in user_prompt, (
        "and the phase is declared unknown rather than guessed from the clock"
    )


def test_a_failed_sun_lookup_does_not_also_lose_the_hours_ahead(tmp_path, monkeypatch):
    """Separate concerns, separate try blocks. Sharing one meant an
    astronomical lookup could silently cost the forward window — which is the
    more useful of the two."""
    monkeypatch.setattr(
        solar, "sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
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
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "sunset 18:47" in user_prompt, "the sun times are unaffected"
    assert "TODAY'S MULTI-MODEL GUIDANCE" in user_prompt, "the calendar day still stands"


def test_neither_failure_stops_a_forecast_being_produced(tmp_path, monkeypatch):
    """A forecast without sun times is worse. A forecast that does not happen
    because an astronomical lookup failed is much worse."""
    def down(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(solar, "sun_times", down)
    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_forward", down)
    llm = FakeLLMProvider()
    result = issue(
        make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True
    )
    assert result.log_entry is not None
    # Two calls since ROADMAP item 59 step 3: judgment, then narrative.
    assert len(llm.calls) == 2


def test_sunrise_and_sunset_are_stored_from_code_not_the_narrative(tmp_path):
    """Facts, not prose. Asking a language model to restate a computed time is
    how a wrong one gets published — and these are checkable by anyone who
    looks out of a window, so being wrong is expensive."""
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=True)
    assert result.log_entry.sunrise == "06:40"
    assert result.log_entry.sunset == "18:47"


def test_the_computed_sun_times_reach_the_prompt_and_the_entry(tmp_path, monkeypatch):
    """The one test in this file that runs the real computation.

    Everything else pins `solar.sun_times` so its assertions stay meaningful
    as the calendar moves, which means everything else would still pass if the
    pipeline handed the computation the wrong coordinates, the wrong date or
    the wrong offset. That is the failure this file has seen before: a value
    correctly computed and never wired to what consumes it.

    The equality is the sharp check — it fails if the pipeline hands the
    computation the wrong latitude, longitude, date or offset. Verified by
    swapping lat and lon in `_sun_context`, which turns it red.

    The band is the blunt one, and it is what stops both sides being wrong
    together. LOCATION sits within a degree of the equator, where the year's
    whole spread is 05:33-06:03 and 17:37-18:08; the bounds below leave room
    for that and still exclude a value that is missing, garbled, or from
    somewhere else entirely.
    """
    monkeypatch.setattr(solar, "sun_times", real_sun_times)
    llm = FakeLLMProvider()
    result = issue(
        make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True
    )
    _, user_prompt = llm.calls[0]

    today_local = now_in_tz(LOCATION.timezone).date()
    expected = real_sun_times(
        LOCATION.primary_point.lat, LOCATION.primary_point.lon, today_local, 0
    )
    assert result.log_entry.sunrise == expected.sunrise.strftime("%H:%M")
    assert result.log_entry.sunset == expected.sunset.strftime("%H:%M")
    assert f"sunset {result.log_entry.sunset}" in user_prompt

    assert "05:15" < result.log_entry.sunrise < "06:20", result.log_entry.sunrise
    assert "17:20" < result.log_entry.sunset < "18:25", result.log_entry.sunset


def test_missing_sun_times_are_stored_as_absent_not_as_an_empty_clock(tmp_path, monkeypatch):
    """An empty string would render as a blank value next to the label, which
    reads as a broken page rather than as the correct answer.

    The docstring used to say "polar night has no sunrise". That was wrong
    about the data as well as about polar night: Open-Meteo reports polar
    night as sunrise and sunset both at local midnight, verified against the
    live API on 2026-08-28, and `solar.sun_times` now matches it. This path is
    reached when the sun could not be worked out at all, not at high
    latitude."""
    monkeypatch.setattr(
        solar, "sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=True)
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
    issue(deps1, today=date(2026, 8, 11), dry_run=False)

    second = FakeLLMProvider()
    second.response = second.response.model_copy(
        update={"today_narrative": "## Overview\nSECOND issuance."}
    )
    issue(make_deps(tmp_path, llm=second), today=date(2026, 8, 11), dry_run=False)

    third = FakeLLMProvider()
    third.response = third.response.model_copy(
        update={"today_narrative": "## Overview\nTHIRD issuance."}
    )
    issue(make_deps(tmp_path, llm=third), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert "THIRD" in entry.narrative_markdown, "the latest stays at the top level"
    assert len(entry.earlier_issuances) == 2
    assert "Dry and warm" in entry.earlier_issuances[0].narrative_markdown, "first, oldest first"
    assert "SECOND" in entry.earlier_issuances[1].narrative_markdown, "second, no longer lost"
    assert "Dry and warm" in entry.issuance_log()[0].narrative_markdown, "still the first"

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
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    before = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).model_predictions

    for _ in range(3):
        issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

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
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
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
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    second = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).model_predictions

    assert second == first, "a second run rewrote the numbers tomorrow scores"


def test_a_second_run_still_writes_a_fresh_narrative(tmp_path):
    """The other half of the rule: force forces the NARRATIVE, and can never
    reach the scored numbers. A guard that also froze the prose would make a
    forced re-run pointless."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    evening = FakeLLMProvider()
    evening.response = evening.response.model_copy(
        update={"today_narrative": "## Overview\nStorms arrived after all."}
    )
    issue(
        make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False
    )

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.narrative_markdown == "## Overview\nStorms arrived after all."


def test_a_second_run_describes_the_numbers_the_record_holds(tmp_path, monkeypatch):
    """A run must be told the numbers the record holds FOR IT, and since
    contract item 4 that is its own extraction, not the day's first.

    INVERTED 2026-09-13, and the old expectation is kept here because the
    reason it flipped is the point. It read: "A run whose predictions are
    kept must be told the KEPT ones. Handing it the fresher extraction would
    leave the narrative quoting values the record does not contain." True
    while a DAY held one set of predictions. Contract item 4 gives every
    issuance a row, so the record now contains exactly what each issuance
    saw — the premise dissolved, the same way C4 did once Day+0 stopped
    being a calendar day.

    The second assertion is the one that earns the inversion: the value the
    evening was shown is in the evening's own row. Without that this would
    just be a test bent to fit the code."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    def evening_cycle_hourly(*args, **kwargs):
        fixture = hourly_fixture()
        for model in MODELS:
            fixture["hourly"][f"temperature_2m_{model}"] = [19.0, 23.0, 31.0]
        return fixture

    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today", evening_cycle_hourly)
    evening = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = evening.calls[-1]
    block = predictions_block(user_prompt)
    assert "31.0" in table_column(block, "high_c"), (
        "the evening is shown the cycle the evening read"
    )

    rows = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows
    assert len(rows) == 2
    assert any(p.high_c == 31.0 for p in rows[1].predictions.day0), (
        "and the record holds it — which is why showing it is no longer a lie"
    )
    assert any(p.high_c == 26.0 for p in rows[0].predictions.day0), (
        "row 0, the scored set, still holds the morning's"
    )


def _forced_rerun(tmp_path, narrative, after_refresh: bool):
    """A day that has already been forecast, then run-daily again — the shape
    `force: true` on a manual dispatch produces."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    if after_refresh:
        evening = FakeLLMProvider()
        evening.response = evening.response.model_copy(
            update={"today_narrative": "## Overview\nEvening refresh."}
        )
        issue(make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False)

    forced = FakeLLMProvider()
    forced.response = forced.response.model_copy(update={"today_narrative": narrative})
    deps = make_deps(tmp_path, llm=forced)
    issue(deps, today=date(2026, 8, 11), dry_run=False)
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

    assert entry.issuance_log()[0].narrative_markdown == "## Overview\nDry and warm.", (
        "the morning issuance must stay the MORNING's, not the last run's"
    )
    assert entry.meta.generated_at_utc == entry.issuance_log()[0].generated_at_utc, (
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

    assert len(entry.issuance_log()) == 2
    assert entry.issuance_log()[0].narrative_markdown == "## Overview\nDry and warm."


def test_a_forced_re_run_is_told_its_verification_is_already_written(tmp_path):
    """Otherwise it writes a fresh morning-style forecast over one its readers
    have already read, and emails it as though it were the day's first."""
    _, forced = _forced_rerun(tmp_path, "## Overview\nForced re-run.", after_refresh=True)

    system_prompt, user_prompt = forced.system_prompts, forced.user_prompts
    assert "VERIFICATION IS ALREADY WRITTEN" in system_prompt
    assert "Evening refresh." not in user_prompt, (
        "the day's published narrative is NOT sent — items 137/138. The "
        "system prompt still says verification is written, because that is a "
        "fact about the day; what was published is not."
    )


def test_a_refresh_keeps_the_sun_times(tmp_path):
    """They were only set on the day's first run, so any refreshed day
    carried nulls from that point — the site would simply stop showing sun
    times after the evening run, with nothing to flag it."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.sunrise == "06:40"
    assert entry.sunset == "18:47"


def test_a_refresh_with_no_sun_data_keeps_the_mornings(tmp_path, monkeypatch):
    """A failed sun lookup on a re-issue must not erase a good value the first
    run captured. Fresher is not automatically better when the fresher value
    is 'unknown'."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    monkeypatch.setattr(
        solar, "sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.sunrise == "06:40", "the morning's value survives a failed re-fetch"


def test_a_run_daily_re_issue_also_keeps_the_mornings_sun_times(tmp_path, monkeypatch):
    """The same rule as the refresh above, on the other path into an existing
    day. It had the rule and a comment explaining it; this one had neither, so
    a forced re-run whose sun computation threw erased times the morning had
    captured and the site simply stopped showing them.

    Measured 2026-09-13 by driving both re-issue paths against identical
    fixtures: six fields disagreed and each path held a fix the other lacked.
    ROADMAP item 104 step 3."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    monkeypatch.setattr(
        solar, "sun_times", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.sunrise == "06:40", "the morning's value survives a failed re-compute"
    assert entry.sunset == "18:47"


def test_a_re_issue_is_stamped_with_the_model_that_wrote_it(tmp_path):
    """The entry describes the forecast currently IN it, which is the rule the
    refresh path already applied to the prompt hash, the finish reason and the
    token counts — and did not apply to the model that produced them.

    It mattered twice over: `write_prompt_archive` copies `meta.llm_model` onto
    the archived evening prompt, and answering "which model wrote this" is what
    that archive is for. ROADMAP item 104 step 3."""
    morning = make_deps(tmp_path, llm=FakeLLMProvider())
    morning.llm_provider.model = "morning-model"
    issue(morning, today=date(2026, 8, 11), dry_run=False)

    evening = make_deps(tmp_path, llm=FakeLLMProvider())
    evening.llm_provider.model = "evening-model"
    issue(evening, today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.meta.llm_model == "evening-model"


def test_a_re_issue_records_the_trigger_and_version_that_ran_it(tmp_path):
    """Same rule, two more fields that were the morning's on a refresh. A
    pipeline_version naming the release that wrote the PREVIOUS narrative makes
    the record's own provenance wrong in the direction nobody checks."""
    morning = make_deps(tmp_path)
    morning.trigger_source = "schedule"
    morning.pipeline_version = "1.0.0"
    issue(morning, today=date(2026, 8, 11), dry_run=False)

    evening = make_deps(tmp_path)
    evening.trigger_source = "workflow_dispatch"
    evening.pipeline_version = "2.0.0"
    issue(evening, today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.meta.trigger_source == "workflow_dispatch"
    assert entry.meta.pipeline_version == "2.0.0"


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
    pin_clock(monkeypatch)
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: convective_forward_hourly()
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

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
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert "no model supplied a CAPE series" in user_prompt


def test_quiet_cape_does_not_set_the_convective_flag(tmp_path, monkeypatch):
    pin_clock(monkeypatch)
    def calm(times):
        return [120.0] * len(times)

    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        lambda *a, **k: forward_hourly_from_now(cape_gfs_seamless=calm),
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

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
    issue(deps, today=date(2026, 8, 11), dry_run=False)

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
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
            {d: StationWeather(thunder=True, precipitation=False) for d in (start, end)},
            None,
        ),
    )
    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    cache = actuals_cache_store.read_actuals_cache(tmp_path)
    stored = actuals_cache_store.as_date_dict(cache.primary)
    assert stored, "no actuals were written"
    assert all(a.thunder is True for a in stored.values())


# ---------------------------------------------------------------------------
# olw forecast — one verb that reads the day and dispatches
# ---------------------------------------------------------------------------


def test_forecast_runs_the_full_pipeline_when_the_day_is_empty(tmp_path):
    result = pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11))

    assert result.first_issuance is True
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert scored_predictions(entry).day0, "the day's first run owns the predictions"
    assert entry.meta.refreshed_at is None


def test_forecast_re_issues_when_the_day_already_has_an_entry(tmp_path):
    """The real distinction has nothing to do with the clock: the first run of
    a day owns verification and the scored numbers, every later run is an
    update. An operator picking a verb by time of day is the last place the
    morning/evening split survives."""
    pipeline.run_forecast(make_deps(tmp_path), today=date(2026, 8, 11))
    before = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    # TWELVE HOURS LATER IS TWO CYCLES LATER, and since ROADMAP item 121 the
    # test has to say so. `now` moves only the repeat-interval clock; the
    # guidance cycle is derived from the real one, so without this the
    # evening run finds the same cycle it started with and correctly declines
    # to reason. Aged through the stored field the comparison actually reads,
    # rather than by patching the comparison.
    before.guidance_initialised_at = before.guidance_initialised_at - timedelta(hours=12)
    log_store.write_log_entry(tmp_path, before)

    evening = FakeLLMProvider()
    evening.response = evening.response.model_copy(
        update={"today_narrative": "## Overview\nEvening update."}
    )
    result = pipeline.run_forecast(
        make_deps(tmp_path, llm=evening),
        today=date(2026, 8, 11),
        now=before.meta.generated_at_utc + timedelta(hours=12),
    )

    assert result.first_issuance is False
    assert result.newly_verified is None, "a later issuance does not verify"
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.narrative_markdown == "## Overview\nEvening update."
    assert scored_predictions(entry) == scored_predictions(before)
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

    assert result.first_issuance is False
    after = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert after.narrative_markdown == "## Overview\nForced."
    assert scored_predictions(after) == scored_predictions(entry)


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

    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
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

    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = result.log_entry
    expected = aligned_cycle_at(now).initialised_at

    assert entry.guidance_source == "derived"
    assert entry.guidance_initialised_at == expected


def test_a_metadata_fetch_returning_none_costs_the_run_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(model_run_fetch, "fetch_model_run", lambda model: None)

    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
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

    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
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

    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

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
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=evening_initialised, available_at=now - timedelta(minutes=30)
        ),
    )
    refresh_result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = refresh_result.log_entry

    assert entry.guidance_source == "observed"
    assert entry.guidance_initialised_at == evening_initialised
    assert len(entry.earlier_issuances) == 1
    assert entry.earlier_issuances[0].guidance_source == "observed"
    assert entry.earlier_issuances[0].guidance_initialised_at == morning_initialised


def test_an_impossible_guidance_age_is_not_narrated(tmp_path, monkeypatch):
    """A cycle initialised in the future means this machine's clock is wrong,
    or the provider said something impossible. The entry still records what
    was computed — the anomaly belongs in the record — but the prompt is told
    the recency is unknown rather than handed a negative number to explain."""
    from openlocalweather.fetch import model_run as model_run_fetch

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model,
            initialised_at=datetime.now(timezone.utc) + timedelta(hours=3),
            available_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ),
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert "could not establish which model cycle" in user_prompt
    assert '"hours_old"' not in user_prompt

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.guidance_age_hours < 0, "the record keeps the anomaly"


def test_the_days_first_run_has_no_previous_issuance_to_compare(tmp_path):
    """No stored entry yet, so there is no basis for the comparison — null,
    not false. Uses the default derived-cycle fallback from patch_fetches."""
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert '"newer_than_previous_issuance": null' in user_prompt


def test_a_reissue_reports_true_when_a_newer_cycle_has_landed(tmp_path, monkeypatch):
    """A re-issue whose observed cycle is newer than the morning's stored
    one is real news — the models changed their minds, not just the clock."""
    now = datetime.now(timezone.utc)
    morning_initialised = now - timedelta(hours=10)
    advanced_initialised = now - timedelta(hours=4)

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=morning_initialised, available_at=now - timedelta(minutes=30)
        ),
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=advanced_initialised, available_at=now - timedelta(minutes=30)
        ),
    )
    later_llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=later_llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = later_llm.calls[0]
    assert '"newer_than_previous_issuance": true' in user_prompt


def test_a_reissue_reports_false_when_no_newer_cycle_has_landed(tmp_path, monkeypatch):
    """A re-issue whose observed cycle matches the morning's stored one:
    the hours moved, the models did not — the prompt must say so plainly
    rather than let the model hunt for manufactured differences."""
    now = datetime.now(timezone.utc)
    unchanged_initialised = now - timedelta(hours=10)

    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model, initialised_at=unchanged_initialised, available_at=now - timedelta(minutes=30)
        ),
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    later_llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=later_llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = later_llm.calls[0]
    assert '"newer_than_previous_issuance": false' in user_prompt


def test_a_run_that_knows_less_than_the_last_one_says_so(tmp_path, monkeypatch):
    """A resolved cycle OLDER than the previous issuance's means this run fell
    back to the derived floor while the last one had a real observation. That
    is not "no new guidance has landed" — which tells the model to keep the
    update short — it is no basis for the comparison, and null says so."""
    from openlocalweather.fetch import model_run as model_run_fetch

    observed = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    monkeypatch.setattr(
        model_run_fetch,
        "fetch_model_run",
        lambda model: model_run_fetch.ModelRun(
            model=model,
            initialised_at=observed,
            available_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ),
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    # The re-issue loses the observation and falls back to the derived floor,
    # which is older than what the first run recorded.
    monkeypatch.setattr(model_run_fetch, "fetch_model_run", lambda model: None)
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert '"newer_than_previous_issuance": null' in user_prompt
    assert '"newer_than_previous_issuance": false' not in user_prompt


# ---------------------------------------------------------------------------
# Item 53.2 — the forward fetch is not the only place CAPE lives.
# ---------------------------------------------------------------------------


def pin_clock(monkeypatch, on=date(2026, 8, 11), hour=8):
    """Make the run's clock agree with the day the test pins.

    THE MISMATCH THIS CLOSES IS A TIME BOMB, not a flake. The hourly fixtures
    build their timestamps from `now_in_tz`, so a test that passes
    `today=2026-08-11` to the pipeline while the fixture dates its hours today
    is using TWO different days. It holds together until some arithmetic
    crosses a boundary between them, and then fails for every date after —
    `test_a_failed_forward_window_falls_back_to_the_day_zero_cape` ran for 43
    days and died on 2026-09-23, with nothing in the code changed.

    Item 169 pinned the suite's HOUR and left the date alone, on the grounds
    that a test speaking about "today" should use the real one. Correct for a
    test that reasons about today; wrong for one that has already chosen a
    different day and told the pipeline so.
    """
    fixed = datetime(on.year, on.month, on.day, hour, 30)
    monkeypatch.setattr(pipeline, "now_in_tz", lambda tz: fixed)
    monkeypatch.setattr(dates_module, "now_in_tz", lambda tz: fixed)


def today_only_hourly_from_now(**extra_series):
    """The day-0 fetch, anchored to the real clock so hours remain ahead.

    Shape matters: `fetch_forecast_hourly_today` asks for forecast_days=1, so
    this is ONE local day and stops at 23:00 — which is exactly the limit the
    fallback has to be honest about.
    """
    start = now_in_tz(LOCATION.timezone).replace(minute=0, second=0, microsecond=0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(6)]
    series = {name: values(times) for name, values in extra_series.items()}
    return {"hourly": {"time": times, "precipitation_gfs_seamless": [0.0] * len(times), **series}}


def test_a_failed_forward_window_falls_back_to_the_day_zero_cape(tmp_path, monkeypatch):
    """The 2026-08-29 run, in miniature.

    `fetch_forecast_hourly_forward` timed out three runs running while
    `fetch_forecast_hourly_today` — same host, same endpoint, same variable
    list including cape — succeeded in every one of them. The convective
    outlook was reported "unavailable" with the data sitting in memory.

    THE RUN'S CLOCK MATCHES THE DAY IT IS ABOUT, pinned 2026-09-23.
    `today_only_hourly_from_now` builds its hours from `now_in_tz`, so on the
    wall clock the fixture's hours were dated TODAY while the pipeline was
    told 2026-08-11. That held together for 43 days and stopped dead on
    2026-09-23 — passing for every date before it and failing for every date
    after, on code that had not changed.

    Item 169 pinned the suite's HOUR and left the date alone on the grounds
    that a test speaking about "today" should use the real one. This is the
    case that missed: a test which pins `today` for the pipeline and takes
    its fixture's dates from the clock is not using the real date, it is
    using two different ones.
    """
    pin_clock(monkeypatch)
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read timed out")),
    )
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_today",
        lambda *a, **k: today_only_hourly_from_now(
            cape_ukmo_seamless=lambda times: [1830.0 if i == 1 else 40.0 for i in range(len(times))]
        ),
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "no model supplied a CAPE series" not in user_prompt
    assert '"convective": true' in user_prompt
    assert '"peak_cape_jkg": 1830.0' in user_prompt


def test_the_fallback_window_says_it_is_only_the_rest_of_today(tmp_path, monkeypatch):
    """A window that stops at 23:00 must not be read as covering tonight and
    tomorrow. The narrative is told the window narrowed, so "no instability
    overnight" cannot be inferred from a series that simply ends."""
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read timed out")),
    )
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_today",
        lambda *a, **k: today_only_hourly_from_now(
            cape_ukmo_seamless=lambda times: [1830.0 if i == 1 else 40.0 for i in range(len(times))]
        ),
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    # Case-insensitive: the prompt shouts it, and the emphasis is styling
    # rather than the behaviour under test.
    assert "rest of today only" in user_prompt.lower()
    assert "ENDS AT 23:00 local" in user_prompt
    assert "Unavailable this run." not in user_prompt.split("HOURS AHEAD")[1][:200]


def test_the_full_forward_window_is_not_labelled_as_narrowed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: convective_forward_hourly()
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "rest of today only" not in user_prompt.lower()


# ---------------------------------------------------------------------------
# Item 53.3 — a gap must not be reportable as safety.
# ---------------------------------------------------------------------------


def test_an_absent_cape_series_forbids_an_all_clear(tmp_path, monkeypatch):
    """The sentence that actually reached readers on 2026-08-29:

        "Model convective instability guidance (CAPE) was unavailable for
        this cycle. Under stable synoptic conditions and limited atmospheric
        moisture, no thunderstorm or severe weather hazards are anticipated
        for the basin tonight or tomorrow."

    The code half of the contract held — summarize_instability returned None,
    exactly as instability.py promises. Nothing forbade the narrative from
    filling the hole with reassurance, so it reasoned from absence of
    evidence to evidence of absence, in the section a reader checks before
    going out on the water.
    """
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read timed out")),
    )
    # No CAPE anywhere: not in the forward window, not in the day-0 fallback.
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_today", lambda *a, **k: today_only_hourly_from_now()
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts

    assert "no model supplied a CAPE series" in user_prompt
    # The gap is stated AND the inference from it is refused, in the block
    # itself rather than only in the system prompt — the failing run had the
    # system prompt in front of it too.
    assert "absence of evidence" in user_prompt.lower()
    assert "do NOT" in user_prompt
    # And the rule is in the standing instructions as well, so it survives a
    # reader of either half.
    assert "A MISSING BLOCK IS NOT AN ALL-CLEAR" in system_prompt


def test_a_present_cape_series_carries_no_gap_warning(tmp_path, monkeypatch):
    pin_clock(monkeypatch)
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: convective_forward_hourly()
    )
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=True)
    _, user_prompt = llm.calls[0]

    assert "no model supplied a CAPE series" not in user_prompt
    assert "absence of evidence" not in user_prompt.lower()


# ---------------------------------------------------------------------------
# ROADMAP item 53.4 — a degraded run says it is degraded
#
# The 2026-08-29 incident ran three times with the forward hourly window
# missing and said so only on stderr, inside an Actions log nobody reads. The
# record it committed was indistinguishable from a clean run's, so neither
# the page, the reader, nor a later investigation could tell that the day's
# hazard block had been built on less than usual.
# ---------------------------------------------------------------------------


def _raises(exc: Exception):
    def _f(*args, **kwargs):
        raise exc

    return _f


def test_a_run_that_lost_the_forward_window_records_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        _raises(RuntimeError("Read timed out. (read timeout=30)")),
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    codes = [d.code for d in result.log_entry.meta.degradations]
    assert "hours_ahead_narrowed" in codes

    # And it survives the round trip to disk, because the committed record is
    # the thing an investigation actually reads.
    written = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert [d.code for d in written.meta.degradations] == codes


def test_a_clean_run_records_no_degradations(tmp_path):
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    assert result.log_entry.meta.degradations == []


def test_a_configured_station_that_did_not_answer_is_recorded(tmp_path, monkeypatch):
    """Named in item 53's "Not established": fetch_metar returns None on every
    failure path and airport_metar is never persisted, so the record could not
    say whether the station was consulted."""
    monkeypatch.setattr(
        pipeline.metar_fetch, "observed_station_data", lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, None)
    )
    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    monkeypatch.setattr(metar_fetch, "fetch_metar", lambda icao: None)

    result = issue(deps, today=date(2026, 8, 11), dry_run=False)
    assert "metar_unavailable" in [d.code for d in result.log_entry.meta.degradations]


def test_no_station_configured_is_a_state_not_a_degradation(tmp_path):
    """LOCATION has metar_station_icao="". A deployment with no station is not
    running degraded; it is running as configured, and saying otherwise would
    make the flag mean nothing."""
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    assert "metar_unavailable" not in [d.code for d in result.log_entry.meta.degradations]


def test_a_station_that_answered_is_not_a_degradation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline.metar_fetch, "observed_station_data", lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, None)
    )
    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    monkeypatch.setattr(metar_fetch, "fetch_metar", lambda icao: [{"rawOb": "HKKI 111200Z"}])

    result = issue(deps, today=date(2026, 8, 11), dry_run=False)
    assert "metar_unavailable" not in [d.code for d in result.log_entry.meta.degradations]


def test_a_re_issue_keeps_the_earlier_issuance_s_own_degradation(tmp_path, monkeypatch):
    """A degraded morning followed by a clean evening. The day is not
    "degraded" — the MORNING was, and the evening was not, so each issuance
    has to carry its own answer. Merging them would either accuse a clean
    re-issue of a gap it did not have, or erase the morning's."""
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        _raises(RuntimeError("Read timed out. (read timeout=30)")),
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    # The evening run's forward fetch works.
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: forward_hourly_fixture()
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    entry = result.log_entry
    assert entry.meta.degradations == [], "the re-issue was clean and must say so"
    assert [d.code for d in entry.earlier_issuances[0].degradations] == [
        "hours_ahead_narrowed"
    ], "the morning's own gap must survive being overwritten"


def test_a_degraded_re_issue_records_its_own_gap(tmp_path, monkeypatch):
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        _raises(RuntimeError("Read timed out. (read timeout=30)")),
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    assert [d.code for d in result.log_entry.meta.degradations] == ["hours_ahead_narrowed"]
    assert result.log_entry.earlier_issuances[0].degradations == []


def test_a_forced_re_run_keeps_its_own_and_the_earlier_gap(tmp_path, monkeypatch):
    """The third path into an existing day, and the one with previous form:
    items 8 and 34 both came from run_daily_pipeline rebuilding an entry and
    quietly losing what the earlier issuance held. A degraded morning
    followed by a forced clean re-run must keep both answers separate."""
    monkeypatch.setattr(
        open_meteo,
        "fetch_forecast_hourly_forward",
        _raises(RuntimeError("Read timed out. (read timeout=30)")),
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: forward_hourly_fixture()
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    assert result.log_entry.meta.degradations == []
    assert [d.code for d in result.log_entry.earlier_issuances[0].degradations] == [
        "hours_ahead_narrowed"
    ]


def test_a_re_issue_against_a_legacy_entry_keeps_its_scored_set_as_row_zero(tmp_path):
    """The migration's dangerous hour, and the one that could destroy a day.

    Every entry committed before contract item 4 holds `model_predictions` and
    no rows. The FIRST re-issue after the upgrade meets one of those, and if
    the append had read `.prediction_rows` directly it would have appended to
    an empty list — writing a day whose scored numbers are the EVENING's and
    whose morning call is gone. The bridge is read instead, so the stored set
    becomes row 0 and the re-issue lands after it.
    """
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    # Rewrite it into the shape every committed entry is in today.
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    legacy = entry.model_copy(
        update={
            "model_predictions": scored_predictions(entry),
            "prediction_rows": [],
        }
    )
    log_store.write_log_entry(tmp_path, legacy)
    morning_numbers = scored_predictions(legacy)
    assert morning_numbers.day0, "fixture must actually hold predictions"

    evening = FakeLLMProvider()
    evening.response = evening.response.model_copy(
        update={"today_narrative": "## Overview\nEvening."}
    )
    issue(make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False)

    after = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert len(after.prediction_rows) == 2, "the legacy set became row 0, the re-issue row 1"
    assert after.prediction_rows[0].predictions == morning_numbers
    assert scored_predictions(after) == morning_numbers, "what tomorrow scores has not moved"


def test_prediction_rows_are_append_only_across_three_issuances(tmp_path):
    """Row 0 is immutable. It is the write-once rule the accuracy record rests
    on, in the form contract item 4 gives it."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    first = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0]

    for narrative in ("## Overview\nSecond.", "## Overview\nThird."):
        llm = FakeLLMProvider()
        llm.response = llm.response.model_copy(update={"today_narrative": narrative})
        issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    rows = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows
    assert len(rows) == 3
    assert rows[0] == first, "row 0 is never rewritten"
    assert [r.issued_at for r in rows] == sorted(r.issued_at for r in rows), "oldest first"


def test_a_later_issuance_stores_the_blend_it_actually_made(tmp_path):
    """Before contract item 4 this number was computed and thrown away: the
    evening run spent a judgment call, `_blend_prediction` turned it into a
    prediction, and the entry kept only the morning's. Item 104 measured that
    as "a whole LLM call, up to four requests, for display only"."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    evening = FakeLLMProvider()
    evening.response = evening.response.model_copy(
        update={
            "today_properties": evening.response.today_properties.model_copy(
                update={"rain": True, "rain_expected": "Likely", "temp_high_c": 31.0}
            )
        }
    )
    issue(make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False)

    rows = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows
    blends = [
        next(p for p in r.predictions.day0 if p.model == BLEND_MODEL_ID) for r in rows
    ]
    assert blends[0].rain is False and blends[1].rain is True
    assert blends[1].high_c == 31.0, "the evening's own call is on the record"


def test_a_run_on_a_day_that_has_an_entry_reports_a_later_issuance(tmp_path):
    """A run on a day that already has an entry says so in its result.

    `first_issuance` is a PREDICATE, not a constant. One verb, `olw forecast`,
    serves every run of the day since item 104 merged the two pipelines, so
    the same code path produces both the day's first forecast and its later
    ones and must report which it just wrote.

    THE NAMES IN THIS DOCSTRING WERE DEAD FOR WEEKS. It cited `olw run-daily`
    and `run_refresh_pipeline`, neither of which exists, and built its whole
    premise on a second CLI verb whose output could disagree with the first.
    There is no second verb. The assertion below is what stands between the
    result and a lie about the entry it just wrote.
    """
    first = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    assert first.first_issuance is True

    again = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    assert again.first_issuance is False


def test_an_entry_written_before_this_existed_loads_as_unrecorded(tmp_path):
    """Not [] — see LogEntryMeta.degradations. The health check reads this
    back and must not report those runs as having had everything they need."""
    from openlocalweather.models import LogEntryMeta

    meta = LogEntryMeta(
        generated_at_utc=datetime(2026, 8, 11, 6, 7, tzinfo=timezone.utc),
        llm_provider="gemini",
        llm_model="gemini-3.6-flash",
        pipeline_version="0.1.0",
    )
    assert meta.degradations is None


# ---------------------------------------------------------------------------
# ROADMAP item 57 — what a real model has to beat
# ---------------------------------------------------------------------------


def test_the_baselines_are_predicted_and_stored(tmp_path):
    """Persistence and climatology enter the ledger through the same path as
    GFS, so the published figures have something to be read against."""
    _seed_yesterday_log_entry(tmp_path, date(2026, 8, 10))
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    day0 = {p.model for p in scored_predictions(result.log_entry).day0}
    assert "persistence" in day0
    assert "climatology" in day0

    # At every lead, because a real model is scored at every lead and a
    # yardstick that only exists at Day+0 cannot say whether the extended
    # outlook is worth anything.
    assert "persistence" in {p.model for p in scored_predictions(result.log_entry).day3}
    assert "climatology" in {p.model for p in scored_predictions(result.log_entry).day7}


def test_persistence_repeats_yesterday_not_today(tmp_path):
    """The failure that would not look like one: a baseline reading the day it
    is forecasting would score near-perfectly and make every real model look
    hopeless, and nothing about the page would appear broken."""
    _seed_yesterday_log_entry(tmp_path, date(2026, 8, 10))
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    cache = actuals_cache_store.read_actuals_cache(tmp_path)
    stored = actuals_cache_store.as_date_dict(cache.primary)
    yesterday = stored.get(date(2026, 8, 10))
    assert yesterday is not None, "the fixture must supply yesterday's actual"

    persistence = next(
        p for p in scored_predictions(result.log_entry).day0 if p.model == "persistence"
    )
    assert persistence.rain == yesterday.rain


def test_the_forecaster_is_never_shown_a_baseline(tmp_path):
    """Same standing rule as the blend, adjacent reason: a yardstick handed to
    the forecaster reads as a sixth opinion, and persistence's only input is
    an observation the forecaster already holds."""
    llm = FakeLLMProvider()
    _seed_yesterday_log_entry(tmp_path, date(2026, 8, 10))
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    assert "persistence" not in predictions_block(user_prompt)
    assert "climatology" not in predictions_block(user_prompt)


def test_a_baseline_with_nothing_to_stand_on_makes_no_prediction(tmp_path, monkeypatch):
    """Day one, with no archive to read. Neither baseline may invent a dry
    call out of an empty record — ModelPrediction.rain's docstring explains
    why that accrues a flattering fake score, and these two are what
    everything else is measured against, so the distortion would move every
    comparison on the page rather than one row of it.

    The actuals come from the reanalysis fetch, NOT from a stored log entry,
    which is why this patches the archive rather than simply skipping the
    seed — worth stating because getting that backwards is what made the
    first version of this test pass for the wrong reason."""
    empty = {"daily": {"time": []}}
    monkeypatch.setattr(open_meteo, "fetch_archive_single_day", lambda *a, **k: empty)
    monkeypatch.setattr(open_meteo, "fetch_archive_range", lambda *a, **k: empty)

    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    day0 = {p.model for p in scored_predictions(result.log_entry).day0}
    assert "persistence" not in day0
    assert "climatology" not in day0


def test_no_baseline_reaches_the_prompt_through_any_block(tmp_path):
    """Four blocks can leak a hidden model — the predictions, MODEL TRACK
    RECORD, the review findings, and the day-over-day comparison. The blend
    has been leaked through one of them before (see the note in
    _model_predictions_prompt_payload), so this asserts against the WHOLE
    prompt rather than any single section."""
    _seed_yesterday_log_entry(tmp_path, date(2026, 8, 10))
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    # Both calls' instructions. Whether a rule reached the forecaster is a
    # question about the run; WHICH of the two calls carries it is
    # tests/test_prompt_seam.py's.
    system_prompt, user_prompt = llm.system_prompts, llm.user_prompts
    for baseline in BASELINE_MODEL_IDS:
        assert baseline not in user_prompt, f"{baseline} leaked into the user prompt"
        assert baseline not in system_prompt, f"{baseline} leaked into the system prompt"


def test_a_re_issue_is_not_shown_a_baseline_either(tmp_path):
    """The re-issue path reads the day's STORED predictions, which DO contain
    the baselines. That is exactly how the blend leaked once."""
    _seed_yesterday_log_entry(tmp_path, date(2026, 8, 10))
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    _, user_prompt = llm.calls[0]
    for baseline in BASELINE_MODEL_IDS:
        assert baseline not in user_prompt


# ---------------------------------------------------------------------------
# ROADMAP item 53 — the position experiment, and making it readable
# ---------------------------------------------------------------------------


def test_the_forward_window_is_fetched_before_the_optional_extras(tmp_path, monkeypatch):
    """The experiment. The forward fetch failed on 8 of the last 9 runs while
    sitting 7th in the run's sequence of /v1/forecast requests, and the call
    that previously sat 7th failed the same way until it was deleted. Moving
    this one early is what distinguishes "the 7th request fails" from "this
    request fails"; one scheduled run answers it.

    Asserted as an ORDER rather than a comment, because a later refactor that
    quietly moves it back would silently invalidate the result and nobody
    would notice until the fallback started firing again."""
    seen: list[str] = []

    def _record(name, result):
        def _f(*a, **k):
            seen.append(name)
            return result
        return _f

    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_today",
                        _record("today", hourly_fixture()))
    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_forward",
                        _record("forward", forward_hourly_fixture()))
    monkeypatch.setattr(open_meteo, "fetch_forecast_daily_extended",
                        _record("daily", daily_fixture()))
    monkeypatch.setattr(open_meteo, "fetch_synoptic_pressure",
                        _record("synoptic", {"points": []}))

    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=True)

    assert seen.index("forward") < seen.index("daily")
    assert seen.index("forward") < seen.index("synoptic")
    # Still after the day-0 fetch, which it depends on: the reconciled clock
    # that trims the forward window comes from that response.
    assert seen.index("today") < seen.index("forward")


def test_a_failed_synoptic_fetch_is_recorded_rather_than_swallowed(tmp_path, monkeypatch):
    """It used to be `except Exception: synoptic = None` with no log at all,
    so a failure there was invisible in every surface — which is the exact
    shape of item 53's original incident. It also matters for the experiment
    above: moving the forward fetch early puts this call where the failures
    have been landing, and a silent failure there would waste the run."""
    monkeypatch.setattr(
        open_meteo, "fetch_synoptic_pressure", _raises(RuntimeError("Read timed out"))
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    codes = [d.code for d in result.log_entry.meta.degradations]
    assert "synoptic_unavailable" in codes


def test_a_working_synoptic_fetch_records_nothing(tmp_path):
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    assert "synoptic_unavailable" not in [
        d.code for d in result.log_entry.meta.degradations
    ]


def test_a_lost_forward_window_is_tried_once_more_later_in_the_run(tmp_path, monkeypatch):
    """Redundancy that does not require knowing the cause — ROADMAP item 53.

    The three retries inside `_get` all happen within one 94.5-second burst
    and all three timed out on every one of the eight failing runs, so more of
    the same shape buys nothing. This is a SPACED attempt instead: the rest of
    the run happens in between, which is the only variable the burst
    hypothesis says matters. The day-0 fallback stays the floor, so this can
    only ever improve on it."""
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Read timed out. (read timeout=30)")
        return forward_hourly_fixture()

    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_forward", _flaky)
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    assert calls["n"] == 2, "the second attempt must actually be made"
    # And having succeeded, the run is NOT degraded: the reader gets the full
    # window and the record must not claim otherwise.
    assert [d.code for d in result.log_entry.meta.degradations] == []


def test_the_retry_is_not_attempted_when_the_first_one_worked(tmp_path, monkeypatch):
    calls = {"n": 0}

    def _ok(*a, **k):
        calls["n"] += 1
        return forward_hourly_fixture()

    monkeypatch.setattr(open_meteo, "fetch_forecast_hourly_forward", _ok)
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    assert calls["n"] == 1


def test_both_attempts_failing_still_falls_back_and_says_so(tmp_path, monkeypatch):
    """The floor. Two failures must land exactly where one used to."""
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", _raises(RuntimeError("Read timed out"))
    )
    result = issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    assert "hours_ahead_narrowed" in [d.code for d in result.log_entry.meta.degradations]


def test_the_verification_block_hides_the_blend_and_the_baselines_too(tmp_path):
    """A FOURTH block the rule leaks through, and the one that reached
    production: PRE-COMPUTED VERIFICATION RESULTS.

    The existing tests above all run a FIRST day, which has nothing to
    verify — so verification_context is empty and the leak cannot appear in
    them. It needs a second run, with yesterday's forecast on disk to score.

    Found 2026-09-09 by the prompt harness, in the real archived payload for
    2026-09-08: per_model_scores carried olw_blend at Day+0 and persistence
    and climatology at all three lead times, while the system prompt in the
    same call told the model "Your own accuracy record is deliberately NOT in
    your context". The worker noticed, said so, and had to decide for itself
    what to do — which is the loop models_visible_to_the_forecaster exists to
    keep closed.
    """
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 12), dry_run=False)

    system_prompt, user_prompt = llm.calls[-1]
    assert "PRE-COMPUTED VERIFICATION RESULTS" in user_prompt
    # Guard the guard: an empty block would pass every assertion below
    # without exercising anything, which is exactly how this survived.
    assert '"per_model_scores"' in user_prompt
    assert any(m in user_prompt for m in MODELS), "no model was scored — the block is empty"

    for hidden in (BLEND_MODEL_ID, *BASELINE_MODEL_IDS):
        assert hidden not in user_prompt, f"the forecaster can see {hidden}'s scores"
        assert hidden not in system_prompt


def test_the_code_blend_is_stored_scored_and_never_shown(tmp_path, monkeypatch):
    """ROADMAP item 173, stage 2. The code blend votes only once its inputs
    have RAIN_WEIGHT_MIN_CHECKS verified days, so a two-day run would store
    none and every "not in the prompt" below would pass against nothing —
    the failure the blend's own rule suffered for weeks. Twelve days, then
    the blend is shown to exist on disk and in the record before its
    absence from the prompt means anything."""
    from dataclasses import replace

    # The fixture's range fetch returns one day, which empties the actuals
    # cache on the Monday batch; twelve days always include a Monday.
    def _whole_range(lat, lon, start, end, tz):
        hours = [archive_fixture(start + timedelta(days=i))["hourly"] for i in range((end - start).days + 1)]
        return {"hourly": {k: [v for h in hours for v in h[k]] for k in hours[0]}}

    monkeypatch.setattr(open_meteo, "fetch_archive_range", _whole_range)

    def deps(llm=None):
        # Two calls a day for twelve days is over the default cap of 20.
        return replace(make_deps(tmp_path, llm=llm), location=LOCATION.model_copy(update={"max_llm_calls_per_24h": 100}))

    days = [date(2026, 8, 11) + timedelta(days=i) for i in range(12)]
    for d in days[:-1]:
        issue(deps(), today=d, dry_run=False)

    stored = log_store.read_log_entry(tmp_path, days[-2])
    blend = [p for p in scored_predictions(stored).day0 if p.model == CODE_BLEND_MODEL_ID]
    assert len(blend) == 1, "the code blend was not stored in row 0"
    assert blend[0].rain is False
    assert blend[0].rain_probability_pct == 0

    llm = FakeLLMProvider()
    issue(deps(llm), today=days[-1], dry_run=False)

    record = track_record_store.read_track_record(tmp_path)
    scored = [e for e in record.entries if e.model == CODE_BLEND_MODEL_ID and e.lead_time_days == 0]
    assert scored and scored[0].all_time_checks >= 1, "the code blend was never scored"

    system_prompt, user_prompt = llm.calls[-1]
    assert '"per_model_scores"' in user_prompt
    assert CODE_BLEND_MODEL_ID not in user_prompt
    assert CODE_BLEND_MODEL_ID not in system_prompt

    # The same-day re-issue reads the STORED row, which now carries it.
    again = FakeLLMProvider()
    issue(deps(again), today=days[-1], dry_run=False)
    assert CODE_BLEND_MODEL_ID not in again.calls[-1][1]




def summarising_provider(summary: str) -> FakeLLMProvider:
    from openlocalweather.llm.schema import SkillProfileSummaryItem

    response = FakeLLMProvider()._default_response()
    response.skill_profile_summaries = [
        SkillProfileSummaryItem(model=MODELS[0], lead_time_days=0, summary=summary)
    ]
    return FakeLLMProvider(response)


def test_a_stored_summary_carrying_a_figure_never_comes_back(tmp_path):
    """Item 91. The summary's only consumer is the NEXT run's prompt, and by
    then the counts have advanced one verification cycle — measured
    2026-09-09, where 11 of 12 stored figures matched n-1 exactly. GFS Day+0
    said "63% all-time" against a stored 17/28, and 17/27 is 63%.

    A figure inside stored prose is therefore always read beside a fresher
    one, and a cold reader could not tell which side was right: it dropped
    every percentage rather than choose. So a figure must not be stored,
    and one already stored must not be fed back.
    """
    issue(
        make_deps(tmp_path, llm=summarising_provider(
            "At Day+0, strong on timing, though highs run 63% of the time too warm."
        )),
        today=date(2026, 8, 11), dry_run=False,
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 12), dry_run=False)

    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 13), dry_run=False)
    _, user_prompt = llm.calls[-1]

    assert "MODEL TRACK RECORD" in user_prompt
    # The column header, since the block is a table now — item 176. Still the
    # same guard: it stops the assertion below passing because the summary
    # column was absent altogether rather than because the figure was filtered.
    assert "\tskill_profile_summary" in user_prompt, "the block never carried one"
    assert "63% of the time too warm" not in user_prompt


def test_a_figureless_summary_is_kept(tmp_path):
    """A filter, not a delete. The qualitative picture is the whole value and
    survives a cycle intact — it is only the numbers that go stale."""
    clean = "At Day+0, strong on precip timing and pressure, with highs running warm."
    issue(
        make_deps(tmp_path, llm=summarising_provider(clean)),
        today=date(2026, 8, 11), dry_run=False,
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 12), dry_run=False)

    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 13), dry_run=False)

    assert clean in llm.calls[-1][1]


def test_a_forecast_survives_losing_the_seven_day_outlook(tmp_path):
    """ROADMAP items 51 and 79. On 2026-09-09 the extended daily fetch
    read-timed out three times and the whole run aborted — no forecast at all,
    though today's hourly guidance had already arrived and nothing about today
    was in doubt.

    A forecast for today without a seven-day outlook beats no forecast. The
    operator's call, and the machinery already existed: `degradations` carries
    exactly this shape of message.
    """
    deps = make_deps(tmp_path)
    real = pipeline.open_meteo.fetch_forecast_daily_extended

    def fail_extended(lat, lon, models, timezone, days=8):
        raise pipeline.open_meteo.OpenMeteoFetchError("Read timed out. (read timeout=30)")

    system_prompts = []
    # The narrative builder: the Extended Outlook section these tests read is
    # a narrative one, and since ROADMAP item 59 step 3 the judgment call does
    # not carry it at all.
    real_system = pipeline.build_narrative_prompt

    def spy(*args, **kwargs):
        built = real_system(*args, **kwargs)
        system_prompts.append(built)
        return built

    pipeline.open_meteo.fetch_forecast_daily_extended = fail_extended
    pipeline.build_narrative_prompt = spy
    try:
        result = issue(deps, today=date(2026, 8, 11), dry_run=False)
    finally:
        pipeline.open_meteo.fetch_forecast_daily_extended = real
        pipeline.build_narrative_prompt = real_system

    assert result is not None, "the run aborted rather than degrading"
    codes = {d.code for d in result.log_entry.meta.degradations}
    assert DEGRADATION_EXTENDED_OUTLOOK in codes

    # A CODE, not just prose. Item 51's whole sequence is reason, then count,
    # then report — and a degradation nothing can count is an accumulating
    # miss nobody will see.
    deg = next(d for d in result.log_entry.meta.degradations if d.code == DEGRADATION_EXTENDED_OUTLOOK)
    assert deg.summary and deg.detail
    assert "seven" in deg.summary.lower() or "extended" in deg.summary.lower()

    # AND THE FORECASTER IS TOLD. Wiring the flag through is the half that
    # can rot silently: the degraded run leaves "primary_extended_daily"
    # empty and the Day+3/Day+7 blocks empty, and a prompt that still asks
    # for the section "using the daily summary data" is asking for invention.
    # Asserted on the prompt the RUN built, not on build_system_prompt in
    # isolation, because what fails here is the wiring and not the wording.
    assert "THE EXTENDED GUIDANCE DID NOT ARRIVE" in system_prompts[0]
    assert "using the daily summary data" not in system_prompts[0]

    # AND IT SURVIVES THE ROUND TRIP TO DISK. check_recent_degradations reads
    # the COMMITTED log, so an in-memory code that never lands on disk is a
    # miss that accumulates invisibly — the exact failure this degradation was
    # added to prevent.
    written = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert DEGRADATION_EXTENDED_OUTLOOK in {d.code for d in written.meta.degradations}
    assert DEGRADATION_EXTENDED_OUTLOOK in {
        d.code for issuance in written.issuance_log() for d in issuance.degradations
    }


def test_the_lake_losing_its_outlook_degrades_too(tmp_path):
    """The SAME endpoint and the same outage. The secondary point's extended
    daily is fetched from Open-Meteo exactly as the primary's is, so a read
    timeout takes both or either — and wrapping only the primary would leave
    half the calls to the fragile endpoint still able to abort a run that has
    today's guidance in hand.

    `secondary_daily` is already `dict | None` because a location can have no
    secondary point at all, so None is a state every consumer downstream
    already handles.

    THE SECONDARY POINT HAS TO BE TURNED ON HERE. Module LOCATION disables it
    to keep fixtures simple, and against that fixture this test passed while
    fetching nothing at all — a guard that never runs, tested against an
    input that never reaches it.
    """
    location = LOCATION.model_copy(
        update={
            "secondary_point": SecondaryPoint(
                enabled=True, name="Test Lake", section_label="Conditions for Boaters", lat=1.5, lon=2.5
            )
        }
    )
    from dataclasses import replace

    deps = replace(make_deps(tmp_path), location=location)

    real = pipeline.open_meteo.fetch_forecast_daily_extended
    attempted: list[tuple[float, float]] = []

    def fail_secondary_only(lat, lon, models, timezone, days=8):
        attempted.append((lat, lon))
        if (lat, lon) == (location.primary_point.lat, location.primary_point.lon):
            return real(lat, lon, models, timezone, days)
        raise pipeline.open_meteo.OpenMeteoFetchError("Read timed out. (read timeout=30)")

    system_prompts = []
    # The narrative builder: the Extended Outlook section these tests read is
    # a narrative one, and since ROADMAP item 59 step 3 the judgment call does
    # not carry it at all.
    real_system = pipeline.build_narrative_prompt

    def spy(*args, **kwargs):
        built = real_system(*args, **kwargs)
        system_prompts.append(built)
        return built

    pipeline.open_meteo.fetch_forecast_daily_extended = fail_secondary_only
    pipeline.build_narrative_prompt = spy
    try:
        result = issue(deps, today=date(2026, 8, 11), dry_run=False)
    finally:
        pipeline.open_meteo.fetch_forecast_daily_extended = real
        pipeline.build_narrative_prompt = real_system

    assert (location.secondary_point.lat, location.secondary_point.lon) in attempted, (
        "the secondary extended fetch was never attempted, so nothing was tested"
    )
    assert result is not None, "the run aborted rather than degrading"
    codes = {d.code for d in result.log_entry.meta.degradations}
    assert DEGRADATION_SECONDARY_EXTENDED_OUTLOOK in codes

    # A DIFFERENT CODE FROM THE PRIMARY'S, and this is the reason for the
    # split rather than tidiness. The prompt's "the extended guidance did not
    # arrive" switch is derived from the primary code, so sharing one code
    # would let the lake's outlook failing suppress a perfectly good
    # seven-day outlook for the town.
    assert DEGRADATION_EXTENDED_OUTLOOK not in codes
    assert "THE EXTENDED GUIDANCE DID NOT ARRIVE" not in system_prompts[0]
    assert "using the daily summary data" in system_prompts[0]


def test_losing_today_is_still_fatal(tmp_path):
    """Degrading is not the same as tolerating anything. Today's hourly
    guidance IS the forecast; a run without it has nothing to say and must
    still abort rather than publish a confident silence."""
    deps = make_deps(tmp_path)
    real = pipeline.open_meteo.fetch_forecast_hourly_today

    def fail_today(lat, lon, models, timezone):
        raise pipeline.open_meteo.OpenMeteoFetchError("Read timed out. (read timeout=30)")

    pipeline.open_meteo.fetch_forecast_hourly_today = fail_today
    try:
        with pytest.raises(pipeline.open_meteo.OpenMeteoFetchError):
            issue(deps, today=date(2026, 8, 11), dry_run=False)
    finally:
        pipeline.open_meteo.fetch_forecast_hourly_today = real


def notes_block(user_prompt: str) -> str:
    """Just the HISTORICAL NOTES section. The date the correction ran is also
    a run timestamp elsewhere in the payload, so an unscoped assertion for it
    passes whether or not a single marker was emitted — which is how the first
    version of these tests passed."""
    start = user_prompt.index("HISTORICAL NOTES")
    return user_prompt[start : user_prompt.index("\nLONG-RUN REVIEW", start)]


def _mark_a_note_corrected(tmp_path, d: date) -> str:
    """Stamp one stored note the way tools/fix_note_signs.py does."""
    entry = log_store.read_log_entry(tmp_path, d)
    assert entry is not None and entry.verification.day0.note, (
        "no Day+0 note on this day — the test would pass against nothing"
    )
    entry.verification.day0.note_sign_corrected_on = date(2026, 9, 10)
    log_store.write_log_entry(tmp_path, entry)
    return entry.verification.day0.note





def test_the_entry_records_how_the_call_ended(tmp_path):
    """ROADMAP item 100. A run returned HTTP 200 in 54.5s and published a UV
    Index of 15,930 characters; the ledger recorded the status and the
    elapsed time, which is a different question, and nothing recorded why the
    model stopped or what it spent. When the question came the record could
    not answer it."""
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    meta = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta
    assert meta.finish_reason == "STOP"
    assert meta.input_tokens == 41_000
    assert meta.output_tokens == 2_100


def test_a_re_issue_records_its_own_call_not_the_mornings(tmp_path):
    """Both pipelines, and the values must be THIS issuance's — the same rule
    the prompt hash follows one line above them, for the same reason: the
    entry describes the forecast currently in it.

    Paired because this file's divergences all look alike — the last one was
    the historical-notes projection, where only run_daily_pipeline had the
    change AND only run_daily_pipeline had the test.
    """
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    morning = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta
    assert morning.output_tokens == 2_100

    evening = FakeLLMProvider()
    evening.finish_reason = "MAX_TOKENS"
    evening.output_tokens = 8_192
    issue(
        make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False
    )

    meta = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta
    assert meta.finish_reason == "MAX_TOKENS", "the morning's value survived the re-issue"
    assert meta.output_tokens == 8_192
    # And the fields that are deliberately the MORNING's are still the
    # morning's — the re-issue rewrites the narrative, not the day's history.
    assert meta.generated_at_utc == morning.generated_at_utc


def test_a_provider_that_reports_nothing_leaves_the_fields_unset(tmp_path):
    """None means "did not say", never zero — a provider outside this repo
    need not implement the hook, and an entry from before the field existed
    reads identically."""
    llm = FakeLLMProvider()
    llm.after_response = None

    class Silent(FakeLLMProvider):
        def generate(self, system_prompt, user_prompt, response_schema):
            if self.before_attempt is not None:
                self.before_attempt()
            self.calls.append((system_prompt, user_prompt))
            return self.response

    issue(make_deps(tmp_path, llm=Silent()), today=date(2026, 8, 11), dry_run=False)

    meta = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta
    assert meta.finish_reason is None
    assert meta.input_tokens is None
    assert meta.output_tokens is None
    assert meta.response_schema_sha256 is None
    # None, NOT [] — "the provider did not say" and "the schema marked
    # nothing nullable" are different answers, and the second is a real one.
    assert meta.nullable_fields is None


def test_a_failed_write_up_still_publishes_the_scored_call(tmp_path):
    """ROADMAP item 59 step 3, and the cost the split introduced.

    The judgment call decides the numbers the record SCORES; the rendering
    call only writes them up. Losing the second used to lose the first too,
    because the merge happened after both returned — so a provider blip in
    the ~60s between them threw away a complete, paid-for forecast and left
    the day unscored.

    A day with numbers and no prose is a degraded forecast. A day with
    neither is a hole in the accuracy record, and the record is the thing
    this project is for.
    """
    llm = FakeLLMProvider()
    llm.fail_narrative = LLMResponseError("Gemini request failed after 4 attempts")

    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    # The scored call survived, in full — the stored entry flattens
    # today_properties onto itself, so these ARE the judgment call's numbers.
    assert entry.temp_high_c is not None
    assert entry.rain_expected
    assert entry.meta is not None

    # And the record says the day is degraded rather than normal.
    codes = {d.code for d in entry.meta.degradations or []}
    assert DEGRADATION_NARRATIVE in codes, codes

    # The prose says what happened instead of pretending to be a forecast.
    assert entry.narrative_markdown, "an empty narrative reads as a missing section"
    assert "could not be written" in entry.narrative_markdown.lower()

    # AND THE DAY IS SCORED. This is the whole justification for degrading
    # rather than aborting: the blend's row is in the record beside the
    # models, so tomorrow's verification has something to check.
    blend = [p for p in scored_predictions(entry).day0 if p.model == BLEND_MODEL_ID]
    assert len(blend) == 1, "the forecaster's own scored row is missing"
    assert blend[0].rain is not None
    assert blend[0].high_c is not None


@pytest.mark.parametrize("policy, fallback_calls", [("scored_call", 1), ("both_calls", 2)])
def test_the_configured_fallback_policy_reaches_the_run(tmp_path, policy, fallback_calls):
    """`llm_fallback_calls` read by a real issuance, not only stored —
    ROADMAP item 180. With the primary down for both calls, the fallback is
    sent the judgment alone under `scored_call`, and the day publishes
    scored and degraded; under `both_calls` it is sent both, as before.
    Either way the primary, having failed the scored call, is not asked for
    the narrative, and a degraded day's record names that failure."""
    import dataclasses

    from openlocalweather.llm.errors import LLMUnavailableError
    from openlocalweather.llm.fallback import FallbackProvider

    primary = FakeLLMProvider()
    primary.fail_judgment = LLMUnavailableError("503")
    primary.fail_narrative = LLMUnavailableError("503")
    fallback = FakeLLMProvider()
    deps = dataclasses.replace(
        make_deps(tmp_path, llm=FallbackProvider([primary, fallback])),
        location=LOCATION.model_copy(update={"llm_fallback_calls": policy}),
    )

    issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert len(fallback.calls) == fallback_calls
    assert len(primary.calls) == 1
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    degraded = [d for d in entry.meta.degradations or [] if d.code == "narrative_unavailable"]
    assert bool(degraded) == (policy == "scored_call")
    if degraded:
        assert "(fake-model) failed the scored call" in degraded[0].detail


def test_a_failed_judgment_call_still_aborts_the_whole_run(tmp_path):
    """The degradation above is deliberately ONE-SIDED.

    There is nothing to publish without the scored call — a page of prose
    around numbers that were never decided is not a degraded forecast, it is
    an invented one. So the judgment call failing aborts exactly as it did
    before the split.
    """
    llm = FakeLLMProvider()
    llm.fail_judgment = LLMResponseError("Gemini request failed after 4 attempts")

    with pytest.raises(LLMResponseError):
        issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    assert log_store.read_log_entry(tmp_path, date(2026, 8, 11)) is None


def test_a_first_issuance_records_that_it_had_nothing_to_move_from(tmp_path):
    """ROADMAP item 104, C2 — stage 2b records the signals, acts on none.

    The day's first run IS the trigger, so the other two have no basis: there
    is no previous cycle to be newer than and no standing call to contradict.
    Recorded as None rather than False, the same three-valued rule the rest of
    this record follows.
    """
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    moved = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta.information_moved
    assert moved is not None, "the signals must be recorded even on a first run"
    assert moved.first_issuance_of_day is True
    assert moved.guidance_is_newer is None, "no previous issuance to compare against"
    # None, not []. This fixture's station does not answer, and the two mean
    # different things: None is "nothing was looked at", [] is "looked, and
    # nothing contradicts the call". Recording the second on the strength of
    # the first is the error class that cost a published forecast on
    # 2026-08-29.
    assert moved.observation_disagreements is None


def test_a_later_issuance_records_all_three_signals(tmp_path):
    """And a re-issue has a basis for all of them."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    issue(
        make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False
    )

    moved = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta.information_moved
    assert moved is not None
    assert moved.first_issuance_of_day is False
    # The fixture's guidance does not advance between the two runs, so this is
    # the interesting value rather than an incidental one: nothing moved.
    assert moved.guidance_is_newer is False


def test_a_station_that_answers_and_agrees_records_an_EMPTY_list(tmp_path, monkeypatch):
    """The distinction the record has to keep, and the one an empty fixture
    cannot prove.

    Every other pipeline test here runs with a station that does not answer,
    so `observation_disagreements` is None in all of them and the computation
    never actually runs. That is the shape of guard this project has been
    burned by before — one that passes because its input was empty. This test
    makes the station answer.
    """
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
            {d: StationWeather(thunder=False, precipitation=False) for d in (start, end)},
            None,
        ),
    )
    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    moved = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta.information_moved
    assert moved.observation_disagreements == [], "looked, and nothing contradicted"


def test_rain_seen_while_the_standing_call_said_dry_is_recorded(tmp_path, monkeypatch):
    """C2's third trigger firing end to end, through the real pipeline.

    The morning call has to say dry for this to mean anything, which the
    fixture's canned response does, and the station then reports rain.
    """
    # Flipped between the two runs rather than patched once: the morning must
    # see a dry station (or it would have no reason to call dry), and the
    # evening must see rain. Patching only before the refresh would leave the
    # morning making a real HTTP call.
    raining = {"now": False}
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
            {
                d: StationWeather(thunder=False, precipitation=raining["now"])
                for d in (start, end)
            },
            None,
        ),
    )

    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    raining["now"] = True
    refresh = make_deps(tmp_path)
    refresh.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    issue(refresh, today=date(2026, 8, 11), dry_run=False)

    moved = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta.information_moved
    assert DISAGREEMENT_RAIN_WHILE_DRY in (moved.observation_disagreements or [])


def test_force_spends_whatever_the_signals_say(tmp_path):
    """The signals stopped being inert in ROADMAP item 121 — an unforced
    trigger that finds nothing moved now refreshes its observations and buys
    no call. `force` is the override, and this pins that it still reaches
    past a `guidance_is_newer` of False to both calls and a publish.

    THIS TEST USED TO ASSERT THE OPPOSITE and was renamed rather than
    deleted: it read "the signals change nothing yet", which was true from
    item 104 stage 2b until item 121 wired them. The forced path is what is
    left of the behaviour it was guarding."""
    from openlocalweather.spend import read_ledger

    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    before = len(read_ledger(tmp_path))

    issue(
        make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False
    )

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert entry.meta.information_moved.guidance_is_newer is False
    assert len(read_ledger(tmp_path)) == before + 2, "the refresh still made both calls"
    assert entry.meta.refreshed_at is not None, "and still published"


def test_the_entry_records_which_schema_permitted_the_answer(tmp_path):
    """ROADMAP items 59 and 102. Three optional fields went empty for three
    runs and filled on a re-run of the same input. Deciding whether the
    schema had anything to do with it means reading what was SENT beside
    what came back, and item 102 found the record could not say."""
    llm = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=llm), today=date(2026, 8, 11), dry_run=False)

    meta = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta
    assert meta.response_schema_sha256 == "a" * 64
    assert meta.nullable_fields == ["/today_properties/mslp_trend_24h"]


def test_a_re_issue_records_the_schema_it_used(tmp_path):
    """Paired with the prompt hash and the finish reason for the reason this
    file keeps pairing them: every divergence found here so far has been a
    change that landed in run_daily_pipeline and not in run_refresh_pipeline,
    with the test following the change rather than the pair."""
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    evening = FakeLLMProvider()
    evening.response_schema_sha256 = "b" * 64
    evening.nullable_fields = ("/today_properties/air_quality_aqi",)
    issue(
        make_deps(tmp_path, llm=evening), today=date(2026, 8, 11), dry_run=False
    )

    meta = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).meta
    assert meta.response_schema_sha256 == "b" * 64, "the morning's schema survived the re-issue"
    assert meta.nullable_fields == ["/today_properties/air_quality_aqi"]


# --- ROADMAP item 121, the no-LLM refresh path -----------------------------
#
# The saving item 121 exists for. Everything above this line spends an LLM
# call on every issuance; these pin the case where it must not.


def _station_seeing(raining: dict):
    """A station whose report the test can flip between two runs.

    Flipped rather than patched once because a morning that already saw rain
    has no dry call to update — see the disagreement test above, which had to
    learn the same thing.
    """
    return lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
        {d: StationWeather(thunder=False, precipitation=raining["now"], reported_through="05:45") for d in (start, end)},
        None,
    )


def _with_station(tmp_path, llm=None):
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    return deps


def test_no_new_cycle_means_no_llm_call(tmp_path):
    """ROADMAP item 121's saving, realised.

    An hourly cron whose guidance carries the SAME model cycle as the last
    issuance has nothing new to reason about. What it does have — what the
    station has seen since — is composed in code, so the run refreshes that
    and spends nothing.
    """
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    quiet = FakeLLMProvider()
    refresh(make_deps(tmp_path, llm=quiet), date(2026, 8, 11))

    assert quiet.calls == [], "an observation-only update must not reach the model"


def test_an_observation_only_update_leaves_the_forecast_exactly_as_it_was(tmp_path):
    """The narrative and the scored numbers belong to the run that reasoned
    them, and an update that did no reasoning must not touch either.

    Checked as whole objects rather than field by field: this is the
    invariant the accuracy record rests on, and a list of fields can only
    catch the ones somebody remembered to list.
    """
    today = date(2026, 8, 11)
    issue(make_deps(tmp_path), today=today, dry_run=False)
    before = log_store.read_log_entry(tmp_path, today)

    refresh(make_deps(tmp_path, llm=FakeLLMProvider()), today)
    after = log_store.read_log_entry(tmp_path, today)

    assert after.narrative_markdown == before.narrative_markdown
    assert after.prediction_rows == before.prediction_rows
    assert after.verification == before.verification
    assert after.yesterday_verification_summary == before.yesterday_verification_summary
    assert after.model_predictions == before.model_predictions


def test_an_observation_only_update_still_refreshes_what_the_station_saw(tmp_path, monkeypatch):
    """The whole point of the path: a reader learns it has started raining
    without anybody paying a model to say so."""
    raining = {"now": False}
    monkeypatch.setattr(pipeline.metar_fetch, "observed_station_data", _station_seeing(raining))

    today = date(2026, 8, 11)
    issue(_with_station(tmp_path), today=today, dry_run=False)
    assert log_store.read_log_entry(tmp_path, today).observed_so_far.precipitation is False

    raining["now"] = True
    quiet = FakeLLMProvider()
    refresh(_with_station(tmp_path, llm=quiet), today)

    assert quiet.calls == []
    assert log_store.read_log_entry(tmp_path, today).observed_so_far.precipitation is True
    # And how far the station's reports reach — item 151's reach, stored so
    # a reader of the entry knows which hours "so far" covers.
    assert log_store.read_log_entry(tmp_path, today).observed_so_far.reported_through == "05:45"


def test_a_new_cycle_still_spends_the_call(tmp_path):
    """The gate is about NEW MODEL DATA. When a cycle lands, the forecast is
    re-reasoned exactly as it always was — this path is a saving, not a cap.
    """
    today = date(2026, 8, 11)
    issue(make_deps(tmp_path), today=today, dry_run=False)

    # Age the stored cycle so this run's guidance is genuinely newer, through
    # the real comparison rather than around it.
    entry = log_store.read_log_entry(tmp_path, today)
    entry.guidance_initialised_at = entry.guidance_initialised_at - timedelta(hours=6)
    log_store.write_log_entry(tmp_path, entry)

    spender = FakeLLMProvider()
    issue(make_deps(tmp_path, llm=spender), today=today, dry_run=False)

    assert spender.calls != [], "a new cycle is what the LLM call is FOR"


def test_the_always_policy_spends_on_every_issuance(tmp_path):
    """An operator who wants the daypart narrative refreshed on its own —
    C2's middle tier — declares it, and the gate steps aside. ROADMAP item
    120: the option is the operator's, on both sides."""
    today = date(2026, 8, 11)
    always = LOCATION.model_copy(update={"llm_refresh_policy": "always"})

    deps = make_deps(tmp_path)
    deps.location = always
    issue(deps, today=today, dry_run=False)

    spender = FakeLLMProvider()
    again = make_deps(tmp_path, llm=spender)
    again.location = always
    issue(again, today=today, dry_run=False)

    assert spender.calls != []


# --- ROADMAP item 104, contract item 2: the issuance window ----------------


def _clock_at(monkeypatch, when):
    """Freeze the issuance clock inside the fixtures' own dates.

    The window is sliced from the issuance forward, so it is empty unless the
    guidance actually covers the moment the run happens — which for fixtures
    dated 2026-08-11 means the run has to think it is 2026-08-11. Both seams,
    because `_sun_context` re-derives the moment through `reconcile_now` and
    would put the real clock back.
    """
    monkeypatch.setattr(pipeline, "now_in_tz", lambda tz: when)
    monkeypatch.setattr(pipeline, "reconcile_now", lambda local, header, offset: (when, None))


def test_the_row_carries_a_window_prediction_beside_day_zero(tmp_path, monkeypatch):
    """Contract item 2, accumulating. Every issuance stores what the models
    say about the next 24 hours from ITS moment, beside the calendar-day
    Day+0 the record still scores."""
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 0))
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    row = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0]

    assert row.window_predictions, "the window is the point of contract item 2"
    assert row.predictions.day0, "and Day+0 is still stored beside it"


def test_the_window_cannot_reach_what_is_scored(tmp_path, monkeypatch):
    """THE SAFETY PROPERTY, and the reason the window is a separate field.

    `fetch_forecast_hourly_forward` is kept away from scoring on purpose —
    "widening that fetch to two days would silently score 48 hours as today".
    The window crosses that fence; the scored set must not. Pinned by
    identity: what tomorrow scores comes from the day-0 fetch and is byte for
    byte what the extractor makes of it.
    """
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    from openlocalweather.extract import extract_day0_predictions_from_hourly

    expected = extract_day0_predictions_from_hourly(hourly_fixture(), MODELS)
    scored = scored_predictions(entry).day0
    by_model = {p.model: p for p in scored}

    for want in expected:
        got = by_model[want.model].model_dump()
        # `target_date` is stamped by the pipeline at assembly rather than by
        # the extractor — contract item C1 — so it is the one field that
        # legitimately differs from a bare extraction, and it is asserted
        # rather than skipped.
        assert got.pop("target_date") == date(2026, 8, 11)
        want_fields = want.model_dump()
        want_fields.pop("target_date")
        assert got == want_fields, (
            f"{want.model}'s scored Day+0 moved when the window was added"
        )


def test_a_run_that_cannot_fill_the_window_stores_no_claim(tmp_path, monkeypatch):
    """The degraded path. When the two-day fetch fails the run falls back to
    today's hours only, which late in the day is a handful — and six hours
    stored as a 24-hour claim would be scored against 24 hours of weather the
    models were never asked about."""
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: {"hourly": {"time": []}}
    )
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    row = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0]

    assert row.window_predictions == [], "an unfillable window is an absence, not a forecast"
    assert row.predictions.day0, "and the day's scored numbers are unaffected"


def test_an_unreadable_sunset_never_promotes_an_afternoon():
    """ROADMAP item 104, contract item 8. `_sunset_hour` returns None rather
    than a default, and the asymmetry with `_issued_hour`'s 24 is the point:
    that returns a value LATER than any onset so an unknown clock suppresses a
    timing phrase, this returns None so an unknown sunset suppresses the
    evening subject. Both resolve toward saying less."""
    from openlocalweather.pipeline import _sunset_hour

    class Issuance:
        def __init__(self, sunset):
            self.sunset = sunset

    assert _sunset_hour(None) is None
    assert _sunset_hour(Issuance(None)) is None
    assert _sunset_hour(Issuance("")) is None
    assert _sunset_hour(Issuance("not a time")) is None
    assert _sunset_hour(Issuance("18:47")) == 18


def test_an_evening_issuance_compares_tomorrow_against_today(tmp_path, monkeypatch):
    """ROADMAP item 104, contract item 8, stage 2 — the whole wiring, driven.

    Three inputs have to reach `compute_day_over_day` for the evening subject
    to exist, and each of them was passed by nothing until now: the sunset the
    gate pivots on, tomorrow's predictions, and a baseline for today. Every
    one of them defaults to None in the function, so a missing wire renders as
    a comparison that is simply absent — which is also what a real gap looks
    like. That is the defect class `_locked_blocks` exists to close, and the
    reason this test drives the pipeline rather than the function.

    Mutating any of the three call-site arguments to None survived the whole
    suite before this existed.
    """
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
            {d: StationWeather(thunder=False, precipitation=False) for d in (start, end)},
            {d: StationReadings(high_c=31.8, low_c=19.4, peak_wind_kmh=24.0) for d in (start, end)},
        ),
    )
    # 20:05, after the fixture's 18:47 sunset. At 18:00 the gate is silent,
    # which is why this deployment's own schedule does not reach here — the
    # consumer that does is the app, where a tap can come after dark.
    _clock_at(monkeypatch, datetime(2026, 8, 11, 20, 5))
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    # ROADMAP item 127 closed the gap this used to read around: the comparison
    # is now stored on the row, so this asserts BOTH — that the forecaster was
    # handed the sentence, and that the record kept the one it was handed.
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    stored = entry.prediction_rows[0].day_over_day
    assert stored is not None, "the comparison never reached the record"
    assert "than today (Tuesday) was" in stored.overview_comparison
    # The dimensions no instrument here can pair are absent in the RECORD too,
    # not merely unsaid in the prose.
    assert stored.wind_label is None
    assert stored.cloud_label is None
    assert stored.rain_contrast is None
    assert stored.provenance["high_c"] == "metar_station"

    # AND THE SENTENCE NO LONGER REACHES THE FORECASTER — item 159 step 5,
    # 2026-09-22. It is still composed and still stored on the row above,
    # because the record is the thing worth keeping; what changed is that the
    # Overview it was written to open does not exist, and the comparison now
    # reaches the reader as tile modifiers beside the numbers they concern.
    # Asserting its ABSENCE here is what stops it drifting back in: the block
    # is still handed over, with three booleans in it.
    prompt = llm.user_prompts
    assert "than today (Tuesday) was" not in prompt, (
        "the composed comparison sentence is back in the prompt"
    )
    assert "DAY-OVER-DAY COMPARISON" in prompt, "the booleans went with it"
    assert "yesterday_rain" in prompt


def test_the_morning_comparison_is_kept_on_the_row(tmp_path, monkeypatch):
    """ROADMAP item 127. Computed every run since item 23 and thrown away.

    Until this landed, nothing could measure whether the Overview used the
    sentence it was ordered to use verbatim, because the sentence was nowhere
    in the record to compare the published prose against. Item 126 was only
    answerable because the published gust IS stored; this is the same question
    one field over.

    Asserted through the PIPELINE rather than on `compute_day_over_day`,
    because the gap was never in the computation — it was the wiring, and a
    unit test of the function would have passed throughout.
    """
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 0))
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    row = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0]

    assert row.day_over_day is not None, "the comparison never reached the record"
    # The OPERANDS, not the sentence. On this fixture nothing moved on any
    # dimension and the rain was unchanged, so `describe_day_over_day`
    # composes nothing — which is a legitimate outcome and exactly the case a
    # record has to keep, because "the Overview said nothing" and "the
    # comparison found nothing to say" are different facts and only the stored
    # operands tell them apart.
    assert row.day_over_day.yesterday_high_c == 26.0
    assert row.day_over_day.provenance["high_c"] == "era5_archive"
    # A SIBLING of the scored set, never a member of it — the firewall is the
    # nesting, and `verify.scoring` names `row.predictions` as what is scored.
    assert not hasattr(row.predictions, "day_over_day")


def test_the_stored_comparison_carries_both_gusts(tmp_path, monkeypatch):
    """Item 127's other reason: the raw and the calibrated consensus are both
    computed here and were both discarded. A calibration that cannot be
    audited afterwards is item 126's finding waiting to happen again."""
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 0))
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    stored = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0].day_over_day

    # Present as FIELDS whatever their values: a thin record has no measured
    # bias yet, and the point is that the pair is re-derivable at all.
    assert hasattr(stored, "today_consensus_peak_wind_kmh")
    assert hasattr(stored, "today_calibrated_peak_wind_kmh")


def _models_and_yardsticks(row):
    """The row's Day+0 as the run held it before storage — the models alone,
    and the models with persistence and climatology joined. The two blends
    are added at storage and belong to neither."""
    live = [p for p in row.predictions.day0 if p.model not in {BLEND_MODEL_ID, CODE_BLEND_MODEL_ID}]
    models = [p for p in live if p.model not in BASELINE_MODEL_IDS]
    assert {p.model for p in live} - {p.model for p in models} == set(BASELINE_MODEL_IDS), (
        "the fixture must produce both yardsticks, or nothing here is tested"
    )
    return models, live


def test_the_day_over_day_consensus_is_the_models_alone(tmp_path, monkeypatch):
    """Persistence IS yesterday's observation, so averaging it into "today"
    pulls a comparison against yesterday toward no change by construction —
    item 104 recorded that 2026-09-13 and held the fix until the gust bias it
    was masking had its own correction, which item 126 shipped.

    Through the stored row rather than the function, because the defect was
    never in `compute_day_over_day`: it was which list the pipeline handed it.
    """
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 0))
    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    row = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0]
    models, with_yardsticks = _models_and_yardsticks(row)

    for field, stored in (
        ("high_c", row.day_over_day.today_consensus_high_c),
        ("wind_kmh", row.day_over_day.today_consensus_peak_wind_kmh),
    ):
        expected = round(mean([getattr(p, field) for p in models]), 1)
        assert round(mean([getattr(p, field) for p in with_yardsticks]), 1) != expected, (
            f"the fixture cannot tell the two lists apart on {field}"
        )
        assert stored == expected, f"{field}: the yardsticks are in the consensus"


def test_no_other_averaging_consumer_is_handed_a_yardstick(tmp_path, monkeypatch):
    """The other three consumers of the Day+0 mean. The extended trend
    measures days 1-3 of the MODELS against today, so a today with the
    yardsticks in compares two different populations: persistence is an
    observed gust and the models under-forecast theirs, which leaned the
    replayed trend toward "calmer" on 30 of 36 archived prompts. The
    calibrated gust and the sustained gap are each documented as a consensus
    of the models.
    """
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
            {d: StationWeather(thunder=False, precipitation=False) for d in (start, end)},
            {d: StationReadings(high_c=31.8, low_c=19.4, peak_wind_kmh=24.0) for d in (start, end)},
        ),
    )
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 0))

    seen = {}

    def spy(name, real):
        def wrapper(*args, **kwargs):
            seen[name] = (args, kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(pipeline, name, wrapper)

    spy("describe_extended_trend", pipeline.describe_extended_trend)
    spy("calibrated_gust_consensus", pipeline.calibrated_gust_consensus)
    spy("sustained_wind_gap", pipeline.sustained_wind_gap)

    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert set(seen) == {"describe_extended_trend", "calibrated_gust_consensus", "sustained_wind_gap"}

    for name, position in (("calibrated_gust_consensus", 0), ("sustained_wind_gap", 1)):
        handed = {p.model for p in seen[name][0][position]}
        assert not handed & set(BASELINE_MODEL_IDS), f"{name} was handed {handed}"

    row = log_store.read_log_entry(tmp_path, date(2026, 8, 11)).prediction_rows[0]
    models, with_yardsticks = _models_and_yardsticks(row)
    trend = seen["describe_extended_trend"][1]
    for kwarg, field in (("today_high_c", "high_c"), ("today_wind_kmh", "wind_kmh")):
        expected = mean([getattr(p, field) for p in models])
        assert mean([getattr(p, field) for p in with_yardsticks]) != expected
        assert trend[kwarg] == expected, f"{kwarg}: the yardsticks are in today's side"


def test_every_forecast_run_files_under_one_purpose(tmp_path):
    """ROADMAP item 137, the operator's decision 2026-09-16: every run is a
    fresh forecast, so the ledger uses one label for all of them.

    THE FIELD HELD THREE SPELLINGS FOR ONE ACTIVITY — "refresh", then
    "forecast-reissue", beside "forecast" — and the argument for keeping them
    apart was that a reader wants to know which issuance of the day a call
    belonged to. Every row carries `at`, so the ledger already answered that
    by counting rows within a date; the label was a weaker second copy of what
    the timestamp holds exactly.

    PINNED BECAUSE NOTHING PINNED THE OLD BEHAVIOUR. Collapsing the labels
    broke no test, which means the distinction had never been asserted and the
    collapse would not be either — the next person to reintroduce a second
    spelling would get a green suite.
    """
    from openlocalweather.spend import read_ledger

    issue(make_deps(tmp_path), today=date(2026, 8, 11), dry_run=False)

    later_llm = FakeLLMProvider(
        GeminiForecastResponse(
            yesterday_verification="n/a",
            skill_profile_summaries=[],
            today_properties=TodayProperties(
                rain=False, rain_expected="Still unlikely",
                temp_high_c=25.0, temp_low_c=17.0, temp_high_low="25°C / 77°F",
            ),
            today_narrative="## Overview\nA second forecast, later in the day.",
        )
    )
    issue(make_deps(tmp_path, llm=later_llm), today=date(2026, 8, 11), dry_run=False)

    purposes = [e.purpose for e in read_ledger(tmp_path)]
    assert purposes == ["forecast"] * 4, (
        f"a later run must file under the same label as the first: {purposes}"
    )


# --- the station's day readings, when they do not arrive — ROADMAP item 151 --


def test_a_station_that_returns_nothing_is_recorded_not_swallowed(monkeypatch, tmp_path):
    """ROADMAP item 151, found in production.

    `observed_so_far` was absent on 14 of 16 stored days with NOTHING on the
    record saying so: `_observed_so_far` had four exits and only the exception
    path printed, so "the station said nothing" and "we never got an answer"
    were indistinguishable afterwards. Three features depended on it — the
    OBSERVED SO FAR TODAY block, C2's third trigger, and item 143's divergence
    — and all three were quietly inert.

    The degradation is what makes the difference visible. It is the same
    machinery item 53.4 built for the forward hourly fetch, for the same
    reason: a stderr line inside a CI log is not somewhere anyone looks until
    a reader has been rained on.
    """
    from openlocalweather.config import load_location_config
    from openlocalweather.models import DEGRADATION_STATION_READINGS
    from openlocalweather.pipeline import _observed_so_far
    from openlocalweather.fetch import metar as metar_fetch

    location = load_location_config("config/location.yaml")

    # (what the fetch returns, why it is a gap)
    cases = {
        "the station had no rows at all": (None, None),
        "rows, but none covering today": ({}, {}),
    }
    for name, payload in cases.items():
        monkeypatch.setattr(
            metar_fetch, "observed_station_data", lambda *a, **k: payload
        )
        observed, gap = _observed_so_far(location, date(2026, 8, 11), tmp_path)

        assert observed is None, name
        assert gap is not None, f"{name}: returned None and said nothing"
        assert gap.code == DEGRADATION_STATION_READINGS, name
        assert gap.detail, f"{name}: no detail, so the two exits stay indistinguishable"

    # And the two exits must not produce the SAME detail, or the record still
    # cannot say which happened — which is the whole defect.
    details = set()
    for payload in cases.values():
        monkeypatch.setattr(
            metar_fetch, "observed_station_data", lambda *a, **k: payload
        )
        details.add(_observed_so_far(location, date(2026, 8, 11), tmp_path)[1].detail)
    assert len(details) == 2, f"both exits report the same thing: {details}"


def test_a_failed_archive_request_records_what_the_server_said(monkeypatch, tmp_path):
    """ROADMAP item 151, step 2. The evening exit used to claim "the request
    succeeded and the response was empty" for a 503 and a timeout alike. The
    fetch now raises with the status and the first line of the body, and the
    degradation carries that sentence into the record."""
    from openlocalweather.config import load_location_config
    from openlocalweather.models import DEGRADATION_STATION_READINGS
    from openlocalweather.pipeline import _observed_so_far
    from openlocalweather.fetch import metar as metar_fetch

    location = load_location_config("config/location.yaml")

    def unavailable(*a, **k):
        raise metar_fetch.ArchiveUnavailable("HTTP 503; first line: '<html>'")

    monkeypatch.setattr(metar_fetch, "observed_station_data", unavailable)
    observed, gap = _observed_so_far(location, date(2026, 9, 17), tmp_path)

    assert observed is None
    assert gap.code == DEGRADATION_STATION_READINGS
    assert "HTTP 503" in gap.detail
    assert "<html>" in gap.detail
    assert "succeeded" not in gap.detail


def test_a_fallback_to_stored_reports_is_used_and_declared(monkeypatch, tmp_path):
    """ROADMAP item 151, step 3. The archive failed but the store held the
    morning's rows: the forecast gets the observation, with its reach, AND a
    degradation that names the archive's reason — never the observation
    alone, which would present five o'clock as the day."""
    from openlocalweather.config import load_location_config
    from openlocalweather.fetch.metar import StationReadings, StationWeather
    from openlocalweather.models import DEGRADATION_STATION_STORED
    from openlocalweather.pipeline import _observed_so_far
    from openlocalweather.fetch import metar as metar_fetch

    location = load_location_config("config/location.yaml")
    today = date(2026, 9, 17)

    def from_the_store(icao, start, end, tz, data_dir=None, on_fallback=None):
        on_fallback("HTTP 503; first line: '<html>'")
        return (
            {today: StationWeather(thunder=False, precipitation=False, reported_through="05:45")},
            {today: StationReadings(high_c=21.0, low_c=20.0, peak_wind_kmh=5.56)},
        )

    monkeypatch.setattr(metar_fetch, "observed_station_data", from_the_store)
    observed, gap = _observed_so_far(location, today, tmp_path)

    assert observed is not None and observed.high_c == 21.0
    assert observed.reported_through == "05:45"
    assert gap is not None and gap.code == DEGRADATION_STATION_STORED
    assert "05:45" in gap.summary
    assert "HTTP 503" in gap.detail


def test_a_forecast_carries_both_the_stored_observation_and_its_degradation(tmp_path, monkeypatch):
    from openlocalweather.fetch.metar import StationReadings, StationWeather
    from openlocalweather.models import DEGRADATION_STATION_STORED

    today = date(2026, 8, 11)

    def from_the_store(icao, start, end, tz, data_dir=None, on_fallback=None):
        if on_fallback is not None:
            on_fallback("HTTP 503")
        return (
            {d: StationWeather(thunder=False, precipitation=True, reported_through="05:45") for d in (start, end)},
            {d: StationReadings(high_c=21.0, low_c=20.0, peak_wind_kmh=5.56) for d in (start, end)},
        )

    monkeypatch.setattr(pipeline.metar_fetch, "observed_station_data", from_the_store)
    issue(_with_station(tmp_path), today=today, dry_run=False)

    entry = log_store.read_log_entry(tmp_path, today)
    assert entry.observed_so_far.precipitation is True
    assert entry.observed_so_far.reported_through == "05:45"
    # Beside the fixture's own metar_unavailable, not instead of it.
    assert DEGRADATION_STATION_STORED in [d.code for d in entry.meta.degradations]


def test_no_station_configured_is_not_a_degradation(tmp_path):
    """A location with no station is running AS CONFIGURED. RunDegradation's
    own docstring draws this line, and blurring it makes the field mean
    nothing within a week."""
    from openlocalweather.config import load_location_config
    from openlocalweather.pipeline import _observed_so_far

    location = load_location_config("config/location.yaml").model_copy(
        update={"metar_station_icao": ""}
    )
    observed, gap = _observed_so_far(location, date(2026, 8, 11), tmp_path)
    assert observed is None and gap is None


# ---------------------------------------------------------------------------
# One archive request per run — ROADMAP item 151, 2026-09-20
# ---------------------------------------------------------------------------
def test_a_run_prefetches_the_station_once_for_every_reader(tmp_path, monkeypatch):
    """The run's one archive request, up front, covering yesterday's overlay
    padded behind and the same-day read padded ahead, with the current report
    beside it — so the three readers that follow make no request of their own."""
    calls = []
    monkeypatch.setattr(metar_fetch, "prefetch_station_rows",
                        lambda icao, start, end, data_dir, current_report=None: calls.append(
                            (icao, start, end, data_dir, current_report)))
    report = {"rawOb": "METAR HKKI 110300Z 07005KT CAVOK 22/16 Q1017", "reportTime": "2026-08-11T03:00:00.000Z", "temp": 22}
    monkeypatch.setattr(metar_fetch, "fetch_metar", lambda icao: [report])
    # The readers below the prefetch are stubbed as every station test stubs
    # them; this test is about the call above them.
    monkeypatch.setattr(
        pipeline.metar_fetch, "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, None),
    )
    deps = make_deps(tmp_path)
    deps.location = LOCATION.model_copy(update={"metar_station_icao": "HKKI"})

    issue(deps, today=date(2026, 8, 11))

    assert calls == [("HKKI", date(2026, 8, 9), date(2026, 8, 12), tmp_path, report)]


def test_a_deployment_without_a_station_does_not_prefetch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(metar_fetch, "prefetch_station_rows", lambda *a, **k: calls.append(a))
    issue(make_deps(tmp_path), today=date(2026, 8, 11))
    assert calls == []


def test_a_malformed_composed_phrase_is_dropped_rather_than_published(
    tmp_path, monkeypatch
):
    """ROADMAP item 158, 2026-09-21 — the runtime half of the shape check.

    The 09-20 and 09-21 defect was not a bad INPUT. `describe_extended_trend`
    was handed ordinary weather and composed an artefact out of it, and every
    layer below did its job: the prompt told the model to use the phrase
    verbatim, and the model did.

    So the fault is simulated where it actually occurred — at the composer's
    return — rather than by feeding the pipeline strange numbers. What this
    pins is that a malformed phrase does not reach the forecaster, and that
    dropping it leaves a mark rather than passing silently, which is the
    difference between a degradation and a bug nobody counts.
    """
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, {}),
    )
    monkeypatch.setattr(
        pipeline,
        "describe_extended_trend",
        lambda *a, **k: "much the same through Thursday, with , and showers",
    )
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert "with , and showers" not in llm.user_prompts, (
        "a malformed phrase reached the forecaster, which publishes it verbatim"
    )
    # AND THE DROP LEAVES NO HOLE. The block falls back to the absence line it
    # already has for a genuinely quiet span, which the Overview rules answer
    # with "omit it when it says Unavailable" — so a dropped phrase is a case
    # the prompt already knows how to handle rather than a new one.
    assert "Unavailable — omit the extended clause." in llm.user_prompts

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    marks = [d for d in (entry.meta.degradations or []) if d.code == DEGRADATION_COMPOSED_PHRASE]
    assert len(marks) == 1, "the drop was silent"
    assert "extended_trend" in marks[0].detail
    # The reason is named, because the next person's first question is which
    # artefact it was and the phrase itself is in the detail beside it.
    assert "space before ','" in marks[0].detail


def test_a_sound_phrase_is_passed_through_untouched(tmp_path, monkeypatch):
    """The half with teeth. A check that drops everything also passes this
    suite's malformed case, so the real sentence has to survive."""
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, {}),
    )
    monkeypatch.setattr(
        pipeline,
        "describe_extended_trend",
        lambda *a, **k: "temperatures much the same through Thursday, with "
                        "showers and thunderstorms likely each day",
    )
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    assert "showers and thunderstorms likely each day" in llm.user_prompts
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert not [
        d for d in (entry.meta.degradations or []) if d.code == DEGRADATION_COMPOSED_PHRASE
    ]


def test_an_overlong_tile_value_is_recorded_as_a_finding(tmp_path, monkeypatch):
    """ROADMAP item 7, and the wiring a mutation pass found untested.

    `overlong_display_values` had vectors on both sides and nothing proved the
    RUN consulted it: deleting the call from `_narrative_findings` left all
    1,503 tests green. The check is only worth having if what it finds reaches
    the record, so this drives the pipeline rather than the function.

    The value is the real one published on 2026-09-20, 149 characters of prose
    in a box the page renders as a tile.
    """
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, {}),
    )
    overlong = (
        "Dry conditions expected today with zero measurable accumulation, though "
        "scattered thunderstorm activity remains possible late afternoon into evening."
    )
    llm = FakeLLMProvider()
    llm.response = llm.response.model_copy(
        update={
            "today_properties": llm.response.today_properties.model_copy(
                update={"rain_expected": overlong}
            )
        }
    )
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    found = [f for f in (entry.meta.narrative_findings or []) if f.kind == CLAIM_DISPLAY_TOO_LONG]
    assert len(found) == 1, "the tile check never reached the run"
    assert found[0].quote == overlong
    assert "149 characters" in found[0].detail
    # THE RUN STILL PUBLISHES. This is a layout complaint, and a reader would
    # rather have an overlong tile than no forecast — the same call the
    # weekday check carries.
    assert entry.rain_expected == overlong


def test_a_tile_value_that_fits_leaves_no_finding(tmp_path, monkeypatch):
    """The half with teeth: a check that flagged the good month would report
    the regime this is meant to restore as the defect."""
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, {}),
    )
    llm = FakeLLMProvider()
    llm.response = llm.response.model_copy(
        update={
            "today_properties": llm.response.today_properties.model_copy(
                update={"rain_expected": "Isolated Evening Showers & Thunderstorms"}
            )
        }
    )
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))
    assert not [
        f for f in (entry.meta.narrative_findings or []) if f.kind == CLAIM_DISPLAY_TOO_LONG
    ]


# ---------------------------------------------------------------------------
# Which hourly block a clock-sensitive composer is given — 2026-09-21
# ---------------------------------------------------------------------------


def _hourly_with_directions(times: list[str], bearing: float) -> dict:
    fields: dict[str, list] = {"time": times}
    for model in MODELS:
        n = len(times)
        fields[f"precipitation_{model}"] = [0.0] * n
        fields[f"windgusts_10m_{model}"] = [10.0] * n
        fields[f"wind_gusts_10m_{model}"] = [10.0] * n
        fields[f"wind_speed_10m_{model}"] = [8.0] * n
        fields[f"temperature_2m_{model}"] = [22.0] * n
        fields[f"pressure_msl_{model}"] = [1012.0] * n
        fields[f"cloud_cover_{model}"] = [10.0] * n
        # The anchors that matter carry the bearing; the rest repeat it, so a
        # clause built from EITHER block is well formed and only its CONTENT
        # says which block it came from.
        fields[f"wind_direction_10m_{model}"] = [bearing] * n
    return {"hourly": fields}


def test_the_wind_shift_is_given_the_whole_day_not_the_forward_window(
    tmp_path, monkeypatch
):
    """THE WIRING, pinned behaviourally — and the reason it is worth pinning.

    `guidance.primary_hourly` is a `forecast_days=1` fetch covering one whole
    local day; `guidance.forward_hourly` is that same block trimmed to the
    hours ahead. They carry the SAME VARIABLES UNDER THE SAME NAMES, so
    `describe_wind_shift(guidance.forward_hourly, ...)` compiles, runs, and
    produces a plausible clause — in which the 03:00 anchor resolves to
    TOMORROW, because a forward window from 06:00 does not reach today's small
    hours. The clause would then describe a rotation running backwards in
    time and nothing would catch it: the vectors pass their own fixture and
    cannot see the wiring.

    I made exactly that mistake on 2026-09-21 while reading the archived
    prompt, which shows the forward window and not the day. The composer was
    correct; my reading was not. This test is what would have answered it in
    one line.
    """
    today_block = _hourly_with_directions(
        [f"2026-08-11T{h:02d}:00" for h in (0, 3, 6, 12, 18, 21)], 45.0
    )
    forward_block = _hourly_with_directions(
        [f"2026-08-11T{h:02d}:00" for h in (6, 12, 18)]
        + [f"2026-08-12T{h:02d}:00" for h in (0, 3, 6)],
        225.0,
    )
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_today", lambda *a, **k: today_block
    )
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_forward", lambda *a, **k: forward_block
    )
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, {}),
    )
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 1))
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    prompt = llm.user_prompts
    shift = prompt.split("WIND SHIFT")[1].split("\n")[1]
    # 45° is northeasterly and lives only in the whole-day block; 225° is
    # southwesterly and lives only in the forward window.
    assert "northeast" in shift, f"the wind shift was built from the wrong block: {shift}"
    assert "southwest" not in shift, shift

    # AND THE DIRECTION BLOCK TAKES THE SAME BLOCK — item 160, 2026-09-22.
    # Added because a mutation swapping it to the forward window survived the
    # whole suite. That is the same mistake as above, on a second consumer,
    # and I made it twice in one day: once on 2026-09-21 reading the archive,
    # and again on 09-22 measuring how often a bearing exists, where the
    # "overnight" figure was reading TOMORROW's 03:00 and had to be withdrawn.
    #
    # 45 degrees is northeasterly and lives only in the whole-day block; 225
    # is southwesterly and lives only in the forward window. The block is
    # keyed by anchor word, so this asserts the VALUES rather than the shape.
    directions = prompt.split("WIND DIRECTION")[1].split("\n\n")[0]
    assert "NE" in directions, f"the direction block was built from the wrong block: {directions}"
    assert "SW" not in directions, directions


def test_the_tiles_anchors_survive_the_entry_that_is_written(tmp_path, monkeypatch):
    """The anchors reach the DAY'S RECORD, not a field pydantic throws away.

    THE DEFECT THIS EXISTS FOR, found on 2026-09-21 by driving the pipeline
    rather than by any test. `cloud_anchors` and `wind_anchors` were declared
    on `IssuanceSnapshot` instead of `DailyLogEntry`, and pydantic's default
    `extra="ignore"` meant the pipeline's `DailyLogEntry(cloud_anchors=...,
    wind_anchors=...)` DISCARDED both without raising. 1,534 tests stayed
    green because every one of them called the composers directly, and the
    committed entry schema agreed with itself because it was generated from
    the wrong class.

    So this asserts the one thing a unit test cannot: that the value is still
    there after the entry has been built and read back off disk.
    """
    deps = make_deps(tmp_path)
    issue(deps, today=date(2026, 8, 11))

    stored = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))

    # the fixture's day: clear morning, overcast afternoon, wind backing from
    # southwest and easing after a late peak
    assert stored.cloud_anchors == [
        {"when": "early", "cover": "Clear"},
        {"when": "midday", "cover": "Mostly cloudy"},
        {"when": "evening", "cover": "Overcast"},
    ]
    assert stored.wind_anchors == [
        {"when": "early", "direction": "SW", "sustained_kmh": 8.0, "gust_kmh": 12.8},
        {"when": "midday", "direction": "SSW", "sustained_kmh": 22.0, "gust_kmh": 35.2},
        {"when": "evening", "direction": "S", "sustained_kmh": 18.0, "gust_kmh": 28.8},
    ]

    # and a snapshot of an EARLIER issuance must not carry them at all
    assert "cloud_anchors" not in IssuanceSnapshot.model_fields
    assert "wind_anchors" not in IssuanceSnapshot.model_fields


def test_the_index_halves_are_on_the_day_record(tmp_path, monkeypatch):
    """UV and AQI reach the record as a number, a word and a joined display.

    THE NUMBER IS STILL THE MODEL'S — its blended call across the models and
    the ground sensors, which is a judgement about which source to trust.
    THE WORD IS CODE'S, looked up in `scales.py` from the WHO and US EPA
    tables. The archive is why: 41 stored UV values in 4 shapes and 39 AQI
    values in TWENTY, ten of those a range rather than a number.

    Asserted off disk rather than on the model, because that is the check the
    anchor fields did not have when they spent a day being silently discarded
    by pydantic — see `test_the_tiles_anchors_survive_the_entry_that_is_written`.
    """
    deps = make_deps(tmp_path)
    issue(deps, today=date(2026, 8, 11))

    stored = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))

    assert stored.air_quality_index == 85
    assert stored.air_quality_aqi == "85 (Moderate)"

    # UV NO LONGER COMES FROM THE MODEL — item 161. It is taken from the
    # daily block for the day the horizon points at, and the record says
    # which source answered and for which date.
    assert stored.uv_index == 9.1
    assert stored.uv_index_max == "9.1 (Very high)"
    assert stored.uv_index_source == "gfs_seamless"
    assert stored.uv_index_date == date(2026, 8, 11)

    # and a snapshot of an EARLIER issuance carries the display, not the halves
    assert "uv_index" not in IssuanceSnapshot.model_fields
    assert "air_quality_index" not in IssuanceSnapshot.model_fields


def test_the_comparison_modifiers_reach_the_day_record(tmp_path, monkeypatch):
    """The tile's day-over-day modifier is COMPUTED AND STORED, not inert.

    THE THIRD COMPOSER IN THIS ITEM TO BE SHIPPED AND NEVER CALLED. Item 159
    step 1 built `comparison_modifiers` and `notable_moves`, pinned both with
    vectors on two sides, mutation-tested four ways — and nothing in the
    pipeline ever invoked either. Steps 2 and 3's anchors had the same fault
    for a day, from a different cause. A composer with no caller is green
    forever, so this asserts the value off disk.

    THE GATE IS THE STATION'S OWN TOP DECILE and it is usually shut: measured
    over the reference record on 2026-09-22 the gates are 2.2 °C, 10.5 km/h
    and 34.4 percentage points, so a modifier speaks on roughly a third of
    days across all three dimensions together. The fixture below moves the
    temperature well past its gate and leaves wind and cloud inside theirs,
    so the assertion is that ONE dimension speaks and the others stay quiet —
    which is the behaviour the sentence this replaces could never produce.
    """
    deps = make_deps(tmp_path)

    # THE RUN'S CLOCK IS PINNED, and before this test it was not. A
    # comparison exists only while the day is mostly ahead —
    # `comparison_subject` returns None once the local hour reaches
    # COMPARISON_MORNING_ENDS_HOUR (12) and before sunset — and this
    # fixture's location is UTC. So the test passed before noon UTC and
    # failed after it, every day, on unchanged code: CI was green at 10:45Z
    # on 2026-09-22 and red at 12:23Z on a commit that changed only a
    # markdown file.
    #
    # Pinning also makes the run COHERENT. `today` is 2026-08-11 while the
    # wall clock is whatever today happens to be, so the run was reasoning
    # about a day weeks in the past with a clock saying otherwise.
    # NAIVE, like the real one: `now_in_tz` returns wall-clock time with no
    # tzinfo because it is compared against Open-Meteo's naive local strings.
    monkeypatch.setattr(
        pipeline, "now_in_tz", lambda tz: datetime(2026, 8, 11, 8, 0)
    )

    # WITHOUT A RECORD, SILENCE. The cache the fixture starts with holds a
    # handful of days, which is fewer than the thirty pairs `notable_moves`
    # demands, so no dimension has a gate and none can speak.
    issue(deps, today=date(2026, 8, 11))
    assert log_store.read_log_entry(deps.data_dir, date(2026, 8, 11)).comparison == {}

    # WITH ONE, exactly the dimension that moved. Forty days whose own
    # day-to-day moves are small put the gates at 3.0 °C, 2.0 km/h and 4.0
    # points; the run's deltas are -0.3 °C and +20.2 km/h, so the wind clears
    # its gate tenfold and the temperature does not come close. They were
    # -0.2 and +14.4 while climatology, averaged over a record that is mostly
    # these forty calm days, sat in the consensus — see `day0_models`.
    cache = actuals_cache_store.read_actuals_cache(deps.data_dir)
    for i in range(40):
        day = date(2026, 6, 1) + timedelta(days=i)
        cache.primary[day.isoformat()] = DailyActual(
            date=day,
            high_c=25.0 + (i % 4),
            low_c=18.0,
            peak_wind_kmh=20.0 + (i % 3),
            cloud_cover_pct=40.0 + (i % 5),
            rain=False,
        )
    actuals_cache_store.write_actuals_cache(deps.data_dir, cache)

    issue(deps, today=date(2026, 8, 11))
    stored = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))

    assert stored.comparison == {"wind": "much windier"}, stored.comparison


def test_the_entry_carries_its_composed_tiles(tmp_path, monkeypatch):
    """The tiles ride on the record, for a consumer that cannot compose them.

    THE APPS SCRIPT MAILER IS THAT CONSUMER. It fetches `data/log/<date>.json`
    from raw.githubusercontent and renders it in JavaScript inside Google's
    infrastructure, so it cannot call `compose_tiles`. Publishing the composed
    tiles is what stops it needing a fourth implementation after Python, Dart
    and the page — and `mailer/test_mailer.js` records what that costs: the
    mailer ran twenty-three days behind the site because it had to know which
    fields the site showed.

    BOTH RETURN PATHS, which is the part worth a test. A later issuance merges
    into the existing entry, and its tiles must be recomposed from the merged
    result rather than carried over from the morning — so this issues twice
    and changes the rain call in between.
    """
    from openlocalweather.tiles import compose_tiles

    deps = make_deps(tmp_path)
    issue(deps, today=date(2026, 8, 11))

    stored = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))
    assert stored.tiles == compose_tiles(stored.model_dump(mode="json"), metric=True)
    assert [t["label"] for t in stored.tiles][:2] == ["High / Low", "Rain"]

    # a RE-ISSUE recomposes rather than inheriting
    deps.llm_provider.response.today_properties.rain_expected = "Heavy Rain All Day"
    issue(deps, today=date(2026, 8, 11))

    reissued = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))
    rain = next(t for t in reissued.tiles if t["label"] == "Rain")
    assert rain["lines"][0]["text"] == "Heavy Rain All Day", reissued.tiles


def test_the_direction_block_reaches_the_prompt_with_its_anchors(tmp_path, monkeypatch):
    """The per-anchor bearings are IN the prompt, not merely computable.

    A mutation feeding the block an empty map survived the whole suite, which
    is the same class as every other inert composer this item has produced:
    the function was right and nothing carried its answer.
    """
    today_block = _hourly_with_directions(
        [f"2026-08-11T{h:02d}:00" for h in (0, 3, 6, 12, 18, 21)], 225.0
    )
    monkeypatch.setattr(
        open_meteo, "fetch_forecast_hourly_today", lambda *a, **k: today_block
    )
    monkeypatch.setattr(
        pipeline.metar_fetch,
        "observed_station_data",
        lambda icao, start, end, tz, data_dir=None, on_fallback=None: ({}, {}),
    )
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 1))
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    block = llm.user_prompts.split("WIND DIRECTION")[1].split("\n\n")[0]

    assert '"midday": "SW"' in block, block


def test_the_prompt_says_which_ground_aqi_absence_it_found(tmp_path, monkeypatch):
    """The reason reaches the prompt — ROADMAP item 163.

    THE COMPOSER WAS RIGHT AND NOTHING CARRIED ITS ANSWER, which is the fault
    this item shares with three others in 159. A mutation deleting the
    pipeline's `last_known_absence(...)` argument left the whole suite green:
    the block fell back to its wiring-gap text and no assertion looked.

    THE SHAPE IS THE ARCHIVE'S OWN, 2026-09-22: three stations reporting,
    timestamped, four hours old, carrying PM figures and no AQI number. On
    that day the block said "no station has a timestamped reading at all"
    while every station carried `measured_at` and `hours_old`.
    """
    from openlocalweather.config import WaqiStation

    measured = datetime(2026, 8, 10, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        pipeline.waqi_fetch,
        "fetch_ground_aqi_stations",
        lambda stations, token: [
            GroundAQIReading(name="Kisumu Airport", station_id="A418534",
                             aqi=None, pm25=52.0, pm10=15.0, measured_at=measured),
            GroundAQIReading(name="Dunga Beach", station_id="A418504",
                             aqi=None, pm25=42.0, pm10=12.0, measured_at=measured),
        ],
    )
    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)
    deps.location = LOCATION.model_copy(
        update={"waqi_stations": LOCATION.waqi_stations or [
            WaqiStation(name="Kisumu Airport", station_id="A418534")]}
    )
    issue(deps, today=date(2026, 8, 11), dry_run=False)

    block = llm.user_prompts.split("GROUND AQI LAST KNOWN")[1].split("\n\n")[0]

    assert "reporting but none of them carried a numeric AQI" in block
    assert "NOT down and NOT absent" in block
    # the false claim, and the wiring-gap fallback, are both absent
    assert "no station has a timestamped reading at all" not in llm.user_prompts
    assert "the reason was not supplied to this prompt" not in llm.user_prompts


def test_the_uv_index_rolls_to_tomorrow_once_the_horizon_does(tmp_path, monkeypatch):
    """At dusk the forecast is about tonight and tomorrow, and so is the UV.

    THE SYSTEM CONTRADICTED ITSELF UNTIL 2026-09-22. `_horizon_for` drops
    today at dusk, and the 18:01 run's own prompt says "WHAT MATTERS NOW:
    tonight (dusk, evening and overnight through to dawn), then tomorrow" —
    while `uv_index_max` went on reporting a peak that happened around
    midday, six hours earlier. Over the 11 archived evening runs this rule
    changes the number on 6 and the BAND a reader sees on 2, once from High
    to Very high.

    The fixture's daily UV is 9.1 today and 7.4 tomorrow, which straddles the
    WHO boundary at 8.0, so the assertion is on the WORD a reader acts on and
    not only on the figure.
    """
    from openlocalweather.scales import uv_band

    llm = FakeLLMProvider()
    deps = make_deps(tmp_path, llm=llm)

    # 06:01 local — dawn, today still wholly ahead
    _clock_at(monkeypatch, datetime(2026, 8, 11, 6, 1))
    issue(deps, today=date(2026, 8, 11), dry_run=False)
    morning = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))

    assert morning.uv_index == 9.1
    assert morning.uv_index_date == date(2026, 8, 11)
    assert uv_band(morning.uv_index) == "Very high"

    # 18:01 local — dusk, the horizon has rolled off today
    _clock_at(monkeypatch, datetime(2026, 8, 11, 18, 1))
    issue(deps, today=date(2026, 8, 11), dry_run=False)
    dusk = log_store.read_log_entry(deps.data_dir, date(2026, 8, 11))

    assert dusk.uv_index == 7.4, "the UV did not roll with the horizon"
    assert dusk.uv_index_date == date(2026, 8, 12)
    assert uv_band(dusk.uv_index) == "High"
    # and the source is recorded either way, so item 167 can swap it
    assert dusk.uv_index_source == "gfs_seamless"


def test_the_entry_names_the_link_that_served_not_the_one_that_failed(tmp_path):
    """ROADMAP item 171, end to end through a real run.

    `test_fallback.py` pins the resolver; this pins that the ENTRY uses it.
    The two are not the same assertion, and the gap between them is where the
    bug lived: `served_identity` was always available in spirit —
    `provider_identity` resolves the live child — and the record still read
    `.model` off the wrapper, which is the FIRST entry whoever served.

    The 2026-09-22 15:01Z forecast was written end to end by a fallback after
    Gemini returned four 503s and was filed under `gemini-3.6-flash`. Since
    `replay.py` partitions the accuracy record by `meta.llm_model`, that files
    a scored forecast under a model that did not make it.
    """
    from openlocalweather.llm.errors import LLMUnavailableError
    from openlocalweather.llm.fallback import FallbackProvider

    class Down:
        model = "gemini-3.6-flash"

        def generate(self, system_prompt, user_prompt, response_schema):
            raise LLMUnavailableError("503")

    served = FakeLLMProvider()
    served.model = "nex-agi/nex-n2.5-pro:free"

    issue(
        make_deps(tmp_path, llm=FallbackProvider([Down(), served])),
        today=date(2026, 8, 11),
        dry_run=False,
    )
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    assert entry.meta.llm_model == "nex-agi/nex-n2.5-pro:free"
    assert entry.meta.llm_provider == "FakeLLMProvider"
    # Both calls fell through to the same link here, so the prose is credited
    # to it too. The fields differ only when the chain moves between calls.
    assert entry.meta.narrative_llm_model == "nex-agi/nex-n2.5-pro:free"


def test_an_unchained_provider_is_named_exactly_as_before(tmp_path):
    """The ordinary deployment. Item 171 changed where the name comes from,
    and a run with no chain must be unaffected by that."""
    issue(make_deps(tmp_path, llm=FakeLLMProvider()), today=date(2026, 8, 11), dry_run=False)
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    assert entry.meta.llm_provider == "FakeLLMProvider"
    assert entry.meta.llm_model == "fake-model"


# ---------------------------------------------------------------------------
# A chain survives one vendor running out — ROADMAP items 170 and 178
# ---------------------------------------------------------------------------
#
# Through a REAL FallbackProvider, the real cap hook and the real ledger. The
# item-170 tests proved each link counts against its own budget, through a
# hand-made chain, and never asked whether a refused link lets the next one
# serve. It did not: the refusal was a plain SpendCapExceeded, the chain only
# catches LLMUnavailableError, and the run died with the fallback untouched.


def _link(model: str, limit: int | None = None) -> FakeLLMProvider:
    link = FakeLLMProvider()
    link.model = model
    if limit is not None:
        link.max_calls_per_24h = limit
    return link


def _spent(tmp_path, link: FakeLLMProvider, n: int) -> None:
    from openlocalweather.spend import record_attempt

    for _ in range(n):
        record_attempt(
            tmp_path, provider=type(link).__name__, model=link.model,
            purpose="forecast", max_calls=10**6,
        )


def test_yesterdays_failures_on_one_vendor_do_not_refuse_todays_run(tmp_path):
    """The failure item 170 was written to remove, still alive at the
    pre-flight: "it can refuse tomorrow's forecast on the strength of
    yesterday's failures."

    A bad day spent 19 of Gemini's 20 on retries. OpenRouter has all 20 of
    its own. The pre-flight counted the WHOLE ledger — 19 used, 2 needed, 20
    allowed — and refused the run before either vendor was asked.
    """
    from openlocalweather.llm.fallback import FallbackProvider

    gemini = _link("gemini-3.6-flash")
    openrouter = _link("nex-agi/nex-n2.5-pro:free")
    _spent(tmp_path, gemini, 19)

    issue(
        make_deps(tmp_path, llm=FallbackProvider([gemini, openrouter])),
        today=date(2026, 8, 11), dry_run=False,
    )
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    # Gemini's last call took the scored judgment; its refusal on the next
    # handed the write-up on. Item 171 names each call's server separately.
    assert entry.meta.llm_model == "gemini-3.6-flash"
    assert entry.meta.narrative_llm_model == "nex-agi/nex-n2.5-pro:free"
    assert not entry.meta.degradations


def test_a_vendor_at_its_own_limit_hands_the_run_to_the_next(tmp_path):
    """Defect A on its own: a link refused by its OWN ceiling must be skipped,
    not end the run. Before this, the refusal escaped the chain and the
    fallback that had calls to spare was never asked."""
    from openlocalweather.llm.fallback import FallbackProvider

    gemini = _link("gemini-3.6-flash", limit=2)
    openrouter = _link("nex-agi/nex-n2.5-pro:free")
    _spent(tmp_path, gemini, 2)

    issue(
        make_deps(tmp_path, llm=FallbackProvider([gemini, openrouter])),
        today=date(2026, 8, 11), dry_run=False,
    )
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    assert entry.meta.llm_model == "nex-agi/nex-n2.5-pro:free"
    assert entry.meta.narrative_llm_model == "nex-agi/nex-n2.5-pro:free"
    assert gemini.calls == [], "refused before a request, so never reached"


def test_every_vendor_running_out_mid_run_still_publishes_the_scored_call(tmp_path):
    """The operator's "restart it if we have enough calls", from the side
    where there are not enough. The budget covered the run at the pre-flight
    and was gone by the write-up, because a retry spent a call in between.
    No link can take the narrative, so the narrative degrades — and the
    judgment, already made and paid for, is published and scored."""
    from openlocalweather.llm.fallback import FallbackProvider

    class RetriedOnce(FakeLLMProvider):
        def generate(self, system_prompt, user_prompt, response_schema):
            if response_schema is GeminiJudgmentResponse and self.before_attempt:
                self.before_attempt()  # the attempt that drew a 503
            return super().generate(system_prompt, user_prompt, response_schema)

    gemini = _link("gemini-3.6-flash", limit=2)
    _spent(tmp_path, gemini, 2)
    openrouter = RetriedOnce()
    openrouter.model = "nex-agi/nex-n2.5-pro:free"
    openrouter.max_calls_per_24h = 2

    issue(
        make_deps(tmp_path, llm=FallbackProvider([gemini, openrouter])),
        today=date(2026, 8, 11), dry_run=False,
    )
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    assert entry.temp_high_c is not None, "the scored call must survive"
    assert entry.meta.llm_model == "nex-agi/nex-n2.5-pro:free"
    assert DEGRADATION_NARRATIVE in {d.code for d in entry.meta.degradations or []}


def test_a_chain_with_no_calls_left_anywhere_refuses_to_start(tmp_path):
    """The loud path, unchanged: when no link can cover the run, it is refused
    before any vendor is asked — the guard stops the call, not just counts."""
    from openlocalweather.llm.fallback import FallbackProvider
    from openlocalweather.spend import SpendCapExceeded

    gemini = _link("gemini-3.6-flash")
    openrouter = _link("nex-agi/nex-n2.5-pro:free")
    _spent(tmp_path, gemini, 20)
    _spent(tmp_path, openrouter, 20)

    with pytest.raises(SpendCapExceeded):
        issue(
            make_deps(tmp_path, llm=FallbackProvider([gemini, openrouter])),
            today=date(2026, 8, 11), dry_run=False,
        )
    assert gemini.calls == [] and openrouter.calls == []


def test_the_pre_flight_holds_each_link_to_its_own_ceiling(tmp_path):
    """The pre-flight and the hook must agree about a link's allowance, or
    the pre-flight starts runs the hook then refuses halfway through.

    Gemini's own ceiling is 2, OpenRouter's 1, the deployment's 20. Reading
    the deployment's number for every link, the pre-flight saw 38 calls left
    and started a run that had one. That mutation SURVIVED both suites until
    this test existed: the per-link ceiling reached the hook and nothing
    checked it reached the pre-flight.
    """
    from openlocalweather.llm.fallback import FallbackProvider
    from openlocalweather.spend import SpendCapExceeded

    gemini = _link("gemini-3.6-flash", limit=2)
    openrouter = _link("nex-agi/nex-n2.5-pro:free", limit=1)
    _spent(tmp_path, gemini, 2)

    with pytest.raises(SpendCapExceeded, match="refused before starting"):
        issue(
            make_deps(tmp_path, llm=FallbackProvider([gemini, openrouter])),
            today=date(2026, 8, 11), dry_run=False,
        )
    assert gemini.calls == [] and openrouter.calls == []


def test_a_run_may_be_split_across_links_with_one_call_each(tmp_path):
    """Why the pre-flight SUMS the links rather than asking for one link that
    could carry the whole run: a chain splits a run naturally. Each vendor has
    exactly one call left; Gemini spends its last on the judgment, is refused
    on the write-up, and OpenRouter spends ITS last on that. A complete
    forecast, no degradation — and asking for a single link with two left
    would have refused it before it began.
    """
    from openlocalweather.llm.fallback import FallbackProvider

    gemini = _link("gemini-3.6-flash", limit=2)
    openrouter = _link("nex-agi/nex-n2.5-pro:free", limit=2)
    _spent(tmp_path, gemini, 1)
    _spent(tmp_path, openrouter, 1)

    issue(
        make_deps(tmp_path, llm=FallbackProvider([gemini, openrouter])),
        today=date(2026, 8, 11), dry_run=False,
    )
    entry = log_store.read_log_entry(tmp_path, date(2026, 8, 11))

    assert entry.meta.llm_model == "gemini-3.6-flash"
    assert entry.meta.narrative_llm_model == "nex-agi/nex-n2.5-pro:free"
    assert not entry.meta.degradations
