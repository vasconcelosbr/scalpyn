export type ShadowStatus = "PENDING" | "RUNNING" | "COMPLETED" | "ERROR";
export type ShadowOutcome = "TP_HIT" | "SL_HIT" | "TRAILING_STOP" | "TIMEOUT" | null;

export interface ShadowConsolidationProfile {
  rank: number;
  profile_id: string | null;
  profile_name: string | null;
  profile_version: string | null;
  profile_version_id: string | null;
  watchlist_id: string | null;
  watchlist_name: string | null;
  rejection_stage: string | null;
  rejection_reasons: unknown;
  selection_metrics: Record<string, number>;
}

export interface ShadowConsolidation {
  enforcement: boolean;
  event_id: string;
  rule_version: string;
  lane: string | null;
  timeframe: string | null;
  candle_open: string | null;
  primary_profile: ShadowConsolidationProfile;
  candidate_count: number;
  associated_count: number;
  candidates: ShadowConsolidationProfile[];
  associated_profiles: ShadowConsolidationProfile[];
  selection_rule: string[];
  selection_metrics: Record<string, number>;
}

export interface ShadowTradeDetail {
  id: string;
  symbol: string;
  direction: string | null;
  strategy: string | null;
  entry_price: number | null;
  current_price: number | null;
  tp_price: number | null;
  sl_price: number | null;
  exit_price: number | null;
  amount_usdt: number;
  outcome: ShadowOutcome;
  pnl_pct: number | null;
  pnl_usdt: number | null;
  status: ShadowStatus;
  skip_reason: string | null;
  rejected_by_layer?: "L1" | "L2" | "L3" | "NONE" | null;
  rejected_by_rule?: string | null;
  layer_verdicts?: Record<string, unknown> | null;
  holding_seconds: number | null;
  created_at: string | null;
  completed_at: string | null;
  entry_timestamp: string | null;
  exit_timestamp: string | null;
  tp_pct: number | null;
  sl_pct: number | null;
  timeout_candles: number | null;
  decision_id: number | null;
  last_processed_time: string | null;
  updated_at: string | null;
  profile_id: string | null;
  profile_name: string | null;
  consolidation?: ShadowConsolidation | null;
  config_snapshot: Record<string, unknown> | null;
  features_snapshot: Record<string, unknown> | null;
  features_snapshot_exit: Record<string, unknown> | null;
  entry_risk_features?: Record<string, unknown> | null;
  entry_risk_capture_status?: "NOT_AVAILABLE" | "PENDING" | "VALID" | "PARTIAL" | "INVALID" | "ERROR";
  entry_risk_captured_at?: string | null;
  decision_strategy: string | null;
  decision_score: number | null;
  decision_decision: string | null;
  decision_event_type: string | null;
  decision_l1_pass: boolean | null;
  decision_l2_pass: boolean | null;
  decision_l3_pass: boolean | null;
  decision_latency_ms: number | null;
  decision_created_at: string | null;
  decision_reasons: Record<string, unknown> | null;
  decision_metrics: Record<string, unknown> | null;
  entry_metrics: Record<string, unknown> | null;
  exit_metrics: Record<string, unknown> | null;
  profile_version: string | null;
  strategy_type: string | null;
  rules_snapshot: Record<string, unknown> | null;
  ml_probability: number | null;
  ml_model_id: string | null;
  final_priority_score: number | null;
  btc_price_at_entry: number | null;
  btc_change_1h_pct: number | null;
  funding_rate_at_entry: number | null;
  n_concurrent_signals: number | null;
  mae_pct: number | null;
  mfe_pct: number | null;
  max_drawdown_pct: number | null;
  max_profit_pct: number | null;
}

export interface ShadowTradeChartCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface ShadowTradeChartResponse {
  shadow_id: string;
  symbol: string;
  timeframe: string | null;
  exchange: string | null;
  context_minutes: number;
  window_start: string;
  window_end: string;
  entry_timestamp: string;
  exit_timestamp: string;
  entry_price: number | null;
  exit_price: number | null;
  tp_price: number | null;
  sl_price: number | null;
  outcome: ShadowOutcome;
  candles: ShadowTradeChartCandle[];
}
