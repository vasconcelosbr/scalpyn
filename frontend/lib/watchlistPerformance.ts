export type WatchlistPriority = "A+" | "A" | "B" | "C" | "D" | "LOW_N" | "BLOCKED";

export interface WatchlistPerformanceRow {
  rank_position: number;
  watchlist_id: string | null;
  watchlist_name: string | null;
  profile_id: string;
  profile_name: string;
  level: string;
  total_trades: number;
  open_trades: number;
  completed_trades: number;
  win_rate: number | null;
  tp_4h_rate: number | null;
  avg_pnl_pct: number | null;
  pnl_total_usdt: number;
  avg_holding_win_seconds: number | null;
  ev_score: number;
  stat_confidence: "HIGH" | "MEDIUM" | "LOW" | "LOW_N" | "EMPTY";
  delta_win_rate_vs_baseline: number;
  delta_pnl_vs_baseline: number;
  priority: WatchlistPriority;
  priority_reason: string;
  operational_class: string;
  computed_at: string;
}

export interface WatchlistPerformanceSummary {
  total: number;
  ranked: number;
  trusted: number;
  lowN: number;
  positivePnl: number;
  topScore: number | null;
}

export const PRIORITY_ORDER: WatchlistPriority[] = ["A+", "A", "B", "C", "D", "LOW_N", "BLOCKED"];

export function summarizeWatchlistPerformance(rows: WatchlistPerformanceRow[]): WatchlistPerformanceSummary {
  return {
    total: rows.length,
    ranked: rows.filter((row) => row.completed_trades > 0).length,
    trusted: rows.filter((row) => row.completed_trades >= 30).length,
    lowN: rows.filter((row) => row.priority === "LOW_N").length,
    positivePnl: rows.filter((row) => (row.avg_pnl_pct ?? 0) > 0 && row.pnl_total_usdt > 0).length,
    topScore: rows.length ? Math.max(...rows.map((row) => row.ev_score)) : null,
  };
}

export function countByPriority(rows: WatchlistPerformanceRow[]): { priority: WatchlistPriority; count: number }[] {
  return PRIORITY_ORDER.map((priority) => ({ priority, count: rows.filter((row) => row.priority === priority).length }));
}