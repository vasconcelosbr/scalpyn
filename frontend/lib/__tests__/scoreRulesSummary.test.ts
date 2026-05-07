import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { summarizeScoreRules, fmtConfidence } from '../scoreRulesSummary';
import type { ScoreRule } from '@/components/watchlist/PipelineAssetTable';

function rule(over: Partial<ScoreRule>): ScoreRule {
  return {
    id: over.id ?? 'r',
    indicator: over.indicator ?? 'rsi',
    label: over.label ?? 'RSI',
    operator: over.operator ?? '<=',
    target_value: over.target_value ?? null,
    min: over.min ?? null,
    max: over.max ?? null,
    actual_value: over.actual_value ?? null,
    passed: over.passed ?? false,
    points_awarded: over.points_awarded ?? 0,
    points_possible: over.points_possible ?? 0,
    type: over.type ?? 'positive',
    condition_text: over.condition_text ?? '',
    category: over.category ?? 'momentum',
    awarded_points: over.awarded_points,
    indicator_confidence: over.indicator_confidence,
    data_available: over.data_available,
  };
}

describe('summarizeScoreRules — deterministic scoring (Task #211)', () => {
  it('awarded_points equals full configured points (no confidence weighting)', () => {
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true,  points_awarded: 20, points_possible: 20, awarded_points: 20, indicator_confidence: 0.42, data_available: true }),
      rule({ id: 'b', passed: true,  points_awarded: 15, points_possible: 15, awarded_points: 15, indicator_confidence: 0.40, data_available: true }),
      rule({ id: 'c', passed: true,  points_awarded: 10, points_possible: 10, awarded_points: 10, indicator_confidence: 0.35, data_available: true }),
      rule({ id: 'd', passed: true,  points_awarded: 10, points_possible: 10, awarded_points: 10, indicator_confidence: 0.25, data_available: true }),
      rule({ id: 'e', passed: true,  points_awarded: 5,  points_possible: 5,  awarded_points: 5,  indicator_confidence: 0.16, data_available: true }),
      rule({ id: 'f', passed: false, points_awarded: 0,  points_possible: 20 }),
      rule({ id: 'g', passed: false, points_awarded: 0,  points_possible: 15 }),
      rule({ id: 'h', passed: false, points_awarded: 0,  points_possible: 10 }),
      rule({ id: 'i', passed: false, points_awarded: 0,  points_possible: 8  }),
      rule({ id: 'j', passed: false, points_awarded: 0,  points_possible: 5  }),
      rule({ id: 'k', passed: false, points_awarded: 0,  points_possible: 2  }),
    ];

    const s = summarizeScoreRules(rules);
    assert.equal(s.matchedCount, 5);
    assert.equal(s.positiveCount, 11);
    assert.equal(s.totalPossible, 120);
    assert.equal(s.nominalEarned, 60);
    assert.ok(s.hasEnriched);
    assert.equal(s.awardedEarned, 60);
    const score = (s.awardedEarned / s.totalPossible) * 100;
    assert.equal(score, 50);
  });

  it('falls back to nominal when no rule has awarded_points (legacy snapshot)', () => {
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true,  points_awarded: 20, points_possible: 20 }),
      rule({ id: 'b', passed: false, points_awarded: 0,  points_possible: 30 }),
    ];
    const s = summarizeScoreRules(rules);
    assert.equal(s.hasEnriched, false);
    assert.equal(s.nominalEarned, 20);
    assert.equal(s.awardedEarned, 20);
    assert.equal(s.totalPossible, 50);
  });

  it('penalty rules contribute to totalPenalties, not totalPossible', () => {
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true,  points_awarded: 25, points_possible: 25, awarded_points: 25, indicator_confidence: 0.5, data_available: true }),
      rule({ id: 'p', passed: true,  points_awarded: -10, points_possible: -10, type: 'penalty' }),
      rule({ id: 'p2', passed: false, points_awarded: 0, points_possible: -5,  type: 'penalty' }),
    ];
    const s = summarizeScoreRules(rules);
    assert.equal(s.positiveCount, 1);
    assert.equal(s.totalPossible, 25);
    assert.equal(s.totalPenalties, -10);
    assert.equal(s.awardedEarned, 25);
  });

  it('handles empty rule lists without dividing by zero', () => {
    const s = summarizeScoreRules([]);
    assert.equal(s.matchedCount, 0);
    assert.equal(s.positiveCount, 0);
    assert.equal(s.totalPossible, 0);
    assert.equal(s.awardedEarned, 0);
    assert.equal(s.hasEnriched, false);
  });

  it('treats partial enrichment as enriched (sums what is available)', () => {
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true, points_awarded: 20, points_possible: 20, awarded_points: 20, indicator_confidence: 0.475, data_available: true }),
      rule({ id: 'b', passed: true, points_awarded: 10, points_possible: 10 }),
    ];
    const s = summarizeScoreRules(rules);
    assert.equal(s.hasEnriched, true);
    assert.equal(s.awardedEarned, 20);
    assert.equal(s.nominalEarned, 30);
  });
});

describe('fmtConfidence', () => {
  it('formats 0–1 floats to two decimals', () => {
    assert.equal(fmtConfidence(0.4234), '0.42');
    assert.equal(fmtConfidence(1), '1.00');
    assert.equal(fmtConfidence(0), '0.00');
  });
  it('returns em dash for null/NaN/undefined', () => {
    assert.equal(fmtConfidence(null), '—');
    assert.equal(fmtConfidence(undefined), '—');
    assert.equal(fmtConfidence(NaN), '—');
  });
});
