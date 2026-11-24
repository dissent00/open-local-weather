"""The Discussion's drivers — ROADMAP item 200 step 1. Code's reading of
the guidance as stated facts, over the brief's inputs and the recent
record. The fixture is 2026-10-07's, as in test_brief.py."""

import copy
import json
from pathlib import Path

from openlocalweather.brief import BriefInputs, recent_calls, render_drivers

FIXTURES = Path(__file__).parent / "fixtures"
FOUR = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless"]


def _inputs(**overrides) -> BriefInputs:
    prompt = (FIXTURES / "user_prompt_2026-10-07.txt").read_text()
    entry = json.loads((FIXTURES / "brief_entry_2026-10-07.json").read_text())
    inputs = BriefInputs.from_user_prompt(
        prompt, entry, secondary_name="Winam Gulf", met_service_name="Kenya Met", met_service_model_id="kenya_met",
    )
    for key, value in overrides.items():
        setattr(inputs, key, value)
    return inputs


def _day(lead, name, *, high, wet, models=FOUR):
    return {"lead_time_days": lead, "day_name": name, "models": models, "wet_votes": wet, "high_c": high}


def _days(early_high=30.0, late_high=30.0, early_wet=3, late_wet=3, day6_models=FOUR[:3], day7_models=FOUR[:2]):
    return [
        _day(1, "Thursday", high=early_high, wet=early_wet),
        _day(2, "Friday", high=early_high, wet=early_wet),
        _day(3, "Saturday", high=29.0, wet=4),
        _day(4, "Sunday", high=late_high, wet=late_wet),
        _day(5, "Monday", high=late_high, wet=late_wet),
        _day(6, "Tuesday", high=25.0, wet=0, models=day6_models),
        _day(7, "Wednesday", high=24.0, wet=0, models=day7_models),
    ]


def _follows(**days) -> list[str]:
    text = render_drivers(_inputs(extended_days=_days(**days)), [])
    return [l.strip() for l in text.splitlines() if l.startswith("    ")]


def test_what_follows_compares_days_one_and_two_with_four_and_five():
    text = render_drivers(_inputs(extended_days=_days()), [])

    assert "what follows (Thursday–Friday against Sunday–Monday, the same 4 models):" in text
    # Days 6 and 7, cold and dry with fewer models, do not move the figures.
    assert _follows() == [
        "highs steady near 30 °C",
        "wet models steady near 3 of 4 a day",
        "beyond Monday: fewer models reach Tuesday (3) and Wednesday (2)",
    ]


def test_highs_turn_at_two_degrees():
    assert _follows(late_high=28.1)[0] == "highs steady near 30 °C"
    assert _follows(late_high=28.0)[0] == "highs 30 °C, then 28 °C: turning cooler"
    assert _follows(late_high=32.5)[0] == "highs 30 °C, then 32 °C: turning warmer"


def test_the_wet_share_turns_at_three_model_days_in_eight():
    assert _follows(early_wet=4, late_wet=3)[1] == "wet models steady near 4 of 4 a day"
    # 4+4 against 2+3: three model-days of eight.
    days = _days(early_wet=4, late_wet=2)
    days[4]["wet_votes"] = 3
    lines = [l.strip() for l in render_drivers(_inputs(extended_days=days), []).splitlines()]
    assert "wet models 4 of 4 a day, then 2.5 of 4: turning drier" in lines
    assert _follows(early_wet=1, late_wet=3)[1] == "wet models 1 of 4 a day, then 3 of 4: turning wetter"


def test_no_comparison_across_different_model_sets():
    days = _days()
    days[4]["models"] = FOUR[:3]

    assert "what follows" not in render_drivers(_inputs(extended_days=days), [])


def test_no_beyond_line_when_every_model_reaches_the_end():
    assert _follows(day6_models=FOUR, day7_models=FOUR)[-1].startswith("wet models")


def test_the_lean_names_the_nearest_model_and_its_highs_record():
    text = render_drivers(_inputs(), [])

    assert "  the lean, high: the call's 31.1 °C is nearest " in text
    lean = next(l for l in text.splitlines() if l.startswith("  the lean, high"))
    model = lean.split(" is nearest ")[1].split(" (")[0]
    record = next((l for l in text.splitlines() if l.startswith("  the lean, record")), None)
    if record:
        assert record.startswith(f"  the lean, record: {model} ") and "daytime highs" in record
    assert "gfs_seamless" not in text and "ecmwf_ifs025" not in text


def test_the_met_service_agrees_disagrees_or_is_missing():
    i = _inputs()
    rows = [r for r in i.predictions if not (r.get("lead") == "day0" and r.get("model") == "kenya_met")]
    theirs = next(r for r in i.predictions if r.get("lead") == "day0" and r.get("model") == "kenya_met")
    called = i.served_call["today_properties"]["rain"]

    def line(rain):
        row = {**theirs, "rain": rain}
        text = render_drivers(_inputs(predictions=[*rows, row]), [])
        return next(l for l in text.splitlines() if l.startswith("  met service"))

    same, other = ("true", "false") if called else ("false", "true")
    assert line(same) == f"  met service: Kenya Met agrees, {'rain' if called else 'dry'}"
    assert line(other).startswith("  met service: Kenya Met calls ")
    assert "against the call's" in line(other)
    missing = render_drivers(_inputs(predictions=rows), [])
    assert "  met service: no forecast from Kenya Met this run" in missing
    assert "met service" not in render_drivers(_inputs(met_service_name=None), [])


def _entry(rain, high):
    return {"served_call": {"today_properties": {"rain": rain, "temp_high_c": high}}}


def test_recent_calls_pairs_the_call_with_what_was_observed():
    entries = {"2026-11-23": _entry(True, 30.5), "2026-11-22": _entry(False, 31.0), "2026-11-20": {}}
    actuals = {"2026-11-23": {"rain": False, "precip_mm": 0.3, "high_c": 31.4, "precipitation": True, "thunder": True}}

    got = recent_calls(entries, actuals, "2026-11-24")

    assert [r["date"] for r in got] == ["2026-11-23", "2026-11-22"], "a day without a served call is skipped"
    assert got[0] == {
        "date": "2026-11-23", "days_ago": 1, "called_rain": True, "called_high_c": 30.5,
        "observed_rain": False, "observed_precip_mm": 0.3, "observed_high_c": 31.4,
        "station_rain": True, "station_thunder": True,
    }
    assert got[1]["observed_rain"] is None


def test_yesterday_shows_the_score_and_the_station_beside_it():
    entries = {"2026-11-23": _entry(True, 30.5), "2026-11-22": _entry(False, 31.0), "2026-11-21": _entry(True, 30.0)}
    actuals = {
        "2026-11-23": {"rain": False, "precip_mm": 0.3, "high_c": 31.4, "precipitation": True, "thunder": True},
        "2026-11-22": {"rain": False, "precip_mm": 0.0, "high_c": 30.0},
    }

    text = render_drivers(_inputs(), recent_calls(entries, actuals, "2026-11-24"))

    assert (
        "  yesterday: called rain, observed dry (0.3 mm), the station reporting rain and thunder; "
        "called a high of 30.5 °C, observed 31.4 °C"
    ) in text
    assert "  the rain call: right on 1 of the last 2 days observed" in text, "an unobserved day is not scored"


def test_yesterday_not_yet_observed():
    text = render_drivers(_inputs(), recent_calls({"2026-11-23": _entry(True, 30.5)}, {}, "2026-11-24"))

    assert "  yesterday: called rain; not yet observed" in text
    assert "the rain call" not in text


def test_thunder_findings_only_while_convective():
    i = _inputs()
    finding = {"kind": "convective", "checks": 40, "claim": "At Day+0, gfs_seamless does not see this location's thunderstorms coming."}
    stormy = copy.deepcopy(i.instability)
    stormy["convective"] = True
    calm = {**stormy, "convective": False}

    assert "  the record, thunder: GFS does not see this location's thunderstorms coming" in render_drivers(
        _inputs(instability=stormy, review_findings=[finding]), [],
    )
    assert "the record, thunder" not in render_drivers(_inputs(instability=calm, review_findings=[finding]), [])
    assert "no model above the thunderstorm threshold" in render_drivers(_inputs(instability=calm), [])


def test_a_day_with_nothing_to_say_renders_nothing():
    assert render_drivers(BriefInputs(), []) == ""
