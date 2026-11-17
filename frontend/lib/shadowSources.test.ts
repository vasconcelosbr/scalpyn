import test from "node:test";
import assert from "node:assert/strict";
import { applyShadowSourceFilter, shadowSourceLabel } from "./shadowSources";

test("approved views combine history and Consolidado while retaining profile filters", () => {
  const params = new URLSearchParams({profile_id: "profile", symbol: "BTC_USDT"});
  applyShadowSourceFilter(params, "L3", "all");
  assert.equal(params.get("sources"), "L3,L3_CONSOLIDATED");
  assert.equal(params.get("profile_id"), "profile");
  assert.equal(params.get("symbol"), "BTC_USDT");
});
test("origin selection and rejected views remain segregated", () => {
  for (const origin of ["L3", "L3_CONSOLIDATED"] as const) {
    const params = new URLSearchParams(); applyShadowSourceFilter(params, "L3", origin);
    assert.equal(params.get("source"), origin); assert.equal(params.has("sources"), false);
  }
  const rejected = new URLSearchParams(); applyShadowSourceFilter(rejected, "L3_REJECTED", "all");
  assert.equal(rejected.get("source"), "L3_REJECTED");
  assert.notEqual(shadowSourceLabel("L3"), shadowSourceLabel("L3_CONSOLIDATED"));
});
