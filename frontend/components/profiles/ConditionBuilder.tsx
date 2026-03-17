"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";

interface Condition {
  id: string;
  field: string;
  operator: string;
  value: any;
  required?: boolean;
}

interface ConditionBuilderProps {
  conditions: Condition[];
  onChange: (conditions: Condition[]) => void;
  showRequired?: boolean;
}

// Common indicator fields for the dropdown
const INDICATOR_FIELDS = [
  { value: "volume_24h", label: "Volume 24h", type: "number" },
  { value: "market_cap", label: "Market Cap", type: "number" },
  { value: "price", label: "Price", type: "number" },
  { value: "change_24h", label: "Change 24h %", type: "number" },
  { value: "rsi", label: "RSI", type: "number" },
  { value: "adx", label: "ADX", type: "number" },
  { value: "macd", label: "MACD", type: "number" },
  { value: "macd_histogram", label: "MACD Histogram", type: "number" },
  { value: "stoch_k", label: "Stochastic %K", type: "number" },
  { value: "stoch_d", label: "Stochastic %D", type: "number" },
  { value: "bb_width", label: "Bollinger Width", type: "number" },
  { value: "atr", label: "ATR", type: "number" },
  { value: "atr_percent", label: "ATR %", type: "number" },
  { value: "obv", label: "OBV", type: "number" },
  { value: "vwap_distance_pct", label: "VWAP Distance %", type: "number" },
  { value: "zscore", label: "Z-Score", type: "number" },
  { value: "di_plus", label: "DI+", type: "number" },
  { value: "di_minus", label: "DI-", type: "number" },
  { value: "score", label: "Alpha Score", type: "number" },
  { value: "liquidity_score", label: "Liquidity Score", type: "number" },
  { value: "momentum_score", label: "Momentum Score", type: "number" },
  { value: "volume_spike", label: "Volume Spike", type: "boolean" },
  { value: "ema_full_alignment", label: "EMA Full Alignment", type: "boolean" },
  { value: "ema9_gt_ema50", label: "EMA9 > EMA50", type: "boolean" },
  { value: "ema50_gt_ema200", label: "EMA50 > EMA200", type: "boolean" },
  { value: "psar_trend", label: "PSAR Trend", type: "string" },
  { value: "macd_signal", label: "MACD Signal", type: "string" },
];

const OPERATORS = [
  { value: ">", label: ">" },
  { value: ">=", label: ">=" },
  { value: "<", label: "<" },
  { value: "<=", label: "<=" },
  { value: "==", label: "=" },
  { value: "!=", label: "!=" },
];

const BOOLEAN_OPERATORS = [
  { value: "==", label: "is" },
];

export function ConditionBuilder({
  conditions,
  onChange,
  showRequired = false,
}: ConditionBuilderProps) {
  const addCondition = () => {
    const newCondition: Condition = {
      id: `cond_${Date.now()}`,
      field: "rsi",
      operator: "<",
      value: 30,
      required: false,
    };
    onChange([...conditions, newCondition]);
  };

  const updateCondition = (index: number, updates: Partial<Condition>) => {
    const updated = conditions.map((c, i) =>
      i === index ? { ...c, ...updates } : c
    );
    onChange(updated);
  };

  const removeCondition = (index: number) => {
    onChange(conditions.filter((_, i) => i !== index));
  };

  const getFieldType = (field: string): string => {
    return INDICATOR_FIELDS.find((f) => f.value === field)?.type || "number";
  };

  return (
    <div className="space-y-3">
      {conditions.map((condition, index) => {
        const fieldType = getFieldType(condition.field);
        const operators = fieldType === "boolean" ? BOOLEAN_OPERATORS : OPERATORS;

        return (
          <div
            key={condition.id}
            className="flex items-center gap-2 p-3 bg-[var(--bg-secondary)] rounded-lg"
            data-testid={`condition-${index}`}
          >
            {/* Field Select */}
            <select
              className="input flex-1"
              value={condition.field}
              onChange={(e) => {
                const newFieldType = getFieldType(e.target.value);
                let newValue = condition.value;
                if (newFieldType === "boolean") {
                  newValue = true;
                } else if (typeof condition.value === "boolean") {
                  newValue = 0;
                }
                updateCondition(index, {
                  field: e.target.value,
                  value: newValue,
                });
              }}
              data-testid={`condition-field-${index}`}
            >
              <optgroup label="Price & Volume">
                {INDICATOR_FIELDS.filter((f) =>
                  ["volume_24h", "market_cap", "price", "change_24h"].includes(
                    f.value
                  )
                ).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Momentum">
                {INDICATOR_FIELDS.filter((f) =>
                  ["rsi", "macd", "macd_histogram", "stoch_k", "stoch_d", "zscore"].includes(
                    f.value
                  )
                ).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Trend">
                {INDICATOR_FIELDS.filter((f) =>
                  ["adx", "atr", "atr_percent", "bb_width", "di_plus", "di_minus"].includes(
                    f.value
                  )
                ).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
              <optgroup label="EMA & Alignment">
                {INDICATOR_FIELDS.filter((f) =>
                  f.value.includes("ema") || f.value === "psar_trend"
                ).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Scores">
                {INDICATOR_FIELDS.filter((f) =>
                  f.value.includes("score")
                ).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Other">
                {INDICATOR_FIELDS.filter((f) =>
                  ["volume_spike", "obv", "vwap_distance_pct", "macd_signal"].includes(
                    f.value
                  )
                ).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
            </select>

            {/* Operator Select */}
            <select
              className="input w-20"
              value={condition.operator}
              onChange={(e) => updateCondition(index, { operator: e.target.value })}
              data-testid={`condition-operator-${index}`}
            >
              {operators.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </select>

            {/* Value Input */}
            {fieldType === "boolean" ? (
              <select
                className="input w-24"
                value={condition.value ? "true" : "false"}
                onChange={(e) =>
                  updateCondition(index, { value: e.target.value === "true" })
                }
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
                placeholder="Value"
                data-testid={`condition-value-${index}`}
              />
            ) : (
              <input
                className="input w-28"
                type="number"
                value={condition.value || 0}
                onChange={(e) =>
                  updateCondition(index, { value: parseFloat(e.target.value) || 0 })
                }
                data-testid={`condition-value-${index}`}
              />
            )}

            {/* Required Toggle (for signals) */}
            {showRequired && (
              <label className="flex items-center gap-1 text-[11px] text-[var(--text-secondary)] whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={condition.required || false}
                  onChange={(e) =>
                    updateCondition(index, { required: e.target.checked })
                  }
                  className="w-3 h-3"
                  data-testid={`condition-required-${index}`}
                />
                Required
              </label>
            )}

            {/* Remove Button */}
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

      {/* Add Condition Button */}
      <button
        className="btn btn-secondary w-full"
        onClick={addCondition}
        data-testid="add-condition-btn"
      >
        <Plus className="w-4 h-4 mr-2" />
        Add Condition
      </button>
    </div>
  );
}
