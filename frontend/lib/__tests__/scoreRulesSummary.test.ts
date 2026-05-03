import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { summarizeScoreRules, fmtConfidence } from '../scoreRulesSummary';
import type { ScoreRule } from '@/components/watchlist/PipelineAssetTable';

// Minimal helper so each fixture stays compact.
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
    weighted_points: over.weighted_points,
    indicator_confidence: over.indicator_confidence,
  };
}

describe('summarizeScoreRules — robust path (Task #193)', () => {
  it('uses confidence-weighted sum so Score and Regras reconcile', () => {
    // HYPE_USDT-style fixture: 5 of 11 positive rules matched, totalling 60
    // nominal pts of 120, but the robust engine's confidence-weighted sum
    // is ~21.2 → score 17.7.
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true,  points_awarded: 20, points_possible: 20, weighted_points: 8.4,  indicator_confidence: 0.42 }),
      rule({ id: 'b', passed: true,  points_awarded: 15, points_possible: 15, weighted_points: 6.0,  indicator_confidence: 0.40 }),
      rule({ id: 'c', passed: true,  points_awarded: 10, points_possible: 10, weighted_points: 3.5,  indicator_confidence: 0.35 }),
      rule({ id: 'd', passed: true,  points_awarded: 10, points_possible: 10, weighted_points: 2.5,  indicator_confidence: 0.25 }),
      rule({ id: 'e', passed: true,  points_awarded: 5,  points_possible: 5,  weighted_points: 0.8,  indicator_confidence: 0.16 }),
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
    assert.ok(s.hasRobust);
    // 8.4 + 6.0 + 3.5 + 2.5 + 0.8 = 21.2
    assert.ok(Math.abs(s.weightedEarned - 21.2) < 1e-9, `weightedEarned=${s.weightedEarned}`);
    // Score reconciliation: 21.2 / 120 * 100 = 17.66… ≈ 17.7
    const score = (s.weightedEarned / s.totalPossible) * 100;
    assert.ok(Math.abs(score - 17.7) < 0.1, `score=${score}`);
  });

  it('falls back to nominal when no rule has weighted_points (legacy snapshot)', () => {
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true,  points_awarded: 20, points_possible: 20 }),
      rule({ id: 'b', passed: false, points_awarded: 0,  points_possible: 30 }),
    ];
    const s = summarizeScoreRules(rules);
    assert.equal(s.hasRobust, false);
    assert.equal(s.nominalEarned, 20);
    assert.equal(s.weightedEarned, 20);
    assert.equal(s.totalPossible, 50);
  });

  it('penalty rules contribute to totalPenalties, not totalPossible', () => {
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true,  points_awarded: 25, points_possible: 25, weighted_points: 12.5, indicator_confidence: 0.5 }),
      rule({ id: 'p', passed: true,  points_awarded: -10, points_possible: -10, type: 'penalty' }),
      rule({ id: 'p2', passed: false, points_awarded: 0, points_possible: -5,  type: 'penalty' }),
    ];
    const s = summarizeScoreRules(rules);
    assert.equal(s.positiveCount, 1);
    assert.equal(s.totalPossible, 25);
    assert.equal(s.totalPenalties, -10);
    assert.equal(s.weightedEarned, 12.5);
  });

  it('handles empty rule lists without dividing by zero', () => {
    const s = summarizeScoreRules([]);
    assert.equal(s.matchedCount, 0);
    assert.equal(s.positiveCount, 0);
    assert.equal(s.totalPossible, 0);
    assert.equal(s.weightedEarned, 0);
    assert.equal(s.hasRobust, false);
  });

  it('treats partial robust enrichment as robust (sums what is available)', () => {
    // Defensive: backend should never send partial enrichment (all-or-nothing
    // per asset), but if it does, summing the weighted_points present is
    // strictly closer to the score than dropping back to nominal.
    const rules: ScoreRule[] = [
      rule({ id: 'a', passed: true, points_awarded: 20, points_possible: 20, weighted_points: 9.5, indicator_confidence: 0.475 }),
      rule({ id: 'b', passed: true, points_awarded: 10, points_possible: 10 /* no weighted_points */ }),
    ];
    const s = summarizeScoreRules(rules);
    assert.equal(s.hasRobust, true);
    assert.equal(s.weightedEarned, 9.5);
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
