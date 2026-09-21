"""The published band word for an index value — ROADMAP item 159 step 4.

WHY THIS IS CODE'S JOB AND NOT THE MODEL'S, measured on this project's own
archive rather than argued. Both `uv_index_max` and `air_quality_aqi` were
free text the model wrote, and both drifted in FORM exactly as
`format_temp_high_low`'s docstring says an LLM-written display value does:

    uv_index_max      41 stored values in  4 shapes
    air_quality_aqi   39 stored values in 20 shapes

The AQI field is the worse of the two and by a distance. Ten of its 39 values
are a RANGE rather than a number — "78-87 (Moderate)", "94 - 97 US AQI
(Moderate)", "78–87 (Moderate)" with an en-dash, "67 - 80 (Moderate)" with
spaces — and the unit is spelled "US AQI", "USAQI", "AQI" or omitted. Two put
the word FIRST: "Moderate (US AQI 85)". A renderer cannot split those into a
number and a word, and a tile that tried would be wrong on more than a
quarter of the days in the record.

THE WORD WAS NEVER THE MODEL'S TO INVENT. `prompt.py` already states the US
EPA thresholds in prose — "0-50 Good, 51-100 Moderate, 101-150 USG, 151+
Unhealthy/Hazardous" — so the model has been doing a table lookup by hand
every run and publishing its arithmetic. UV never had a format rule AT ALL;
the "(Very High)" convention is unprompted, and it appears on 21 of 41 days,
so a tile reading the word out of that string would be blank on half of them.

WHAT DOES NOT CHANGE IS THE NUMBER, AND THE TWO FIELDS DIFFER ON WHY.

For AQI it is a real judgement: the ground stations and CAMS are separate
sources that disagree, stations go stale, and on 2026-09-21 all three served
`aqi: null` with PM2.5 of 128, 104 and 92 while CAMS said 61. Choosing is
work, and it stays the model's.

For UV IT IS NOT A JUDGEMENT AND THIS IS WORTH KNOWING. Checked against the
2026-09-21 03:03Z archived prompt: of the five models only `gfs_seamless`
serves `uv_index`, and `best_match`'s hourly array is value-for-value
identical to it across all 30 values, while ECMWF, ICON and UKMO are null
throughout. So the "blend" is one model under two names, and the prompt's
"your synthesized BLENDED call across all models" cannot be true of this
field. Asking for it is asking the model to copy a number, which is the work
`format_temp_high_low` exists to stop. Computing it in code is item 161 and is
NOT done here.
"""

from __future__ import annotations

# THE UV INDEX BANDS, from the WHO's own scale.
#
# 0-2 low, 3-5 moderate, 6-7 high, 8-10 very high, 11 and over extreme, as
# published in "Global Solar UV Index: A Practical Guide" (WHO/WMO/UNEP/ICNIRP,
# 2002). National met services publish against the same scale, so a reader who
# has seen the word on a government forecast sees the same word here.
#
# The table is stated as "value is BELOW this threshold", which is what makes
# 2.9 low and 3.0 moderate — the guide's bands are on the rounded index, and
# rounding here rather than at the boundary would make 2.6 moderate.
UV_BANDS = ((3.0, "Low"), (6.0, "Moderate"), (8.0, "High"), (11.0, "Very high"))
UV_EXTREME_LABEL = "Extreme"

# THE AIR QUALITY BANDS, from the US EPA scale `prompt.py` already names.
#
# 0-50 Good, 51-100 Moderate, 101-150 Unhealthy for sensitive groups, 151-200
# Unhealthy, 201-300 Very unhealthy, 301+ Hazardous. The AQI is defined on
# whole numbers, so these are exact integer boundaries and not midpoints.
#
# "Unhealthy for sensitive groups" IS SPELLED OUT rather than "USG". The
# prompt's own prose abbreviates it, and the archive shows the model expanding
# it anyway — the one day it reached that band it wrote "Unhealthy for
# Sensitive Groups". An abbreviation a reader has to decode is not an
# at-a-glance answer.
AQI_BANDS = (
    (51, "Good"),
    (101, "Moderate"),
    (151, "Unhealthy for sensitive groups"),
    (201, "Unhealthy"),
    (301, "Very unhealthy"),
)
AQI_HAZARDOUS_LABEL = "Hazardous"


def uv_band(index: float | None) -> str | None:
    """The WHO's word for a UV index, or None when there is no index."""
    if index is None:
        return None

    for threshold, word in UV_BANDS:
        if index < threshold:
            return word

    return UV_EXTREME_LABEL


def aqi_band(index: int | None) -> str | None:
    """The US EPA's word for an air quality index, or None."""
    if index is None:
        return None

    for threshold, word in AQI_BANDS:
        if index < threshold:
            return word

    return AQI_HAZARDOUS_LABEL
