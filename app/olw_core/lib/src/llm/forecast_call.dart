// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 dissent00

/// What the reader is shown where the forecast would have been.
///
/// NOT EMPTY, and not an apology. An empty narrative renders as a missing
/// section — the page keeps its stat tiles and simply looks short, which
/// reads as a forecast that had nothing to say rather than one whose
/// write-up failed. Mirrors Python's NARRATIVE_UNAVAILABLE_MARKDOWN.
const String narrativeUnavailableMarkdown = '''## Write-up unavailable

The forecast below could not be written up this issuance: the model call that turns the day's figures into prose did not complete. The figures themselves were produced normally and are shown as usual — they are the same numbers this forecast is scored on.

There is no discussion, no extended outlook and no hazard section for this issuance. Check a later one.''';

/// How the model's own call ended — stored by the caller beside the run.
/// Mirrors the pipeline's `LLM_CALL_*` (upstream item 189).
const String llmCallServed = 'served';
const String llmCallNotConfigured = 'not configured';

/// `yesterday_verification` on a run: the record scores yesterday itself,
/// and the model is no longer asked to narrate it (upstream items 147, 189).
const String verificationNotWritten =
    'Not written this run; the record scores yesterday itself.';

/// THE PENDING MARKER'S WORDS — upstream item 189. `degradationNarrative`
/// used to mean the rendering call failed after the judgment succeeded; it
/// now means the write-up has not been asked for yet, because the forecast
/// is stored before any prose is. The caller removes it when
/// `writeUpForecast` lands. Not an apology.
const String narrativePendingSummary =
    "Today's figures are decided. The written discussion follows when a "
    'model answers; the numbers are the same ones this forecast is scored on.';
const String narrativePendingDetail =
    'The write-up is asked for after the forecast is stored (upstream '
    'ROADMAP item 189), by writeUpForecast; until it lands the run carries '
    'the placeholder.';
