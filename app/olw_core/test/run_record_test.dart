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

  /// The row as the MODEL emits it, which is what this app has to match.
  ///
  /// `committed()` above is a historical fixture, copied from
  /// data/log/2026-09-16.json and deliberately never regenerated — the
  /// exporter's own comment explains why, and it is a good reason. But that
  /// makes it the wrong thing to compare a key LIST against: every additive
  /// field the pipeline gains is absent from it by design, and comparing
  /// would turn CI red on exactly the change the fixture exists to tolerate.
  /// `expected` is the same case after `model_dump`, so it carries today's
  /// shape. Split out 2026-09-21 when `secondary_predictions` arrived.
  Map<String, Object?> emitted() {
    final file = File('../../spec/vectors/run_row.json');
    final cases = (jsonDecode(file.readAsStringSync()) as Map)['cases'] as List;
    return ((cases.first as Map)['expected'] as Map).cast<String, Object?>();
  }

  test('the committed shape only ever grows', () {
    // What `committed()` is actually for, once it stopped being the key-order
    // reference. The 2026-09-16 fixture is a real row this app has to keep
    // reading, so every key it carries must still exist today and in the same
    // relative order — an additive change is fine and a rename or a removal
    // is not, because the app reads rows the server wrote months ago.
    final old = committed().keys.toList();
    final now = emitted().keys.toList();
    expect(now, containsAll(old));
    expect(
      [for (final k in now) if (old.contains(k)) k],
      old,
      reason: 'a committed key was reordered relative to the others',
    );
  });

  test('a created row has the committed row keys, in order', () {
    final row = RunRecord.create(
      issuedAt: DateTime.utc(2026, 9, 17, 3, 3, 18, 229, 475),
      day0: const [ModelPrediction(model: 'gfs_seamless', rain: true)],
      windowOpenedLocal: DateTime(2026, 9, 17, 6),
    );
    expect(row.toJson().keys.toList(), emitted().keys.toList());
    expect(
      (row.toJson()['predictions'] as Map).keys.toList(),
      (emitted()['predictions'] as Map).keys.toList(),
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
