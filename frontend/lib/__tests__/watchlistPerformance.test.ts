import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { countByPriority, summarizeWatchlistPerformance, type WatchlistPerformanceRow } from "../watchlistPerformance";

const row = (overrides: Partial<WatchlistPerformanceRow>): WatchlistPerformanceRow => ({
  rank_position: 1, watchlist_id: "watchlist-1", watchlist_name: "L3", profile_id: "profile-1", profile_name: "Profile", level: "L3",
  total_trades: 50, open_trades: 0, completed_trades: 50, win_rate: 0.55, tp_4h_rate: 0.8, avg_pnl_pct: 0.3,
  pnl_total_usdt: 100, avg_holding_win_seconds: 3600, ev_score: 60, stat_confidence: "LOW", delta_win_rate_vs_baseline: 0.05,
  delta_pnl_vs_baseline: 0.1, priority: "A", priority_reason: "Amostra suficiente", operational_class: "GOOD_4H",
  computed_at: "2026-06-27T00:00:00Z", ...overrides,
});

describe("watchlist performance dashboard helpers", () => {
  it("summarizes trusted, low-N and positive rows", () => {
    const summary = summarizeWatchlistPerformance([
      row({ ev_score: 72 }),
      row({ completed_trades: 10, priority: "LOW_N", stat_confidence: "LOW_N", ev_score: 38 }),
      row({ completed_trades: 0, priority: "BLOCKED", stat_confidence: "EMPTY", avg_pnl_pct: null, pnl_total_usdt: 0, ev_score: 0 }),
    ]);
    assert.deepEqual(summary, { total: 3, ranked: 2, trusted: 1, lowN: 1, positivePnl: 2, topScore: 72 });
  });

  it("returns every priority bucket in operational order", () => {
    const buckets = countByPriority([row({ priority: "B" }), row({ priority: "LOW_N" })]);
    assert.deepEqual(buckets.map((bucket) => bucket.priority), ["A+", "A", "B", "C", "D", "LOW_N", "BLOCKED"]);
    assert.equal(buckets.find((bucket) => bucket.priority === "B")?.count, 1);
  });
});