import assert from "node:assert/strict";
import test from "node:test";

import {
  formatHolding,
  formatRate,
  formatUsd,
  filterProfileRows,
  heatmapColor,
  profileDailyPerformanceRequestPath,
  profilePerformanceRequestPath,
  sortProfileRows,
  type ProfilePerformanceRow,
} from "./profilePerformance";


function row(overrides: Partial<ProfilePerformanceRow>): ProfilePerformanceRow {
  return {
    rank: 1,
    profile_id: "p1",
    profile_name: "Profile",
    watchlist_name: null,
    trades: 100,
    closed_trades: 90,
    open_trades: 10,
    tp: 55,
    sl: 30,
    timeout: 5,
    ev_score: 70,
    ev_delta: 2,
    win_rate: 0.61,
    win_rate_delta_pp: 1.5,
    pnl_day_usdt: 10,
    pnl_period_usdt: 100,
    avg_pnl_pct: 0.4,
    holding_seconds: 1_200,
    trend: "IMPROVING",
    trend_evidence: { points: 7, slope: 1, net_change: 6, positive_days: 5, negative_days: 1 },
    sample_status: "MEDIUM",
    status: "POSITIVE",
    priority: "A",
    priority_reason: "test",
    history: [],
    ...overrides,
  };
}


test("sortProfileRows sorts numeric values and keeps canonical rank as tie-breaker", () => {
  const rows = [
    row({ profile_id: "p1", rank: 1, ev_score: 70 }),
    row({ profile_id: "p2", rank: 2, ev_score: 85 }),
    row({ profile_id: "p3", rank: 3, ev_score: 70 }),
  ];
  assert.deepEqual(sortProfileRows(rows, "ev_score", "desc").map((item) => item.profile_id), ["p2", "p1", "p3"]);
  assert.deepEqual(sortProfileRows(rows, "ev_score", "asc").map((item) => item.profile_id), ["p1", "p3", "p2"]);
});


test("profile filters combine profile, status and search without mutating rows", () => {
  const rows = [
    row({ profile_id: "p1", profile_name: "EMA Reclaim", status: "POSITIVE" }),
    row({ profile_id: "p2", profile_name: "VWAP Breakout", status: "ATTENTION" }),
  ];
  assert.deepEqual(filterProfileRows(rows, { profileId: "", status: "ATTENTION", search: "vwap" }).map((item) => item.profile_id), ["p2"]);
  assert.deepEqual(filterProfileRows(rows, { profileId: "p1", status: "ALL", search: "" }).map((item) => item.profile_id), ["p1"]);
  assert.equal(rows.length, 2);
});


test("profile request path carries the selected date and period", () => {
  assert.equal(
    profilePerformanceRequestPath("2026-08-21", 14),
    "/api/shadow-portfolio/profile-performance?as_of=2026-08-21&range_days=14",
  );
});


test("daily evolution request path supports bounded and total ranges", () => {
  assert.equal(
    profileDailyPerformanceRequestPath("2026-08-21", "15d"),
    "/api/shadow-portfolio/profile-performance/daily?as_of=2026-08-21&range=15d",
  );
  assert.equal(
    profileDailyPerformanceRequestPath("2026-08-21", "total"),
    "/api/shadow-portfolio/profile-performance/daily?as_of=2026-08-21&range=total",
  );
});


test("formatters preserve units and percentage-point semantics", () => {
  assert.equal(formatUsd(42.5), "+$42.50");
  assert.equal(formatUsd(-3), "-$3.00");
  assert.equal(formatRate(0.618), "61.8%");
  assert.equal(formatHolding(1_200), "20m");
  assert.equal(formatHolding(5_400), "1h 30m");
});


test("heatmap colors reflect metric semantics", () => {
  assert.match(heatmapColor("pnl_usdt", 10, -10, 10), /34,185,122/);
  assert.match(heatmapColor("pnl_usdt", -10, -10, 10), /229,72,77/);
  assert.match(heatmapColor("trades", 10, 0, 10), /79,123,247/);
  assert.match(heatmapColor("holding_seconds", 10, 0, 10), /157,124,247/);
});
