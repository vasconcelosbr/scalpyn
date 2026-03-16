"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw, Plus, Trash2, ShieldOff } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

export default function BlockSettings() {
  const { config, updateConfig, isLoading } = useConfig("block");
  const [blocks, setBlocks] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config?.blocks) setBlocks(config.blocks);
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig({ blocks }); } catch (e) { console.error(e); }
    setSaving(false);
  };

  const addBlock = () => {
    setBlocks([...blocks, { id: `b${Date.now()}`, name: "New Block", enabled: true, indicator: "rsi", type: "threshold", operator: ">", value: 20 }]);
  };

  const removeBlock = (id: string) => setBlocks(blocks.filter((b) => b.id !== id));

  const updateBlock = (id: string, field: string, value: any) => {
    setBlocks(blocks.map((b) => (b.id === id ? { ...b, [field]: value } : b)));
  };

  if (isLoading) return <div className="p-8"><div className="skeleton h-96 w-full" /></div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Block Rules</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">If ANY enabled block triggers, the trade is blocked. These are safety filters.</p>
        </div>
        <div className="flex gap-2">
          <button onClick={addBlock} className="btn btn-secondary"><Plus className="w-4 h-4 mr-1" />Add Block</button>
          <button onClick={handleSave} disabled={saving} className="btn btn-primary">
            {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {blocks.map((block) => (
          <div key={block.id} className={`card ${block.enabled ? "border-l-4 border-l-[var(--color-loss)]" : "opacity-60"}`}>
            <div className="p-5 space-y-3">
              <div className="flex items-center justify-between">
                <input type="text" className="input h-8 text-[14px] font-semibold flex-1 mr-3" value={block.name} onChange={(e) => updateBlock(block.id, "name", e.target.value)} />
                <div className="flex items-center gap-2">
                  <div className={`toggle ${block.enabled ? "active" : ""}`} onClick={() => updateBlock(block.id, "enabled", !block.enabled)}>
                    <div className="knob" />
                  </div>
                  <button onClick={() => removeBlock(block.id)} className="btn-icon w-7 h-7 flex items-center justify-center hover:text-[var(--color-loss)]"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="label">Indicator</label>
                  <select className="input h-8 text-[13px]" value={block.indicator} onChange={(e) => updateBlock(block.id, "indicator", e.target.value)}>
                    {["rsi", "adx", "atr_pct", "spread_pct", "volume_24h", "funding_rate_abs", "ema_trend", "bb_width"].map((i) => <option key={i} value={i}>{i}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="label">Type</label>
                  <select className="input h-8 text-[13px]" value={block.type} onChange={(e) => updateBlock(block.id, "type", e.target.value)}>
                    <option value="threshold">Threshold</option>
                    <option value="range">Range</option>
                    <option value="condition">Condition</option>
                  </select>
                </div>
              </div>

              {block.type === "threshold" && (
                <div className="flex items-center gap-2">
                  <select className="input h-8 text-[13px] w-16" value={block.operator} onChange={(e) => updateBlock(block.id, "operator", e.target.value)}>
                    {[">", "<", ">=", "<="].map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                  <input type="number" className="input numeric h-8 w-24 text-[13px]" value={block.value ?? 0} onChange={(e) => updateBlock(block.id, "value", parseFloat(e.target.value))} />
                </div>
              )}

              {block.type === "range" && (
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-[var(--text-secondary)]">Min</span>
                  <input type="number" className="input numeric h-8 w-20 text-[13px]" value={block.min ?? 0} onChange={(e) => updateBlock(block.id, "min", parseFloat(e.target.value))} />
                  <span className="text-[12px] text-[var(--text-secondary)]">Max</span>
                  <input type="number" className="input numeric h-8 w-20 text-[13px]" value={block.max ?? 100} onChange={(e) => updateBlock(block.id, "max", parseFloat(e.target.value))} />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
