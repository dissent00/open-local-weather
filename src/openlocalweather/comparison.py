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

from openlocalweather.defaults import TEMP_CHANGE_BANDS_C, WIND_CHANGE_BANDS_KMH
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
    # Surfaced beside the label for the same reason today_rain_expected is:
    # a raw observation in the payload is much harder for the LLM to misread
    # than a phrase alone. None means no station observation, not "no thunder".
    yesterday_thunder: bool | None
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
    # The three labels above, composed into finished sentences — item 83.
    # This is what the PROMPT is given; the labels themselves stay in the
    # record because that is what is stored and scored.
    overview_comparison: str | None


def _band_label(
    delta: float | None,
    bands: list[tuple[float, str]],
    up: str,
    down: str,
) -> str | None:
    """Maps a signed delta onto a felt-change band.

    Bands, not raw numbers, because the consensus this is computed from will
    differ slightly from the LLM's final blended call. A band is stable
    across that gap; "1.3 degrees" would not be.

    THE FIRST BAND IS THE WHOLE LABEL — "about the same", "similar winds" —
    because a change too small to remark on has no direction worth naming.
    Every band above it is a MODIFIER on `up` or `down`, and an empty
    modifier means the bare word.
    """
    if delta is None:
        return None

    magnitude = abs(delta)
    no_change_threshold, no_change_label = bands[0]
    if magnitude < no_change_threshold:
        return no_change_label

    direction = up if delta > 0 else down
    for threshold, modifier in bands[1:]:
        if magnitude < threshold:
            return f"{modifier} {direction}".strip()

    return f"{bands[-1][1]} {direction}".strip()


# What separates a wet day from a dry one with a shower in it.
#
# RAIN_THRESHOLD_MM (0.5) answers a different question — "did measurable rain
# fall in any hour", which is what per-model skill is scored on and must not
# change. It is a poor description of a DAY: half a millimetre at 20:00 and
# forty millimetres from dawn were both "rain", so the summary called both
# "another wet day". Kisumu on 2026-08-22 was clear and dry until evening
# convection, and read as a wet day.
DRY_DAY_LABEL = "dry"

DAY_RAIN_BANDS_MM = (
    (1.0, DRY_DAY_LABEL),
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


def day_rain_band(precip_mm: float | None) -> str | None:
    """The amount band alone — "dry", "largely dry", "showery", "wet".

    Separated from describe_day_rain because the two answer different
    questions and one of them was being used for the other. The PHRASE
    carries timing and thunder as well, and only ever carries them for a day
    that has already happened: today's side of a comparison is a forecast, so
    it has no observed onset and no thunder. Comparing two phrases therefore
    compared a bare forecast against a rich observation, and they matched
    almost never — see compute_day_over_day.
    """
    if precip_mm is None:
        return None

    for threshold, word in DAY_RAIN_BANDS_MM:
        if precip_mm < threshold:
            return word

    return WET_DAY_LABEL


def describe_day_rain(
    precip_mm: float | None, onset: str | None, thunder: bool | None = None
) -> str | None:
    """One phrase for the rain character of a day: how much, when, and
    whether it thundered.

    Returns None when there is no amount to reason from, so the caller omits
    the comparison rather than guessing — a gap must read as a gap.

    `thunder` is an OBSERVATION and therefore only ever meaningful for a day
    that has already happened; today's side of the comparison always passes
    None. See DailyActual.thunder for why None and False differ.
    """
    if precip_mm is None:
        return None

    band = day_rain_band(precip_mm)
    when = _onset_phrase(onset)

    # Thunder outranks the amount. A storm that passes over the city and
    # drops half a millimetre is the thing the reader remembers about the
    # day, and calling that day "dry" to their face is how this project
    # loses their trust — they were standing outside in it. Measured case:
    # 2026-08-24, told to readers the next morning as "dry again".
    if thunder:
        if band == "dry":
            return "dry but thundery"
        if when == "evening":
            return "dry until evening thunderstorms"
        if when == "afternoon":
            return f"{band} with afternoon thunderstorms"
        return f"{band} with thunderstorms"

    if band == "dry":
        # The band edge was a cliff. 0.9 mm falling entirely at 17:00 read
        # "dry"; 1.1 mm at 17:00 read "dry until evening showers". A fifth of
        # a millimetre should not redescribe the day, so timing qualifies the
        # dry band too — an onset exists only when some hour actually crossed
        # the rain threshold, which is a shower whatever the daily total.
        if when == "evening":
            return "dry apart from a brief evening shower"
        if when == "afternoon":
            return "dry apart from a brief afternoon shower"
        if when == "from the morning":
            return "dry apart from an early shower"

        return "dry"

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

    THE SUBSET IS SELF-SELECTED AND MAY HAVE ONE MEMBER, in which case this
    returns that member's opinion under a name that says consensus. It is
    the caller's job to have established that rain is expected at all before
    asking when it starts — see compute_day_over_day, and 2026-09-08.
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
    today_convective: bool | None = None,
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

    # THE ONSET ANSWERS "WHEN", NEVER "WHETHER", so it is gated on the same
    # vote today_rain_expected reports. Both now come from one decision, and
    # the block can no longer contradict itself.
    #
    # Measured 2026-09-08, and the numbers are the argument. Of six models,
    # ecmwf alone forecast rain from 16:00, at 8.7 mm; the other four
    # carrying an amount forecast 0.3 to 0.7 mm. The mean that outlier
    # dragged to 2.12 mm banded as "largely dry", and _consensus_onset took
    # the median of a ONE-MEMBER list — a median with no consensus behind it
    # — so the phrase read "dry until evening showers today" beside
    # "today_rain_expected": false.
    #
    # The forecaster is handed both and told the phrase is verbatim. It
    # resolved toward the prose and said so: "my own rain: true call agrees
    # with the label's prose and disagrees with the block's boolean." A
    # payload that contradicts itself does not produce a refusal, it
    # produces a quiet choice — which is the judgement this file exists to
    # take away from the model.
    #
    # The minority's storm is not lost. It reaches the reader through the
    # convective block, which on that same day carried CAPE to 1360 J/kg,
    # and through Today's Forecast — where a risk belongs, and where it can
    # be hedged. The Overview's job is the shape of the day.
    today_onset = _consensus_onset(today_day0_predictions) if today_rain else None
    # SYMMETRY. Today's side used to pass thunder=None always, on the
    # reasoning that today has no thunder OBSERVATION — true, and it made the
    # comparison structurally incapable of ever calling today thundery while
    # yesterday always could be. Every thundery yesterday therefore
    # manufactured a change.
    #
    # Raised by the operator 2026-09-09 from a live Overview: "Largely dry,
    # after a thundery day. Thunderstorms are possible this evening" — a
    # contrast drawn against yesterday's storms, then an admission that today
    # has them too, one sentence later.
    #
    # Today does have a thunder signal: the convective flag, computed in code
    # from the hours ahead and already in the payload. A forecast against an
    # observation is not a perfect pairing, but it is the pairing the reader
    # is making anyway, and it is far better than comparing a value against
    # nothing. THE RULE IS GENERAL: a dimension may enter this comparison only
    # if BOTH days can be measured on it.
    today_character = describe_day_rain(today_precip, today_onset, thunder=today_convective)
    # observed_onset(), not onset_hour: a shower the reanalysis missed
    # entirely leaves onset_hour None, and the dry band's shower phrases are
    # reached by TIMING. Without this the description says "dry" for a day
    # the record scores as wet — the same contradiction, one layer down,
    # that item 42 was raised to fix.
    yesterday_character = describe_day_rain(
        yesterday_actual.precip_mm,
        yesterday_actual.observed_onset(),
        yesterday_actual.thunder,
    )

    if today_character and yesterday_character:
        # These strings reach the reader almost verbatim: the prompt tells the
        # model to use rain_contrast AS GIVEN, precisely so it can't invent a
        # difference the numbers don't support. That makes the wording here a
        # user-facing decision, not an internal label — and the unchanged
        # cases need the most restraint, because there is genuinely no news
        # in them.
        # NO RAIN NEWS IS NOT A SENTENCE. Two dry days in a row is the
        # commonest case here and it was producing the Overview's opening
        # clause every single day — "with dry today; yesterday was dry apart
        # from a brief evening shower", which is ungrammatical where it lands
        # and, worse, spends the first thing a reader sees on weather that
        # has already happened. Item 67's complaint, arriving by a different
        # route.
        #
        # SILENCE RATHER THAN "dry again", and the difference is not stylistic.
        # Item 53.1a exists because on 2026-08-29 the reanalysis recorded
        # 0.0 mm, the airport reported -RA at 19:00, and the forecast called
        # the day dry to someone who had stood in it. "dry again" makes that
        # false claim about yesterday. Saying nothing makes no claim at all —
        # and the shower is still in the verification notes and the detailed
        # discussion, which is where a reader goes to look it up. That is how
        # both wants are satisfied instead of traded.
        #
        # "again" then belongs to whatever is genuinely RECURRING, which on a
        # pair of dry days is usually the instability rather than the rain:
        # "convective instability spikes sharply again tonight" is the clause
        # that earns the word.
        # TEST WHAT THE SUMMARY REPORTS. This compared full CHARACTER phrases
        # while the else-branch below reports only the BAND, so two days in
        # the same band could differ as characters and be framed as a change:
        # "Largely dry, after a largely dry day" was reachable, and is not
        # English anybody means. The key is now exactly the pair the summary
        # is built from, so the test and the sentence cannot disagree.
        today_band = day_rain_band(today_precip)
        yesterday_band = day_rain_band(yesterday_actual.precip_mm)
        today_key = (today_band, bool(today_convective))
        yesterday_key = (yesterday_band, bool(yesterday_actual.thunder))
        both_dry = today_band == DRY_DAY_LABEL and yesterday_band == DRY_DAY_LABEL

        if both_dry and today_character == today_band and not yesterday_actual.thunder:
            # None is already the prompt's "omit the comparison" signal, so
            # this needs no new rule on that side.
            rain_contrast = None
        elif today_key == yesterday_key:
            # The sentence this lands in already opens with a day-over-day
            # comparison, so ", like yesterday" produced "much like yesterday
            # - ... until evening showers again, like yesterday".
            rain_contrast = f"{today_character} again"
        else:
            # ONE STATEMENT, NOT TWO. This was "X today; yesterday was Y",
            # which is a second sentence smuggled into a slot that allows
            # one, and it spent the reader's opening words on weather that
            # had already happened — the complaint item 67 raised and the
            # both_dry branch above already answers.
            #
            # "after a Y day" was tried first and rejected, correctly,
            # because yesterday_character varies in shape: "after a dry
            # until evening thunderstorms day" is not English. So YESTERDAY
            # CONTRIBUTES ONE WORD, which always fits the frame, while the
            # full phrase describes TODAY — the day the reader is walking
            # into.
            #
            # Thunder outranks the band, the same rule describe_day_rain
            # uses and for the same measured reason: 2026-08-24 thundered
            # over the city and was reported the next morning as "dry
            # again", to readers who had stood in it.
            yesterday_summary = (
                "thundery" if yesterday_actual.thunder else day_rain_band(yesterday_actual.precip_mm)
            )
            rain_contrast = f"{today_character}, after a {yesterday_summary} day"

    high_label = _band_label(high_delta, TEMP_CHANGE_BANDS_C, "warmer", "cooler")
    rain_unchanged = bool(rain_contrast) and today_key == yesterday_key
    wind_label = _band_label(wind_delta, WIND_CHANGE_BANDS_KMH, "windier", "calmer")

    return DayOverDayComparison(
        yesterday_high_c=yesterday_actual.high_c,
        yesterday_low_c=yesterday_actual.low_c,
        yesterday_rain=yesterday_actual.rain,
        yesterday_thunder=yesterday_actual.thunder,
        yesterday_peak_wind_kmh=yesterday_actual.peak_wind_kmh,
        today_rain_expected=today_rain,
        today_consensus_high_c=round(consensus_high, 1) if consensus_high is not None else None,
        today_consensus_low_c=round(consensus_low, 1) if consensus_low is not None else None,
        today_consensus_peak_wind_kmh=round(consensus_wind, 1) if consensus_wind is not None else None,
        high_delta_c=high_delta,
        low_delta_c=low_delta,
        wind_delta_kmh=wind_delta,
        high_label=high_label,
        wind_label=wind_label,
        rain_contrast=rain_contrast,
        overview_comparison=describe_day_over_day(
            high_label,
            wind_label,
            rain_contrast,
            today_character=today_character,
            rain_unchanged=rain_unchanged,
        ),
    )

# ROADMAP item 83. THE COMPOSITION CONTRACT.
#
# The three labels above are computed in code and the prompt orders them used
# verbatim. That half of the bargain works. The other half was never written
# down: a phrase the model may not alter must be GRAMMATICAL WHERE IT LANDS
# and must CARRY ITS OWN BASELINE, because the model has been forbidden from
# fixing either.
#
# Four defects came out of that gap, all in one real Overview on 2026-09-08:
#
#   "Slightly warmer and calmer today, with dry until evening showers today;
#    yesterday was largely dry — much the same through Friday..."
#
#   1. "dry until evening showers" is a sentence opener. After "with" it is
#      an adjective with no noun, and there was no legal move available: the
#      prompt said open with the comparison AND use the phrase verbatim.
#   2. Two baselines, neither stated. The labels measure today against
#      YESTERDAY; the extended trend measures the next three days against
#      TODAY. Welded, the day is warmer and also much the same.
#   3. "; yesterday was largely dry" is a second statement in a one-statement
#      slot, spending the reader's first words on a day already over.
#   4. "today" twice.
#
# THE FIX IS NOT A PROMPT RULE, and that is a finding rather than a
# preference. It was tried in this exact spot; see PROMPT_COMPARISON_FIELDS
# below, where a rule lost to a payload supplying its own counter-example and
# deleting the field is what worked. Prompt rule 8 ALREADY told the model to
# give a non-fitting phrase its own sentence, and the model still wrote "with
# dry until evening showers today". A rule instructing a model to repair
# input it was told not to alter is a rule against itself.
#
# So code composes, because code is what knows the shape of the phrases it
# wrote. What is left to the model is the judgement code cannot do: WHETHER
# to lead with this at all. Three quiet labels do not make a quiet day — the
# operator's point, and the reason "much like yesterday" is offered rather
# than imposed — because nothing here measures the sky, the air quality or
# how it felt, and a cloudy day at yesterday's temperature is not yesterday.
def describe_day_over_day(
    high_label: str | None,
    wind_label: str | None,
    rain_contrast: str | None,
    *,
    today_character: str | None = None,
    rain_unchanged: bool = False,
) -> str | None:
    """The whole day-over-day comparison as finished, punctuated sentences.

    Returns None when there is nothing to compare, which is the prompt's
    existing "omit it" signal and needs no new rule.

    THE RAIN PHRASE ALWAYS GETS ITS OWN SENTENCE. It is written as a sentence
    opener and there is no preposition it survives — which is defect 1, and
    the reason this function exists rather than a longer instruction.
    """
    quiet_high = TEMP_CHANGE_BANDS_C[0][1]
    quiet_wind = WIND_CHANGE_BANDS_KMH[0][1]

    moved = [
        label
        for label, quiet in ((high_label, quiet_high), (wind_label, quiet_wind))
        if label is not None and label != quiet
    ]

    measured = high_label is not None and wind_label is not None

    lead = None
    if moved:
        # "than yesterday" ONCE, on the clause that owns the comparison. The
        # unmoved label is dropped rather than listed: "slightly warmer and
        # similar winds" is an enumeration of one fact and one non-fact.
        lead = f"{' and '.join(moved)} than yesterday"
    elif measured and (rain_unchanged or not rain_contrast):
        # NOTHING MOVED ON ANY DIMENSION, so say that rather than reporting
        # one of them. Raised by the operator 2026-09-09: an Overview opening
        # "Largely dry with thunderstorms again" tells the reader the rain is
        # unchanged and says nothing about the temperature or the wind, which
        # were unchanged too.
        #
        # A claim about the measurements, not about the day, so a MISSING
        # label withholds it: a null wind label is absent data, not a quiet
        # wind, and this would be asserting a baseline never measured.
        lead = "much like yesterday"

    sentences = [s for s in (lead,) if s]

    if rain_contrast:
        # The lead has already made the comparison, so the rain half drops
        # its own "again" and simply describes today. Item 48's enumeration:
        # a reader told the day is like yesterday has been told the rain is.
        sentences.append(
            today_character if lead == "much like yesterday" and today_character else rain_contrast
        )

    if not sentences:
        return None

    return " ".join(f"{s[0].upper()}{s[1:]}." for s in sentences)


# ROADMAP item 61. How far the three-day high has to move before the word
# "warming" is honest.
#
# 2.0 C across the span, not a per-day drift. Item 23 is the measurement
# behind the size: a live run asked to compare 29.6 C against 29.5 C called
# it "about 1 C cooler" — a ten-fold overstatement in the one sentence most
# readers act on. The threshold has to clear ordinary day-to-day noise
# outright, because the cost of a false "warming trend" is a reader planning
# around a change that is not there, and the cost of a missed one is a
# sentence saying things are steady, which is also useful.
EXTENDED_TREND_THRESHOLD_C = 2.0


def describe_extended_trend(
    today_high_c: float | None,
    day_highs_c: list[float | None],
    day_precip_mm: list[float | None],
    last_day_name: str,
    today_wind_kmh: float | None = None,
    day_winds_kmh: list[float | None] | None = None,
) -> str | None:
    """One finished phrase for the next three days, or None when the data is
    too thin to say anything.

    ROADMAP item 61: the Overview stopped at today, so a reader deciding
    whether to move a job to Thursday had to get through the Extended Outlook
    to find out. This is the clause that answers it in the paragraph they
    already read.

    A FINISHED PHRASE, not a label or a flag, for the same reason
    `rain_contrast` ships "dry again" rather than a boolean: the prompt is
    told to use it verbatim, so anything left for the model to phrase is
    something the model can get wrong. "Wednesday through Friday show a
    consistent trend" is what a flag produces — bureaucratic, longer than the
    thing it replaces, and it says less than "much the same through Friday".

    A STEADY SPELL IS SAID, NOT SKIPPED. An earlier design had this go quiet
    when nothing was changing and the operator pushed back, correctly: "it
    will be about the same for the next few days" is one of the most useful
    things a forecast can tell someone choosing when to do a job. The absence
    of change IS the planning answer, and a reader told nothing has to go and
    check. So the steady band carries real words rather than a null.
    """
    highs = [h for h in day_highs_c if h is not None]
    if today_high_c is None or not highs:
        return None

    # The END of the span against today, not the mean. A reader planning
    # three days out wants to know where it ends up, and a warm-cool-warm
    # sequence averages into a steadiness none of the three days has.
    delta = highs[-1] - today_high_c

    # NAME THE SCOPE YOU ACTUALLY MEASURED, and widen it when you can.
    #
    # Raised by the operator 2026-09-09 in two steps. First: "'Much the same
    # through Saturday' — are temps, wind, everything much the same?" It was
    # a claim about the DAY+3 HIGH wearing the clothes of a claim about the
    # weather. Then, on the narrowed wording: "let's make sure we're not only
    # on temps — if it's temps/wind/cloudcover/precip/chance of thunderstorms
    # that are all the same, let's call it conditions."
    #
    # WIND WAS AVAILABLE AT DAY+1..3 THE WHOLE TIME and was being discarded,
    # so a three-day build in gusts under a flat temperature read as "much
    # the same". With it in, "conditions" is honest when every measured
    # dimension is steady, and the narrow noun is used when it is not.
    #
    # Cloud and convective risk are NOT available at these leads, so
    # "conditions" still means temperature, wind and rain. That is a wider
    # claim than before and a smaller one than the word suggests; it widens
    # again when item 65's cloud data lands.
    # Rain is reported only when it ARRIVES. A dry spell continuing is already
    # carried by "much the same", and a second clause saying so is the
    # enumeration item 48 was raised to stop. Computed here rather than below
    # because the scope noun depends on it.
    wet_days = [
        p for p in day_precip_mm if p is not None and day_rain_band(p) != DRY_DAY_LABEL
    ]

    wind_delta = None
    if today_wind_kmh is not None and day_winds_kmh:
        winds = [w for w in day_winds_kmh if w is not None]
        if winds:
            wind_delta = winds[-1] - today_wind_kmh

    moving = []
    if delta >= EXTENDED_TREND_THRESHOLD_C:
        moving.append("warming")
    elif delta <= -EXTENDED_TREND_THRESHOLD_C:
        moving.append("cooling")

    # The same threshold the day-over-day comparison uses for "not worth
    # remarking on", so a reader is not told about a change in one place that
    # the other calls noise.
    if wind_delta is not None and abs(wind_delta) >= WIND_CHANGE_BANDS_KMH[0][0]:
        moving.append("becoming windier" if wind_delta > 0 else "becoming calmer")

    if moving:
        trend = f"{' and '.join(moving)} through {last_day_name}"
    else:
        measured_wind = wind_delta is not None
        if not measured_wind:
            scope = "temperatures"
        elif wet_days:
            # "conditions much the same, with rain becoming more likely" would
            # contradict its own tail.
            scope = "temperatures and winds"
        else:
            scope = "conditions"
        trend = f"{scope} much the same through {last_day_name}"

    if wet_days:
        return f"{trend}, with rain becoming more likely"

    return trend


# What the PROMPT is shown, which is deliberately less than what is STORED.
#
# Everything below is arithmetic the model is told not to redo, and it was
# being handed the operands anyway. Measured 2026-09-05: with
# "yesterday_high_c": 30.4 and "wind_delta_kmh": -11.0 in the payload, a run
# produced "against yesterday's 30.4C" and "a drop of 11 km/h" — the two
# things an explicit STATE NO NUMBER FROM YESTERDAY rule had just forbidden,
# in the sentence right after the label it was supposed to use instead.
#
# Moving that rule to the front of the section did not fix it. Deleting the
# fields did. A rule cannot win against a payload that supplies its own
# counter-example, and the cheapest way to delete a rule is to delete the
# temptation.
#
# The full comparison is unchanged in the RECORD — asdict(day_over_day) is
# still what gets stored and scored. This narrows only the view handed to the
# forecaster.
#
# THE THREE LABELS WENT THE SAME WAY, 2026-09-08 (item 83). They were
# fragments of unstated grammatical shape, handed over with an order to use
# them verbatim and an instruction to weld them into one flowing sentence.
# Code now does the welding — describe_day_over_day — and the fragments are
# withdrawn for the reason above: leaving them beside the composed sentence
# leaves the temptation to re-weld them, and a rule would be all that stood
# in the way.
PROMPT_COMPARISON_FIELDS = (
    "yesterday_rain",
    "yesterday_thunder",
    "today_rain_expected",
    "overview_comparison",
)


def comparison_for_prompt(comparison: dict | None) -> dict | None:
    """The labels and the booleans, never the numbers behind them."""
    if comparison is None:
        return None

    return {k: comparison[k] for k in PROMPT_COMPARISON_FIELDS if k in comparison}

