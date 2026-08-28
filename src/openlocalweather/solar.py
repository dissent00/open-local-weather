"""Sunrise and sunset, computed rather than fetched.

WHY THIS EXISTS.

These were fetched from Open-Meteo's daily endpoint until 2026-08-28. On
GitHub's runners that one call never once succeeded: all 18 committed log
entries carry `sunrise: null`, and the three production run logs read in full
(2026-08-26, 08-27, 08-28) all logged the same 30 second read timeout against
`api.open-meteo.com/v1/forecast`. The same call from a laptop answers in well
under a second, so it was neither the endpoint being down nor a bug in the
tree — it was that one request, from that one place, reliably.

The degradation worked: the run continued and the prompt said the part of day
was unknown rather than guessing. But it degraded every single day, silently,
for six days — the site showed no sun times and every prompt in that window
was told the phase of the day was unknown, so the model wrote "it is 06:01"
without knowing whether that was before or after dawn.

Sunrise and sunset depend on latitude, longitude and date and nothing else.
Fetching a deterministic quantity over a network is the only part of it that
can fail, so this module computes them instead, and the failure mode goes
with the call.

THE ALGORITHM, AND HOW FAR IT CAN BE TRUSTED.

NOAA's solar position equations (the ones behind NOAA's own Solar Calculator),
evaluated for the sun's centre at an altitude of -0.833 degrees — half a
degree of solar radius so the UPPER LIMB touches the horizon, plus 34 arc
minutes of standard atmospheric refraction. That is the convention Open-Meteo
uses, which is what makes this a replacement rather than a change.

Measured against Open-Meteo over 108 consecutive days at each of eleven
locations, 2026-08-28:

| Location | Latitude | Worst disagreement |
|---|---|---|
| Kisumu, Quito, Singapore, Kathmandu, Sydney, London, Ushuaia | 0-55 deg | 1 min |
| Anchorage, Reykjavik | 61-64 deg | 2 min |
| Tromso | 69.6 deg | 14 min, median 2 |
| Longyearbyen | 78.2 deg | 21 min, median 4 |

The two polar conventions below matched on all 148 polar days in that sample,
with no mismatches.

The degradation at high latitude is inherent, and NOAA documents it above
roughly 72 degrees: near the poles the sun crosses the horizon at a very
shallow angle, so a small difference in the assumed altitude moves the
crossing by many minutes. It concentrates at the ends of the midnight sun,
where the crossing is shallowest - every Tromso disagreement above 5 minutes
falls in the three days after its midnight sun ended. This project is deployed
at 0.09 degrees south, where the disagreement is one minute at worst; a fork
above the Arctic Circle should read the table and decide whether it cares.

Whole minutes, truncated, which is Open-Meteo's convention and the Kenya Met
Department's - see `_to_whole_minutes` for why that beats rounding here.

TWILIGHT (roadmap item 30) is this same routine with a different altitude:
civil twilight is the sun's centre at -6 degrees. Not added here because
nothing consumes it yet, but this is now the only way to get it. Open-Meteo
has no twilight variable — verified, its daily endpoint offers sunrise,
sunset, daylight_duration and sunshine_duration, and rejects
`civil_twilight_begin` outright.

WHERE THIS DIVERGES FROM OPEN-METEO, ON PURPOSE AND OTHERWISE.

Open-Meteo keys its sun rows by UTC day. At Kiritimati (UTC+14) that puts the
sunrise it labels 2026-08-26 at 2026-08-27T06:26 local — the event on UTC
2026-08-26, rendered in a local time that is a day ahead. This module asks the
question the pipeline actually asks: what is sunrise on THIS LOCAL DATE. For
any timezone whose offset roughly matches its longitude the two agree; where
they do not, this one is the answer the caller wanted.

Open-Meteo also applies ONE UTC offset to every date in a response, and it is
not always the right one. Measured 2026-08-28: asked for Europe/London on
2025-12-15 — a date deep in GMT — it answered `utc_offset_seconds: 3600` and
put sunrise at 08:59, an hour after the 07:59 every published table gives.
Asked for the three days around the 2025 changeover it returned the same 3600
for all of them, so the two days after it were an hour out. Kisumu keeps no
daylight saving and the live pipeline only ever asked for today, so this never
reached production here; a fork in a DST zone would have met it immediately.
Taking the offset per date from the IANA zone (`dates.utc_offset_seconds`)
removes it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# The sun's centre when its upper limb sits on the horizon, refraction
# included. Half a degree of solar radius plus 34 arc minutes of standard
# refraction. Open-Meteo's convention, and the one every published sunrise
# table uses; changing it changes what the word "sunrise" means here.
SUN_ALTITUDE_AT_RISE_SET_DEG = -0.833

# Julian Date of 2000-01-01 12:00 UT, the epoch NOAA's polynomials are in.
J2000 = 2451545.0
DAYS_PER_JULIAN_CENTURY = 36525.0
# Julian Date of the Unix epoch, 1970-01-01 00:00 UT.
UNIX_EPOCH_JD = 2440587.5

SECONDS_PER_DAY = 86400.0
MINUTES_PER_DAY = 1440
MINUTES_PER_HOUR = 60
# The earth turns a degree every four minutes, which is what converts an hour
# angle in degrees into a time either side of solar noon.
MINUTES_PER_DEGREE_OF_ROTATION = 4.0

# Sunrise/sunset are found by fixed-point iteration: estimate the time, take
# the sun's position AT that time, correct the estimate. Three passes puts
# every latitude tested below a second of movement between passes; the cost is
# a handful of trigonometric calls on a quantity computed twice a day.
REFINEMENT_PASSES = 3


@dataclass(frozen=True)
class SunTimes:
    """Sunrise and sunset for one local date, as naive local wall clock.

    Naive because everything downstream is: `now_in_tz` and Open-Meteo's
    hourly timestamps are both naive local, and mixing an aware datetime into
    `summarize_daypart` would either raise or silently shift every phase
    boundary by the offset.

    Whole minutes, not seconds. The prompt only ever shows `%H:%M`, and an
    integer count is the one representation Python and Dart cannot round
    differently.
    """

    sunrise: datetime
    sunset: datetime

    def to_json(self) -> dict:
        return {
            "sunrise": self.sunrise.isoformat(),
            "sunset": self.sunset.isoformat(),
        }


def _solar_position(jd: float) -> tuple[float, float]:
    """The equation of time in minutes, and the sun's declination in radians.

    A transcription of NOAA's solar position equations. The coefficients are
    published constants of the earth's orbit, not choices made here, so they
    are left inline rather than each given a name that would only repeat the
    number back. `t` is the time in Julian centuries since J2000.
    """
    t = (jd - J2000) / DAYS_PER_JULIAN_CENTURY

    geom_mean_long = math.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0)
    geom_mean_anom = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    equation_of_centre = (
        math.sin(geom_mean_anom) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * geom_mean_anom) * (0.019993 - 0.000101 * t)
        + math.sin(3 * geom_mean_anom) * 0.000289
    )
    true_long = math.degrees(geom_mean_long) + equation_of_centre

    # The moon's ascending node, which wobbles the apparent position slightly.
    node = math.radians(125.04 - 1934.136 * t)
    apparent_long = math.radians(true_long - 0.00569 - 0.00478 * math.sin(node))

    mean_obliquity = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    obliquity = math.radians(mean_obliquity + 0.00256 * math.cos(node))

    declination = math.asin(math.sin(obliquity) * math.sin(apparent_long))

    # Written as a multiplication rather than `** 2` so the Dart port can be
    # the same two operations. `pow(x, 2)` and `x * x` agree on every value
    # anyone has found, but only one of them is obviously the same in both
    # languages, and this project has already been bitten by a rounding that
    # differed between them.
    y = math.tan(obliquity / 2.0)
    y = y * y
    equation_of_time = MINUTES_PER_DEGREE_OF_ROTATION * math.degrees(
        y * math.sin(2 * geom_mean_long)
        - 2 * eccentricity * math.sin(geom_mean_anom)
        + 4 * eccentricity * y * math.sin(geom_mean_anom) * math.cos(2 * geom_mean_long)
        - 0.5 * y * y * math.sin(4 * geom_mean_long)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * geom_mean_anom)
    )
    return equation_of_time, declination


def _julian_date(utc: datetime) -> float:
    """Julian Date of a naive UTC datetime."""
    return (utc - datetime(1970, 1, 1)).total_seconds() / SECONDS_PER_DAY + UNIX_EPOCH_JD


def _hour_angle_deg(lat_deg: float, declination: float) -> float | None:
    """Half the length of the day, as an angle. None where the sun never
    crosses the horizon at all — either way round; the caller knows which from
    the declination's sign against the latitude's."""
    lat = math.radians(lat_deg)
    cos_ha = (
        math.sin(math.radians(SUN_ALTITUDE_AT_RISE_SET_DEG))
        - math.sin(lat) * math.sin(declination)
    ) / (math.cos(lat) * math.cos(declination))

    if cos_ha > 1.0 or cos_ha < -1.0:
        return None
    return math.degrees(math.acos(cos_ha))


def _minutes_of_day(t: datetime) -> float:
    return t.hour * MINUTES_PER_HOUR + t.minute + t.second / MINUTES_PER_HOUR


def _solar_noon_utc(lon: float, day: date, utc_offset_seconds: int) -> datetime:
    """The moment the sun crosses the meridian, nearest to the local noon of
    `day`.

    "Nearest" rather than "on the same UTC date", because a timezone need not
    match its longitude: Kiritimati keeps UTC+14 at 157 degrees WEST, so its
    local noon and its solar noon sit on different UTC dates. Wrapping the
    difference into plus or minus twelve hours picks the right crossing
    without special-casing the date line.
    """
    local_noon = datetime(day.year, day.month, day.day, 12)
    noon_utc = local_noon - timedelta(seconds=utc_offset_seconds)

    equation_of_time, _ = _solar_position(_julian_date(noon_utc))
    for _ in range(REFINEMENT_PASSES):
        transit = _wrap_to_nearest(noon_utc, lon, equation_of_time)
        equation_of_time, _ = _solar_position(_julian_date(transit))

    return _wrap_to_nearest(noon_utc, lon, equation_of_time)


def _wrap_to_nearest(noon_utc: datetime, lon: float, equation_of_time: float) -> datetime:
    """Solar noon expressed as the crossing within twelve hours of `noon_utc`."""
    minutes_from_utc_midnight = (
        MINUTES_PER_DAY / 2 - MINUTES_PER_DEGREE_OF_ROTATION * lon - equation_of_time
    )
    delta = minutes_from_utc_midnight - _minutes_of_day(noon_utc)
    delta = (delta + MINUTES_PER_DAY / 2) % MINUTES_PER_DAY - MINUTES_PER_DAY / 2
    return noon_utc + timedelta(minutes=delta)


def _to_whole_minutes(utc: datetime, utc_offset_seconds: int) -> datetime:
    """A UTC instant as naive local time, truncated to the minute.

    TRUNCATED, NOT ROUNDED, and the choice is not arithmetic taste. Rounding
    is the better answer to "when is sunset" by about fifteen seconds on
    average, and it would put this project a minute ahead of both sources a
    reader can check it against: Open-Meteo truncates, and so does the Kenya
    Met Department bulletin this app prints beside its own figures — both give
    Kisumu 18:47 on 2026-08-19 where rounding gives 18:48. A forecast that
    disagrees with the bulletin on the same page is worse than one fifteen
    seconds less precise.

    Truncation is also the operation the rest of this codebase already applies
    to these values (`daypart._hhmm` is `%H:%M`), and floor is the one form of
    minute-taking Python and Dart cannot disagree about — neither language's
    `round()` is safe here, one being half-to-even and the other
    half-away-from-zero.
    """
    local = utc + timedelta(seconds=utc_offset_seconds)
    seconds = (local - datetime(1970, 1, 1)).total_seconds()
    return datetime(1970, 1, 1) + timedelta(minutes=math.floor(seconds / MINUTES_PER_HOUR))


def sun_times(lat: float, lon: float, day: date, utc_offset_seconds: int) -> SunTimes:
    """Sunrise and sunset on `day`, in local wall-clock time.

    `utc_offset_seconds` is the location's offset, not this machine's — the
    caller resolves it from the IANA zone (see `pipeline._sun_context`). A
    single offset for the whole day, which is Open-Meteo's convention too;
    on a DST changeover day an event on the far side of the transition is
    therefore an hour out, which is a minute of daylight nobody plans around.

    POLAR CONVENTIONS, matched to Open-Meteo's so `classify_phase` needs no
    change. Under the midnight sun, sunrise is local midnight and sunset is
    midnight the following day — a 24 hour span. In polar night both are local
    midnight, a span of zero. Both were verified against the live API on
    2026-08-28: Longyearbyen in June returns 00:00 and 00:00 next day with
    `daylight_duration` 86400, and in December returns 00:00 for both with
    `daylight_duration` 0. Note that the second contradicts a comment that
    stood in `pipeline._sun_context` until this change, which claimed polar
    night came back as nulls.
    """
    transit = _solar_noon_utc(lon, day, utc_offset_seconds)
    _, declination = _solar_position(_julian_date(transit))
    hour_angle = _hour_angle_deg(lat, declination)

    if hour_angle is None:
        midnight = datetime(day.year, day.month, day.day)
        # Which side of the horizon the sun is stuck on. The declination and
        # the latitude share a sign under the midnight sun and oppose it in
        # polar night.
        sunlit = (declination >= 0) == (lat >= 0)
        return SunTimes(midnight, midnight + timedelta(days=1) if sunlit else midnight)

    rise = transit - timedelta(minutes=MINUTES_PER_DEGREE_OF_ROTATION * hour_angle)
    fall = transit + timedelta(minutes=MINUTES_PER_DEGREE_OF_ROTATION * hour_angle)

    # The declination at solar noon is not quite the declination at sunrise,
    # and near the solstices at high latitude that difference is minutes. Each
    # pass re-reads the sun's position at the current estimate. A pass whose
    # hour angle has gone out of range is discarded rather than acted on: it
    # means the estimate has wandered to a day the sun does not cross the
    # horizon, and the crossing found at the transit is the real one.
    for _ in range(REFINEMENT_PASSES):
        _, rise_decl = _solar_position(_julian_date(rise))
        _, fall_decl = _solar_position(_julian_date(fall))
        rise_ha = _hour_angle_deg(lat, rise_decl)
        fall_ha = _hour_angle_deg(lat, fall_decl)
        if rise_ha is None or fall_ha is None:
            break
        rise = transit - timedelta(minutes=MINUTES_PER_DEGREE_OF_ROTATION * rise_ha)
        fall = transit + timedelta(minutes=MINUTES_PER_DEGREE_OF_ROTATION * fall_ha)

    return SunTimes(
        _to_whole_minutes(rise, utc_offset_seconds),
        _to_whole_minutes(fall, utc_offset_seconds),
    )
