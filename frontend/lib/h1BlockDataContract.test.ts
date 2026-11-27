import assert from "node:assert/strict";
import test from "node:test";
import { candlePolicyForTimeframe, profileConditionTimeframeUpdates, prepareProfileBlockRuleIdentities } from "./profileConditionState";
import { classifySkip, inputReasonLabel } from "../components/watchlist/EvaluationTraceBreakdown";

const policy = { timeframe: "5m", max_age_seconds: 360, provider_policy_id: "spot_gate_closed_ohlcv_v1", allowed_source_providers: ["gate.io"], candle_policy: "CLOSED_ONLY" };

test("H1 validity derives from configured cadence; timeframe overrides and custom values survive", () => {
  assert.equal(candlePolicyForTimeframe(policy, "1h").max_age_seconds, 4320);
  assert.equal(candlePolicyForTimeframe({ ...policy, max_age_seconds_by_timeframe: { "1h": 3900 } }, "1h").max_age_seconds, 3900);
  assert.equal(profileConditionTimeframeUpdates({ timeframe: "5m", max_age_seconds: 360 }, "1h", { ohlcv: policy }).max_age_seconds, 4320);
  assert.equal(profileConditionTimeframeUpdates({ timeframe: "5m", max_age_seconds: 50 }, "1h", { ohlcv: policy }).max_age_seconds, 50);
  const next = prepareProfileBlockRuleIdentities({ default_timeframe: "1h", block_rules: { blocks: [{ conditions: [{ type: "threshold", indicator: "psar_trend", operator: "==", value: "bearish" }] }] } }, { ohlcv: policy });
  assert.deepEqual(next.issues, []);
  assert.equal((next.config.block_rules.blocks[0].conditions[0] as Record<string, any>).max_age_seconds, 4320);
});

test("missing, incompatible, unclosed and expired inputs have different diagnoses", () => {
  const labels = ["indicator_not_available", "indicator_identity_mismatch", "indicator_candle_not_closed", "indicator_stale"].map(reason => classifySkip({ status: "SKIPPED", reason })?.currentText);
  assert.equal(new Set(labels).size, 4);
  assert.ok(labels.every(label => label && !label.includes("aguardando coleta")));
  assert.match(inputReasonLabel("indicator_identity_mismatch"), /incompatíveis/);
});
