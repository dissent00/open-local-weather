import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

void main() {
  group('nextGuidanceSentence', () {
    // Africa/Nairobi, UTC+3 and no DST — the windows that open at
    // 02/08/14/20 UTC land at 05/11/17/23 local.
    const eat = 3 * 3600;

    test('names the next window in the location\'s own local time', () {
      final s = nextGuidanceSentence(
        nowLocal: DateTime(2026, 8, 31, 6, 4),
        utcOffsetSeconds: eat,
      );
      expect(s, contains('usually in by about 11:00 local'));
    });

    test('the last window of the day rolls to tomorrow morning', () {
      final s = nextGuidanceSentence(
        nowLocal: DateTime(2026, 8, 31, 23, 30),
        utcOffsetSeconds: eat,
      );
      expect(s, contains('05:00'));
    });

    test('hedged, never promised', () {
      final s = nextGuidanceSentence(
        nowLocal: DateTime(2026, 8, 31, 6, 4),
        utcOffsetSeconds: eat,
      );
      // Item 50 measured ECMWF's availability varying by more than the hour
      // the windows are rounded to. A notice that names an exact time and is
      // wrong twice teaches the reader to ignore every notice.
      expect(s, contains('usually'));
      expect(s, contains('about'));
    });

    test('says nothing rather than guessing when the offset is unknown', () {
      expect(
        nextGuidanceSentence(
            nowLocal: DateTime(2026, 8, 31, 6, 4), utcOffsetSeconds: null),
        isEmpty,
      );
    });

    test('does not use the device offset', () {
      // The same wall clock in two places is two different instants, and the
      // sentence must follow the LOCATION. A device in UTC reading a UTC+3
      // location must still be told the location's 11:00.
      final atEat = nextGuidanceSentence(
          nowLocal: DateTime(2026, 8, 31, 6, 4), utcOffsetSeconds: eat);
      final atUtc = nextGuidanceSentence(
          nowLocal: DateTime(2026, 8, 31, 6, 4), utcOffsetSeconds: 0);
      expect(atEat, isNot(equals(atUtc)));
      expect(atEat, contains('11:00'));
      expect(atUtc, contains('08:00'));
    });
  });
}
