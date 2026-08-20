"""Turning a coarse pressure field into statements a forecaster would make.

The Synoptic Overview section had no synoptic data behind it. The configured
`region_points` span roughly 125 x 55 km, while the features that section is
supposed to describe — highs, lows, the ITCZ, tropical systems — have
wavelengths of 1,000-4,000 km. The whole domain fit inside a single system's
pressure gradient, so the model was describing a mesoscale gradient under a
heading that promised a synoptic one. That was a data problem wearing a
prompt problem's clothes; asking for better writing would have been asking
for invention.

This module consumes the wider ring (see fetch.open_meteo.fetch_synoptic_
pressure) and derives the descriptive terms IN CODE, per the project's
standing rule. Handing the LLM nine raw arrays and asking which quadrant is
lowest is exactly the arithmetic-by-eye that the day-over-day comparison had
to be rescued from after a live run called a 0.1C difference "about 1C
cooler".

WHAT THIS DELIBERATELY DOES NOT CLAIM. Point pressure at 12-degree spacing
supports "lower pressure lies to the northeast and is deepening". It does
not support a named storm centre, a track, or a frontal position — the
sampling is far too coarse, and the true centre may sit between points or
outside the ring entirely. The vocabulary below is bounded accordingly, and
the prompt is told the same. This narrows the README's "no true storm-center
or track forecasting" limitation; it does not remove it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openlocalweather.defaults import (
    SYNOPTIC_GRADIENT_BANDS_HPA,
    SYNOPTIC_TENDENCY_THRESHOLD_HPA,
)


@dataclass
class SynopticSnapshot:
    """A description of the large-scale pressure pattern, ready to narrate."""

    centre_mslp_hpa: float | None
    lowest_label: str | None
    lowest_mslp_hpa: float | None
    highest_label: str | None
    highest_mslp_hpa: float | None
    gradient_hpa: float | None
    gradient_strength: str | None
    # Per-quadrant 72-hour tendency: "deepening" / "filling" / "steady".
    tendencies: dict[str, str] = field(default_factory=dict)
    # Plain-language lines, already safe to state.
    statements: list[str] = field(default_factory=list)


_COMPASS_NAMES = {
    "N": "north", "NE": "northeast", "E": "east", "SE": "southeast",
    "S": "south", "SW": "southwest", "W": "west", "NW": "northwest",
    "centre": "overhead",
}


def _first(values: list) -> float | None:
    for v in values:
        if v is not None:
            return float(v)
    return None


def _last(values: list) -> float | None:
    for v in reversed(values):
        if v is not None:
            return float(v)
    return None


def _strength(gradient: float) -> str:
    for threshold, label in SYNOPTIC_GRADIENT_BANDS_HPA:
        if gradient < threshold:
            return label
    return SYNOPTIC_GRADIENT_BANDS_HPA[-1][1]


def summarize_synoptic(payload: dict | None) -> SynopticSnapshot | None:
    """Reduces the ring to labels. Returns None if nothing usable arrived —
    an absent synoptic picture must read as absent, not as a flat field."""
    points = (payload or {}).get("points") or []
    readings = [
        (p.get("label"), _first(p.get("mslp_hpa") or []), _last(p.get("mslp_hpa") or []))
        for p in points
    ]
    readings = [(label, now, later) for label, now, later in readings if now is not None]
    if len(readings) < 3:
        return None

    centre = next((now for label, now, _ in readings if label == "centre"), None)
    outer = [(label, now, later) for label, now, later in readings if label != "centre"]
    if not outer:
        return None

    lowest = min(outer, key=lambda r: r[1])
    highest = max(outer, key=lambda r: r[1])
    gradient = highest[1] - lowest[1]

    tendencies: dict[str, str] = {}
    for label, now, later in readings:
        if later is None:
            continue
        delta = later - now
        if abs(delta) < SYNOPTIC_TENDENCY_THRESHOLD_HPA:
            tendencies[label] = "steady"
        else:
            tendencies[label] = "deepening" if delta < 0 else "filling"

    snapshot = SynopticSnapshot(
        centre_mslp_hpa=centre,
        lowest_label=lowest[0],
        lowest_mslp_hpa=lowest[1],
        highest_label=highest[0],
        highest_mslp_hpa=highest[1],
        gradient_hpa=round(gradient, 1),
        gradient_strength=_strength(gradient),
        tendencies=tendencies,
    )
    snapshot.statements = _statements(snapshot)
    return snapshot


def _statements(s: SynopticSnapshot) -> list[str]:
    """Sentences bounded by what point sampling at this spacing can support.

    Note the vocabulary: "lower pressure lies to the northeast", never "a low
    is centred over Somalia". The ring cannot locate a centre — it can only
    say which sampled direction is lowest.
    """
    lines = []
    low_dir = _COMPASS_NAMES.get(s.lowest_label, s.lowest_label)
    high_dir = _COMPASS_NAMES.get(s.highest_label, s.highest_label)
    if s.gradient_hpa is not None:
        lines.append(
            f"Across roughly 2,600 km, pressure is lowest toward the {low_dir} "
            f"({s.lowest_mslp_hpa:.0f} hPa) and highest toward the {high_dir} "
            f"({s.highest_mslp_hpa:.0f} hPa) — a {s.gradient_hpa:.0f} hPa spread, "
            f"a {s.gradient_strength} large-scale gradient."
        )
    low_trend = s.tendencies.get(s.lowest_label)
    if low_trend and low_trend != "steady":
        lines.append(f"The lower pressure to the {low_dir} is {low_trend} over the next three days.")

    # Any OTHER direction where pressure is falling. This is the "something is
    # building over there" signal, and reporting only the currently-lowest
    # quadrant would miss it entirely: a system approaching from the west
    # shows up as the west falling well before the west is the lowest point
    # on the ring.
    deepening = [
        lbl for lbl, trend in s.tendencies.items()
        if trend == "deepening" and lbl not in (s.lowest_label, "centre")
    ]
    if deepening:
        names = ", ".join(_COMPASS_NAMES.get(d, d) for d in sorted(deepening))
        lines.append(
            f"Pressure is also falling toward the {names} — a large-scale feature "
            "building in that direction, though this sampling cannot say how fast "
            "or whether it will reach here."
        )
    centre_trend = s.tendencies.get("centre")
    if centre_trend:
        word = {"deepening": "falling", "filling": "rising", "steady": "near-steady"}[centre_trend]
        lines.append(f"Pressure overhead is {word}.")
    lines.append(
        "Sampling is a nine-point ring at 12-degree spacing, so this locates a "
        "direction, not a centre or a front."
    )
    return lines
