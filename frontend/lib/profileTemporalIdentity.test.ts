import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import test from "node:test";
import { ConditionBuilder } from "../components/profiles/ConditionBuilder";
import {
  prepareProfileSignalIdentities,
  profileTemporalIdentity,
  serializeProfileEditorConfig,
  updateProfileConditionIndicator,
} from "./profileConditionState";

const policies = {
  live_trade_flow: { window_seconds: 60, provider_policy_id: "flow", allowed_source_providers: ["gate_trades_ws_spot"], max_age_seconds: 60 },
  live_order_book: { snapshot: true, provider_policy_id: "book", allowed_source_providers: ["gate"], max_age_seconds: 600 },
  ohlcv: { timeframe: "5m", candle_policy: "CLOSED_ONLY", provider_policy_id: "candles", allowed_source_providers: ["gate.io"] },
};
const flow = { id: "delta", field: "volume_delta", operator: ">", value: 0,
  source: "live_trade_flow", source_provider: "gate_trades_ws_spot", provider_policy_id: "flow", window_seconds: 60,
  // Historical irrelevant period must not be offered as an editable candle period.
  period: 20,
};

test("flow editor shows configured seconds without candle timeframe or period controls", () => {
  const html = renderToStaticMarkup(createElement(ConditionBuilder, {
    conditions: [flow], onChange: () => {}, defaultTimeframe: "5m", sourcePolicies: policies,
  }));
  assert.match(html, /60 s/);
  assert.doesNotMatch(html, /condition-timeframe-0/);
  assert.doesNotMatch(html, /Period \(default:/);
  assert.doesNotMatch(html, /<option[^>]*>5m<\/option>/);
});

test("flow threshold edit and export/reload preserve the persisted identity", () => {
  const saved = { default_timeframe: "5m", signals: { conditions: [flow] } };
  const edited = { ...saved, signals: { conditions: [{ ...flow, value: 10 }] } };
  const result = prepareProfileSignalIdentities(edited, policies, saved);
  assert.deepEqual(result.issues, []);
  const reloaded = JSON.parse(JSON.stringify(serializeProfileEditorConfig(result.config)));
  assert.deepEqual(reloaded.signals.conditions, [{ ...flow, value: 10 }]);
  assert.equal(profileTemporalIdentity(reloaded.signals.conditions[0], policies, "1h").windowSeconds, 60);
});

test("switching OHLCV to flow removes old source and new save obtains flow policy", () => {
  const original: Record<string, any> = { id: "c", field: "rsi", operator: ">", value: 50, source: "ohlcv", timeframe: "5m", period: 14, source_provider: "gate.io", provider_policy_id: "candles", candle_policy: "CLOSED_ONLY" };
  const changed = updateProfileConditionIndicator(original, { field: "volume_delta", value: 0 });
  assert.equal(changed.source, undefined);
  assert.equal(changed.timeframe, undefined);
  const result = prepareProfileSignalIdentities({ default_timeframe: "5m", signals: { conditions: [changed] } }, policies);
  assert.deepEqual(result.issues, []);
  const condition = result.config.signals.conditions[0];
  assert.equal(condition.source, "live_trade_flow");
  assert.equal(condition.window_seconds, 60);
  assert.equal(condition.period, undefined);
  assert.equal(condition.timeframe, undefined);
});

test("missing policy does not invent a window and policy difference is visible", () => {
  const missing = profileTemporalIdentity({ field: "volume_delta" }, {}, "5m");
  assert.equal(missing.windowSeconds, undefined);
  assert.match(missing.label, /não definida/);
  const different = profileTemporalIdentity({ ...flow, window_seconds: 300 }, policies, "5m");
  assert.equal(different.windowSeconds, 300);
  assert.equal(different.needsValidation, true);
  assert.match(different.detail, /60 s/);
});

test("book snapshot does not display freshness as an aggregation window", () => {
  const book = profileTemporalIdentity({ field: "orderbook_pressure", source: "live_order_book", snapshot: true, max_age_seconds: 600 }, policies, "5m");
  assert.equal(book.label, "Snapshot do livro");
  assert.equal(book.windowSeconds, undefined);
  assert.equal(book.showTimeframe, false);
});

test("volume spike keeps its closed candle and calculation period", () => {
  const spike = { id: "spike", field: "volume_spike", operator: ">=", value: 1.05, source: "ohlcv", timeframe: "5m", period: 20, candle_policy: "CLOSED_ONLY" };
  const html = renderToStaticMarkup(createElement(ConditionBuilder, { conditions: [spike], onChange: () => {}, sourcePolicies: policies }));
  assert.match(html, /condition-timeframe-0/);
  assert.match(html, /Period \(default: 20\)/);
  assert.match(html, /Candles fechados/);
});
