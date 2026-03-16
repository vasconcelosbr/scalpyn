"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Brain, ChevronDown, ChevronUp } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

export default function StrategySettings() {
  const { config, updateConfig, isLoading } = useConfig("strategy");
  const [strategies, setStrategies] = useState<any[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config?.strategies) setStrategies(config.strategies);
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig({ strategies }); } catch (e) { console.error(e); }
    setSaving(false);
  };

  const toggleStrategy = (id: string) => {
    setStrategies(strategies.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)));
  };

  const updateParam = (stratId: string, paramKey: string, value: any) => {
    setStrategies(strategies.map((s) => s.id === stratId ? { ...s, params: { ...s.params, [paramKey]: value } } : s));
  };

  if (isLoading) return <div className="p-8"><div className="skeleton h-96 w-full" /></div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Strategies Module</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Enable/disable strategies and fine-tune their parameters.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {strategies.map((strat) => (
          <div key={strat.id} className={`card transition-all ${strat.enabled ? "border-[var(--accent-primary-border)]" : ""}`}>
            <div className="p-5">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <Brain className={`w-5 h-5 ${strat.enabled ? "text-[var(--accent-primary)]" : "text-[var(--text-tertiary)]"}`} />
                  <h3 className="font-semibold text-[15px] text-[var(--text-primary)]">{strat.name}</h3>
                </div>
                <div className="flex items-center gap-2">
                  <div className={`toggle ${strat.enabled ? "active" : ""}`} onClick={() => toggleStrategy(strat.id)}>
                    <div className="knob" />
                  </div>
                  <button className="btn-ghost p-1" onClick={() => setExpanded(expanded === strat.id ? null : strat.id)}>
                    {expanded === strat.id ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <span className="text-[11px] font-mono text-[var(--text-tertiary)]">{strat.id}</span>

              {expanded === strat.id && strat.params && (
                <div className="mt-4 pt-4 border-t border-[var(--border-subtle)] space-y-3">
                  {Object.entries(strat.params).map(([key, val]) => (
                    <div key={key} className="flex items-center justify-between">
                      <label className="text-[12px] text-[var(--text-secondary)] font-medium capitalize">{key.replace(/_/g, " ")}</label>
                      <input
                        type="number"
                        step="any"
                        className="input numeric w-24 h-8 text-[13px]"
                        value={val as number}
                        onChange={(e) => updateParam(strat.id, key, parseFloat(e.target.value) || 0)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
