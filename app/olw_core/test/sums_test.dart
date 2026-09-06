// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'package:olw_core/src/sums.dart';
import 'package:test/test.dart';

void main() {
  group('compensatedSum', () {
    test('matches the Python mean that a plain reduce got wrong', () {
      // The case that surfaced this, from the weekly_review vectors: five
      // Brier scores of 0.81 followed by five of (0.9 - 1.0)^2. Python's
      // sum()/len gives 0.41000000000000003; `reduce((a, b) => a + b)` gives
      // 0.41, and the vectors compare exactly.
      final low = (0.9 - 1.0) * (0.9 - 1.0);
      final values = [...List.filled(5, 0.81), ...List.filled(5, low)];

      expect(compensatedSum(values) / values.length, equals(0.41000000000000003));
      expect(values.reduce((a, b) => a + b) / values.length, equals(0.41));
    });

    test('compensation survives an addend that dwarfs the rest', () {
      // The shape compensation exists for: a large leading value whose
      // exponent swallows the small ones. Plain summation loses them entirely.
      final values = [1e16, 1.0, 1.0, 1.0, 1.0];

      expect(compensatedSum(values), equals(1.0e16 + 4.0));
      expect(values.reduce((a, b) => a + b), equals(1e16));
    });

    test('an empty sum is zero, not an error', () {
      expect(compensatedSum(const []), equals(0.0));
    });

    test('order does not change the result for equal magnitudes', () {
      final values = [0.1, 0.2, 0.3, 0.4];

      expect(
        compensatedSum(values),
        equals(compensatedSum(values.reversed)),
      );
    });
  });
}
