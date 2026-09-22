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


def test_the_spend_ledger_is_committed_even_when_the_run_fails():
    """The defect this exists for, measured 2026-09-06.

    The evening refresh aborted after four Gemini 503s. `record_attempt`
    appended all four to `data/spend_ledger.json` on the runner's disk — the
    ledger's own note promises it is "written BEFORE each call so a crash
    cannot lose the count" — and then the workflow threw the file away,
    because the commit step carries an `if:` with no status function and
    GitHub therefore ANDs `success()` into it.

    So four real HTTP requests to a metered API left no trace, and the next
    run's 24-hour window is short by four.

    It has a sharp cause. On 2026-09-02 the same total abort was RECORDED,
    because the missing `pipefail` made that job report success and the
    commit step ran. Fixing pipefail turned a wrongly-succeeding job into a
    correctly-failing one, and silently disabled ledger persistence for
    exactly the runs that spend without producing anything.

    It also biases the ledger as a latency record: it keeps failed attempts
    that were followed by a success and drops the ones that were not.
    """
    doc = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "forecast.yml").read_text())
    steps = [s for job in (doc.get("jobs") or {}).values() for s in (job.get("steps") or [])]

    ledger_steps = [
        s for s in steps
        if isinstance(s.get("run"), str) and "spend_ledger.json" in s["run"]
    ]
    assert ledger_steps, (
        "no step commits data/spend_ledger.json by name, so the only step that "
        "can persist it is the general one — which does not run on failure"
    )

    for step in ledger_steps:
        condition = str(step.get("if", ""))
        assert "failure()" in condition or "always()" in condition, (
            f"step {step.get('name')!r} has if: {condition!r}. A condition with "
            "no status function has success() ANDed into it by GitHub, which "
            "is precisely how the ledger was lost"
        )


def test_the_workflow_only_greps_run_kinds_the_cli_can_print():
    """ROADMAP item 137. `cli.py`'s RUN_KIND_* block says "Change the strings
    and change the workflow with them", and nothing checked it — which is how
    `run-kind: reissue` outlived the concept it named, still printed, still
    grepped, and mapping to a commit subject nobody had chosen in weeks.

    BOTH DIRECTIONS MATTER AND THEY FAIL DIFFERENTLY. A workflow grepping a
    string the CLI never prints is a branch that silently never fires: the
    archive quietly loses a distinction and every run looks ordinary. A CLI
    printing a kind the workflow ignores is the cheaper mistake but the same
    drift, and it is the one that was live.
    """
    import re

    from openlocalweather import cli

    text = (REPO_ROOT / ".github" / "workflows" / "forecast.yml").read_text()
    grepped = set(re.findall(r'grep -q "\^(run-kind: [a-z]+)\$"', text))
    assert grepped, "no run-kind grep found — did the commit-subject step move?"

    printed = {
        v for k, v in vars(cli).items()
        if k.startswith("RUN_KIND_") and isinstance(v, str)
    }

    assert grepped <= printed, (
        f"forecast.yml greps for {sorted(grepped - printed)}, which cli.py "
        f"never prints. That branch can never fire."
    )
    # The CLI may print a kind the workflow does not branch on — "forecast"
    # and "skipped" both fall through to the default subject on purpose — so
    # this direction is reported rather than asserted.
    assert printed <= grepped | {cli.RUN_KIND_FORECAST, cli.RUN_KIND_SKIPPED}, (
        f"cli.py prints {sorted(printed - grepped)}, which the workflow "
        f"ignores. If that is deliberate, add it to this allowance and say why."
    )


# ---------------------------------------------------------------------------
# Provider credentials reach the run — ROADMAP item 81, 2026-09-22
# ---------------------------------------------------------------------------

#: The workflows that actually invoke the pipeline against a provider.
PIPELINE_WORKFLOWS = ["forecast.yml", "health_check.yml"]

#: The allowlist the workflows use, kept here so its behaviour is pinned
#: rather than only asserted to exist.
CREDENTIAL_PATTERN = (
    r"^[A-Z0-9_]+_(API_KEY|BASE_URL|MODEL|FALLBACK_MODELS|JSON_MODE|MAX_TOKENS)$"
)


def _step_named(path: Path, name: str) -> dict | None:
    doc = yaml.safe_load(path.read_text())
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if step.get("name") == name:
                return step
    return None


@pytest.mark.parametrize("name", PIPELINE_WORKFLOWS)
def test_every_provider_prefix_reaches_the_run(name):
    """A chain entry's own `env_prefix` must arrive in the environment.

    THE DEFECT THIS EXISTS FOR: `llm_providers` can name OpenRouter and Groq
    and a fourth gateway, each reading its own OPENROUTER_* / GROQ_* names —
    item 81, 2026-09-22. The env blocks listed LLM_* and GEMINI_* by hand, so
    a third provider was configured, built with nothing, and DROPPED FROM THE
    CHAIN IN SILENCE. The config said three links and the run held two, and
    nothing anywhere said otherwise.
    """
    path = REPO_ROOT / ".github" / "workflows" / name
    step = _step_named(path, "Provider credentials")
    assert step is not None, (
        f"{name} has no 'Provider credentials' step, so only the prefixes "
        "named by hand in its env block can reach the run"
    )
    assert "toJSON(vars)" in yaml.dump(step)
    assert "toJSON(secrets)" in yaml.dump(step)
    assert CREDENTIAL_PATTERN in step["run"], (
        "the allowlist in the workflow has drifted from the one this file "
        "pins; the test below is what says which names it admits"
    )


@pytest.mark.parametrize("name", PIPELINE_WORKFLOWS)
def test_keys_are_not_also_named_by_hand(name):
    """A hand-named key would mask the generic pass-through.

    A step-level `env:` entry wins over `$GITHUB_ENV`, so re-adding
    `LLM_API_KEY: ${{ secrets.LLM_API_KEY }}` would set it to the empty
    string on a deployment that has moved to a prefixed name — which
    `_env` reads as absent, dropping the link.
    """
    path = REPO_ROOT / ".github" / "workflows" / name
    text = path.read_text()
    for named in ("LLM_API_KEY:", "GEMINI_API_KEY:", "LLM_BASE_URL:", "LLM_MODEL:"):
        assert named not in text, (
            f"{name} names {named} by hand; the 'Provider credentials' step "
            "already passes it, and the hand-written one takes precedence"
        )


def test_the_allowlist_admits_providers_and_nothing_else():
    """What the pattern lets through, stated rather than assumed.

    Checked against real names on both sides: `github_token` is present in
    `toJSON(secrets)` on every run, and passing it into the environment of a
    step that then runs arbitrary Python is not something to do by accident.
    """
    import re

    admits = re.compile(CREDENTIAL_PATTERN)

    for key in (
        "LLM_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
        "OPENROUTER_BASE_URL", "OPENROUTER_MODEL", "GEMINI_MODEL",
        "MY_GATEWAY_API_KEY", "OPENROUTER_FALLBACK_MODELS",
        "LLM_JSON_MODE", "LLM_MAX_TOKENS",
    ):
        assert admits.match(key), f"a provider name was refused: {key}"

    for key in (
        "github_token", "GITHUB_TOKEN", "WAQI_TOKEN", "PUBLIC_WEBPAGE_URL",
        "LLM_PROVIDER", "GEMINI_THINKING_LEVEL", "TRIGGER_SOURCE",
    ):
        assert not admits.match(key), f"a non-provider name was admitted: {key}"
