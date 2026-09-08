// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
import 'package:test/test.dart';
import 'package:olw_core/src/rounding.dart';

void main() {
  group('roundLikePython', () {
    test('a scaled multiply invents ties that the value does not have', () {
      // -11.95 is stored as -11.94999999999999928, so it is NOT a tie and
      // Python rounds it down. `-11.95 * 10` lands exactly on -119.5 and the
      // manufactured tie then rounds away. This one crossed a band edge and
      // changed "much cooler" into "dramatically cooler".
      expect(roundLikePython(-11.95, 1), -11.9);
      expect(roundLikePython(-17.95, 1), -17.9);
      expect(roundLikePython(11.95, 1), 11.9);
    });

    test('a genuine tie rounds half to EVEN, not away from zero', () {
      // Exact ties at one decimal are the odd quarters, and nothing else.
      expect(roundLikePython(0.25, 1), 0.2);
      expect(roundLikePython(0.75, 1), 0.8);
      expect(roundLikePython(-0.25, 1), -0.2);
      expect(roundLikePython(-59.25, 1), -59.2);
    });

    test('at two decimals the ties are eighths, and quarters are not ties', () {
      // The trap this helper exists to close: copying one place's `* 4` test
      // to two places was measured making things worse, because 27.25 needs
      // no rounding at all at two decimals and the copied test claimed it
      // was a tie.
      expect(roundLikePython(27.25, 2), 27.25);
      expect(roundLikePython(0.125, 2), 0.12);
      expect(roundLikePython(0.375, 2), 0.38);
      expect(roundLikePython(-0.125, 2), -0.12);
    });

    test('ordinary values are unchanged', () {
      expect(roundLikePython(1.24, 1), 1.2);
      expect(roundLikePython(1.26, 1), 1.3);
      expect(roundLikePython(0.0, 2), 0.0);
      expect(roundLikePython(-3.0, 1), -3.0);
    });
  });
}
