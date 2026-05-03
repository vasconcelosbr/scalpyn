/**
 * Score-rule aggregation helper (Task #193).
 *
 * The Score Breakdown panel renders two values that historically did not
 * reconcile: the alpha_score (0–100, confidence-weighted) and a "Regras"
 * counter showing nominal points (rule.points × passed). Under the robust
 * engine the actual contribution of each matched rule is
 * `points × indicator_confidence`, so 5 matched rules worth 60 nominal
 * points might only contribute ~21 weighted points → score ≈ 21/120·100.
 *
 * This helper centralises the aggregation so both PipelineAssetTable and
 * RejectedAssetTable show numbers that mathematically reconcile with the
 * displayed score. When the backend ships `weighted_points` per matched
 * rule (robust path), `weightedEarned` is the confidence-weighted sum and
 * `hasRobust` is true. When the backend cannot produce a robust score
 * (legacy snapshots, critical-gate / confidence-gate rejections), the
 * helper falls back to nominal points and `hasRobust` is false.
 */

import type { ScoreRule } from '@/components/watchlist/PipelineAssetTable';

export interface ScoreRulesSummary {
  /** Number of positive rules whose condition matched. */
  matchedCount: number;
  /** Total number of positive rules considered. */
  positiveCount: number;
  /** Sum of `points_possible` over all positive rules (denominator). */
  totalPossible: number;
  /** Σ points_awarded for matched positive rules (legacy / nominal). */
  nominalEarned: number;
  /**
   * Σ weighted_points for matched positive rules. Falls back to
   * `nominalEarned` when the robust engine did not enrich any rule.
   */
  weightedEarned: number;
  /**
   * True when at least one matched positive rule carries a backend-provided
   * `weighted_points` field. False means the backend could not produce a
   * robust contribution → UI should render nominal numbers + a "(legacy)"
   * marker so the mismatch with the score does not look like a bug.
   */
  hasRobust: boolean;
  /** Σ points_awarded for fired penalty rules (negative number). */
  totalPenalties: number;
}

export function summarizeScoreRules(rules: ScoreRule[]): ScoreRulesSummary {
  const positiveRules = rules.filter(
    (r) => (r.type ?? 'positive') !== 'penalty',
  );
  const matched = positiveRules.filter((r) => r.passed);
  const totalPossible = positiveRules.reduce(
    (s, r) => s + (r.points_possible || 0),
    0,
  );
  const nominalEarned = matched.reduce(
    (s, r) => s + (r.points_awarded || 0),
    0,
  );
  // A rule is "robust-enriched" when the backend attached a numeric
  // weighted_points. We treat the breakdown as robust as soon as any
  // matched rule carries that field — partial enrichment shouldn't
  // happen in practice (the backend either runs the robust engine for
  // the whole asset or skips it), but if it does, summing the
  // weighted_points we have is still strictly closer to the truth than
  // the nominal sum.
  const enriched = matched.filter(
    (r) => typeof r.weighted_points === 'number' && Number.isFinite(r.weighted_points),
  );
  const hasRobust = enriched.length > 0;
  const weightedEarned = hasRobust
    ? enriched.reduce((s, r) => s + (r.weighted_points as number), 0)
    : nominalEarned;
  const totalPenalties = rules
    .filter((r) => r.type === 'penalty')
    .reduce((s, r) => s + (r.points_awarded || 0), 0);

  return {
    matchedCount: matched.length,
    positiveCount: positiveRules.length,
    totalPossible,
    nominalEarned,
    weightedEarned,
    hasRobust,
    totalPenalties,
  };
}

/** Format a confidence value (0–1) as e.g. "0.42" for chip tooltips. */
export function fmtConfidence(confidence: number | undefined | null): string {
  if (confidence == null || !Number.isFinite(confidence)) return '—';
  return confidence.toFixed(2);
}
