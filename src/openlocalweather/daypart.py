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


def _horizon_for(phase: str) -> tuple[str, ...]:
    """What matters most to someone reading at this hour.

    Ordered, and deliberately short. At dawn the whole day is ahead and the
    day is the story; by dusk most of it has happened and no amount of
    describing it helps anyone decide anything.
    """
    return {
        "polar_morning": (TODAY, TONIGHT),
        "polar_midday": (REST_OF_TODAY, TONIGHT),
        "polar_afternoon": (REST_OF_TODAY, TONIGHT, TOMORROW),
        "polar_night": (TODAY, TONIGHT),
        "night": (UNTIL_DAWN, TODAY),
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
        horizon=_horizon_for(phase),
    )


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
