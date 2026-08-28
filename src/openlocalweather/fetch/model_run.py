"""One model's real run metadata — the one OBSERVATION in this project's
guidance-recency story; everything else (cycle.aligned_cycle_at) is an
inference from the clock.

THE ENDPOINT: `https://api.open-meteo.com/data/<raw-model>/static/meta.json`.
Verified live, ~600 bytes: `last_run_initialisation_time` and
`last_run_availability_time`, both unix seconds, plus `update_interval_seconds`.
No API key, no rate limit documented.

ONLY ANSWERS FOR A RAW MODEL. Verified live: `gfs_seamless`, `icon_seamless`,
`ukmo_seamless` and `best_match` all return HTTP 500 here — each is a BLEND
assembled from several underlying runs at serve time, so there is no single
"last run" to report. `ecmwf_ifs025` (and the other non-blend model names
Open-Meteo serves) answers normally. This project fetches `ecmwf_ifs025`
specifically — see pipeline.py's guidance-recency resolution — because
verified separately, it is the SLOWEST of the models this project fetches to
publish (8h24m behind initialisation, vs GFS's 5h35m), so its newest
available run is in practice the newest cycle every model has, which is
exactly the quantity cycle.aligned_cycle_at estimates. Observing this one
model is worth the request; there is no reason to ask the other four.

BEST-EFFORT, LIKE metar.py AND waqi.py. This observation refines a number
the pipeline can already produce without it (cycle.aligned_cycle_at's
inferred floor); it must never be the reason a run fails. Missing config,
network error, non-200 (including the four blends' expected 500), malformed
JSON, or a response missing/mistyping the two timestamp keys all return
None. Nothing here raises into the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

META_URL_TEMPLATE = "https://api.open-meteo.com/data/{model}/static/meta.json"
REQUEST_TIMEOUT_S = 15


@dataclass
class ModelRun:
    model: str
    initialised_at: datetime  # UTC, from last_run_initialisation_time
    available_at: datetime  # UTC, from last_run_availability_time


def fetch_model_run(model: str) -> ModelRun | None:
    """This model's most recent run, or None on any failure — see this
    module's docstring for the full list of what "any failure" covers,
    including the four blend model names that 500 here by design."""
    if not model:
        return None
    try:
        resp = requests.get(META_URL_TEMPLATE.format(model=model), timeout=REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None

    try:
        initialised_at = datetime.fromtimestamp(data["last_run_initialisation_time"], tz=timezone.utc)
        available_at = datetime.fromtimestamp(data["last_run_availability_time"], tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OSError):
        # KeyError: shape changed underneath us. TypeError/ValueError/OSError:
        # a key was present but not the numeric-seconds value this endpoint
        # has always returned so far (fromtimestamp rejects strings, None,
        # and out-of-range values).
        return None

    return ModelRun(model=model, initialised_at=initialised_at, available_at=available_at)
