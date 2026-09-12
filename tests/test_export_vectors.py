"""The vector GENERATOR runs, and reproduces what is committed.

Nothing else in this suite runs `spec/export_vectors.py`. That gap let item
59's prompt split delete `build_system_prompt` on 2026-09-11 while the
exporter kept importing it: the script raised ImportError before writing a
byte, the committed vectors were edited by hand instead, and 1074 tests
passed for a day with the contract's generator unable to start.

The cost is not cosmetic. `spec/README.md` step 2 of every shared-logic change
is "add a vector case, regenerate" — so a broken exporter blocks every future
change to the one artefact that proves the two implementations agree.

Byte-for-byte against the committed files, because a regeneration that
produces a diff is either a behaviour change nobody declared or a generator
that has drifted from what it generated. `spec/README.md` says an unintended
diff is a bug report; this is that sentence as a test.
"""

import importlib
import json
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"
COMMITTED = SPEC_DIR / "vectors"


def test_the_exporter_runs_and_reproduces_every_committed_vector(tmp_path, monkeypatch):
    export_vectors = importlib.import_module("spec.export_vectors")

    monkeypatch.setattr(export_vectors, "OUT_DIR", tmp_path)
    export_vectors.main()

    produced = {p.name for p in tmp_path.glob("*.json")}
    committed = {p.name for p in COMMITTED.glob("*.json")}
    assert produced == committed, (
        "the exporter writes a different SET of files than is committed: "
        f"only generated {sorted(produced - committed)}, "
        f"only committed {sorted(committed - produced)}"
    )

    differing = [
        name
        for name in sorted(committed)
        if json.loads((tmp_path / name).read_text())
        != json.loads((COMMITTED / name).read_text())
    ]
    assert not differing, (
        f"regenerating changes {differing} — run `python spec/export_vectors.py`, "
        "read the diff, and commit it with whatever behaviour change caused it"
    )
