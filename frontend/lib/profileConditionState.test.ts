import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeProfileRuleCondition,
  serializeProfileRuleCondition,
  updateProfileRuleCondition,
} from "./profileConditionState";

const IDS = [
  "adx_acceleration", "adx_slope_3", "macd_hist_slope_3", "macd_hist_slope_5",
  "rsi_slope_3", "entry_exhaustion_score", "rsi_6", "breakout_distance_pct",
];

test("indicator condition round trip preserves contractual and provenance metadata", () => {
  for (const indicator of IDS) {
    const input = {
      id: `condition_${indicator}`,
      type: "threshold",
      indicator,
      operator: ">=",
      value: 1.25,
      min: -1,
      max: 2,
      period: indicator === "rsi_6" ? 6 : 3,
      timeframe: "5m",
      required: true,
      enabled: true,
      reference_window: indicator === "breakout_distance_pct" ? "15m" : undefined,
      source: "ohlcv",
      source_provider: "binance",
      provider_policy: "primary_only",
      max_age_seconds: 420,
      custom_contract_marker: { immutable: true },
    };
    const state = normalizeProfileRuleCondition(input, "fallback");
    assert.deepEqual(serializeProfileRuleCondition(state), input);
  }
});

test("UI edits replace only explicitly changed fields", () => {
  const original = normalizeProfileRuleCondition({
    id: "c1", type: "threshold", indicator: "adx_slope_3", operator: "<=",
    value: 0, source: "ohlcv", provider_policy: "primary_only", required: true,
  });
  const edited = updateProfileRuleCondition(original, { value: -0.1 });
  assert.equal(edited.value, -0.1);
  assert.equal(edited.indicator, "adx_slope_3");
  assert.equal(edited.source, "ohlcv");
  assert.equal(edited.provider_policy, "primary_only");
  assert.equal(edited.required, true);
});

test("normalization does not rewrite existing boolean or between values", () => {
  const booleanCondition = normalizeProfileRuleCondition({
    id: "b1", type: "boolean", indicator: "di_trend", operator: "is_false", value: true,
  });
  const betweenCondition = normalizeProfileRuleCondition({
    id: "r1", type: "threshold", indicator: "rsi", operator: "between", value: 42, min: 30, max: 60,
  });
  assert.equal(booleanCondition.value, true);
  assert.equal(betweenCondition.value, 42);
});
