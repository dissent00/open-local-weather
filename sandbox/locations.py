"""The fleet, chosen so that between them they produce what Kisumu cannot.

Each entry is (name, latitude, longitude, timezone, icao, why it is here).
The ICAO code is recorded for the station-observation work item 47 covers; the
sweep itself does not need it yet.

CHOSEN FOR THE CASE THEY PRODUCE, not for coverage of the globe. Wellington is
here because it is one of the windiest inhabited places on earth and should
reach the gale bands that have never fired. Ulaanbaatar and Winnipeg are here
for day-over-day temperature swings that clear the 12 °C ceiling. Kisumu is
here as the control — if a band fires everywhere but here, that is the finding.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxLocation:
    name: str
    lat: float
    lon: float
    timezone: str
    icao: str
    why: str


# THE GLOBAL FLEET answers "what does this deployment's weather hide" — see
# README.md. Chosen for the cases they produce, not for coverage.
FLEET: tuple[SandboxLocation, ...] = (
    SandboxLocation("Kisumu", -0.0917, 34.7680, "Africa/Nairobi", "HKKI",
                    "the control — the deployment whose blind spots this is measuring"),
    SandboxLocation("Wellington", -41.2866, 174.7756, "Pacific/Auckland", "NZWN",
                    "roaring-forties wind; should reach gale bands that have never fired"),
    SandboxLocation("Reykjavik", 64.1466, -21.9426, "Atlantic/Reykjavik", "BIRK",
                    "north-Atlantic frontal passages, wind and rapid pressure falls"),
    SandboxLocation("Punta Arenas", -53.1638, -70.9171, "America/Punta_Arenas", "SCCI",
                    "southern-ocean gales, and a southern-hemisphere winter against our summer"),
    SandboxLocation("Ulaanbaatar", 47.8864, 106.9057, "Asia/Ulaanbaatar", "ZMUB",
                    "extreme continental range; the temperature ceiling should fire here"),
    SandboxLocation("Winnipeg", 49.8951, -97.1384, "America/Winnipeg", "CYWG",
                    "continental fronts with large day-over-day temperature swings"),
    SandboxLocation("Phoenix", 33.4484, -112.0740, "America/Phoenix", "KPHX",
                    "desert heat with near-zero precipitation; the dry bands at their limit"),
    SandboxLocation("Alice Springs", -23.6980, 133.8807, "Australia/Darwin", "YBAS",
                    "arid interior, huge diurnal range, sparse model agreement"),
    SandboxLocation("Mumbai", 19.0760, 72.8777, "Asia/Kolkata", "VABB",
                    "monsoon rainfall an order of magnitude past the wet band"),
    SandboxLocation("Singapore", 1.3521, 103.8198, "Asia/Singapore", "WSSS",
                    "equatorial convection almost daily; a thunder-heavy control"),
    SandboxLocation("Shannon", 52.7019, -8.9247, "Europe/Dublin", "EINN",
                    "maritime temperate, persistent wind, frequent frontal rain"),
    SandboxLocation("Denver", 39.7392, -104.9903, "America/Denver", "KDEN",
                    "lee-of-the-Rockies downslope wind and violent temperature changes"),
)


# THE REGIONAL FLEET answers a different question, and is kept separate so the
# two purposes stay legible. ROADMAP item 99: the operator wants a primary
# area plus supplementary ones a reader can choose — someone who lives in
# Kisumu and works in Homa Bay, which is common wherever people commute into
# a town.
#
# The open question is not whether it can be built. One Open-Meteo request
# returns six points, five models and the full hourly set in 1.2 seconds, and
# the label machinery is already point-agnostic. The question is HOW OFTEN A
# NEIGHBOUR ACTUALLY DIFFERS, because a difference that is there every day is
# geography rather than news — Kisii is 500 m higher and will read "cooler"
# forever. Accumulating these lets that threshold be chosen from data.
#
# ICAO CODES ARE MOSTLY EMPTY AND THAT IS THE FINDING, not an omission. Only
# Kisumu has a station, so every other area can be verified against reanalysis
# alone — which item 98 has just shown is the weak source for exactly the
# convective rain that matters here. Anyone filling these in should confirm
# the station actually files, rather than that an identifier exists.
NYANZA: tuple[SandboxLocation, ...] = (
    SandboxLocation("Kisumu", -0.0917, 34.7680, "Africa/Nairobi", "HKKI",
                    "the primary — every other area is a contrast against this"),
    SandboxLocation("Siaya", 0.0607, 34.2881, "Africa/Nairobi", "",
                    "56 km NW, 1315 m — lower relief contrast, tests whether distance alone matters"),
    SandboxLocation("Homa Bay", -0.5273, 34.4571, "Africa/Nairobi", "",
                    "60 km S on the lake shore, 1154 m — nearly Kisumu's elevation, so any difference is not relief"),
    SandboxLocation("Kisii", -0.6773, 34.7796, "Africa/Nairobi", "",
                    "65 km SE at 1684 m — 500 m higher, and the area most likely to differ every single day"),
    SandboxLocation("Migori", -1.0634, 34.4731, "Africa/Nairobi", "",
                    "113 km S, 1373 m — the far edge of the basin"),
    SandboxLocation("Winam Gulf", -0.25, 34.50, "Africa/Nairobi", "",
                    "35 km W on the water where Kisumu's boats actually are; the configured "
                    "secondary point is 194 km away in open lake and 9.5 C colder"),
)
