"use client";

import { useState, useEffect } from "react";
import { Save, RefreshCw } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";

export default function RiskSettingsPage() {
  const { config, updateConfig, isLoading } = useConfig("risk");
  const [form, setForm] = useState({
    take_profit_pct: 1.5,
    stop_loss_atr_multiplier: 1.5,
    trailing_stop_enabled: false,
    trailing_stop_distance_pct: 0.5,
    max_positions: 5,
    daily_loss_limit_pct: 3.0,
    max_exposure_per_asset_pct: 20,
    circuit_breaker_consecutive_losses: 3,
    circuit_breaker_pause_minutes: 60,
    default_order_type: "limit",
    max_slippage_pct: 0.1,
    capital_per_trade_pct: 10,
    max_capital_in_use_pct: 80,
  });
  const [saving, setSaving] = useState(false);
  const assumedCapital = 100000;

  useEffect(() => {
    if (config?.data && Object.keys(config.data).length > 0) {
      setForm(prev => ({ ...prev, ...config.data }));
    }
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try { await updateConfig(form); } catch (e) { console.error(e); }
    setSaving(false);
  };

  const update = (key: string, value: any) => setForm(prev => ({ ...prev, [key]: value }));

  if (isLoading) return <div className="p-8"><div className="skeleton h-8 w-64 mb-4" /><div className="skeleton h-96 w-full" /></div>;

  const maxRiskPerTrade = assumedCapital * (form.capital_per_trade_pct / 100) * (form.stop_loss_atr_multiplier * 0.01);
  const circuitBreakerAmount = assumedCapital * (form.daily_loss_limit_pct / 100);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Global Risk Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">ZERO HARDCODE: All parameters dynamically control the execution engine.</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save Configuration"}
        </button>
      </div>

      <div className="card">
        <div className="card-body p-6">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-8">
            <div className="md:col-span-3 space-y-8">
              {/* Circuit Breaker Toggle */}
              <div className="flex items-center justify-between p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
                <div>
                  <h4 className="font-semibold text-[14px] text-[var(--text-primary)]">Trailing Stop</h4>
                  <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">Enable trailing stop for open positions.</p>
                </div>
                <div className={`toggle ${form.trailing_stop_enabled ? "active" : ""}`}
                  onClick={() => update("trailing_stop_enabled", !form.trailing_stop_enabled)}>
                  <div className="knob" />
                </div>
              </div>

              {/* Sliders */}
              {[
                { key: "take_profit_pct", label: "Default Take Profit", suffix: "%", min: 0.1, max: 10, step: 0.1 },
                { key: "stop_loss_atr_multiplier", label: "Dynamic Stop Loss (ATR)", suffix: "x", min: 0.5, max: 5, step: 0.1 },
                { key: "max_positions", label: "Max Concurrent Positions", suffix: "POS", min: 1, max: 20, step: 1 },
                { key: "daily_loss_limit_pct", label: "Daily Loss Limit", suffix: "%", min: 0.5, max: 15, step: 0.5 },
                { key: "capital_per_trade_pct", label: "Capital Per Trade", suffix: "%", min: 1, max: 50, step: 1 },
                { key: "max_capital_in_use_pct", label: "Max Capital In Use", suffix: "%", min: 10, max: 100, step: 5 },
                { key: "max_slippage_pct", label: "Max Slippage", suffix: "%", min: 0.01, max: 1, step: 0.01 },
                { key: "circuit_breaker_consecutive_losses", label: "Circuit Breaker Losses", suffix: "", min: 1, max: 10, step: 1 },
              ].map(({ key, label, suffix, min, max, step }) => (
                <div key={key} className="space-y-3 pt-2 border-t border-[var(--border-subtle)]">
                  <div className="flex justify-between items-center">
                    <label className={`text-[13px] font-semibold ${key === "daily_loss_limit_pct" ? "text-[var(--color-loss)]" : "text-[var(--text-primary)]"}`}>{label}</label>
                    <div className="input-group w-[100px]">
                      <input type="number" value={(form as any)[key]} step={step}
                        onChange={e => update(key, step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value))}
                        className="input numeric" />
                      {suffix && <span className="suffix">{suffix}</span>}
                    </div>
                  </div>
                  <input type="range" min={min} max={max} step={step} value={(form as any)[key]}
                    onChange={e => update(key, step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value))}
                    className="slider w-full"
                    style={{ "--progress": `${(((form as any)[key] - min) / (max - min)) * 100}%` } as any} />
                </div>
              ))}

              {/* Order Type */}
              <div className="space-y-2 pt-2 border-t border-[var(--border-subtle)]">
                <label className="text-[13px] font-semibold text-[var(--text-primary)]">Default Order Type</label>
                <div className="flex gap-2">
                  {["limit", "market"].map(t => (
                    <button key={t} onClick={() => update("default_order_type", t)}
                      className={`px-5 py-2 rounded-[var(--radius-md)] text-[13px] font-semibold transition-all ${form.default_order_type === t ? "bg-[var(--accent-primary)] text-white" : "bg-[var(--bg-hover)] text-[var(--text-secondary)] border border-[var(--border-default)]"}`}>
                      {t.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Preview Panel */}
            <div className="md:col-span-2 space-y-4">
              <div className="bg-[var(--bg-elevated)] border border-[var(--border-strong)] rounded-[var(--radius-lg)] p-5 sticky top-24">
                <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-4 pb-3 border-b border-[var(--border-subtle)]">Risk Exposure Preview</h3>
                <div className="space-y-4">
                  <div className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                    <span className="text-[12px] text-[var(--text-secondary)]">Assumed Capital</span>
                    <span className="data-value text-[16px] text-[var(--text-primary)]">${assumedCapital.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                    <div><span className="text-[12px] text-[var(--text-secondary)]">Capital Per Trade</span></div>
                    <span className="data-value text-[15px]">${(assumedCapital * form.capital_per_trade_pct / 100).toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between items-end border-b border-[var(--border-subtle)] pb-2">
                    <div><span className="text-[12px] text-[var(--text-secondary)]">Max Concurrent</span></div>
                    <span className="data-value text-[15px]">{form.max_positions} positions</span>
                  </div>
                  <div className="flex justify-between items-end pt-2">
                    <div className="border-l-2 border-[var(--color-loss)] pl-3">
                      <span className="text-[12px] font-bold text-[var(--color-loss)]">Circuit Breaker</span>
                      <br /><span className="caption">HALT AT</span>
                    </div>
                    <span className="data-value text-[18px] font-bold text-[var(--color-loss)]">-${circuitBreakerAmount.toLocaleString()}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
