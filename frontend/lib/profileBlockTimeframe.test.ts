import assert from "node:assert/strict";
import test from "node:test";
import { indicatorOptionsForSection } from "./indicatorCatalog";
import { validateExecutionSections } from "./profileImportPreflight";
import { blockConditionHasTimeframe, normalizeProfileRuleCondition, prepareProfileBlockRuleIdentities,
  profileBooleanSelection, profileTemporalIdentity, serializeProfileEditorConfig,
  setProfileBlockTimeframe, withProfileCalculationIdentities } from "./profileConditionState";

const policies = withProfileCalculationIdentities({ ohlcv: {
  allowed_source_providers: ["gate.io"], provider_policy_id: "spot_gate_closed_ohlcv_v1",
  timeframe: "5m", max_age_seconds: 360, candle_policy: "CLOSED_ONLY",
} }, { adx: { period: 10 }, macd: { fast: 8, slow: 21, signal: 5 }, bollinger: { period: 18, deviation: 2.5 } });

test("higher highs exposes both boolean choices and preserves false through save/reopen", () => {
  const option = indicatorOptionsForSection("block_rules").find(x => x.id === "higher_highs_5");
  assert.equal(option?.kind, "boolean");
  for (const value of [true, false]) {
    const condition = { indicator: "higher_highs_5", operator: value ? "is_true" : "is_false", value, type: "boolean" };
    const config = { default_timeframe: "15m", block_rules: { blocks: [{ conditions: [condition] }] } };
    const prepared = prepareProfileBlockRuleIdentities(config, policies);
    assert.deepEqual(prepared.issues, []);
    const saved = serializeProfileEditorConfig(prepared.config);
    const reopened = normalizeProfileRuleCondition(saved.block_rules.blocks[0].conditions[0]);
    assert.equal(reopened.value, value);
    assert.equal(profileBooleanSelection(reopened), String(value));
    assert.equal(reopened.timeframe, "15m");
    assert.deepEqual(validateExecutionSections(saved), []);
  }
  assert.equal(profileBooleanSelection({ operator: "==", value: false }), "false");
  assert.equal(profileBooleanSelection({ operator: "!=", value: false }), "true");
});

test("block period survives save/reopen with RSI 6 independent of the profile and flow", () => {
  const condition = { id: "rsi", indicator: "rsi_6", operator: ">", value: 65, type: "threshold", source: "ohlcv", timeframe: "5m", period: 6 };
  const flow = { indicator: "taker_ratio", source: "live_trade_flow", window_seconds: 300, operator: "<", value: 0.5 };
  const original: { id: string; conditions: Record<string, any>[] } = { id: "b", conditions: [condition, flow] };
  const selected = setProfileBlockTimeframe(original, "1m", "5m");
  assert.equal(original.conditions[0].timeframe, "5m");
  assert.deepEqual(selected.conditions[1], flow);
  const config = { default_timeframe: "5m", block_rules: { blocks: [{ ...selected, conditions: [selected.conditions[0]] }] } };
  const prepared = prepareProfileBlockRuleIdentities(config, policies);
  assert.deepEqual(prepared.issues, []);
  const saved = serializeProfileEditorConfig(prepared.config);
  assert.equal(saved.default_timeframe, "5m");
  assert.equal(saved.block_rules.blocks[0].conditions[0].timeframe, "1m");
  assert.equal(saved.block_rules.blocks[0].conditions[0].period, 6);
  assert.match(profileTemporalIdentity(saved.block_rules.blocks[0].conditions[0], policies, "5m").label, /1m/);
  assert.deepEqual(prepareProfileBlockRuleIdentities(saved, policies, saved).config, saved);
});

test("new M15 block conditions inherit the block/profile over the L3 source policy", () => {
  const indicators = ["adx_slope_3", "macd_hist_slope_3", "bb_upper_distance_pct"];
  const conditions = indicators.map(indicator => ({ indicator, type: "threshold", operator: ">", value: 0 }));
  const config = { default_timeframe: "15m", block_rules: { blocks: [{ conditions }] } };
  const result = prepareProfileBlockRuleIdentities(config, policies);
  assert.deepEqual(result.issues, []);
  for (const c of result.config.block_rules.blocks[0].conditions as Record<string, any>[]) {
    assert.equal(c.timeframe, "15m");
    assert.equal(c.candle_policy, "CLOSED_ONLY");
    assert.ok(blockConditionHasTimeframe(c));
  }
  const [adx, macd, bb] = result.config.block_rules.blocks[0].conditions as Record<string, any>[];
  assert.equal(adx.period, 10);
  assert.deepEqual(macd.parameters, { fast: 8, slow: 21, signal: 5 });
  assert.equal(bb.period, 18);
  assert.deepEqual(bb.parameters, { deviation: 2.5 });
});

test("untouched legacy block stays unchanged and comparison operands follow an explicit edit", () => {
  const legacy = { default_timeframe: "15m", block_rules: { blocks: [{ conditions: [{ indicator: "adx_slope_3", operator: "<", value: 0 }] }] } };
  assert.deepEqual(prepareProfileBlockRuleIdentities(legacy, policies, legacy).config, legacy);
  const comparison = { type: "comparison", left: "rsi_6", right: "rsi", timeframe: "5m", resolved_operands: {
    left: { indicator: "rsi_6", source: "ohlcv", timeframe: "5m", period: 6 },
    right: { indicator: "rsi", source: "ohlcv", timeframe: "15m", period: 14 },
  } };
  const updated = setProfileBlockTimeframe({ conditions: [comparison] }, "1m", "5m");
  assert.equal(updated.conditions[0].resolved_operands.left.timeframe, "1m");
  assert.equal(updated.conditions[0].resolved_operands.right.timeframe, "1m");
  assert.equal(updated.conditions[0].resolved_operands.right.period, 14);
});
