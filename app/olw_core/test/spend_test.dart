// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// `evaluateCap`'s own rules.
///
/// The cap's behaviour across the two implementations is pinned by the S1-S6
/// table in `spec/README.md` and exercised end-to-end in spend_seam_test.dart.
/// What is here is the one question those do not ask: whether the work about
/// to start FITS in what is left.

SpendRecord _call(DateTime at) =>
    SpendRecord(at: at, provider: 'gemini', model: 'm', purpose: 'forecast');

void main() {
  final now = DateTime.utc(2026, 9, 11, 12);

  group('a cap must fit the whole job', () {
    // Upstream ROADMAP item 59 step 3. The cap is enforced per REQUEST, so
    // before `callsNeeded` a caller with one slot left could start a two-call
    // forecast: the first call was made and paid for and the second refused
    // mid-flight, charging for half a forecast that is worth nothing.
    test('work costing two calls is refused with only one slot left', () {
      final used = [_call(now.subtract(const Duration(minutes: 5)))];

      expect(evaluateCap(used, now, maxCalls: 2).allowed, isTrue,
          reason: 'one more single call still fits');
      expect(evaluateCap(used, now, maxCalls: 2, callsNeeded: 2).allowed,
          isFalse,
          reason: 'a two-call forecast does not');
    });

    test('the default is one, so existing callers are unchanged', () {
      expect(evaluateCap(const [], now, maxCalls: 1).allowed, isTrue);
      expect(evaluateCap(const [], now, maxCalls: 1, callsNeeded: 2).allowed,
          isFalse);
    });

    test('an exact fit is allowed, because a cap is a limit not a margin', () {
      expect(evaluateCap(const [], now, maxCalls: 2, callsNeeded: 2).allowed,
          isTrue);
    });

    test('a refusal still says when capacity returns', () {
      // The reason someone is refused changed; what they need to know did
      // not. A bare "limit reached" leaves them guessing whether to wait ten
      // minutes or raise the cap.
      final used = [_call(now.subtract(const Duration(hours: 3)))];
      final decision = evaluateCap(used, now, maxCalls: 2, callsNeeded: 2);

      expect(decision.allowed, isFalse);
      expect(decision.capacityReturnsAt,
          equals(now.subtract(const Duration(hours: 3)).add(spendWindow)));
    });
  });
}
