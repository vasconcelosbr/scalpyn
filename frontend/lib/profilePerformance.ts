export type ProfileTrend = "IMPROVING" | "STABLE" | "DETERIORATING" | "INSUFFICIENT_DATA";
export type ProfileMonitorStatus = "POSITIVE" | "STABLE" | "ATTENTION" | "DETERIORATING" | "LOW_SAMPLE";
export type ProfilePerformanceMetric = "ev_score" | "win_rate" | "pnl_usdt" | "trades" | "holding_seconds";
export type ProfileDailyRange = "7d" | "15d" | "30d" | "90d" | "total";
export type ProfilePerformanceSortKey =
  | "trades"
  | "ev_score"
  | "ev_delta"
  | "win_rate"
  | "win_rate_delta_pp"
  | "pnl_day_usdt"
  | "pnl_period_usdt"
  | "holding_seconds";

export interface ProfileTrendEvidence {
  points: number;
  slope: number;
  net_change: number;
  positive_days: number;
  negative_days: number;
}

export interface ProfilePerformanceHistoryPoint {
  date: string;
  trades: number;
  closed_trades: number;
  tp: number;
  sl: number;
  timeout: number;
  ev_score: number;
  win_rate: number | null;
  pnl_usdt: number;
  holding_seconds: number | null;
}

export interface ProfilePerformanceRow {
  rank: number;
  profile_id: string;
  profile_name: string;
  watchlist_name: string | null;
  trades: number;
  closed_trades: number;
  open_trades: number;
  tp: number;
  sl: number;
  timeout: number;
  ev_score: number;
  ev_delta: number | null;
  win_rate: number | null;
  win_rate_delta_pp: number | null;
  pnl_day_usdt: number;
  pnl_period_usdt: number;
  avg_pnl_pct: number | null;
  holding_seconds: number | null;
  trend: ProfileTrend;
  trend_evidence: ProfileTrendEvidence;
  sample_status: string;
  status: ProfileMonitorStatus;
  priority: string;
  priority_reason: string;
  history: ProfilePerformanceHistoryPoint[];
}

export interface ProfilePerformanceHighlight {
  profile_id: string;
  profile_name: string;
  ev_score: number;
  ev_delta: number | null;
  ev_period_change: number | null;
  win_rate: number | null;
  pnl_period_usdt: number;
}

export interface ProfilePerformanceResponse {
  contract_version: string;
  as_of: string;
  range_days: number;
  timezone: string;
  available_from: string | null;
  available_to: string | null;
  summary: {
    active_profiles: number;
    ev_score_mean: number | null;
    ev_score_delta: number | null;
    win_rate: number | null;
    win_rate_delta_pp: number | null;
    pnl_day_usdt: number;
    pnl_period_usdt: number;
    trades_period: number;
    closed_trades_period: number;
    alerts: number;
  };
  highlights: {
    best_profile: ProfilePerformanceHighlight | null;
    biggest_improvement: ProfilePerformanceHighlight | null;
    biggest_deterioration: ProfilePerformanceHighlight | null;
    highest_pnl: ProfilePerformanceHighlight | null;
  };
  profiles: ProfilePerformanceRow[];
  metric_definitions: Record<string, string>;
}

export interface ProfileDailyPerformancePoint {
  date: string;
  closed_trades: number;
  wins: number;
  win_rate: number | null;
  pnl_usdt: number;
}

export interface ProfileDailyPerformanceResponse {
  contract_version: string;
  as_of: string;
  range: ProfileDailyRange;
  timezone: string;
  points: ProfileDailyPerformancePoint[];
  metric_definitions: Record<string, string>;
}

export const STATUS_LABEL: Record<ProfileMonitorStatus, string> = {
  POSITIVE: "Positivo",
  STABLE: "Estável",
  ATTENTION: "Atenção",
  DETERIORATING: "Deteriorando",
  LOW_SAMPLE: "Amostra baixa",
};

export const TREND_LABEL: Record<ProfileTrend, string> = {
  IMPROVING: "Melhorando",
  STABLE: "Estável",
  DETERIORATING: "Deteriorando",
  INSUFFICIENT_DATA: "Sem histórico",
};

export function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatRate(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function formatSigned(value: number | null | undefined, suffix = ""): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}${suffix}`;
}

export function formatHolding(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

export function sortProfileRows(
  rows: ProfilePerformanceRow[], key: ProfilePerformanceSortKey, direction: "asc" | "desc",
): ProfilePerformanceRow[] {
  const factor = direction === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = left[key];
    const b = right[key];
    if (a == null && b == null) return left.rank - right.rank;
    if (a == null) return 1;
    if (b == null) return -1;
    const compared = (a - b) * factor;
    return compared || left.rank - right.rank;
  });
}

export function filterProfileRows(
  rows: ProfilePerformanceRow[],
  filters: { profileId: string; status: "ALL" | ProfileMonitorStatus; search: string },
): ProfilePerformanceRow[] {
  const normalizedSearch = filters.search.trim().toLowerCase();
  return rows.filter((row) => {
    if (filters.profileId && row.profile_id !== filters.profileId) return false;
    if (filters.status !== "ALL" && row.status !== filters.status) return false;
    return !normalizedSearch
      || `${row.profile_name} ${row.watchlist_name ?? ""}`.toLowerCase().includes(normalizedSearch);
  });
}

export function profilePerformanceRequestPath(asOf: string, rangeDays: 7 | 14 | 30): string {
  const params = new URLSearchParams({ as_of: asOf, range_days: String(rangeDays) });
  return `/api/shadow-portfolio/profile-performance?${params}`;
}

export function profileDailyPerformanceRequestPath(asOf: string, range: ProfileDailyRange): string {
  const params = new URLSearchParams({ as_of: asOf, range });
  return `/api/shadow-portfolio/profile-performance/daily?${params}`;
}

export function historyMetricValue(
  point: ProfilePerformanceHistoryPoint, metric: ProfilePerformanceMetric,
): number | null {
  return point[metric];
}

export function heatmapColor(
  metric: ProfilePerformanceMetric, value: number | null, minimum: number, maximum: number,
): string {
  if (value == null || !Number.isFinite(value)) return "rgba(90,96,117,0.18)";
  if (metric === "pnl_usdt") {
    const magnitude = Math.min(Math.abs(value) / Math.max(Math.abs(minimum), Math.abs(maximum), 1), 1);
    if (value > 0) return `rgba(34,185,122,${(0.18 + magnitude * 0.72).toFixed(3)})`;
    if (value < 0) return `rgba(229,72,77,${(0.18 + magnitude * 0.72).toFixed(3)})`;
    return "rgba(90,96,117,0.20)";
  }
  const normalized = maximum === minimum ? 0.5 : Math.max(0, Math.min(1, (value - minimum) / (maximum - minimum)));
  if (metric === "trades") return `rgba(79,123,247,${(0.16 + normalized * 0.72).toFixed(3)})`;
  if (metric === "holding_seconds") return `rgba(157,124,247,${(0.16 + normalized * 0.66).toFixed(3)})`;
  const red = Math.round(229 - normalized * 195);
  const green = Math.round(72 + normalized * 113);
  const blue = Math.round(77 + normalized * 45);
  return `rgba(${red},${green},${blue},${(0.28 + normalized * 0.58).toFixed(3)})`;
}
