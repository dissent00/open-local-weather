"""The forecast's vocabulary, defined once and read by every surface.

ROADMAP item 56. The narrative is written to be technical where it needs to
be, and nothing explained its terms. Measured across the 30 stored narratives
(129,951 characters) on 2026-09-09: knots 171, hPa 156, "Day+N" 127, AQI 125,
J/kg 103, convective 88, CAPE 69, gust 67, instability 62, PM2.5/PM10 54,
UV 41, synoptic 37, MSLP 29, consensus 21, METAR 10, cumulonimbus 9.

STATIC DATA, NOT AN LLM CALL. A definition is the same kind of thing as
arithmetic: it has one right answer that does not depend on today's weather.
Generated per issuance it would cost a call and would define CAPE slightly
differently on Tuesday than on Monday — drift in the one part of the page
whose whole job is to be a fixed reference.

CITE THE STANDARD WHERE ONE EXISTS. `source` is filled only where the
definition or its numbers come from a body that publishes them, and it is
None where the entry is this project's own plain-English explanation. An
invented citation is worse than none, so the absence is deliberate and is
listed in item 56 rather than quietly left blank.

PORTED VERBATIM into olw_core/lib/src/glossary.dart, and pinned by
spec/vectors/glossary.json so the two cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GlossaryEntry:
    """One term. `definition` is what a reader sees; `source` is where its
    numbers come from, or None when the entry is our own wording."""

    term: str
    definition: str
    source: str | None = None


NOAA_MARINE = "US National Weather Service, marine forecast definitions (weather.gov/marine/faq)"
EPA_AQI = "US EPA Air Quality Index (airnow.gov/aqi/aqi-basics)"
# The standing source for meteorological vocabulary: 2,000+ terms, letter
# indexed at glossary.php?letter=<x>. CHECK IT BEFORE WRITING A DEFINITION —
# a term it defines should be defined its way, and a term it does not carry
# (like "consensus") is one we are genuinely on our own for.
NWS_GLOSSARY = "US National Weather Service Glossary (forecast.weather.gov/glossary.php)"

GLOSSARY: tuple[GlossaryEntry, ...] = (
    GlossaryEntry(
        "AQI",
        "Air Quality Index: a 0-500+ scale that converts pollutant "
        "concentrations into one number. 0-50 is Good, 51-100 Moderate, "
        "101-150 Unhealthy for Sensitive Groups, 151-200 Unhealthy, "
        "201-300 Very Unhealthy, 301 and above Hazardous. The number "
        "reported is the worst single pollutant, not an average of them.",
        EPA_AQI,
    ),
    GlossaryEntry(
        "PM2.5 and PM10",
        "Airborne particles under 2.5 and under 10 micrometres across, "
        "measured in micrograms per cubic metre. PM10 includes PM2.5 by "
        "definition, so PM10 can never be the smaller of the two. The finer "
        "particles reach deeper into the lungs, which is why PM2.5 usually "
        "drives the AQI.",
        EPA_AQI,
    ),
    GlossaryEntry(
        "CAPE",
        "Convective Available Potential Energy, in joules per kilogram: how "
        "much energy is available to lift air, and so how vigorous a "
        "thunderstorm could become. Below about 300 J/kg convection is "
        "unlikely, 300-1000 is marginal to moderate, and above 1000 supports "
        "thunderstorms. It measures POTENTIAL, not certainty — a day can "
        "carry high CAPE and never produce a storm, because something still "
        "has to lift the air.",
        NWS_GLOSSARY,
    ),
    GlossaryEntry(
        "instability",
        "The atmosphere's willingness to keep lifting air that has started "
        "rising. An unstable day is one where a small nudge can grow into a "
        "storm. CAPE is how it is measured here.",
    ),
    GlossaryEntry(
        "convective",
        "Driven by rising air rather than by a large weather system. "
        "Convective rain is showery, local and short-lived, and can fall "
        "heavily on one side of a town while the other stays dry.",
        NWS_GLOSSARY,
    ),
    GlossaryEntry(
        "gust versus sustained wind",
        "Sustained wind is an average over a period, usually ten minutes; a "
        "gust is a brief peak within it. Gusts run roughly one and a half "
        "times the sustained speed, and they are what moves boats and "
        "branches. Wind figures in this forecast are GUSTS unless it says "
        "otherwise.",
    ),
    GlossaryEntry(
        "gale force",
        "Sustained wind, or frequent gusts, of 34 to 47 knots (63-87 km/h). "
        "A large-scale event lasting hours, driven by a front or system "
        "passing — distinct from the sudden gusts a single thunderstorm "
        "produces, which are violent, local and over in minutes.",
        NOAA_MARINE,
    ),
    GlossaryEntry(
        "storm force",
        "Sustained wind, or frequent gusts, of 48 to 63 knots (89-117 km/h). "
        "Above 64 knots (118 km/h) is hurricane force.",
        NOAA_MARINE,
    ),
    GlossaryEntry(
        "knot",
        "One nautical mile per hour, 1.852 km/h. Used for wind because "
        "marine and aviation forecasts worldwide use it, so a figure in "
        "knots can be compared against any of them.",
    ),
    GlossaryEntry(
        "hPa and MSLP",
        "Hectopascals, the unit of air pressure, and Mean Sea Level "
        "Pressure — the reading corrected to sea level so that places at "
        "different altitudes can be compared. Around 1013 hPa is average. "
        "Falling pressure generally means unsettled weather approaching; "
        "the RATE of change says more than the value.",
    ),
    GlossaryEntry(
        "synoptic",
        "Of the large scale — systems spanning hundreds to thousands of "
        "kilometres, such as fronts and pressure centres, as opposed to a "
        "single storm cell over one town.",
    ),
    GlossaryEntry(
        "Day+0, Day+3, Day+7",
        "How far ahead a forecast was made. Day+0 is today, forecast this "
        "morning; Day+3 is today, forecast three days ago. Accuracy is "
        "tracked separately at each, because a model good at tomorrow is not "
        "necessarily good at next week.",
    ),
    GlossaryEntry(
        "consensus",
        "What the numerical models agree on before any judgement is applied "
        "— here, the average across them. Where they disagree sharply, the "
        "spread is reported rather than averaged away, because the "
        "disagreement is itself the information.",
    ),
    GlossaryEntry(
        "METAR",
        "The standard hourly weather report filed by airports worldwide. It "
        "is a direct observation rather than a model output, which makes it "
        "the closest thing to ground truth available here.",
    ),
    GlossaryEntry(
        "cumulonimbus",
        "The tall, anvil-topped cloud that produces thunderstorms. Its "
        "presence in an airport report is a direct observation of a storm, "
        "not a forecast of one.",
        NWS_GLOSSARY,
    ),
    GlossaryEntry(
        "onset",
        "The hour rain is expected to begin. A forecast of the same total "
        "rainfall starting at dawn or at dusk describes two very different "
        "days, so onset is scored separately from amount.",
    ),
    GlossaryEntry(
        "UV index",
        "How strong the sun's ultraviolet radiation is, on an open-ended "
        "scale where 3 is moderate, 6 high, 8 very high and 11 extreme. It "
        "peaks near midday and is barely reduced by thin cloud.",
    ),
)
