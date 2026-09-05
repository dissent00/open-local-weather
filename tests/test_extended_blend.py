"""The blend's own call beyond today — ROADMAP item 72, minimal shape.

Until this existed `olw_blend` had rows at Day+0 and nowhere else, because
`_blend_prediction` builds from `today_properties`. Day+0 is where the free
NWP is already near its ceiling, so the forecaster was measured exclusively
where it can least distinguish itself and unmeasured at the leads where
reconciling disagreement is worth most.
"""

import pytest

from openlocalweather.defaults import BLEND_MODEL_ID
from openlocalweather.llm.schema import ExtendedDayProperties
from openlocalweather.pipeline import _extended_blend_predictions


def _props(*leads):
    return [
        ExtendedDayProperties(lead_time_days=lead, rain=rain, rain_probability_pct=pct)
        for lead, rain, pct in leads
    ]


def test_a_committed_lead_becomes_a_scorable_row():
    got = _extended_blend_predictions(_props((3, True, 60)), 3)

    assert len(got) == 1
    assert got[0].model == BLEND_MODEL_ID
    assert got[0].rain is True
    assert got[0].rain_probability_pct == 60


def test_each_lead_takes_only_its_own_entry():
    """A Day+7 call landing in the Day+3 row would score the forecaster
    against a day it was not talking about, and nothing downstream could
    detect it — both rows look perfectly well-formed."""
    extended = _props((3, True, 60), (7, False, 20))

    assert _extended_blend_predictions(extended, 3)[0].rain is True
    assert _extended_blend_predictions(extended, 7)[0].rain is False


def test_declining_to_call_stores_nothing():
    """The honesty rule, in code. A boolean the forecaster invented is scored
    wrong exactly as confidently as one it meant, so a lead it left out must
    produce NO row rather than a default."""
    assert _extended_blend_predictions([], 3) == []
    assert _extended_blend_predictions(_props((3, True, 60)), 7) == []


def test_fields_the_forecaster_was_not_asked_for_stay_absent():
    """Absent, not zero. The minimal shape asks for rain only, and a 0.0 mm
    precip or a 0 km/h wind would enter the record as a value the forecaster
    never gave — then be scored as one."""
    got = _extended_blend_predictions(_props((3, True, 60)), 3)[0]

    assert got.precip_mm is None
    assert got.wind_kmh is None
    assert got.high_c is None
    assert got.low_c is None
    assert got.onset is None
    assert got.mslp_trend is None


def test_a_probability_is_optional_but_the_boolean_is_not():
    """rain_probability_pct None means "no confidence stated" and leaves the
    Brier column empty for that row, which is the three-valued discipline
    every other optional field here follows. The boolean is what the existing
    hit-rate scores and is required by the schema."""
    got = _extended_blend_predictions(
        [ExtendedDayProperties(lead_time_days=3, rain=True)], 3
    )

    assert got[0].rain is True
    assert got[0].rain_probability_pct is None

    with pytest.raises(Exception):
        ExtendedDayProperties(lead_time_days=3)


# ---------------------------------------------------------------------------
# Through the real pipeline. The builder above is a list comprehension; what
# these catch is whether the row reaches the RECORD, at the right lead, and
# without displacing anything already there.
# ---------------------------------------------------------------------------

from datetime import date

from openlocalweather.llm.schema import (
    GeminiForecastResponse,
    TodayProperties,
    VerificationNote,
)
from openlocalweather.pipeline import run_daily_pipeline
from openlocalweather.store import log_store

from tests.test_pipeline_run import make_deps, patch_fetches  # noqa: F401
from tests.test_pipeline_run import FakeLLMProvider

TODAY = date(2026, 8, 11)


def _response_with(extended):
    return GeminiForecastResponse(
        yesterday_verification="Fine.",
        verification_notes=[VerificationNote(lead_time_days=0, note="Accurate.")],
        skill_profile_summaries=[],
        today_properties=TodayProperties(
            rain=False,
            rain_expected="Unlikely",
            temp_high_c=27.0,
            temp_low_c=18.0,
            temp_high_low="27°C / 81°F",
        ),
        extended_properties=extended,
        today_narrative="## Overview\nQuiet.\n",
    )


def _stored(tmp_path, extended):
    llm = FakeLLMProvider(response=_response_with(extended))
    run_daily_pipeline(make_deps(tmp_path, llm=llm), today=TODAY, dry_run=False)
    return log_store.read_log_entry(tmp_path, TODAY).model_predictions


def _blend(rows):
    return [p for p in rows if p.model == BLEND_MODEL_ID]


def test_the_blend_now_has_a_row_at_day3_and_day7(tmp_path):
    stored = _stored(tmp_path, _props((3, True, 55), (7, False, 15)))

    assert _blend(stored.day3)[0].rain is True
    assert _blend(stored.day3)[0].rain_probability_pct == 55
    assert _blend(stored.day7)[0].rain is False
    assert _blend(stored.day7)[0].rain_probability_pct == 15


def test_day0_is_untouched_by_the_change(tmp_path):
    """The Day+0 blend comes from today_properties and must keep coming from
    there — it carries temperatures the extended rows deliberately do not."""
    stored = _stored(tmp_path, _props((3, True, 55)))

    day0 = _blend(stored.day0)[0]
    assert day0.high_c == 27.0
    assert day0.low_c == 18.0


def test_the_models_at_those_leads_are_not_displaced(tmp_path):
    """Appended, not substituted. A change that replaced the extracted model
    rows would leave the record with a forecaster and nothing to compare it
    against, which is worse than having no blend row at all."""
    with_blend = _stored(tmp_path, _props((3, True, 55)))

    assert len(with_blend.day3) > 1
    assert {p.model for p in with_blend.day3} > {BLEND_MODEL_ID}


def test_a_run_that_commits_to_nothing_stores_no_extended_blend(tmp_path):
    stored = _stored(tmp_path, [])

    assert _blend(stored.day3) == []
    assert _blend(stored.day7) == []
    assert _blend(stored.day0) != [], "Day+0 is not optional and is unaffected"
