"""Date utilities shared by the fetch, verify, and store modules.

Small and dependency-free on purpose: this is exactly the kind of module
that's trivial to get subtly wrong (off-by-one lead-time math, timezone
drift), so it stays isolated and fully unit-tested rather than inlined
wherever a date is needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DATE_FMT = "%Y-%m-%d"


def today_in_tz(tz_name: str) -> date:
    """Today's date in the given IANA timezone (e.g. 'Africa/Nairobi')."""
    return datetime.now(ZoneInfo(tz_name)).date()


def now_in_tz(tz_name: str) -> datetime:
    """The current local wall-clock time, naive.

    Naive on purpose: it is compared against Open-Meteo's sunrise/sunset and
    hourly timestamps, which come back as naive local strings when
    `timezone=` is set. Mixing an aware datetime with those would raise, and
    making them aware instead would invite a UTC value to slip in unnoticed
    and shift every part-of-day boundary by hours.
    """
    return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)


def utc_offset_seconds(tz_name: str, d: date) -> int:
    """The location's offset from UTC on a given local date.

    Taken at local NOON, not midnight. A date's midnight can fall inside a
    daylight-saving gap or fold, where the offset is ambiguous or the wall
    clock does not exist; noon never is.

    One offset stands for the whole date, which is also Open-Meteo's
    convention — its responses carry a single `utc_offset_seconds`. On a
    changeover day an event on the far side of the transition is therefore an
    hour out, which for sunrise and sunset is a minute of daylight nobody
    plans around.
    """
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(tz_name))
    return int(noon.utcoffset().total_seconds())


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def format_date(d: date) -> str:
    return d.strftime(DATE_FMT)


def parse_date(s: str) -> date:
    return datetime.strptime(s, DATE_FMT).date()


def prediction_row_date_for_target(target_date: date, lead_time_days: int) -> date:
    """The date of the log entry that MADE a prediction targeting `target_date`
    at the given lead time.

    For a lead time of k days, a prediction made on date D targets date D + k.
    So the row that made a k-lead prediction FOR target_date is dated
    (target_date - k). This is the single place this date math lives — ported
    from getPredictionRowDateForTarget() in the original Apps Script pipeline,
    shared by both the scoring pass and the note-writing pass there, and kept
    that way here too. If lead-time verification is ever giving results that
    seem too early/late, this function is the first place to check.
    """
    return add_days(target_date, -lead_time_days)
