"use client";

import useSWR from "swr";
import { apiGet } from "@/lib/api";

export type WindowKey = "1D" | "7D" | "30D" | "90D" | "MTD" | "YTD" | "ALL";

export interface PerformanceFilter {
  window: WindowKey;
  from?: string;
  to?: string;
  autoRefresh: boolean;
}

function qs(filter: PerformanceFilter, extra: Record<string, string | number | undefined> = {}) {
  const p = new URLSearchParams();
  p.set("window", filter.window);
  if (filter.from) p.set("from", filter.from);
  if (filter.to) p.set("to", filter.to);
  for (const [k, v] of Object.entries(extra)) {
    if (v !== undefined && v !== "") p.set(k, String(v));
  }
  return p.toString();
}

const fetcher = <T,>(url: string) => apiGet<T>(url);

export interface SummaryResp {
  window: { key: string; from: string; to: string };
  capital: { invested_usdt: number; spot_pnl_usdt: number; futures_pnl_usdt: number; open_positions: number };
  pnl: { total_usdt: number; roi_pct: number; fees_usdt: number; delta_vs_previous: number };
  stats: {
    total_trades: number; wins: number; losses: number; win_rate_pct: number;
    profit_factor: number | null; sharpe: number | null;
    avg_win_usdt: number; avg_loss_usdt: number;
    biggest_win_usdt: number; biggest_loss_usdt: number;
    avg_holding_seconds: number; volume_usdt: number;
  };
  risk: { max_drawdown_usdt: number; current_drawdown_usdt: number; recovery_pct: number | null };
}

export function usePerformanceSummary(filter: PerformanceFilter) {
  return useSWR<SummaryResp>(
    `/api/performance/summary?${qs(filter)}`,
    fetcher,
    { refreshInterval: filter.autoRefresh ? 30_000 : 0, revalidateOnFocus: false }
  );
}

export interface EquityResp {
  window: { from: string; to: string };
  points: { date: string | null; pnl_day: number; cum_pnl: number; drawdown: number }[];
}
export function usePerformanceEquity(filter: PerformanceFilter) {
  return useSWR<EquityResp>(
    `/api/performance/equity?${qs(filter)}`,
    fetcher,
    { refreshInterval: filter.autoRefresh ? 30_000 : 0, revalidateOnFocus: false }
  );
}

export interface DistributionResp {
  counts: { wins: number; losses: number; spot: number; futures: number; longs: number; shorts: number };
  heatmap: { dow: number; hour: number; n: number; pnl: number }[];
}
export function usePerformanceDistribution(filter: PerformanceFilter) {
  return useSWR<DistributionResp>(
    `/api/performance/distribution?${qs(filter)}`,
    fetcher,
    { refreshInterval: filter.autoRefresh ? 30_000 : 0, revalidateOnFocus: false }
  );
}

export interface ByAssetRow {
  symbol: string; market_type: string; trades: number; win_rate_pct: number;
  pnl_usdt: number; fees_usdt: number; roi_pct: number; avg_holding_seconds: number;
}
export function usePerformanceByAsset(filter: PerformanceFilter) {
  return useSWR<{ rows: ByAssetRow[] }>(
    `/api/performance/by-asset?${qs(filter)}`,
    fetcher,
    { refreshInterval: filter.autoRefresh ? 60_000 : 0, revalidateOnFocus: false }
  );
}

export interface ExecutionRow {
  id: number; symbol: string; market_type: string; direction: string;
  opened_at: string | null; closed_at: string | null; holding_seconds: number | null;
  qty: number | null; avg_entry: number | null; avg_exit: number | null;
  invested_usdt: number | null; final_usdt: number | null; fees_total: number | null;
  pnl_usdt: number | null; pnl_pct: number | null; roi: number | null;
  status: string; n_fills_in: number; n_fills_out: number;
  slippage_estimate: number | null; maker_taker_ratio: number | null;
  data_quality: string;
}

export interface ExecutionsResp {
  page: number; page_size: number; total: number; rows: ExecutionRow[];
}

export interface ExecutionFilters {
  symbol?: string;
  market_type?: string;
  direction?: string;
  status?: string;
  search?: string;
  page?: number;
  page_size?: number;
  sort?: string;
}

export function usePerformanceExecutions(filter: PerformanceFilter, ex: ExecutionFilters) {
  return useSWR<ExecutionsResp>(
    `/api/performance/executions?${qs(filter, ex as Record<string, string | number | undefined>)}`,
    fetcher,
    { refreshInterval: filter.autoRefresh ? 30_000 : 0, revalidateOnFocus: false, keepPreviousData: true }
  );
}
