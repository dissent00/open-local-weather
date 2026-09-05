"""What the last health check saw: data/health/status.json.

The health check had no memory. Every run read the world, printed it, and
forgot — which is fine for a check whose answer is a state ("this feed is
unreachable") and wrong for one whose answer is an EVENT ("this feed just
woke up").

ROADMAP item 2 turns on exactly such an event. The KMD CAP feed has been
quiet since May 2026, the item's own conclusion is to let October decide
whether it is episodic or abandoned, and the probe that watches it reports
QUIET green and FRESH green. So the transition it exists to catch produced
one line in a weekly Actions log and nothing else — a real event, correctly
detected, reported only where nobody looks. That is item 66's shape, one
layer along.

Small and deliberately not a general key-value store. It holds what a check
must remember to recognise a change, and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

_FILENAME = "status.json"


def health_status_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "health" / _FILENAME


def read_health_status(data_dir: str | Path) -> dict[str, str]:
    """What the previous run recorded, or {} if there was none.

    An empty dict means "never observed", NOT "observed nothing" — the same
    three-valued discipline `degradations` uses. A caller comparing against a
    missing key must treat it as "cannot tell", because a transition nobody
    was present for is not a transition anyone can report.
    """
    path = health_status_path(data_dir)
    if not path.exists():
        return {}

    return json.loads(path.read_text(encoding="utf-8"))


def write_health_status(data_dir: str | Path, status: dict[str, str]) -> Path:
    """Records this run's observations, replacing the previous ones.

    MUST be called even on a run that failed the check. An alarm that fires
    on a transition has to record the new state, or the same transition is
    re-detected every week and a signal meant to be seen once becomes a
    weekly red job — which is how a notification stops being read.
    """
    path = health_status_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return path
