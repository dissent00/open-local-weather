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

    test('the baselines are NOT withheld here', () {
      // Their exclusion from the prompt is the pipeline's, applied where the
      // MODEL TRACK RECORD block is built. Duplicating it here would put two
      // files in charge of one rule, and the last time a filter like this was
      // duplicated it leaked in exactly one of the three places it lived.
      expect(modelsVisibleToTheForecaster(), containsAll(baselineModelIds));
    });
  });
}
