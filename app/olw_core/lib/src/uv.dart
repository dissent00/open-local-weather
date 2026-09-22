// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The day's UV index: which day it describes, and which source said so.
///
/// Upstream ROADMAP item 161. The field was the model's to write and it was
/// copying a number: of the five models only `gfs_seamless` serves a UV index
/// and `best_match` duplicates it value for value on all 28 archived
/// issuances, while ECMWF, ICON and UKMO serve none. The prompt's "your
/// synthesized BLENDED call across all models" was never true of it.
///
/// WHAT MADE IT WORTH COMPUTING WAS THE DAY, NOT THE COPYING. Measured
/// against the run that wrote each entry, the published figure matched the
/// source exactly on 12 of 18 days and the band a reader sees never differed.
/// But `_horizonFor` drops today at dusk, and the 18:01 run's own prompt says
/// "WHAT MATTERS NOW: tonight, then tomorrow" while this field went on
/// reporting a peak six hours past. Over 11 archived evening runs the rule
/// changes the number on 6 and the band on 2.
library;

/// Which source answers, in order, NAMED rather than discovered.
///
/// `best_match` is second though it always agrees: it is Open-Meteo's own
/// blend and on this quantity it is GFS under another name, so preferring the
/// model that computed the figure keeps the record honest about where the
/// number came from. A national met service would outrank both — it issues
/// the public sun-safety advice — and upstream item 167 is what accepting one
/// would take.
const List<String> uvSourcePreference = ['gfs_seamless', 'best_match'];

/// The index, the day it describes, and who said so.
class DayUvIndex {
  const DayUvIndex({
    required this.index,
    required this.targetDate,
    required this.source,
  });

  final double index;
  final DateTime targetDate;
  final String source;
}

List<Object?> _values(Map<String, Object?> daily, String model) {
  final block = daily['daily'];
  if (block is! Map) return const [];
  final v = block['uv_index_max_$model'] ?? block['uv_index_max'];
  return v is List ? v : const [];
}

/// The UV index for the day the horizon is pointed at, or null.
///
/// [horizonHasToday] is whether `today` or `the rest of today` is among the
/// horizon's periods — the caller resolves that, because the period strings
/// belong to daypart.dart and this needs only the answer.
///
/// THE INDEX IS 0 OR 1 and nothing else: index 0 is the issuance day, and a
/// horizon that no longer holds today means tomorrow. There is no third case,
/// because no horizon this project builds skips a day.
DayUvIndex? dayUvIndex(
  Map<String, Object?> daily, {
  required bool horizonHasToday,
  required DateTime today,
  List<String> sources = uvSourcePreference,
}) {
  final dayIndex = horizonHasToday ? 0 : 1;
  final target = DateTime(today.year, today.month, today.day + dayIndex);

  for (final model in sources) {
    final values = _values(daily, model);
    if (dayIndex >= values.length) continue;

    final value = values[dayIndex];
    if (value is! num) continue;

    return DayUvIndex(
      index: value.toDouble(),
      targetDate: target,
      source: model,
    );
  }

  return null;
}
