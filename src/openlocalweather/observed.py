"""What the station has already seen today, composed for a reader.

ROADMAP item 121. An observation is a measured fact, and this project's first
principle is that facts are composed in code and never asked of the model.
Until this existed the only way "the station has recorded 27 °C and rain since
13:00" reached anybody was by paying for an LLM call to restate it — which is
the one call nobody should have to make, and the reason an operator running
frequent refreshes had to choose between cost and currency.

IT IS ALSO WHAT MAKES ITEM 104'S C2 TRIGGER 3 WORTH ACTING ON. That trigger
fires when an observation contradicts the standing call, and until item 121
the run it caused was never shown the observation that caused it — see item
120, where that gap is recorded.

THREE-VALUED THROUGHOUT, and the distinction is the point. `thunder=False`
means the station reported and saw none, which is information a reader wants
at 14:00. `thunder=None` means nothing was measured, which is not, and is
omitted rather than rendered as a negative. Absence is absence — the rule
whose violation cost a published forecast on 2026-08-29.

THE CLAUSE ORDER IS PART OF THE CONTRACT, not a presentation choice: the
output is pinned by `spec/vectors/observed_so_far.json` and compared exactly
against the Dart port, so reordering it is a behaviour change.
"""

from __future__ import annotations

from openlocalweather.models import ObservedSoFar
from openlocalweather.models import format_temp_c


def describe_observed_so_far(
    observed: ObservedSoFar | None, *, as_of: str | None = None
) -> str | None:
    """One finished sentence for what has been measured today, or None when
    nothing has.

    None rather than a cheerful "nothing to report": a station that did not
    answer has not reported a quiet day, and the caller renders a gap as a
    gap. Same rule as `describe_extended_trend` and every other composer here.

    `as_of` is the issuance's local "HH:MM". Item 118's discipline applied to
    a block that is inherently about elapsed hours: it is safe for this one to
    speak about the past — that is all it does — but a reader still has to be
    told how current it is, because "no rain so far" means something very
    different at 09:00 and at 21:00.
    """
    if observed is None:
        return None

    clauses = [
        _rain(observed),
        _thunder(observed),
        _temperature("high so far", observed.high_c),
        _temperature("low so far", observed.low_c),
        _gust(observed.peak_wind_kmh),
        _sky(observed.cloud_oktas),
    ]
    said = [c for c in clauses if c is not None]
    if not said:
        return None

    opening = f"As of {as_of}" if as_of else "So far today"
    return f"{opening}: " + "; ".join(said) + "."


def _rain(observed: ObservedSoFar) -> str | None:
    if observed.precipitation is None:
        return None

    if not observed.precipitation:
        return "no rain"

    # The onset is the more useful half and is reported when the station
    # caught it. A station that saw rain without a first-seen time still says
    # so; dropping the clause for want of the clock would lose the fact.
    if observed.precipitation_onset:
        return f"rain from {observed.precipitation_onset}"

    return "rain"


def _thunder(observed: ObservedSoFar) -> str | None:
    if observed.thunder is None:
        return None

    return "thunder" if observed.thunder else "no thunder"


def _temperature(label: str, celsius: float | None) -> str | None:
    if celsius is None:
        return None

    # format_temp_c, never a local round: one rounding site per quantity is
    # the rule this project arrived at the expensive way — see item 88.
    return f"{label} {format_temp_c(celsius)}"


def _gust(kmh: float | None) -> str | None:
    if kmh is None:
        return None

    return f"peak gust {round(kmh)} km/h"


def _sky(oktas: float | None) -> str | None:
    if oktas is None:
        return None

    # Eighths, which is how a METAR reports cover and how the mean of a day's
    # reports stays comparable with a single one.
    return f"sky {round(oktas)}/8"
