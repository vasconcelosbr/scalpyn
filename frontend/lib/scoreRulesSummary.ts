/**
 * Score-rule aggregation helper (Task #211).
 *
 * Scoring is now deterministic: matched rules award their full configured
 * points (no confidence weighting). `awarded_points` equals `points_possible`
 * for every matched rule. Confidence is preserved as metadata for tooltips
 * and `can_trade` gating but does NOT influence the score.
 *
 * When the backend ships `awarded_points` per matched rule (enriched path),
 * `awardedEarned` is the sum and `hasEnriched` is true. When the backend
 * cannot produce enrichment (legacy snapshots), the helper falls back to
 * nominal `points_awarded` and `hasEnriched` is false.
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
   * Σ awarded_points for matched positive rules. Falls back to
   * `nominalEarned` when the engine did not enrich any rule.
   */
  awardedEarned: number;
  /**
   * True when at least one matched positive rule carries a backend-provided
   * `awarded_points` field. False means the backend could not produce
   * enrichment → UI should render nominal numbers + a "(legacy)" marker.
   */
  hasEnriched: boolean;
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
  const enriched = matched.filter(
    (r) => typeof r.awarded_points === 'number' && Number.isFinite(r.awarded_points),
  );
  const hasEnriched = enriched.length > 0;
  const awardedEarned = hasEnriched
    ? enriched.reduce((s, r) => s + (r.awarded_points as number), 0)
    : nominalEarned;
  const totalPenalties = rules
    .filter((r) => r.type === 'penalty')
    .reduce((s, r) => s + (r.points_awarded || 0), 0);

  return {
    matchedCount: matched.length,
    positiveCount: positiveRules.length,
    totalPossible,
    nominalEarned,
    awardedEarned,
    hasEnriched,
    totalPenalties,
  };
}

/** Format a confidence value (0–1) as e.g. "0.42" for chip tooltips. */
export function fmtConfidence(confidence: number | undefined | null): string {
  if (confidence == null || !Number.isFinite(confidence)) return '—';
  return confidence.toFixed(2);
}
