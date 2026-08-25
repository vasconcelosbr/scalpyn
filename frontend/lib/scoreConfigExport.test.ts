import assert from "node:assert/strict";
import test from "node:test";

import {
  buildScoreConfigExport,
  scoreConfigExportFilename,
} from "./scoreConfigExport";

test("exports the complete Score Engine payload in the accepted import shape", () => {
  const scoringRules = [
    {
      id: "rule_adx_between",
      indicator: "adx",
      operator: "between",
      min: 20,
      max: 35,
      value: null,
      points: 12,
      category: "market_structure",
    },
    {
      id: "rule_price_above_ema9",
      indicator: "price",
      operator: ">",
      value: "ema9",
      points: 4,
      category: "market_structure",
    },
    {
      id: "rule_breakout_15m",
      indicator: "breakout_distance_pct",
      operator: ">=",
      value: 0.25,
      points: 6,
      category: "signal",
      reference_window: "15m",
    },
  ];

  const payload = buildScoreConfigExport({
    weights: { liquidity: 35, market_structure: 25, momentum: 25, signal: 15 },
    thresholds: { strong_buy: 80, buy: 65, neutral: 40 },
    autoSelectTopN: 5,
    autoSelectMinScore: 80,
    scoringRules,
  });

  assert.deepEqual(payload, {
    weights: { liquidity: 35, market_structure: 25, momentum: 25, signal: 15 },
    thresholds: { strong_buy: 80, buy: 65, neutral: 40 },
    auto_select_top_n: 5,
    auto_select_min_score: 80,
    scoring_rules: scoringRules,
  });

  const serialized = JSON.parse(JSON.stringify(payload));
  assert.equal(serialized.scoring_rules[0].min, 20);
  assert.equal(serialized.scoring_rules[0].max, 35);
  assert.equal(serialized.scoring_rules[1].value, "ema9");
  assert.equal(serialized.scoring_rules[1].points, 4);
  assert.equal(serialized.scoring_rules[1].category, "market_structure");
  assert.equal(serialized.scoring_rules[2].reference_window, "15m");
});

test("creates a stable JSON filename using the export date", () => {
  assert.equal(
    scoreConfigExportFilename(new Date("2026-08-25T18:45:00.000Z")),
    "scalpyn_score_engine_2026-08-25.json",
  );
});
