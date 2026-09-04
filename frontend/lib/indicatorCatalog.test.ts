import assert from "node:assert/strict";
import test from "node:test";

import {
  STRATEGY_PROFILE_INDICATORS,
  indicatorOptionsForSection,
  optionsWithUnsupportedIndicator,
} from "./indicatorCatalog";

const PREVIOUS_CATALOG_IDS = [
  "volume_24h", "market_cap", "price", "change_24h", "spread_pct",
  "orderbook_depth_usdt", "taker_ratio", "volume_spike", "volume_delta",
  "orderbook_pressure", "bid_ask_imbalance", "funding_rate", "obv", "ema5_distance_pct",
  "ema9_distance_pct", "ema21_distance_pct", "ema50_distance_pct",
  "ema200_distance_pct", "vwap_distance_pct", "bb_upper_distance_pct",
  "bb_middle_distance_pct", "bb_lower_distance_pct", "recent_high_5m_distance_pct",
  "recent_high_15m_distance_pct", "recent_high_30m_distance_pct",
  "recent_high_1h_distance_pct", "recent_low_15m_distance_pct",
  "breakout_distance_pct", "price_change_1m_pct", "price_change_5m_pct",
  "price_change_15m_pct", "rsi", "macd", "macd_histogram", "macd_signal",
  "stoch_k", "stoch_d", "zscore", "adx", "di_plus", "di_minus", "di_trend",
  "atr", "atr_percent", "bb_width", "psar_trend", "ema5", "ema9", "ema21",
  "ema50", "ema200", "ema_full_alignment", "ema9_gt_ema21", "ema9_gt_ema50",
  "ema50_gt_ema200", "alpha_score", "score", "liquidity_score", "momentum_score",
];

test("catalog has unique IDs and retains every previous editor option", () => {
  const ids = STRATEGY_PROFILE_INDICATORS.map((indicator) => indicator.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const id of PREVIOUS_CATALOG_IDS) assert.ok(ids.includes(id), `missing ${id}`);
});

test("pending indicators expose the required labels, kinds and sections", () => {
  const byId = new Map(STRATEGY_PROFILE_INDICATORS.map((indicator) => [indicator.id, indicator]));
  const expected = {
    adx_acceleration: ["ADX Acceleration", ["block_rules"]],
    adx_slope_3: ["ADX Slope 3", ["block_rules"]],
    macd_hist_slope_3: ["MACD Histogram Slope 3", ["block_rules", "entry_triggers"]],
    macd_hist_slope_5: ["MACD Histogram Slope 5", ["entry_triggers"]],
    rsi_slope_3: ["RSI Slope 3", ["block_rules"]],
    entry_exhaustion_score: ["Entry Exhaustion Score", ["block_rules"]],
    rsi_6: ["RSI 6", ["block_rules"]],
  } as const;
  for (const [id, [label, sections]] of Object.entries(expected)) {
    const indicator = byId.get(id);
    assert.ok(indicator, id);
    assert.equal(indicator.kind, "number");
    assert.equal(indicator.label, label);
    assert.deepEqual(indicator.sections, sections);
  }
  assert.equal(byId.get("rsi_6")?.defaultPeriod, 6);
  assert.equal(byId.get("atr_pct")?.kind, "number");
  assert.equal(byId.get("breakout_distance_pct")?.requiresReferenceWindow, true);
});

test("unknown IDs receive an explicit sentinel and never select the first numeric option", () => {
  const options = indicatorOptionsForSection("block_rules");
  const rendered = optionsWithUnsupportedIndicator(options, "future_indicator_v9");
  assert.equal(rendered[0].value, "future_indicator_v9");
  assert.equal(rendered[0].unsupported, true);
  assert.match(rendered[0].label, /indicador não suportado/);
  assert.notEqual(rendered[0].value, "ema5_distance_pct");
});
