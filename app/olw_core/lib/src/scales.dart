// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The published band word for an index value — upstream ROADMAP item 159
/// step 4.
///
/// WHY THIS IS CODE'S JOB AND NOT THE MODEL'S, measured on the upstream
/// archive rather than argued. Both `uv_index_max` and `air_quality_aqi` were
/// free text the model wrote, and both drifted in FORM: 41 stored UV values in
/// 4 shapes, and 39 AQI values in TWENTY. Ten of the AQI values are a range
/// rather than a number, the unit is spelled four different ways, and two put
/// the word before the number. A renderer cannot split those.
///
/// The word was never the model's to invent — the prompt already stated the US
/// EPA thresholds in prose, so the model was doing a table lookup by hand every
/// run. UV had no format rule at all, and its "(Very High)" convention appears
/// on only 21 of 41 days.
///
/// WHAT DOES NOT CHANGE IS THE NUMBER, and the two fields differ on why. For
/// AQI it is a real judgement — ground stations and CAMS disagree and stations
/// go stale. For UV it is not: upstream checked the 2026-09-21 archive and
/// found only `gfs_seamless` serving a UV index, with `best_match` duplicating
/// it value for value, so there is one source and nothing to blend.
library;

/// The UV index bands, from the WHO's own scale: 0-2 low, 3-5 moderate, 6-7
/// high, 8-10 very high, 11 and over extreme, as published in "Global Solar UV
/// Index: A Practical Guide". Stated as "value is BELOW this threshold".
const List<(double, String)> uvBands = [
  (3.0, 'Low'),
  (6.0, 'Moderate'),
  (8.0, 'High'),
  (11.0, 'Very high'),
];
const String uvExtremeLabel = 'Extreme';

/// The air quality bands, from the US EPA scale. The AQI is defined on whole
/// numbers, so these are exact integer boundaries and not midpoints.
///
/// "Unhealthy for sensitive groups" is spelled out rather than "USG": an
/// abbreviation a reader has to decode is not an at-a-glance answer.
const List<(int, String)> aqiBands = [
  (51, 'Good'),
  (101, 'Moderate'),
  (151, 'Unhealthy for sensitive groups'),
  (201, 'Unhealthy'),
  (301, 'Very unhealthy'),
];
const String aqiHazardousLabel = 'Hazardous';

/// The WHO's word for a UV index, or null when there is no index.
String? uvBand(double? index) {
  if (index == null) return null;

  for (final (threshold, word) in uvBands) {
    if (index < threshold) return word;
  }

  return uvExtremeLabel;
}

/// The US EPA's word for an air quality index, or null.
String? aqiBand(int? index) {
  if (index == null) return null;

  for (final (threshold, word) in aqiBands) {
    if (index < threshold) return word;
  }

  return aqiHazardousLabel;
}
