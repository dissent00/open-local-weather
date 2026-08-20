"""Deterministic day-over-day comparison for the narrative's Overview.

WHY THIS IS IN CODE. The Overview opens by telling a reader how today
compares to yesterday — the single most useful orienting sentence in the
forecast, because people rarely remember yesterday's numbers but do
remember how it felt. The first live run with yesterday's observations in
the prompt produced "about 1°C cooler than yesterday" when the real
difference was 0.1°C (29.6 -> 29.5): a ten-fold overstatement of the one
sentence most readers actually act on.

That is the project's founding principle re-learned the hard way: an LLM
handed two numbers and asked to compare them will sometimes get it wrong,
so the comparison belongs in code, like every other number here.

The subtlety is that today's *published* high is the LLM's own blended
call, which does not exist until it responds — so there is nothing to
subtract from at prompt-building time. The resolution: compare against the
MODEL CONSENSUS for today, which code can compute before the call, and
hand the LLM a categorical label rather than a raw delta. The category is
robust to the small gap between consensus and the final blend (a 0.2°C
difference never changes "about the same" into "much warmer"), while the
raw number is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from openlocalweather.defaults import TEMP_CHANGE_BANDS_C, WIND_CHANGE_THRESHOLD_KMH
from openlocalweather.models import DailyActual, ModelPrediction
from openlocalweather.verify.scoring import mean


@dataclass
class DayOverDayComparison:
    """Pre-computed comparison of today's consensus against yesterday's
    observations. Every field is derived in code; the LLM's job is to phrase
    `summary`, not to recompute it."""

    yesterday_high_c: float | None
    yesterday_low_c: float | None
    yesterday_rain: bool | None
    yesterday_peak_wind_kmh: float | None
    # Exposed alongside the derived label, not just folded into it. A live
    # run read "rain again, as yesterday" and wrote "wetter conditions",
    # dropping the comparison — having both raw booleans visible makes that
    # much harder to misread than a lone phrase.
    today_rain_expected: bool | None
    today_consensus_high_c: float | None
    today_consensus_low_c: float | None
    today_consensus_peak_wind_kmh: float | None
    high_delta_c: float | None
    low_delta_c: float | None
    wind_delta_kmh: float | None
    high_label: str | None
    wind_label: str | None
    rain_contrast: str | None


def _band_label(delta: float | None, warmer: str, cooler: str) -> str | None:
    """Maps a signed delta onto a felt-change band.

    Bands, not raw numbers, because the consensus this is computed from will
    differ slightly from the LLM's final blended call. A band is stable
    across that gap; "1.3 degrees" would not be.
    """
    if delta is None:
        return None
    magnitude = abs(delta)
    for threshold, word in TEMP_CHANGE_BANDS_C:
        if magnitude < threshold:
            return word if word == "about the same" else f"{word} {warmer if delta > 0 else cooler}"
    threshold, word = TEMP_CHANGE_BANDS_C[-1]
    return f"{word} {warmer if delta > 0 else cooler}"


def compute_day_over_day(
    yesterday_actual: DailyActual | None,
    today_day0_predictions: list[ModelPrediction],
) -> DayOverDayComparison | None:
    """None when there is no observed record for yesterday — a gap must read
    as a gap, not as a day with unremarkable weather."""
    if yesterday_actual is None:
        return None

    consensus_high = mean([p.high_c for p in today_day0_predictions])
    consensus_low = mean([p.low_c for p in today_day0_predictions])
    consensus_wind = mean([p.wind_kmh for p in today_day0_predictions])

    def delta(today: float | None, yesterday: float | None) -> float | None:
        if today is None or yesterday is None:
            return None
        return round(today - yesterday, 1)

    high_delta = delta(consensus_high, yesterday_actual.high_c)
    low_delta = delta(consensus_low, yesterday_actual.low_c)
    wind_delta = delta(consensus_wind, yesterday_actual.peak_wind_kmh)

    wind_label = None
    if wind_delta is not None:
        if abs(wind_delta) < WIND_CHANGE_THRESHOLD_KMH:
            wind_label = "similar winds"
        else:
            wind_label = "windier" if wind_delta > 0 else "calmer"

    rain_contrast = None
    today_rain_votes = [p.rain for p in today_day0_predictions if p.rain is not None]
    today_rain = (
        sum(today_rain_votes) > len(today_rain_votes) / 2 if today_rain_votes else None
    )
    if today_rain_votes and yesterday_actual.rain is not None:
        # These strings reach the reader almost verbatim: the prompt tells the
        # model to use rain_contrast AS GIVEN, precisely so it can't invent a
        # difference the numbers don't support. That makes the wording here a
        # user-facing decision, not an internal label — and the first version
        # showed what happens when it's written as a data description instead
        # of a sentence. "rain expected again today, as it rained yesterday
        # too" is circular: it states the same fact twice and says nothing a
        # reader can act on. The unchanged cases are the ones that need the
        # most restraint, because there is genuinely no news in them.
        if yesterday_actual.rain and not today_rain:
            rain_contrast = "drier than yesterday, which saw rain"
        elif not yesterday_actual.rain and today_rain:
            rain_contrast = "wetter than yesterday, which stayed dry"
        elif yesterday_actual.rain and today_rain:
            rain_contrast = "another wet day, like yesterday"
        else:
            rain_contrast = "dry again, like yesterday"

    return DayOverDayComparison(
        yesterday_high_c=yesterday_actual.high_c,
        yesterday_low_c=yesterday_actual.low_c,
        yesterday_rain=yesterday_actual.rain,
        yesterday_peak_wind_kmh=yesterday_actual.peak_wind_kmh,
        today_rain_expected=today_rain,
        today_consensus_high_c=round(consensus_high, 1) if consensus_high is not None else None,
        today_consensus_low_c=round(consensus_low, 1) if consensus_low is not None else None,
        today_consensus_peak_wind_kmh=round(consensus_wind, 1) if consensus_wind is not None else None,
        high_delta_c=high_delta,
        low_delta_c=low_delta,
        wind_delta_kmh=wind_delta,
        high_label=_band_label(high_delta, "warmer", "cooler"),
        wind_label=wind_label,
        rain_contrast=rain_contrast,
    )
