"""Where the current moment sits in the day, as a human would feel it.

WHY THIS EXISTS.

The evening refresh read like an early-afternoon summary, and the cause was
not the prompt's wording. The model was never told what time it was: the user
prompt's header carried a DATE and nothing else. "Shift emphasis toward
tonight" is an instruction with no anchor when the reader cannot tell 06:00
from 18:00, and it was competing with a full day of hourly data in which the
already-happened hours looked exactly like the ones still ahead.

WHY SUN-RELATIVE RATHER THAN CLOCK-RELATIVE.

"Evening" is not a clock reading. Kisumu's sunset moves by only a few minutes
across the year, but this project is built to be forked to any latitude, and
at 60°N "18:00" is the middle of the afternoon in June and long after dark in
December. A forecast that says "this evening" while the sun is still well up
is wrong in the way readers notice first.

The measured case that prompted this: the evening run fires at 18:15 and
Kisumu's sunset that day was 18:47. It was not evening. It was half an hour
before sunset, which is a different thing to write about — the light is going,
the day's heat is coming off, and what matters next is tonight.

ALL ARITHMETIC IN CODE, AS EVERYWHERE ELSE.

The LLM is told "it is 18:15, 32 minutes before sunset, the light is going"
and never asked to work that out. Solar noon is the midpoint of sunrise and
sunset, which is what solar noon means, so it needs no ephemeris.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# How long before sunset the light starts visibly going. Not an astronomical
# quantity — civil twilight is defined after sunset — but the point at which
# a person outdoors would say the evening is coming on.
DUSK_LEAD = timedelta(minutes=90)

# Sunrise is a moment; "early morning" is the stretch around it.
DAWN_LEAD = timedelta(minutes=60)
DAWN_TRAIL = timedelta(minutes=30)

# How long after sunset it still reads as evening rather than night.
EVENING_LENGTH = timedelta(hours=4)

# Either side of solar noon that reads as "the middle of the day".
MIDDAY_HALF_WIDTH = timedelta(hours=2)


@dataclass(frozen=True)
class DayPart:
    """The moment a forecast is issued, reduced to things worth saying."""

    local_time: str
    phase: str
    minutes_since_sunrise: int | None
    minutes_to_sunset: int | None
    sunrise: str
    sunset: str

    # Whole hours of daylight left. Rounded down: "2 hours of daylight left"
    # should not be said when there are 2 hours and 5 minutes of dusk.
    daylight_hours_left: int

    # A ready-made sentence for the prompt. Written here rather than left to
    # the model so the arithmetic and the phrasing are both deterministic.
    statement: str

    # What a reader at this hour actually wants, most pressing first. The
    # prompt uses this to decide emphasis instead of guessing.
    horizon: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "local_time": self.local_time,
            "phase": self.phase,
            "minutes_since_sunrise": self.minutes_since_sunrise,
            "minutes_to_sunset": self.minutes_to_sunset,
            "sunrise": self.sunrise,
            "sunset": self.sunset,
            "daylight_hours_left": self.daylight_hours_left,
            "statement": self.statement,
            "horizon": list(self.horizon),
        }


def _mins(delta: timedelta) -> int:
    return int(round(delta.total_seconds() / 60))


def _hhmm(t: datetime) -> str:
    return t.strftime("%H:%M")


# Beyond these, the sun-relative phases stop meaning anything: under the
# midnight sun there is no dusk to be 90 minutes before, and in polar night
# there is no morning. Measured, not assumed — Open-Meteo reports Longyearbyen
# in June as sunrise 00:00 with sunset exactly 24 hours later, which the
# ordinary logic would read as a normal day with a very late dusk.
#
# This project is built to be forked to any latitude, so this is not a
# hypothetical: it is the first thing that breaks north of the Arctic Circle.
CONTINUOUS_DAYLIGHT = timedelta(hours=22)
CONTINUOUS_DARK = timedelta(hours=2)


def classify_phase(now: datetime, sunrise: datetime, sunset: datetime) -> str:
    """The phase of the day, by the sun rather than the clock."""
    span = sunset - sunrise
    solar_noon = sunrise + span / 2

    # Where the sun does not rise or set, fall back to position around solar
    # noon. The phases still order the day correctly for a reader; what
    # changes is that the statement stops talking about sunrise and sunset,
    # because saying "3 hours until sunset" during the midnight sun is worse
    # than saying nothing.
    if span >= CONTINUOUS_DAYLIGHT:
        if now < solar_noon - MIDDAY_HALF_WIDTH:
            return "polar_morning"
        if now < solar_noon + MIDDAY_HALF_WIDTH:
            return "polar_midday"
        return "polar_afternoon"
    if span <= CONTINUOUS_DARK:
        return "polar_night"

    if now < sunrise - DAWN_LEAD:
        return "night"
    if now < sunrise + DAWN_TRAIL:
        return "dawn"
    if now < solar_noon - MIDDAY_HALF_WIDTH:
        return "morning"
    if now < solar_noon + MIDDAY_HALF_WIDTH:
        return "midday"
    if now < sunset - DUSK_LEAD:
        return "afternoon"
    if now < sunset:
        return "dusk"
    if now < sunset + EVENING_LENGTH:
        return "evening"
    return "night"


def _horizon_for(phase: str) -> tuple[str, ...]:
    """What matters most to someone reading at this hour.

    Ordered, and deliberately short. At dawn the whole day is ahead and the
    day is the story; by dusk most of it has happened and no amount of
    describing it helps anyone decide anything.
    """
    return {
        "polar_morning": ("today", "tonight"),
        "polar_midday": ("the rest of today", "tonight"),
        "polar_afternoon": ("the rest of today", "tonight", "tomorrow"),
        "polar_night": ("today", "tonight"),
        "night": ("the hours until dawn", "today"),
        "dawn": ("today", "tonight"),
        "morning": ("today", "tonight"),
        "midday": ("the rest of today", "tonight"),
        "afternoon": ("the rest of today", "tonight", "tomorrow"),
        "dusk": ("tonight", "tomorrow"),
        "evening": ("tonight", "tomorrow"),
    }[phase]


def _statement(
    phase: str,
    now: datetime,
    sunrise: datetime,
    sunset: datetime,
    next_sunrise: datetime | None,
) -> str:
    """One sentence placing the reader in the day.

    Phrased the way someone would say it out loud, because it is written into
    a prompt whose output is read by people who were outside ten minutes ago
    and already know what the sky is doing.
    """
    t = _hhmm(now)
    if phase.startswith("polar_"):
        # No sunrise or sunset to anchor to, so the sentence says so rather
        # than quoting a time that would mislead.
        if phase == "polar_night":
            return f"It is {t}. The sun does not rise at this time of year."
        part = {
            "polar_morning": "morning",
            "polar_midday": "the middle of the day",
            "polar_afternoon": "afternoon",
        }[phase]
        return f"It is {t}, {part}. The sun does not set at this time of year."
    if phase == "dawn":
        # Dawn straddles sunrise, so the sentence has to know which side of it
        # we are on. "The sun rises at 06:40" ten minutes after it did is the
        # kind of error a reader spots by looking out of a window.
        if now < sunrise:
            return f"It is {t}, first light — the sun rises at {_hhmm(sunrise)}."
        return f"It is {t}, just after sunrise ({_hhmm(sunrise)})."
    if phase == "morning":
        mins = _mins(now - sunrise)
        return (
            f"It is {t}, {_describe_span(mins)} after sunrise, with the "
            f"whole day still ahead."
        )
    if phase == "midday":
        return f"It is {t}, the middle of the day; the sun sets at {_hhmm(sunset)}."
    if phase == "afternoon":
        mins = _mins(sunset - now)
        return f"It is {t}, afternoon, with {_describe_span(mins)} until sunset."
    if phase == "dusk":
        mins = _mins(sunset - now)
        return (
            f"It is {t} and the light is going — sunset is at {_hhmm(sunset)}, "
            f"{_describe_span(mins)} from now."
        )
    if phase == "evening":
        mins = _mins(now - sunset)
        return f"It is {t}, {_describe_span(mins)} after sunset; it is dark."
    # Night spans midnight, so after sunset the sunrise worth naming is
    # tomorrow's. Pointing at this morning's — seventeen hours gone — would be
    # both useless and obviously wrong.
    rise = next_sunrise if (next_sunrise is not None and now > sunset) else sunrise
    return f"It is {t} and dark; the sun rises at {_hhmm(rise)}."


def _describe_span(minutes: int) -> str:
    """Durations as a person says them, not as a clock reports them."""
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"{minutes} minutes"
    hours, rem = divmod(minutes, 60)
    hour_word = "hour" if hours == 1 else "hours"
    if rem == 0:
        return f"{hours} {hour_word}"
    return f"{hours} {hour_word} {rem} minutes"


def summarize_daypart(
    now: datetime,
    sunrise: datetime,
    sunset: datetime,
    next_sunrise: datetime | None = None,
) -> DayPart:
    """Reduces the issuance moment to labels, a sentence and a horizon.

    All three arguments are LOCAL times for the forecast location, naive or
    consistently aware. Mixing the two would silently shift the phase
    boundaries, so the caller is responsible for handing over one or the
    other — see `pipeline`, which uses the location's own timezone throughout.

    `next_sunrise` is tomorrow's, used only after dark, where the sunrise a
    reader cares about is the coming one rather than this morning's.
    """
    phase = classify_phase(now, sunrise, sunset)
    before_sunrise = now < sunrise
    after_sunset = now > sunset

    daylight_left = timedelta(0) if after_sunset or before_sunrise else sunset - now
    if phase.startswith("polar_"):
        # "Hours of daylight left" is meaningless in both polar cases — either
        # all of them or none — so it is reported as zero and the statement
        # carries the meaning instead.
        daylight_left = timedelta(0)

    return DayPart(
        local_time=_hhmm(now),
        phase=phase,
        minutes_since_sunrise=None if before_sunrise else _mins(now - sunrise),
        minutes_to_sunset=None if after_sunset else _mins(sunset - now),
        sunrise=_hhmm(sunrise),
        sunset=_hhmm(sunset),
        daylight_hours_left=int(daylight_left.total_seconds() // 3600),
        statement=_statement(phase, now, sunrise, sunset, next_sunrise),
        horizon=_horizon_for(phase),
    )
