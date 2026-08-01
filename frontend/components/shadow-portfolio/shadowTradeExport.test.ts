import assert from "node:assert/strict";
import test from "node:test";

import { buildShadowTradeExport, shadowTradeExportFilename } from "./shadowTradeExport";
import type { ShadowTradeChartResponse, ShadowTradeDetail } from "./types";

const detail = {
  id: "trade-123",
  symbol: "BTC/USDT",
  status: "COMPLETED",
  outcome: "TP_HIT",
  entry_timestamp: "2026-08-01T12:00:00Z",
  exit_timestamp: "2026-08-01T12:15:00Z",
  entry_metrics: { adx: 18, rsi: 42, state: "ready" },
  exit_metrics: { adx: 21, rsi: 40, state: "closed" },
  features_snapshot: { adx: 18 },
  features_snapshot_exit: { adx: 21 },
  amount_usdt: 100,
} as unknown as ShadowTradeDetail;

const chart = {
  shadow_id: detail.id,
  symbol: detail.symbol,
  timeframe: "1m",
  exchange: "gateio",
  context_minutes: 30,
  window_start: "2026-08-01T11:30:00Z",
  window_end: "2026-08-01T12:45:00Z",
  entry_timestamp: detail.entry_timestamp,
  exit_timestamp: detail.exit_timestamp,
  entry_price: 100,
  exit_price: 102,
  tp_price: 102,
  sl_price: 99,
  outcome: detail.outcome,
  candles: [{ time: "2026-08-01T12:00:00Z", open: 100, high: 101, low: 99, close: 100.5, volume: 10 }],
} as ShadowTradeChartResponse;

test("keeps raw data and builds an analysis-friendly comparison", () => {
  const exported = buildShadowTradeExport(detail, chart, {
    generatedAt: "2026-08-01T13:00:00Z",
    chartError: null,
  });

  assert.equal(exported.export_metadata.schema_version, "1.0.0");
  assert.equal(exported.export_metadata.completeness.chart_loaded, true);
  assert.equal(exported.chart?.markers.sell.outcome, "TP_HIT");
  assert.equal(exported.raw.trade_detail, detail);
  assert.equal(exported.raw.chart_response, chart);
  assert.deepEqual(exported.indicator_analysis.comparison.find((item) => item.indicator === "adx"), {
    indicator: "adx",
    entry: 18,
    exit: 21,
    delta_absolute: 3,
    delta_percent: 16.666666666666664,
  });
  assert.deepEqual(exported.indicator_analysis.comparison.find((item) => item.indicator === "state"), {
    indicator: "state",
    entry: "ready",
    exit: "closed",
    delta_absolute: null,
    delta_percent: null,
  });
});

test("documents chart failures without dropping the trade", () => {
  const exported = buildShadowTradeExport(detail, null, { chartError: "candles unavailable" });

  assert.equal(exported.chart, null);
  assert.equal(exported.export_metadata.completeness.chart_loaded, false);
  assert.equal(exported.export_metadata.completeness.chart_error, "candles unavailable");
  assert.equal(exported.raw.trade_detail.id, "trade-123");
});

test("creates a traceable and browser-safe filename", () => {
  assert.equal(
    shadowTradeExportFilename(detail),
    "shadow-trade_BTC_USDT_TP_HIT_2026-08-01T12-15-00-000Z_trade-123.json",
  );
});
