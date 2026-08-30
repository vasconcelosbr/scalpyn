import assert from "node:assert/strict";
import test from "node:test";

import {
  SHADOW_REPORT_OUTCOMES,
  shadowReportSelectionKey,
  toggleShadowReportOutcome,
} from "./shadowReportOutcomeFilters";

test("outcome buttons toggle a result without mutating the current selection", () => {
  const current = [...SHADOW_REPORT_OUTCOMES];
  const withoutStop = toggleShadowReportOutcome(current, "TRAILING_STOP");

  assert.deepEqual(current, SHADOW_REPORT_OUTCOMES);
  assert.deepEqual(withoutStop, ["TP_HIT", "SL_HIT", "TIMEOUT", "OPEN"]);
  assert.deepEqual(toggleShadowReportOutcome(withoutStop, "TRAILING_STOP"), SHADOW_REPORT_OUTCOMES);
});

test("selection identity is stable across array ordering and changes with outcomes", () => {
  const base = {
    sources: ["L3_REJECTED", "L3"],
    outcomes: ["TP_HIT", "SL_HIT"] as Array<"TP_HIT" | "SL_HIT">,
    dateFrom: "2026-08-23",
    dateTo: "2026-08-30",
    watchlistIds: ["watchlist-b", "watchlist-a"],
    profileIds: ["profile-b", "profile-a"],
    includeLegacy: false,
  };

  const reordered = shadowReportSelectionKey({
    ...base,
    sources: [...base.sources].reverse(),
    outcomes: [...base.outcomes].reverse(),
    watchlistIds: [...base.watchlistIds].reverse(),
    profileIds: [...base.profileIds].reverse(),
  });

  assert.equal(shadowReportSelectionKey(base), reordered);
  assert.notEqual(
    shadowReportSelectionKey(base),
    shadowReportSelectionKey({ ...base, outcomes: ["TP_HIT"] }),
  );
});
