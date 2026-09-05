"""Guards on the GitHub Actions workflows.

These are not unit tests of Python. They exist because a workflow defect is
invisible in the way that matters most: it does not fail anything, it makes
failures stop being reported.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))


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


def test_the_suite_runs_the_same_way_ci_runs_it():
    """`pytest` and `python -m pytest` must not disagree.

    The module form inserts the current directory into sys.path; the console
    script does not. Several test modules import helpers and fixtures from
    their neighbours as `tests.test_pipeline_run`, which resolves only when the
    repo root is on the path — so without `pythonpath` the suite passes for
    anyone typing `python -m pytest` and fails in CI, which types `pytest -q`.

    Measured 2026-09-05: twelve consecutive red CI runs across two days while
    the suite was green on the machine that pushed them. The failure mode is
    not that CI is wrong; it is that the two invocations are different
    programs and only one of them was ever run before pushing.
    """
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_config = config["tool"]["pytest"]["ini_options"]

    assert "." in pytest_config.get("pythonpath", []), (
        "pyproject must put the repo root on sys.path, or cross-module test "
        "imports resolve under `python -m pytest` and fail under `pytest`"
    )


def test_ci_invokes_pytest_the_plain_way():
    """The other half of the pair above. If CI ever gains a `PYTHONPATH=` or
    switches to the module form, the guard above stops guarding anything —
    it would be masking the divergence rather than preventing it."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert "run: pytest -q" in workflow, (
        "CI's invocation changed; test_the_suite_runs_the_same_way_ci_runs_it "
        "is written against `pytest -q` and must be revisited with it"
    )
