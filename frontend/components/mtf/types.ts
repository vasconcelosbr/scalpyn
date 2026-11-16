export type Timeframe = '4h' | '1h' | '5m';
export type MTFStatus = 'NO_TRADE' | 'WAIT' | 'ARMED' | 'TRIGGERED';
export interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number; closed: boolean }
export interface Point { time: number; value: number }
export interface Pattern { time: number; pattern: string; metrics: Record<string, number> }
export interface Pivot { time: number; price: number; label: string; kind: string; confirmed_at: number }
export interface Zone { type: string; low: number; high: number; touches: number; available_at: number; distance_atr?: number; in_zone?: boolean; rejection?: boolean }
export interface Frame { candles: Candle[]; indicators: Record<string, Point[]>; patterns: Pattern[]; pivots: Pivot[]; error?: string | null; stale: boolean; source: string }
export interface Plan { side: string; entry_type: string; entry: number; stop: number; tp1: number; tp2: number; rr_net: number; rr_gross: number; quantity: number; notional_usdt: number; expires_at: string; expires_after_candles: number }
export interface Analysis {
  evaluation_id?: string; symbol: string; status: MTFStatus; observed_at_utc: string; evaluated_at_utc: string;
  candle_closed_5m: string | null; candle_close_at_utc?: string; config_hash: string; mode: 'SHADOW'; operational_effect: false;
  context_4h: { trend: string; last_pivots: string[]; strength: string; last_candle_pattern: string | null };
  zone_1h: Zone | null; zones: Zone[]; trigger_5m: (Pattern & { volume_ratio: number | null; type: string }) | null;
  plan: Plan | null; score: { total: number; maximum: number; components: Record<string, number>; calibrated: false };
  vetoes: string[]; explanation: string[]; unavailable_profile_indicators: string[]; spread_pct: number | null; btc_trend: string | null;
}
export interface ChartData { symbol: string; frames: Record<Timeframe, Frame>; analysis: Analysis; as_of: string; profile: Profile | null; indicator_config: Record<string, unknown> }
export interface Profile { id: string; name: string; default_timeframe?: string }
export interface Watchlist { id: string; name: string; profile_id?: string }
export interface Asset { symbol: string; current_price?: number | null; alpha_score?: number | null; profile_id?: string; mtf?: Analysis | null }
export interface MTFConfig {
  schema_version: string; enabled: boolean; calibrated: false; default_watchlist: string; monitored_watchlists: string[];
  poll_seconds: number; history_page_size: number; candle_limits: Record<Timeframe, number>; stale_tf_multiplier: number;
  [key: string]: unknown;
}
export interface Preferences { theme: 'dark' | 'light'; timezone: 'America/Sao_Paulo' | 'UTC'; sound: boolean; aligned_only: boolean }
export interface Bootstrap { config: MTFConfig | null; preset: MTFConfig; watchlists: Watchlist[]; profiles: Profile[]; preferences: Partial<Preferences> }
export interface HistoryItem { evaluation: Analysis; observed_at: string; shadow: { status: string; pnl_pct?: number | null; pnl_usdt?: number | null; fill?: number; ambiguous?: boolean; reason?: string } | null }
export interface MTFReport { groups: { pattern: string; config_hash: string; n: number; mean_pct: number; ambiguous: number; sample_sufficient: boolean; confidence_interval: [number,number] | null; fee_per_side_pct: number; slippage_pct: number }[]; confidence_level: number; formula: string; origin: string; method: string }
export const statusLabels: Record<MTFStatus, string> = { NO_TRADE: 'SEM TRADE', WAIT: 'AGUARDAR', ARMED: 'ARMADO', TRIGGERED: 'GATILHO' };
export const patternLabels: Record<string, string> = { ENGULF_BULL: 'Engolfo de alta', ENGULF_BEAR: 'Engolfo de baixa', PIN_BULL: 'Martelo / Pin de alta', PIN_BEAR: 'Pin de baixa', SHOOTING_STAR: 'Estrela cadente', DOJI: 'Doji', HARAMI: 'Harami', INSIDE_BAR: 'Inside Bar', MARUBOZU_BULL: 'Marubozu de alta', MARUBOZU_BEAR: 'Marubozu de baixa' };
export const patternShort: Record<string, string> = { ENGULF_BULL: 'ENG', ENGULF_BEAR: 'ENG', PIN_BULL: 'MART', PIN_BEAR: 'PIN', SHOOTING_STAR: 'EC', DOJI: 'DOJI', HARAMI: 'HAR', INSIDE_BAR: 'IB', MARUBOZU_BULL: 'MARU', MARUBOZU_BEAR: 'MARU' };
export const number = (value: number | undefined | null, digits = 5) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('pt-BR', { maximumFractionDigits: digits });
