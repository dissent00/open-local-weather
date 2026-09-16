// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// Which models carry a tracked skill record — the Dart counterpart of the
/// `scored_models` half of Python's defaults.
///
/// Nothing covered this list until 2026-09-05, which is how it came to omit
/// the two baselines for the whole time Python's `scored_models` carried
/// them. The divergence never threw and never failed a vector: the app stored
/// baseline predictions and then verified against a list that did not name
/// them, so they were written every day, scored never, and absent from the
/// accuracy screen — the one surface whose entire purpose is to say what a
/// percentage is worth.
library;

import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

void main() {
  group('scoredModels', () {
    test('carries the models, the blend and both baselines', () {
      final scored = scoredModels();

      expect(scored, containsAll(defaultModels));
      expect(scored, contains(blendModelId));
      expect(scored, containsAll(baselineModelIds));
    });

    test('the baseline ids agree with the functions that produce them', () {
      // config.dart keeps its own copy so it owes baselines.dart no
      // dependency — defaults.py does the same, for the same reason, and the
      // Python suite asserts the same agreement over there.
      expect(baselineModelIds, [persistenceModelId, climatologyModelId]);
    });

    test('a local met service joins only when one is configured', () {
      expect(scoredModels(), isNot(contains('kenya_met')));
      expect(
        scoredModels(localBulletinModelId: 'kenya_met'),
        contains('kenya_met'),
      );
    });
  });

  group('modelsVisibleToTheForecaster', () {
    test('is everything scored except the forecaster own call', () {
      // The blend is withheld deliberately and permanently — see the doc
      // comment on modelsVisibleToTheForecaster for why this is a standing
      // rule and not an oversight to be tidied up later.
      final visible = modelsVisibleToTheForecaster();

      expect(visible, isNot(contains(blendModelId)));
      expect(visible, containsAll(defaultModels));
    });

    test('the baselines are withheld too, exactly as the Python is', () {
      // This test used to assert the OPPOSITE, on the stated grounds that
      // the baseline exclusion "is the pipeline's, applied where the MODEL
      // TRACK RECORD block is built". That was wrong when it was written
      // (2026-09-05): `models_visible_to_the_forecaster` in defaults.py had
      // hidden the baselines since 2026-08-31, and the pipeline passes ITS
      // result everywhere the forecaster's model set is needed — the track
      // record, the verification scores and the long-run review. A port
      // that leaves them in would build the app's review over a different
      // model set from the server's, and the review is what `ensemble` item
      // 12 is about to put in the app's prompt.
      final visible = modelsVisibleToTheForecaster();
      for (final id in baselineModelIds) {
        expect(visible, isNot(contains(id)));
      }
    });
  });

  group('lightning is its own variable', () {
    // ROADMAP item 65. "Did it storm" and "did it rain" are different
    // questions with different answers, and the ledger has had one column for
    // both. Pinned here because the field reads as an obvious omission from
    // observedConvection() to anyone who finds it without the reasoning.

    test('three-valued, and starts unasked', () {
      expect(const DailyActual(rain: false).lightning, isNull);
      expect(const DailyActual(rain: false, lightning: false).lightning, isFalse);
      expect(const DailyActual(rain: false, lightning: true).lightning, isTrue);
    });

    test('does not make a day wet', () {
      // observedConvection is an OR, so every term added to it can only
      // CREATE wet days and can only move the rain rate.
      const dryStorm = DailyActual(rain: false, lightning: true);

      expect(dryStorm.lightning, isTrue);
      expect(dryStorm.observedConvection(), isFalse,
          reason: 'lightning must not join the OR — ROADMAP item 65');
    });

    test('the existing convection terms are untouched', () {
      expect(const DailyActual(rain: true).observedConvection(), isTrue);
      expect(const DailyActual(rain: false, thunder: true).observedConvection(), isTrue);
      expect(
          const DailyActual(rain: false, precipitation: true).observedConvection(), isTrue);
      expect(const DailyActual(rain: false).observedConvection(), isFalse);
    });

    test('survives a round trip', () {
      final stored = const DailyActual(rain: false, lightning: true).toJson();

      expect(stored['lightning'], isTrue);
      expect(DailyActual.fromJson(stored).lightning, isTrue);
    });
  });
}
