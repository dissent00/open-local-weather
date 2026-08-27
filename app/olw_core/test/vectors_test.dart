// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
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

  group('system prompt', () {
    // Compared VERBATIM. This string is the instruction set behind every
    // forecast; a drift here means the app and the pipeline reason
    // differently from identical data.
    test('matches Python character for character across all branches', () {
      for (final c in casesOf('llm_system_prompt.json')) {
        final i = c['input'] as Map<String, Object?>;
        final loc = i['location'] as Map<String, Object?>;
        final sec = loc['secondary_point'] as Map<String, Object?>;
        final got = buildSystemPrompt(
          LocationConfig(
            regionName: loc['region_name'] as String,
            primaryPlaceName: loc['primary_place_name'] as String,
            timezone: 'UTC',
            lat: 0,
            lon: 0,
            secondaryPoint: SecondaryPoint(
              enabled: sec['enabled'] as bool,
              name: sec['name'] as String,
              sectionLabel: sec['section_label'] as String,
            ),
          ),
          historicalLookbackDaysArg: i['historical_lookback_days'] as int,
          rollingWindowShortArg: i['rolling_window_short'] as int,
          rollingWindowLongArg: i['rolling_window_long'] as int,
          isReissue: i['is_reissue'] as bool,
        );
        expect(got, equals(c['expected']), reason: 'case "${c['name']}"');
      }
    });
  });

  group('verification', () {
    // The credibility of the whole project. An app whose accuracy screen
    // disagreed with the site's would discredit both, and a user comparing
    // them could not tell which was right.
    test('produces identical scores and track record to Python', () {
      for (final c in casesOf('verification.json')) {
        final i = c['input'] as Map<String, Object?>;
        final predictions = (i['predictions'] as Map).cast<String, Object?>();
        final actuals = (i['actuals'] as Map).cast<String, Object?>();

        List<ModelPrediction>? predictionsFor(DateTime rowDate, int lead) {
          final byLead = predictions[formatDate(rowDate)] as Map<String, Object?>?;
          final raw = byLead?['$lead'] as List?;
          if (raw == null) return null;
          return raw
              .map((e) => ModelPrediction.fromJson((e as Map).cast<String, Object?>()))
              .toList();
        }

        DailyActual? actualFor(DateTime target) {
          final raw = actuals[formatDate(target)] as Map?;
          if (raw == null) return null;
          return DailyActual.fromJson(raw.cast<String, Object?>());
        }

        final prior = [
          for (final e in (i['prior_track_record'] as List).cast<Map<String, Object?>>())
            TrackRecordEntry(
              model: e['model'] as String,
              leadTimeDays: e['lead_time_days'] as int,
              allTimeChecks: e['all_time_checks'] as int,
              allTimeCorrect: e['all_time_correct'] as int,
              allTimeRainPct: (e['all_time_rain_pct'] as num?)?.toDouble(),
            )
        ];

        final result = runVerification(
          predictionsFor: predictionsFor,
          actualFor: actualFor,
          priorTrackRecord: prior,
          today: DateTime.parse(i['today'] as String),
          yesterday: DateTime.parse(i['yesterday'] as String),
          earliestRecordDate: DateTime.parse(i['earliest_record_date'] as String),
          models: (i['models'] as List).cast<String>(),
          leadTimesDays: (i['lead_times_days'] as List).cast<int>(),
        );

        final want = c['expected'] as Map<String, Object?>;
        final reason = 'case "${c['name']}"';

        final wantLeads = (want['lead_time_results'] as List).cast<Map<String, Object?>>();
        expect(result.leadTimeResults.length, wantLeads.length, reason: reason);
        for (var n = 0; n < wantLeads.length; n++) {
          final got = result.leadTimeResults[n];
          expect(got.leadTimeDays, wantLeads[n]['lead_time_days'], reason: reason);
          expect(
            got.targetDateVerified == null ? null : formatDate(got.targetDateVerified!),
            equals(wantLeads[n]['target_date_verified']),
            reason: reason,
          );
          // A model with no data must be ABSENT from the scores, not present
          // with a wrong answer — the distinction that stops absence
          // accruing fake accuracy.
          expect((got.perModelScores.keys.toList()..sort()),
              equals((wantLeads[n]['scored_models'] as List).cast<String>()),
              reason: reason);
        }

        expect(
          result.newlyVerified
              .map((e) => [formatDate(e.key), e.value])
              .toList(),
          equals((want['newly_verified'] as List)
              .map((e) => [(e as List)[0], e[1]])
              .toList()),
          reason: reason,
        );

        final wantTrack = (want['track_record'] as List).cast<Map<String, Object?>>();
        expect(result.trackRecord.length, wantTrack.length, reason: reason);
        for (var n = 0; n < wantTrack.length; n++) {
          final got = result.trackRecord[n];
          final w = wantTrack[n];
          expect(got.model, w['model'], reason: reason);
          expect(got.leadTimeDays, w['lead_time_days'], reason: reason);
          expect(got.rolling10RainPct, w['rolling_10_rain_pct'], reason: reason);
          expect(got.rolling30RainPct, w['rolling_30_rain_pct'], reason: reason);
          expect(got.rainPctTrend, w['rain_pct_trend'], reason: reason);
          expect(got.allTimeChecks, w['all_time_checks'], reason: reason);
          expect(got.allTimeCorrect, w['all_time_correct'], reason: reason);
          expect(got.allTimeRainPct, w['all_time_rain_pct'], reason: reason);
          expect(got.checksInWindow10, w['checks_in_window_10'], reason: reason);
          expect(got.avgTempHighErrorC10, w['avg_temp_high_error_c_10'], reason: reason);
          expect(got.avgOnsetErrorHrs10, w['avg_onset_error_hrs_10'], reason: reason);
        }
      }
    });

    test('a shrinking all-time derivation warns rather than publishing it', () {
      // Fewer checks than last time means observations are missing for dates
      // we hold predictions for. Quietly publishing the smaller number would
      // erase real history and look like the models got worse.
      final cases = casesOf('verification.json');
      final shrink = cases.firstWhere(
          (c) => (c['name'] as String).contains('shrinking'));
      final i = shrink['input'] as Map<String, Object?>;
      final predictions = (i['predictions'] as Map).cast<String, Object?>();
      final actuals = (i['actuals'] as Map).cast<String, Object?>();

      final result = runVerification(
        predictionsFor: (d, lead) {
          final byLead = predictions[formatDate(d)] as Map<String, Object?>?;
          final raw = byLead?['$lead'] as List?;
          if (raw == null) return null;
          return raw
              .map((e) => ModelPrediction.fromJson((e as Map).cast<String, Object?>()))
              .toList();
        },
        actualFor: (d) {
          final raw = actuals[formatDate(d)] as Map?;
          return raw == null ? null : DailyActual.fromJson(raw.cast<String, Object?>());
        },
        priorTrackRecord: [
          TrackRecordEntry(
              model: 'alpha',
              leadTimeDays: 0,
              allTimeChecks: 99,
              allTimeCorrect: 90,
              allTimeRainPct: 90.9),
          TrackRecordEntry(model: 'beta', leadTimeDays: 0),
        ],
        today: DateTime.parse(i['today'] as String),
        yesterday: DateTime.parse(i['yesterday'] as String),
        earliestRecordDate: DateTime.parse(i['earliest_record_date'] as String),
        models: const ['alpha', 'beta'],
        leadTimesDays: const [0],
      );

      expect(result.warnings, hasLength(1));
      expect(result.warnings.single, contains('alpha'));
      expect(result.warnings.single, contains('99'));
      // Returned rather than printed, so each surface decides how to show it.
      expect(result.trackRecord.first.allTimeChecks, 99);
    });
  });

  group('spend cap', () {
    // Both surfaces must answer "is one more call allowed" identically. If
    // the app counted the window differently from the pipeline, one would
    // permit spending the other refuses — and a cap that can be exceeded on
    // any surface is not a cap.
    test('counts and prunes identically to Python', () {
      for (final c in casesOf('spend.json')) {
        final i = c['input'] as Map<String, Object?>;
        final now = DateTime.parse(i['now'] as String);
        final records = (i['records'] as List)
            .map((e) => SpendRecord.fromJson((e as Map).cast<String, Object?>()))
            .toList();
        final want = c['expected'] as Map<String, Object?>;
        final reason = 'case "${c['name']}"';

        expect(callsInWindow(records, now), equals(want['calls_in_window']),
            reason: reason);

        final kept = prune(records, now).map((r) => r.at.toIso8601String()).toList();
        final wantKept = (want['kept_after_prune'] as List)
            .map((e) => DateTime.parse((e as Map)['at'] as String).toIso8601String())
            .toList();
        expect(kept, equals(wantKept), reason: reason);
      }
    });

    test('the cap refuses at the limit and says when capacity returns', () {
      final now = DateTime.utc(2026, 8, 21, 12);
      final records = [
        for (var i = 0; i < 3; i++)
          SpendRecord(
            at: now.subtract(Duration(hours: 3 - i)),
            provider: 'p',
            model: 'm',
            purpose: 'forecast',
          )
      ];
      final decision = evaluateCap(records, now, maxCalls: 3);
      expect(decision.allowed, isFalse);
      expect(decision.used, 3);
      expect(decision.remaining, 0);
      // The oldest is 3 hours old, so a slot frees 21 hours from now.
      expect(decision.capacityReturnsAt,
          equals(now.subtract(const Duration(hours: 3)).add(const Duration(hours: 24))));
    });

    test('a call exactly one window old has aged out', () {
      // Off-by-one here permits real extra spending while looking correct.
      final now = DateTime.utc(2026, 8, 21, 12);
      final records = [
        SpendRecord(
            at: now.subtract(const Duration(hours: 24)),
            provider: 'p',
            model: 'm',
            purpose: 'forecast')
      ];
      expect(callsInWindow(records, now), 0);
      expect(evaluateCap(records, now, maxCalls: 1).allowed, isTrue);
    });
  });

  group('coverage', () {
    // The three-way split is the contract. An implementation that treated a
    // peer_gap as never_published would reproduce the exact months-long
    // silence this module exists to end — the ECMWF wind gap had no
    // before-and-after transition, only peers that reported what it didn't.
    test('classifies findings identically to Python', () {
      for (final c in casesOf('coverage.json')) {
        final i = c['input'] as Map<String, Object?>;
        final predictions = (i['predictions'] as Map).cast<String, Object?>();

        List<ModelPrediction>? predictionsFor(DateTime rowDate, int lead) {
          final byLead = predictions[formatDate(rowDate)] as Map<String, Object?>?;
          final raw = byLead?['$lead'] as List?;
          if (raw == null) return null;
          return raw
              .map((e) => ModelPrediction.fromJson((e as Map).cast<String, Object?>()))
              .toList();
        }

        final got = detectCoverage(
          predictionsFor: predictionsFor,
          today: DateTime.parse(i['today'] as String),
          models: (i['models'] as List).cast<String>(),
          leadTimesDays: (i['lead_times_days'] as List).cast<int>(),
        );
        final want = (c['expected'] as List).cast<Map<String, Object?>>();
        final reason = 'case "${c['name']}"';

        expect(got.length, equals(want.length),
            reason: '$reason — finding COUNT must match; an extra finding is '
                'noise that trains people to ignore the real ones, and a '
                'missing one is the silence this exists to end');
        for (var n = 0; n < want.length; n++) {
          expect(got[n].kind, equals(want[n]['kind']), reason: reason);
          expect(got[n].model, equals(want[n]['model']), reason: reason);
          expect(got[n].variable, equals(want[n]['variable']), reason: reason);
          expect(got[n].absentRuns, equals(want[n]['absent_runs']), reason: reason);
          expect(got[n].checkedRuns, equals(want[n]['checked_runs']), reason: reason);
          expect(got[n].peersWithValue,
              equals((want[n]['peers_with_value'] as List).cast<String>()), reason: reason);
          final wantSeen = want[n]['last_seen'];
          expect(got[n].lastSeen == null ? null : formatDate(got[n].lastSeen!),
              equals(wantSeen), reason: reason);
        }
      }
    });
  });

  group('synoptic', () {
    // The bounded vocabulary is the contract. An implementation that widened
    // "lower pressure lies toward the northeast" into a named centre or a
    // track would overstate what 12-degree point sampling can carry, and the
    // statements are compared verbatim to stop that drifting.
    test('matches Python, statements included', () {
      for (final c in casesOf('synoptic.json')) {
        final i = c['input'] as Map<String, Object?>;
        final payload = i['payload'] as Map<String, Object?>?;
        final got = summarizeSynoptic(payload);
        final want = c['expected'] as Map<String, Object?>?;
        final reason = 'case "${c['name']}"';

        if (want == null) {
          expect(got, isNull, reason: '$reason — an absent picture must read '
              'as absent, never as a flat field');
          continue;
        }
        expect(got, isNotNull, reason: reason);
        expect(got!.lowestLabel, equals(want['lowest_label']), reason: reason);
        expect(got.highestLabel, equals(want['highest_label']), reason: reason);
        expect(got.gradientHpa, equals(want['gradient_hpa']), reason: reason);
        expect(got.gradientStrength, equals(want['gradient_strength']), reason: reason);
        expect(got.centreMslpHpa, equals(want['centre_mslp_hpa']), reason: reason);
        expect(got.tendencies, equals((want['tendencies'] as Map).cast<String, String>()),
            reason: reason);
        expect(got.statements, equals((want['statements'] as List).cast<String>()),
            reason: '$reason — statements are published prose and compared verbatim');
      }
    });
  });

  group('weekly review', () {
    // The gates are the sensitive part. An implementation that ranked models
    // one check earlier than the other would publish a claim the other
    // withholds — worse than either behaviour alone, because a user comparing
    // the app against the site would have no way to tell which was right.
    test('produces identical findings to Python', () {
      for (final c in casesOf('weekly_review.json')) {
        final i = c['input'] as Map<String, Object?>;
        final predictions = (i['predictions'] as Map).cast<String, Object?>();
        final actuals = (i['actuals'] as Map).cast<String, Object?>();

        List<ModelPrediction>? predictionsFor(DateTime rowDate, int lead) {
          final byLead = predictions[formatDate(rowDate)] as Map<String, Object?>?;
          final raw = byLead?['$lead'] as List?;
          if (raw == null) return null;
          return raw
              .map((e) => ModelPrediction.fromJson((e as Map).cast<String, Object?>()))
              .toList();
        }

        DailyActual? actualFor(DateTime target) {
          final raw = actuals[formatDate(target)] as Map?;
          if (raw == null) return null;
          return DailyActual.fromJson(raw.cast<String, Object?>());
        }

        final review = buildWeeklyReview(
          predictionsFor: predictionsFor,
          actualFor: actualFor,
          allLogDates: predictions.keys.map(DateTime.parse).toList(),
          today: DateTime.parse(i['today'] as String),
          models: (i['models'] as List).cast<String>(),
          leadTimesDays: (i['lead_times_days'] as List).cast<int>(),
        );

        final expected = c['expected'] as Map<String, Object?>;
        final reason = 'case "${c['name']}"';

        expect(review.dataSufficiency, equals(expected['data_sufficiency']), reason: reason);
        expect(review.daysWithPredictions, equals(expected['days_with_predictions']), reason: reason);
        expect(review.daysVerified, equals(expected['days_verified']), reason: reason);
        expect(formatDate(review.periodStart), equals(expected['period_start']), reason: reason);
        expect(formatDate(review.periodEnd), equals(expected['period_end']), reason: reason);

        final expectedFindings = (expected['findings'] as List).cast<Map<String, Object?>>();
        expect(review.findings.length, equals(expectedFindings.length),
            reason: '$reason — finding COUNT must match; an extra or missing '
                'finding is a different published claim');
        for (var n = 0; n < expectedFindings.length; n++) {
          final got = review.findings[n];
          final want = expectedFindings[n];
          expect(got.kind, equals(want['kind']), reason: reason);
          expect(got.claim, equals(want['claim']), reason: reason);
          expect(got.evidence, equals(want['evidence']), reason: reason);
          expect(got.confidence, equals(want['confidence']), reason: reason);
          expect(got.checks, equals(want['checks']), reason: reason);
        }

        final expectedCells = (expected['cells'] as List).cast<Map<String, Object?>>();
        expect(review.cells.length, equals(expectedCells.length), reason: reason);
        for (var n = 0; n < expectedCells.length; n++) {
          final got = review.cells[n];
          final want = expectedCells[n];
          expect(got.model, equals(want['model']), reason: reason);
          expect(got.checks, equals(want['checks']), reason: reason);
          expect(got.correct, equals(want['correct']), reason: reason);
          expect(got.confidence, equals(want['confidence']), reason: reason);
          expect(got.rainPct, equals(want['rain_pct']), reason: reason);
          expect(got.meanHighErrorC, equals(want['mean_high_error_c']), reason: reason);
          expect(got.meanMslpErrorHpa, equals(want['mean_mslp_error_hpa']), reason: reason);
        }
      }
    });
  });

  group('user prompt', () {
    // Byte-for-byte, same as the system prompt — but the cold-start case is
    // the one that matters most. Every "Unavailable — ..." string is what
    // stops a missing input reading as a measurement, and an implementation
    // that emitted an empty list or "null" there would look fine in a diff
    // while inviting the model to treat a gap as data.
    test('matches Python character for character', () {
      for (final c in casesOf('llm_user_prompt.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = buildUserPrompt(
          today: DateTime.parse(i['today'] as String),
          yesterday: DateTime.parse(i['yesterday'] as String),
          publicWebpageUrl: i['public_webpage_url'] as String,
          verificationContext: i['verification_context'],
          trackRecordContext: i['track_record_context'],
          historicalLogs: i['historical_logs'],
          groundAqiReadings: i['ground_aqi_readings'],
          groundAqiSummary: i['ground_aqi_summary'],
          yesterdayActual: i['yesterday_actual'],
          todayWeatherData: (i['today_weather_data'] as Map).cast<String, Object?>(),
          localBulletinSourceName: i['local_bulletin_source_name'] as String,
          localBulletinText: i['local_bulletin_text'] as String,
          earlierToday: (i['earlier_today'] as List?)
              ?.map((e) => (e as Map).cast<String, Object?>())
              .toList(),
          issuance: i['issuance'],
          forwardHourly: i['forward_hourly'],
          reviewContext: i['review_context'],
          modelPredictionsContext: i['model_predictions_context'],
        );
        expect(got, equals(c['expected']), reason: 'case "${c['name']}"');
      }
    });
  });

  group('day over day', () {
    test('compute_day_over_day', () {
      for (final c in casesOf('day_over_day.json')) {
        final i = c['input'] as Map<String, Object?>;
        final y = i['yesterday_actual'] == null
            ? null
            : DailyActual.fromJson(i['yesterday_actual'] as Map<String, Object?>);
        final preds = (i['today_day0_predictions'] as List)
            .map((p) => ModelPrediction.fromJson(p as Map<String, Object?>))
            .toList();
        expectMatches(computeDayOverDay(y, preds)?.toJson(), c['expected'], c['name'] as String);
      }
    });
  });

  group('day character', () {
    test('describe_day_rain', () {
      // The phrase that reaches the reader almost verbatim, including the
      // thunder override that stops an observed storm reading as "dry".
      for (final c in casesOf('describe_day_rain.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = describeDayRain(
          (i['precip_mm'] as num?)?.toDouble(),
          i['onset'] as String?,
          i['thunder'] as bool?,
        );
        expect(got, equals(c['expected']), reason: 'case "${c['name']}"');
      }
    });
  });

  group('temperature display', () {
    test('temp_high_low', () {
      // Arithmetic that used to be the model's job. The .5 cases are the
      // cross-language edge: Python rounds half to EVEN and Dart's .round()
      // rounds half away from zero, so a port using the latter publishes a
      // different temperature than the site.
      for (final c in casesOf('temp_high_low.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = formatTempHighLow(
          (i['high_c'] as num).toDouble(),
          (i['low_c'] as num).toDouble(),
        );
        expect(got, equals(c['expected']), reason: 'case "${c['name']}"');
      }
    });
  });

  group('ground aqi last known', () {
    test('last_known_ground_aqi', () {
      for (final c in casesOf('aqi_last_known.json')) {
        final i = c['input'] as Map<String, Object?>;
        final readings = (i['readings'] as List)
            .map((r) => GroundAqiReading.fromJson(r as Map<String, Object?>))
            .toList();
        final got = lastKnownGroundAqi(readings, DateTime.parse(i['now'] as String));
        expectMatches(got?.toJson(), c['expected'], c['name'] as String);
      }
    });
  });

  group('instability', () {
    test('summarize_instability', () {
      // Whether the Overview must mention thunder — a threshold decision, so
      // both implementations have to land on the same side of it.
      for (final c in casesOf('instability.json')) {
        final i = c['input'] as Map<String, Object?>;
        final got = summarizeInstability(
          i['hourly_multi_model'] as Map<String, Object?>,
          (i['models'] as List).cast<String>(),
          (i['threshold'] as num).toDouble(),
        );
        expectMatches(got?.toJson(), c['expected'], c['name'] as String);
      }
    });
  });

  group('daypart', () {
    test('the part of day matches Python, statements included', () {
      // Every string here goes into the prompt verbatim, so a difference of
      // one word between the two implementations is a difference in what the
      // model is told.
      for (final c in loadVectors('daypart.json')['cases'] as List) {
        final i = (c as Map)['input'] as Map;
        final next = i['next_sunrise'] as String?;
        final got = summarizeDaypart(
          DateTime.parse(i['now'] as String),
          DateTime.parse(i['sunrise'] as String),
          DateTime.parse(i['sunset'] as String),
          next == null ? null : DateTime.parse(next),
        ).toJson();
        expect(got, equals(c['expected']), reason: 'case "${c['name']}"');
      }
    });

    test('the no-sun fallback matches Python', () {
      for (final c in loadVectors('daypart_without_sun.json')['cases'] as List) {
        final got = daypartWithoutSun(
                DateTime.parse(((c as Map)['input'] as Map)['now'] as String))
            .toJson();
        expect(got, equals(c['expected']), reason: 'case "${c['name']}"');
      }
    });

    test('the clock reconciliation matches Python', () {
      for (final c in loadVectors('daypart_clock.json')['cases'] as List) {
        final i = (c as Map)['input'] as Map;
        final r = reconcileNow(
          DateTime.parse(i['system_local'] as String),
          i['server_date_header'] as String?,
          i['utc_offset_seconds'] as int?,
        );
        expect(
          {
            'now': r.now.toIso8601String().replaceFirst(RegExp(r'\.\d+$'), ''),
            'warned': r.warning != null,
          },
          equals(c['expected']),
          reason: 'case "${c['name']}"',
        );
      }
    });

    test('the forward-hours trim matches Python', () {
      for (final c in loadVectors('daypart_forward_hours.json')['cases'] as List) {
        final i = (c as Map)['input'] as Map;
        final got = forwardHours(
          (i['hourly_multi_model'] as Map).cast<String, Object?>(),
          DateTime.parse(i['now'] as String),
        );
        expect(jsonDecode(jsonEncode(got)), equals(c['expected']),
            reason: 'case "${c['name']}"');
      }
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
      'llm_system_prompt.json',
      'llm_user_prompt.json',
      'weekly_review.json',
      'synoptic.json',
      'coverage.json',
      'spend.json',
      'verification.json',
      'day_over_day.json',
      'describe_day_rain.json',
      'temp_high_low.json',
      'aqi_last_known.json',
      'instability.json',
      'daypart.json',
      'daypart_without_sun.json',
      'daypart_clock.json',
      'daypart_forward_hours.json',
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
