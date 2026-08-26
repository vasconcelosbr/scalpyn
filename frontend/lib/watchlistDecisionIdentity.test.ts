import assert from "node:assert/strict";
import test from "node:test";

import { watchlistDecisionRowKey } from "./watchlistDecisionIdentity";

test("keeps an expanded watchlist decision stable when its snapshot timestamp changes", () => {
  const beforeRefresh = {
    symbol: "NEAR_USDT",
    status: "approved",
    stage: "L3",
    profile_id: "profile-1",
    timestamp: "2026-08-24T12:00:00Z",
  };
  const afterRefresh = {
    ...beforeRefresh,
    timestamp: "2026-08-24T12:01:00Z",
  };

  assert.equal(
    watchlistDecisionRowKey(beforeRefresh),
    watchlistDecisionRowKey(afterRefresh),
  );
});

test("distinguishes decisions that belong to a different logical row", () => {
  const base = {
    symbol: "NEAR_USDT",
    status: "approved",
    stage: "L3",
    profile_id: "profile-1",
  };
  const baseKey = watchlistDecisionRowKey(base);

  assert.notEqual(baseKey, watchlistDecisionRowKey({ ...base, symbol: "TRX_USDT" }));
  assert.notEqual(baseKey, watchlistDecisionRowKey({ ...base, status: "rejected" }));
  assert.notEqual(baseKey, watchlistDecisionRowKey({ ...base, stage: "L2" }));
  assert.notEqual(baseKey, watchlistDecisionRowKey({ ...base, profile_id: "profile-2" }));
});
