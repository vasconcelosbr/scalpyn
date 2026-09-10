import assert from "node:assert/strict";
import test from "node:test";

import {
  isProfileComparisonCondition,
  normalizeProfileRuleCondition,
  prepareProfileBlockRuleIdentities,
  prepareProfileEntryTriggerIdentities,
  profileConditionManualUpdates,
  profileConditionPrimaryIndicator,
  profileSourcePoliciesForEditor,
  serializeProfileEditorConfig,
  serializeProfileRuleCondition,
  updateProfileRuleCondition,
  withoutProfileFeatureIdentity,
} from "./profileConditionState";

const SOURCE_POLICIES = {
  ohlcv: {
    allowed_source_providers: ["gate.io"],
    provider_policy_id: "spot_gate_closed_ohlcv_v1",
    max_age_seconds: 360,
    candle_policy: "CLOSED_ONLY",
  },
  live_trade_flow: {
    allowed_source_providers: ["gate_trades_ws_spot"],
    provider_policy_id: "spot_gate_trade_flow_v1",
    max_age_seconds: 30,
    window_seconds: 60,
  },
  live_order_book: {
    allowed_source_providers: ["gate_orderbook_ws_spot"],
    provider_policy_id: "spot_gate_order_book_v1",
    max_age_seconds: 15,
    snapshot: true,
  },
  decision_context: {
    allowed_source_providers: ["market_metadata", "robust_score"],
    provider_policy_id: "spot_decision_context_v1",
    max_age_seconds: 90,
  },
};

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

test("changing an Entry Trigger feature drops stale identity but keeps its timeframe choice", () => {
  const condition = withoutProfileFeatureIdentity({
    id: "entry-1", indicator: "rsi", operator: ">", value: 50,
    source: "ohlcv", source_provider: "gate.io", provider_policy_id: "ohlcv-v1",
    max_age_seconds: 360, timeframe: "15m", candle_policy: "CLOSED_ONLY",
    period: 14, resolved_operands: { left: { indicator: "rsi" } },
  });

  assert.equal(condition.timeframe, "15m");
  assert.equal("source" in condition, false);
  assert.equal("source_provider" in condition, false);
  assert.equal("provider_policy_id" in condition, false);
  assert.equal("period" in condition, false);
  assert.equal("resolved_operands" in condition, false);
});

test("new Entry Trigger receives governed OHLCV identity before save", () => {
  const prepared = prepareProfileEntryTriggerIdentities({
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [{
        id: "entry-new", type: "threshold", indicator: "rsi",
        operator: "between", min: 52, max: 72, required: true, enabled: true,
      }],
    },
  }, SOURCE_POLICIES);

  assert.deepEqual(prepared.issues, []);
  assert.deepEqual(
    prepared.config.entry_triggers.conditions[0],
    {
      id: "entry-new", type: "threshold", indicator: "rsi",
      operator: "between", min: 52, max: 72, required: true, enabled: true,
      source: "ohlcv", source_provider: "gate.io",
      provider_policy_id: "spot_gate_closed_ohlcv_v1",
      max_age_seconds: 360, timeframe: "5m", candle_policy: "CLOSED_ONLY",
      period: 14,
    },
  );
});

test("comparison Entry Trigger receives an independently resolved identity per operand", () => {
  const prepared = prepareProfileEntryTriggerIdentities({
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [{
        id: "entry-comparison", type: "comparison", left: "price",
        operator: ">", right: "ema9", required: false, enabled: true,
      }],
    },
  }, SOURCE_POLICIES);

  assert.deepEqual(prepared.issues, []);
  const condition = prepared.config.entry_triggers.conditions[0] as Record<string, any>;
  assert.equal(condition.source, "ohlcv");
  assert.equal(condition.timeframe, "5m");
  assert.deepEqual(condition.resolved_operands.left, {
    indicator: "price", source: "ohlcv", source_provider: "gate.io",
    provider_policy_id: "spot_gate_closed_ohlcv_v1", max_age_seconds: 360,
    timeframe: "5m", candle_policy: "CLOSED_ONLY",
  });
  assert.deepEqual(condition.resolved_operands.right, {
    indicator: "ema9", source: "ohlcv", source_provider: "gate.io",
    provider_policy_id: "spot_gate_closed_ohlcv_v1", max_age_seconds: 360,
    timeframe: "5m", candle_policy: "CLOSED_ONLY", period: 9,
  });
});

test("live Entry Trigger identity comes from the configured source policy", () => {
  const prepared = prepareProfileEntryTriggerIdentities({
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [{
        id: "entry-flow", type: "threshold", indicator: "volume_delta",
        operator: ">", value: 0, required: true, enabled: true,
      }],
    },
  }, SOURCE_POLICIES);

  assert.deepEqual(prepared.issues, []);
  assert.deepEqual(
    prepared.config.entry_triggers.conditions[0],
    {
      id: "entry-flow", type: "threshold", indicator: "volume_delta",
      operator: ">", value: 0, required: true, enabled: true,
      source: "live_trade_flow", source_provider: "gate_trades_ws_spot",
      provider_policy_id: "spot_gate_trade_flow_v1", max_age_seconds: 30,
      window_seconds: 60,
    },
  );
});

test("editor reports incomplete governed policies instead of sending an invalid write", () => {
  const prepared = prepareProfileEntryTriggerIdentities({
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [{
        id: "entry-new", type: "threshold", indicator: "rsi",
        operator: ">", value: 50, required: true, enabled: true,
      }],
    },
  }, {});

  assert.deepEqual(prepared.issues, [
    "entry_triggers.conditions[0].source_provider",
    "entry_triggers.conditions[0].provider_policy_id",
    "entry_triggers.conditions[0].max_age_seconds",
    "entry_triggers.conditions[0].candle_policy",
  ]);
});

test("MTF profiles select their layer policies and standard profiles select L3 policies", () => {
  const spotEngine = {
    scanner: {
      l3_v3_provenance_resolver: { source_policies: { decision_context: { provider_policy_id: "standard" } } },
      multilayer_contract: {
        layers: {
          L1: { source_policies: { ohlcv: { provider_policy_id: "l1" } } },
          L2: { source_policies: { ohlcv: { provider_policy_id: "l2" } } },
        },
      },
    },
  };

  assert.equal(
    profileSourcePoliciesForEditor(spotEngine, "MTF_LAYER", "primary_filter").ohlcv?.provider_policy_id,
    "l1",
  );
  assert.equal(
    profileSourcePoliciesForEditor(spotEngine, "MTF_LAYER", "score_engine").ohlcv?.provider_policy_id,
    "l2",
  );
  assert.equal(
    profileSourcePoliciesForEditor(spotEngine, "STANDARD", "acquisition_queue").decision_context?.provider_policy_id,
    "standard",
  );
  assert.deepEqual(
    profileSourcePoliciesForEditor(spotEngine, "MTF_LAYER", "unknown_role"),
    {},
  );
});

test("standard profiles merge resolver policies with the materialized L3 source contract", () => {
  const policies = profileSourcePoliciesForEditor({
    scanner: {
      l3_v3_provenance_resolver: {
        source_policies: {
          decision_context: { provider_policy_id: "resolver-decision" },
        },
      },
      multilayer_contract: {
        layers: {
          L3: {
            source_policies: {
              ohlcv: { provider_policy_id: "layer-ohlcv" },
            },
          },
        },
      },
    },
  }, "STANDARD", "acquisition_queue");

  assert.equal(policies.ohlcv?.provider_policy_id, "layer-ohlcv");
  assert.equal(policies.decision_context?.provider_policy_id, "resolver-decision");
});

test("standard L3 editor uses the materialized L3 policy and its governed validity margin", () => {
  const spotEngine = {
    scanner: {
      l3_v3_provenance_resolver: { enabled: false },
      l3_global_block_range_compiler: {
        source_policies: { ohlcv: { allowed_source_providers: [] } },
      },
      multilayer_contract: {
        layers: {
          L3: {
            default_timeframe: "5m",
            validity_margin_seconds: 741,
            validity_margin_seconds_by_group: { structural: 717, microstructure: 741 },
            source_policies: {
              ohlcv: {
                allowed_source_providers: ["gate.io"],
                provider_policy_id: "spot_gate_closed_ohlcv_v1",
                timeframe: "5m",
                candle_policy: "CLOSED_ONLY",
              },
            },
          },
        },
      },
    },
  };

  const policies = profileSourcePoliciesForEditor(
    spotEngine,
    "STANDARD",
    "acquisition_queue",
  );
  assert.equal(policies.ohlcv?.provider_policy_id, "spot_gate_closed_ohlcv_v1");
  assert.equal(policies.ohlcv?.max_age_seconds, 717);

  const prepared = prepareProfileEntryTriggerIdentities({
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [{
        id: "entry-rsi", type: "threshold", indicator: "rsi",
        operator: ">=", value: 52, required: true, enabled: true,
      }],
    },
  }, policies);
  assert.deepEqual(prepared.issues, []);
  const condition = prepared.config.entry_triggers.conditions[0] as Record<string, any>;
  assert.equal(
    condition.max_age_seconds,
    717,
  );
});

test("editing a legacy profile does not require identity for untouched Entry Triggers", () => {
  const currentConfig = {
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [
        {
          id: "legacy-rsi", type: "threshold", indicator: "rsi",
          operator: ">=", value: 55, required: true, enabled: true,
        },
        {
          id: "legacy-taker", type: "threshold", indicator: "taker_ratio",
          operator: ">=", value: 0.53, required: true, enabled: true,
        },
      ],
    },
  };
  const candidate = {
    ...currentConfig,
    entry_triggers: {
      conditions: [
        { ...currentConfig.entry_triggers.conditions[0], value: 52 },
        currentConfig.entry_triggers.conditions[1],
        {
          id: "entry-new", type: "comparison", left: "price",
          operator: ">", right: "ema9", required: true, enabled: true,
        },
      ],
    },
  };

  const prepared = prepareProfileEntryTriggerIdentities(
    candidate,
    { ohlcv: SOURCE_POLICIES.ohlcv },
    currentConfig,
  );

  assert.deepEqual(prepared.issues, []);
  assert.equal("source" in prepared.config.entry_triggers.conditions[0], false);
  assert.equal("source" in prepared.config.entry_triggers.conditions[1], false);
  const added = prepared.config.entry_triggers.conditions[2] as Record<string, any>;
  assert.equal(added.source, "ohlcv");
  assert.equal(
    added.resolved_operands.right.source_provider,
    "gate.io",
  );
});

test("a newly added live Entry Trigger still fails closed without a configured live policy", () => {
  const prepared = prepareProfileEntryTriggerIdentities({
    default_timeframe: "5m",
    entry_triggers: {
      conditions: [{
        id: "entry-new", type: "threshold", indicator: "volume_delta",
        operator: ">", value: 0, required: true, enabled: true,
      }],
    },
  }, { ohlcv: SOURCE_POLICIES.ohlcv }, { entry_triggers: { conditions: [] } });

  assert.deepEqual(prepared.issues, [
    "entry_triggers.conditions[0].source_provider",
    "entry_triggers.conditions[0].provider_policy_id",
    "entry_triggers.conditions[0].max_age_seconds",
    "entry_triggers.conditions[0].window_seconds",
  ]);
});

test("new Block Rule condition receives governed OHLCV identity before save", () => {
  const prepared = prepareProfileBlockRuleIdentities({
    default_timeframe: "5m",
    block_rules: {
      blocks: [{
        id: "block-new", name: "RSI guard", enabled: true, logic: "AND",
        conditions: [{
          id: "cond-new", type: "threshold", indicator: "rsi",
          operator: ">=", value: 75, period: 14,
        }],
      }],
    },
  }, SOURCE_POLICIES);

  assert.deepEqual(prepared.issues, []);
  assert.deepEqual(
    prepared.config.block_rules.blocks[0].conditions[0],
    {
      id: "cond-new", type: "threshold", indicator: "rsi",
      operator: ">=", value: 75, period: 14,
      source: "ohlcv", source_provider: "gate.io",
      provider_policy_id: "spot_gate_closed_ohlcv_v1",
      max_age_seconds: 360, timeframe: "5m", candle_policy: "CLOSED_ONLY",
    },
  );
});

test("comparison Block Rule condition receives an independently resolved identity per operand", () => {
  const prepared = prepareProfileBlockRuleIdentities({
    default_timeframe: "5m",
    block_rules: {
      blocks: [{
        id: "block-cmp", name: "EMA cross", enabled: true, logic: "AND",
        conditions: [{
          id: "cond-cmp", type: "comparison", left: "price",
          operator: "<", right: "ema50",
        }],
      }],
    },
  }, SOURCE_POLICIES);

  assert.deepEqual(prepared.issues, []);
  const condition = prepared.config.block_rules.blocks[0].conditions[0] as Record<string, any>;
  assert.equal(condition.source, "ohlcv");
  assert.equal(condition.timeframe, "5m");
  assert.deepEqual(condition.resolved_operands.left, {
    indicator: "price", source: "ohlcv", source_provider: "gate.io",
    provider_policy_id: "spot_gate_closed_ohlcv_v1", max_age_seconds: 360,
    timeframe: "5m", candle_policy: "CLOSED_ONLY",
  });
});

test("adding new blocks to a legacy L3 profile does not require identity for the untouched blocks", () => {
  // Reproduces the production failure: an L3 profile whose existing blocks
  // predate the source contract (no identity fields at all) gets two brand
  // new blocks appended by a JSON round trip. Only the new blocks' new
  // conditions should receive materialized identity; the untouched legacy
  // ones must stay exactly as they are so the backend's non-regression
  // check (positional for block_rules) still recognizes them as unchanged.
  const legacyBlock = (name: string, indicator: string) => ({
    name, enabled: true, logic: "AND",
    conditions: [{ type: "threshold", indicator, operator: ">=", value: 1 }],
  });
  const currentConfig = {
    default_timeframe: "5m",
    block_rules: {
      blocks: [
        legacyBlock("Spread Guard", "spread_pct"),
        legacyBlock("Volume Guard", "macd_histogram"),
      ],
    },
  };
  const candidate = {
    ...currentConfig,
    block_rules: {
      blocks: [
        ...currentConfig.block_rules.blocks,
        {
          name: "MACD Momentum Decay", enabled: true, logic: "AND",
          conditions: [
            { type: "threshold", indicator: "macd_histogram", operator: ">", value: 0, period: 12 },
            { type: "threshold", indicator: "macd_hist_slope_3", operator: "<", value: 0 },
          ],
        },
      ],
    },
  };

  const prepared = prepareProfileBlockRuleIdentities(
    candidate,
    { ohlcv: SOURCE_POLICIES.ohlcv },
    currentConfig,
  );

  assert.deepEqual(prepared.issues, []);
  // Untouched legacy blocks keep zero identity fields.
  assert.equal("source" in prepared.config.block_rules.blocks[0].conditions[0], false);
  assert.equal("source" in prepared.config.block_rules.blocks[1].conditions[0], false);
  // The brand-new block's conditions are fully identified.
  const newBlockConditions = prepared.config.block_rules.blocks[2].conditions as Record<string, any>[];
  assert.equal(newBlockConditions[0].source, "ohlcv");
  assert.equal(newBlockConditions[0].source_provider, "gate.io");
  assert.equal(newBlockConditions[1].source, "ohlcv");
});

test("a newly added Block Rule condition still fails closed without a configured policy", () => {
  const prepared = prepareProfileBlockRuleIdentities({
    default_timeframe: "5m",
    block_rules: {
      blocks: [{
        name: "New guard", enabled: true, logic: "AND",
        conditions: [{ type: "threshold", indicator: "rsi", operator: ">", value: 50, required: true }],
      }],
    },
  }, {}, { block_rules: { blocks: [] } });

  assert.deepEqual(prepared.issues, [
    "block_rules.blocks[0].conditions[0].source_provider",
    "block_rules.blocks[0].conditions[0].provider_policy_id",
    "block_rules.blocks[0].conditions[0].max_age_seconds",
    "block_rules.blocks[0].conditions[0].candle_policy",
  ]);
});

test("reproduces the live L3_HIGH_VOLUME_PUMP_CHASE_V1 production fix with the real governed policy", () => {
  // Values queried read-only from the live spot_engine config
  // (scanner.multilayer_contract.layers.L3), 2026-09-10.
  const spotEngine = {
    scanner: {
      multilayer_contract: {
        layers: {
          L3: {
            default_timeframe: "5m",
            validity_margin_seconds_by_group: { structural: 717, microstructure: 741 },
            source_policies: {
              ohlcv: {
                allowed_source_providers: ["gate.io"],
                provider_policy_id: "spot_gate_closed_ohlcv_v1",
                timeframe: "5m",
                candle_policy: "CLOSED_ONLY",
              },
            },
          },
        },
      },
    },
  };
  const policies = profileSourcePoliciesForEditor(spotEngine, "STANDARD", "acquisition_queue");

  const currentConfig = {
    default_timeframe: "5m",
    block_rules: {
      blocks: [{
        name: "existing legacy block", enabled: true, logic: "AND",
        conditions: [{ type: "threshold", indicator: "orderbook_depth_usdt", operator: ">=", value: 1 }],
      }],
    },
  };
  const candidate = {
    ...currentConfig,
    block_rules: {
      blocks: [
        ...currentConfig.block_rules.blocks,
        {
          name: "MACD Momentum Decay", enabled: true, logic: "AND", timeframe: "5m",
          conditions: [
            { type: "threshold", indicator: "macd_histogram", operator: ">", value: 0, period: 12 },
            { type: "threshold", indicator: "macd_hist_slope_3", operator: "<", value: 0 },
          ],
        },
        {
          name: "Extensao EMA21 VWAP com perda de velocidade", enabled: true, logic: "AND", timeframe: "5m",
          conditions: [
            { type: "threshold", indicator: "ema21_distance_pct", operator: ">", value: 1.5 },
            { type: "threshold", indicator: "vwap_distance_pct", operator: ">", value: 2.0 },
          ],
        },
      ],
    },
  };

  const prepared = prepareProfileBlockRuleIdentities(candidate, policies, currentConfig);

  assert.deepEqual(prepared.issues, []);
  for (const block of prepared.config.block_rules.blocks.slice(1)) {
    for (const condition of block.conditions as Record<string, any>[]) {
      assert.equal(condition.source, "ohlcv");
      assert.equal(condition.source_provider, "gate.io");
      assert.equal(condition.provider_policy_id, "spot_gate_closed_ohlcv_v1");
      assert.equal(condition.candle_policy, "CLOSED_ONLY");
      assert.equal(condition.max_age_seconds, 717);
    }
  }
});
