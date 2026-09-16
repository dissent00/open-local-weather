import 'package:olw_core/olw_core.dart';
import 'package:test/test.dart';

/// `reviewPromptPayload` mirrors `_review_prompt_payload` in pipeline.py.
/// The Python function is private, so no vector pins it; this test pins the
/// key names as the Python spells them, and that the skill table stays out.
void main() {
  test('carries the six keys the Python sends, and no cells', () {
    final review = WeeklyReview(
      periodStart: DateTime(2026, 9, 1),
      periodEnd: DateTime(2026, 9, 15),
      daysWithPredictions: 15,
      daysVerified: 14,
      cells: const [],
      findings: const [
        Finding(
          kind: 'bias',
          claim: 'gfs runs warm',
          evidence: '-0.98 C over 36 checks',
          confidence: 'high',
          checks: 36,
        ),
      ],
      dataSufficiency: 'adequate',
    );

    final payload = reviewPromptPayload(review);

    expect(payload.keys.toList(), [
      'period_start',
      'period_end',
      'days_with_predictions',
      'days_verified',
      'data_sufficiency',
      'findings',
    ]);
    expect(payload['period_start'], '2026-09-01');
    expect(payload['period_end'], '2026-09-15');
    expect(payload, isNot(contains('cells')));

    final findings = payload['findings'] as List;
    expect(findings, hasLength(1));
    expect((findings.single as Map).keys.toList(),
        ['kind', 'claim', 'evidence', 'confidence', 'checks']);
    expect((findings.single as Map)['checks'], 36);
  });
}
