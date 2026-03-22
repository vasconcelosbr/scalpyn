"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Plus, Trash2, Filter } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

const INDICATORS = [
  "volume_24h",
  "adx",
  "spread_pct",
  "rsi",
  "atr_pct",
  "bb_width",
  "vwap_distance_pct",
  "funding_rate_abs",
  "market_cap",
  "price_change_24h",
];

const OPERATORS = [">=", "<=", ">", "<", "=", "!="];

type FilterRule = {
  id: string;
  name: string;
  enabled: boolean;
  indicator: string;
  operator: string;
  value: number | string;
};

export default function FiltersSettings() {
  const { config, updateConfig, isLoading } = useConfig("filters");
  const [enabled, setEnabled] = useState(true);
  const [logic, setLogic] = useState("AND");
  const [filters, setFilters] = useState<FilterRule[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      setEnabled(config.enabled !== false);
      setLogic(config.logic || "AND");
      setFilters(config.filters || []);
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateConfig({ enabled, logic, filters });
    } catch (e) {
      console.error(e);
    }
    setSaving(false);
  };

  const addFilter = () => {
    setFilters([
      ...filters,
      {
        id: `f${Date.now()}`,
        name: "New Filter",
        enabled: true,
        indicator: "volume_24h",
        operator: ">=",
        value: 1000000,
      },
    ]);
  };

  const removeFilter = (id: string) =>
    setFilters(filters.filter((f) => f.id !== id));

  const updateFilter = (id: string, field: string, value: any) =>
    setFilters(filters.map((f) => (f.id === id ? { ...f, [field]: value } : f)));

  if (isLoading)
    return (
      <div className="p-8">
        <div className="skeleton h-96 w-full" />
      </div>
    );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)] flex items-center gap-2">
            <Filter className="w-6 h-6 text-[var(--accent-primary)]" />
            Filters
          </h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Binary pre-filter applied before scoring. Assets that fail any
            enabled filter are excluded from the pipeline entirely.
          </p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? (
            <RefreshCw className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Global toggle + logic */}
      <div className="card">
        <div className="card-body flex flex-wrap items-center gap-6">
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <div
              className={`toggle ${enabled ? "active" : ""}`}
              onClick={() => setEnabled((v) => !v)}
            >
              <div className="knob" />
            </div>
            <span className="text-[13px] font-semibold text-[var(--text-primary)]">
              Filter Engine Enabled
            </span>
          </label>

          <div className="flex items-center gap-3">
            <span className="text-[13px] font-semibold text-[var(--text-primary)]">
              Logic:
            </span>
            <select
              className="input w-24 h-9 text-[13px]"
              value={logic}
              onChange={(e) => setLogic(e.target.value)}
              disabled={!enabled}
            >
              <option value="AND">AND — all must pass</option>
              <option value="OR">OR — any must pass</option>
            </select>
          </div>

          {!enabled && (
            <span className="text-[12px] text-[var(--color-warning)] font-medium">
              Filter engine disabled — all assets pass through to scoring.
            </span>
          )}
        </div>
      </div>

      {/* Filter rules */}
      <div className="card">
        <div className="card-header">
          <h3>Filter Rules</h3>
          <button
            onClick={addFilter}
            className="btn btn-secondary text-[12px] px-3 py-1.5"
            disabled={!enabled}
          >
            <Plus className="w-3.5 h-3.5 mr-1" />
            Add Filter
          </button>
        </div>
        <div className="space-y-3 p-4">
          {filters.map((filt) => (
            <div
              key={filt.id}
              className={`flex items-center gap-3 p-3 rounded-[var(--radius-md)] border transition-opacity ${
                filt.enabled && enabled
                  ? "border-[var(--border-default)] bg-[var(--bg-surface)]"
                  : "border-[var(--border-subtle)] bg-[var(--bg-base)] opacity-50"
              }`}
            >
              {/* Toggle */}
              <div
                className={`toggle ${filt.enabled ? "active" : ""}`}
                onClick={() => updateFilter(filt.id, "enabled", !filt.enabled)}
              >
                <div className="knob" />
              </div>

              {/* Name */}
              <input
                type="text"
                className="input h-8 text-[13px] w-40"
                value={filt.name}
                onChange={(e) => updateFilter(filt.id, "name", e.target.value)}
                placeholder="Filter name"
              />

              {/* Indicator */}
              <select
                className="input h-8 text-[13px] w-36"
                value={filt.indicator}
                onChange={(e) =>
                  updateFilter(filt.id, "indicator", e.target.value)
                }
              >
                {INDICATORS.map((i) => (
                  <option key={i} value={i}>
                    {i}
                  </option>
                ))}
              </select>

              {/* Operator */}
              <select
                className="input h-8 text-[13px] w-20"
                value={filt.operator}
                onChange={(e) =>
                  updateFilter(filt.id, "operator", e.target.value)
                }
              >
                {OPERATORS.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>

              {/* Value */}
              <input
                type="text"
                className="input h-8 text-[13px] w-28 font-mono"
                value={filt.value ?? ""}
                onChange={(e) => {
                  const num = parseFloat(e.target.value);
                  updateFilter(
                    filt.id,
                    "value",
                    isNaN(num) ? e.target.value : num
                  );
                }}
              />

              {/* Delete */}
              <button
                onClick={() => removeFilter(filt.id)}
                className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)] ml-auto"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}

          {filters.length === 0 && (
            <div className="text-center py-8 text-[var(--text-tertiary)] text-[13px]">
              No filter rules defined. All assets will pass through to scoring.
            </div>
          )}
        </div>
      </div>

      {/* Preview */}
      <div className="card">
        <div className="card-header">
          <h3>Filter Logic Preview</h3>
        </div>
        <div className="card-body">
          <pre className="text-[13px] font-mono text-[var(--text-secondary)] bg-[var(--bg-base)] p-4 rounded-[var(--radius-md)] overflow-x-auto">
            {enabled
              ? `FILTER (${logic}):\n` +
                filters
                  .filter((f) => f.enabled)
                  .map((f) => `  ${f.indicator} ${f.operator} ${f.value}  /* ${f.name} */`)
                  .join(`\n  ${logic}\n`) +
                (filters.filter((f) => f.enabled).length === 0
                  ? "  (no active filters)"
                  : "") +
                "\n→ PASS to Score Engine"
              : "Filter engine disabled — all assets pass to Score Engine"}
          </pre>
        </div>
      </div>
    </div>
  );
}
