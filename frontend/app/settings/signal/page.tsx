"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Plus, Trash2, Zap } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

export default function SignalSettings() {
  const { config, updateConfig, isLoading } = useConfig("signal");
  const [logic, setLogic] = useState("AND");
  const [conditions, setConditions] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      setLogic(config.logic || "AND");
      setConditions(config.conditions || []);
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig({ logic, conditions }); } catch (e) { console.error(e); }
    setSaving(false);
  };

  const addCondition = () => {
    setConditions([...conditions, { id: `s${Date.now()}`, indicator: "alpha_score", operator: ">", value: 70, required: false, enabled: true }]);
  };

  const removeCondition = (id: string) => setConditions(conditions.filter((c) => c.id !== id));

  const updateCondition = (id: string, field: string, value: any) => {
    setConditions(conditions.map((c) => (c.id === id ? { ...c, [field]: value } : c)));
  };

  if (isLoading) return <div className="p-8"><div className="skeleton h-96 w-full" /></div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Signal Rules</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Define conditions that trigger buy/sell signals. Required conditions must always pass.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {/* Logic Mode */}
      <div className="card">
        <div className="card-body flex items-center gap-4">
          <span className="text-[13px] font-semibold text-[var(--text-primary)]">Logic Mode:</span>
          <select className="input w-24 h-9 text-[13px]" value={logic} onChange={(e) => setLogic(e.target.value)}>
            <option value="AND">AND</option>
            <option value="OR">OR</option>
          </select>
          <span className="text-[12px] text-[var(--text-secondary)]">
            {logic === "AND" ? "All required + at least one optional must pass" : "All required + any optional must pass"}
          </span>
        </div>
      </div>

      {/* Conditions */}
      <div className="card">
        <div className="card-header">
          <h3>Signal Conditions</h3>
          <button onClick={addCondition} className="btn btn-secondary text-[12px] px-3 py-1.5"><Plus className="w-3.5 h-3.5 mr-1" />Add Condition</button>
        </div>
        <div className="space-y-3 p-4">
          {conditions.map((cond) => (
            <div key={cond.id} className={`flex items-center gap-3 p-3 rounded-[var(--radius-md)] border ${cond.enabled ? "border-[var(--border-default)] bg-[var(--bg-surface)]" : "border-[var(--border-subtle)] bg-[var(--bg-base)] opacity-60"}`}>
              <div className={`toggle ${cond.enabled ? "active" : ""}`} onClick={() => updateCondition(cond.id, "enabled", !cond.enabled)}>
                <div className="knob" />
              </div>
              <select className="input h-8 text-[13px] w-36" value={cond.indicator} onChange={(e) => updateCondition(cond.id, "indicator", e.target.value)}>
                {["alpha_score", "rsi", "adx", "volume_spike", "macd_signal", "ema_alignment", "stoch_k", "atr_pct", "vwap_distance_pct", "bb_width"].map((i) => (
                  <option key={i} value={i}>{i}</option>
                ))}
              </select>
              <select className="input h-8 text-[13px] w-20" value={cond.operator} onChange={(e) => updateCondition(cond.id, "operator", e.target.value)}>
                {[">", "<", ">=", "<=", "=", "!="].map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              <input type="text" className="input h-8 text-[13px] w-20 font-mono" value={cond.value ?? ""} onChange={(e) => {
                const num = parseFloat(e.target.value);
                updateCondition(cond.id, "value", isNaN(num) ? e.target.value : num);
              }} />
              <label className="flex items-center gap-1.5 text-[12px] cursor-pointer">
                <input type="checkbox" checked={cond.required} onChange={(e) => updateCondition(cond.id, "required", e.target.checked)} className="accent-[var(--accent-primary)]" />
                <span className={cond.required ? "text-[var(--color-warning)] font-semibold" : "text-[var(--text-secondary)]"}>Required</span>
              </label>
              <button onClick={() => removeCondition(cond.id)} className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)] ml-auto"><Trash2 className="w-3.5 h-3.5" /></button>
            </div>
          ))}
          {conditions.length === 0 && (
            <div className="text-center py-8 text-[var(--text-tertiary)] text-[13px]">No signal conditions defined. Add one to start generating signals.</div>
          )}
        </div>
      </div>

      {/* Preview */}
      <div className="card">
        <div className="card-header"><h3>Signal Logic Preview</h3></div>
        <div className="card-body">
          <pre className="text-[13px] font-mono text-[var(--text-secondary)] bg-[var(--bg-base)] p-4 rounded-[var(--radius-md)] overflow-x-auto">
            {`IF (\n`}
            {conditions.filter((c) => c.enabled && c.required).map((c) => `  [REQUIRED] ${c.indicator} ${c.operator} ${c.value}`).join("\n  AND\n")}
            {conditions.filter((c) => c.enabled && c.required).length > 0 && conditions.filter((c) => c.enabled && !c.required).length > 0 && `\n  AND (`}
            {conditions.filter((c) => c.enabled && !c.required).map((c) => `    ${c.indicator} ${c.operator} ${c.value}`).join(`\n    ${logic}\n`)}
            {conditions.filter((c) => c.enabled && !c.required).length > 0 && `\n  )`}
            {`\n) → GENERATE SIGNAL`}
          </pre>
        </div>
      </div>
    </div>
  );
}
