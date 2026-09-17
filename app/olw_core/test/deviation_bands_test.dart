// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00

import 'package:olw_core/src/disagreement.dart';
import 'package:olw_core/src/models.dart';
import 'package:test/test.dart';

void main() {
  group('reporting and spending bands', () {
    test('tuning the reporting band can never change what is spent', () {
      // THE SAFETY PROPERTY OF UPSTREAM ITEM 145, and the reason the bands
      // are two things rather than one.
      //
      // `llmShouldReason` buys a judgment call AND a narrative for any member
      // of `observationDisagreements`. A reader who tightens what they want to
      // be TOLD about must not thereby start paying for calls they never asked
      // for — this app's readers are the ones who will not be buying an API
      // key, and nothing on screen would connect the setting to the bill.
      //
      // SWEPT RATHER THAN SAMPLED, against a case sitting near freezing where
      // the old coupling bit hardest. The vectors pin three band settings;
      // this covers the range between and beyond them.
      const standing = StandingCall(tempLowC: -0.5);
      const observed = ObservedSoFar(lowC: 2.0);

      final baseline =
          observationDisagreements(standing, observed, lowIsSettled: true);

      for (var tenth = 1; tenth <= 100; tenth++) {
        final band = tenth / 10;
        final got = observationDisagreements(
          standing,
          observed,
          lowIsSettled: true,
          bands: DeviationBands(lowC: band, lowFreezingC: band),
        );
        expect(got, equals(baseline),
            reason: 'a reporting band of $band C changed what this run SPENDS');
      }
    });

    test('the new bands default to the spend margins, so nothing changed on shipping', () {
      expect(const DeviationBands().highC, tempContradictionMarginC);
      expect(const DeviationBands().onsetMin, onsetContradictionMarginMin);
    });

    test('tuning the high and onset bands can never change what is spent', () {
      const standing = StandingCall(
          rain: false, tempHighC: 30.0, onsetHour: '18:00', tempLowC: -0.5);
      const observed = ObservedSoFar(
          precipitation: true, highC: 32.0, precipitationOnset: '17:00', lowC: 2.0);
      final baseline =
          observationDisagreements(standing, observed, lowIsSettled: true);
      expect(baseline, contains(disagreementHighExceeded));
      for (var tenth = 1; tenth <= 100; tenth++) {
        for (final minutes in [5, 15, 30, 45, 60, 90, 120, 180, 240]) {
          final got = observationDisagreements(
            standing,
            observed,
            lowIsSettled: true,
            bands: DeviationBands(highC: tenth / 10, onsetMin: minutes),
          );
          expect(got, equals(baseline),
              reason: 'high band ${tenth / 10} / onset band $minutes changed '
                  'what this run SPENDS');
        }
      }
    });

    test('the reporting band does change what is reported', () {
      // Without this the test above would pass on a band wired to nothing.
      const standing = StandingCall(tempLowC: 18.2);
      const observed = ObservedSoFar(lowC: 20.0);

      final wide = lowDivergence(standing, observed,
          lowIsSettled: true, bands: const DeviationBands(lowC: 3.0));
      final tight = lowDivergence(standing, observed,
          lowIsSettled: true, bands: const DeviationBands(lowC: 1.0));

      expect(wide!.notable, isFalse, reason: '1.8 C is inside the default');
      expect(tight!.notable, isTrue, reason: 'and outside a tightened band');
      expect(wide.decisive, isFalse);
      expect(tight.decisive, isFalse, reason: 'neither may spend');
    });
  });
}
