import assert from "node:assert/strict";
import test from "node:test";

import {
  collectEditableLeafPaths,
  normaliseBarrierContract,
  parseStrategySettingsJson,
  updateAtPath,
} from "./strategySettings";

test("parser accepts partial aggregate documents", () => {
  assert.deepEqual(parseStrategySettingsJson('{"ml_shadow":{"ml_fee_roundtrip_pct":0.25}}'), {
    ml_shadow: { ml_fee_roundtrip_pct: 0.25 },
  });
});

test("path updater is immutable", () => {
  const before = { spot_engine: { shadow: { amount_usdt: 1000 } } };
  const after = updateAtPath(before, ["spot_engine", "shadow", "amount_usdt"], 250);
  assert.equal(((before.spot_engine as Record<string, unknown>).shadow as Record<string, unknown>).amount_usdt, 1000);
  assert.equal((((after.spot_engine as Record<string, unknown>).shadow) as Record<string, unknown>).amount_usdt, 250);
});

test("mode selection pins the implemented contract", () => {
  const fixed = normaliseBarrierContract({ ml_shadow: { shadow_barrier_mode: "FIXED" } });
  assert.equal((fixed.ml_shadow as Record<string, unknown>).ml_active_barrier_contract_version, "shadow_fixed_v1");
});

test("recursive editor coverage sees every leaf", () => {
  const paths = collectEditableLeafPaths({
    spot_engine: { shadow: { amount_usdt: 1000, ttt: { enabled: true } } },
    ml_shadow: { shadow_barrier_mode: "ATR_DYNAMIC" },
  });
  assert.deepEqual(paths.sort(), [
    "ml_shadow.shadow_barrier_mode",
    "spot_engine.shadow.amount_usdt",
    "spot_engine.shadow.ttt.enabled",
  ]);
});
