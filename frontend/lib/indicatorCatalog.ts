export type IndicatorKind = "number" | "boolean" | "string";

export interface IndicatorCatalogOption {
  value: string;
  label: string;
  kind: IndicatorKind;
  group: "price_position";
}

export const BREAKOUT_REFERENCE_WINDOWS = ["5m", "15m", "30m", "1h"] as const;

export const PRICE_POSITION_INDICATORS: IndicatorCatalogOption[] = [
  { value: "ema5_distance_pct", label: "EMA 5 Distance %", kind: "number", group: "price_position" },
  { value: "ema9_distance_pct", label: "EMA 9 Distance %", kind: "number", group: "price_position" },
  { value: "ema21_distance_pct", label: "EMA 21 Distance %", kind: "number", group: "price_position" },
  { value: "ema50_distance_pct", label: "EMA 50 Distance %", kind: "number", group: "price_position" },
  { value: "ema200_distance_pct", label: "EMA 200 Distance %", kind: "number", group: "price_position" },
  { value: "vwap_distance_pct", label: "VWAP Distance %", kind: "number", group: "price_position" },
  { value: "bb_upper_distance_pct", label: "BB Upper Distance %", kind: "number", group: "price_position" },
  { value: "bb_middle_distance_pct", label: "BB Middle Distance %", kind: "number", group: "price_position" },
  { value: "bb_lower_distance_pct", label: "BB Lower Distance %", kind: "number", group: "price_position" },
  { value: "recent_high_5m_distance_pct", label: "Recent High 5m Distance %", kind: "number", group: "price_position" },
  { value: "recent_high_15m_distance_pct", label: "Recent High 15m Distance %", kind: "number", group: "price_position" },
  { value: "recent_high_30m_distance_pct", label: "Recent High 30m Distance %", kind: "number", group: "price_position" },
  { value: "recent_high_1h_distance_pct", label: "Recent High 1h Distance %", kind: "number", group: "price_position" },
  { value: "recent_low_15m_distance_pct", label: "Recent Low 15m Distance %", kind: "number", group: "price_position" },
  { value: "breakout_distance_pct", label: "Breakout Distance %", kind: "number", group: "price_position" },
  { value: "price_change_1m_pct", label: "Price Change 1m %", kind: "number", group: "price_position" },
  { value: "price_change_5m_pct", label: "Price Change 5m %", kind: "number", group: "price_position" },
  { value: "price_change_15m_pct", label: "Price Change 15m %", kind: "number", group: "price_position" },
];

export const PRICE_POSITION_INDICATOR_VALUES = new Set(
  PRICE_POSITION_INDICATORS.map((indicator) => indicator.value),
);
