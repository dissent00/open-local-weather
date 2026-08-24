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


# What separates a wet day from a dry one with a shower in it.
#
# RAIN_THRESHOLD_MM (0.5) answers a different question — "did measurable rain
# fall in any hour", which is what per-model skill is scored on and must not
# change. It is a poor description of a DAY: half a millimetre at 20:00 and
# forty millimetres from dawn were both "rain", so the summary called both
# "another wet day". Kisumu on 2026-08-22 was clear and dry until evening
# convection, and read as a wet day.
DAY_RAIN_BANDS_MM = (
    (1.0, "dry"),
    (5.0, "largely dry"),
    (15.0, "showery"),
)
WET_DAY_LABEL = "wet"

# When rain arriving stops being a feature OF the day and becomes a feature
# AT THE END of it. Someone deciding how to spend a day cares enormously
# about the difference.
EVENING_ONSET_HOUR = 16
AFTERNOON_ONSET_HOUR = 12


def _onset_phrase(onset: str | None) -> str | None:
    """'evening', 'afternoon' or 'from the morning' — never a bare time.

    The reader is being told what shape the day has, not given a timestamp to
    interpret. A precise onset belongs in Today's Forecast, where it can be
    acted on.
    """
    if not onset:
        return None
    try:
        hour = int(onset.split(":")[0])
    except (ValueError, IndexError):
        return None
    if hour >= EVENING_ONSET_HOUR:
        return "evening"
    if hour >= AFTERNOON_ONSET_HOUR:
        return "afternoon"
    return "from the morning"


def describe_day_rain(precip_mm: float | None, onset: str | None) -> str | None:
    """One phrase for the rain character of a day: how much, and when.

    Returns None when there is no amount to reason from, so the caller omits
    the comparison rather than guessing — a gap must read as a gap.
    """
    if precip_mm is None:
        return None

    band = WET_DAY_LABEL
    for threshold, word in DAY_RAIN_BANDS_MM:
        if precip_mm < threshold:
            band = word
            break

    if band == "dry":
        return "dry"

    when = _onset_phrase(onset)

    # Timing only qualifies the wetter bands. "Largely dry from the morning"
    # reads as though the DRYNESS started in the morning; the band already
    # carries the whole story for a day with a couple of millimetres in it.
    if band == "largely dry":
        return "largely dry" if when != "evening" else "dry until evening showers"

    if when == "evening":
        # The case that prompted this. A dry day with evening storms is a dry
        # day, described as such, with the rain named for when it arrives.
        return f"dry until {'heavy evening rain' if band == 'wet' else 'evening showers'}"
    if when == "afternoon":
        return f"{band} from the afternoon"
    # Morning onset, or none recorded: the band alone is the whole story. A
    # wet day that started in the morning is simply a wet day.
    return band


def _consensus_onset(predictions: list[ModelPrediction]) -> str | None:
    """The median onset among models that expect rain, as "HH:MM".

    Median rather than mean: onset is a time of day, and one model calling
    dawn while three call evening should not average into mid-afternoon — a
    shape of day none of them forecast.
    """
    hours = []
    for p in predictions:
        if not p.onset:
            continue
        try:
            hours.append(int(p.onset.split(":")[0]))
        except (ValueError, IndexError):
            continue
    if not hours:
        return None
    hours.sort()
    return f"{hours[len(hours) // 2]:02d}:00"


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

    # Both days described by AMOUNT and TIMING, then compared — rather than
    # by whether any hour crossed 0.5 mm, which called a clear day with
    # evening storms "another wet day".
    #
    # today_rain (the boolean vote) is still computed and still stored, because
    # it is what the accuracy record scores. It is simply no longer what the
    # reader is handed.
    rain_contrast = None
    today_rain_votes = [p.rain for p in today_day0_predictions if p.rain is not None]
    today_rain = (
        sum(today_rain_votes) > len(today_rain_votes) / 2 if today_rain_votes else None
    )

    today_precip = mean([p.precip_mm for p in today_day0_predictions])
    today_onset = _consensus_onset(today_day0_predictions)
    today_character = describe_day_rain(today_precip, today_onset)
    yesterday_character = describe_day_rain(
        yesterday_actual.precip_mm, yesterday_actual.onset_hour
    )

    if today_character and yesterday_character:
        # These strings reach the reader almost verbatim: the prompt tells the
        # model to use rain_contrast AS GIVEN, precisely so it can't invent a
        # difference the numbers don't support. That makes the wording here a
        # user-facing decision, not an internal label — and the unchanged
        # cases need the most restraint, because there is genuinely no news
        # in them.
        if today_character == yesterday_character:
            # "again" alone carries it. The sentence this lands in is
            # already a day-over-day comparison that opens with "much like
            # yesterday", so appending ", like yesterday" produced "much like
            # yesterday - ... until evening showers again, like yesterday".
            rain_contrast = f"{today_character} again"
        else:
            # "X today; yesterday was Y" rather than "X after a Y day",
            # because the characters are phrases of varying shape and only
            # this frame reads correctly for all of them.
            rain_contrast = f"{today_character} today; yesterday was {yesterday_character}"

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
