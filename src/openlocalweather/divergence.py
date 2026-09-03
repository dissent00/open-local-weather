"""How far apart the station and the reanalysis actually are.

ROADMAP item 45. Its sequencing is cross-check before replacement: stamp
provenance, store the station's readings unscored, let divergence accumulate,
then decide what precedence would earn. This produces the numbers that
decision is made from.

WHAT IT DOES NOT DO, and cannot. It never says which source is right. There
is no held-out truth here — the sources ARE the truth candidates — so what is
measurable is disagreement, and disagreement is a flag for a human rather
than a weight to fit. A version of this that ranked the sources would be
inventing a yardstick it does not have.

WHY IT WAS WRITTEN BEFORE THERE WAS DATA. The decision it feeds is weeks
away, and the reasoning about what to measure is freshest now. Written later,
under the pressure of wanting an answer, it would be tempting to measure
whatever makes the choice look obvious.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from openlocalweather.models import DailyActual

# (label, reanalysis field, station field). Precipitation AMOUNT is absent on
# purpose: the reference station files it as a constant zero, so a comparison
# would measure the reanalysis against a number nobody observed. Occurrence is
# compared separately and as a contingency table.
COMPARED_VARIABLES = (
    ("high_c", "high_c", "station_high_c"),
    ("low_c", "low_c", "station_low_c"),
    ("peak_wind_kmh", "peak_wind_kmh", "station_peak_wind_kmh"),
)


@dataclass
class VariableDivergence:
    """One continuous variable, over the days BOTH sources reported it.

    Signed and absolute both, because they answer different questions. A
    systematic +2 °C offset is a calibration difference somebody could correct
    for; the same magnitude scattered either way is noise, and a signed mean
    near zero would hide it completely.
    """

    variable: str
    days: int
    mean_signed: float | None = None  # station minus archive
    mean_absolute: float | None = None
    max_absolute: float | None = None


@dataclass
class OccurrenceDivergence:
    """Rain, as a contingency table rather than an average.

    Averaging booleans produces a number that reads like an error magnitude
    and is not one. What matters is WHICH WAY they disagree: `station_only` is
    item 42's entire finding — the airport standing in rain the reanalysis
    recorded as a dry day — and `archive_only` is a different event with a
    different explanation, most likely rain elsewhere in a 25 km cell.

    `days` counts only days the station REPORTED. A day it filed nothing is
    not evidence either way, and counting it as agreement would manufacture a
    match with every dry reanalysis day in the record.
    """

    days: int = 0
    both_wet: int = 0
    both_dry: int = 0
    station_only: int = 0
    archive_only: int = 0


@dataclass
class SourceDivergence:
    variables: list[VariableDivergence] = field(default_factory=list)
    occurrence: OccurrenceDivergence = field(default_factory=OccurrenceDivergence)


def compare_sources(actuals: Mapping[date, DailyActual]) -> SourceDivergence:
    """Where the two instruments disagree, and by how much.

    Pure, and takes the stored record rather than reading it, for the same
    reason `check_recent_degradations` does: the caller owns the I/O and this
    stays testable without one.
    """
    variables = []
    for label, archive_field, station_field in COMPARED_VARIABLES:
        deltas = [
            getattr(a, station_field) - getattr(a, archive_field)
            for a in actuals.values()
            if getattr(a, archive_field) is not None
            and getattr(a, station_field) is not None
        ]
        if not deltas:
            variables.append(VariableDivergence(variable=label, days=0))
            continue

        magnitudes = [abs(d) for d in deltas]
        variables.append(
            VariableDivergence(
                variable=label,
                days=len(deltas),
                mean_signed=round(sum(deltas) / len(deltas), 2),
                mean_absolute=round(sum(magnitudes) / len(magnitudes), 2),
                max_absolute=round(max(magnitudes), 2),
            )
        )

    occurrence = OccurrenceDivergence()
    for a in actuals.values():
        # Three-valued, as everywhere else here. None means the station filed
        # nothing, which is not an observation of a quiet day.
        if a.precipitation is None and a.thunder is None:
            continue

        occurrence.days += 1
        station_saw = bool(a.precipitation) or bool(a.thunder)
        if station_saw and a.rain:
            occurrence.both_wet += 1
        elif station_saw:
            occurrence.station_only += 1
        elif a.rain:
            occurrence.archive_only += 1
        else:
            occurrence.both_dry += 1

    return SourceDivergence(variables=variables, occurrence=occurrence)
