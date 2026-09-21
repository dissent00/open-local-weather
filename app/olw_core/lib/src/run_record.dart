import 'models.dart';

/// One run's row of the record — the Dart mirror of `IssuancePredictions`.
///
/// THE ROW IS KEPT AS THE PIPELINE COMMITS IT, and re-emitted unchanged. The
/// app stores this row on the device (its item 4), and the shared datastore
/// the modes discussion describes (upstream 113) is a copy of these rows, so
/// a field this class does not know about must survive a round trip rather
/// than be dropped — the shape gains fields faster than a port follows. The
/// raw JSON is the source of truth; the typed getters read from it. Pinned by
/// spec/vectors/run_row.json against a committed row.
///
/// A RUN, NOT A DAY. The operator's direction on 2026-09-17: a location can
/// produce many runs in a calendar day, scoring is moving to every run with
/// new model data on the rolling window, and the "daily" forecast is a
/// legacy unit. Nothing here knows what day it is; `localDay` is derived by
/// the caller from [issuedAt] and the location's zone.
class RunRecord {
  RunRecord.fromJson(Map<String, Object?> json) : _raw = _deepCopy(json);

  /// A row for a run made on this device, in the pipeline's field order.
  ///
  /// `issuedAt` must be UTC: the pipeline stamps `issued_at` with the UTC
  /// instant and emits it with a `Z`, and a local instant here would be
  /// re-read as a different moment by every other consumer of the row.
  /// `windowOpenedLocal` is the LOCAL wall clock the window was sliced at,
  /// stored naive, exactly as `IssuancePredictions.window_opened_local`.
  RunRecord.create({
    required DateTime issuedAt,
    required List<ModelPrediction> day0,
    List<ModelPrediction> day3 = const [],
    List<ModelPrediction> day7 = const [],
    List<ModelPrediction> windowPredictions = const [],
    List<ModelPrediction> secondaryPredictions = const [],
    DateTime? windowOpenedLocal,
  })  : assert(issuedAt.isUtc, 'issued_at is a UTC instant'),
        _raw = {
          'issued_at': _utcStamp(issuedAt),
          'predictions': {
            'day0': [for (final p in day0) p.toJson()],
            'day3': [for (final p in day3) p.toJson()],
            'day7': [for (final p in day7) p.toJson()],
          },
          'window_predictions': [for (final p in windowPredictions) p.toJson()],
          // Upstream ROADMAP item 6. The second point's Day+0 per model, so
          // the gulf section can be scored against actuals the server has
          // cached and discarded since this project was written. Empty on
          // every row written before 2026-09-21 and wherever no secondary
          // point is configured, which is this app today.
          'secondary_predictions': [
            for (final p in secondaryPredictions) p.toJson()
          ],
          // Written empty and filled by the server's verification pass, the
          // same way `window_scores` is: a row is created before the day it
          // describes has finished, so nothing can be scored yet.
          'secondary_scores': <String, Object?>{},
          'secondary_verified_at': null,
          'window_opened_local':
              windowOpenedLocal == null ? null : _naiveStamp(windowOpenedLocal),
          'window_scores': <String, Object?>{},
          'window_verified_at': null,
          'day_over_day': null,
        };

  final Map<String, Object?> _raw;

  DateTime get issuedAt => DateTime.parse(_raw['issued_at'] as String);

  List<ModelPrediction> predictionsAt(int leadTimeDays) {
    final byLead = _raw['predictions'] as Map<String, Object?>?;
    final raw = byLead?['day$leadTimeDays'] as List?;
    if (raw == null) return const [];
    return [
      for (final p in raw) ModelPrediction.fromJson((p as Map).cast<String, Object?>())
    ];
  }

  List<ModelPrediction> get windowPredictions => [
        for (final p in (_raw['window_predictions'] as List? ?? const []))
          ModelPrediction.fromJson((p as Map).cast<String, Object?>())
      ];

  /// What the second point's Day+0 turned out to be worth, per model —
  /// upstream ROADMAP item 6. Empty until the day has finished and the
  /// server's pass has scored it against the second point's own actuals.
  Map<String, VerificationScore> get secondaryScores => {
        for (final e in ((_raw['secondary_scores'] as Map?) ?? const {}).entries)
          e.key as String:
              VerificationScore.fromJson((e.value as Map).cast<String, Object?>())
      };

  DateTime? get secondaryVerifiedAt {
    final s = _raw['secondary_verified_at'] as String?;
    return s == null ? null : DateTime.parse(s);
  }

  List<ModelPrediction> get secondaryPredictions => [
        for (final p in (_raw['secondary_predictions'] as List? ?? const []))
          ModelPrediction.fromJson((p as Map).cast<String, Object?>())
      ];

  DateTime? get windowOpenedLocal {
    final s = _raw['window_opened_local'] as String?;
    return s == null ? null : DateTime.parse(s);
  }

  Map<String, VerificationScore> get windowScores => {
        for (final e in ((_raw['window_scores'] as Map?) ?? const {}).entries)
          e.key as String:
              VerificationScore.fromJson((e.value as Map).cast<String, Object?>()),
      };

  DateTime? get windowVerifiedAt {
    final s = _raw['window_verified_at'] as String?;
    return s == null ? null : DateTime.parse(s);
  }

  /// The row as it was given, every field included, for storage or sharing.
  Map<String, Object?> toJson() => _deepCopy(_raw);

  static Map<String, Object?> _deepCopy(Map<String, Object?> m) =>
      {for (final e in m.entries) e.key: _copyValue(e.value)};

  static Object? _copyValue(Object? v) => switch (v) {
        Map() => _deepCopy(v.cast<String, Object?>()),
        List() => [for (final x in v) _copyValue(x)],
        _ => v,
      };

  /// `2026-09-16T03:03:18.229475Z` — the pipeline's form. Dart keeps
  /// microseconds, and `toIso8601String` on a UTC instant emits exactly this.
  static String _utcStamp(DateTime utc) => utc.toIso8601String();

  /// `2026-09-16T06:00:00` — naive, no fraction, as the pipeline emits a
  /// zone-less datetime. `toIso8601String` would append `.000`.
  static String _naiveStamp(DateTime t) {
    String two(int n) => n.toString().padLeft(2, '0');
    return '${t.year}-${two(t.month)}-${two(t.day)}T${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }
}
