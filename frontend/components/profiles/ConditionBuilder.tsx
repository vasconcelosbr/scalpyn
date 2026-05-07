"use client";

import { Plus, Trash2 } from "lucide-react";

type ConditionValue = string | number | boolean | null | undefined;

export interface ScoreRule {
  id: string;
  indicator: string;
  operator: string;
  value?: ConditionValue;
  min?: number;
  max?: number;
  points?: number;
  category?: string;
}

interface Condition {
  id: string;
  field: string;
  operator: string;
  value: ConditionValue;
  min?: number;
  max?: number;
  required?: boolean;
  timeframe?: string;
  period?: number;
  rule_id?: string;
  points?: number;
  category?: string;
}

interface ConditionBuilderProps {
  conditions: Condition[];
  onChange: (conditions: Condition[]) => void;
  showRequired?: boolean;
  defaultTimeframe?: string;
  scoreRules?: ScoreRule[];
  showPoints?: boolean;
}

const INDICATOR_FIELDS = [
  { value: "volume_24h", label: "Volume 24h", type: "number", group: "price" },
  { value: "market_cap", label: "Market Cap", type: "number", group: "price" },
  { value: "price", label: "Preco", type: "number", group: "price" },
  { value: "change_24h", label: "Variacao 24h %", type: "number", group: "price" },
  { value: "spread_pct", label: "Spread %", type: "number", group: "liquidity" },
  { value: "orderbook_depth_usdt", label: "Profundidade Book (USDT)", type: "number", group: "liquidity" },
  { value: "taker_ratio", label: "Taker Ratio (buy/(buy+sell), 0-1)", type: "number", group: "liquidity" },
  { value: "volume_spike", label: "Volume Spike", type: "number", group: "liquidity" },
  { value: "volume_delta", label: "Volume Delta", type: "number", group: "liquidity" },
  { value: "orderbook_pressure", label: "Orderbook Pressure", type: "number", group: "liquidity" },
  { value: "bid_ask_imbalance", label: "Bid/Ask Imbalance", type: "number", group: "liquidity" },
  { value: "obv", label: "OBV", type: "number", group: "liquidity" },
  { value: "vwap_distance_pct", label: "VWAP Distance %", type: "number", group: "liquidity" },
  { value: "rsi", label: "RSI", type: "number", group: "momentum" },
  { value: "macd", label: "MACD", type: "number", group: "momentum" },
  { value: "macd_histogram", label: "MACD Histogram", type: "number", group: "momentum" },
  { value: "macd_signal", label: "MACD Signal", type: "string", group: "momentum" },
  { value: "stoch_k", label: "Stochastic %K", type: "number", group: "momentum" },
  { value: "stoch_d", label: "Stochastic %D", type: "number", group: "momentum" },
  { value: "zscore", label: "Z-Score", type: "number", group: "momentum" },
  { value: "adx", label: "ADX", type: "number", group: "trend" },
  { value: "di_plus", label: "DI+", type: "number", group: "trend" },
  { value: "di_minus", label: "DI-", type: "number", group: "trend" },
  { value: "di_trend", label: "DI+ > DI- (Alta)", type: "boolean", group: "trend" },
  { value: "atr", label: "ATR", type: "number", group: "trend" },
  { value: "atr_percent", label: "ATR %", type: "number", group: "trend" },
  { value: "bb_width", label: "Bollinger Width", type: "number", group: "trend" },
  { value: "psar_trend", label: "PSAR Trend", type: "string", group: "trend" },
  { value: "ema_full_alignment", label: "EMA Full Alignment", type: "boolean", group: "ema" },
  { value: "ema9_gt_ema21", label: "EMA9 > EMA21", type: "boolean", group: "ema" },
  { value: "ema9_gt_ema50", label: "EMA9 > EMA50", type: "boolean", group: "ema" },
  { value: "ema50_gt_ema200", label: "EMA50 > EMA200", type: "boolean", group: "ema" },
  { value: "score", label: "Alpha Score", type: "number", group: "scores" },
  { value: "liquidity_score", label: "Liquidity Score", type: "number", group: "scores" },
  { value: "momentum_score", label: "Momentum Score", type: "number", group: "scores" },
];

const OPERATORS = [
  { value: ">", label: ">" },
  { value: ">=", label: ">=" },
  { value: "<", label: "<" },
  { value: "<=", label: "<=" },
  { value: "==", label: "=" },
  { value: "!=", label: "!=" },
  { value: "between", label: "entre" },
];

const BOOLEAN_OPERATORS = [{ value: "==", label: "is" }];

const TIMEFRAME_OPTIONS = [
  { value: "", label: "Default" },
  { value: "1m", label: "1m" },
  { value: "3m", label: "3m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1h" },
];

const PERIOD_DEFAULTS: Record<string, number> = {
  rsi: 14,
  adx: 14,
  di_plus: 14,
  di_minus: 14,
  atr: 14,
  atr_percent: 14,
  stoch_k: 14,
  stoch_d: 14,
  macd: 12,
  macd_histogram: 12,
  bb_width: 20,
  zscore: 20,
  volume_spike: 20,
  obv: 20,
  volume_delta: 20,
  vwap_distance_pct: 20,
};

const NO_TF_INDICATORS = new Set([
  "volume_24h", "market_cap", "price", "change_24h",
  "spread_pct", "orderbook_depth_usdt",
  "score", "liquidity_score", "momentum_score",
  "di_trend", "ema_full_alignment", "ema9_gt_ema21",
  "ema9_gt_ema50", "ema50_gt_ema200", "psar_trend",
  "macd_signal",
]);

function buildRuleLabel(rule: ScoreRule) {
  const points = Number(rule.points ?? 0);
  const category = (rule.category || "").replaceAll("_", " ");
  if (rule.operator === "between") {
    return `${rule.operator} ${rule.min ?? "?"} - ${rule.max ?? "?"} • ${points} pts${category ? ` • ${category}` : ""}`;
  }
  return `${rule.operator} ${rule.value ?? "—"} • ${points} pts${category ? ` • ${category}` : ""}`;
}

function applyScoreRule(rule: ScoreRule): Partial<Condition> {
  return {
    field: rule.indicator,
    operator: rule.operator,
    value: rule.operator === "between" ? undefined : rule.value,
    min: rule.operator === "between" ? rule.min : undefined,
    max: rule.operator === "between" ? rule.max : undefined,
    rule_id: rule.id,
    points: Number(rule.points ?? 0),
    category: rule.category,
  };
}

export function ConditionBuilder({
  conditions,
  onChange,
  showRequired = false,
  defaultTimeframe = "5m",
  scoreRules = [],
  showPoints = false,
}: ConditionBuilderProps) {
  const getFieldType = (field: string) =>
    INDICATOR_FIELDS.find((candidate) => candidate.value === field)?.type || "number";

  const fieldsByGroup = (group: string) =>
    INDICATOR_FIELDS.filter((field) => field.group === group);

  const getRulesForField = (field: string) =>
    scoreRules.filter((rule) => rule.indicator === field);

  const getSelectedRule = (condition: Condition) =>
    getRulesForField(condition.field).find((rule) => rule.id === condition.rule_id);

  const updateCondition = (index: number, updates: Partial<Condition>) => {
    onChange(conditions.map((condition, currentIndex) => (
      currentIndex === index ? { ...condition, ...updates } : condition
    )));
  };

  const addCondition = () => {
    const defaultRule = showPoints ? getRulesForField("rsi")[0] : undefined;
    onChange([
      ...conditions,
      {
        id: `cond_${Date.now()}`,
        field: defaultRule?.indicator || "rsi",
        operator: defaultRule?.operator || "<",
        value: defaultRule?.operator === "between" ? undefined : (defaultRule?.value ?? 30),
        min: defaultRule?.operator === "between" ? defaultRule.min : undefined,
        max: defaultRule?.operator === "between" ? defaultRule.max : undefined,
        required: false,
        rule_id: defaultRule?.id,
        points: Number(defaultRule?.points ?? 0),
        category: defaultRule?.category,
      },
    ]);
  };

  const removeCondition = (index: number) => {
    onChange(conditions.filter((_, currentIndex) => currentIndex !== index));
  };

  return (
    <div className="space-y-3">
      {conditions.map((condition, index) => {
        const fieldType = getFieldType(condition.field);
        const isBetween = condition.operator === "between";
        const operators = fieldType === "boolean" ? BOOLEAN_OPERATORS : OPERATORS;
        const availableRules = getRulesForField(condition.field);
        const selectedRule = getSelectedRule(condition);
        const ruleLocked = Boolean(showPoints && selectedRule);
        const points = Number(selectedRule?.points ?? condition.points ?? 0);

        return (
          <div
            key={condition.id}
            className="flex items-center gap-2 p-3 bg-[var(--bg-secondary)] rounded-lg flex-wrap"
            data-testid={`condition-${index}`}
          >
            <select
              className="input flex-1 min-w-[140px]"
              value={condition.field}
              onChange={(event) => {
                const newField = event.target.value;
                const newType = getFieldType(newField);
                const fieldRules = getRulesForField(newField);
                const firstRule = showPoints ? fieldRules[0] : undefined;
                const updates: Partial<Condition> = {
                  field: newField,
                  rule_id: undefined,
                  points: 0,
                  category: undefined,
                };

                if (firstRule) {
                  Object.assign(updates, applyScoreRule(firstRule));
                } else if (newType === "boolean") {
                  updates.value = true;
                  updates.operator = "==";
                  updates.min = undefined;
                  updates.max = undefined;
                } else if (typeof condition.value === "boolean") {
                  updates.value = 0;
                }

                updateCondition(index, updates);
              }}
              data-testid={`condition-field-${index}`}
            >
              <optgroup label="Preco e Volume">
                {fieldsByGroup("price").map((field) => (
                  <option key={field.value} value={field.value}>{field.label}</option>
                ))}
              </optgroup>
              <optgroup label="Liquidez Real">
                {fieldsByGroup("liquidity").map((field) => (
                  <option key={field.value} value={field.value}>{field.label}</option>
                ))}
              </optgroup>
              <optgroup label="Momentum">
                {fieldsByGroup("momentum").map((field) => (
                  <option key={field.value} value={field.value}>{field.label}</option>
                ))}
              </optgroup>
              <optgroup label="Tendencia e Estrutura">
                {fieldsByGroup("trend").map((field) => (
                  <option key={field.value} value={field.value}>{field.label}</option>
                ))}
              </optgroup>
              <optgroup label="EMA e Alinhamento">
                {fieldsByGroup("ema").map((field) => (
                  <option key={field.value} value={field.value}>{field.label}</option>
                ))}
              </optgroup>
              <optgroup label="Scores">
                {fieldsByGroup("scores").map((field) => (
                  <option key={field.value} value={field.value}>{field.label}</option>
                ))}
              </optgroup>
            </select>

            {showPoints && availableRules.length > 0 && (
              <select
                className="input min-w-[220px] max-w-[280px]"
                value={condition.rule_id ?? ""}
                onChange={(event) => {
                  const rule = availableRules.find((candidate) => candidate.id === event.target.value);
                  if (!rule) {
                    updateCondition(index, { rule_id: undefined, points: 0, category: undefined });
                    return;
                  }
                  updateCondition(index, applyScoreRule(rule));
                }}
                data-testid={`condition-rule-${index}`}
              >
                <option value="">Manual</option>
                {availableRules.map((rule) => (
                  <option key={rule.id} value={rule.id}>
                    {buildRuleLabel(rule)}
                  </option>
                ))}
              </select>
            )}

            <select
              className="input w-24"
              value={condition.operator}
              onChange={(event) => {
                const operator = event.target.value;
                const updates: Partial<Condition> = {
                  operator,
                  rule_id: undefined,
                  points: 0,
                  category: undefined,
                };
                if (operator === "between") {
                  updates.min = typeof condition.value === "number" ? condition.value : 0;
                  updates.max = 100;
                  updates.value = undefined;
                } else if (condition.operator === "between") {
                  updates.value = condition.min ?? 0;
                }
                updateCondition(index, updates);
              }}
              disabled={ruleLocked}
              data-testid={`condition-operator-${index}`}
            >
              {operators.map((operator) => (
                <option key={operator.value} value={operator.value}>{operator.label}</option>
              ))}
            </select>

            {fieldType === "boolean" ? (
              <select
                className="input w-24"
                value={condition.value ? "true" : "false"}
                onChange={(event) => updateCondition(index, {
                  value: event.target.value === "true",
                  rule_id: undefined,
                  points: 0,
                  category: undefined,
                })}
                disabled={ruleLocked}
                data-testid={`condition-value-${index}`}
              >
                <option value="true">True</option>
                <option value="false">False</option>
              </select>
            ) : fieldType === "string" ? (
              <input
                className="input w-32"
                type="text"
                value={(condition.value as string) || ""}
                onChange={(event) => updateCondition(index, {
                  value: event.target.value,
                  rule_id: undefined,
                  points: 0,
                  category: undefined,
                })}
                disabled={ruleLocked}
                placeholder="Valor"
                data-testid={`condition-value-${index}`}
              />
            ) : isBetween ? (
              <>
                <input
                  className="input w-20"
                  type="number"
                  value={condition.min ?? 0}
                  onChange={(event) => updateCondition(index, {
                    min: parseFloat(event.target.value) || 0,
                    rule_id: undefined,
                    points: 0,
                    category: undefined,
                  })}
                  disabled={ruleLocked}
                  placeholder="Min"
                  data-testid={`condition-min-${index}`}
                />
                <span className="text-[var(--text-secondary)] text-xs font-medium">e</span>
                <input
                  className="input w-20"
                  type="number"
                  value={condition.max ?? 100}
                  onChange={(event) => updateCondition(index, {
                    max: parseFloat(event.target.value) || 0,
                    rule_id: undefined,
                    points: 0,
                    category: undefined,
                  })}
                  disabled={ruleLocked}
                  placeholder="Max"
                  data-testid={`condition-max-${index}`}
                />
              </>
            ) : (
              <input
                className="input w-28"
                type="number"
                value={typeof condition.value === "number" ? condition.value : Number(condition.value ?? 0)}
                onChange={(event) => updateCondition(index, {
                  value: parseFloat(event.target.value) || 0,
                  rule_id: undefined,
                  points: 0,
                  category: undefined,
                })}
                disabled={ruleLocked}
                data-testid={`condition-value-${index}`}
              />
            )}

            {showPoints && (
              <div className="min-w-[88px]">
                <div className="px-3 py-2 rounded-md border border-[var(--border-default)] bg-[var(--bg-primary)] text-[12px] font-semibold text-[var(--text-primary)] text-center">
                  {points} pts
                </div>
                {selectedRule?.category && (
                  <div className="mt-1 text-[10px] text-[var(--text-tertiary)] text-center capitalize">
                    {selectedRule.category.replaceAll("_", " ")}
                  </div>
                )}
              </div>
            )}

            {!NO_TF_INDICATORS.has(condition.field) && (
              <select
                className="input w-[72px] text-[11px]"
                value={condition.timeframe || ""}
                onChange={(event) => updateCondition(index, { timeframe: event.target.value || undefined })}
                title={`Timeframe (default: ${defaultTimeframe})`}
                data-testid={`condition-timeframe-${index}`}
              >
                {TIMEFRAME_OPTIONS.map((timeframe) => (
                  <option key={timeframe.value} value={timeframe.value}>
                    {timeframe.value === "" ? defaultTimeframe : timeframe.label}
                  </option>
                ))}
              </select>
            )}

            {PERIOD_DEFAULTS[condition.field] !== undefined && (
              <input
                className="input w-16 text-[11px] font-mono"
                type="number"
                min={1}
                value={condition.period ?? ""}
                onChange={(event) => {
                  const value = parseInt(event.target.value, 10);
                  updateCondition(index, { period: Number.isNaN(value) ? undefined : value });
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
                  onChange={(event) => updateCondition(index, { required: event.target.checked })}
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
