// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00
/// The structured-output contract: what the LLM must return, and the two
/// provider dialects that contract is expressed in.
///
/// IMPORTANT — how this stays honest. The Python side DERIVES these schemas
/// from a pydantic class. Dart has no pydantic, so it DECLARES them by hand
/// below. That would be a drift risk, except the shared vectors
/// (`spec/vectors/llm_schema_*.json`) assert the two are byte-identical, so
/// both implementations send the same contract to the same APIs.
///
/// Direction of travel if the shape changes: edit the pydantic model,
/// regenerate the vectors, then update these maps until the test passes.
/// Never the reverse.
library;

/// The `name` sent in OpenAI's `json_schema` block.
///
/// Deliberately keeps the Python class's legacy name rather than matching
/// Dart's [ForecastResponse]. It is only a label the API echoes back, but
/// keeping it identical means requests from both implementations are
/// byte-for-byte the same, which is one less thing to reason about when
/// comparing them.
import '../models.dart';

const String forecastSchemaName = 'GeminiForecastResponse';

const String _todayPropertiesDescription =
    "The LLM's synthesized, BLENDED call across all models — genuine\n"
    "reasoning, not any one model's raw number. Only rain_expected, rain,\n"
    "temp_high_c and temp_low_c are required.\n"
    "\n"
    "`temp_high_low` is deliberately absent. It was a display string the model\n"
    "wrote, and it drifted in both value and format; it is now computed from\n"
    "the two numbers here by `models.format_temp_high_low`. Asking a language\n"
    "model to convert units is asking it to do arithmetic, which this project\n"
    "does in code.";

/// Gemini's `responseSchema` dialect: uppercase type names, a `nullable`
/// flag, and no `$ref`/`$defs`.
Map<String, Object?> geminiForecastSchema() => {
      'type': 'OBJECT',
      'properties': {
        'yesterday_verification': {'type': 'STRING'},
        'verification_notes': {
          'type': 'ARRAY',
          'items': {
            'type': 'OBJECT',
            'properties': {
              'lead_time_days': {'type': 'INTEGER'},
              'note': {'type': 'STRING'},
            },
            'required': ['lead_time_days', 'note'],
          },
        },
        'skill_profile_summaries': {
          'type': 'ARRAY',
          'items': {
            'type': 'OBJECT',
            'properties': {
              'model': {'type': 'STRING'},
              'lead_time_days': {'type': 'INTEGER'},
              'summary': {'type': 'STRING'},
            },
            'required': ['model', 'lead_time_days', 'summary'],
          },
        },
        'today_properties': _geminiTodayProperties(),
        'extended_properties': {
          'type': 'ARRAY',
          'items': {
            'type': 'OBJECT',
            'properties': {
              'lead_time_days': {'type': 'INTEGER'},
              'rain': {'type': 'BOOLEAN'},
              'rain_probability_pct': {'type': 'INTEGER', 'nullable': true},
            },
            'required': ['lead_time_days', 'rain'],
            'description':
                'Your own rain call for one day beyond today. Scored against what\n'
                'happens. Omit a lead rather than guess at it.',
          },
        },
        'today_narrative': {'type': 'STRING'},
        'whatsapp_summary': {'type': 'STRING', 'nullable': true},
      },
      'required': [
        'yesterday_verification',
        'today_properties',
        'today_narrative',
      ],
    };

Map<String, Object?> _geminiTodayProperties() => {
      'type': 'OBJECT',
      'properties': {
        'rain_expected': {'type': 'STRING'},
        'onset_window': {'type': 'STRING', 'nullable': true},
        'peak_wind_kmh': {'type': 'NUMBER', 'nullable': true},
        'temp_high_c': {'type': 'NUMBER'},
        'temp_low_c': {'type': 'NUMBER'},
        'rain': {'type': 'BOOLEAN'},
        'onset_hour': {'type': 'STRING', 'nullable': true},
        'precip_mm': {'type': 'NUMBER', 'nullable': true},
        'rain_probability_pct': {'type': 'INTEGER', 'nullable': true},
        'mslp_trend_24h': {'type': 'STRING', 'nullable': true},
        'synoptic_pattern': {'type': 'STRING', 'nullable': true},
        'uv_index_max': {'type': 'STRING', 'nullable': true},
        'air_quality_aqi': {'type': 'STRING', 'nullable': true},
      },
      'required': [
        'rain_expected',
        'temp_high_c',
        'temp_low_c',
        'rain',
      ],
      'description': _todayPropertiesDescription,
    };

/// Standard JSON Schema as OpenAI strict mode and Anthropic tool
/// `input_schema` require.
///
/// Three rules beyond plain JSON Schema, all satisfied here:
///  1. every object sets `additionalProperties: false`
///  2. every object's `required` lists ALL its properties — optional fields
///     are expressed as nullable rather than omitted
///  3. no `default` keyword anywhere
Map<String, Object?> strictForecastSchema() => {
      'type': 'object',
      'properties': {
        'yesterday_verification': {'type': 'string'},
        'verification_notes': {
          'type': 'array',
          'items': {
            'type': 'object',
            'properties': {
              'lead_time_days': {'type': 'integer'},
              'note': {'type': 'string'},
            },
            'required': ['lead_time_days', 'note'],
            'additionalProperties': false,
          },
        },
        'skill_profile_summaries': {
          'type': 'array',
          'items': {
            'type': 'object',
            'properties': {
              'model': {'type': 'string'},
              'lead_time_days': {'type': 'integer'},
              'summary': {'type': 'string'},
            },
            'required': ['model', 'lead_time_days', 'summary'],
            'additionalProperties': false,
          },
        },
        'today_properties': _strictTodayProperties(),
        'extended_properties': {
          'type': 'array',
          'items': {
            'type': 'object',
            'properties': {
              'lead_time_days': {'type': 'integer'},
              'rain': {'type': 'boolean'},
              'rain_probability_pct': {
                'type': ['integer', 'null']
              },
            },
            'required': ['lead_time_days', 'rain', 'rain_probability_pct'],
            'additionalProperties': false,
            'description':
                'Your own rain call for one day beyond today. Scored against what\n'
                'happens. Omit a lead rather than guess at it.',
          },
        },
        'today_narrative': {'type': 'string'},
        'whatsapp_summary': {
          'type': ['string', 'null']
        },
      },
      'required': [
        'yesterday_verification',
        'verification_notes',
        'skill_profile_summaries',
        'today_properties',
        'extended_properties',
        'today_narrative',
        'whatsapp_summary',
      ],
      'additionalProperties': false,
    };

Map<String, Object?> _strictTodayProperties() => {
      'type': 'object',
      'properties': {
        'rain_expected': {'type': 'string'},
        'onset_window': {
          'type': ['string', 'null']
        },
        'peak_wind_kmh': {
          'type': ['number', 'null']
        },
        'temp_high_c': {'type': 'number'},
        'temp_low_c': {'type': 'number'},
        'rain': {'type': 'boolean'},
        'onset_hour': {'type': ['string', 'null']},
        'precip_mm': {'type': ['number', 'null']},
        'rain_probability_pct': {'type': ['integer', 'null']},
        'mslp_trend_24h': {
          'type': ['string', 'null']
        },
        'synoptic_pattern': {
          'type': ['string', 'null']
        },
        'uv_index_max': {
          'type': ['string', 'null']
        },
        'air_quality_aqi': {
          'type': ['string', 'null']
        },
      },
      'required': [
        'rain_expected',
        'onset_window',
        'peak_wind_kmh',
        'temp_high_c',
        'temp_low_c',
        'rain',
        'onset_hour',
        'precip_mm',
        'rain_probability_pct',
        'mslp_trend_24h',
        'synoptic_pattern',
        'uv_index_max',
        'air_quality_aqi',
      ],
      'additionalProperties': false,
      'description': _todayPropertiesDescription,
    };

double? _toDouble(Object? v) => v == null ? null : (v as num).toDouble();

class VerificationNote {
  final int leadTimeDays;
  final String note;
  const VerificationNote(this.leadTimeDays, this.note);
  factory VerificationNote.fromJson(Map<String, Object?> j) =>
      VerificationNote((j['lead_time_days'] as num).toInt(), j['note'] as String);
}

class SkillProfileSummaryItem {
  final String model;
  final int leadTimeDays;
  final String summary;
  const SkillProfileSummaryItem(this.model, this.leadTimeDays, this.summary);
  factory SkillProfileSummaryItem.fromJson(Map<String, Object?> j) =>
      SkillProfileSummaryItem(
        j['model'] as String,
        (j['lead_time_days'] as num).toInt(),
        j['summary'] as String,
      );
}

/// The blended cross-model call. Genuine reasoning, not any single model's
/// raw number.
class TodayProperties {
  final String rainExpected;
  final String? onsetWindow;
  final double? peakWindKmh;
  final double tempHighC;
  final double tempLowC;
  /// Computed from [tempHighC] and [tempLowC], never parsed. The model used
  /// to supply this and it drifted in value and in format — see
  /// [formatTempHighLow].
  String get tempHighLow => formatTempHighLow(tempHighC, tempLowC);

  /// The scored commitment.
  ///
  /// [rainExpected] and [onsetWindow] above are prose, written for a reader.
  /// These are the same calls in the form the accuracy record can check, and
  /// they are what the blend is scored on as a peer of the models it
  /// synthesizes. Prose is what the forecast SAYS; these are what it COMMITS
  /// to, and a forecast whose prose and commitment disagree is a bug that is
  /// now visible instead of unfalsifiable.
  final bool rain;

  /// "HH:MM" local, Day+0 only. Null means no rain expected, or expected
  /// without resolvable timing — never midnight.
  final String? onsetHour;
  final double? precipMm;

  /// The forecaster's OWN chance of rain, percent — upstream ROADMAP item 58.
  ///
  /// A separate commitment from [rain], not a restatement: `rain` is checked
  /// for being right, this is checked for CALIBRATION. Optional, so a stored
  /// response from before the field existed does not fail a run — it simply
  /// is not Brier-scored. Absent is not 50.
  final int? rainProbabilityPct;

  final String? mslpTrend24h;
  final String? synopticPattern;
  final String? uvIndexMax;
  final String? airQualityAqi;

  const TodayProperties({
    required this.rainExpected,
    this.onsetWindow,
    this.peakWindKmh,
    required this.tempHighC,
    required this.tempLowC,
    required this.rain,
    this.onsetHour,
    this.precipMm,
    this.rainProbabilityPct,
    this.mslpTrend24h,
    this.synopticPattern,
    this.uvIndexMax,
    this.airQualityAqi,
  });

  factory TodayProperties.fromJson(Map<String, Object?> j) => TodayProperties(
        rainExpected: j['rain_expected'] as String,
        onsetWindow: j['onset_window'] as String?,
        peakWindKmh: _toDouble(j['peak_wind_kmh']),
        tempHighC: _toDouble(j['temp_high_c'])!,
        tempLowC: _toDouble(j['temp_low_c'])!,
        rain: j['rain'] as bool,
        onsetHour: j['onset_hour'] as String?,
        precipMm: _toDouble(j['precip_mm']),
        rainProbabilityPct: (j['rain_probability_pct'] as num?)?.toInt(),
        mslpTrend24h: j['mslp_trend_24h'] as String?,
        synopticPattern: j['synoptic_pattern'] as String?,
        uvIndexMax: j['uv_index_max'] as String?,
        airQualityAqi: j['air_quality_aqi'] as String?,
      );

  /// The counterpart to [fromJson], using the SAME wire keys.
  ///
  /// Round-tripping matters because a client storing a forecast locally must
  /// be able to read back exactly what a provider returned — and because the
  /// keys here are the pipeline's committed JSON shape, so a client that
  /// invented its own names would produce records the server could not
  /// import. Mirrors `model_dump()` on the Python side.
  Map<String, Object?> toJson() => {
        'rain_expected': rainExpected,
        'onset_window': onsetWindow,
        'peak_wind_kmh': peakWindKmh,
        'temp_high_c': tempHighC,
        'temp_low_c': tempLowC,
        'rain': rain,
        'onset_hour': onsetHour,
        'precip_mm': precipMm,
        'rain_probability_pct': rainProbabilityPct,
        'temp_high_low': tempHighLow,
        'mslp_trend_24h': mslpTrend24h,
        'synoptic_pattern': synopticPattern,
        'uv_index_max': uvIndexMax,
        'air_quality_aqi': airQualityAqi,
      };
}

/// What a provider must return. Validated on parse — a malformed response
/// throws rather than yielding a half-built forecast.
/// The blend's committed call for one lead time beyond today — ROADMAP item
/// 72, minimal shape.
///
/// RAIN ONLY, deliberately. The full item widens the schema at every lead the
/// record scores; this carries the one variable item 58's Brier can already
/// score. Shipped small and early because item 72 changes what FUTURE days
/// record and recovers nothing, so a day spent designing the rest is a Day+3
/// call permanently lost.
class ExtendedDayProperties {
  /// 3 or 7 — the leads the record already scores. Days 1 and 2 have no row
  /// to land in.
  final int leadTimeDays;
  final bool rain;

  /// Null means no confidence stated, which leaves the Brier column empty for
  /// that row rather than filling it with a guess.
  final int? rainProbabilityPct;

  const ExtendedDayProperties({
    required this.leadTimeDays,
    required this.rain,
    this.rainProbabilityPct,
  });

  factory ExtendedDayProperties.fromJson(Map<String, Object?> j) =>
      ExtendedDayProperties(
        leadTimeDays: (j['lead_time_days'] as num).toInt(),
        rain: j['rain'] as bool,
        rainProbabilityPct: (j['rain_probability_pct'] as num?)?.toInt(),
      );

  Map<String, Object?> toJson() => {
        'lead_time_days': leadTimeDays,
        'rain': rain,
        'rain_probability_pct': rainProbabilityPct,
      };
}

class ForecastResponse {
  final String yesterdayVerification;
  final List<VerificationNote> verificationNotes;
  final List<SkillProfileSummaryItem> skillProfileSummaries;
  final TodayProperties todayProperties;

  /// ROADMAP item 72. Empty is a legitimate answer and the default: a run
  /// that declines to commit at a lead scores nothing there, where a guessed
  /// boolean is scored wrong exactly as confidently as a real one.
  final List<ExtendedDayProperties> extendedProperties;
  final String todayNarrative;
  final String? whatsappSummary;

  const ForecastResponse({
    required this.yesterdayVerification,
    required this.verificationNotes,
    required this.skillProfileSummaries,
    required this.todayProperties,
    this.extendedProperties = const [],
    required this.todayNarrative,
    this.whatsappSummary,
  });

  factory ForecastResponse.fromJson(Map<String, Object?> j) => ForecastResponse(
        yesterdayVerification: j['yesterday_verification'] as String,
        verificationNotes: ((j['verification_notes'] as List?) ?? const [])
            .map((e) => VerificationNote.fromJson(e as Map<String, Object?>))
            .toList(),
        skillProfileSummaries:
            ((j['skill_profile_summaries'] as List?) ?? const [])
                .map((e) =>
                    SkillProfileSummaryItem.fromJson(e as Map<String, Object?>))
                .toList(),
        todayProperties: TodayProperties.fromJson(
            j['today_properties'] as Map<String, Object?>),
        extendedProperties: ((j['extended_properties'] as List?) ?? const [])
            .map((e) =>
                ExtendedDayProperties.fromJson(e as Map<String, Object?>))
            .toList(),
        todayNarrative: j['today_narrative'] as String,
        whatsappSummary: j['whatsapp_summary'] as String?,
      );
}
