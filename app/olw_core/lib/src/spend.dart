// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'config.dart';

/// The decision half of the hard spend cap.
///
/// Port of the pure parts of Python's `spend.py`. Storage differs by surface
/// — a committed JSON file in the pipeline, a local database in the app — but
/// the DECISION must not: if one surface counted a window differently from
/// the other, one would permit spending the other refuses, and a cap that can
/// be exceeded on any surface is not a cap. `spec/vectors/spend.json` holds
/// both implementations to identical answers.
///
/// The boundary is the sensitive part. An inclusive cutoff, or a calendar day
/// instead of a rolling window, permits real extra spending while looking
/// entirely correct in review.
///
/// Note what is NOT here: writing. Recording an attempt must happen before
/// the call it accounts for, and that is inherently I/O, so it belongs to
/// each surface's own store. This module only answers "is one more allowed".

/// One recorded call attempt. Written before the attempt, never edited after.
class SpendRecord {
  const SpendRecord({
    required this.at,
    required this.provider,
    required this.model,
    required this.purpose,
  });

  final DateTime at;
  final String provider;
  final String model;

  /// What the call was for, so a ledger read later can tell a forecast from
  /// a health check without cross-referencing timestamps.
  final String purpose;

  Map<String, Object?> toJson() => {
        'at': at.toIso8601String(),
        'provider': provider,
        'model': model,
        'purpose': purpose,
      };

  factory SpendRecord.fromJson(Map<String, Object?> j) => SpendRecord(
        at: DateTime.parse(j['at'] as String),
        provider: (j['provider'] ?? '') as String,
        model: (j['model'] ?? '') as String,
        purpose: (j['purpose'] ?? '') as String,
      );
}

/// How many calls fall inside the rolling window ending at [now].
///
/// Recomputed on every check, never stored — the same rule every other
/// statistic in this project follows. A cached total could drift from the
/// entries it claims to summarise, and would do so silently.
int callsInWindow(
  List<SpendRecord> records,
  DateTime now, {
  Duration window = spendWindow,
}) {
  final cutoff = now.subtract(window);
  // Strictly after: a call exactly `window` old has aged out. Using >= here
  // would keep it counted for one more instant, which is harmless in
  // isolation and exactly the kind of drift that makes two implementations
  // disagree.
  return records.where((r) => r.at.isAfter(cutoff)).length;
}

/// Drops entries far older than the window.
///
/// Keeps a week rather than exactly the window: the extra history costs
/// nothing and makes "what did it spend last Tuesday" answerable, which
/// matters the first time a bill looks wrong.
List<SpendRecord> prune(
  List<SpendRecord> records,
  DateTime now, {
  Duration keep = spendKeepHistory,
}) {
  final cutoff = now.subtract(keep);
  return [for (final r in records) if (r.at.isAfter(cutoff)) r];
}

/// Whether one more call is permitted, and why not if it is not.
class SpendDecision {
  const SpendDecision({
    required this.allowed,
    required this.used,
    required this.maxCalls,
    this.capacityReturnsAt,
  });

  final bool allowed;
  final int used;
  final int maxCalls;

  /// When the oldest in-window call ages out, freeing one slot.
  ///
  /// A bare "limit reached" leaves someone guessing whether to wait ten
  /// minutes or raise the cap; this makes that answerable.
  final DateTime? capacityReturnsAt;

  int get remaining => maxCalls - used < 0 ? 0 : maxCalls - used;
}

SpendDecision evaluateCap(
  List<SpendRecord> records,
  DateTime now, {
  required int maxCalls,
  Duration window = spendWindow,
}) {
  final used = callsInWindow(records, now, window: window);
  if (used < maxCalls) {
    return SpendDecision(allowed: true, used: used, maxCalls: maxCalls);
  }
  final cutoff = now.subtract(window);
  final inWindow = records.where((r) => r.at.isAfter(cutoff)).toList()
    ..sort((a, b) => a.at.compareTo(b.at));
  return SpendDecision(
    allowed: false,
    used: used,
    maxCalls: maxCalls,
    capacityReturnsAt:
        inWindow.isEmpty ? now : inWindow.first.at.add(window),
  );
}
