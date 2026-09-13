// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// What a machine can check in the narrative, checked before it is published.
///
/// A faithful port of `openlocalweather/claims.py` — see that file for the
/// full reasoning. In brief: most of what a forecast asserts cannot be checked
/// here and this does not try. What it checks is the narrow set of claims that
/// are decidable with certainty, and the first is the calendar — a weekday
/// paired with a date is either right or wrong, and no weather comes into it.
///
/// Measured upstream on 2026-09-13: of 39 weekday/date pairings the published
/// record asserts, 7 are false, on four separate days. `forwardCalendar` now
/// hands the pairings over finished; this notices if one is wrong anyway.
///
/// A FINDING DOES NOT STOP A RUN. It is recorded and the forecast publishes.
library;

import 'dates.dart' show weekdayName;

const List<String> claimWeekdays = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

const List<String> claimMonths = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

/// Which check produced a finding. Mirrors `claims.CLAIM_WEEKDAY`.
const String claimWeekdayMismatch = 'weekday_date_mismatch';

final String _days = claimWeekdays.join('|');
final String _months = claimMonths.join('|');

/// "Monday (16 September)", "Monday, 16 September", "Monday 16 September".
final RegExp _dayFirst =
    RegExp(r'\b(' + _days + r')\b[\s,(]+(\d{1,2})(?:st|nd|rd|th)?\s+(' + _months + r')\b');

/// "Monday (September 16)", "Monday, September 16".
final RegExp _monthFirst =
    RegExp(r'\b(' + _days + r')\b[\s,(]+(' + _months + r')\s+(\d{1,2})(?:st|nd|rd|th)?\b');

/// "Monday, 2026-09-16".
final RegExp _iso =
    RegExp(r'\b(' + _days + r')\b[\s,(]+(\d{4})-(\d{2})-(\d{2})\b');

/// How far from the run's own date a bare "17 August" may be resolved.
const int _nearestWindowDays = 183;

/// The occurrence of month/day closest to [today].
///
/// A YEAR IS RARELY WRITTEN AND MUST NOT BE ASSUMED. A forecast issued on
/// 31 December mentioning "Saturday, 2 January" means the NEXT year, and
/// resolving that against the run's own year would invent a mismatch — a false
/// alarm is worse than the defect, because a check that cries wolf is one
/// nobody reads.
DateTime? _nearest(DateTime today, int month, int day) {
  DateTime? best;
  var bestGap = 0;
  for (final year in [today.year - 1, today.year, today.year + 1]) {
    final candidate = DateTime(year, month, day);
    // DateTime rolls an impossible date forward — 29 February in a non-leap
    // year becomes 1 March — so the roll is detected rather than trusted.
    if (candidate.month != month || candidate.day != day) continue;

    final gap = candidate.difference(today).inDays.abs();
    if (best == null || gap < bestGap) {
      best = candidate;
      bestGap = gap;
    }
  }

  if (best == null || bestGap > _nearestWindowDays) return null;

  return best;
}

/// Every weekday/date pairing in [text] that the calendar contradicts.
///
/// An empty list means the check ran and found nothing, which is a different
/// fact from never having run; the caller keeps them apart.
List<Map<String, Object?>> falseWeekdayClaims(String text, DateTime today) {
  if (text.isEmpty) return const [];

  final findings = <Map<String, Object?>>[];

  void record(String quote, String claimed, DateTime? when) {
    if (when == null) return;
    final actual = weekdayName(when);
    if (actual == claimed) return;
    final iso = '${when.year.toString().padLeft(4, '0')}-'
        '${when.month.toString().padLeft(2, '0')}-'
        '${when.day.toString().padLeft(2, '0')}';
    findings.add({
      'kind': claimWeekdayMismatch,
      'quote': quote.trim(),
      'detail': '$iso is a $actual, not a $claimed',
    });
  }

  for (final m in _dayFirst.allMatches(text)) {
    final month = claimMonths.indexOf(m.group(3)!) + 1;
    record(m.group(0)!, m.group(1)!, _nearest(today, month, int.parse(m.group(2)!)));
  }

  for (final m in _monthFirst.allMatches(text)) {
    final month = claimMonths.indexOf(m.group(2)!) + 1;
    record(m.group(0)!, m.group(1)!, _nearest(today, month, int.parse(m.group(3)!)));
  }

  for (final m in _iso.allMatches(text)) {
    final year = int.parse(m.group(2)!);
    final month = int.parse(m.group(3)!);
    final day = int.parse(m.group(4)!);
    final when = DateTime(year, month, day);
    if (when.month != month || when.day != day) continue;
    record(m.group(0)!, m.group(1)!, when);
  }

  return findings;
}
