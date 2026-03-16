"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

export function RiskConfigForm() {
  const { config, updateConfig, isLoading } = useConfig("risk");
  const [local, setLocal] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (config && Object.keys(config).length > 0) setLocal(config);
  }, [config]);

  const set = (key: string, value: any) => setLocal((p) => ({ ...p, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig(local); } catch (e) { console.error(e); }
    setSaving(false);
  };

  if (isLoading) return <div className="card"><div className="card-body"><div className="skeleton h-64 w-full" /></div></div>;

  const tp = local.take_profit_pct ?? 1.5;
  const sl = local.stop_loss_atr_multiplier ?? 1.5;
  const maxPos = local.max_positions ?? 5;
  const dailyLoss = local.daily_loss_limit_pct ?? 3;
  const capPer = local.capital_per_trade_pct ?? 10;
  const maxCap = local.max_capital_in_use_pct ?? 80;
  const maxExp = local.max_exposure_per_asset_pct ?? 20;
  const cbLosses = local.circuit_breaker_consecutive_losses ?? 3;

  const sliders = [
    { label: "Default Take Profit", key: "take_profit_pct", value: tp, min: 0.1, max: 10, step: 0.1, suffix: "%" },
    { label: "Dynamic Stop Loss (ATR)", key: "stop_loss_atr_multiplier", value: sl, min: 0.5, max: 5, step: 0.1, suffix: "x" },
    { label: "Max Concurrent Positions", key: "max_positions", value: maxPos, min: 1, max: 20, step: 1, suffix: "POS" },
    { label: "Capital Per Trade", key: "capital_per_trade_pct", value: capPer, min: 1, max: 50, step: 1, suffix: "%" },
    { label: "Max Capital In Use", key: "max_capital_in_use_pct", value: maxCap, min: 10, max: 100, step: 5, suffix: "%" },
    { label: "Max Exposure Per Asset", key: "max_exposure_per_asset_pct", value: maxExp, min: 5, max: 50, step: 1, suffix: "%" },
  ];

  return (
    <div className="card">
      <div className="card-header pb-4 border-b border-[var(--border-subtle)]">
        <div>
          <h2 className="text-[18px] font-bold tracking-tight">Risk Management Parameters</h2>
          <p className="text-[13px] text-[var(--text-secondary)] mt-1">ZERO HARDCODE — saved to database, controls execution engine in real-time.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save Configuration"}
        </button>
      </div>

      <div className="card-body p-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-8">
          <div className="md:col-span-3 space-y-6">
            {/* Circuit Breaker */}
            <div className="flex items-center justify-between p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
              <div>
                <h4 className="font-semibold text-[14px] text-[var(--text-primary)]">Circuit Breaker</h4>
                <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">Halt after {cbLosses} consecutive losses</p>
              </div>
              <input type="number" className="input numeric w-16 h-8 text-[13px]" value={cbLosses} min={1} max={20} onChange={(e) => set("circuit_breaker_consecutive_losses", parseInt(e.target.value) || 3)} />
            </div>

            {/* Sliders */}
            {sliders.map((s) => (
              <div key={s.key} className="space-y-2 pt-3 border-t border-[var(--border-subtle)]">
                <div className="flex justify-between items-center">
                  <label className={`text-[13px] font-semibold ${s.key === "daily_loss_limit_pct" ? "text-[var(--color-loss)]" : "text-[var(--text-primary)]"}`}>{s.label}</label>
                  <div className="input-group w-[100px]">
                    <input type="number" value={s.value} onChange={(e) => set(s.key, parseFloat(e.target.value) || 0)} className="input numeric" step={s.step} />
                    <span className="suffix">{s.suffix}</span>
                  </div>
                </div>
                <input type="range" min={s.min} max={s.max} step={s.step} value={s.value} onChange={(e) => set(s.key, parseFloat(e.target.value))} className="slider w-full" style={{ "--progress": `${((s.value - s.min) / (s.max - s.min)) * 100}%` } as any} />
              </div>
            ))}

            {/* Daily Loss */}
            <div className="space-y-2 pt-3 border-t border-[var(--border-subtle)]">
              <div className="flex justify-between items-center">
                <label className="text-[13px] font-semibold text-[var(--color-loss)]">Daily Loss Limit</label>
                <div className="input-group w-[100px]">
                  <input type="number" value={dailyLoss} onChange={(e) => set("daily_loss_limit_pct", parseFloat(e.target.value) || 0)} className="input numeric text-[var(--color-loss)]" step={0.5} />
                  <span className="suffix">%</span>
                </div>
              </div>
              <input type="range" min={0.5} max={15} step={0.5} value={dailyLoss} onChange={(e) => set("daily_loss_limit_pct", parseFloat(e.target.value))} className="slider w-full" style={{ "--progress": `${(dailyLoss / 15) * 100}%` } as any} />
            </div>

            {/* Trailing Stop */}
            <div className="flex items-center justify-between p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
              <div>
                <h4 className="font-semibold text-[14px] text-[var(--text-primary)]">Trailing Stop</h4>
                <p className="text-[12px] text-[var(--text-secondary)]">Distance: {local.trailing_stop_distance_pct ?? 0.5}%</p>
              </div>
              <div className={`toggle ${local.trailing_stop_enabled ? "active" : ""}`} onClick={() => set("trailing_stop_enabled", !local.trailing_stop_enabled)}>
                <div className="knob" />
              </div>
            </div>

            {/* Order Type */}
            <div className="flex items-center justify-between pt-3 border-t border-[var(--border-subtle)]">
              <label className="text-[13px] font-semibold text-[var(--text-primary)]">Default Order Type</label>
              <select className="input w-32 h-9 text-[13px]" value={local.default_order_type ?? "limit"} onChange={(e) => set("default_order_type", e.target.value)}>
                <option value="limit">Limit</option>
                <option value="market">Market</option>
              </select>
            </div>
          </div>

          {/* Preview */}
          <div className="md:col-span-2">
            <div className="bg-[var(--bg-elevated)] border border-[var(--border-strong)] rounded-[var(--radius-lg)] p-5 sticky top-24">
              <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-4 pb-3 border-b border-[var(--border-subtle)]">Risk Exposure Preview</h3>
              <div className="space-y-4">
                {[
                  { l: "Capital (assumed)", v: "$100,000" },
                  { l: "Per Trade", v: `$${(100000 * capPer / 100).toLocaleString()}`, s: `${capPer}%` },
                  { l: "Max Deployed", v: `$${(100000 * maxCap / 100).toLocaleString()}`, s: `${maxPos} pos` },
                  { l: "Max Per Asset", v: `$${(100000 * maxExp / 100).toLocaleString()}`, s: `${maxExp}%` },
                ].map((r, i) => (
                  <div key={i} className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                    <div><span className="text-[12px] text-[var(--text-secondary)]">{r.l}</span>{r.s && <span className="caption block">{r.s}</span>}</div>
                    <span className="data-value text-[15px]">{r.v}</span>
                  </div>
                ))}
                <div className="flex justify-between items-end pt-2">
                  <div className="border-l-2 border-[var(--color-loss)] pl-3">
                    <span className="text-[12px] font-bold text-[var(--color-loss)]">Circuit Breaker</span>
                    <span className="caption block">HALT TRADING AT</span>
                  </div>
                  <span className="data-value text-[18px] font-bold text-[var(--color-loss)]">-${(100000 * dailyLoss / 100).toLocaleString()}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
