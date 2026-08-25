export interface ScoreConfigExportRule {
  id: string;
  indicator: string;
  operator: string;
  value?: number | string | boolean | null;
  min?: number | null;
  max?: number | null;
  points: number;
  category: string;
  reference_window?: string;
}

export interface ScoreConfigExportInput {
  weights: Record<string, number>;
  thresholds: Record<string, number>;
  autoSelectTopN: number;
  autoSelectMinScore: number;
  scoringRules: ScoreConfigExportRule[];
}

export interface ScoreConfigExportPayload {
  weights: Record<string, number>;
  thresholds: Record<string, number>;
  auto_select_top_n: number;
  auto_select_min_score: number;
  scoring_rules: ScoreConfigExportRule[];
}

export function buildScoreConfigExport({
  weights,
  thresholds,
  autoSelectTopN,
  autoSelectMinScore,
  scoringRules,
}: ScoreConfigExportInput): ScoreConfigExportPayload {
  return {
    weights,
    thresholds,
    auto_select_top_n: autoSelectTopN,
    auto_select_min_score: autoSelectMinScore,
    scoring_rules: scoringRules,
  };
}

export function scoreConfigExportFilename(date = new Date()): string {
  return `scalpyn_score_engine_${date.toISOString().slice(0, 10)}.json`;
}
