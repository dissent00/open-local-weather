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

from openlocalweather.defaults import (
    CLOUD_CHANGE_BANDS_PCT,
    KNOTS_TO_KMH,
    TEMP_CHANGE_BANDS_C,
    WIND_CHANGE_BANDS_KMH,
    WIND_WARNING_BANDS_KT,
)
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
    # The gust operand the LABEL was actually banded from — calibration.py,
    # and the reason the raw consensus above is kept beside it. The two differ
    # by the models' measured bias, and a stored comparison has to be
    # re-derivable from its own fields: a delta computed from one number and
    # stored beside another cannot be checked afterwards, which is most of
    # what the record is for. None means no model had enough verified checks
    # and the raw consensus was used.
    today_calibrated_peak_wind_kmh: float | None
    high_delta_c: float | None
    low_delta_c: float | None
    wind_delta_kmh: float | None
    high_label: str | None
    wind_label: str | None
    # Items 87, 65 and 83. The fourth measurement, and the one the operator's
    # founding objection was about: "a cloudy/rainy day with the same temps,
    # wind speed, and AQI is not 'much the same' even though 3/4 vectors may
    # be the same."
    cloud_label: str | None
    rain_contrast: str | None
    # The three labels above, composed into finished sentences — item 83.
    # This is what the PROMPT is given.
    #
    # THE SECOND HALF OF THIS COMMENT WAS FALSE and said "the labels
    # themselves stay in the record because that is what is stored and
    # scored". Checked 2026-09-14: nothing writes this dataclass to the entry
    # and no stored log carries a label. The labels stay HERE, on a structure
    # that lives for the length of a run. See PROMPT_COMPARISON_FIELDS.
    overview_comparison: str | None
    # Where yesterday's observed values were taken — item 98. Carried through
    # so comparison_for_prompt can name the source beside each boolean; the
    # comparison itself never reads it.
    provenance: dict[str, str] | None = None


def wind_warning(gust_kmh: float | None) -> str | None:
    """NOAA's marine wind descriptor for a gust reading, or None below the
    lowest band.

    A LEVEL, NOT A CHANGE, which is the defect this exists to close: every
    other label in this file is relative to yesterday, so two gale days
    compare as "similar winds", feed "much like yesterday", and never tell the
    reader it is dangerous.

    The thresholds are NOAA's and they apply to gusts by the standard's own
    wording — see WIND_WARNING_BANDS_KT, including what this cannot yet do:
    a daily PEAK is one gust and the criterion is FREQUENT gusts, so the
    caller must say "gusts reaching gale force" and never "Gale Warning".
    """
    if gust_kmh is None:
        return None

    name = None
    for knots, descriptor, _product in WIND_WARNING_BANDS_KT:
        if gust_kmh >= knots * KNOTS_TO_KMH:
            name = descriptor

    return name


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


def _onset_is_ahead(onset: str | None, issued_hour: int | None) -> bool:
    """Whether a timing qualifier is still a FORECAST at the issuance hour.

    ROADMAP item 118. Every timing phrase this module composes says, in
    effect, "and not before" — "dry until evening showers" asserts that the
    hours before the evening were dry. That is a forecast at 06:00 and a claim
    about the past at 18:00, and until this existed nothing checked which one
    it was.

    THE RULE IS NOT "DID IT ACTUALLY RAIN". This module has no observations of
    a day in progress and cannot acquire any here. It is the narrower one: do
    not assert what a period was like when that period has already elapsed.
    Same discipline as "unknown is not false", one layer up.

    Measured case, 2026-09-12: the day's FIRST issuance went out at 18:01
    local with the blend's onset at 18:00, and this function composed "dry
    until evening thunderstorms" for a day on which GFS had already put 3.5 mm
    on the ground at 15:00 and the reader had been rained on for hours. Four
    of the five models carried afternoon rain. The phrase is locked VERBATIM
    by the prompt, so no instruction the forecaster could have followed would
    have repaired it.

    `issued_hour` is None when THE HOUR BOUND DOES NOT APPLY, which is two
    different days rather than one. A day that is OVER and described from
    observations — yesterday's side of the comparison — where a timing phrase
    is a report rather than a claim. And a day that has NOT BEGUN — tomorrow,
    once contract item 8's evening subject exists — where every hour is still
    ahead by construction and nothing of it can have elapsed.

    Both are "always allowed", and they are the same rule seen from either
    side: the bound exists only for a day IN PROGRESS, which is the only kind
    of day whose hours can be partly spent. Stated as two cases because a
    reader meeting `issued_hour=None` at a new call site has to know which one
    they are in, and because the second one was added later.

    That case is passed explicitly rather than defaulted, so a new caller has
    to decide which day it is holding.
    """
    if onset is None:
        return False
    if issued_hour is None:
        return True

    try:
        onset_hour = int(onset.split(":")[0])
    except (ValueError, IndexError):
        return False

    return onset_hour > issued_hour


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
    precip_mm: float | None,
    onset: str | None,
    thunder: bool | None = None,
    *,
    issued_hour: int | None,
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
    when = _onset_phrase(onset) if _onset_is_ahead(onset, issued_hour) else None

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
    *,
    issued_hour: int | None,
    sunset_hour: int | None = None,
    tomorrow_predictions: list[ModelPrediction] | None = None,
    calibrated_wind_kmh: float | None = None,
    today_name: str | None = None,
    tomorrow_name: str | None = None,
    today_actual: DailyActual | None = None,
) -> DayOverDayComparison | None:
    """None when there is no observed record for the day being compared
    AGAINST — a gap must read as a gap, not as a day with unremarkable
    weather. Which day that is depends on the hour: yesterday in the morning,
    today after sunset.

    ALSO None WHEN THE HOUR HAS PASSED FOR IT — ROADMAP item 104, contract
    item 8, and `comparison_subject` carries the reasoning. A comparison earns
    its place while the day is mostly ahead; by mid-afternoon the reader has
    lived it. The prompt already handles the null: "If DAY-OVER-DAY COMPARISON
    is unavailable, simply omit the comparison rather than guessing or hedging
    about its absence."

    `sunset_hour` defaults to None so the morning half works for every caller
    that has not been given one, and the evening pivot simply never fires
    there. A missing boundary must not promote an afternoon into a comparison.
    """
    subject = comparison_subject(issued_hour, sunset_hour=sunset_hour)
    if subject is None:
        return None

    # WHICH DAY THE NUMBERS DESCRIBE — contract item 8, stage 2. After sunset
    # the subject is tomorrow, so the consensus must be built from TOMORROW'S
    # predictions. Until this existed the gate said "tomorrow" while the
    # arithmetic went on averaging today's Day+0 row: a comparison labelled one
    # thing and computed from another.
    #
    # THESE ARE NOT A NEW LEAD. `_locked_blocks` already extracts days 1, 2
    # and 3 on every run, from the same daily source and model list as the
    # scored Day+3 row, for `describe_extended_trend`. This is the same
    # predictions reaching a second consumer.
    #
    # None RATHER THAN A FALLBACK TO TODAY'S. A tomorrow comparison computed
    # from today's numbers against yesterday's observation means "Tuesday will
    # be cooler than Sunday", which is worse than silence — and the prompt
    # already knows what to do with an absent comparison.
    #
    # WHAT THE SENTENCE CALLS THINGS moves with it. A comparison about
    # tomorrow measured against today has to say so, and at 20:00 on a Monday
    # "dry until evening showers" is about Monday night to any reader who has
    # not been told otherwise. The operator's instruction, 2026-09-14:
    # substitute day names where it is ambiguous.
    #
    # NAMES ARE OPTIONAL AND THE FALLBACK IS PLAINER, NOT WRONGER. Without a
    # weekday the sentence still says "today" and "tomorrow", which is
    # unambiguous in every case except the one the names exist for — a reader
    # opening the page the next morning. A caller with no calendar gets the
    # honest lesser version rather than a parenthesis with nothing in it.
    baseline_comparative, baseline_similarity = "yesterday", "yesterday"
    subject_prefix = None
    # Tomorrow has not started, so every hour of it is ahead of this issuance
    # and no timing qualifier in it is a claim about elapsed hours — see
    # _onset_is_ahead, whose None means exactly that the hour bound does not
    # apply.
    character_issued_hour = issued_hour

    if subject == COMPARISON_SUBJECT_TOMORROW:
        if not tomorrow_predictions:
            return None
        # AND THE BASELINE MOVES WITH THE SUBJECT. "Tomorrow against yesterday"
        # is a comparison nobody asked for — the operator's 20:00 scenario is
        # tomorrow against TODAY'S daytime, which at 20:00 is closed and
        # final. None rather than falling back to yesterday, for the same
        # reason tomorrow's predictions are required: a sentence measured
        # against a day the reader has half forgotten is worse than silence.
        #
        # Only the STATION observes today — `archive-api` serves the current
        # day as model output — so this baseline arrives already narrowed to
        # the dimensions a station can honestly supply. See
        # `observed.observed_baseline`, which is where each exclusion is
        # argued, and expect wind, cloud and the rain amount to be absent.
        yesterday_actual = today_actual
        today_day0_predictions = tomorrow_predictions
        today_phrase = f"today ({today_name})" if today_name else "today"
        baseline_comparative = f"{today_phrase} was"
        baseline_similarity = today_phrase
        subject_prefix = f"{tomorrow_name} will be " if tomorrow_name else "tomorrow will be "
        character_issued_hour = None

    # A GAP MUST READ AS A GAP, not as a day with unremarkable weather — and
    # it is checked HERE, after the subject has chosen which day is the
    # baseline, rather than on the parameter. Before contract item 8's evening
    # subject the two were the same thing; now a 20:00 run with today's
    # observations in hand and no record for yesterday is a comparison that
    # can be made, and testing the parameter would have refused it.
    if yesterday_actual is None:
        return None

    consensus_high = mean([p.high_c for p in today_day0_predictions])
    consensus_low = mean([p.low_c for p in today_day0_predictions])
    consensus_wind = mean([p.wind_kmh for p in today_day0_predictions])

    def delta(today: float | None, yesterday: float | None) -> float | None:
        if today is None or yesterday is None:
            return None
        return round(today - yesterday, 1)

    # The sky, at last — items 87, 65 and 83. Percent on both sides, so this
    # is like for like: the models forecast cloud_cover and the reanalysis
    # observed it. The station's eighths are stored beside it as a
    # cross-check and are deliberately not the comparison basis, exactly as
    # the station's sustained wind sits beside the scored gust.
    consensus_cloud = mean([p.cloud_cover_pct for p in today_day0_predictions])
    cloud_delta = delta(consensus_cloud, yesterday_actual.cloud_cover_pct)

    # THE GUST OPERAND IS THE CALIBRATED ONE — see calibration.py for the
    # measurement, and for why the correction is not applied to the scored
    # rows. The label was reporting a model bias as weather: over 34 mornings
    # it read "calmer than yesterday" fourteen times and "windier" NOT ONCE,
    # at a mean delta of -8.31 km/h against an 8.0 km/h no-change band. The
    # band was being cleared by the bias alone. With the record's own
    # correction applied the mean is +0.24 km/h.
    #
    # THE RAW CONSENSUS IS NOT A FALLBACK ON PURPOSE — it is what a day with
    # too little verified history gets, because on such a day nothing has
    # measured a bias to remove and the honest operand is the one the models
    # gave. Which was used is recorded, not inferred.
    wind_for_label = consensus_wind if calibrated_wind_kmh is None else calibrated_wind_kmh

    high_delta = delta(consensus_high, yesterday_actual.high_c)
    low_delta = delta(consensus_low, yesterday_actual.low_c)
    wind_delta = delta(wind_for_label, yesterday_actual.peak_wind_kmh)

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
    # ROADMAP item 118. Today is a day IN PROGRESS and its phrase is composed
    # from a forecast, so a timing qualifier whose hour has passed is a claim
    # about hours nobody here can see — see _onset_is_ahead.
    today_character = describe_day_rain(
        today_precip, today_onset, thunder=today_convective, issued_hour=character_issued_hour
    )
    # observed_onset(), not onset_hour: a shower the reanalysis missed
    # entirely leaves onset_hour None, and the dry band's shower phrases are
    # reached by TIMING. Without this the description says "dry" for a day
    # the record scores as wet — the same contradiction, one layer down,
    # that item 42 was raised to fix.
    yesterday_character = describe_day_rain(
        yesterday_actual.precip_mm,
        yesterday_actual.observed_onset(),
        yesterday_actual.thunder,
        # The day is OVER and this is built from observations, so its timing
        # is a report rather than a claim and is never suppressed.
        issued_hour=None,
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
            # NOTHING ABOUT YESTERDAY HERE AT ALL. This slot has now shed two
            # backward glances in two days: "X today; yesterday was Y" was a
            # second sentence smuggled into a slot that allows one, and its
            # replacement, ", after a Y day", was still spending the
            # Overview's opening on a day the reader has already lived.
            #
            # Raised by the operator 2026-09-10, from that morning's live
            # Overview: "Much calmer than yesterday. Dry until evening
            # thunderstorms, after a thundery day."
            #
            # AND THE TAIL WAS PRINTING THE WRONG DIMENSION. This branch is
            # reached only when the two days DIFFER, and on that day they
            # differed on the band — yesterday measurably wet, today dry
            # until the evening. But the summary word put thunder ahead of
            # the band, so it named the one dimension where the days AGREED,
            # and the sentence drew a contrast out of what had not changed.
            # Choosing a better word was the smaller fix and is not the one
            # taken: the lead sentence already carries "than yesterday", and
            # once per Overview is enough.
            #
            # So the rain half simply describes TODAY — the day the reader is
            # walking into. Recurrence still reaches them, through the
            # "again" branch above, which is the one case where yesterday is
            # the news.
            rain_contrast = today_character

    high_label = _band_label(high_delta, TEMP_CHANGE_BANDS_C, "warmer", "cooler")
    cloud_label = _band_label(cloud_delta, CLOUD_CHANGE_BANDS_PCT, "cloudier", "clearer")
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
        today_calibrated_peak_wind_kmh=(
            round(calibrated_wind_kmh, 1) if calibrated_wind_kmh is not None else None
        ),
        high_delta_c=high_delta,
        low_delta_c=low_delta,
        wind_delta_kmh=wind_delta,
        high_label=high_label,
        wind_label=wind_label,
        cloud_label=cloud_label,
        rain_contrast=rain_contrast,
        provenance=dict(yesterday_actual.provenance) if yesterday_actual.provenance else None,
        overview_comparison=describe_day_over_day(
            high_label,
            wind_label,
            rain_contrast,
            cloud_label=cloud_label,
            today_character=today_character,
            rain_unchanged=rain_unchanged,
            # THE WARNING TAKES THE CALIBRATED GUST TOO, and this is the
            # consumer where it matters most. Every other label here is
            # relative and a shared bias partly cancels; a warning is a LEVEL
            # measured against NOAA's absolute thresholds, so a gust forecast
            # 12 km/h low sits a whole band below where it belongs and the
            # day it matters is the day it stays silent.
            wind_warning_name=wind_warning(wind_for_label),
            baseline_comparative=baseline_comparative,
            baseline_similarity=baseline_similarity,
            subject_prefix=subject_prefix,
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
    cloud_label: str | None = None,
    today_character: str | None = None,
    rain_unchanged: bool = False,
    wind_warning_name: str | None = None,
    baseline_comparative: str = "yesterday",
    baseline_similarity: str = "yesterday",
    subject_prefix: str | None = None,
) -> str | None:
    """The whole day-over-day comparison as finished, punctuated sentences.

    Returns None when there is nothing to compare, which is the prompt's
    existing "omit it" signal and needs no new rule.

    THE RAIN PHRASE ALWAYS GETS ITS OWN SENTENCE. It is written as a sentence
    opener and there is no preposition it survives — which is defect 1, and
    the reason this function exists rather than a longer instruction.

    THE BASELINE IS NAMED TWICE BECAUSE ENGLISH NAMES IT TWICE — item 104,
    contract item 8, stage 2. "Warmer than yesterday" and "much like
    yesterday" take the same word, and "warmer than today (Monday) WAS" and
    "much like today (Monday)" do not: the comparative needs a verb to place a
    day that is still in progress, and the similarity form reads as a
    stammer with one. Two parameters rather than one plus a rule, because a
    rule here would be a conditional on a string.

    `subject_prefix` names the day the rain phrase is ABOUT, and exists for
    the same reason. "Dry until evening showers." read at 20:00 on Monday is
    about Monday night to anyone who has not been told otherwise, and it is
    the operator's own instruction for this: substitute day names where it is
    ambiguous. The default is None because a comparison about TODAY has no
    ambiguity to resolve — the Overview is already about today.
    """
    dimensions = (
        (high_label, TEMP_CHANGE_BANDS_C[0][1]),
        (wind_label, WIND_CHANGE_BANDS_KMH[0][1]),
        (cloud_label, CLOUD_CHANGE_BANDS_PCT[0][1]),
    )

    moved = [label for label, quiet in dimensions if label is not None and label != quiet]

    measured = all(label is not None for label, _quiet in dimensions)

    lead = None
    if moved:
        # "than yesterday" ONCE, on the clause that owns the comparison. The
        # unmoved label is dropped rather than listed: "slightly warmer and
        # similar winds" is an enumeration of one fact and one non-fact.
        lead = f"{' and '.join(moved)} than {baseline_comparative}"
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
        lead = f"much like {baseline_similarity}"

    sentences = [s for s in (lead,) if s]

    if rain_contrast:
        # The lead has already made the comparison, so the rain half drops
        # its own "again" and simply describes today. Item 48's enumeration:
        # a reader told the day is like yesterday has been told the rain is.
        phrase = (
            today_character
            if lead == f"much like {baseline_similarity}" and today_character
            else rain_contrast
        )
        # The day name goes on the rain half and NOT on the lead, because the
        # lead already carries its own baseline ("than today (Monday) was")
        # and a second day name in one breath reads as two forecasts.
        sentences.append(f"{subject_prefix}{phrase}" if subject_prefix else phrase)

    if wind_warning_name:
        # A LEVEL, NOT A CHANGE, and it gets its own sentence so that nothing
        # can suppress it. Every other label here is relative to yesterday, so
        # two gale days running compare as "similar winds" and are then
        # swallowed by "much like yesterday" — true, and the least useful
        # thing that could be said on the day it matters most.
        sentences.append(f"gusting to {wind_warning_name}")

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

    # A LEVEL, NOT A TREND, for the same reason the day-over-day half needs
    # one: four dangerous days running are "conditions much the same".
    span_warning = wind_warning(max(
        (w for w in (day_winds_kmh or []) if w is not None), default=None
    ))

    if moving:
        trend = f"{' and '.join(moving)} through {last_day_name}"
    else:
        # A SCOPE NOUN CANNOT COVER WHAT THE TAIL IS ABOUT TO CONTRADICT.
        # "conditions much the same, with rain becoming more likely" denies
        # itself, and so does "winds much the same, with gusts reaching gale"
        # — steady and dangerous are both true of that wind, and welding them
        # into one clause reads as a mistake rather than as two facts.
        if wind_delta is None or span_warning:
            scope = "temperatures"
        elif wet_days:
            scope = "temperatures and winds"
        else:
            scope = "conditions"
        trend = f"{scope} much the same through {last_day_name}"

    # ONE "with", however many things follow it. Two tails stacked as
    # "with gusts reaching gale, with rain becoming more likely" reads as a
    # dropped word.
    tails = []
    if span_warning:
        tails.append(f"gusts reaching {span_warning}")
    if wet_days:
        tails.append("rain becoming more likely")

    if tails:
        return f"{trend}, with {' and '.join(tails)}"

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
# WHAT THIS NARROWS IS THE ONLY COPY THERE IS, and an earlier version of this
# comment said otherwise: "the full comparison is unchanged in the RECORD —
# asdict(day_over_day) is still what gets stored and scored". Checked
# 2026-09-14 and false in both halves. `comparison_for_prompt` is the sole
# consumer, nothing writes a DayOverDayComparison to the entry, and no stored
# log carries `overview_comparison` or any of the labels. The wider structure
# below exists for callers and tests, not for a record.
#
# So the fields dropped here are dropped from everything, which raises the
# stakes on the choice rather than lowering them — see the item on storing the
# comparison, where the case for a record is made on its own merits.
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


# Which observed field each exposed boolean actually comes from. ROADMAP
# item 98.
#
# TWO POINTS, PRESENTED AS ONE DAY. `yesterday_rain` is the reanalysis grid
# cell at the primary point; `yesterday_thunder` is the airport station, 3.8 km
# away at this deployment. They answer questions about different places at
# different scales, and nothing said so.
#
# Measured over the fortnight to 2026-09-08: the station and the cell
# disagreed on 4 OF 14 DAYS — three where the station saw rain the cell
# missed, one the reverse. Four independent careful readers have called that
# pairing a contradiction. It is not one; it is two places, and the fix is to
# say so rather than to reconcile values that were never measuring the same
# thing.
#
# A SOURCE IS NOT AN OPERAND. This adds where a value came from, never the
# value itself, so it does not reopen what PROMPT_COMPARISON_FIELDS closed.
OBSERVED_FIELD_SOURCES = {
    "yesterday_rain": "rain",
    "yesterday_thunder": "thunder",
}


def comparison_for_prompt(comparison: dict | None) -> dict | None:
    """The labels and the booleans, never the numbers behind them — plus
    where each observation was taken."""
    if comparison is None:
        return None

    view = {k: comparison[k] for k in PROMPT_COMPARISON_FIELDS if k in comparison}

    provenance = comparison.get("provenance") or {}
    sources = {
        exposed: provenance[stored]
        for exposed, stored in OBSERVED_FIELD_SOURCES.items()
        if stored in provenance and exposed in view
    }
    if sources:
        view["observed_from"] = sources

    return view



# What a day-over-day comparison is ABOUT at a given hour, or None when it
# should not appear at all — ROADMAP item 104, contract item 8.
COMPARISON_SUBJECT_TODAY = "today"
COMPARISON_SUBJECT_TOMORROW = "tomorrow"

# Where a day stops being mostly ahead. Local noon, and it is the day's own
# midpoint rather than a round number that happens to look like one.
COMPARISON_MORNING_ENDS_HOUR = 12


def comparison_subject(issued_hour: int | None, *, sunset_hour: int | None) -> str | None:
    """Whether a day-over-day comparison is worth reading at this hour, and
    what it should be about.

    THE OPERATOR'S FIVE SCENARIOS, 2026-09-14, are the specification. Asked
    what they wanted hour by hour, the answer was not a recast window but a
    gate:

      03:00, 06:00  the day ahead, against a previous full day
      15:00, 18:00  nothing — "I've already lived enough of it that I don't
                    care how it compares to yesterday"
      20:00         tomorrow from sunrise, against today's DAYTIME

    THIS WITHDRAWS "it is not suppressed at a late issuance", which contract
    item 8 asserted without reasoning. A comparison earns its place while the
    day is mostly ahead; by mid-afternoon the reader has lived it and is
    asking a different question.

    BOTH BOUNDARIES ARE THE DAY'S OWN. Noon separates a day mostly ahead from
    one mostly lived. SUNSET is where "the day ahead" stops meaning today and
    starts meaning tomorrow — which is why an 18:00 issuance is suppressed and
    a 20:00 one is not, on a day whose sun sets at 18:39. A fixed evening hour
    would put that pivot in the wrong place twice a year at latitude, and in
    the wrong place always for a fork somewhere else.

    NONE WHEN THE CLOCK IS UNKNOWN. `_issued_hour` returns 24 when the moment
    could not be established, and absence is not permission — the rule item
    118 applies to every other clock-dependent phrase.

    NONE AFTER SUNSET WHEN THERE IS NO SUNSET. Without one the evening pivot
    cannot be placed, so the morning half still works and the tomorrow subject
    simply never fires. A missing boundary must not promote an afternoon into
    a comparison.
    """
    if issued_hour is None or not 0 <= issued_hour < 24:
        return None

    if issued_hour < COMPARISON_MORNING_ENDS_HOUR:
        return COMPARISON_SUBJECT_TODAY

    if sunset_hour is not None and issued_hour > sunset_hour:
        return COMPARISON_SUBJECT_TOMORROW

    return None
