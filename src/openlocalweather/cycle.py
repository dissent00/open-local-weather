"""Which model RUN (initialisation cycle) is likely behind the guidance in
hand right now — an INFERENCE, not an observation.

WHY THIS EXISTS: nothing in an Open-Meteo `/v1/forecast` response says which
cycle produced it. Verified live: no JSON key, no HTTP header carries a run
time or "as of" stamp for any of the five models this project fetches. A
forecast built from that response therefore cannot say how old the guidance
behind it is — not approximately, not at all — unless something computes an
estimate from the clock instead.

WHERE THE TABLE COMES FROM: `docs-internal/ROADMAP.md` measured, twice on
2026-08-11 from each model's own `/data/{model}/static/meta.json`
availability delay, the windows in which ECMWF, GFS, ICON and UKMO are all
on the SAME cycle (search that file for "Aligned windows open at"): 02:00,
08:00, 14:00 and 20:00 UTC, carrying the 18z, 00z, 06z and 12z cycles
respectively.

WHAT THE ANSWER MEANS, PRECISELY. The same table records that a window stays
clean for only about two HOURS before the faster models jump a cycle ahead
of the slower ones. So outside those two hours the returned cycle is not
what every model is on — it is what the SLOWEST of them is still on, while
the faster ones have moved. Read it as a FLOOR on the age of the guidance
("some of this is at least this old"), never as a description of the whole
blend. Anything stating this to a reader has to be worded that way too: "the
models were last all on the same cycle at X" is true, "the data is from X"
is not.

THIS IS AN INFERENCE, NOT A MEASUREMENT OF TODAY. The table records when a
cycle was PAST measured as landed, not a live check that today's run has. A
provider's delay varies run to run, so a given call to this function can be
wrong — in either direction — even though the rule it applies is correct on
average. It is still worth having, because the alternative is not "a more
careful estimate": it is nothing, since the response answers this question
with total silence. A later change compares this function's guess against
one model's own observed run time (its `meta.json`) and reports when they
disagree — the honest way to use an inference is to say what it is, then
check it.

THE FAILURE THIS MAKES VISIBLE: on 2026-08-28 a run fired at 00:27 UTC and
produced that day's FIRST forecast — the one whose numbers get scored
against the day's actuals — from data more than twelve hours old
(`aligned_cycle_at` at that moment: 12z initialised 2026-08-27, age_hours
12.45). Nothing in the record said so; the forecast looked exactly as fresh
as any other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


@dataclass
class AlignedCycle:
    initialised_at: datetime  # the model run's own initialisation time, UTC
    window_opened_at: datetime  # when this project measured that cycle as aligned and available, UTC
    age_hours: float  # now - initialised_at, in hours


def _at(d: date, hour: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc)


# CROSS-CHECK (measured, not argued): at 2026-08-28T00:27Z this function
# yields 12z initialised 2026-08-27, age_hours 12.45. Independently,
# Open-Meteo's own per-model metadata endpoint
# (https://api.open-meteo.com/data/ecmwf_ifs025/static/meta.json) recorded
# ECMWF's 18z run as first available at 2026-08-28T02:24:56Z — nearly two
# hours AFTER 00:27. So 12z genuinely was the newest cycle any model had in
# hand at that instant, and the derived table agrees with observed
# availability at this one point. One point does not prove the table holds
# everywhere; it is the check available before a live per-model comparison
# exists.
def aligned_cycle_at(now: datetime) -> AlignedCycle:
    """The cycle this project infers is aligned across all five models at
    `now`, per the measured table above.

    `now` must be timezone-aware UTC. Rejected rather than silently
    misread: this function's whole job is bucketing by UTC hour, so a naive
    or non-UTC `now` would misclassify the row without raising anywhere
    near the mistake.
    """
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError(f"aligned_cycle_at requires a timezone-aware UTC datetime, got {now!r}")

    today = now.date()
    yesterday = today - timedelta(days=1)

    # docs-internal/ROADMAP.md's measured aligned-window table: the UTC hour
    # a window opens and which cycle — whose calendar day — is aligned from
    # that hour. Checked in descending hour order; each 6-hour window is
    # exactly wide enough to reach the next one, except 12z's, which spans
    # midnight and so is written here as two date-relative branches.
    if now.hour >= 20:
        window_opened_at, initialised_at = _at(today, 20), _at(today, 12)
    elif now.hour >= 14:
        window_opened_at, initialised_at = _at(today, 14), _at(today, 6)
    elif now.hour >= 8:
        window_opened_at, initialised_at = _at(today, 8), _at(today, 0)
    elif now.hour >= 2:
        window_opened_at, initialised_at = _at(today, 2), _at(yesterday, 18)
    else:
        window_opened_at, initialised_at = _at(yesterday, 20), _at(yesterday, 12)

    age_hours = (now - initialised_at).total_seconds() / 3600
    return AlignedCycle(initialised_at=initialised_at, window_opened_at=window_opened_at, age_hours=age_hours)
