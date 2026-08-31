"""Hand-computed expectations for aligned_cycle_at — see cycle.py's module
docstring for what the table means and why it is an inference, not an
observation."""

from datetime import datetime, timedelta, timezone

import pytest

from openlocalweather.cycle import aligned_cycle_at


def utc(y, m, d, h, minute=0, second=0):
    return datetime(y, m, d, h, minute, second, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The five rows, each at its cleanest instant (the hour the window opens)
# ---------------------------------------------------------------------------


def test_20_00_or_later_is_12z_today():
    got = aligned_cycle_at(utc(2026, 8, 11, 20, 0, 0))
    assert got.initialised_at == utc(2026, 8, 11, 12, 0, 0)
    assert got.window_opened_at == utc(2026, 8, 11, 20, 0, 0)
    assert got.age_hours == 8.0


def test_14_to_19_59_is_06z_today():
    got = aligned_cycle_at(utc(2026, 8, 11, 14, 0, 0))
    assert got.initialised_at == utc(2026, 8, 11, 6, 0, 0)
    assert got.window_opened_at == utc(2026, 8, 11, 14, 0, 0)
    assert got.age_hours == 8.0


def test_08_to_13_59_is_00z_today():
    got = aligned_cycle_at(utc(2026, 8, 11, 8, 0, 0))
    assert got.initialised_at == utc(2026, 8, 11, 0, 0, 0)
    assert got.window_opened_at == utc(2026, 8, 11, 8, 0, 0)
    assert got.age_hours == 8.0


def test_02_to_07_59_is_18z_yesterday():
    got = aligned_cycle_at(utc(2026, 8, 11, 2, 0, 0))
    assert got.initialised_at == utc(2026, 8, 10, 18, 0, 0)
    assert got.window_opened_at == utc(2026, 8, 11, 2, 0, 0)
    assert got.age_hours == 8.0


def test_00_to_01_59_is_12z_yesterday():
    got = aligned_cycle_at(utc(2026, 8, 11, 0, 0, 0))
    assert got.initialised_at == utc(2026, 8, 10, 12, 0, 0)
    # This window opened the PREVIOUS day at 20:00, not "today" at all —
    # the one row where window_opened_at's date differs from initialised_at's.
    assert got.window_opened_at == utc(2026, 8, 10, 20, 0, 0)
    assert got.age_hours == 12.0


# ---------------------------------------------------------------------------
# Both sides of each boundary. A UTC hour is a half-open interval
# [threshold, threshold+6) in this table; the instant one second before a
# threshold must still land in the PREVIOUS row.
# ---------------------------------------------------------------------------


def test_boundary_08_00_vs_07_59_59():
    just_before = aligned_cycle_at(utc(2026, 8, 11, 7, 59, 59))
    at_boundary = aligned_cycle_at(utc(2026, 8, 11, 8, 0, 0))
    assert just_before.initialised_at == utc(2026, 8, 10, 18, 0, 0)  # still 18z yesterday
    assert at_boundary.initialised_at == utc(2026, 8, 11, 0, 0, 0)  # now 00z today


def test_boundary_14_00_vs_13_59_59():
    just_before = aligned_cycle_at(utc(2026, 8, 11, 13, 59, 59))
    at_boundary = aligned_cycle_at(utc(2026, 8, 11, 14, 0, 0))
    assert just_before.initialised_at == utc(2026, 8, 11, 0, 0, 0)  # still 00z today
    assert at_boundary.initialised_at == utc(2026, 8, 11, 6, 0, 0)  # now 06z today


def test_boundary_20_00_vs_19_59_59():
    just_before = aligned_cycle_at(utc(2026, 8, 11, 19, 59, 59))
    at_boundary = aligned_cycle_at(utc(2026, 8, 11, 20, 0, 0))
    assert just_before.initialised_at == utc(2026, 8, 11, 6, 0, 0)  # still 06z today
    assert at_boundary.initialised_at == utc(2026, 8, 11, 12, 0, 0)  # now 12z today


def test_boundary_02_00_vs_01_59_59():
    just_before = aligned_cycle_at(utc(2026, 8, 11, 1, 59, 59))
    at_boundary = aligned_cycle_at(utc(2026, 8, 11, 2, 0, 0))
    assert just_before.initialised_at == utc(2026, 8, 10, 12, 0, 0)  # still 12z yesterday
    assert at_boundary.initialised_at == utc(2026, 8, 10, 18, 0, 0)  # now 18z yesterday


def test_midnight_continuity_same_cycle_both_sides():
    """23:59:59 (today's 12z, framed as "today") and 00:00:00 the next day
    (the same 12z run, now framed as "yesterday") name the SAME initialised
    run — the 12z window is the one row that spans midnight, so it is
    written as two date-relative branches rather than one. If this ever
    disagreed, the aligned cycle would appear to jump at midnight with no
    new data having landed."""
    just_before_midnight = aligned_cycle_at(utc(2026, 8, 11, 23, 59, 59))
    just_after_midnight = aligned_cycle_at(utc(2026, 8, 12, 0, 0, 0))
    assert just_before_midnight.initialised_at == utc(2026, 8, 11, 12, 0, 0)
    assert just_after_midnight.initialised_at == utc(2026, 8, 11, 12, 0, 0)


# ---------------------------------------------------------------------------
# Real-world cross-checks against docs-internal/ROADMAP.md's own measured
# rows, and the incident this function exists to make visible.
# ---------------------------------------------------------------------------


def test_age_hours_matches_roadmap_measured_row_at_03_00_utc():
    """ROADMAP.md's own table: 03:00 UTC, 18z, "9 h" — the morning run's
    current schedule."""
    got = aligned_cycle_at(utc(2026, 8, 11, 3, 0, 0))
    assert got.initialised_at == utc(2026, 8, 10, 18, 0, 0)
    assert got.age_hours == 9.0


def test_age_hours_matches_roadmap_measured_row_at_15_00_utc():
    """ROADMAP.md's own table: 15:00 UTC, 06z, "9 h" — the recommended
    evening slot."""
    got = aligned_cycle_at(utc(2026, 8, 11, 15, 0, 0))
    assert got.initialised_at == utc(2026, 8, 11, 6, 0, 0)
    assert got.age_hours == 9.0


def test_the_00_27_incident_this_function_exists_to_surface():
    """2026-08-28: a run at 00:27 UTC produced that day's first, scored
    forecast from data over twelve hours old, with nothing in the record
    saying so. See cycle.py's module docstring."""
    got = aligned_cycle_at(utc(2026, 8, 28, 0, 27, 0))
    assert got.initialised_at == utc(2026, 8, 27, 12, 0, 0)
    assert got.window_opened_at == utc(2026, 8, 27, 20, 0, 0)
    assert got.age_hours == 12.45


# ---------------------------------------------------------------------------
# The timezone-aware-UTC precondition
# ---------------------------------------------------------------------------


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        aligned_cycle_at(datetime(2026, 8, 11, 8, 0, 0))


def test_non_utc_aware_datetime_is_rejected():
    east_africa = timezone(timedelta(hours=3))
    with pytest.raises(ValueError):
        aligned_cycle_at(datetime(2026, 8, 11, 8, 0, 0, tzinfo=east_africa))


# ---------------------------------------------------------------------------
# next_aligned_window — ROADMAP item 10's shared half, used by 53.4's notice
# ---------------------------------------------------------------------------

from openlocalweather.cycle import next_aligned_window


def test_the_next_window_is_the_one_after_the_current_cycle():
    # 03:00Z sits in the window that opened at 02:00 carrying 18z. The next
    # one opens at 08:00 and brings 00z.
    nxt = next_aligned_window(datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc))
    assert nxt.opens_at == datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    assert nxt.initialised_at == datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)


def test_the_last_window_of_the_day_rolls_to_tomorrow():
    nxt = next_aligned_window(datetime(2026, 8, 28, 21, 30, tzinfo=timezone.utc))
    assert nxt.opens_at == datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
    assert nxt.initialised_at == datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def test_before_the_first_window_of_the_day():
    nxt = next_aligned_window(datetime(2026, 8, 28, 0, 30, tzinfo=timezone.utc))
    assert nxt.opens_at == datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def test_exactly_on_a_boundary_looks_forward_not_at_itself():
    """08:00Z has just opened its own window. The NEXT one is 14:00, not the
    one standing open — otherwise a notice would tell a reader to wait for
    guidance they already have."""
    nxt = next_aligned_window(datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc))
    assert nxt.opens_at == datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)


def test_it_agrees_with_aligned_cycle_at_across_a_full_day():
    """Swept rather than sampled: the next window's cycle must be exactly what
    aligned_cycle_at reports one minute after it opens. Two encodings of one
    table is how they drift."""
    from openlocalweather.cycle import aligned_cycle_at

    now = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
    for _ in range(24 * 60):
        nxt = next_aligned_window(now)
        just_after = aligned_cycle_at(nxt.opens_at + timedelta(minutes=1))
        assert just_after.initialised_at == nxt.initialised_at, now
        assert just_after.window_opened_at == nxt.opens_at, now
        now += timedelta(minutes=1)


def test_next_window_rejects_a_naive_datetime():
    with pytest.raises(ValueError):
        next_aligned_window(datetime(2026, 8, 28, 8, 0))
