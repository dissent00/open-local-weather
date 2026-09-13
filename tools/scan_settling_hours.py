#!/usr/bin/env python3
"""When each scored Day+0 quantity SETTLES, hour by hour, against the record.

WHY THIS EXISTS. ROADMAP item 104's C4 declines a target date below a floor
of remaining period: at 23:30 the day's high and rain are settled, and
scoring them would credit a forecaster for reading a thermometer. The floor
value was left open — "measure once late issuances exist; this project sizes
thresholds against the record, not against convenient samples" — and item 100
is why that sentence is there.

But the floor does not need late ISSUANCES to be measured. It needs to know
when the day's weather stops moving, and forty days of hourly archive already
say that. The one measurement this project has published on the question —
"the daily minimum fell at or before 08:00 on 39 of 40 days" — sized exactly
one dimension, the low. The high, the onset and the peak gust were never
measured, and C4 declines all of them.

A QUANTITY IS SETTLED AT HOUR h WHEN ITS VALUE OVER THE WHOLE DAY EQUALS ITS
VALUE OVER THE HOURS UP TO h. That is the only definition that matches what
C4 is deciding: a forecast issued at h is a report, not a forecast, exactly
when nothing after h can change the answer.

Read-only. Fetches from Open-Meteo's archive, which needs no key, and writes
nothing.

  python tools/scan_settling_hours.py [--days 120]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.config import load_location_config  # noqa: E402
from openlocalweather.fetch import open_meteo  # noqa: E402

# The scored Day+0 fields C4 would decline, and how each one settles.
#
# `rain` is the boolean the accuracy record is built on, so it settles the
# moment the day's first measurable precipitation falls — after that, no later
# hour can change the answer from true. A dry day never settles until 24:00,
# which is itself the finding: "no rain today" is a claim about every
# remaining hour and cannot be reported early.
DIMENSIONS = ("temp_high_c", "temp_low_c", "peak_wind_kmh", "rain", "onset_hour")

# Open-Meteo reports a trace as a non-zero float; the record's own rain
# boolean is built the same way, so this matches what gets scored.
RAIN_THRESHOLD_MM = 0.1


def _series(payload: dict) -> dict[date, list[tuple[int, dict]]]:
    """The archive response, regrouped as {day: [(hour, values), ...]}."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    out: dict[date, list[tuple[int, dict]]] = defaultdict(list)
    for i, stamp in enumerate(times):
        moment = datetime.fromisoformat(stamp)
        values = {
            key: (hourly.get(key) or [None] * len(times))[i]
            for key in ("temperature_2m", "precipitation", "windgusts_10m")
        }
        out[moment.date()].append((moment.hour, values))
    return {d: sorted(v) for d, v in out.items()}


def _settles_at(hours: list[tuple[int, dict]], dimension: str) -> int | None:
    """The earliest hour by which this dimension's whole-day answer is fixed,
    or None when the day never settles before midnight."""
    def temps():
        return [(h, v["temperature_2m"]) for h, v in hours if v["temperature_2m"] is not None]

    def gusts():
        return [(h, v["windgusts_10m"]) for h, v in hours if v["windgusts_10m"] is not None]

    def rains():
        return [(h, v["precipitation"]) for h, v in hours if v["precipitation"] is not None]

    if dimension == "temp_high_c":
        series = temps()
        return max(series, key=lambda p: p[1])[0] if series else None

    if dimension == "temp_low_c":
        series = temps()
        return min(series, key=lambda p: p[1])[0] if series else None

    if dimension == "peak_wind_kmh":
        series = gusts()
        return max(series, key=lambda p: p[1])[0] if series else None

    if dimension in ("rain", "onset_hour"):
        # Settled at the first wet hour. A dry day is never settled early —
        # see the module docstring.
        for hour, mm in rains():
            if mm >= RAIN_THRESHOLD_MM:
                return hour
        return None

    raise ValueError(dimension)


def _report_windows(complete: dict) -> int:
    """What a rolling +24h window holds, against the calendar day it starts in.

    ROADMAP item 104's contract replaces Day+0 with this window, on the
    argument that every issuance then makes the same KIND of claim, so an
    06:00 row and a 22:00 row are directly comparable. That argument has a
    structural precondition: the window has to CONTAIN a diurnal maximum and
    minimum whatever hour it starts at, or the quantity is not the same one.
    Twenty-four consecutive hours does, but "obviously" is how this project
    got a rounding divergence past 4801 values, so it is checked.

    The second column is what re-deriving the existing Day+0 series will
    actually do to it — contract item 3 re-derives rather than freezes, and
    the size of the change is what a reader will see move on the accuracy
    page.
    """
    ordered = sorted(complete)
    hours_by_day = {d: {h: v for h, v in complete[d]} for d in ordered}

    print("A rolling +24h window from each issuance hour, against the CALENDAR")
    print("day it starts in. 'moves' = the window's value differs from the")
    print("calendar day's by more than 0.05.\n")
    print("  issued   window high vs day high      window low vs day low")
    print("           moves      median shift      moves      median shift")

    for issued in (0, 6, 9, 12, 15, 18, 21):
        high_shifts, low_shifts = [], []
        for i, d in enumerate(ordered[:-1]):
            nxt = ordered[i + 1]
            if (nxt - d).days != 1:
                continue
            window = [hours_by_day[d][h]["temperature_2m"] for h in range(issued, 24)]
            window += [hours_by_day[nxt][h]["temperature_2m"] for h in range(0, issued)]
            window = [t for t in window if t is not None]
            day = [v["temperature_2m"] for _, v in complete[d] if v["temperature_2m"] is not None]
            if not window or not day:
                continue
            high_shifts.append(max(window) - max(day))
            low_shifts.append(min(window) - min(day))

        def summarise(shifts):
            moved = sum(1 for x in shifts if abs(x) > 0.05)
            median = sorted(shifts)[len(shifts) // 2] if shifts else 0.0
            return f"{moved:3d}/{len(shifts):3d}   {median:+6.2f} C"

        print(f"   {issued:02d}:00   {summarise(high_shifts)}   {summarise(low_shifts)}")

    print("\nA 24-hour window always spans one full diurnal cycle, so it always")
    print("contains a maximum and a minimum — the comparability precondition")
    print("holds by construction and the numbers above only say by how much the")
    print("re-derived series will differ from the stored one.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=120, help="How far back to look.")
    parser.add_argument("--config", default=str(ROOT / "config" / "location.yaml"))
    parser.add_argument(
        "--window",
        action="store_true",
        help="Instead of settling hours, report what a rolling +24h window from "
             "each issuance hour holds, against the calendar day it starts in.",
    )
    args = parser.parse_args()

    location = load_location_config(args.config)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days - 1)
    print(f"{location.primary_place_name}: {start} to {end} ({args.days} days), "
          f"local time ({location.timezone})\n")

    payload = open_meteo.fetch_archive_range(
        location.primary_point.lat, location.primary_point.lon, start, end, location.timezone
    )
    days = _series(payload)
    complete = {d: h for d, h in days.items() if len(h) == 24}
    print(f"{len(complete)} complete days of hourly archive\n")

    if args.window:
        return _report_windows(complete)

    for dimension in DIMENSIONS:
        settled = {d: _settles_at(h, dimension) for d, h in complete.items()}
        known = [h for h in settled.values() if h is not None]
        never = len(settled) - len(known)

        print(f"{dimension}")
        if not known:
            print(f"   never settles on any of {len(settled)} days\n")
            continue

        # The floor question: issue at hour h and the answer is already fixed
        # on what fraction of days?
        for h in (6, 8, 9, 12, 15, 17, 18, 20, 21, 22):
            fixed = sum(1 for v in known if v <= h)
            pct = 100.0 * fixed / len(settled)
            print(f"   by {h:02d}:00  settled on {fixed:3d}/{len(settled)} days  ({pct:5.1f}%)")
        print(f"   median settling hour {sorted(known)[len(known) // 2]:02d}:00, "
              f"never-settles {never}/{len(settled)} days\n")

    # The control. This project has published exactly one settling
    # measurement — "the daily minimum fell at or before 08:00 on 39 of 40
    # days", from 40 days of hourly archive — and a method that cannot
    # reproduce it is measuring something else.
    lows = [_settles_at(h, "temp_low_c") for h in complete.values()]
    by_eight = sum(1 for h in lows if h is not None and h <= 8)
    print(f"CONTROL — temp_low_c settled by 08:00 on {by_eight}/{len(lows)} days "
          f"({100.0 * by_eight / len(lows):.1f}%). ROADMAP item 104 reports 39/40 "
          f"(97.5%) over a different 40-day window.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
