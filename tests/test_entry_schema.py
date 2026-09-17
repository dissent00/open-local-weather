"""The committed day-entry schema is what the models say — ROADMAP item 156.

A schema that lagged the model would let the store accept an entry the
pipeline cannot read, or refuse one it writes, and neither failure appears
on this side. So the file is compared byte for byte, the way the vectors
are: a diff is either an undeclared change to the entry or a regeneration
someone forgot.
"""

import importlib
import json

from openlocalweather.models import DailyLogEntry


def test_the_committed_schema_matches_the_model():
    export = importlib.import_module("spec.export_entry_schema")
    assert export.OUT_PATH.read_text() == export.entry_schema_text(), (
        "spec/day_entry.schema.json is stale; run python spec/export_entry_schema.py"
    )


def test_a_committed_entry_satisfies_the_schema():
    """The reference deployment's own entry validates, so a writer held to
    this file is held to something the pipeline actually produces."""
    jsonschema = importlib.import_module("jsonschema")
    export = importlib.import_module("spec.export_entry_schema")
    schema = json.loads(export.OUT_PATH.read_text())
    entry = json.loads((export.OUT_PATH.parents[1] / "data" / "log" / "2026-09-17.json").read_text())

    jsonschema.Draft202012Validator(schema).validate(entry)
    DailyLogEntry.model_validate(entry)
