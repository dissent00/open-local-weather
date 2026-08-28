"""fetch_model_run: Open-Meteo's real per-model run metadata — the one
OBSERVATION in this project's guidance-recency story. See
fetch/model_run.py's module docstring for what the endpoint answers, why it
only works for a raw model, and why the driver never raises."""

from datetime import datetime, timezone

import requests
import requests_mock

from openlocalweather.fetch.model_run import META_URL_TEMPLATE, ModelRun, fetch_model_run


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
