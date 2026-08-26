"""Whether the hours ahead are convective enough to belong in the Overview.

WHY THIS IS IN CODE. The Overview is the one section most readers finish, and
it is a tight slot: one or two sentences that open with the day-over-day
comparison. Nothing competes for that space unless something decides it may,
and left to its own judgement the LLM put instability in Today's Forecast and
Severe Weather and left the Overview to the temperature.

Live case, 2026-08-26: afternoon CAPE peaked between 1100 and 2600 J/kg
across models, with UKMO and ICON both above 2000. The narrative discussed it
twice further down. The Overview said "similar warmth, calmer winds, and dry
again" — which a reader who stopped there would have taken as a quiet day.

So the decision is made here, on a number, and the prompt is told to obey it.
Same contract as DAY-OVER-DAY COMPARISON: code decides, the LLM phrases.

THE THRESHOLD IS DELIBERATELY THE LOW ONE. 1000 J/kg is where the prompt's
own guidance already says thunderstorms become supported. Over the Lake
Victoria basin — among the most thunderstorm-prone places on Earth, where
lake-breeze convergence drives convection at scales global models at 9-25 km
resolve poorly — the models under-forecast storms systematically, so a
higher bar would mean staying quiet about exactly the days worth warning on.

MAX ACROSS MODELS, NEVER THE MEAN. One model at 2600 and three near zero is a
disagreement, and averaging it away is the failure this project exists to
avoid. See the peak_cape_by_model breakdown, which the prompt states in the
hazard sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openlocalweather.fetch.open_meteo import pick_series

# Above this, a model is forecasting an atmosphere that supports
# thunderstorms, and the Overview has to say so.
CONVECTIVE_CAPE_THRESHOLD_JKG = 1000.0


@dataclass
class InstabilityOutlook:
    """Pre-computed convective outlook for the hours still ahead."""

    peak_cape_jkg: float
    peak_model: str
    peak_hour: str | None
    convective: bool
    models_above_threshold: list[str] = field(default_factory=list)
    peak_cape_by_model: dict[str, float] = field(default_factory=dict)


def summarize_instability(
    hourly_multi_model: dict,
    models: list[str],
    threshold: float = CONVECTIVE_CAPE_THRESHOLD_JKG,
) -> InstabilityOutlook | None:
    """Peak CAPE per model over the supplied hours, and whether any model
    crosses into thunderstorm-supporting territory.

    Returns None when no model supplied a usable CAPE series, so an absent
    forecast reads as a gap rather than as a calm afternoon. Expects the
    window to have been trimmed to the hours ahead already
    (daypart.forward_hours) — a peak that happened this morning is not a
    reason to warn anyone about this evening.
    """
    if not hourly_multi_model or not hourly_multi_model.get("hourly"):
        return None

    hours = hourly_multi_model["hourly"]
    times = hours.get("time") or []
    if not times:
        return None

    peak_cape_by_model: dict[str, float] = {}
    peak_hour_by_model: dict[str, str] = {}
    for model in models:
        # Unsuffixed fallback covers a single-model fetch, where Open-Meteo
        # omits the suffix. pick_series, not .get(), because a named-but-null
        # series is the documented way this data goes silently missing.
        series = pick_series(hours, f"cape_{model}", "cape")
        readings = [
            (value, times[i])
            for i, value in enumerate(series[: len(times)])
            if value is not None
        ]
        if not readings:
            continue

        value, at_time = max(readings, key=lambda pair: pair[0])
        peak_cape_by_model[model] = value
        peak_hour_by_model[model] = at_time

    if not peak_cape_by_model:
        return None

    peak_model = max(peak_cape_by_model, key=lambda m: peak_cape_by_model[m])
    peak_cape = peak_cape_by_model[peak_model]
    peak_time = peak_hour_by_model[peak_model]

    return InstabilityOutlook(
        peak_cape_jkg=peak_cape,
        peak_model=peak_model,
        # The clock time alone; the date is today by construction and a bare
        # "HH:MM" is what every other timing field here carries.
        peak_hour=peak_time.split("T")[-1][:5] if "T" in peak_time else None,
        convective=peak_cape >= threshold,
        models_above_threshold=sorted(
            m for m, v in peak_cape_by_model.items() if v >= threshold
        ),
        peak_cape_by_model=peak_cape_by_model,
    )
