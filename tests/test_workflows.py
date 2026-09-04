"""Guards on the GitHub Actions workflows.

These are not unit tests of Python. They exist because a workflow defect is
invisible in the way that matters most: it does not fail anything, it makes
failures stop being reported.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))


def _run_blocks(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text())
    blocks = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str):
                blocks.append(step["run"])
    return blocks


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_piped_run_block_sets_pipefail(path: Path):
    """The defect this exists for, measured 2026-09-02: `olw forecast | tee`
    exits with TEE's status, and GitHub's default shell is `bash -e {0}` —
    `-e` but NOT `-o pipefail`. So the 15:01 refresh aborted after four failed
    Gemini attempts, produced no forecast, and the job reported SUCCESS.

    Nothing else would have said so — no commit, no email, no red job, and
    the only trace a line in an Actions log nobody reads. A future step that
    pipes without pipefail would re-open exactly that hole, silently.
    """
    for block in _run_blocks(path):
        lines = [l.strip() for l in block.splitlines()]
        code = [l for l in lines if l and not l.startswith("#")]
        pipes = [l for l in code if "|" in l and "||" not in l]
        if not pipes:
            continue
        assert any(l.startswith("set -o pipefail") or "pipefail" in l for l in code), (
            f"{path.name} pipes without pipefail: {pipes[:2]} — a failing command "
            "on the left of a pipe would be reported as success"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_parses(path: Path):
    assert yaml.safe_load(path.read_text()), f"{path.name} is empty or unparseable"
