"use client";

import { Plus, Trash2 } from "lucide-react";

interface Condition {
  id: string;
  field: string;
  operator: string;
  value: any;
  min?: number;   // used when operator === "between"
  max?: number;   // used when operator === "between"
  required?: boolean;
  timeframe?: string;  // override timeframe (e.g. "1m","3m","5m","15m","1h"); empty = inherit profile default
  period?: number;     // indicator period (e.g. 14 for RSI, 7 for volume_spike)
}

interface ConditionBuilderProps {
  conditions: Condition[];
  onChange: (conditions: Condition[]) => void;
  showRequired?: boolean;
  defaultTimeframe?: string;  // profile-level default (e.g. "5m")
}

const INDICATOR_FIELDS = [
  // Price & Volume
  { value: "volume_24h",           label: "Volume 24h",              type: "number",  group: "price" },
  { value: "market_cap",           label: "Market Cap",              type: "number",  group: "price" },
  { value: "price",                label: "Preco",                   type: "number",  group: "price" },
  { value: "change_24h",           label: "Variacao 24h %",          type: "number",  group: "price" },
  // Liquidity
  { value: "spread_pct",           label: "Spread %",                type: "number",  group: "liquidity" },
  { value: "orderbook_depth_usdt", label: "Profundidade Book (USDT)",type: "number",  group: "liquidity" },
  { value: "taker_ratio",          label: "Taker Ratio",             type: "number",  group: "liquidity" },
  { value: "volume_spike",         label: "Volume Spike",            type: "number",  group: "liquidity" },
  { value: "volume_delta",         label: "Volume Delta",            type: "number",  group: "liquidity" },
  { value: "orderbook_pressure",   label: "Orderbook Pressure",      type: "number",  group: "liquidity" },
  { value: "bid_ask_imbalance",    label: "Bid/Ask Imbalance",       type: "number",  group: "liquidity" },
  { value: "obv",                  label: "OBV",                     type: "number",  group: "liquidity" },
  { value: "vwap_distance_pct",    label: "VWAP Distance %",         type: "number",  group: "liquidity" },
  // Momentum
  { value: "rsi",                  label: "RSI",                     type: "number",  group: "momentum" },
  { value: "macd",                 label: "MACD",                    type: "number",  group: "momentum" },
  { value: "macd_histogram",       label: "MACD Histogram",          type: "number",  group: "momentum" },
  { value: "macd_signal",          label: "MACD Signal",             type: "string",  group: "momentum" },
  { value: "stoch_k",              label: "Stochastic %K",           type: "number",  group: "momentum" },
  { value: "stoch_d",              label: "Stochastic %D",           type: "number",  group: "momentum" },
  { value: "zscore",               label: "Z-Score",                 type: "number",  group: "momentum" },
  // Trend & Structure
  { value: "adx",                  label: "ADX",                     type: "number",  group: "trend" },
  { value: "di_plus",              label: "DI+",                     type: "number",  group: "trend" },
  { value: "di_minus",             label: "DI-",                     type: "number",  group: "trend" },
  { value: "di_trend",             label: "DI+ > DI- (Alta)",        type: "boolean", group: "trend" },
  { value: "atr",                  label: "ATR",                     type: "number",  group: "trend" },
  { value: "atr_percent",          label: "ATR %",                   type: "number",  group: "trend" },
  { value: "bb_width",             label: "Bollinger Width",         type: "number",  group: "trend" },
  { value: "psar_trend",           label: "PSAR Trend",              type: "string",  group: "trend" },
  // EMA
  { value: "ema_full_alignment",   label: "EMA Full Alignment",      type: "boolean", group: "ema" },
  { value: "ema9_gt_ema21",        label: "EMA9 > EMA21",            type: "boolean", group: "ema" },
  { value: "ema9_gt_ema50",        label: "EMA9 > EMA50",            type: "boolean", group: "ema" },
  { value: "ema50_gt_ema200",      label: "EMA50 > EMA200",          type: "boolean", group: "ema" },
  // Scores
  { value: "score",                label: "Alpha Score",             type: "number",  group: "scores" },
  { value: "liquidity_score",      label: "Liquidity Score",         type: "number",  group: "scores" },
  { value: "momentum_score",       label: "Momentum Score",          type: "number",  group: "scores" },
];

const OPERATORS = [
  { value: ">",       label: ">"     },
  { value: ">=",      label: ">="    },
  { value: "<",       label: "<"     },
  { value: "<=",      label: "<="    },
  { value: "==",      label: "="     },
  { value: "!=",      label: "!="    },
  { value: "between", label: "entre" },
];

const BOOLEAN_OPERATORS = [
  { value: "==", label: "is" },
];

/** Available timeframe options for per-indicator override */
const TIMEFRAME_OPTIONS = [
  { value: "",    label: "Default" },
  { value: "1m",  label: "1m" },
  { value: "3m",  label: "3m" },
  { value: "5m",  label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h",  label: "1h" },
];

/**
 * Indicators that support a configurable period.
 * The `default` is shown in the input as placeholder and auto-filled on add.
 */
const PERIOD_DEFAULTS: Record<string, number> = {
  rsi:                14,
  adx:                14,
  di_plus:            14,
  di_minus:           14,
  atr:                14,
  atr_percent:        14,
  stoch_k:            14,
  stoch_d:            14,
  macd:               12,
  macd_histogram:     12,
  bb_width:           20,
  zscore:             20,
  volume_spike:       20,
  obv:                20,
  volume_delta:       20,
  vwap_distance_pct:  20,
};

/**
 * Indicators that should NOT show the timeframe/period selectors.
 * These are market metadata, derived booleans, or computed scores.
 */
const NO_TF_INDICATORS = new Set([
  "volume_24h", "market_cap", "price", "change_24h",
  "spread_pct", "orderbook_depth_usdt",
  "score", "liquidity_score", "momentum_score",
  "di_trend", "ema_full_alignment", "ema9_gt_ema21",
  "ema9_gt_ema50", "ema50_gt_ema200", "psar_trend",
  "macd_signal",
]);

export function ConditionBuilder({
  conditions,
  onChange,
  showRequired = false,
  defaultTimeframe = "5m",
}: ConditionBuilderProps) {
  const addCondition = () => {
    onChange([...conditions, {
      id: `cond_${Date.now()}`,
      field: "rsi",
      operator: "<",
      value: 30,
      required: false,
    }]);
  };

  const updateCondition = (index: number, updates: Partial<Condition>) => {
    onChange(conditions.map((c, i) => i === index ? { ...c, ...updates } : c));
  };

  const removeCondition = (index: number) => {
    onChange(conditions.filter((_, i) => i !== index));
  };

  const getFieldType = (field: string) =>
    INDICATOR_FIELDS.find((f) => f.value === field)?.type || "number";

  const fieldsByGroup = (group: string) =>
    INDICATOR_FIELDS.filter((f) => f.group === group);

  return (
    <div className="space-y-3">
      {conditions.map((condition, index) => {
        const fieldType = getFieldType(condition.field);
        const isBetween = condition.operator === "between";
        const operators = fieldType === "boolean" ? BOOLEAN_OPERATORS : OPERATORS;

        return (
          <div
            key={condition.id}
            className="flex items-center gap-2 p-3 bg-[var(--bg-secondary)] rounded-lg flex-wrap"
            data-testid={`condition-${index}`}
          >
            {/* Field Select */}
            <select
              className="input flex-1 min-w-[140px]"
              value={condition.field}
              onChange={(e) => {
                const newType = getFieldType(e.target.value);
                const upd: Partial<Condition> = { field: e.target.value };
                if (newType === "boolean") {
                  upd.value = true;
                  upd.operator = "==";
                } else if (typeof condition.value === "boolean") {
                  upd.value = 0;
                }
                updateCondition(index, upd);
              }}
              data-testid={`condition-field-${index}`}
            >
              <optgroup label="Preco e Volume">
                {fieldsByGroup("price").map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </optgroup>
              <optgroup label="Liquidez Real">
                {fieldsByGroup("liquidity").map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </optgroup>
              <optgroup label="Momentum">
                {fieldsByGroup("momentum").map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </optgroup>
              <optgroup label="Tendencia e Estrutura">
                {fieldsByGroup("trend").map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </optgroup>
              <optgroup label="EMA e Alinhamento">
                {fieldsByGroup("ema").map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </optgroup>
              <optgroup label="Scores">
                {fieldsByGroup("scores").map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </optgroup>
            </select>

            {/* Operator Select */}
            <select
              className="input w-24"
              value={condition.operator}
              onChange={(e) => {
                const op = e.target.value;
                const upd: Partial<Condition> = { operator: op };
                if (op === "between") {
                  upd.min = typeof condition.value === "number" ? condition.value : 0;
                  upd.max = 100;
                  upd.value = undefined;
                } else if (condition.operator === "between") {
                  upd.value = condition.min ?? 0;
                }
                updateCondition(index, upd);
              }}
              data-testid={`condition-operator-${index}`}
            >
              {operators.map((op) => (
                <option key={op.value} value={op.value}>{op.label}</option>
              ))}
            </select>

            {/* Value Input(s) */}
            {fieldType === "boolean" ? (
              <select
                className="input w-24"
                value={condition.value ? "true" : "false"}
                onChange={(e) => updateCondition(index, { value: e.target.value === "true" })}
                data-testid={`condition-value-${index}`}
              >
                <option value="true">True</option>
                <option value="false">False</option>
              </select>
            ) : fieldType === "string" ? (
              <input
                className="input w-32"
                type="text"
                value={condition.value || ""}
                onChange={(e) => updateCondition(index, { value: e.target.value })}
                placeholder="Valor"
                data-testid={`condition-value-${index}`}
              />
            ) : isBetween ? (
              <>
                <input
                  className="input w-20"
                  type="number"
                  value={condition.min ?? 0}
                  onChange={(e) => updateCondition(index, { min: parseFloat(e.target.value) || 0 })}
                  placeholder="Min"
                  data-testid={`condition-min-${index}`}
                />
                <span className="text-[var(--text-secondary)] text-xs font-medium">e</span>
                <input
                  className="input w-20"
                  type="number"
                  value={condition.max ?? 100}
                  onChange={(e) => updateCondition(index, { max: parseFloat(e.target.value) || 0 })}
                  placeholder="Max"
                  data-testid={`condition-max-${index}`}
                />
              </>
            ) : (
              <input
                className="input w-28"
                type="number"
                value={condition.value ?? 0}
                onChange={(e) => updateCondition(index, { value: parseFloat(e.target.value) || 0 })}
                data-testid={`condition-value-${index}`}
              />
            )}

            {/* Timeframe override (only for technical indicators) */}
            {!NO_TF_INDICATORS.has(condition.field) && (
              <select
                className="input w-[72px] text-[11px]"
                value={condition.timeframe || ""}
                onChange={(e) => updateCondition(index, { timeframe: e.target.value || undefined })}
                title={`Timeframe (default: ${defaultTimeframe})`}
                data-testid={`condition-timeframe-${index}`}
              >
                {TIMEFRAME_OPTIONS.map((tf) => (
                  <option key={tf.value} value={tf.value}>
                    {tf.value === "" ? defaultTimeframe : tf.label}
                  </option>
                ))}
              </select>
            )}

            {/* Period input (only for indicators that support it) */}
            {PERIOD_DEFAULTS[condition.field] !== undefined && (
              <input
                className="input w-16 text-[11px] font-mono"
                type="number"
                min={1}
                value={condition.period ?? ""}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  updateCondition(index, { period: isNaN(v) ? undefined : v });
                }}
                placeholder={`P:${PERIOD_DEFAULTS[condition.field]}`}
                title={`Period (default: ${PERIOD_DEFAULTS[condition.field]})`}
                data-testid={`condition-period-${index}`}
              />
            )}

            {showRequired && (
              <label className="flex items-center gap-1 text-[11px] text-[var(--text-secondary)] whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={condition.required || false}
                  onChange={(e) => updateCondition(index, { required: e.target.checked })}
                  className="w-3 h-3"
                  data-testid={`condition-required-${index}`}
                />
                Obrig.
              </label>
            )}

            <button
              className="btn btn-secondary p-2 text-red-500 hover:bg-red-500/10"
              onClick={() => removeCondition(index)}
              data-testid={`remove-condition-${index}`}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}

      <button
        className="btn btn-secondary w-full"
        onClick={addCondition}
        data-testid="add-condition-btn"
      >
        <Plus className="w-4 h-4 mr-2" />
        Adicionar Condicao
      </button>
    </div>
  );
}
