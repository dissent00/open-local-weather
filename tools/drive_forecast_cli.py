#!/usr/bin/env python3
"""Drive the REAL `olw forecast` CLI through every outcome, and dump what it did.

WHAT THIS IS FOR. A refactor of the pipeline is proved by showing that the
thing still does what it did, and this repo has learnt twice that the suite
cannot show that: it was green throughout the weeks `run_refresh_pipeline`
silently omitted three prompt blocks, and green again while a re-issue
archived every issuance under the first one's timestamp. ROADMAP item 104's
steps are each required to be proved by RUNNING the pipeline and diffing its
real output. This is what runs it.

NOT MOCKS OF THE PIPELINE. `cli.main` parses the real argv, `run_forecast`
dispatches for real, the real pipelines run, the real printers print, and the
real `log_store` writes to disk. Three things are stubbed, all of them outside
this process: the network, the model, and the clock. The network and model
seams are `tests/test_pipeline_run.py`'s own fixtures, imported rather than
copied — a second corpus of fixtures is a second thing to keep in step.

HOW TO USE IT FOR A BEFORE/AFTER. Capture the unchanged code from a detached
worktree, so the working tree carrying the change is never reverted to get a
baseline (`git checkout -- <file>` has silently eaten uncommitted work in this
repo three times — AGENTS.md, "Reverting a mutation"):

    git worktree add /tmp/olw-head HEAD --detach
    OLW_ROOT=/tmp/olw-head .venv/bin/python tools/drive_forecast_cli.py /tmp/a
    OLW_ROOT=/tmp/olw-head .venv/bin/python tools/drive_forecast_cli.py /tmp/a2
    .venv/bin/python tools/drive_forecast_cli.py /tmp/b
    diff -r /tmp/a /tmp/a2   # the control: unchanged vs unchanged
    diff -r /tmp/a /tmp/b    # the comparison
    git worktree remove /tmp/olw-head

RUN THE CONTROL. Two captures of the UNCHANGED code, diffed against each
other, is what makes "identical" mean something rather than "never equal",
and it is what caught the one clock this file's masks did not reach — see
`EARLIER_ISSUANCE_CLOCK`. A comparison whose control has not been run is not
evidence.

WHAT IT COVERS, AND WHAT IT DOES NOT. Three outcomes: a first issuance, a
forced re-issue and a skipped repeat trigger. It does not drive `olw run-daily`
or `olw run-refresh`, which print through the same two functions the forecast
path already exercises. It has no test of its own: its entire output is a
diff, so a test would assert what the diff is for.

Writes only inside the output directory given as its one argument. Used for
item 104 step 2 (`9843f58`).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

# Which checkout to import — this one by default, another via OLW_ROOT so a
# baseline can come from a detached worktree. See the module docstring.
ROOT = Path(os.environ.get("OLW_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from openlocalweather import cli, pipeline, solar  # noqa: E402
from openlocalweather.fetch import open_meteo, metar as metar_fetch  # noqa: E402
from openlocalweather.fetch import model_run as model_run_fetch  # noqa: E402
from openlocalweather.fetch import waqi as waqi_fetch  # noqa: E402
from openlocalweather.fetch.bulletin import NullBulletinFetcher  # noqa: E402

from tests.test_pipeline_run import (  # noqa: E402
    LOCATION,
    FakeLLMProvider,
    archive_fixture,
    daily_fixture,
    forward_hourly_fixture,
    hourly_fixture,
    sun_fixture,
)

TODAY = date(2026, 8, 11)
ISSUED_LOCAL = datetime(2026, 8, 11, 14, 28)


def _no_network(*args, **kwargs):
    raise AssertionError(f"unmocked HTTP call: {args[:1]}")


def patch_everything_outside_the_process() -> None:
    open_meteo.fetch_forecast_hourly_today = lambda *a, **k: hourly_fixture()
    open_meteo.fetch_forecast_daily_extended = lambda *a, **k: daily_fixture()
    open_meteo.fetch_regional_pressure = lambda *a, **k: {"daily": {}}
    open_meteo.fetch_synoptic_pressure = lambda *a, **k: {"points": []}
    open_meteo.fetch_air_quality = lambda *a, **k: {"hourly": {}}
    open_meteo.fetch_forecast_hourly_forward = lambda *a, **k: forward_hourly_fixture()
    open_meteo.fetch_archive_single_day = lambda lat, lon, day, tz: archive_fixture(day)
    open_meteo.fetch_archive_range = lambda lat, lon, start, end, tz: archive_fixture(end)
    solar.sun_times = sun_fixture
    metar_fetch.fetch_metar = lambda icao: None
    waqi_fetch.fetch_ground_aqi_stations = lambda stations, token: []
    model_run_fetch.fetch_model_run = lambda model: None
    requests.get = _no_network
    requests.post = _no_network
    # The forecast date, so the fixtures above line up with what the pipeline
    # asks for. run_forecast's own `today` argument is not reachable from the
    # CLI, which is the point — this drives the CLI.
    pipeline.today_in_tz = lambda tz: TODAY
    # And the WALL CLOCK, both seams. now_in_tz alone is not enough:
    # _sun_context re-derives the issuance moment through reconcile_now
    # against the server's Date header and overrides what now_in_tz said, so
    # an unfrozen reconcile_now puts the real minute back into the prompt's
    # ISSUED line and its forecast windows.
    pipeline.now_in_tz = lambda tz: ISSUED_LOCAL
    pipeline.reconcile_now = lambda system_local, header, offset: (ISSUED_LOCAL, None)


def install_deps(data_dir: Path, narrative: str) -> None:
    llm = FakeLLMProvider()
    llm.response = llm.response.model_copy(update={"today_narrative": narrative})

    def _build(config, data, docs, public_url):
        return pipeline.PipelineDeps(
            location=LOCATION,
            data_dir=data_dir,
            llm_provider=llm,
            public_webpage_url="https://example.org",
            bulletin_fetcher=NullBulletinFetcher(),
        )

    cli._build_pipeline_deps = _build


TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?")


# Derived from the clock rather than printed from it, so the timestamp
# pattern does not reach it: two runs seconds apart compute guidance ages
# that differ in the fourth decimal.
CLOCK_DERIVED = re.compile(r'("guidance_age_hours":\s*)[0-9.]+')

# The EARLIER TODAY header in a re-issue's prompt, rendered HH:MM with no
# seconds from the first run's generated_at_utc — which the frozen seams above
# do not reach, because it is `datetime.now(timezone.utc)` at entry
# construction rather than the issuance clock. Two runs a minute apart differ
# here and nowhere else; it cost an hour to find, so it is named rather than
# swept up by a blanket HH:MM mask that would also blank the ISSUED line and
# the forecast windows, which ARE frozen and are worth diffing.
EARLIER_ISSUANCE_CLOCK = re.compile(r"(Issued )\d{2}:\d{2}")


def mask(text: str) -> str:
    """Blank every wall clock, so a diff shows what the CODE decided."""
    masked = CLOCK_DERIVED.sub(r"\1<AGE>", TIMESTAMP.sub("<TS>", text))
    return EARLIER_ISSUANCE_CLOCK.sub(r"\1<CLOCK>", masked)


def run(data_dir: Path, argv: list[str], narrative: str) -> tuple[int, str]:
    install_deps(data_dir, narrative)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


def main() -> int:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(exist_ok=True)

    patch_everything_outside_the_process()
    base = ["forecast", "--data-dir", str(data_dir)]

    transcript: list[dict] = []
    for name, argv, narrative in [
        ("1-first", base, "## Overview\nDry and warm."),
        ("2-reissue", base + ["--force"], "## Overview\nEvening update."),
        ("3-skipped", base, "## Overview\nShould never be asked for."),
    ]:
        code, text = run(data_dir, argv, narrative)
        transcript.append({"case": name, "exit": code, "stdout": mask(text)})

    (out_dir / "transcript.json").write_text(json.dumps(transcript, indent=2))
    (out_dir / "transcript.txt").write_text(
        "\n".join(f"=== {t['case']} (exit {t['exit']}) ===\n{t['stdout']}" for t in transcript)
    )
    # Every file the run wrote, with the wall-clock fields masked. Two runs
    # seconds apart differ on those and on nothing else; masking them is what
    # makes "identical" mean something rather than "never equal".
    dump = {}
    for path in sorted(data_dir.rglob("*")):
        if path.is_file():
            dump[str(path.relative_to(data_dir))] = mask(path.read_text())
    (out_dir / "data_dump.json").write_text(json.dumps(dump, indent=2, sort_keys=True))
    print(f"wrote {out_dir} from {pipeline.__file__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
