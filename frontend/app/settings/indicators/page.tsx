"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Activity } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

interface IndicatorDef {
  key: string;
  label: string;
  params: { key: string; label: string; type: "number" | "array"; default: any }[];
}

const INDICATOR_DEFS: IndicatorDef[] = [
  { key: "rsi", label: "RSI", params: [{ key: "period", label: "Period", type: "number", default: 14 }] },
  { key: "adx", label: "ADX", params: [{ key: "period", label: "Period", type: "number", default: 14 }] },
  { key: "ema", label: "EMA", params: [{ key: "periods", label: "Periods", type: "array", default: [9, 21, 50, 200] }] },
  { key: "atr", label: "ATR", params: [{ key: "period", label: "Period", type: "number", default: 14 }] },
  { key: "macd", label: "MACD", params: [
    { key: "fast", label: "Fast", type: "number", default: 12 },
    { key: "slow", label: "Slow", type: "number", default: 26 },
    { key: "signal", label: "Signal", type: "number", default: 9 },
  ]},
  { key: "vwap", label: "VWAP", params: [] },
  { key: "stochastic", label: "Stochastic", params: [
    { key: "k", label: "K", type: "number", default: 14 },
    { key: "d", label: "D", type: "number", default: 3 },
    { key: "smooth", label: "Smooth", type: "number", default: 3 },
  ]},
  { key: "obv", label: "OBV", params: [] },
  { key: "bollinger", label: "Bollinger Bands", params: [
    { key: "period", label: "Period", type: "number", default: 20 },
    { key: "deviation", label: "Deviation", type: "number", default: 2.0 },
  ]},
  { key: "parabolic_sar", label: "Parabolic SAR", params: [
    { key: "step", label: "Step", type: "number", default: 0.02 },
    { key: "max_step", label: "Max Step", type: "number", default: 0.2 },
  ]},
  { key: "zscore", label: "Z-Score", params: [{ key: "lookback", label: "Lookback", type: "number", default: 20 }] },
  { key: "volume_delta", label: "Volume Delta", params: [] },
  { key: "funding_rate", label: "Funding Rate", params: [] },
  { key: "btc_dominance", label: "BTC Dominance", params: [] },
];

export default function IndicatorSettings() {
  const { config, updateConfig, isLoading } = useConfig("indicators");
  const [local, setLocal] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      setLocal(config);
    }
  }, [config]);

  const toggleIndicator = (key: string) => {
    setLocal((prev) => ({
      ...prev,
      [key]: { ...prev[key], enabled: !prev[key]?.enabled },
    }));
  };

  const updateParam = (indicatorKey: string, paramKey: string, value: any) => {
    setLocal((prev) => ({
      ...prev,
      [indicatorKey]: { ...prev[indicatorKey], [paramKey]: value },
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateConfig(local);
    } catch (e) {
      console.error(e);
    }
    setSaving(false);
  };

  if (isLoading) {
    return <div className="p-8"><div className="skeleton h-8 w-64 mb-4" /><div className="skeleton h-64 w-full" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Indicators Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Toggle and configure each technical indicator. All changes are dynamic — ZERO HARDCODE.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save Configuration"}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {INDICATOR_DEFS.map((def) => {
          const ind = local[def.key] || { enabled: false };
          return (
            <div key={def.key} className={`card transition-all ${ind.enabled ? "border-[var(--accent-primary-border)]" : ""}`}>
              <div className="p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <Activity className={`w-5 h-5 ${ind.enabled ? "text-[var(--accent-primary)]" : "text-[var(--text-tertiary)]"}`} />
                    <h3 className="font-semibold text-[15px] text-[var(--text-primary)]">{def.label}</h3>
                  </div>
                  <div className={`toggle ${ind.enabled ? "active" : ""}`} onClick={() => toggleIndicator(def.key)}>
                    <div className="knob" />
                  </div>
                </div>

                {ind.enabled && def.params.length > 0 && (
                  <div className="space-y-3 pt-3 border-t border-[var(--border-subtle)]">
                    {def.params.map((param) => (
                      <div key={param.key} className="flex items-center justify-between">
                        <label className="text-[12px] text-[var(--text-secondary)] font-medium">{param.label}</label>
                        {param.type === "number" ? (
                          <input
                            type="number"
                            className="input numeric w-20 h-8 text-[13px]"
                            value={ind[param.key] ?? param.default}
                            onChange={(e) => updateParam(def.key, param.key, parseFloat(e.target.value) || 0)}
                          />
                        ) : (
                          <input
                            type="text"
                            className="input w-32 h-8 text-[13px] font-mono"
                            value={Array.isArray(ind[param.key]) ? ind[param.key].join(", ") : param.default.join(", ")}
                            onChange={(e) =>
                              updateParam(def.key, param.key, e.target.value.split(",").map((v: string) => parseInt(v.trim())).filter(Boolean))
                            }
                          />
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
