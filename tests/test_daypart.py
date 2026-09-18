"""Where the current moment sits in the day.

The bug that prompted this module: the evening run fires at 18:15 and reads
like an early-afternoon summary. The cause was not wording — the prompt
carried a date and no time, so the model could not tell 06:00 from 18:00.

These tests are mostly about the failure directions. A phase function that
returns something plausible for every input is easy; one that refuses to say
"evening" while the sun is still up is the point.
"""

from datetime import datetime

from openlocalweather.daypart import (
    TODAY,
    TOMORROW,
    TONIGHT,
    REST_OF_TODAY_TO_MIDNIGHT,
    classify_phase,
    daypart_without_sun,
    forward_hours,
    reconcile_now,
    summarize_daypart,
)

# Kisumu, 2026-08-22 — the real figures from Open-Meteo on the day this was
# written, including the sunset that started the whole investigation.
SUNRISE = datetime(2026, 8, 22, 6, 40)
SUNSET = datetime(2026, 8, 22, 18, 47)
NEXT_SUNRISE = datetime(2026, 8, 23, 6, 40)


def at(hour, minute=0):
    return datetime(2026, 8, 22, hour, minute)


def test_the_1815_run_is_dusk_not_evening_and_not_afternoon():
    """The measured case. Sunset was 18:47, so the run fires 32 minutes before
    it — genuinely not evening yet, and long past 'afternoon' as a reader
    standing outside would judge it."""
    d = summarize_daypart(at(18, 15), SUNRISE, SUNSET, NEXT_SUNRISE)
    assert d.phase == "dusk"
    assert d.minutes_to_sunset == 32
    assert d.statement == "It is 18:15. Sunset is in 32 minutes."


def test_the_horizon_moves_off_today_once_most_of_it_has_happened():
    """By dusk, describing the day that has already happened helps nobody
    decide anything. What is left is tonight and tomorrow."""
    assert summarize_daypart(at(9), SUNRISE, SUNSET).horizon[0] == TODAY
    assert summarize_daypart(at(18, 15), SUNRISE, SUNSET).horizon == (
        TONIGHT,
        TOMORROW,
    )


def test_tonight_is_defined_rather_than_left_to_interpretation():
    """Otherwise one run reads it as "this evening" and the next as "the small
    hours", and a reader comparing two issuances sees a contradiction that is
    really just two definitions."""
    assert "dusk" in TONIGHT and "overnight" in TONIGHT and "dawn" in TONIGHT


def test_phases_follow_the_SUN_not_the_clock():
    """The same clock time is a different part of the day at a different
    latitude or season. This is the reason the module exists rather than a
    lookup table of hours: at 60°N, 18:00 is mid-afternoon in June and long
    after dark in December."""
    # Kisumu in August: 18:00 is nearly sunset.
    assert classify_phase(at(18), SUNRISE, SUNSET) == "dusk"

    # A far northern summer day, same clock time, sun still high.
    north_rise = datetime(2026, 8, 22, 3, 30)
    north_set = datetime(2026, 8, 22, 22, 30)
    assert classify_phase(at(18), north_rise, north_set) == "afternoon"

    # A far northern winter day: the sun is long gone by 18:00.
    winter_rise = datetime(2026, 8, 22, 9, 30)
    winter_set = datetime(2026, 8, 22, 15, 15)
    assert classify_phase(at(18), winter_rise, winter_set) == "evening"


def test_dawn_knows_which_side_of_sunrise_it_is_on():
    """'The sun rises at 06:40' ten minutes after it did is the kind of error
    a reader spots by looking out of a window."""
    before = summarize_daypart(at(6, 20), SUNRISE, SUNSET)
    after = summarize_daypart(at(6, 50), SUNRISE, SUNSET)
    assert before.phase == after.phase == "dawn"
    assert before.statement == "It is 06:20. Sunrise is in 20 minutes."
    assert after.statement == "It is 06:50. The sun rose at 06:40."


def test_after_dark_it_names_TOMORROW_s_sunrise():
    """Night spans midnight. Pointing at this morning's sunrise, seventeen
    hours gone, would be both useless and obviously wrong."""
    d = summarize_daypart(at(23), SUNRISE, SUNSET, NEXT_SUNRISE)
    assert d.phase == "night"
    assert "06:40" in d.statement  # tomorrow's, same clock time here
    d_no_next = summarize_daypart(at(23), SUNRISE, SUNSET)
    assert "06:40" in d_no_next.statement  # degrades, never crashes


def test_daylight_left_is_never_negative():
    """A forecast saying '-3 hours of daylight left' would be absurd, and the
    subtraction that produces it is the obvious way to write this."""
    for hour in (2, 6, 12, 18, 20, 23):
        assert summarize_daypart(at(hour), SUNRISE, SUNSET).daylight_hours_left >= 0
    assert summarize_daypart(at(20), SUNRISE, SUNSET).daylight_hours_left == 0


def test_it_rounds_daylight_DOWN():
    """'Two hours of daylight left' should not be said with 2h05m of it, and
    certainly not with 1h55m."""
    d = summarize_daypart(at(16, 45), SUNRISE, SUNSET)  # 2h02m to sunset
    assert d.daylight_hours_left == 2


def test_the_midnight_sun_is_not_reported_as_dusk():
    """Open-Meteo reports Longyearbyen in June as sunrise 00:00 with sunset
    exactly 24 hours later. The ordinary logic reads that as a normal day with
    a very late dusk, and would tell a reader the light is going while the sun
    sits well above the horizon."""
    rise = datetime(2026, 6, 21, 0, 0)
    set_ = datetime(2026, 6, 22, 0, 0)
    d = summarize_daypart(datetime(2026, 6, 21, 23, 0), rise, set_)
    assert d.phase.startswith("polar_")
    assert d.statement == "It is 23:00. The sun does not set at this time of year."


def test_polar_night_says_the_sun_does_not_rise():
    """Not verified against live data — the forecast API will not reach a
    December date from here — so this pins the intended behaviour rather than
    a measured response shape."""
    rise = datetime(2026, 12, 21, 11, 30)
    set_ = datetime(2026, 12, 21, 12, 30)
    d = summarize_daypart(datetime(2026, 12, 21, 10, 0), rise, set_)
    assert d.phase == "polar_night"
    assert "does not rise" in d.statement


def test_every_phase_has_a_horizon_and_a_statement():
    """A missing key here would be a KeyError in the middle of a live run, and
    the phases are the one thing guaranteed to change with latitude."""
    seen = set()
    for hour in range(24):
        for minute in (0, 30):
            d = summarize_daypart(at(hour, minute), SUNRISE, SUNSET, NEXT_SUNRISE)
            seen.add(d.phase)
            assert d.statement and d.horizon
    assert {"night", "dawn", "morning", "midday", "afternoon", "dusk", "evening"} <= seen


# --- trimming the forward window -------------------------------------------


def _hourly(start_hour: int, count: int) -> dict:
    """Two days of multi-model hourly data, in Open-Meteo's shape."""
    times, temps = [], []
    for i in range(count):
        hour = start_hour + i
        day = 22 + hour // 24
        times.append(f"2026-08-{day:02d}T{hour % 24:02d}:00")
        temps.append(float(hour))
    return {
        "latitude": -0.09,
        "hourly": {
            "time": times,
            "temperature_2m_gfs_seamless": temps,
            "temperature_2m_ecmwf_ifs025": list(reversed(temps)),
        },
    }


def test_the_evening_run_finally_gets_tonight_and_tomorrow():
    """The reason this module exists. At 18:15 the old fetch gave six hours of
    forecast and no overnight data, while the prompt asked for tonight and
    tomorrow."""
    trimmed = forward_hours(_hourly(0, 48), at(18, 15))["hourly"]["time"]
    assert trimmed[0] == "2026-08-22T18:00"
    assert len(trimmed) == 30
    assert any(t.startswith("2026-08-23") for t in trimmed), "tomorrow is covered"


def test_already_happened_hours_are_dropped():
    """Given a full day, the model described the full day — including the
    afternoon its readers had just lived through."""
    trimmed = forward_hours(_hourly(0, 48), at(18, 15))["hourly"]["time"]
    assert not any(t < "2026-08-22T18:00" for t in trimmed)


def test_the_hour_you_are_standing_in_is_kept():
    """At 18:15 someone still cares what 18:00-19:00 holds. Rounding forward
    would silently drop it."""
    assert forward_hours(_hourly(0, 48), at(18, 15))["hourly"]["time"][0] == (
        "2026-08-22T18:00"
    )


def test_every_model_series_is_trimmed_in_step():
    """Trimming time but not the values, or one model but not another, would
    silently misalign every reading with its hour — the kind of fault that
    produces a confident forecast about the wrong moment."""
    out = forward_hours(_hourly(0, 48), at(18, 15))["hourly"]
    lengths = {k: len(v) for k, v in out.items()}
    assert len(set(lengths.values())) == 1, lengths


def test_data_that_does_not_cover_now_returns_empty_rather_than_stale():
    """A cache or a timezone mismatch could hand back the wrong day dressed as
    the right one. Empty is detectable; wrong is not."""
    out = forward_hours(_hourly(0, 48), datetime(2030, 1, 1, 12, 0))
    assert out["hourly"]["time"] == []


def test_it_survives_empty_and_missing_input():
    assert forward_hours({}, at(12)) == {}
    assert forward_hours({"hourly": {}}, at(12)) == {"hourly": {}}


# --- trusting the clock -----------------------------------------------------


def test_a_correct_clock_is_left_alone():
    """Small differences are normal and change nothing a forecast says."""
    got, warning = reconcile_now(datetime(2026, 8, 22, 7, 1), "Sat, 22 Aug 2026 04:00:00 GMT", 10800)
    assert got == datetime(2026, 8, 22, 7, 1)
    assert warning is None


def test_a_wrong_clock_is_corrected_and_reported():
    """A host whose clock has drifted produces a forecast written confidently
    for the wrong part of the day, and nothing in the output would show it.
    One machine's clock against a public API's is not a close contest."""
    got, warning = reconcile_now(datetime(2026, 8, 22, 19, 0), "Sat, 22 Aug 2026 04:00:00 GMT", 10800)
    assert got == datetime(2026, 8, 22, 7, 0), "the server is believed"
    assert warning is not None and "NTP" in warning


def test_it_reconstructs_local_time_without_the_tz_database():
    """The backup has to work on a container built without tzdata, where
    ZoneInfo raises and there is no local timezone to fall back to. The Date
    header plus utc_offset_seconds needs neither."""
    got, _ = reconcile_now(datetime(2026, 8, 22, 19, 0), "Sat, 22 Aug 2026 04:00:00 GMT", 10800)
    assert got.hour == 7  # 04:00 UTC + 3h, derived purely from the response


def test_a_missing_or_unparseable_header_stops_checking_rather_than_guessing():
    """An absent header is not evidence the clock is wrong. Treating it as
    such would make every offline or proxied run 'correct' itself to nothing."""
    for header in (None, "", "not a date"):
        got, warning = reconcile_now(datetime(2026, 8, 22, 7, 0), header, 10800)
        assert got == datetime(2026, 8, 22, 7, 0)
        assert warning is None
    got, warning = reconcile_now(datetime(2026, 8, 22, 7, 0), "Sat, 22 Aug 2026 04:00:00 GMT", None)
    assert warning is None, "no offset means no comparison is possible"


def test_without_the_sun_the_horizon_is_still_precise():
    """Midnight is midnight at every latitude. Losing sunrise and sunset costs
    the phase, not the precision — "tonight" would be a guess about where the
    sun is, "the rest of today" would not."""
    d = daypart_without_sun(datetime(2026, 8, 22, 18, 15))
    assert d.phase == "unknown"
    assert d.horizon == (REST_OF_TODAY_TO_MIDNIGHT, TOMORROW)
    assert "midnight" in d.horizon[0]
    assert "It is 18:15" in d.statement


# --- ROADMAP item 158, step 1: the thunder's timing in the sun's words ---------
#
# Every case is Kisumu on 2026-09-18: sunrise 06:31, sunset 18:38, so dusk
# begins 17:08, evening runs to 22:38, and the next dawn opens at 05:31.

from openlocalweather.daypart import ConvectiveTiming, convective_timing, describe_convective_timing, onset_word

_RISE = datetime(2026, 9, 18, 6, 31)
_SET = datetime(2026, 9, 18, 18, 38)
_NEXT_RISE = datetime(2026, 9, 19, 6, 31)


def _timing(now, onset, peak):
    return convective_timing(onset, peak, now=now, sunrise=_RISE, sunset=_SET, next_sunrise=_NEXT_RISE)


def _clause(now, onset, peak):
    return describe_convective_timing(_timing(now, onset, peak))


def test_the_2026_09_18_run_at_dawn():
    assert _clause(datetime(2026, 9, 18, 6, 1), "2026-09-18T15:00", "2026-09-19T01:00") == (
        "thunder possible from the afternoon, peaking overnight"
    )


def test_a_morning_run_reads_the_same():
    assert _clause(datetime(2026, 9, 18, 9, 0), "2026-09-18T15:00", "2026-09-19T01:00") == (
        "thunder possible from the afternoon, peaking overnight"
    )


def test_an_onset_already_passed_is_not_named():
    assert _clause(datetime(2026, 9, 18, 12, 0), "2026-09-18T11:00", "2026-09-19T01:00") == (
        "thunder possible, peaking overnight"
    )


def test_an_afternoon_run_sees_the_evening_and_tomorrow_morning():
    assert _clause(datetime(2026, 9, 18, 15, 30), "2026-09-18T18:00", "2026-09-19T09:00") == (
        "thunder possible from the evening, peaking tomorrow morning"
    )


def test_dusk_is_the_evening():
    assert _clause(datetime(2026, 9, 18, 18, 0), "2026-09-18T18:30", "2026-09-19T01:00") == (
        "thunder possible from the evening, peaking overnight"
    )


def test_onset_and_peak_in_one_phase_is_one_word():
    assert _clause(datetime(2026, 9, 18, 20, 0), "2026-09-18T23:00", "2026-09-19T01:00") == (
        "thunder possible overnight"
    )


def test_a_2200_run_rolls_the_late_evening_into_overnight():
    # 23:30 is past the evening's end at 22:38, so it is overnight — never
    # "this evening" at an hour when the reader is going to bed.
    assert _clause(datetime(2026, 9, 18, 22, 0), "2026-09-18T23:30", "2026-09-19T02:00") == (
        "thunder possible overnight"
    )


def test_before_midnight_the_small_hours_are_still_overnight():
    assert _clause(datetime(2026, 9, 18, 23, 30), "2026-09-19T00:30", "2026-09-19T04:00") == (
        "thunder possible overnight"
    )


def test_after_midnight_the_coming_day_is_today():
    # A 02:00 run on the 19th: its sun is the 19th's, and 09:00 is this morning.
    rise, set_, next_rise = datetime(2026, 9, 19, 6, 31), datetime(2026, 9, 19, 18, 38), datetime(2026, 9, 20, 6, 31)
    t = convective_timing("2026-09-19T09:00", "2026-09-19T15:00", now=datetime(2026, 9, 19, 2, 0),
                          sunrise=rise, sunset=set_, next_sunrise=next_rise)
    assert describe_convective_timing(t) == "thunder possible from the morning, peaking this afternoon"


def test_an_evening_run_names_tomorrow():
    assert _clause(datetime(2026, 9, 18, 20, 0), "2026-09-19T09:00", "2026-09-19T15:00") == (
        "thunder possible from tomorrow morning, peaking tomorrow afternoon"
    )


def test_no_sun_withholds_the_clause():
    assert convective_timing("2026-09-18T15:00", "2026-09-19T01:00", now=datetime(2026, 9, 18, 6, 1),
                             sunrise=None, sunset=None, next_sunrise=None) is None


def test_no_peak_is_no_timing():
    assert _timing(datetime(2026, 9, 18, 6, 1), None, None) is None


def test_the_timing_carries_its_parts_for_the_composers():
    t = _timing(datetime(2026, 9, 18, 6, 1), "2026-09-18T15:00", "2026-09-19T01:00")
    assert t == ConvectiveTiming(onset="from the afternoon", peak="overnight", onset_passed=False)


def test_the_rain_onset_words_follow_the_sun_too():
    now = datetime(2026, 9, 18, 6, 1)
    word = lambda hhmm: onset_word(hhmm, now=now, sunrise=_RISE, sunset=_SET)
    assert word("07:00") == "from the morning"
    assert word("11:00") == "from the morning"
    assert word("13:00") == "afternoon"
    assert word("17:30") == "evening"
    assert word("20:00") == "evening"
    assert onset_word("13:00", now=now, sunrise=None, sunset=None) is None
