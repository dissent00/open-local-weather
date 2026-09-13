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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from openlocalweather.dates import weekday_name

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
    """Whole minutes, rounded half-UP by integer arithmetic.

    Not `round()`. Python's round() is half-to-EVEN and Dart's .round() is
    half-away-from-zero, so a duration with a 30-second remainder would come
    out a minute apart in the two implementations — the same divergence that
    once put "1006 hPa" on the site and "1007 hPa" in the app, and which no
    behavioural test catches because both answers look reasonable.

    Integer arithmetic sidesteps the question: there is no floating-point
    halfway case to disagree about.
    """
    seconds = int(delta.total_seconds())
    return (seconds + 30) // 60 if seconds >= 0 else -((-seconds + 30) // 60)


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


# Periods named once, so "tonight" cannot be read as "this evening" by one
# run and "the small hours" by the next. Everything from dusk to dawn is one
# period as far as a reader planning their night is concerned.
TONIGHT = "tonight (dusk, evening and overnight through to dawn)"
# Used when sunrise and sunset are unavailable. Midnight is midnight at every
# latitude, so this stays exactly true where "tonight" and "this evening"
# would be guesses — 18:00 is nearly dark in Kisumu and mid-afternoon in
# Tromsø in June, but "the rest of today" means the same in both.
REST_OF_TODAY_TO_MIDNIGHT = "the rest of today, through to midnight"
TODAY = "today"
REST_OF_TODAY = "the rest of today"
TOMORROW = "tomorrow"
UNTIL_DAWN = "the remaining hours until dawn"


def _horizon_for(phase: str, now: datetime, sunrise: datetime | None = None) -> tuple[str, ...]:
    """What matters most to someone reading at this hour.

    Ordered, and deliberately short. At dawn the whole day is ahead and the
    day is the story; by dusk most of it has happened and no amount of
    describing it helps anyone decide anything.

    "NIGHT" IS TWO SITUATIONS AND USED TO RETURN ONE ANSWER. `classify_phase`
    labels both 23:00 and 02:30 "night", but the day a reader is waiting for is
    the NEXT one before midnight and the CURRENT one after it. Returning
    (UNTIL_DAWN, TODAY) for both meant that before midnight the second entry
    named the hour or so already ending, which is not a forecast of anything —
    measured while giving the windows explicit bounds, it left 80 minutes a
    night in which a run said what the hours to dawn held and nothing at all
    about the day that followed.

    `sunrise` absent means the sun could not be placed, and the pre-midnight
    reading is the safe one: it promises a day still wholly ahead rather than
    one that may already be over.
    """
    if phase == "night":
        after_midnight = sunrise is not None and now < sunrise
        return (UNTIL_DAWN, TODAY) if after_midnight else (UNTIL_DAWN, TOMORROW)

    return {
        "polar_morning": (TODAY, TONIGHT),
        "polar_midday": (REST_OF_TODAY, TONIGHT),
        "polar_afternoon": (REST_OF_TODAY, TONIGHT, TOMORROW),
        "polar_night": (TODAY, TONIGHT),
        "dawn": (TODAY, TONIGHT),
        "morning": (TODAY, TONIGHT),
        "midday": (REST_OF_TODAY, TONIGHT),
        "afternoon": (REST_OF_TODAY, TONIGHT, TOMORROW),
        "dusk": (TONIGHT, TOMORROW),
        "evening": (TONIGHT, TOMORROW),
    }[phase]


def _statement(
    phase: str,
    now: datetime,
    sunrise: datetime,
    sunset: datetime,
    next_sunrise: datetime | None,
) -> str:
    """One plain sentence placing the reader in the day.

    Deliberately flat. An earlier draft read "it is 18:15 and the light is
    going — sunset is at 18:47, 32 minutes from now", which is more writing
    than the fact deserves and sets a register the forecast then has to either
    match or clash with. The model is being told a fact so it can decide what
    to emphasise; the prose belongs in the forecast, not in its scaffolding.
    """
    t = _hhmm(now)
    if phase.startswith("polar_"):
        # No sunrise or sunset to anchor to, so the sentence says so rather
        # than quoting a time that would mislead.
        if phase == "polar_night":
            return f"It is {t}. The sun does not rise at this time of year."
        return f"It is {t}. The sun does not set at this time of year."
    if phase == "dawn":
        # Dawn straddles sunrise, so the sentence has to know which side of it
        # we are on. "Sunrise is in 20 minutes" ten minutes after it happened
        # is the kind of error a reader spots by looking out of a window.
        if now < sunrise:
            return f"It is {t}. Sunrise is in {_describe_span(_mins(sunrise - now))}."
        return f"It is {t}. The sun rose at {_hhmm(sunrise)}."
    if phase == "morning":
        return f"It is {t}. Sunrise was at {_hhmm(sunrise)}, sunset is at {_hhmm(sunset)}."
    if phase == "midday":
        return f"It is {t}. Sunset is at {_hhmm(sunset)}."
    if phase in ("afternoon", "dusk"):
        return f"It is {t}. Sunset is in {_describe_span(_mins(sunset - now))}."
    if phase == "evening":
        return f"It is {t}. The sun set at {_hhmm(sunset)}."
    # Night spans midnight, so after sunset the sunrise worth naming is
    # tomorrow's. Pointing at this morning's would be both useless and
    # obviously wrong.
    rise = next_sunrise if (next_sunrise is not None and now > sunset) else sunrise
    return f"It is {t}. Sunrise is at {_hhmm(rise)}."


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
        horizon=_horizon_for(phase, now, sunrise),
    )


# --- Named windows with explicit clock bounds — ROADMAP item 104 -------------
#
# The horizon above says WHICH periods matter; it does not say when they start
# and stop, and a locked sentence that says "today" at 22:01 means two hours
# while the same word at 06:01 means eighteen. The operator's frame is that a
# run can happen at any time and the forecast is a look at what is ahead, so
# every named period the prompt uses now carries the clock range it covers.
#
# THE FIRST WINDOW STARTS AT THE ISSUANCE, NEVER EARLIER. That is the whole
# point: a window is a period still to come, and one that began at dusk is
# reported from now rather than from dusk when the reader is standing in it at
# 22:01.
#
# THE WINDOWS ARE CONTIGUOUS AND DO NOT OVERLAP, which is a decision and not
# an accident of the arithmetic. "The rest of today" and "tonight" genuinely
# overlap in ordinary speech — dusk to midnight belongs to both — and so do
# "tonight" and "tomorrow", because tonight runs past midnight to dawn.
# Printed with explicit bounds, overlapping windows invite a reader to count
# the same rain twice. So each window begins where the previous one ended, and
# only the first is anchored to the clock: "the rest of today" stops at dusk
# when tonight follows it, and "tomorrow" starts at sunrise when tonight
# precedes it, which is what a reader already means by the word once they have
# been told about the night.


@dataclass(frozen=True)
class ForecastWindow:
    """One named period still ahead, with the clock range it covers."""

    name: str
    start: str
    end: str

    # True when `end` falls on a later local date than `start`. The label
    # needs it, and so does any caller deciding whether to name a date.
    crosses_midnight: bool

    # The finished phrase, composed here so the prompt never has to assemble
    # one and two callers cannot word it differently.
    label: str

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "crosses_midnight": self.crosses_midnight,
            "label": self.label,
        }


def _midnight_after(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _natural_end(
    name: str,
    next_name: str | None,
    start: datetime,
    now: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    next_sunrise: datetime | None,
) -> datetime | None:
    """Where a named period ends, given what follows it.

    None when this module cannot place the end — the night periods without a
    `next_sunrise`, or a daylight period that must stop at a dusk it was not
    given a sunset for. A window whose end is unknown is dropped rather than
    guessed: "tonight (22:01 to ??)" is worse than saying nothing.

    `next_name` is what makes the daylight windows come out right, and getting
    it wrong is not subtle. "The rest of today" runs to midnight when it is the
    last thing said, but when TONIGHT follows it, it must stop at dusk instead
    — otherwise a 15:00 run reports "the rest of today (15:00-24:00)" and then
    "tonight (00:00-06:33)", which hands the evening to the day and leaves
    "tonight" meaning the small hours. TONIGHT is defined two screens up as
    dusk, evening and overnight together, and the bounds have to agree with the
    name.
    """
    if name in (TODAY, REST_OF_TODAY):
        if next_name in (TONIGHT, UNTIL_DAWN):
            return None if sunset is None else sunset - DUSK_LEAD
        return _midnight_after(now)

    # The no-sun variant says "through to midnight" in so many words, so it
    # ends there whatever follows — there is no dusk to hand over at.
    if name == REST_OF_TODAY_TO_MIDNIGHT:
        return _midnight_after(now)

    if name in (TONIGHT, UNTIL_DAWN):
        # THE FIRST SUNRISE AFTER THE WINDOW STARTS, which is not always
        # tomorrow's and is not decided by `now`.
        #
        # `classify_phase` returns "night" both before midnight and after it —
        # 23:00 and 02:30 carry the same label — so the phase cannot answer
        # this. Nor can the issuance moment: at 06:01, before sunrise, TONIGHT
        # is the night still to COME and ends at tomorrow's dawn, while
        # UNTIL_DAWN at 02:30 ends at today's. Both are "before sunrise" and
        # they want different answers. What separates them is where each
        # window BEGINS — 17:10 for the first, 02:30 for the second — so the
        # cursor decides and the clock does not.
        #
        # Measured while writing this: keying on `now` dropped the night
        # window from every pre-dawn run, because today's sunrise had already
        # passed the cursor and the window came out empty.
        if sunrise is not None and start < sunrise:
            return sunrise
        return next_sunrise

    if name == TOMORROW:
        # Through to the end of tomorrow. Anchored on `now` rather than on the
        # window's own start, so a window that begins at tomorrow's sunrise
        # still ends at tomorrow's midnight rather than the one after it.
        return _midnight_after(now) + timedelta(days=1)

    return None


def _crosses_midnight(start: datetime, end: datetime) -> bool:
    """Whether the window runs past the end of the day it began in.

    NOT `end.date() > start.date()`, which is wrong for the commonest window
    there is. A window closing at midnight is stored as 00:00 of the NEXT
    date, so that test called "06:33-24:00" a crossing — one daylight day,
    reported to the reader as though it ran into tomorrow.
    """
    return end > _midnight_after(start)


def _end_text(end: datetime) -> str:
    """Midnight reads as the end of the day it closes, not the start of the
    next one: "22:01-24:00" is one evening, "22:01-00:00" looks like a window
    of no length."""
    return "24:00" if end.hour == 0 and end.minute == 0 else _hhmm(end)


def _display_name(name: str, start: datetime, now: datetime, night: bool) -> str:
    """The period's name as the reader sees it.

    THE RULE IS TO NAME THE DAY WHENEVER THE RELATIVE WORD COULD BE READ
    AGAINST A DIFFERENT ONE, and there are two ways that happens.

    AROUND MIDNIGHT THE RELATIVE WORDS STOP AGREEING WITH EACH OTHER. A run at
    23:00 on Monday calls the coming day "tomorrow" and a run at 01:00 on
    Tuesday calls the same day "today" — two issuances two hours apart, naming
    one calendar day two different ways, and "today" at 1 am is the more
    treacherous of the two because a reader awake then usually means the day
    that just ended. Both say "Tuesday" instead.

    A FORECAST OUTLIVES THE DAY IT WAS WRITTEN ON. In the app a reader may not
    open it for days, and the last issuance stays on their screen; on the site
    the archive keeps every issuance forever. "Tomorrow" is wrong the moment
    the day turns and nothing in the text says so, while "Tuesday" stays true
    for as long as anyone can read it. Same reasoning that kept the day-over-day
    comparison off the previous issuance — see ROADMAP item 104.

    So a day window is named by weekday whenever it falls on a date other than
    the issuance's, and always at night. What is left with a relative word is
    the period the reader is standing in — "the rest of today", "tonight",
    "the remaining hours until dawn" — which are anchored to now by their own
    clock bounds and cannot drift.
    """
    if name not in (TODAY, TOMORROW):
        return name

    if night or start.date() != now.date():
        return weekday_name(start.date())

    return name


def _window_label(name: str, start: datetime, end: datetime) -> str:
    if _crosses_midnight(start, end):
        return f"{name} ({_hhmm(start)} to {_end_text(end)} next day)"
    return f"{name} ({_hhmm(start)}-{_end_text(end)})"


def forecast_windows(
    now: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    horizon: tuple[str, ...],
    next_sunrise: datetime | None = None,
) -> tuple[ForecastWindow, ...]:
    """The horizon's named periods, given explicit non-overlapping bounds.

    `horizon` comes from a DayPart, so the caller cannot pick a set of periods
    the phase does not call for. Local times throughout, matching
    summarize_daypart.

    Returns an empty tuple rather than raising when nothing can be placed, for
    the same reason every other block here reports absence: the prompt already
    knows how to say a thing is unavailable, and a forecast does not abort
    because it could not name a window.
    """
    # Night is one of the two conditions that force a weekday — see
    # _display_name. A sun-less run cannot establish the phase, so it falls
    # back to the date test alone, which needs no sun.
    night = (
        sunrise is not None
        and sunset is not None
        and classify_phase(now, sunrise, sunset) == "night"
    )

    windows: list[ForecastWindow] = []
    cursor = now

    for position, name in enumerate(horizon):
        next_name = horizon[position + 1] if position + 1 < len(horizon) else None
        end = _natural_end(name, next_name, cursor, now, sunrise, sunset, next_sunrise)
        if end is None:
            continue

        # A period wholly behind the reader is not a forecast. Dropped rather
        # than clamped to zero width, so nothing downstream has to decide what
        # an empty window means.
        if end <= cursor:
            continue

        shown = _display_name(name, cursor, now, night)
        windows.append(
            ForecastWindow(
                name=shown,
                start=_hhmm(cursor),
                end=_end_text(end),
                crosses_midnight=_crosses_midnight(cursor, end),
                label=_window_label(shown, cursor, end),
            )
        )
        cursor = end

    return tuple(windows)

# How far ahead a forecast issued now should look.
#
# Thirty hours so that a run at any time of day reaches through tonight and
# well into tomorrow. A 06:00 run gets to midday tomorrow; an 18:15 run gets
# to midnight tomorrow. Shorter, and a late-evening run would have nothing to
# say about the day people are asking about.
FORWARD_HOURS = 30


def forward_hours(
    hourly_multi_model: dict, now: datetime, hours_ahead: int = FORWARD_HOURS
) -> dict:
    """Trims multi-model hourly data to the hours still ahead.

    NARRATIVE ONLY. Nothing scored ever passes through here — per-model
    predictions come from `extract_day0_predictions_from_hourly` on the
    untrimmed day-0 fetch, and must, because scoring a partial day against a
    full day's observation would quietly reward a model for the hours it was
    not asked about.

    The reason this exists: an evening run received all 24 hours of today with
    the eighteen already-happened ones formatted identically to the six still
    to come. Given a full day, the model described the full day — including
    the afternoon its readers had just lived through.

    The current hour is kept rather than dropped. Someone reading at 18:15
    still cares what 18:00-19:00 holds, and dropping it would silently lose
    the hour they are standing in.
    """
    if not hourly_multi_model or not hourly_multi_model.get("hourly"):
        return hourly_multi_model
    h = hourly_multi_model["hourly"]
    times = h.get("time") or []
    if not times:
        return hourly_multi_model

    current_hour = now.replace(minute=0, second=0, microsecond=0)
    keep = [
        i
        for i, t in enumerate(times)
        if datetime.fromisoformat(t) >= current_hour
    ][:hours_ahead]

    # No overlap at all means the data does not cover now — a stale cache or a
    # timezone mismatch. Returning it untrimmed would hand back the wrong day
    # dressed as the right one, so return an explicitly empty series and let
    # the caller notice.
    if not keep:
        return {**hourly_multi_model, "hourly": {k: [] for k in h}}

    return {
        **hourly_multi_model,
        "hourly": {
            key: ([series[i] for i in keep if i < len(series)]
                  if isinstance(series, list) else series)
            for key, series in h.items()
        },
    }


def daypart_without_sun(now: datetime) -> DayPart:
    """The issuance moment when sunrise and sunset could not be fetched.

    The clock is not the sun. `now_in_tz` reads the system clock and cannot
    fail, while sunrise and sunset come over the network and can — so losing
    the second is no reason to discard the first. An earlier version put both
    in one try block and reported "time of day unavailable" for a run that
    knew perfectly well it was 18:15.

    Phase is "unknown" rather than guessed. Inferring dusk from a clock
    reading is precisely what this module exists to avoid: 18:15 is nearly
    dark in Kisumu and mid-afternoon in Tromsø in June, and a forecast that
    says "this evening" while the sun is high is wrong in the way readers
    notice first.

    The horizon still gives the model something precise to aim at. Midnight is
    midnight everywhere, so "the rest of today, through to midnight" then
    "tomorrow" holds at any latitude — where "tonight" would be a guess about
    where the sun is. Losing the sun costs the phase, not the precision.
    """
    return DayPart(
        local_time=_hhmm(now),
        phase="unknown",
        minutes_since_sunrise=None,
        minutes_to_sunset=None,
        sunrise="",
        sunset="",
        daylight_hours_left=0,
        statement=(
            f"It is {_hhmm(now)}. Sunrise and sunset could not be retrieved "
            f"for this location today, so treat the part of day as unknown."
        ),
        horizon=(REST_OF_TODAY_TO_MIDNIGHT, TOMORROW),
    )


# How far the system clock may drift from the server's before it is treated as
# wrong rather than merely imprecise. Generous: a couple of minutes changes
# nothing a forecast says, and a threshold too tight would cry wolf on every
# slightly-lagged container.
MAX_CLOCK_SKEW = timedelta(minutes=5)


def reconcile_now(
    system_local: datetime,
    server_date_header: str | None,
    utc_offset_seconds: int | None,
) -> tuple[datetime, str | None]:
    """The local time to use, and a warning if the system clock cannot be trusted.

    WHY A BACKUP IS NEEDED AT ALL.

    A common worry is that `datetime.now(ZoneInfo(tz))` might return UTC or the
    machine's own local time depending on how the host is configured. It does
    not: it takes the current instant and renders it in the requested zone, so
    the host's timezone setting is irrelevant. A server in California and one
    in Nairobi both produce the same Africa/Nairobi wall clock.

    What it DOES depend on is the machine's clock being right in absolute
    terms, and on the tz database being present. Neither is guaranteed — an
    unsynced VM, a container built without tzdata, a clock that drifted while
    the host was suspended. In every one of those the failure is silent: a
    forecast confidently written for the wrong part of the day.

    THE BACKUP.

    Every Open-Meteo response carries a `Date` header — the server's own UTC
    clock — and the forecast payload carries `utc_offset_seconds` for the
    requested location. Together they reconstruct local time without trusting
    this machine's clock OR its timezone database, on a call already being
    made for other reasons.

    Where the two disagree by more than MAX_CLOCK_SKEW, the server is believed.
    It is one machine's clock against a public API's, and the API is the one
    that would be noticed if it were wrong.
    """
    if not server_date_header or utc_offset_seconds is None:
        return system_local, None

    try:
        server_utc = parsedate_to_datetime(server_date_header)
    except (TypeError, ValueError):
        # An unparseable header is not a reason to distrust the clock; it is a
        # reason to stop checking.
        return system_local, None

    if server_utc.tzinfo is None:
        server_utc = server_utc.replace(tzinfo=timezone.utc)
    server_local = (server_utc + timedelta(seconds=utc_offset_seconds)).replace(tzinfo=None)

    skew = abs(server_local - system_local)
    if skew <= MAX_CLOCK_SKEW:
        return system_local, None

    return server_local, (
        f"System clock disagrees with the forecast server by {int(skew.total_seconds() // 60)} "
        f"minutes ({system_local:%Y-%m-%d %H:%M} local vs {server_local:%Y-%m-%d %H:%M}). "
        f"Using the server's time. Check NTP on this host — a wrong clock "
        f"silently produces a forecast written for the wrong part of the day."
    )
