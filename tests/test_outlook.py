"""The Extended Outlook in code — ROADMAP item 190 step 3."""

from openlocalweather.outlook import (
    ExtendedDay,
    LeadRecord,
    OutlookInputs,
    describe_extended_outlook,
    lead_records,
)
from openlocalweather.phrasing import phrase_defect

MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "ukmo_seamless"]
NAMES = {1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday", 7: "Monday"}


def _day(lead, *, wet=0, precip=0.5, high=31.0, spread=1.0, low=19.0, wind=30.0, thunder=None, sky="Mostly cloudy",
         models=MODELS, by_model=None):
    return ExtendedDay(
        lead_time_days=lead, date=f"2026-10-{5 + lead:02d}", day_name=NAMES[lead], models=list(models),
        wet_votes=wet, precip_mm=precip, precip_by_model=by_model or {m: precip for m in models},
        high_c=high, high_min_c=high - spread, high_max_c=high + spread, low_c=low, wind_kmh=wind,
        thunder=thunder, sky=sky,
    )


def _inputs(days, **changes):
    base = dict(days=days, today_high_c=31.0, today_wind_kmh=30.0, served={}, records=[],
                met_service_name=None, met_service_day3_rain=None)
    return OutlookInputs(**{**base, **changes})


def test_a_steady_week_reads_as_steady_in_both_spans():
    days = [_day(n) for n in range(1, 8)]

    text = describe_extended_outlook(_inputs(days))

    near, far = text.split("\n\n")
    assert near == "Conditions much the same through Thursday. Skies mostly cloudy throughout."
    assert far == "Further out, conditions much the same through Monday."
    for paragraph in (near, far):
        for sentence in paragraph.split(". "):
            assert phrase_defect(sentence if sentence.endswith(".") else sentence + ".") is None, sentence


def test_rain_gaining_support_is_said_in_votes_never_in_a_probability():
    days = [_day(1, wet=1, precip=0.2), _day(2, wet=2, precip=3.0), _day(3, wet=4, precip=9.0)]

    text = describe_extended_outlook(_inputs(days))

    assert "Rain gains support through Thursday, from one of four models Tuesday to all four models Thursday." in text
    assert "%" not in text


def test_support_moving_by_one_vote_or_both_ways_is_not_a_trend():
    assert "support" not in describe_extended_outlook(_inputs([_day(1, wet=2), _day(2, wet=2), _day(3, wet=3)]))
    assert "support" not in describe_extended_outlook(_inputs([_day(1, wet=0), _day(2, wet=3), _day(3, wet=1)]))


def test_a_day_that_stands_out_for_wind_is_named_with_its_gust():
    days = [_day(1, wind=30.0), _day(2, wind=41.0), _day(3, wind=31.0)]

    text = describe_extended_outlook(_inputs(days))

    assert "Windier on Wednesday, gusts to 41 km/h." in text


def test_a_wind_already_in_the_trend_clause_is_not_said_twice():
    days = [_day(1, wind=30.0), _day(2, wind=35.0), _day(3, wind=45.0)]

    text = describe_extended_outlook(_inputs(days))

    assert "Becoming windier through Thursday." in text
    assert "Windier on" not in text


def test_the_scored_call_carries_the_served_probability_the_votes_and_the_met_service():
    days = [_day(1), _day(2), _day(3, wet=2, high=31.5, spread=2.5)]
    inputs = _inputs(days, served={"3": {"rain": True, "rain_probability_pct": 68}},
                     met_service_name="Kenya Meteorological Department (KMD)", met_service_day3_rain=False)

    text = describe_extended_outlook(inputs)

    assert ("Thursday's call: rain, 68%, with two of four models wet; highs 29 to 34 °C. "
            "Kenya Meteorological Department (KMD) calls it dry.") in text


def test_the_record_ranks_a_model_or_says_it_cannot():
    days = [_day(1), _day(2), _day(3)]
    ranked = _inputs(days, records=[LeadRecord(3, "ecmwf_ifs025", 77.0, 52)])
    thin = _inputs(days, records=[LeadRecord(3)])

    assert "At this lead ECMWF has the best record, right 77% of the time over 52 checks." in describe_extended_outlook(ranked)
    assert "The record at this lead is too short to rank the models." in describe_extended_outlook(thin)


def test_the_far_span_is_measured_from_thursday_and_names_who_still_reaches():
    near = [_day(1), _day(2), _day(3, high=31.0, wind=30.0)]
    far = [_day(4, high=29.0), _day(5, high=27.5, models=MODELS[:3]), _day(6, high=26.0, models=MODELS[:2]),
           _day(7, high=25.0, models=MODELS[:2])]

    text = describe_extended_outlook(_inputs(near + far))
    _, far_text = text.split("\n\n")

    assert far_text.startswith("Further out, cooling through Monday")
    assert "Only GFS, ECMWF and ICON reach past Friday." in far_text


def test_the_wetter_solutions_are_named_only_a_band_apart():
    wet = {"gfs_seamless": 0.5, "ecmwf_ifs025": 4.0, "icon_seamless": 3.5, "ukmo_seamless": 0.3}
    far_apart = [_day(n, by_model=wet) for n in range(4, 8)]
    near = [_day(1), _day(2), _day(3)]

    text = describe_extended_outlook(_inputs(near + far_apart))
    assert "ECMWF and ICON are the wetter solutions from Friday to Monday; GFS and UKMO keep it drier." in text

    close = [_day(n, by_model={m: 1.0 for m in MODELS}) for n in range(4, 8)]
    assert "solution" not in describe_extended_outlook(_inputs(near + close))


def test_a_single_model_reaching_day_seven_is_said_as_such():
    days = [_day(1), _day(2), _day(3), *[_day(n, wet=1, models=MODELS[:1], by_model={"gfs_seamless": 6.0}) for n in (4, 5, 6, 7)]]

    text = describe_extended_outlook(_inputs(days, served={"7": {"rain": True, "rain_probability_pct": 100}}))

    assert "Monday's call: rain, 100%, with the one model that reaches it wet" in text
    assert "Only GFS reaches past Thursday." in text


def test_lead_records_rank_only_models_with_ten_checks():
    class E:
        def __init__(self, model, lead, pct, checks):
            self.model, self.lead_time_days, self.rolling_30_rain_pct, self.all_time_checks = model, lead, pct, checks

    entries = [E("ecmwf_ifs025", 3, 77.0, 52), E("icon_seamless", 3, 90.0, 4), E("olw_blend", 3, 95.0, 40), E("gfs_seamless", 7, 60.0, 12)]

    records = lead_records(entries, visible_models=MODELS)

    assert records[0] == LeadRecord(3, "ecmwf_ifs025", 77.0, 52), "the blend is not visible and four checks are thin"
    assert records[1] == LeadRecord(7, "gfs_seamless", 60.0, 12)


def test_nothing_to_speak_about_is_none():
    assert describe_extended_outlook(_inputs([])) is None
