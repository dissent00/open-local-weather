"""Kenya Met's FIVE-DAY bulletin: a per-county x per-day grid.

Companion to kmd_daily_parse, which handles the same-day bulletin. Together
they give the met service a Day+0 and a Day+3 prediction, scored against
the numerical models at the matching lead times. The rain vocabulary is
decoded through the shared decode_rain_terms(), so both bulletins read
KMD's controlled vocabulary through one implementation.

THE STRUCTURE, AND THE TRAP IN IT.

Each county occupies a five-row block, with one column per forecast date:

    Kisumu | Morning   | Sunny intervals. | Sunny intervals. | ...
           | Afternoon | Light showers.   | Sunny intervals. | ...
           | Night     | Partly cloudy.   | Partly cloudy.   | ...
           | Maximum   | 29C              | 31C              | ...
           | Minimum   | 18C              | 18C              | ...

**County blocks straddle page boundaries.** In the 20-24 Aug 2026 bulletin,
Kisumu's Morning/Afternoon/Night rows end one page and its Maximum/Minimum
rows begin the next, under a repeated header. A parser that treated each
page's table independently would give Kisumu no temperatures at all — or,
worse, silently attach them to whichever county happened to start the next
page. So every county-shaped table is flattened into one row stream in
document order and blocks are walked across that, which makes a page break
invisible rather than significant.

The label sits in column 1 or column 2 depending on the row, which is a
quirk of how the PDF's merged cells extract, not anything meaningful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from openlocalweather.fetch.bulletin.kmd_daily_parse import (
    RAIN_PROBABILITY_CUTOFF_PCT,
    decode_rain_terms,
)
from openlocalweather.models import ModelPrediction

_HEADER_FIRST_CELL = "county"
_PERIOD_LABELS = ("morning", "afternoon", "night")
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")

_MONTHS = {date(2000, m, 1).strftime("%B").lower(): m for m in range(1, 13)}


@dataclass(frozen=True)
class DayOutlook:
    """One county's forecast for one date."""

    target_date: date
    periods: list[str]
    high_c: float | None
    low_c: float | None
    rain_probability_pct: int | None
    area_coverage_pct: int | None

    @property
    def rain(self) -> bool | None:
        if self.rain_probability_pct is None:
            return False if self.periods else None
        return self.rain_probability_pct > RAIN_PROBABILITY_CUTOFF_PCT


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _number(cell: str | None) -> float | None:
    if not cell:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", cell)
    return float(m.group()) if m else None


def _is_county_table(table: list[list]) -> bool:
    return bool(table) and bool(table[0]) and _normalise(str(table[0][0])) == _HEADER_FIRST_CELL


def parse_header_dates(header_row: list) -> dict[int, date]:
    """Column index -> forecast date, read from the header's own labels.

    Taken from the bulletin rather than derived by counting forward from an
    assumed start, so a bulletin that skips or reorders a day can't silently
    shift every column by one.
    """
    dates: dict[int, date] = {}
    for idx, cell in enumerate(header_row):
        m = _DATE_RE.search(str(cell or "").replace("\n", " "))
        if not m:
            continue
        day, month_name, year = m.groups()
        month = _MONTHS.get(month_name.lower())
        if month is None:
            continue
        try:
            dates[idx] = date(int(year), month, int(day))
        except ValueError:
            continue
    return dates


def _flatten_county_rows(tables: list[list[list]]) -> tuple[list[list], dict[int, date]]:
    """All county-table data rows in document order, headers dropped.

    This is what makes page-straddling blocks reassemble: the stream has no
    notion of which page a row came from.
    """
    rows: list[list] = []
    dates: dict[int, date] = {}
    for table in tables:
        if not _is_county_table(table):
            continue
        if not dates:
            dates = parse_header_dates(table[0])
        rows.extend(table[1:])
    return rows, dates


_KNOWN_LABELS = frozenset({*_PERIOD_LABELS, "maximum", "minimum"})


def _row_label(row: list) -> str:
    """The row's label, found wherever the extraction happened to put it.

    Merged cells extract to a varying number of empty columns between
    issues — the daily bulletin's county row moved its minimum temperature
    from index 2 to index 4 between consecutive days — so this scans for a
    KNOWN label rather than trusting a fixed offset. An unrecognised value
    returns "", and the row contributes nothing, which is the safe direction:
    a mislabelled row would attach temperatures to the wrong field.
    """
    for cell in row[1:4]:
        label = _normalise(str(cell or ""))
        if label in _KNOWN_LABELS:
            return label
    return ""


def parse_county_days(tables: list[list[list]], county: str) -> list[DayOutlook]:
    """Every forecast date available for one county. Empty if not listed."""
    rows, dates = _flatten_county_rows(tables)
    if not rows or not dates:
        return []

    target = _normalise(county)
    block: list[list] = []
    collecting = False
    for row in rows:
        # `or ""` matters: an empty PDF cell arrives as None, and str(None)
        # is "None" — truthy, and enough to read a continuation row as the
        # start of a new county and truncate the block after one row.
        name = _normalise(str((row[0] if row else None) or ""))
        if name:
            # A new county starts here: stop if we already had ours.
            if collecting:
                break
            collecting = name == target
        if collecting:
            block.append(row)
    if not block:
        return []

    periods_by_col: dict[int, list[str]] = {}
    highs: dict[int, float | None] = {}
    lows: dict[int, float | None] = {}
    for row in block:
        label = _row_label(row)
        for col in dates:
            if col >= len(row):
                continue
            value = str(row[col] or "").replace("\n", " ").strip()
            if not value:
                continue
            if label in _PERIOD_LABELS:
                periods_by_col.setdefault(col, []).append(value)
            elif label == "maximum":
                highs[col] = _number(value)
            elif label == "minimum":
                lows[col] = _number(value)

    outlooks = []
    for col, target_date in sorted(dates.items(), key=lambda kv: kv[1]):
        periods = periods_by_col.get(col, [])
        probability, area = decode_rain_terms(periods)
        outlooks.append(
            DayOutlook(
                target_date=target_date,
                periods=periods,
                high_c=highs.get(col),
                low_c=lows.get(col),
                rain_probability_pct=probability,
                area_coverage_pct=area,
            )
        )
    return outlooks


def outlook_for_date(
    tables: list[list[list]], county: str, target_date: date
) -> DayOutlook | None:
    for outlook in parse_county_days(tables, county):
        if outlook.target_date == target_date:
            return outlook
    return None


def outlook_to_prediction(outlook: DayOutlook, model_id: str) -> ModelPrediction:
    """No onset even though the grid names a period.

    Day+3 predictions carry no onset timing anywhere in this project — the
    numerical models are only fetched at daily resolution that far out — and
    giving the met service an onset the models can't have would put it in a
    column nothing else populates rather than compare like with like.
    """
    return ModelPrediction(
        model=model_id,
        rain=outlook.rain,
        high_c=outlook.high_c,
        low_c=outlook.low_c,
    )
