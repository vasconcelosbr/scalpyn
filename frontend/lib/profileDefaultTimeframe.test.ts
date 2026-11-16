import assert from "node:assert/strict";
import test from "node:test";
import { setProfileDefaultTimeframe, prepareProfileFilterIdentities, serializeProfileEditorConfig } from "./profileConditionState";
import { validateExecutionSections } from "./profileImportPreflight";

test("default selection updates all candle sections and saved comparison operands", () => {
  const candle = { indicator: "rsi", timeframe: "5m", period: 14, source: "ohlcv", value: 50, operator: ">" };
  const config = { default_timeframe: "5m", filters: { conditions: [{ ...candle, field: "rsi" }] },
    signals: { conditions: [candle] }, entry_triggers: { conditions: [candle] },
    scoring: { rules: [candle] }, block_rules: { blocks: [{ timeframe: "1m", conditions: [
      { type: "comparison", left: "ema9", right: "ema50", operator: ">", resolved_operands: {
        left: { indicator: "ema9", source: "ohlcv", timeframe: "1m", period: 9 },
        right: { indicator: "ema50", source: "ohlcv", timeframe: "15m", period: 50 },
      } },
    ] }] } };
  const changed = setProfileDefaultTimeframe(config, "4h");
  const saved = JSON.parse(JSON.stringify(serializeProfileEditorConfig(changed)));
  assert.equal(config.default_timeframe, "5m");
  for (const section of ["filters", "signals", "entry_triggers"]) {
    assert.equal(saved[section].conditions[0].timeframe, "4h");
    assert.equal(saved[section].conditions[0].period, 14);
  }
  assert.equal(saved.scoring.rules[0].timeframe, "4h");
  assert.equal(saved.block_rules.blocks[0].timeframe, "4h");
  for (const side of ["left", "right"]) assert.equal(saved.block_rules.blocks[0].conditions[0].resolved_operands[side].timeframe, "4h");
  assert.deepEqual(validateExecutionSections(saved), []);
});

test("metadata, live windows and fixed reference windows remain unchanged", () => {
  const conditions = ["market_cap", "price", "volume_24h", "change_24h", "alpha_score", "funding_rate", "spread_pct",
    "volume_delta", "taker_ratio", "recent_high_5m_distance_pct", "price_change_5m_pct", "breakout_distance_pct"]
    .map(field => ({ field, operator: ">", value: 1, window_seconds: 60, reference_window: "5m" }));
  const changed = setProfileDefaultTimeframe({ filters: { conditions } }, "4h");
  assert.deepEqual(changed.filters.conditions, conditions);
});

test("new candle indicators inherit profile H4 before a global M5 source policy", () => {
  const config = { default_timeframe: "4h", filters: { conditions: [{ field: "rsi", operator: ">", value: 50 }] } };
  const prepared = prepareProfileFilterIdentities(config, { ohlcv: { timeframe: "5m", candle_policy: "CLOSED_ONLY",
    allowed_source_providers: ["gate.io"], provider_policy_id: "spot_gate_closed_ohlcv_v1" } });
  assert.deepEqual(prepared.issues, []);
  assert.equal((prepared.config.filters.conditions[0] as any).timeframe, "4h");
});
