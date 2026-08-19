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
const String forecastSchemaName = 'GeminiForecastResponse';

const String _todayPropertiesDescription =
    "The LLM's synthesized, BLENDED call across all models — genuine\n"
    "reasoning, not any one model's raw number. Only rain_expected,\n"
    "temp_high_c, temp_low_c, and temp_high_low are required; the original\n"
    "schema's `required` list is preserved exactly.";

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
        'temp_high_low': {'type': 'STRING'},
        'mslp_trend_24h': {'type': 'STRING', 'nullable': true},
        'synoptic_pattern': {'type': 'STRING', 'nullable': true},
        'uv_index_max': {'type': 'STRING', 'nullable': true},
        'air_quality_aqi': {'type': 'STRING', 'nullable': true},
      },
      'required': [
        'rain_expected',
        'temp_high_c',
        'temp_low_c',
        'temp_high_low',
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
        'temp_high_low': {'type': 'string'},
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
        'temp_high_low',
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
  final String tempHighLow;
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
    required this.tempHighLow,
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
        tempHighLow: j['temp_high_low'] as String,
        mslpTrend24h: j['mslp_trend_24h'] as String?,
        synopticPattern: j['synoptic_pattern'] as String?,
        uvIndexMax: j['uv_index_max'] as String?,
        airQualityAqi: j['air_quality_aqi'] as String?,
      );
}

/// What a provider must return. Validated on parse — a malformed response
/// throws rather than yielding a half-built forecast.
class ForecastResponse {
  final String yesterdayVerification;
  final List<VerificationNote> verificationNotes;
  final List<SkillProfileSummaryItem> skillProfileSummaries;
  final TodayProperties todayProperties;
  final String todayNarrative;
  final String? whatsappSummary;

  const ForecastResponse({
    required this.yesterdayVerification,
    required this.verificationNotes,
    required this.skillProfileSummaries,
    required this.todayProperties,
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
        todayNarrative: j['today_narrative'] as String,
        whatsappSummary: j['whatsapp_summary'] as String?,
      );
}
