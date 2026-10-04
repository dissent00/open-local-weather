"""The served call, built from code — ROADMAP item 189.

WHAT IT ANSWERS. Until 2026-10-01 the numbers a reader was shown were the
LLM's judgment call, and the day's entry was written only after that call
returned: a morning where every route refused it left no entry at all,
though extraction, calibration and the comparison had already run. The code
blend (item 173) calls Day+0 rain better than the LLM did on the days they
share, so the served call is now the blend where it calls, the models'
equal-weight consensus where the record is too thin for it to, and never a
model's answer. The LLM's own call, when a route answers, is a hidden scored
row beside it — see pipeline._optional_judgment.

PURE, AND SHARED. Every input is a value the run already holds; nothing here
fetches, reads a file or calls a model. It is mirrored in `olw_core` and held
to this implementation by `spec/vectors/code_call.json`, because the app
must serve the same call from the same guidance.

THE SHAPE IS THE JUDGMENT CALL'S. The result is a `GeminiJudgmentResponse`,
so the entry composer, the write-up's "THE FORECASTER'S CALL" block and the
Dart runner read one shape whoever decided the numbers. The schema's own
comments say which fields are prose and which are the commitment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from openlocalweather.comparison import DRY_DAY_LABEL, WET_DAY_LABEL, consensus_onset, day_rain_band
from openlocalweather.defaults import CODE_BLEND_MODEL_ID
from openlocalweather.llm.schema import (
    ExtendedDayProperties,
    GeminiJudgmentResponse,
    TodayProperties,
)
from openlocalweather.models import ModelPrediction, ModelPredictionsByLead
from openlocalweather.synoptic import SynopticSnapshot, describe_pattern
from openlocalweather.verify.scoring import mean

# What decided the served numbers. Stored on the entry as `call_source`, so
# the record can tell a day the blend called from a day it declined and the
# consensus stood in — the first ten days of a fork, and any lead short of
# the check threshold.
CALL_SOURCE_CODE_BLEND = "code_blend"
CALL_SOURCE_CONSENSUS = "consensus"

# The tile's own ceiling, measured by the judgment prompt: over the 32 days to
# 2026-09-11 every label but one came in at 48 or fewer, and the box beside
# "High / Low" has room for a phrase and none for prose.
RAIN_LABEL_MAX_CHARS = 48

# A window wider than this is not a window — 2026-10-04, from the reader-words
# list: the wet models' onsets spanned 16 hours on 08-23 ("03:00 – 19:00")
# where the model wrote a three-hour one. Past it the tile shows the served
# onset alone, the number tomorrow scores.
ONSET_WINDOW_MAX_SPREAD_H = 6

# The amount bands that read as isolated rather than as showers, for the
# label — `day_rain_band`'s own words at its lower edge (under 5 mm).
_ISOLATED_BANDS = (DRY_DAY_LABEL, "largely dry")

# A thunder-timing phrase (daypart.ConvectiveTiming's words) or an onset word
# ("from the morning", "afternoon", "evening"), reduced to the one word a
# label has room for. Matched on substrings because the phrases carry "this
# evening", "from the afternoon", "overnight", "tomorrow night"; a phrase
# naming none of these gives no word, and the label says the kind alone.
_LABEL_WORDS = (("morning", "Morning"), ("afternoon", "Afternoon"), ("evening", "Evening"), ("night", "Overnight"))


class NoTemperatureToServe(RuntimeError):
    """No model carried a high or a low. A forecast without temperatures
    cannot be published, and inventing one is what this module exists to
    stop; the run aborts the way a failed Open-Meteo fetch does."""


@dataclass(frozen=True)
class ServedCall:
    judgment: GeminiJudgmentResponse
    source: str


def label_word(phrase: str | None) -> str | None:
    """The time-of-day word a label carries, from a composed phrase."""
    if not phrase:
        return None
    lowered = phrase.lower()
    for needle, word in _LABEL_WORDS:
        if needle in lowered:
            return word
    return None


def rain_label(*, rain: bool, precip_mm: float | None, convective: bool, when: str | None) -> str:
    """`rain_expected`: the call and roughly when, as a tile label.

    The vocabulary is the record's own — "Dry / No Rain", "Evening
    Thunderstorms", "Isolated Evening Showers & Thunderstorms" are values the
    LLM published — reduced to a rule: dry days name the thunder if any is
    expected; wet days name the amount band, the onset's word and whether
    storms are forecast. No full stop: a label, not a sentence.
    """
    if not rain:
        if not convective:
            return "Dry / No Rain"
        return f"Dry / {when} Thunder Possible" if when else "Dry / Thunder Possible"

    band = day_rain_band(precip_mm)
    amount = "Isolated " if band in _ISOLATED_BANDS else ""
    if convective:
        kind = "Showers & Thunderstorms"
    else:
        kind = "Rain" if band == WET_DAY_LABEL else "Showers"

    if when:
        return f"{amount}{when} {kind}"
    if amount:
        return f"{amount}{kind}"
    return f"{kind} Likely"


def onset_window_label(
    onsets: list[str], *, issued_hour: int | None, served_onset: str | None = None
) -> str | None:
    """`onset_window`: the spread of the wet models' onsets still ahead.

    A range when they differ, "From HH:MM" when they agree, None when none
    is ahead — an onset behind the issuance is an hour already lived
    through, and the prompt's own rule forbids narrating it. A spread past
    ONSET_WINDOW_MAX_SPREAD_H reads "From" the served onset (the median the
    record scores) instead, or from the earliest when none was served.
    """
    ahead = sorted(
        {o for o in onsets if o and (issued_hour is None or _hour_of(o) is None or _hour_of(o) >= issued_hour)}
    )
    if not ahead:
        return None
    if len(ahead) == 1:
        return f"From {ahead[0]}"

    first, last = _hour_of(ahead[0]), _hour_of(ahead[-1])
    if first is not None and last is not None and last - first > ONSET_WINDOW_MAX_SPREAD_H:
        return f"From {served_onset or ahead[0]}"
    return f"{ahead[0]} – {ahead[-1]}"


def _hour_of(hhmm: str) -> int | None:
    try:
        return int(hhmm.split(":")[0])
    except (ValueError, IndexError):
        return None


def cams_peak_aqi(air_quality: dict | None) -> int | None:
    """The day's peak US AQI from the CAMS hourly block, as the judgment
    prompt defined the field: 'us_aqi', never 'european_aqi', the day's
    peak, a plain number. None when the block or the series is absent."""
    hourly = (air_quality or {}).get("hourly") or {}
    values = [v for v in (hourly.get("us_aqi") or []) if v is not None]
    if not values:
        return None
    return round(max(values))


def _round1(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _consensus_rain(models: list[ModelPrediction]) -> tuple[bool, int] | None:
    """Equal-weight majority and its share, the blend's tie rule: dry."""
    votes = [p for p in models if p.rain is not None]
    if not votes:
        return None
    wet = sum(1 for p in votes if p.rain)
    return wet * 2 > len(votes), round(100 * wet / len(votes))


def served_call(
    *,
    day0_models: list[ModelPrediction],
    day3_models: list[ModelPrediction],
    day7_models: list[ModelPrediction],
    code_blend: ModelPredictionsByLead,
    secondary_day0: list[ModelPrediction],
    calibrated_gust_kmh: float | None,
    synoptic: SynopticSnapshot | None,
    air_quality: dict | None,
    convective: bool,
    thunder_when: str | None,
    onset_word_for: Callable[[str | None], str | None] | None,
    issued_hour: int | None,
    inputs: list[str],
) -> ServedCall:
    """The call the reader is shown, in the judgment call's shape.

    `inputs` are the blend's own inputs (`code_blend.blend_inputs`): the
    consensus votes over the same models the blend weighs, so a yardstick
    or the LLM's row in the stored set cannot move the fallback either.
    """
    voters = [p for p in day0_models if p.model in inputs]
    blend = next((p for p in code_blend.day0 if p.model == CODE_BLEND_MODEL_ID), None)
    source = CALL_SOURCE_CODE_BLEND if blend is not None else CALL_SOURCE_CONSENSUS

    if blend is not None:
        rain, probability = blend.rain, blend.rain_probability_pct
        onset = blend.onset
        precip_mm = blend.precip_mm
    else:
        called = _consensus_rain(voters)
        rain, probability = called if called is not None else (False, None)
        onset = consensus_onset([p for p in voters if p.rain]) if rain else None
        precip_mm = _round1(mean([p.precip_mm for p in voters]))

    # The blend's temperatures are corrected means over the models with a
    # record; a day without one falls back to the plain mean rather than to
    # nothing, because the entry cannot carry a null high.
    high = _round1((blend.high_c if blend is not None else None))
    if high is None:
        high = _round1(mean([p.high_c for p in voters]))
    low = _round1((blend.low_c if blend is not None else None))
    if low is None:
        low = _round1(mean([p.low_c for p in voters]))
    if high is None or low is None:
        raise NoTemperatureToServe("no model carried a high and a low for today")

    # The gust: the blend row's where the blend called — the pipeline hands
    # the blend the served gust, so reading it back keeps the served number
    # and the scored number one value by construction — else the record's
    # calibrated consensus (item 126) where it has one, else the models' mean.
    if blend is not None and blend.wind_kmh is not None:
        primary_gust = _round1(blend.wind_kmh)
    elif calibrated_gust_kmh is not None:
        primary_gust = _round1(calibrated_gust_kmh)
    else:
        primary_gust = _round1(mean([p.wind_kmh for p in voters]))
    secondary_gust = _round1(mean([p.wind_kmh for p in secondary_day0]))

    wet_onsets = [p.onset for p in voters if p.rain and p.onset]
    # NO TIMING WORD FOR AN HOUR ALREADY LIVED THROUGH — 2026-10-04, from the
    # reader-words list: an 18:01 run with a 15:00 onset wrote "Afternoon
    # Showers" for the hours ahead. The window below already drops such an
    # onset; the label's word follows the same rule.
    onset_ahead = bool(onset) and (
        issued_hour is None or _hour_of(onset) is None or _hour_of(onset) >= issued_hour
    )
    when = label_word(onset_word_for(onset) if (rain and onset_ahead and onset_word_for) else None)
    if not rain and convective:
        when = label_word(thunder_when)

    trend = mean([p.mslp_trend for p in voters])

    today = TodayProperties(
        rain_expected=rain_label(rain=rain, precip_mm=precip_mm, convective=convective, when=when),
        onset_window=onset_window_label(wet_onsets, issued_hour=issued_hour, served_onset=onset) if rain else None,
        peak_wind_primary_kmh=primary_gust,
        peak_wind_secondary_kmh=secondary_gust,
        temp_high_c=high,
        temp_low_c=low,
        rain=rain,
        onset_hour=onset,
        precip_mm=precip_mm,
        rain_probability_pct=probability,
        # None when no input carries a trend; the entry composer renders an
        # absent value as "", and a stored "" would read as a phrase.
        mslp_trend_24h=f"{trend:+.1f} hPa" if trend is not None else None,
        synoptic_pattern=describe_pattern(synoptic),
        air_quality_aqi=cams_peak_aqi(air_quality),
    )

    extended = []
    for lead, models in ((3, day3_models), (7, day7_models)):
        row = next((p for p in code_blend.for_lead(lead) if p.model == CODE_BLEND_MODEL_ID), None)
        if row is not None and row.rain is not None:
            extended.append(ExtendedDayProperties(lead_time_days=lead, rain=row.rain, rain_probability_pct=row.rain_probability_pct))
            continue
        called = _consensus_rain([p for p in models if p.model in inputs])
        if called is None:
            continue
        extended.append(ExtendedDayProperties(lead_time_days=lead, rain=called[0], rain_probability_pct=called[1]))

    return ServedCall(
        judgment=GeminiJudgmentResponse(today_properties=today, extended_properties=extended),
        source=source,
    )


def verification_summary(lead_time_results, *, visible_models: list[str]) -> str:
    """`yesterday_verification`, from the table the run just scored.

    Item 147 found the review does the learning better than the LLM's note
    did, and item 189 takes the note away from the model entirely: this says
    who called the rain right at each lead that verified, over the models
    the forecaster is shown, and nothing else.
    """
    sentences = []
    for result in lead_time_results:
        if result.target_date_verified is None:
            continue
        right = [m for m in visible_models if m in result.per_model_scores and result.per_model_scores[m].rain_correct]
        wrong = [m for m in visible_models if m in result.per_model_scores and not result.per_model_scores[m].rain_correct]
        if not right and not wrong:
            continue
        parts = []
        if right:
            parts.append(f"{_join(right)} called the rain right")
        if wrong:
            parts.append(f"{_join(wrong)} did not" if right else f"{_join(wrong)} missed the rain call")
        sentences.append(f"Day+{result.lead_time_days} for {result.target_date_verified.isoformat()}: {'; '.join(parts)}.")

    return " ".join(sentences) if sentences else "No verification was possible this run."


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"
