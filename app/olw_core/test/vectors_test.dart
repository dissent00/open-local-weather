/// Holds this Dart port to the exact behaviour of the Python implementation,
/// using the shared vectors in `spec/vectors/`.
///
/// This is the whole point of the port's test strategy. The credibility of
/// the project rests on deterministic accuracy statistics; a divergence
/// between the two implementations would not crash, it would silently
/// produce WRONG numbers that then feed the LLM as its "track record".
/// These vectors make that failure mode a red test instead of an invisible
/// one. See spec/README.md.
///
/// Direction matters: if a case here fails and the Dart behaviour is the one
/// you want, the change must be made in PYTHON first and the vectors
/// regenerated — never by editing the vectors to match Dart.
library;

import 'dart:convert';
import 'dart:io';

import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// spec/ lives at the repo root, two levels up from this package.
final vectorsDir = Directory('../../spec/vectors');

Map<String, Object?> loadVectors(String name) {
  final file = File('${vectorsDir.path}/$name');
  if (!file.existsSync()) {
    fail('vector file not found: ${file.path} (run `python spec/export_vectors.py`)');
  }
  return jsonDecode(file.readAsStringSync()) as Map<String, Object?>;
}

List<Map<String, Object?>> casesOf(String name) =>
    (loadVectors(name)['cases'] as List).cast<Map<String, Object?>>();

/// Structural comparison that treats numbers as equal within a tolerance.
///
/// Necessary because JSON has one number type and Dart has two: Python's
/// `26.0` may round-trip as `26` or `26.0`, and floating-point division in
/// two languages can differ in the last bit. Everything non-numeric is
/// compared exactly.
bool deepMatches(Object? actual, Object? expected, {double eps = 1e-9}) {
  if (expected == null || actual == null) return actual == expected;
  if (expected is num && actual is num) {
    return (actual.toDouble() - expected.toDouble()).abs() <= eps;
  }
  if (expected is List) {
    if (actual is! List || actual.length != expected.length) return false;
    for (var i = 0; i < expected.length; i++) {
      if (!deepMatches(actual[i], expected[i], eps: eps)) return false;
    }
    return true;
  }
  if (expected is Map) {
    if (actual is! Map) return false;
    // Compare on the expected key set: the vector defines the contract.
    for (final key in expected.keys) {
      if (!actual.containsKey(key)) return false;
      if (!deepMatches(actual[key], expected[key], eps: eps)) return false;
    }
    return true;
  }
  return actual == expected;
}

void expectMatches(Object? actual, Object? expected, String caseName) {
  expect(
    deepMatches(actual, expected),
    isTrue,
    reason: 'vector case "$caseName"\n  expected: ${jsonEncode(expected)}\n'
        '  actual:   ${jsonEncode(actual)}',
  );
}

void main() {
  group('dates', () {
    test('prediction_row_date_for_target', () {
      for (final c in casesOf('dates.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = predictionRowDateForTarget(
          parseDate(i['target_date'] as String),
          i['lead_time_days'] as int,
        );
        expectMatches(formatDate(got), c['expected'], c['name'] as String);
      }
    });

    test('add_days', () {
      for (final c in casesOf('dates_add_days.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = addDays(parseDate(i['date'] as String), i['days'] as int);
        expectMatches(formatDate(got), c['expected'], c['name'] as String);
      }
    });
  });

  group('scoring', () {
    test('score_prediction', () {
      for (final c in casesOf('scoring_score_prediction.json')) {
        final i = c['input'] as Map<String, Object?>;
        final predicted = i['predicted'] == null
            ? null
            : ModelPrediction.fromJson(i['predicted'] as Map<String, Object?>);
        final actual = i['actual'] == null
            ? null
            : DailyActual.fromJson(i['actual'] as Map<String, Object?>);
        final got =
            scorePrediction(predicted, actual, i['lead_time_days'] as int);
        expectMatches(got?.toJson(), c['expected'], c['name'] as String);
      }
    });

    test('mean', () {
      for (final c in casesOf('scoring_mean.json')) {
        final values = ((c['input'] as Map)['values'] as List)
            .map((e) => e == null ? null : (e as num).toDouble())
            .toList();
        expectMatches(mean(values), c['expected'], c['name'] as String);
      }
    });

    test('compute_rain_pct_trend', () {
      for (final c in casesOf('scoring_rain_pct_trend.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = computeRainPctTrend(
          rolling10RainPct: (i['rolling_10_rain_pct'] as num?)?.toDouble(),
          rolling30RainPct: (i['rolling_30_rain_pct'] as num?)?.toDouble(),
          checksInWindow10: i['checks_in_window_10'] as int,
          checksInWindow30: i['checks_in_window_30'] as int,
          minChecksShort: i['min_checks_short'] as int,
          minChecksLong: i['min_checks_long'] as int,
          thresholdPct: (i['threshold_pct'] as num).toDouble(),
        );
        expectMatches(
          {'label': got.label, 'delta': got.delta},
          c['expected'],
          c['name'] as String,
        );
      }
    });
  });

  group('extraction', () {
    test('extract_day0_predictions_from_hourly', () {
      for (final c in casesOf('extract_day0.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = extractDay0PredictionsFromHourly(
          i['hourly_multi_model'] as Map<String, Object?>,
          (i['models'] as List).cast<String>(),
          threshold: (i['threshold'] as num).toDouble(),
        );
        expectMatches(
          got.map((p) => p.toJson()).toList(),
          c['expected'],
          c['name'] as String,
        );
      }
    });

    test('extract_day_n_predictions_from_daily', () {
      for (final c in casesOf('extract_day_n.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = extractDayNPredictionsFromDaily(
          i['daily_multi_model'] as Map<String, Object?>,
          i['day_index'] as int,
          (i['models'] as List).cast<String>(),
          threshold: (i['threshold'] as num).toDouble(),
        );
        expectMatches(
          got.map((p) => p.toJson()).toList(),
          c['expected'],
          c['name'] as String,
        );
      }
    });

    test('get_onset_hour', () {
      for (final c in casesOf('extract_onset_hour.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = getOnsetHour(
          (i['times'] as List).cast<String>(),
          (i['precip'] as List)
              .map((e) => e == null ? null : (e as num).toDouble())
              .toList(),
          threshold: (i['threshold'] as num).toDouble(),
        );
        expectMatches(got, c['expected'], c['name'] as String);
      }
    });
  });

  group('ground aqi', () {
    test('hours_old / is_stale', () {
      for (final c in casesOf('aqi_staleness.json')) {
        final i = c['input'] as Map<String, Object?>;
        final reading =
            GroundAqiReading.fromJson(i['reading'] as Map<String, Object?>);
        final now = DateTime.parse(i['now'] as String);
        expectMatches(
          {'hours_old': hoursOld(reading, now), 'is_stale': isStale(reading, now)},
          c['expected'],
          c['name'] as String,
        );
      }
    });

    test('summarize_ground_aqi', () {
      for (final c in casesOf('aqi_summary.json')) {
        final i = c['input'] as Map<String, Object?>;
        final readings = (i['readings'] as List)
            .map((r) => GroundAqiReading.fromJson(r as Map<String, Object?>))
            .toList();
        final got = summarizeGroundAqi(readings, DateTime.parse(i['now'] as String));
        expectMatches(got?.toJson(), c['expected'], c['name'] as String);
      }
    });
  });

  group('bucketing', () {
    test('bucket_hourly_by_date', () {
      for (final c in casesOf('bucket_hourly_by_date.json')) {
        final i = c['input'] as Map<String, Object?>;
        final result = bucketHourlyByDate(
          i['hourly_json'] as Map<String, Object?>,
          threshold: (i['threshold'] as num).toDouble(),
        );
        final got = <String, Object?>{
          for (final e in result.entries) formatDate(e.key): e.value.toJson(),
        };
        expectMatches(got, c['expected'], c['name'] as String);
      }
    });
  });

  group('llm schema dialects', () {
    // Compared STRICTLY (both directions, exact), unlike the other vector
    // checks: these maps are sent verbatim to real provider APIs, so an
    // extra or missing key is a wire-level difference, not a cosmetic one.
    test('gemini responseSchema dialect matches Python exactly', () {
      final expected = casesOf('llm_schema_gemini.json').single['expected'];
      expect(geminiForecastSchema(), equals(expected));
    });

    test('strict JSON Schema dialect matches Python exactly', () {
      final expected = casesOf('llm_schema_strict.json').single['expected'];
      expect(strictForecastSchema(), equals(expected));
    });
  });

  test('every vector file on disk is exercised', () {
    // Mirrors test_every_vector_file_is_exercised on the Python side: a
    // vector file nobody reads is a contract nobody checks.
    const covered = {
      'dates.json',
      'dates_add_days.json',
      'scoring_score_prediction.json',
      'scoring_mean.json',
      'scoring_rain_pct_trend.json',
      'extract_day0.json',
      'extract_day_n.json',
      'extract_onset_hour.json',
      'aqi_staleness.json',
      'aqi_summary.json',
      'bucket_hourly_by_date.json',
      'llm_schema_gemini.json',
      'llm_schema_strict.json',
    };
    final onDisk = vectorsDir
        .listSync()
        .whereType<File>()
        .map((f) => f.uri.pathSegments.last)
        .where((n) => n.endsWith('.json'))
        .toSet();
    expect(onDisk, equals(covered),
        reason: 'a vector file exists that no Dart test reads (or vice versa)');
  });
}
