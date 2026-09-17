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

from openlocalweather.disagreement import (
    DISAGREEMENT_HIGH_EXCEEDED,
    DISAGREEMENT_ONSET_ALREADY_PASSED,
    StandingCall,
)
from openlocalweather.models import LowDivergence, SOURCE_STATION, DailyActual, ObservedSoFar
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


# The dimensions today's own observations can honestly baseline a forecast
# against, and the ones they cannot — ROADMAP item 104, contract item 8.
#
# At 20:00 the comparison's subject is tomorrow and its baseline is TODAY, a
# day only the station has observed: `archive-api` serves the current day as
# model output, proven in Ensemble's item 14 finding 6 by hours that had not
# happened yet. So the baseline has to be built from ObservedSoFar, and the
# question is which of its six dimensions may cross into a comparison.
#
# TEMPERATURE, AND ONLY TEMPERATURE. Each exclusion is a measurement, not a
# shrug:
#
#   high_c, low_c   THE SAME QUANTITY the models forecast and the reanalysis
#                   records. Station minus reanalysis over 40 days is +0.49 C
#                   on the high and -0.05 C on the low, against a 1.0 C band.
#
#   peak_wind_kmh   A DIFFERENT QUANTITY. `fetch/metar.py` reads `sknt`, the
#                   max SUSTAINED wind, because METAR files a gust group only
#                   when a gust occurs — absent on all 932 rows of a 45-day
#                   sample. The forecast side is `windgusts_10m_max`. Pairing
#                   them reads the gust factor as weather, which is exactly
#                   the error item 126 records withdrawing a whole plan over.
#                   This deployment cannot observe a gust at all.
#
#   cloud_oktas     A DIFFERENT STATISTIC: a point mean in eighths, 12.5-point
#                   steps, against a mean over hourly grid values in percent.
#                   Item 123 owns that choice and has four paired days.
#
#   precipitation   NO AMOUNT EXISTS. A METAR reports that rain fell, never
#                   how much — see ObservedSoFar, which withholds the
#                   dimension for the same reason. `describe_day_rain` needs
#                   an amount, so the rain half of an evening comparison is
#                   silent rather than invented.
#
# WHAT THAT LEAVES is a comparison that can say "cooler than today (Monday)
# was" and nothing else, which is thinner than the operator's example sentence
# by one clause — "and breezier" — and that clause is the one no instrument
# here supports.
def observed_baseline(observed: ObservedSoFar | None) -> DailyActual | None:
    """Today's own observations, shaped as the baseline a comparison can use.

    None when nothing was measured, never an empty baseline: a station that
    did not report is not a station reporting a quiet day.

    `rain` is False because DailyActual requires it and the day-over-day
    comparison reads `precip_mm` rather than this flag for everything a reader
    sees. It is not published from here and nothing scores it.
    """
    if observed is None:
        return None

    if observed.high_c is None and observed.low_c is None:
        return None

    return DailyActual(
        rain=False,
        high_c=observed.high_c,
        low_c=observed.low_c,
        # Deliberately absent: see the block above. Each of these would pair a
        # forecast with something that is not the same measurement.
        peak_wind_kmh=None,
        cloud_cover_pct=None,
        precip_mm=None,
        thunder=observed.thunder,
        provenance={
            field: SOURCE_STATION
            for field, value in (("high_c", observed.high_c), ("low_c", observed.low_c))
            if value is not None
        },
    )


def describe_low_divergence(divergence: LowDivergence | None, station_name: str) -> str | None:
    """The overnight-low footnote, or None when there is nothing to footnote —
    ROADMAP item 143, part 3.

    A FACT ABOUT ONE STATION, NOT A CLAIM ABOUT THE BASIN. The operator's
    framing is the specification: "the airport reported 20 against the
    forecast of 18.2. It's not saying nowhere in the area hit 18.2, just that
    the airport didn't." So the sentence names the place and both numbers, and
    asserts nothing about anywhere else. A measuring station can be warmer
    than the country around it and the forecast can be wrong, and this cannot
    tell which — saying it plainly is the honest shape.

    SILENCE IS THE DEFAULT. Only a `notable` divergence gets a sentence. Item
    143 records why: the operator does not want this in front of a reader on
    an ordinary morning, and a footnote that appears every day stops being
    read on the day it matters.

    WHY CODE WRITES IT RATHER THAN THE MODEL. Item 142 found that OBSERVED SO
    FAR TODAY and THE FORECASTER'S CALL both arrive locked verbatim and
    disagreed about the same quantity, so the instruction set REQUIRED
    publishing two lows for one day — the thing rule 1 exists to prevent. The
    forecast escaped it only by taking the other branch of "use it VERBATIM or
    not at all". A pre-computed sentence is the way out: there is now exactly
    one sanctioned form of words that mentions both, and the model's only
    choice is whether to use it.
    """
    if divergence is None or not divergence.notable:
        return None

    return (
        f"{station_name} recorded an overnight low of "
        f"{format_temp_c(divergence.observed_c, decimals=1)} against a forecast of "
        f"{format_temp_c(divergence.forecast_c, decimals=1)}. That is this one station, not the wider "
        f"area: it does not say nowhere reached the forecast low."
    )


def describe_notable_disagreements(
    codes: list[str] | None,
    standing: StandingCall,
    observed: ObservedSoFar,
    station_name: str,
) -> list[str]:
    """The footnotes for a notable high and a notable onset — ROADMAP item
    145, the words its bands were waiting for. One sentence per code, in the
    codes' own order, and nothing for the codes that already have words: the
    low has `describe_low_divergence` with its own numbers, and rain that
    fell is already in OBSERVED SO FAR TODAY.

    THE SAME SHAPE AS THE LOW'S, on purpose: the place, both numbers, and a
    claim about nothing else. A station can run hotter than the country
    around it, or see a shower the basin did not, and the forecast can be
    wrong; this cannot tell which, and says so. Written by code, not the
    model, for the reason item 142 recorded: two locked blocks carrying the
    same quantity means exactly one sanctioned sentence may mention both.

    Silence is the default. `codes` is the REPORTING list
    (`notable_disagreements`), never the spend list; a code whose numbers
    are missing on either side gets no sentence rather than a half one.
    """
    if not codes:
        return []

    notes: list[str] = []
    for code in codes:
        if (
            code == DISAGREEMENT_HIGH_EXCEEDED
            and standing.temp_high_c is not None
            and observed.high_c is not None
        ):
            notes.append(
                f"{station_name} has already recorded "
                f"{format_temp_c(observed.high_c, decimals=1)} today against a forecast "
                f"high of {format_temp_c(standing.temp_high_c, decimals=1)}. That is this "
                "one station, not the wider area: it does not say everywhere has "
                "passed the forecast high."
            )
        if (
            code == DISAGREEMENT_ONSET_ALREADY_PASSED
            and standing.onset_hour
            and observed.precipitation_onset
        ):
            notes.append(
                f"{station_name} saw rain from {observed.precipitation_onset}, against "
                f"a forecast onset of {standing.onset_hour}. That is this one station, "
                "not the wider area: it does not say rain has started everywhere."
            )
    return notes
