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
    for f in transcript.txt transcript.json data_dump.json; do
        diff /tmp/a/$f /tmp/a2/$f    # the control: unchanged vs unchanged
        diff /tmp/a/$f /tmp/b/$f     # the comparison
    done
    git worktree remove /tmp/olw-head

DIFF THE THREE MASKED ARTIFACTS, NOT THE OUTPUT DIRECTORY. This used to say
`diff -r /tmp/a /tmp/a2` and that instruction is wrong: the out_dir also
contains `data/`, which is the RAW tree the run wrote, with its real
`generated_at_utc`, `refreshed_at`, `issued_at` and spend-ledger stamps
unmasked. Two runs a second apart always differ there, so the documented
control could never come back identical and the reader is left unable to tell
a harness artefact from a real change. `data_dump.json` is that same tree with
the clocks masked, which is why it exists. Cost the 2026-09-13 session a
confusing control before the cause was spotted.

RUN THE CONTROL. Two captures of the UNCHANGED code, diffed against each
other, is what makes "identical" mean something rather than "never equal",
and it is what caught the one clock this file's masks did not reach — see
`EARLIER_ISSUANCE_CLOCK`. A comparison whose control has not been run is not
evidence.

WHAT IT COVERS, AND WHAT IT DOES NOT. Four outcomes: a first issuance, a
forced re-issue, a skipped repeat trigger, and item 121's observation-only
refresh — the one an hourly cron should mostly produce. It does not drive `olw run-daily`
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
from datetime import date, datetime, timedelta
from pathlib import Path

# Which checkout to import — this one by default, another via OLW_ROOT so a
# baseline can come from a detached worktree. See the module docstring.
ROOT = Path(os.environ.get("OLW_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from openlocalweather import cli, pipeline, solar  # noqa: E402
from openlocalweather.store import log_store  # noqa: E402
from openlocalweather.fetch import open_meteo, metar as metar_fetch  # noqa: E402
from openlocalweather.fetch import model_run as model_run_fetch  # noqa: E402
from openlocalweather.fetch import waqi as waqi_fetch  # noqa: E402
from openlocalweather.fetch.bulletin import NullBulletinFetcher  # noqa: E402
from openlocalweather.fetch.metar import StationWeather  # noqa: E402

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

# A station whose report the cases can move between runs — ROADMAP item 121.
# LOCATION itself configures no ICAO, which would make `_observed_so_far`
# return None everywhere and leave the observed block absent from every case,
# so the one outcome that exists to refresh it would diff against nothing.
STATION_ICAO = "HKKI"
LOCATION_WITH_STATION = LOCATION.model_copy(update={"metar_station_icao": STATION_ICAO})
STATION = {"raining": False}

# The issuance clock, mutable so a case can move the afternoon on. Item 121's
# observation-only run exists to put a LATER reading beside an EARLIER
# forecast, and a harness that froze both at one minute would render the two
# timestamps identical and prove nothing about the thing it is checking.
CLOCK = {"local": ISSUED_LOCAL}


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
    # The run's one archive request (item 151, 2026-09-20) sits above the
    # stubbed readers and would reach the network guard; stubbed like them.
    metar_fetch.prefetch_station_rows = lambda *a, **k: None
    # What the station has already seen today. Read once per run by the
    # pipeline; this returns whatever STATION currently says, so a case can
    # move the weather between runs the way a real afternoon does.
    metar_fetch.observed_station_data = lambda icao, start, end, tz, data_dir=None, on_fallback=None: (
        {d: StationWeather(thunder=False, precipitation=STATION["raining"]) for d in (start, end)},
        None,
    )
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
    pipeline.now_in_tz = lambda tz: CLOCK["local"]
    pipeline.reconcile_now = lambda system_local, header, offset: (CLOCK["local"], None)


def install_deps(data_dir: Path, narrative: str) -> None:
    llm = FakeLLMProvider()
    llm.response = llm.response.model_copy(update={"today_narrative": narrative})

    def _build(config, data, docs, public_url):
        return pipeline.PipelineDeps(
            location=LOCATION_WITH_STATION,
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
# Two spellings of the same clock-derived quantity: the entry stores
# `guidance_age_hours` at full precision and the prompt embeds `hours_old`
# rounded to one decimal. Masking only the first let a capture six minutes
# later tick 9.8 -> 9.9 and show up as a prompt change, which is exactly the
# false positive this file exists to avoid.
#
# The backslashes are load-bearing: the prompt archive stores the prompt as an
# ESCAPED JSON string, so the same key appears as \"hours_old\" there and as
# "guidance_age_hours" in the log entry. Matching only the unescaped spelling
# masked the entry and left the prompt ticking, which showed up as a prompt
# change twice before it was understood.
CLOCK_DERIVED = re.compile(r'(\\?"(?:guidance_age_hours|hours_old)\\?":\s*)[0-9.]+')

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
        ("2-later", base + ["--force"], "## Overview\nA second forecast, later in the day."),
        ("3-skipped", base, "## Overview\nShould never be asked for."),
    ]:
        code, text = run(data_dir, argv, narrative)
        transcript.append({"case": name, "exit": code, "stdout": mask(text)})

    # --- 4: the observation-only refresh, ROADMAP item 121 ---
    #
    # The hourly-cron outcome, and the one that must cost nothing. Two things
    # have to be true for the CLI to reach it and neither is reachable through
    # argv, which is why they are set here rather than passed:
    #
    #   - THE REPEAT INTERVAL HAS TO HAVE PASSED. `run_forecast` measures it
    #     against `datetime.now(timezone.utc)`, which the frozen seams above
    #     do not reach — they freeze the ISSUANCE clock, not that one. So the
    #     stored entry is aged instead, which is the same fact from the other
    #     end and goes through the real comparison rather than around it.
    #   - THE STATION HAS TO HAVE SEEN SOMETHING NEW, or the case proves only
    #     that a run which changed nothing changed nothing.
    #
    # The narrative handed to the provider is one no run should ever ask for:
    # if it appears in the output, the gate let an LLM call through.
    aged = log_store.read_log_entry(data_dir, TODAY)
    aged.meta.generated_at_utc = aged.meta.generated_at_utc - timedelta(hours=2)
    if aged.meta.refreshed_at is not None:
        aged.meta.refreshed_at = aged.meta.refreshed_at - timedelta(hours=2)
    log_store.write_log_entry(data_dir, aged)
    STATION["raining"] = True
    # Two hours on. What makes the case legible: the page must now show a
    # forecast issued at 14:28 beside a reading taken at 16:45, and a reader
    # who sees one time twice has been told the day was quiet since 14:28.
    CLOCK["local"] = datetime(2026, 8, 11, 16, 45)

    code, text = run(data_dir, base, "## Overview\nShould never be asked for.")
    transcript.append({"case": "4-observed", "exit": code, "stdout": mask(text)})

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
