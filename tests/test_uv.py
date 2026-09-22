"""The UV index: which day, which source — ROADMAP item 161."""

from datetime import date

from openlocalweather.daypart import REST_OF_TODAY, TODAY, TOMORROW, TONIGHT, UNTIL_DAWN


def _daily(uv_by_model, days=("2026-09-22", "2026-09-23", "2026-09-24")):
    block = {"daily": {"time": list(days)}}
    for model, values in uv_by_model.items():
        block["daily"][f"uv_index_max_{model}"] = list(values)
    return block


def test_the_uv_index_follows_the_horizon_and_names_its_source():
    """Today's maximum until the horizon rolls, then tomorrow's.

    THE RULE ALREADY EXISTS AND UV WAS THE ONE FIELD NOT FOLLOWING IT.
    `daypart._horizon_for` decides what a reader at this hour is waiting for:
    today is in it at dawn, morning, midday and afternoon, and GONE at dusk,
    evening and before midnight. The 18:01 run classifies as dusk and its own
    prompt says "WHAT MATTERS NOW: tonight, then tomorrow" — while the UV
    field reported a peak that happened around midday, six hours earlier.

    Measured over the 11 archived evening runs: the number changes on 6 and
    the BAND a reader sees changes on 2, once from High to Very high, which
    is the direction that matters.

    THE SOURCE IS NAMED, NOT DISCOVERED. Of the five models only
    `gfs_seamless` serves a UV index and `best_match` duplicates it value for
    value on all 28 archived issuances, so the prompt's "synthesized BLENDED
    call across all models" was never true of this field. The record stores
    which source answered so a national met service can be slotted in later
    without archaeology — item 167.
    """
    from openlocalweather.uv import day_uv_index

    daily = _daily({"gfs_seamless": [9.3, 7.5, 8.0], "best_match": [9.3, 7.5, 8.0]})

    ahead = day_uv_index(daily, horizon=(TODAY, TONIGHT), today=date(2026, 9, 22))
    assert ahead.index == 9.3
    assert ahead.source == "gfs_seamless"
    assert ahead.target_date == date(2026, 9, 22)

    # dusk: the horizon has rolled, so the answer is tomorrow's
    rolled = day_uv_index(daily, horizon=(TONIGHT, TOMORROW), today=date(2026, 9, 22))
    assert rolled.index == 7.5
    assert rolled.target_date == date(2026, 9, 23)

    # before midnight the same; after midnight today is back
    assert day_uv_index(daily, horizon=(UNTIL_DAWN, TOMORROW),
                        today=date(2026, 9, 22)).target_date == date(2026, 9, 23)
    assert day_uv_index(daily, horizon=(UNTIL_DAWN, TODAY),
                        today=date(2026, 9, 22)).target_date == date(2026, 9, 22)
    assert day_uv_index(daily, horizon=(REST_OF_TODAY, TONIGHT),
                        today=date(2026, 9, 22)).target_date == date(2026, 9, 22)
