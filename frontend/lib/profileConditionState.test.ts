import assert from "node:assert/strict";
import test from "node:test";

import {
  isProfileComparisonCondition,
  normalizeProfileRuleCondition,
  profileConditionManualUpdates,
  profileConditionPrimaryIndicator,
  serializeProfileEditorConfig,
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

test("comparison round trip preserves both indicator operands and compatibility field", () => {
  const input = {
    id: "cmp1",
    type: "comparison",
    field: "ema21",
    left: "ema21",
    operator: ">",
    right: "ema50",
    timeframe: "1h",
    period: 21,
    required: true,
  };

  const state = normalizeProfileRuleCondition(input, "fallback");

  assert.equal(isProfileComparisonCondition(state), true);
  assert.equal(profileConditionPrimaryIndicator(state), "ema21");
  assert.deepEqual(serializeProfileRuleCondition(state), input);
  assert.equal("value" in state, false);
});

test("loaded editor identity is not persisted and missing boolean value stays missing", () => {
  const state = normalizeProfileRuleCondition({
    type: "boolean",
    indicator: "ema9_gt_ema21",
    operator: "is_false",
  }, "cond_loaded_block_0_0");

  assert.equal(state.id, "cond_loaded_block_0_0");
  assert.equal("value" in state, false);
  assert.deepEqual(serializeProfileRuleCondition(state), {
    type: "boolean",
    indicator: "ema9_gt_ema21",
    operator: "is_false",
  });
});

test("new and persisted condition identities remain stable", () => {
  const created = normalizeProfileRuleCondition({
    id: "cond_1788904023177",
    type: "threshold",
    indicator: "rsi",
    operator: ">",
    value: 60,
  });
  const persisted = normalizeProfileRuleCondition({
    id: "contract-rule-1",
    type: "threshold",
    indicator: "adx",
    operator: ">=",
    value: 20,
  });

  assert.equal(serializeProfileRuleCondition(created).id, "cond_1788904023177");
  assert.equal(serializeProfileRuleCondition(persisted).id, "contract-rule-1");
});

test("profile save removes only editor identities across every execution section", () => {
  const config = {
    filters: { conditions: [{ id: "cond_loaded_filter_0", field: "rsi", operator: ">", value: 50 }] },
    signals: { conditions: [{ id: "canonical-signal", field: "adx", operator: ">", value: 20 }] },
    block_rules: {
      blocks: [{
        id: "block_loaded_0",
        name: "legacy block",
        conditions: [{
          id: "cond_loaded_block_0_0",
          type: "boolean",
          indicator: "ema9_gt_ema21",
          operator: "is_false",
        }],
      }],
    },
    entry_triggers: {
      conditions: [{ id: "cond_1788904023177", indicator: "volume_spike", operator: ">=", value: 1 }],
    },
  };

  assert.deepEqual(serializeProfileEditorConfig(config), {
    filters: { conditions: [{ field: "rsi", operator: ">", value: 50 }] },
    signals: { conditions: [{ id: "canonical-signal", field: "adx", operator: ">", value: 20 }] },
    block_rules: {
      blocks: [{
        name: "legacy block",
        conditions: [{
          type: "boolean",
          indicator: "ema9_gt_ema21",
          operator: "is_false",
        }],
      }],
    },
    entry_triggers: {
      conditions: [{ id: "cond_1788904023177", indicator: "volume_spike", operator: ">=", value: 1 }],
    },
  });
});

test("manual filter edits do not inject score metadata", () => {
  assert.deepEqual(
    profileConditionManualUpdates({ value: 800001 }, false),
    { value: 800001 },
  );
  assert.deepEqual(
    profileConditionManualUpdates({ value: 800001 }, true),
    { value: 800001, rule_id: undefined, points: 0, category: undefined },
  );
});
