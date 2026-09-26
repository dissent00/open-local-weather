// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The code blend — upstream ROADMAP item 173. The vector pins agreement
/// with Python; these pin the properties a vector case cannot state.
library;

import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

RollingWindowResult _window({int checks = 30, double? rainPct, double? highErr}) =>
    RollingWindowResult(
      checksFound: checks,
      rainPct: rainPct,
      onsetErr: null,
      windErr: null,
      highErr: highErr,
      lowErr: null,
      mslpErr: null,
    );

void main() {
  test('a model that came in cold is corrected upward', () {
    // THE SIGN. Errors are actual minus predicted, so +1.3 is ADDED.
    final (highs, _) = temperatureCorrections({
      'ecmwf_ifs025': _window(checks: 10, highErr: 1.3),
    });
    final blend = codeBlendPrediction(
      const [ModelPrediction(model: 'ecmwf_ifs025', rain: false, highC: 30.0)],
      {'ecmwf_ifs025': 30.0},
      highs,
    );

    expect(blend!.highC, 31.3);
  });

  test('the record as of an issuance cannot see its day', () {
    // On a POPULATED record: a guard asserted against an empty window passes
    // whatever the code does.
    final issued = DateTime.utc(2026, 9, 20);
    final wrongFrom = <DateTime>{};
    List<ModelPrediction>? predictionsFor(DateTime rowDate, int lead) => [
          ModelPrediction(model: 'gfs_seamless', rain: !wrongFrom.contains(rowDate)),
        ];
    DailyActual? actualFor(DateTime d) =>
        d.isBefore(addDays(issued, -12)) ? null : DailyActual(rain: true);

    Map<String, RollingWindowResult> asOf() => windowsAsOf(
          models: const ['gfs_seamless'],
          leadTimeDays: 0,
          windowSize: 30,
          issued: issued,
          predictionsFor: predictionsFor,
          actualFor: actualFor,
        );

    final before = asOf()['gfs_seamless']!;
    expect(before.checksFound, 12);
    expect(before.rainPct, 100.0);

    // Every call from the issuance on turns wrong.
    for (var i = 0; i < 5; i++) {
      wrongFrom.add(addDays(issued, i));
    }
    final after = asOf()['gfs_seamless']!;
    expect(after.checksFound, before.checksFound);
    expect(after.rainPct, before.rainPct);
  });
}
