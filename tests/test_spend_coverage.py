"""Every path that can reach a model must count what it spends.

THIS IS THE THIRD TIME. The rule has been broken once per new caller, and
each fix was local to the caller that broke it:

1. `check-health` built a provider, called the model and recorded nothing.
   Fixed by writing a SECOND hook-attaching function in `cli.py`.
2. The hook fired once per forecast while the providers retry up to
   MAX_ATTEMPTS times inside one `generate()`, so a cap of 10 permitted 40
   billable requests. Fixed by moving the hook to per-request.
3. `olw replay` — the most expensive command here, one call per frozen case
   — reached the provider without passing through the cap at all. It said it
   counted, in its own printed output and in its own docstring, and did not.
   Found 2026-09-10 by reading the ledger after a six-case run: eight
   requests, zero rows.

Three local fixes and no guard, so the fourth caller will do it again. This
file is the guard: it finds every `.generate(` in the package and requires
the function around it to have attached the cap, so a new path that spends
without counting fails here rather than on someone's bill.
"""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "openlocalweather"

CAP = "attach_spend_cap"

# Functions that call generate() on a provider their CALLER already capped.
# Each entry is a promise that the guard checks the caller instead, and the
# reason it cannot check here.
CAPPED_BY_CALLER = {
    "check_model_deprecation": (
        "Takes the provider as an argument. `cli._run_check_health` attaches "
        "the cap before calling it — and until 2026-09-10 it did so through a "
        "second, weaker copy of the function that omitted the fail-closed "
        "check and the shout when a provider ignores the hook."
    ),
    "run_replay": (
        "Takes the provider as an argument. `cli._run_replay` attaches the cap "
        "before calling it — which is exactly the seam that was broken, so the "
        "guard checks _run_replay below rather than trusting this."
    ),
    "generate_forecast": (
        "Takes the provider as an argument. The two calls of ROADMAP item 59 "
        "step 3 live here so the pipeline and the replay cannot disagree "
        "about what a forecast is; both callers attach the cap above it."
    ),
    "_generate_forecast": (
        "Takes the provider as an argument. pipeline's wrapper around "
        "generate_forecast, adding the per-call meta snapshot. "
        "run_daily_pipeline and run_refresh_pipeline attach the cap before "
        "calling it."
    ),
}


def _generate_calls(tree: ast.Module) -> list[tuple[str, int]]:
    """(enclosing function name, line) for every `<something>.generate(...)`."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "generate"):
            continue

        enclosing = parents.get(node)
        while enclosing is not None and not isinstance(
            enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            enclosing = parents.get(enclosing)
        found.append((enclosing.name if enclosing else "<module>", node.lineno))
    return found


def _names_used(fn: ast.FunctionDef) -> set[str]:
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def test_every_call_to_a_model_is_counted():
    sources = {p: ast.parse(p.read_text()) for p in SRC.rglob("*.py")}

    call_sites = [
        (path, fn, line)
        for path, tree in sources.items()
        for fn, line in _generate_calls(tree)
    ]
    # The empty-input trap. If the scan stops finding call sites — a rename,
    # a refactor, a bad walk — every assertion below passes against nothing.
    # THREE since ROADMAP item 59 step 3, down from four: the pipeline and the
    # replay used to hold a generate() each and now share the two inside
    # `generate_forecast`. The floor is the empty-input trap — if the scan
    # stops finding call sites, every assertion below passes against nothing —
    # so it tracks the real number rather than sitting safely under it.
    assert len(call_sites) >= 3, f"only found {len(call_sites)} generate() call sites"

    uncounted = []
    for path, fn_name, line in call_sites:
        if fn_name in CAPPED_BY_CALLER:
            continue
        fn = _function(sources[path], fn_name)
        if fn is None or CAP not in _names_used(fn):
            uncounted.append(f"{path.relative_to(SRC)}:{line} in {fn_name}()")

    assert not uncounted, (
        "these reach a model without attaching the spend cap:\n  "
        + "\n  ".join(uncounted)
        + f"\n\nCall {CAP}() before generate(), or — if the caller attaches it — "
        "add the function to CAPPED_BY_CALLER with the reason."
    )


def test_the_functions_that_delegate_are_really_capped_by_their_caller():
    """The allowlist above is a promise about somebody else's function. This
    checks that somebody else, so the promise cannot quietly become false."""
    sources = {p: ast.parse(p.read_text()) for p in SRC.rglob("*.py")}

    for delegated in CAPPED_BY_CALLER:
        callers = []
        for path, tree in sources.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name != delegated:
                    continue
                enclosing = _function_containing(tree, node)
                if enclosing is not None:
                    callers.append((path, enclosing))

        assert callers, f"nothing calls {delegated}() — the allowlist entry is stale"
        for path, caller in callers:
            # A delegated function may be called by another delegated one —
            # cli._run_replay -> run_replay -> generate_forecast is two links
            # since ROADMAP item 59 step 3. The chain is capped as long as it
            # ENDS at something that attaches the cap, and each link's own
            # allowlist entry names who does it.
            if caller.name in CAPPED_BY_CALLER:
                continue
            assert CAP in _names_used(caller), (
                f"{path.name}:{caller.name}() calls {delegated}() without attaching "
                f"the cap, and {delegated} is allowlisted on the promise that it does"
            )


def _function_containing(tree: ast.Module, target: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(child is target for child in ast.walk(node)):
                return node
    return None
