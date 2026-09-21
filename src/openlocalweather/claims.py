"""What a machine can check in the narrative, checked before it is published.

NOTHING WATCHED THE PROSE. Item 102 put a guard on the fields the forecaster
writes; the narrative itself has never been read by anything. It goes from the
model's response into `narrative_markdown` and onto the page.

Most of what a forecast asserts cannot be checked here — whether the evening
really will be thundery is not a question this module can answer, and it does
not try. What it checks is the narrow set of claims that are decidable with
certainty, and the first of those is the calendar: a weekday paired with a date
is either right or wrong, and nothing about the weather comes into it.

MEASURED BEFORE BUILDING, 2026-09-13: of 39 weekday/date pairings the published
record asserts, 7 are false, on four separate days — 2026-08-11, 08-12 and
08-13 published "Sunday, August 17" when the 17th was a Monday, and 2026-09-04
published "Sunday, September 7th" when the 7th was a Monday. Every one is off
by exactly one day. `dates.forward_calendar` now hands the pairings over finished so there
is no arithmetic left to do; this is what notices if one is wrong anyway.

A FINDING DOES NOT STOP A RUN. The operator's call, 2026-09-13: record it and
publish. Discarding a whole narrative over one wrong weekday costs the reader
far more than the error does, and a retry spends against a budget that item 111
already says cannot express "spend less per forecast". So this reports, the
entry stores what it found, and the count says whether a retry is ever worth
buying.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}

# "Monday (16 September)", "Monday, 16 September", "Monday 16 September".
_DAY_FIRST = re.compile(
    rf"\b({'|'.join(WEEKDAYS)})\b[\s,(]+(\d{{1,2}})(?:st|nd|rd|th)?\s+({'|'.join(MONTHS)})\b"
)
# "Monday (September 16)", "Monday, September 16".
_MONTH_FIRST = re.compile(
    rf"\b({'|'.join(WEEKDAYS)})\b[\s,(]+({'|'.join(MONTHS)})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b"
)
# "Monday, 2026-09-16".
_ISO = re.compile(rf"\b({'|'.join(WEEKDAYS)})\b[\s,(]+(\d{{4}})-(\d{{2}})-(\d{{2}})\b")

# How far from the run's own date a bare "17 August" may be resolved. Half a
# year each way, so the nearest occurrence is always unambiguous: a date more
# than six months off is closer to the other year's copy of itself.
_NEAREST_WINDOW_DAYS = 183

CLAIM_WEEKDAY = "weekday_date_mismatch"


def _nearest(today: date, month: int, day: int) -> date | None:
    """The occurrence of month/day closest to `today`.

    A YEAR IS RARELY WRITTEN AND MUST NOT BE ASSUMED. A forecast issued on
    31 December that mentions "Saturday, 2 January" means the NEXT year, and
    resolving that against the run's own year would invent a mismatch — a false
    alarm here is worse than the defect, because a check that cries wolf is one
    nobody reads.
    """
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            # 29 February in a non-leap year: not a date, in that year.
            continue

    if not candidates:
        return None

    best = min(candidates, key=lambda d: abs((d - today).days))
    if abs((best - today).days) > _NEAREST_WINDOW_DAYS:
        return None

    return best


def false_weekday_claims(text: str, today: date) -> list[dict]:
    """Every weekday/date pairing in `text` that the calendar contradicts.

    `today` anchors a bare month/day to a year — see `_nearest`. Returns one
    dict per false pairing, in the order they appear, each naming what was
    written and what the day actually was. An empty list means the check ran
    and found nothing, which is a different fact from never having run; the
    caller keeps them apart.
    """
    if not text:
        return []

    findings: list[dict] = []

    def record(match: re.Match, claimed: str, when: date | None) -> None:
        if when is None:
            return
        actual = WEEKDAYS[when.weekday()]
        if actual == claimed:
            return
        findings.append(
            {
                "kind": CLAIM_WEEKDAY,
                "quote": match.group(0).strip(),
                "detail": f"{when.isoformat()} is a {actual}, not a {claimed}",
            }
        )

    for match in _DAY_FIRST.finditer(text):
        claimed, day, month = match.group(1), match.group(2), match.group(3)
        record(match, claimed, _nearest(today, MONTHS[month], int(day)))

    for match in _MONTH_FIRST.finditer(text):
        claimed, month, day = match.group(1), match.group(2), match.group(3)
        record(match, claimed, _nearest(today, MONTHS[month], int(day)))

    for match in _ISO.finditer(text):
        claimed = match.group(1)
        try:
            when = date(int(match.group(2)), int(match.group(3)), int(match.group(4)))
        except ValueError:
            continue
        record(match, claimed, when)

    return findings


# A tile value long enough that the page cannot render it as one.
CLAIM_DISPLAY_TOO_LONG = "display_value_too_long"

# The ceiling, and it is READ OFF THE RECORD rather than chosen.
#
# `rain_expected` and `onset_window` are rendered in the stat grid beside
# "High / Low" and "UV Index" — a box with room for a phrase. No rule ever
# governed their length, and for a month none was needed: over the 32 days to
# 2026-09-11 every `rain_expected` but one came in at 48 characters or fewer,
# with the single exception at 49. "Dry / No Rain", "Evening Thunderstorms",
# "Isolated Evening Showers & Thunderstorms".
#
# THEN THE CALL SPLIT — item 59 step 3, 2026-09-11 — and the first run under
# it, on 09-12, wrote 56 characters. Every run since has been longer: 59, 81,
# 61, 59, 58, 101, 91, 149, 60. The old behaviour was EMERGENT, inferred from
# a combined prompt that also carried the prose rules, and splitting the call
# took the context away without anyone measuring what it had been holding up.
#
# So 48 is the boundary the model itself kept for a month, and it separates
# the two regimes almost perfectly: 1 of 32 above it before, 10 of 10 after.
# `onset_window` never crossed it at all — its longest is 46.
DISPLAY_VALUE_MAX_CHARS = 48

# Which fields are tiles. Named rather than inferred: `synoptic_pattern` and
# `mslp_trend_24h` are also short strings and are NOT rendered in the grid, so
# a check that bounded every short field would report a defect the page does
# not have.
DISPLAY_VALUE_FIELDS = ("rain_expected", "onset_window")


def overlong_display_values(properties: dict, limit: int = DISPLAY_VALUE_MAX_CHARS) -> list[dict]:
    """Every tile field whose value is too long for the box it renders in.

    RECORDED, NOT REFUSED, which is the same call the weekday check carries
    and for a stronger reason here: the schema's own `MAX_DISPLAY_STRING`
    comment says a bound tight enough to refuse a wordy but correct forecast
    leaves the day with no forecast at all, and this is a LAYOUT complaint.
    A reader would rather have an overlong tile than nothing. What the finding
    buys is that the drift is visible in the record on the day it starts,
    instead of a month later when somebody looks at the page.
    """
    found = []
    for field in DISPLAY_VALUE_FIELDS:
        value = properties.get(field)
        if not isinstance(value, str) or len(value) <= limit:
            continue

        found.append(
            {
                "kind": CLAIM_DISPLAY_TOO_LONG,
                "quote": value,
                "detail": (
                    f"{field} is {len(value)} characters; the page renders it "
                    f"as a tile and has room for {limit}."
                ),
            }
        )

    return found
