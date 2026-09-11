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

/// A generous ceiling on the SHORT display strings the forecaster writes.
///
/// These are one-line values a reader sees beside a number: the morning run of
/// 2026-09-10 produced "8.7 (Very High)" for UV, fifteen characters. That
/// evening the same field came back at 15,930 — a repetition loop that parsed,
/// validated, stored and published, because a string field with no bound
/// accepts anything at all.
///
/// SIZED AGAINST THE WHOLE STORED RECORD, and it was wrong first. The original
/// 200 came from two convenient samples and was described as four times the
/// longest value ever seen. It was not: measured across all 360 display
/// strings ever written, the longest is a 155-character `synoptic_pattern`
/// describing an ordinary day, which sat 45 characters from aborting a
/// forecast.
///
/// The cost is not symmetric. Too tight and a wordy but correct forecast is
/// refused and the day has none; too loose and an odd 800-character value is
/// published, which is ugly and nothing worse. So this errs long: 6.5x the
/// longest real value, still 16x tighter than the 15,930 that caused it.
///
/// Not applied to the narrative or the WhatsApp summary, which are long by
/// design. Mirrors `MAX_DISPLAY_STRING` in the Python schema.
const int maxDisplayString = 1000;

/// Throws rather than truncating. A value this long is not a long answer, it
/// is a broken one, and the rest of the response was produced by the same
/// generation — `parseForecast` turns this into an `LlmResponseError` and the
/// run aborts, which is the right outcome.
String? _bounded(Object? value, String field) {
  final s = value as String?;
  if (s != null && s.length > maxDisplayString) {
    throw FormatException(
        '$field is ${s.length} characters, over the $maxDisplayString limit for a display string');
  }
  return s;
}

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
        rainExpected: _bounded(j['rain_expected'], 'rain_expected')!,
        onsetWindow: _bounded(j['onset_window'], 'onset_window'),
        peakWindKmh: _toDouble(j['peak_wind_kmh']),
        tempHighC: _toDouble(j['temp_high_c'])!,
        tempLowC: _toDouble(j['temp_low_c'])!,
        rain: j['rain'] as bool,
        onsetHour: _bounded(j['onset_hour'], 'onset_hour'),
        precipMm: _toDouble(j['precip_mm']),
        rainProbabilityPct: (j['rain_probability_pct'] as num?)?.toInt(),
        mslpTrend24h: _bounded(j['mslp_trend_24h'], 'mslp_trend_24h'),
        synopticPattern: _bounded(j['synoptic_pattern'], 'synoptic_pattern'),
        uvIndexMax: _bounded(j['uv_index_max'], 'uv_index_max'),
        airQualityAqi: _bounded(j['air_quality_aqi'], 'air_quality_aqi'),
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

/// The judgment call's schema — upstream ROADMAP item 59 step 3.
///
/// Generated from Python's `to_gemini_schema(GeminiJudgmentResponse)` and
/// pinned by `spec/vectors/llm_schema_split.json`. The `description` strings
/// are SHIPPED TO THE PROVIDER, so they are part of the instruction set and
/// drift here is drift in the forecast.
Map<String, Object?> geminiJudgmentSchema() => {
      'type': 'OBJECT',
      'properties': {
        'today_properties': {
          'type': 'OBJECT',
          'properties': {
            'rain_expected': {
              'type': 'STRING',
            },
            'onset_window': {
              'type': 'STRING',
              'nullable': true,
            },
            'peak_wind_kmh': {
              'type': 'NUMBER',
              'nullable': true,
            },
            'temp_high_c': {
              'type': 'NUMBER',
            },
            'temp_low_c': {
              'type': 'NUMBER',
            },
            'rain': {
              'type': 'BOOLEAN',
            },
            'onset_hour': {
              'type': 'STRING',
              'nullable': true,
            },
            'precip_mm': {
              'type': 'NUMBER',
              'nullable': true,
            },
            'rain_probability_pct': {
              'type': 'INTEGER',
              'nullable': true,
            },
            'mslp_trend_24h': {
              'type': 'STRING',
              'nullable': true,
            },
            'synoptic_pattern': {
              'type': 'STRING',
              'nullable': true,
            },
            'uv_index_max': {
              'type': 'STRING',
              'nullable': true,
            },
            'air_quality_aqi': {
              'type': 'STRING',
              'nullable': true,
            },
          },
          'required': ['rain_expected', 'temp_high_c', 'temp_low_c', 'rain'],
          'description': 'The LLM\'s synthesized, BLENDED call across all models — genuine\nreasoning, not any one model\'s raw number. Only rain_expected, rain,\ntemp_high_c and temp_low_c are required.\n\n`temp_high_low` is deliberately absent. It was a display string the model\nwrote, and it drifted in both value and format; it is now computed from\nthe two numbers here by `models.format_temp_high_low`. Asking a language\nmodel to convert units is asking it to do arithmetic, which this project\ndoes in code.',
        },
        'extended_properties': {
          'type': 'ARRAY',
          'items': {
            'type': 'OBJECT',
            'properties': {
              'lead_time_days': {
                'type': 'INTEGER',
              },
              'rain': {
                'type': 'BOOLEAN',
              },
              'rain_probability_pct': {
                'type': 'INTEGER',
                'nullable': true,
              },
            },
            'required': ['lead_time_days', 'rain'],
            'description': 'Your own rain call for one day beyond today. Scored against what\nhappens. Omit a lead rather than guess at it.',
          },
        },
      },
      'required': ['today_properties'],
      'description': 'What the judgment call returns: the scored fields, and nothing else.\n\nROADMAP item 59 step 3. This half of the split is the point of it — the\nforecaster deciding these numbers reads an 18,400-character prompt\ninstead of a 47,054-character one, and every instruction in it governs a\nnumber.',
    };

/// The rendering call's schema — upstream ROADMAP item 59 step 3.
///
/// THE SEAM IS THIS SHAPE. It has no `today_properties` and no
/// `extended_properties`, so a rendering call cannot return a scored value
/// however its prompt is later edited. The absences are the point, and they
/// are pinned by `spec/vectors/llm_schema_split.json` for that reason.
Map<String, Object?> geminiNarrativeSchema() => {
      'type': 'OBJECT',
      'properties': {
        'yesterday_verification': {
          'type': 'STRING',
        },
        'verification_notes': {
          'type': 'ARRAY',
          'items': {
            'type': 'OBJECT',
            'properties': {
              'lead_time_days': {
                'type': 'INTEGER',
              },
              'note': {
                'type': 'STRING',
              },
            },
            'required': ['lead_time_days', 'note'],
          },
        },
        'skill_profile_summaries': {
          'type': 'ARRAY',
          'items': {
            'type': 'OBJECT',
            'properties': {
              'model': {
                'type': 'STRING',
              },
              'lead_time_days': {
                'type': 'INTEGER',
              },
              'summary': {
                'type': 'STRING',
              },
            },
            'required': ['model', 'lead_time_days', 'summary'],
          },
        },
        'today_narrative': {
          'type': 'STRING',
        },
        'whatsapp_summary': {
          'type': 'STRING',
          'nullable': true,
        },
      },
      'required': ['yesterday_verification', 'today_narrative'],
      'description': 'What the rendering call returns: prose, and only prose.\n\nTHE SEAM IS THIS CLASS. Nothing here is scored, and there is no field a\nscored value could be written into, so the rendering call cannot revise\nthe forecast however its prompt is later edited. That separation used to\nbe a property of where a paragraph sat inside one string — see\ntests/test_prompt_seam.py, which still checks the weaker claim because a\nprompt can still be edited and this cannot.',
    };

/// What the judgment call returns — upstream ROADMAP item 59 step 3.
class JudgmentResponse {
  final TodayProperties todayProperties;
  final List<ExtendedDayProperties> extendedProperties;

  const JudgmentResponse({
    required this.todayProperties,
    this.extendedProperties = const [],
  });

  factory JudgmentResponse.fromJson(Map<String, Object?> j) => JudgmentResponse(
        todayProperties: TodayProperties.fromJson(
            j['today_properties'] as Map<String, Object?>),
        extendedProperties: ((j['extended_properties'] as List?) ?? const [])
            .map((e) =>
                ExtendedDayProperties.fromJson(e as Map<String, Object?>))
            .toList(),
      );

  /// What the rendering call is handed, under "THE FORECASTER'S CALL".
  ///
  /// Key order and spelling match Python's `model_dump()`, because the
  /// rendering call reads this as part of its user message and the two
  /// implementations must hand the forecaster the same document.
  Map<String, Object?> toJson() => {
        'today_properties': todayProperties.toJson(),
        'extended_properties':
            extendedProperties.map((e) => e.toJson()).toList(),
      };
}

/// What the rendering call returns — prose, and only prose.
///
/// There is no field here a scored value could be written into. That is the
/// seam, and it is structural rather than a matter of what the prompt says.
class NarrativeResponse {
  final String yesterdayVerification;
  final List<VerificationNote> verificationNotes;
  final List<SkillProfileSummaryItem> skillProfileSummaries;
  final String todayNarrative;
  final String? whatsappSummary;

  const NarrativeResponse({
    required this.yesterdayVerification,
    required this.verificationNotes,
    required this.skillProfileSummaries,
    required this.todayNarrative,
    this.whatsappSummary,
  });

  factory NarrativeResponse.fromJson(Map<String, Object?> j) =>
      NarrativeResponse(
        yesterdayVerification: j['yesterday_verification'] as String,
        verificationNotes: ((j['verification_notes'] as List?) ?? const [])
            .map((e) => VerificationNote.fromJson(e as Map<String, Object?>))
            .toList(),
        skillProfileSummaries:
            ((j['skill_profile_summaries'] as List?) ?? const [])
                .map((e) =>
                    SkillProfileSummaryItem.fromJson(e as Map<String, Object?>))
                .toList(),
        todayNarrative: j['today_narrative'] as String,
        whatsappSummary: j['whatsapp_summary'] as String?,
      );
}

/// Puts the two calls back together in the shape everything downstream
/// already reads. Mirrors Python's `merge_forecast_response`.
///
/// The merge is total and mechanical — every field of the result comes from
/// exactly one of the two inputs, and neither can supply a field the other
/// owns. That is what makes the split invisible below this line.
ForecastResponse mergeForecastResponse(
  JudgmentResponse judgment,
  NarrativeResponse narrative,
) =>
    ForecastResponse(
      yesterdayVerification: narrative.yesterdayVerification,
      verificationNotes: narrative.verificationNotes,
      skillProfileSummaries: narrative.skillProfileSummaries,
      todayProperties: judgment.todayProperties,
      extendedProperties: judgment.extendedProperties,
      todayNarrative: narrative.todayNarrative,
      whatsappSummary: narrative.whatsappSummary,
    );

/// The judgment call's schema in OpenAI strict dialect — null as a type
/// union rather than a `nullable` flag. Generated from Python's
/// `to_strict_json_schema(GeminiJudgmentResponse)`.
Map<String, Object?> strictJudgmentSchema() => {
      'type': 'object',
      'properties': {
        'today_properties': {
          'type': 'object',
          'properties': {
            'rain_expected': {
              'type': 'string',
            },
            'onset_window': {
              'type': ['string', 'null'],
            },
            'peak_wind_kmh': {
              'type': ['number', 'null'],
            },
            'temp_high_c': {
              'type': 'number',
            },
            'temp_low_c': {
              'type': 'number',
            },
            'rain': {
              'type': 'boolean',
            },
            'onset_hour': {
              'type': ['string', 'null'],
            },
            'precip_mm': {
              'type': ['number', 'null'],
            },
            'rain_probability_pct': {
              'type': ['integer', 'null'],
            },
            'mslp_trend_24h': {
              'type': ['string', 'null'],
            },
            'synoptic_pattern': {
              'type': ['string', 'null'],
            },
            'uv_index_max': {
              'type': ['string', 'null'],
            },
            'air_quality_aqi': {
              'type': ['string', 'null'],
            },
          },
          'required': ['rain_expected', 'onset_window', 'peak_wind_kmh', 'temp_high_c', 'temp_low_c', 'rain', 'onset_hour', 'precip_mm', 'rain_probability_pct', 'mslp_trend_24h', 'synoptic_pattern', 'uv_index_max', 'air_quality_aqi'],
          'additionalProperties': false,
          'description': 'The LLM\'s synthesized, BLENDED call across all models — genuine\nreasoning, not any one model\'s raw number. Only rain_expected, rain,\ntemp_high_c and temp_low_c are required.\n\n`temp_high_low` is deliberately absent. It was a display string the model\nwrote, and it drifted in both value and format; it is now computed from\nthe two numbers here by `models.format_temp_high_low`. Asking a language\nmodel to convert units is asking it to do arithmetic, which this project\ndoes in code.',
        },
        'extended_properties': {
          'type': 'array',
          'items': {
            'type': 'object',
            'properties': {
              'lead_time_days': {
                'type': 'integer',
              },
              'rain': {
                'type': 'boolean',
              },
              'rain_probability_pct': {
                'type': ['integer', 'null'],
              },
            },
            'required': ['lead_time_days', 'rain', 'rain_probability_pct'],
            'additionalProperties': false,
            'description': 'Your own rain call for one day beyond today. Scored against what\nhappens. Omit a lead rather than guess at it.',
          },
        },
      },
      'required': ['today_properties', 'extended_properties'],
      'additionalProperties': false,
      'description': 'What the judgment call returns: the scored fields, and nothing else.\n\nROADMAP item 59 step 3. This half of the split is the point of it — the\nforecaster deciding these numbers reads an 18,400-character prompt\ninstead of a 47,054-character one, and every instruction in it governs a\nnumber.',
    };

/// The rendering call's schema in OpenAI strict dialect. Generated from
/// Python's `to_strict_json_schema(GeminiNarrativeResponse)`.
Map<String, Object?> strictNarrativeSchema() => {
      'type': 'object',
      'properties': {
        'yesterday_verification': {
          'type': 'string',
        },
        'verification_notes': {
          'type': 'array',
          'items': {
            'type': 'object',
            'properties': {
              'lead_time_days': {
                'type': 'integer',
              },
              'note': {
                'type': 'string',
              },
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
              'model': {
                'type': 'string',
              },
              'lead_time_days': {
                'type': 'integer',
              },
              'summary': {
                'type': 'string',
              },
            },
            'required': ['model', 'lead_time_days', 'summary'],
            'additionalProperties': false,
          },
        },
        'today_narrative': {
          'type': 'string',
        },
        'whatsapp_summary': {
          'type': ['string', 'null'],
        },
      },
      'required': ['yesterday_verification', 'verification_notes', 'skill_profile_summaries', 'today_narrative', 'whatsapp_summary'],
      'additionalProperties': false,
      'description': 'What the rendering call returns: prose, and only prose.\n\nTHE SEAM IS THIS CLASS. Nothing here is scored, and there is no field a\nscored value could be written into, so the rendering call cannot revise\nthe forecast however its prompt is later edited. That separation used to\nbe a property of where a paragraph sat inside one string — see\ntests/test_prompt_seam.py, which still checks the weaker claim because a\nprompt can still be edited and this cannot.',
    };

/// WHAT A CALL ASKS FOR — the Dart stand-in for Python's
/// `response_schema: type[T]` parameter.
///
/// Python passes a pydantic model and each provider adapts it with its own
/// dialect adapter. Dart's schemas are hand-written, so the shape carries
/// one map per dialect instead, plus the parser. The alternative — a method
/// per call on every provider — would put the split's shape into three
/// provider classes that have no business knowing about it.
class ResponseShape<T> {
  /// For error messages, so a failure says which call failed.
  final String name;
  final Map<String, Object?> Function() geminiSchema;
  final Map<String, Object?> Function() strictSchema;
  final T Function(Map<String, Object?>) fromJson;

  const ResponseShape({
    required this.name,
    required this.geminiSchema,
    required this.strictSchema,
    required this.fromJson,
  });
}

const judgmentShape = ResponseShape<JudgmentResponse>(
  name: 'judgment',
  geminiSchema: geminiJudgmentSchema,
  strictSchema: strictJudgmentSchema,
  fromJson: JudgmentResponse.fromJson,
);

const narrativeShape = ResponseShape<NarrativeResponse>(
  name: 'narrative',
  geminiSchema: geminiNarrativeSchema,
  strictSchema: strictNarrativeSchema,
  fromJson: NarrativeResponse.fromJson,
);

/// The MERGED shape — what the record stores, and what no call ever sends.
///
/// Production asks for [judgmentShape] then [narrativeShape] and merges the
/// two. This exists because provider mechanics — retries, error envelopes,
/// code-fence stripping, tool-use plumbing — are shape-independent, and
/// exercising them against a whole forecast body is clearer than picking a
/// half. Anything that asserts about the SPLIT must use the two shapes
/// above, or it is testing something production does not do.
const forecastShape = ResponseShape<ForecastResponse>(
  name: 'forecast',
  geminiSchema: geminiForecastSchema,
  strictSchema: strictForecastSchema,
  fromJson: ForecastResponse.fromJson,
);
