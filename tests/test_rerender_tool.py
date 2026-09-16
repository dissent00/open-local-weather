"""The re-render tool's reconstruction of the forecaster's call.

ROADMAP item 142. The tool sends a model the call a real run would have sent
it, and for a day it did not: it built a FLAT dict of nine fields where
production sends a nested `GeminiJudgmentResponse` of two keys —
`today_properties` with thirteen fields, and `extended_properties`.

That is not a smaller version of the same document. The system prompt
addresses `today_properties.temp_high_c` by path and says the prose must
agree with `extended_properties`; against a flat dict the first has no
referent and the second is absent, so the renderer was being asked to agree
with a call it could not see, for five of the seven days it forecasts.

Caught by item 77's harness, twice — the first correction trusted the tool's
own docstring, which claimed the reconstruction was "exactly what it would
have seen".
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from datetime import datetime, timezone

from openlocalweather.llm.schema import GeminiJudgmentResponse, TodayProperties
from openlocalweather.models import (
    DailyLogEntry,
    LogEntryMeta,
    ModelPrediction,
    ModelPredictionsByLead,
)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "rerender_narrative", Path(__file__).resolve().parents[1] / "tools" / "rerender_narrative.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry() -> DailyLogEntry:
    return DailyLogEntry(
        date=date(2026, 9, 16),
        rain_expected="Showers late",
        temp_high_c=30.8,
        temp_low_c=18.2,
        temp_high_low_display="30.8/18.2",
        mslp_trend_24h="falling (-0.3 hPa)",
        synoptic_pattern="Strong regional gradient",
        narrative_markdown="narrative",
        onset_window="16:00-18:00",
        peak_wind_kmh=41.0,
        model_predictions=ModelPredictionsByLead(
            day0=[
                ModelPrediction(
                    model="olw_blend", rain=True, onset="17:00",
                    precip_mm=4.5, rain_probability_pct=85,
                )
            ],
            day3=[ModelPrediction(model="olw_blend", rain=True, rain_probability_pct=75)],
            day7=[ModelPrediction(model="olw_blend", rain=False, rain_probability_pct=20)],
        ),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc),
            llm_provider="test",
            llm_model="test",
            pipeline_version="0",
        ),
    )


def test_the_rebuilt_call_validates_as_the_object_production_sends():
    """THE ONE ASSERTION THAT MATTERS. Not "has the right keys" — parses as
    the exact schema `build_narrative_user_prompt` is handed."""
    rebuilt = _tool()._rebuild_judgment(_entry())

    GeminiJudgmentResponse.model_validate(rebuilt)

    assert set(rebuilt) == {"today_properties", "extended_properties"}


def test_every_today_properties_field_is_present():
    """Nine live on the entry and four only on the blend's scored row. A
    renderer told nothing about `rain` or `onset_hour` is being asked to
    agree with numbers it was not given."""
    rebuilt = _tool()._rebuild_judgment(_entry())

    assert set(rebuilt["today_properties"]) == set(TodayProperties.model_fields)


def test_it_is_nested_not_flat():
    """The shape IS the finding. A flat dict carrying the same numbers is a
    different document to a prompt that addresses fields by path."""
    rebuilt = _tool()._rebuild_judgment(_entry())

    assert "temp_high_c" not in rebuilt, "flat again — the defect this file exists for"
    assert rebuilt["today_properties"]["temp_high_c"] == 30.8


def test_a_day_with_no_blend_row_is_refused_rather_than_narrowed():
    """`rain` is non-nullable, so a missing blend row produces an object that
    does not validate — and shipping a partial call is the exact failure this
    file exists for. Refusing is the only honest option."""
    import pytest

    entry = _entry()
    entry.model_predictions.day0 = []

    with pytest.raises(SystemExit, match="cannot be rebuilt"):
        _tool()._rebuild_judgment(entry)


def test_extended_properties_carries_both_leads():
    """Five of the seven days a forecast covers live here. They were absent
    entirely until 2026-09-16."""
    rebuilt = _tool()._rebuild_judgment(_entry())

    assert [d["lead_time_days"] for d in rebuilt["extended_properties"]] == [3, 7]
    assert rebuilt["extended_properties"][1]["rain"] is False
