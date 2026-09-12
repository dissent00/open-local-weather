"""Does what we can SEE contradict what we already said?

ROADMAP item 104, C2's third trigger. A judgment call is made when the
information moved, and the operator's correction to that rule was that
"information" is not only a new model cycle: observations are truth where
models are opinion, and local sensors update on their own schedule.

WHY THIS HAS TO BE COMPUTABLE IN CODE. Judging whether an observation
contradicts a forecast is itself judgment, and judgment is the expensive call
being decided on. A test that needed the model to run would be circular, so
everything here is arithmetic over values the pipeline already holds.

THE ASYMMETRY THAT GOVERNS EVERY TEST HERE. A mid-day observation can only
ever prove a forecast too LOW, never too high:

- rain that has fallen has fallen, but a day that has not rained YET may
  still — so observed rain contradicts a dry call, and observed dryness
  contradicts nothing;
- a maximum only rises, so an observed high above the call settles it, while
  one below it merely means the day is not over.

Treating the symmetric case as a contradiction would re-forecast every dry
morning of every wet day, which is the opposite of what C2 is for.

WHAT IS DELIBERATELY ABSENT, AND WHY. There is no wind test, though the
station reports wind and `today_properties` carries `peak_wind_kmh`. **They
describe different places.** That field is the wind at the SECONDARY point —
the prompt says so in terms, "NOT at the primary location" — while the METAR
station sits at the primary. Comparing them would report a disagreement
between two places as a disagreement between a forecast and reality, and
would do it most often when the two places genuinely differ, which is exactly
when the forecast is hardest.
"""

from __future__ import annotations

from dataclasses import dataclass

# What the forecast already committed to, from the standing issuance.
#
# `rain` is the SCORED boolean, taken from the blend's own Day+0 row rather
# than from the prose — the prose may hedge and the record does not.


@dataclass(frozen=True)
class StandingCall:
    rain: bool | None = None
    temp_high_c: float | None = None


@dataclass(frozen=True)
class ObservedSoFar:
    """What the station has actually reported TODAY, so far.

    Both fields are three-valued and absence means absence: a station that
    reported nothing is not a station reporting agreement. That is the same
    rule every other absent input in this project follows, and the one whose
    violation cost a published forecast on 2026-08-29.
    """

    precipitation: bool | None = None
    high_c: float | None = None


DISAGREEMENT_RAIN_WHILE_DRY = "rain_observed_while_dry_called"
DISAGREEMENT_HIGH_EXCEEDED = "high_already_exceeded"

# How far above the standing high an observation must sit before it counts.
#
# SIZED AGAINST TWO MEASURED QUANTITIES, not picked for roundness. The station
# reads +0.43 C against the reanalysis on average (item 44, quoted in
# fetch/metar.py), and the blend's Day+0 high error runs a few tenths. A
# margin at or below either would fire on the instrument rather than on the
# weather, and every spurious firing spends an LLM call.
#
# CONSERVATIVE AND NOT YET MEASURED. The honest threshold needs observed
# station highs through the day set against the standing call, which the
# record cannot supply until later issuances exist — the same bind C4's floor
# is in. Revisit against the record rather than tuning it against a
# convenient sample; item 100 records what that mistake cost last time.
TEMP_CONTRADICTION_MARGIN_C = 2.0


def observation_disagreements(
    standing: StandingCall,
    observed: ObservedSoFar,
    *,
    temp_margin_c: float = TEMP_CONTRADICTION_MARGIN_C,
) -> list[str]:
    """Codes for every way the observation settles against the standing call.

    Empty means "nothing seen contradicts what we said", which is NOT the
    same as "the forecast is right" — most of the day is usually still ahead.

    Order is stable because the result is stored and compared across two
    languages, which makes it part of the contract rather than an
    implementation detail.
    """
    found: list[str] = []

    if standing.rain is False and observed.precipitation is True:
        found.append(DISAGREEMENT_RAIN_WHILE_DRY)

    if (
        standing.temp_high_c is not None
        and observed.high_c is not None
        and observed.high_c >= standing.temp_high_c + temp_margin_c
    ):
        found.append(DISAGREEMENT_HIGH_EXCEEDED)

    return found
