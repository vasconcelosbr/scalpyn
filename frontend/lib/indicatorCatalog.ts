export type IndicatorKind = "number" | "boolean" | "string";
export type StrategyProfileSection = "filters" | "signals" | "block_rules" | "entry_triggers";
export type IndicatorCategory = "price" | "liquidity" | "price_position" | "momentum" | "trend" | "ema" | "scores";

export interface IndicatorCatalogEntry {
  id: string;
  label: string;
  kind: IndicatorKind;
  category: IndicatorCategory;
  sections: readonly StrategyProfileSection[];
  defaultPeriod?: number;
  fixedPeriod?: number;
  noTimeframe?: boolean;
  requiresReferenceWindow?: boolean;
  unit?: "percent";
}

export interface IndicatorCatalogOption extends IndicatorCatalogEntry {
  value: string;
  group: IndicatorCategory;
  unsupported?: boolean;
}

export const BREAKOUT_REFERENCE_WINDOWS = ["5m", "15m", "30m", "1h"] as const;

const ALL_SECTIONS: readonly StrategyProfileSection[] = [
  "filters", "signals", "block_rules", "entry_triggers",
];
const BLOCK_ONLY: readonly StrategyProfileSection[] = ["block_rules"];
const ENTRY_ONLY: readonly StrategyProfileSection[] = ["entry_triggers"];
const BLOCK_AND_ENTRY: readonly StrategyProfileSection[] = ["block_rules", "entry_triggers"];

const entry = (
  id: string,
  label: string,
  kind: IndicatorKind,
  category: IndicatorCategory,
  extra: Partial<Omit<IndicatorCatalogEntry, "id" | "label" | "kind" | "category">> = {},
): IndicatorCatalogEntry => ({ id, label, kind, category, sections: ALL_SECTIONS, ...extra });

/** Canonical Strategy Profiles catalog. Keep this list additive. */
export const STRATEGY_PROFILE_INDICATORS: readonly IndicatorCatalogEntry[] = [
  entry("volume_24h", "Volume 24h", "number", "price", { noTimeframe: true }),
  entry("market_cap", "Market Cap", "number", "price", { noTimeframe: true }),
  entry("price", "Price", "number", "price", { noTimeframe: true }),
  entry("change_24h", "Variacao 24h %", "number", "price", { noTimeframe: true, unit: "percent" }),

  entry("spread_pct", "Spread %", "number", "liquidity", { noTimeframe: true, unit: "percent" }),
  entry("orderbook_depth_usdt", "Profundidade Book (USDT)", "number", "liquidity", { noTimeframe: true }),
  entry("taker_ratio", "Taker Ratio (buy/(buy+sell), 0-1)", "number", "liquidity", { noTimeframe: true }),
  entry("volume_spike", "Volume Spike", "number", "liquidity", { defaultPeriod: 20 }),
  entry("volume_delta", "Volume Delta", "number", "liquidity", { defaultPeriod: 20 }),
  entry("orderbook_pressure", "Orderbook Pressure", "number", "liquidity", { noTimeframe: true }),
  entry("bid_ask_imbalance", "Bid/Ask Imbalance", "number", "liquidity", { noTimeframe: true }),
  entry("funding_rate", "Funding Rate", "number", "liquidity", { noTimeframe: true }),
  entry("obv", "OBV", "number", "liquidity", { defaultPeriod: 20 }),

  entry("ema5_distance_pct", "EMA 5 Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("ema9_distance_pct", "EMA 9 Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("ema21_distance_pct", "EMA 21 Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("ema50_distance_pct", "EMA 50 Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("ema200_distance_pct", "EMA 200 Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("vwap_distance_pct", "VWAP Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("bb_upper_distance_pct", "BB Upper Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("bb_middle_distance_pct", "BB Middle Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("bb_lower_distance_pct", "BB Lower Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("recent_high_5m_distance_pct", "Recent High 5m Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("recent_high_15m_distance_pct", "Recent High 15m Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("recent_high_30m_distance_pct", "Recent High 30m Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("recent_high_1h_distance_pct", "Recent High 1h Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("recent_low_15m_distance_pct", "Recent Low 15m Distance %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("breakout_distance_pct", "Breakout Distance %", "number", "price_position", {
    noTimeframe: true, requiresReferenceWindow: true, unit: "percent",
  }),
  entry("price_change_1m_pct", "Price Change 1m %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("price_change_5m_pct", "Price Change 5m %", "number", "price_position", { noTimeframe: true, unit: "percent" }),
  entry("price_change_15m_pct", "Price Change 15m %", "number", "price_position", { noTimeframe: true, unit: "percent" }),

  entry("rsi", "RSI", "number", "momentum", { defaultPeriod: 14 }),
  entry("rsi_6", "RSI 6", "number", "momentum", { sections: BLOCK_ONLY, defaultPeriod: 6, fixedPeriod: 6 }),
  entry("rsi_slope_3", "RSI Slope 3", "number", "momentum", { sections: BLOCK_ONLY }),
  entry("macd", "MACD", "number", "momentum", { defaultPeriod: 12 }),
  entry("macd_histogram", "MACD Histogram", "number", "momentum", { defaultPeriod: 12 }),
  entry("macd_hist_slope_3", "MACD Histogram Slope 3", "number", "momentum", { sections: BLOCK_AND_ENTRY }),
  entry("macd_hist_slope_5", "MACD Histogram Slope 5", "number", "momentum", { sections: ENTRY_ONLY }),
  entry("macd_signal", "MACD Signal", "string", "momentum", { noTimeframe: true }),
  entry("stoch_k", "Stochastic %K", "number", "momentum", { defaultPeriod: 14 }),
  entry("stoch_d", "Stochastic %D", "number", "momentum", { defaultPeriod: 14 }),
  entry("zscore", "Z-Score", "number", "momentum", { defaultPeriod: 20 }),

  entry("adx", "ADX", "number", "trend", { defaultPeriod: 14 }),
  entry("adx_acceleration", "ADX Acceleration", "number", "trend", { sections: BLOCK_ONLY }),
  entry("adx_slope_3", "ADX Slope 3", "number", "trend", { sections: BLOCK_ONLY }),
  entry("di_plus", "DI+", "number", "trend", { defaultPeriod: 14 }),
  entry("di_minus", "DI-", "number", "trend", { defaultPeriod: 14 }),
  entry("di_trend", "DI+ > DI- (Alta)", "boolean", "trend", { noTimeframe: true }),
  entry("atr", "ATR", "number", "trend", { defaultPeriod: 14 }),
  entry("atr_pct", "ATR % (atr_pct)", "number", "trend", { defaultPeriod: 14, unit: "percent" }),
  entry("atr_percent", "ATR %", "number", "trend", { defaultPeriod: 14, unit: "percent" }),
  entry("bb_width", "Bollinger Width", "number", "trend", { defaultPeriod: 20 }),
  entry("psar_trend", "PSAR Trend", "string", "trend", { noTimeframe: true }),

  entry("ema5", "EMA 5", "number", "ema", { defaultPeriod: 5 }),
  entry("ema9", "EMA 9", "number", "ema", { defaultPeriod: 9 }),
  entry("ema21", "EMA 21", "number", "ema", { defaultPeriod: 21 }),
  entry("ema50", "EMA 50", "number", "ema", { defaultPeriod: 50 }),
  entry("ema200", "EMA 200", "number", "ema", { defaultPeriod: 200 }),
  entry("ema_full_alignment", "EMA Full Alignment", "boolean", "ema", { noTimeframe: true }),
  entry("ema9_gt_ema21", "EMA9 > EMA21", "boolean", "ema", { noTimeframe: true }),
  entry("ema9_gt_ema50", "EMA9 > EMA50", "boolean", "ema", { noTimeframe: true }),
  entry("ema50_gt_ema200", "EMA50 > EMA200", "boolean", "ema", { noTimeframe: true }),

  entry("alpha_score", "Alpha Score", "number", "scores", { noTimeframe: true }),
  entry("score", "Alpha Score", "number", "scores", { noTimeframe: true }),
  entry("liquidity_score", "Liquidity Score", "number", "scores", { noTimeframe: true }),
  entry("momentum_score", "Momentum Score", "number", "scores", { noTimeframe: true }),
  entry("entry_exhaustion_score", "Entry Exhaustion Score", "number", "scores", { sections: BLOCK_ONLY, noTimeframe: true }),
];

export const STRATEGY_PROFILE_INDICATOR_MAP = new Map(
  STRATEGY_PROFILE_INDICATORS.map((indicator) => [indicator.id, indicator]),
);
export const STRATEGY_PROFILE_INDICATOR_IDS = new Set(STRATEGY_PROFILE_INDICATOR_MAP.keys());

const toOption = (indicator: IndicatorCatalogEntry): IndicatorCatalogOption => ({
  ...indicator, value: indicator.id, group: indicator.category,
});

export const STRATEGY_PROFILE_INDICATOR_OPTIONS: readonly IndicatorCatalogOption[] =
  STRATEGY_PROFILE_INDICATORS.map(toOption);

export function indicatorOptionsForSection(section: StrategyProfileSection): IndicatorCatalogOption[] {
  return STRATEGY_PROFILE_INDICATORS
    .filter((indicator) => indicator.sections.includes(section))
    .map(toOption);
}

export function optionsWithUnsupportedIndicator(
  options: readonly IndicatorCatalogOption[],
  currentId: string | null | undefined,
): IndicatorCatalogOption[] {
  const id = String(currentId || "").trim();
  if (!id || options.some((option) => option.id === id)) return [...options];
  return [{
    id, value: id, label: `${id} — indicador não suportado`, kind: "number",
    category: "scores", group: "scores", sections: [], unsupported: true,
  }, ...options];
}

export const PRICE_POSITION_INDICATORS: IndicatorCatalogOption[] =
  STRATEGY_PROFILE_INDICATOR_OPTIONS.filter((indicator) => indicator.category === "price_position");
export const PRICE_POSITION_INDICATOR_VALUES = new Set(
  PRICE_POSITION_INDICATORS.map((indicator) => indicator.value),
);
export const PROFILE_PERIOD_DEFAULTS: Readonly<Record<string, number>> = Object.fromEntries(
  STRATEGY_PROFILE_INDICATORS
    .filter((indicator) => indicator.defaultPeriod !== undefined)
    .map((indicator) => [indicator.id, indicator.defaultPeriod as number]),
);
export const PROFILE_NO_TIMEFRAME_INDICATORS = new Set(
  STRATEGY_PROFILE_INDICATORS.filter((indicator) => indicator.noTimeframe).map((indicator) => indicator.id),
);
