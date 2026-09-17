import 'dart:convert';
import 'dart:io';

import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// A row this app creates must look exactly like a row the pipeline commits:
/// same keys in the same order, the same stamp forms. The vector proves a
/// committed row survives a round trip; this proves a created one would.
void main() {
  Map<String, Object?> committed() {
    final file = File('../../spec/vectors/run_row.json');
    final cases = (jsonDecode(file.readAsStringSync()) as Map)['cases'] as List;
    return ((cases.first as Map)['input'] as Map).cast<String, Object?>();
  }

  test('a created row has the committed row keys, in order', () {
    final row = RunRecord.create(
      issuedAt: DateTime.utc(2026, 9, 17, 3, 3, 18, 229, 475),
      day0: const [ModelPrediction(model: 'gfs_seamless', rain: true)],
      windowOpenedLocal: DateTime(2026, 9, 17, 6),
    );
    expect(row.toJson().keys.toList(), committed().keys.toList());
    expect(
      (row.toJson()['predictions'] as Map).keys.toList(),
      (committed()['predictions'] as Map).keys.toList(),
    );
  });

  test('stamps take the pipeline forms', () {
    final row = RunRecord.create(
      issuedAt: DateTime.utc(2026, 9, 17, 3, 3, 18, 229, 475),
      day0: const [],
      windowOpenedLocal: DateTime(2026, 9, 17, 6),
    );
    final j = row.toJson();
    expect(j['issued_at'], '2026-09-17T03:03:18.229475Z');
    expect(j['window_opened_local'], '2026-09-17T06:00:00');
    expect(row.issuedAt, DateTime.utc(2026, 9, 17, 3, 3, 18, 229, 475));
    expect(row.windowOpenedLocal, DateTime(2026, 9, 17, 6));
  });

  test('a local issued_at is refused', () {
    expect(
      () => RunRecord.create(issuedAt: DateTime(2026, 9, 17, 6), day0: const []),
      throwsA(isA<AssertionError>()),
    );
  });
}
