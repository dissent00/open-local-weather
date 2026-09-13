// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00

/// Does this issuance earn an LLM call?
///
/// Upstream ROADMAP item 121, and the half of item 120 that survived it. The
/// pipeline's first principle is that facts are composed in code and never
/// asked of the model; item 121 applied it to observations, which removed the
/// commonest reason a frequent refresh had to spend anything. What is left is
/// this question, and it is the operator's:
///
///     "So I could run cron every hour, I'd get sensor updates from code as
///     we're now discussing, and only hit my LLM API if there's new model
///     data. Same for the app user who refreshes hourly."
///
/// THE SIGNALS ARE NOT COMPUTED HERE. `InformationMoved` carries all three of
/// C2's triggers and is recorded on every entry; this reads them.
///
/// NOTHING IN THIS PACKAGE CALLS IT YET, AND THAT IS DELIBERATE — the app has
/// no refresh loop to gate. It is ported now because `spec/vectors` pins it
/// and a contract nobody reads is a contract that rots; upstream item 121's
/// second half, the app's station source and its refresh path, is what wires
/// it. NOTE THE SHAPE OF THE RISK, which this repo has paid for once:
/// `describeWindShift` sat here exported and vector-tested with no caller and
/// every forecast went out without it (fixed in b68570d). The reason that is
/// survivable here and was not there is that this function DECIDES rather than
/// COMPOSES — an uncalled composer silently removes content from output, while
/// an uncalled gate leaves the app doing what it does today, which is to reason
/// on every refresh. When the caller lands, prove the gate is reached on the
/// app's real path rather than that it compiles.
library;

import 'models.dart';

/// When a LATER issuance of a day may spend an LLM call.
///
/// A day's first issuance is outside this entirely — see [llmShouldReason].
enum LLMRefreshPolicy {
  /// Every issuance re-reasons, which is what every run did before item 121.
  always('always'),

  /// Chase model runs only. Observations still refresh every run, in code and
  /// for free; the prose stays the prose of the last real forecast.
  newCycleOnly('new_cycle_only'),

  /// Adds C2's third trigger: an observation CONTRADICTING the standing call
  /// also earns a re-forecast.
  ///
  /// NOT THE DEFAULT, and the reason is a measurement that has not been taken
  /// — the contradiction test's temperature margin is upstream-commented
  /// "CONSERVATIVE AND NOT YET MEASURED". Defaulting to it would ship an
  /// unmeasured threshold into a spending decision.
  newCycleOrContradiction('new_cycle_or_contradiction');

  const LLMRefreshPolicy(this.wireName);

  /// The spelling in `location.yaml`, the app's settings and the vectors.
  /// Kept explicit rather than derived from the enum name, because the wire
  /// format is snake_case and Dart's is camelCase — deriving it would make a
  /// rename here a silent config-compatibility break.
  final String wireName;

  static LLMRefreshPolicy fromWireName(String name) => values.firstWhere(
        (p) => p.wireName == name,
        orElse: () => throw ArgumentError('unknown LLM refresh policy: $name'),
      );
}

/// Whether this issuance re-reasons, or refreshes its observations and stops.
///
/// Returning false does not mean the run does nothing: it fetches, composes
/// what the station has seen, and re-renders. It means only that no judgment
/// and no narrative are bought.
bool llmShouldReason(InformationMoved moved, LLMRefreshPolicy policy) {
  // The day has no forecast at all yet, so there is nothing to preserve and
  // nothing to compare against. C2's first trigger, and not subject to policy
  // — a deployment that switched it off would publish observations and no
  // forecast.
  if (moved.firstIssuanceOfDay) {
    return true;
  }

  if (policy == LLMRefreshPolicy.always) {
    return true;
  }

  // THREE-VALUED, AND null IS NOT false. `guidanceIsNewer` is null when there
  // was no BASIS for the comparison — an entry written before the cycle was
  // recorded, or a run that fell back to the derived floor while the previous
  // one had a real observation, which means this run knows LESS than its
  // predecessor did.
  //
  // No basis resolves toward spending. Taking the cheap path is an
  // optimisation and an optimisation needs positive evidence that nothing
  // moved; without it, do what every run did before this gate existed.
  if (moved.guidanceIsNewer != false) {
    return true;
  }

  if (policy == LLMRefreshPolicy.newCycleOrContradiction &&
      (moved.observationDisagreements?.isNotEmpty ?? false)) {
    return true;
  }

  return false;
}
