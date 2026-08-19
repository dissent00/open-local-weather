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
