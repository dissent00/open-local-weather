"""The rules `tools/fix_note_signs.py` had to learn, each from a wrong edit it
nearly made. ROADMAP item 92.

This is a one-off repair tool rather than shipped logic, and it is tested
anyway because it EDITS THE RECORD. Every rule below was arrived at by the
tool getting it wrong first, on real stored notes, and each one is a way a
mechanical rewrite can quietly replace one false claim with another.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from fix_note_signs import find_corrections  # noqa: E402


def entry(lead: int, **preds):
    return {"model_predictions": {f"day{lead}": [
        {"model": m, **vals} for m, vals in preds.items()
    ]}}


def test_a_wind_claim_is_flipped_only_when_the_record_contradicts_it():
    """Nothing is rewritten on the strength of its wording. The magnitude in
    the prose must match the recomputed error and the direction word must
    contradict its sign."""
    e = entry(0, gfs_seamless={"wind_kmh": 12.2})
    actual = {"peak_wind_kmh": 51.1}

    found, _ = find_corrections(e, actual, 0, "GFS overpredicted surface wind speeds by 38.9 km/h.")
    assert [(c["model"], c["field"]) for c in found] == [("gfs_seamless", "wind_kmh")]

    # Right about the direction — left alone.
    found, _ = find_corrections(e, actual, 0, "GFS under-forecast surface wind speeds by 38.9 km/h.")
    assert found == []

    # A magnitude that is not the one in the record describes something else.
    found, _ = find_corrections(e, actual, 0, "GFS overpredicted surface wind speeds by 12.0 km/h.")
    assert found == []


def test_one_verb_governing_two_quantities_is_refused():
    """"GFS overestimated winds by 25.9 km/h and high temperatures by -4.5C"
    was WRONG about the wind and RIGHT about the temperature. Flipping the
    shared verb fixes one and breaks the other, so the clause is reported and
    left alone — the trade this tool exists to avoid."""
    e = entry(3, gfs_seamless={"wind_kmh": 10.0, "high_c": 30.0})
    actual = {"peak_wind_kmh": 35.9, "high_c": 25.5}

    found, unclear = find_corrections(
        e, actual, 3, "GFS overestimated winds by 25.9 km/h and high temperatures by -4.5°C."
    )
    assert found == []
    assert len(unclear) == 1


def test_a_figure_before_the_verb_cannot_be_governed_by_it():
    """The refusal above was once too blunt and caught this, where the
    temperature figure sits in FRONT of the verb and the claim is about wind
    alone."""
    e = entry(7, gfs_seamless={"wind_kmh": 10.0})
    actual = {"peak_wind_kmh": 44.5, "high_c": 25.0}

    found, unclear = find_corrections(
        e, actual, 7,
        "ECMWF captured high temperature within -1.2°C, compared to GFS which "
        "overpredicted wind speed by 34.5 km/h.",
    )
    assert [c["model"] for c in found] == ["gfs_seamless"]
    assert unclear == []


def test_a_split_half_inherits_its_subject_but_not_its_text():
    """"GFS showed a cold bias (-2.6C) and wind overestimation (+17.2 km/h)"
    is two claims; the second names no model because the first already did.

    An earlier version prepended the model name to the clause, and the
    augmented string then matched nothing in the note — so the wind half was
    found and silently never replaced. Attribution and replacement are
    different strings."""
    e = entry(3, gfs_seamless={"high_c": 30.0, "wind_kmh": 10.0})
    actual = {"high_c": 27.4, "peak_wind_kmh": 27.2}

    found, _ = find_corrections(
        e, actual, 3,
        "GFS showed a high temperature cold bias (-2.6°C) and wind overestimation (+17.2 km/h).",
    )
    fields = sorted(c["field"] for c in found)
    assert fields == ["high_c", "wind_kmh"], f"both halves must be found: {found}"
    # And the clause recorded for each must be text that EXISTS in the note.
    note = "GFS showed a high temperature cold bias (-2.6°C) and wind overestimation (+17.2 km/h)."
    for c in found:
        assert c["clause"] in note, f"clause was rewritten before matching: {c['clause']!r}"


def test_a_temperature_bias_must_say_which_temperature():
    """"cold bias" alone cannot be checked — the tool once confirmed a claim
    about the daytime high against the overnight low, whose magnitude
    happened to match to two decimal places."""
    e = entry(0, icon_seamless={"high_c": 30.0, "low_c": 20.0})
    actual = {"high_c": 27.8, "low_c": 17.8}

    found, _ = find_corrections(e, actual, 0, "ICON showed a cold bias of 2.2°C.")
    assert found == [], "neither temperature was named, so neither can be checked"

    found, _ = find_corrections(e, actual, 0, "ICON showed an overnight cold bias of -2.2°C.")
    assert [c["field"] for c in found] == ["low_c"]


def test_a_shared_verb_flips_only_if_every_model_named_is_wrong():
    """"GFS and UKMO overpredicted surface wind speeds (by 27.4 and 16.6
    km/h)" is one verb over two models. One dissenter abandons the clause."""
    both_wrong = entry(0, gfs_seamless={"wind_kmh": 10.0}, ukmo_seamless={"wind_kmh": 20.8})
    actual = {"peak_wind_kmh": 37.4}
    found, _ = find_corrections(
        both_wrong, actual, 0,
        "GFS and UKMO overpredicted surface wind speeds by 27.4 km/h and 16.6 km/h respectively.",
    )
    assert len(found) == 2

    # UKMO genuinely over-forecast: the verb is right for it and wrong for
    # GFS, so nothing is touched.
    one_right = entry(0, gfs_seamless={"wind_kmh": 10.0}, ukmo_seamless={"wind_kmh": 54.0})
    found, _ = find_corrections(
        one_right, actual, 0,
        "GFS and UKMO overpredicted surface wind speeds by 27.4 km/h and 16.6 km/h respectively.",
    )
    assert found == []
