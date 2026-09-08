// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Date arithmetic, isolated for the same reason as the Python `dates.py`:
/// off-by-one lead-time math is exactly the kind of thing that is trivial to
/// get subtly wrong and expensive to notice.

/// Calendar day arithmetic on a date-only value.
///
/// Uses UTC internally on purpose. Dart's local-time `DateTime` arithmetic
/// crosses DST boundaries by adding 24h of *elapsed* time, which can land on
/// the same calendar day twice a year — a bug that would silently misalign
/// verification for users in DST-observing timezones.
DateTime addDays(DateTime d, int n) {
  final utc = DateTime.utc(d.year, d.month, d.day);
  return DateTime.utc(utc.year, utc.month, utc.day + n);
}

/// The date of the log entry that MADE a prediction targeting [targetDate]
/// at the given lead time.
///
/// A prediction made on D targets D+k, so the row that made a k-lead
/// prediction FOR targetDate is dated (targetDate − k). If lead-time
/// verification ever looks shifted, this is the first place to check.
DateTime predictionRowDateForTarget(DateTime targetDate, int leadTimeDays) =>
    addDays(targetDate, -leadTimeDays);

/// `YYYY-MM-DD`, matching the Python `DATE_FMT`.
String formatDate(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

DateTime parseDate(String s) {
  final parts = s.split('-').map(int.parse).toList();
  return DateTime.utc(parts[0], parts[1], parts[2]);
}

/// Day names, Monday-first to match Dart's `DateTime.weekday` (1 = Monday).
///
/// A fixed English list rather than anything locale-aware, because the Python
/// side is `strftime("%A")` under the C locale and the two must agree
/// character for character — the name reaches the forecast inside a phrase the
/// prompt uses VERBATIM, so a localised name here would silently produce a
/// different sentence on a device with a different locale.
const _weekdayNames = <String>[
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

/// "Friday". Computed from a date already resolved in the LOCATION's timezone
/// by the caller — never from a device clock.
///
/// A day name is arithmetic, and this is the kind that silently reads a day
/// early: a run at 03:00 UTC is already the next day in Kisumu, so a name
/// derived from the device's own `DateTime.now()` would be yesterday's.
String weekdayName(DateTime d) =>
    _weekdayNames[DateTime.utc(d.year, d.month, d.day).weekday - 1];
