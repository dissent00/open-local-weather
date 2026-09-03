import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

void main() {
  group('brierScore', () {
    test('a confident correct call scores zero', () {
      expect(brierScore(1.0, true), 0.0);
      expect(brierScore(0.0, false), 0.0);
    });

    test('a confident wrong call scores the worst possible', () {
      expect(brierScore(1.0, false), 1.0);
      expect(brierScore(0.0, true), 1.0);
    });

    test('hedging beats being confidently wrong', () {
      // The incentive the item exists to create. An implementation with the
      // sign backwards would still pass a test that only checked magnitudes.
      expect(brierScore(0.7, false), lessThan(brierScore(1.0, false)));
      expect(brierScore(0.3, true), lessThan(brierScore(0.0, true)));
    });

    test('a coin flip costs the same either way', () {
      expect(brierScore(0.5, true), 0.25);
      expect(brierScore(0.5, false), 0.25);
    });

    test('a percentage is rejected rather than silently misread', () {
      // 70 taken as a probability scores 4761 — a number that would pass
      // through every aggregation and poison the column.
      expect(() => brierScore(70, true), throwsArgumentError);
      expect(() => brierScore(-0.1, true), throwsArgumentError);
    });
  });

  group('aggregation', () {
    test('the mean skips entries with no score', () {
      expect(meanBrier([0.0, null, 1.0]), 0.5);
      expect(meanBrier([null, null]), isNull);
      expect(meanBrier([]), isNull);
    });
  });

  group('brierSkillScore', () {
    test('zero when a forecast only matches the reference', () {
      expect(brierSkillScore(0.2, 0.2), 0.0);
    });

    test('one for a perfect forecast', () {
      expect(brierSkillScore(0.0, 0.25), 1.0);
    });

    test('NEGATIVE when worse than the reference, and not clamped', () {
      expect(brierSkillScore(0.4, 0.25), closeTo(-0.6, 1e-9));
    });

    test('undefined against a perfect reference, not infinite', () {
      expect(brierSkillScore(0.1, 0.0), isNull);
      expect(brierSkillScore(0.1, null), isNull);
      expect(brierSkillScore(null, 0.25), isNull);
    });
  });
}
