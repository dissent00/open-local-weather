from datetime import date

import pytest
import requests
import requests_mock

from openlocalweather.fetch import open_meteo
from openlocalweather.models import SOURCE_REANALYSIS
from openlocalweather.fetch.open_meteo import (
    OpenMeteoFetchError,
    bucket_hourly_by_date,
    fetch_air_quality,
    fetch_archive_range,
    fetch_archive_single_day,
    fetch_forecast_daily_extended,
    fetch_forecast_hourly_today,
    fetch_regional_pressure,
    get_onset_hour,
)


def test_fetch_forecast_hourly_today_builds_correct_request():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, json={"hourly": {}})
        fetch_forecast_hourly_today(1.0, 2.0, ["gfs_seamless", "ecmwf_ifs025"], "Africa/Nairobi")
        req = m.last_request
        assert req.qs["latitude"] == ["1.0"]
        assert req.qs["longitude"] == ["2.0"]
        assert req.qs["forecast_days"] == ["1"]
        assert req.qs["models"] == ["gfs_seamless,ecmwf_ifs025"]
        assert req.qs["timezone"] == ["africa/nairobi"]  # query strings lowercased by the mock lib


def test_fetch_forecast_daily_extended_defaults_to_8_days():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, json={"daily": {}})
        fetch_forecast_daily_extended(1.0, 2.0, ["gfs_seamless"], "UTC")
        assert m.last_request.qs["forecast_days"] == ["8"]


def test_fetch_regional_pressure_joins_all_points_and_uses_best_match():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, json={"daily": {}})
        fetch_regional_pressure((1.0, 2.0), [(3.0, 4.0), (5.0, 6.0)], "UTC")
        req = m.last_request
        assert req.qs["latitude"] == ["1.0,3.0,5.0"]
        assert req.qs["longitude"] == ["2.0,4.0,6.0"]
        assert req.qs["models"] == ["best_match"]


def test_fetch_air_quality_builds_correct_request():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.AIR_QUALITY_URL, json={"hourly": {}})
        fetch_air_quality(1.0, 2.0, "UTC")
        assert m.last_request.qs["latitude"] == ["1.0"]


def test_fetch_archive_range_formats_dates():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.ARCHIVE_URL, json={"hourly": {}})
        fetch_archive_range(1.0, 2.0, date(2026, 8, 1), date(2026, 8, 10), "UTC")
        req = m.last_request
        assert req.qs["start_date"] == ["2026-08-01"]
        assert req.qs["end_date"] == ["2026-08-10"]


def test_fetch_archive_single_day_uses_same_start_and_end():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.ARCHIVE_URL, json={"hourly": {}})
        fetch_archive_single_day(1.0, 2.0, date(2026, 8, 5), "UTC")
        req = m.last_request
        assert req.qs["start_date"] == ["2026-08-05"]
        assert req.qs["end_date"] == ["2026-08-05"]


def test_fetch_raises_on_non_200():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, status_code=500, text="server error")
        with pytest.raises(OpenMeteoFetchError):
            fetch_forecast_hourly_today(1.0, 2.0, ["gfs_seamless"], "UTC")


def test_fetch_raises_on_network_error():
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, exc=requests.exceptions.ConnectionError("boom"))
        with pytest.raises(OpenMeteoFetchError):
            fetch_forecast_hourly_today(1.0, 2.0, ["gfs_seamless"], "UTC")


# ---------------------------------------------------------------------------
# get_onset_hour / bucket_hourly_by_date
# ---------------------------------------------------------------------------


def test_get_onset_hour_returns_first_hour_crossing_threshold():
    times = ["2026-08-11T00:00", "2026-08-11T01:00", "2026-08-11T02:00"]
    precip = [0.0, 0.6, 1.0]
    assert get_onset_hour(times, precip) == "01:00"


def test_get_onset_hour_returns_none_when_never_crosses():
    times = ["2026-08-11T00:00", "2026-08-11T01:00"]
    precip = [0.0, 0.1]
    assert get_onset_hour(times, precip) is None


def test_bucket_hourly_by_date_empty_input():
    assert bucket_hourly_by_date({}) == {}
    assert bucket_hourly_by_date({"hourly": None}) == {}


def test_bucket_hourly_by_date_aggregates_correctly():
    hourly_json = {
        "hourly": {
            "time": [
                "2026-08-11T00:00", "2026-08-11T06:00", "2026-08-11T12:00", "2026-08-11T18:00",
                "2026-08-12T00:00", "2026-08-12T06:00",
            ],
            "temperature_2m": [18.0, 20.0, 26.0, 22.0, 17.0, 19.0],
            "precipitation": [0.0, 0.8, 0.0, 0.0, 0.0, 0.0],
            "windgusts_10m": [10.0, 15.0, 30.0, 12.0, 8.0, 9.0],
            "pressure_msl": [1012.0, 1011.0, 1009.0, 1008.0, 1015.0, 1014.0],
        }
    }
    result = bucket_hourly_by_date(hourly_json)

    d1 = date(2026, 8, 11)
    assert result[d1].rain is True
    assert result[d1].high_c == pytest.approx(26.0)
    assert result[d1].low_c == pytest.approx(18.0)
    assert result[d1].peak_wind_kmh == pytest.approx(30.0)
    assert result[d1].mslp_trend == pytest.approx(1008.0 - 1012.0)
    assert result[d1].onset_hour == "06:00"

    d2 = date(2026, 8, 12)
    assert result[d2].rain is False
    assert result[d2].onset_hour is None


def test_bucket_hourly_by_date_wind_falls_back_to_windspeed_when_gusts_array_absent():
    hourly_json = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00"],
            "temperature_2m": [18.0, 20.0],
            "precipitation": [0.0, 0.0],
            "windspeed_10m": [5.0, 25.0],  # no windgusts_10m key at all
            "pressure_msl": [1010.0, 1010.0],
        }
    }
    result = bucket_hourly_by_date(hourly_json)
    assert result[date(2026, 8, 11)].peak_wind_kmh == pytest.approx(25.0)


def test_bucket_hourly_by_date_handles_missing_values_in_arrays():
    hourly_json = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T06:00"],
            "temperature_2m": [18.0, None],
            "precipitation": [None, 0.6],
            "windgusts_10m": [None, None],
            "pressure_msl": [1010.0],  # short array
        }
    }
    result = bucket_hourly_by_date(hourly_json)
    entry = result[date(2026, 8, 11)]
    assert entry.high_c == pytest.approx(18.0)
    assert entry.low_c == pytest.approx(18.0)
    assert entry.peak_wind_kmh is None
    assert entry.rain is True  # the 0.6 at hour 2 crosses threshold
    assert entry.mslp_trend is None  # fewer than 2 non-null pressure readings


# ---------------------------------------------------------------------------
# Transient-failure retries
# ---------------------------------------------------------------------------


def test_a_transient_failure_is_retried_rather_than_aborting_the_run(requests_mock):
    """The asymmetry this fixes: the LLM providers retry and those calls cost
    money, while this one is free and its failure is more expensive — the LLM
    is never reached, so the run yields no forecast at all.

    Observed live: a run succeeded and an identical one 30 seconds later could
    not reach the API, with the service healthy either side.
    """
    requests_mock.get(
        open_meteo.FORECAST_URL,
        [
            {"status_code": 503, "text": "upstream hiccup"},
            {"json": {"hourly": {"time": ["2026-08-21T00:00"]}}, "status_code": 200},
        ],
    )
    result = open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert result["hourly"]["time"] == ["2026-08-21T00:00"]


def test_a_bad_request_is_not_retried(requests_mock, monkeypatch):
    """A 4xx means the REQUEST is wrong — a misspelled variable, an impossible
    coordinate. Retrying repeats the mistake more slowly and hides it behind a
    longer wait."""
    slept = []
    monkeypatch.setattr(open_meteo.time, "sleep", lambda s: slept.append(s))
    requests_mock.get(
        open_meteo.FORECAST_URL,
        status_code=400,
        text="Data corrupted at path ''. Cannot initialize ForecastVariable",
    )
    with pytest.raises(open_meteo.OpenMeteoFetchError, match="400"):
        open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert slept == [], "a malformed request must fail immediately"


def test_rate_limiting_IS_retried(requests_mock):
    """429 is the one 4xx worth waiting out — the request is fine, the pace
    isn't."""
    requests_mock.get(
        open_meteo.FORECAST_URL,
        [
            {"status_code": 429, "text": "slow down"},
            {"json": {"hourly": {"time": ["2026-08-21T00:00"]}}, "status_code": 200},
        ],
    )
    result = open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert result["hourly"]["time"] == ["2026-08-21T00:00"]


def test_persistent_failure_still_raises_after_exhausting_attempts(requests_mock):
    """Retrying must not turn a real outage into a silent hang or a bogus
    empty result — the caller has to learn the data is unavailable."""
    requests_mock.get(open_meteo.FORECAST_URL, status_code=503, text="down")
    with pytest.raises(open_meteo.OpenMeteoFetchError):
        open_meteo.fetch_forecast_hourly_today(-0.09, 34.77, ["gfs_seamless"], "UTC")
    assert requests_mock.call_count == open_meteo.MAX_ATTEMPTS


def _resp(payload, status=200):
    """Minimal stand-in for a requests.Response, for the session-level tests
    below which bypass requests_mock deliberately — the point of those is that
    the SESSION is used, and requests_mock patches the transport underneath it."""

    class _R:
        status_code = status
        headers: dict = {}

        def json(self):
            return payload

        @property
        def text(self):
            return str(payload)

    return _R()

# ---------------------------------------------------------------------------
# ROADMAP item 53 — redundancy that does not require knowing the cause
# ---------------------------------------------------------------------------


def test_requests_share_one_connection(monkeypatch):
    """A run makes seven /v1/forecast calls plus air quality inside about half
    a minute, and until now opened a fresh TCP+TLS connection for every one.
    Connection reuse is a plausible mechanism for the read timeouts item 53
    could not explain, costs nothing if it is not, and saves a handshake
    either way."""
    from openlocalweather.fetch import open_meteo

    used = []

    class _FakeSession:
        def get(self, url, params=None, timeout=None):
            used.append(url)
            return _resp({"ok": True})

    monkeypatch.setattr(open_meteo, "_SESSION", _FakeSession())
    open_meteo._get("https://example.test/v1/forecast", {"a": 1})
    open_meteo._get("https://example.test/v1/forecast", {"a": 2})

    assert len(used) == 2, "both calls must go through the shared session"


def test_a_timed_out_request_says_which_one_and_how_far_in(monkeypatch, capsys):
    """Item 53 cost a full investigation because the logs said nothing beyond
    the exception string — not which request, not its position in the run, not
    how long it had been running. A recurrence should identify itself on the
    first run."""
    from openlocalweather.fetch import open_meteo

    class _Boom:
        def get(self, url, params=None, timeout=None):
            raise requests.ReadTimeout("Read timed out. (read timeout=30)")

    monkeypatch.setattr(open_meteo, "_SESSION", _Boom())
    monkeypatch.setattr(open_meteo, "RETRY_BASE_DELAY_S", 0)
    open_meteo.reset_request_counter()

    with pytest.raises(open_meteo.OpenMeteoFetchError):
        open_meteo._get("https://example.test/v1/forecast", {"forecast_days": 2})

    err = capsys.readouterr().err
    assert "request #1" in err
    assert "forecast_days=2" in err
    assert "attempt 3/3" in err


def test_the_counter_numbers_requests_within_a_run(monkeypatch):
    from openlocalweather.fetch import open_meteo

    class _Ok:
        def get(self, url, params=None, timeout=None):
            return _resp({"ok": True})

    monkeypatch.setattr(open_meteo, "_SESSION", _Ok())
    open_meteo.reset_request_counter()
    open_meteo._get("https://example.test/a", {})
    open_meteo._get("https://example.test/b", {})
    assert open_meteo.requests_made() == 2

    open_meteo.reset_request_counter()
    assert open_meteo.requests_made() == 0


def test_the_archive_cloud_it_was_already_paying_for_is_kept():
    """ROADMAP items 87 and 65. `cloud_cover` has been in ARCHIVE_HOURLY_VARS
    all along and `bucket_hourly_by_date` never read it — fetched on every
    archive call and thrown away, while item 65 recorded "the forecast
    predicts cloud_cover; nothing observes it".

    A MEAN over the day, matching what the models' own cloud_cover is a mean
    of, and matching the station reading's day-level meaning."""
    payload = {
        "hourly": {
            "time": ["2026-08-11T00:00", "2026-08-11T12:00", "2026-08-11T23:00"],
            "temperature_2m": [18.0, 27.5, 19.0],
            "precipitation": [0.0, 0.0, 0.0],
            "windgusts_10m": [10.0, 20.0, 12.0],
            "pressure_msl": [1013.0, 1012.0, 1013.5],
            "cloud_cover": [10.0, 80.0, 30.0],
        }
    }
    day = bucket_hourly_by_date(payload)[date(2026, 8, 11)]

    assert day.cloud_cover_pct == 40.0
    assert day.provenance["cloud_cover_pct"] == SOURCE_REANALYSIS


def test_an_hour_with_no_cloud_reading_is_not_a_clear_hour():
    """Absent, never zero — the same rule the precipitation sum follows. An
    all-null day gives None rather than a confident clear sky."""
    base = {
        "time": ["2026-08-11T00:00", "2026-08-11T12:00"],
        "temperature_2m": [18.0, 27.5],
        "precipitation": [0.0, 0.0],
        "windgusts_10m": [10.0, 20.0],
        "pressure_msl": [1013.0, 1012.0],
    }
    partial = bucket_hourly_by_date({"hourly": {**base, "cloud_cover": [None, 80.0]}})
    assert partial[date(2026, 8, 11)].cloud_cover_pct == 80.0

    absent = bucket_hourly_by_date({"hourly": {**base, "cloud_cover": [None, None]}})
    day = absent[date(2026, 8, 11)]
    assert day.cloud_cover_pct is None
    assert "cloud_cover_pct" not in day.provenance


def test_a_timeout_waits_longer_than_a_hiccup(monkeypatch):
    """ROADMAP item 79, extended to the weather fetches 2026-09-09.

    The 15:01 run that day died on three consecutive 30-second read timeouts
    against api.open-meteo.com, 1.6 s and 3.2 s apart. That is 95 seconds of
    elapsed time and functionally ONE attempt repeated: a service saturated
    enough to drop a connection is still saturated a second and a half later.

    A timeout means BUSY; a refused connection or a 5xx means something else.
    So a timeout gets the long backoff and everything else keeps the short
    one, because a transient blip genuinely is fixed by trying again at once
    and delaying it would only make a recoverable run slower.
    """
    from openlocalweather.fetch import open_meteo

    # conftest zeroes every backoff so the suite does not sleep. This test is
    # ABOUT the delays, so it puts the real ones back.
    monkeypatch.setattr(open_meteo, "RETRY_BASE_DELAY_S", 1.5)
    monkeypatch.setattr(open_meteo, "TIMEOUT_RETRY_DELAY_S", 15.0)
    delay = open_meteo._retry_delay_s

    # Ranges, not equalities: the delay is jittered so that forks sharing a
    # schedule do not all return at the same instant. Jitter only ever ADDS,
    # so the floor is the nominal wait.
    top = 1 + open_meteo.JITTER_FRACTION
    assert 1.5 <= delay(1, timed_out=False) <= 1.5 * top
    assert 3.0 <= delay(2, timed_out=False) <= 3.0 * top

    # An order of magnitude longer for a service under load, and growing.
    # Floor against floor: jitter widens both, so comparing a jittered value
    # against another jittered ceiling would be asserting luck.
    assert delay(1, timed_out=True) >= 10 * open_meteo.RETRY_BASE_DELAY_S
    assert delay(2, timed_out=True) >= 2 * open_meteo.TIMEOUT_RETRY_DELAY_S


def test_the_long_backoff_still_fits_inside_the_run(monkeypatch):
    """A retry policy that outlives the cron slot is a different outage. Three
    attempts at 30 s plus the waits must stay well under the gap to the next
    scheduled run."""
    from openlocalweather.fetch import open_meteo

    monkeypatch.setattr(open_meteo, "TIMEOUT_RETRY_DELAY_S", 15.0)
    worst = open_meteo.REQUEST_TIMEOUT_S * open_meteo.MAX_ATTEMPTS + sum(
        open_meteo._retry_delay_s(a, timed_out=True)
        for a in range(1, open_meteo.MAX_ATTEMPTS)
    )
    assert worst < 300, f"a single request could take {worst}s before giving up"


def test_we_say_who_we_are():
    """Open-Meteo is free and unauthenticated, so the User-Agent is the only
    thing telling them who is calling. The default `python-requests/x.y` is
    anonymous, indistinguishable from a scraper, and gives an operator no way
    to reach us before blocking us."""
    from openlocalweather.fetch.open_meteo import _SESSION

    ua = _SESSION.headers.get("User-Agent", "")
    assert "open-local-weather" in ua
    assert "github.com" in ua, "a bare name gives them nothing to look up"
    assert "python-requests" not in ua


def test_the_host_saying_when_to_come_back_is_obeyed(monkeypatch):
    """`Retry-After` is the server telling us exactly when it wants us. Our
    own backoff is a guess; theirs is not, and honouring it is both more
    respectful and more likely to succeed than guessing shorter."""
    from openlocalweather.fetch import open_meteo

    monkeypatch.setattr(open_meteo, "TIMEOUT_RETRY_DELAY_S", 15.0)
    slept = []
    monkeypatch.setattr(open_meteo.time, "sleep", lambda s: slept.append(s))

    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, [
            {"status_code": 503, "headers": {"Retry-After": "7"}},
            {"json": {"ok": True}, "status_code": 200},
        ])
        open_meteo._get(open_meteo.FORECAST_URL, {"latitude": 0, "longitude": 0})

    assert slept == [7.0], f"ignored the host's own Retry-After: {slept}"


def test_an_absurd_retry_after_is_not_obeyed_blindly(monkeypatch):
    """A header is input, not instruction. A cron slot cannot wait an hour, so
    an implausible value is capped rather than trusted — and capped rather
    than ignored, because the host still meant "not yet"."""
    from openlocalweather.fetch import open_meteo

    slept = []
    monkeypatch.setattr(open_meteo.time, "sleep", lambda s: slept.append(s))
    with requests_mock.Mocker() as m:
        m.get(open_meteo.FORECAST_URL, [
            {"status_code": 429, "headers": {"Retry-After": "3600"}},
            {"json": {"ok": True}, "status_code": 200},
        ])
        open_meteo._get(open_meteo.FORECAST_URL, {"latitude": 0, "longitude": 0})

    assert slept and slept[0] == open_meteo.MAX_RETRY_AFTER_S


def test_retries_are_jittered_so_clients_do_not_return_in_lockstep(monkeypatch):
    """Every fork of this project retrying on the same fixed schedule turns a
    blip into a thundering herd against a free service. Jitter is the cheapest
    thing a polite client does."""
    from openlocalweather.fetch import open_meteo

    monkeypatch.setattr(open_meteo, "RETRY_BASE_DELAY_S", 1.5)
    seen = {open_meteo._retry_delay_s(1, timed_out=False) for _ in range(40)}

    assert len(seen) > 1, "a fixed delay means every client returns at once"
    assert all(1.5 <= d <= 1.5 * (1 + open_meteo.JITTER_FRACTION) for d in seen)
