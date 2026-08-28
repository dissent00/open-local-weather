"""fetch_model_run: Open-Meteo's real per-model run metadata — the one
OBSERVATION in this project's guidance-recency story. See
fetch/model_run.py's module docstring for what the endpoint answers, why it
only works for a raw model, and why the driver never raises."""

from datetime import datetime, timedelta, timezone

import requests
import requests_mock

from openlocalweather.fetch.model_run import (
    META_URL_TEMPLATE,
    OBSERVED_MODEL,
    RUN_SETTLE_MINUTES,
    ModelRun,
    fetch_model_run,
    fetch_settled_run,
)


def url_for(model: str) -> str:
    return META_URL_TEMPLATE.format(model=model)


def test_fetch_model_run_parses_unix_seconds_as_utc():
    with requests_mock.Mocker() as m:
        m.get(
            url_for("ecmwf_ifs025"),
            json={
                "last_run_initialisation_time": 1755993600,
                "last_run_availability_time": 1756024496,
                "update_interval_seconds": 21600,
            },
        )
        result = fetch_model_run("ecmwf_ifs025")

        assert result == ModelRun(
            model="ecmwf_ifs025",
            initialised_at=datetime.fromtimestamp(1755993600, tz=timezone.utc),
            available_at=datetime.fromtimestamp(1756024496, tz=timezone.utc),
        )


def test_fetch_model_run_returns_none_on_500():
    """The four blend models (gfs_seamless, icon_seamless, ukmo_seamless,
    best_match) 500 here because they have no single run to report — see
    fetch/model_run.py's module docstring."""
    with requests_mock.Mocker() as m:
        m.get(url_for("gfs_seamless"), status_code=500)
        assert fetch_model_run("gfs_seamless") is None


def test_fetch_model_run_returns_none_on_network_error():
    with requests_mock.Mocker() as m:
        m.get(url_for("ecmwf_ifs025"), exc=requests.ConnectionError)
        assert fetch_model_run("ecmwf_ifs025") is None


def test_fetch_model_run_returns_none_on_malformed_json():
    with requests_mock.Mocker() as m:
        m.get(url_for("ecmwf_ifs025"), text="not json")
        assert fetch_model_run("ecmwf_ifs025") is None


def test_fetch_model_run_returns_none_on_missing_keys():
    with requests_mock.Mocker() as m:
        m.get(url_for("ecmwf_ifs025"), json={"update_interval_seconds": 21600})
        assert fetch_model_run("ecmwf_ifs025") is None


def test_fetch_model_run_returns_none_on_blank_model():
    with requests_mock.Mocker() as m:
        assert fetch_model_run("") is None
        assert m.call_count == 0


# ---------------------------------------------------------------------------
# fetch_settled_run — the settle rule, shared by the forecast pipeline and
# check-health so neither states it itself.
# ---------------------------------------------------------------------------


def meta_json(initialised_at: datetime, available_at: datetime) -> dict:
    return {
        "last_run_initialisation_time": int(initialised_at.timestamp()),
        "last_run_availability_time": int(available_at.timestamp()),
        "update_interval_seconds": 21600,
    }


def test_fetch_settled_run_returns_a_run_that_has_settled():
    now = datetime(2026, 8, 28, 4, 17, tzinfo=timezone.utc)
    initialised_at = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    available_at = now - timedelta(minutes=RUN_SETTLE_MINUTES + 1)

    with requests_mock.Mocker() as m:
        m.get(url_for(OBSERVED_MODEL), json=meta_json(initialised_at, available_at))
        run = fetch_settled_run(now)

    assert run is not None
    assert run.model == OBSERVED_MODEL
    assert run.initialised_at == initialised_at


def test_fetch_settled_run_withholds_a_run_that_has_not_settled():
    """Open-Meteo's servers are eventually consistent, so a run that became
    available seconds ago may not be what the forecast fetch received."""
    now = datetime(2026, 8, 28, 4, 17, tzinfo=timezone.utc)
    available_at = now - timedelta(minutes=RUN_SETTLE_MINUTES - 1)

    with requests_mock.Mocker() as m:
        m.get(
            url_for(OBSERVED_MODEL),
            json=meta_json(datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc), available_at),
        )
        assert fetch_settled_run(now) is None


def test_fetch_settled_run_returns_none_when_the_endpoint_fails():
    now = datetime(2026, 8, 28, 4, 17, tzinfo=timezone.utc)
    with requests_mock.Mocker() as m:
        m.get(url_for(OBSERVED_MODEL), exc=requests.ConnectionError)
        assert fetch_settled_run(now) is None
