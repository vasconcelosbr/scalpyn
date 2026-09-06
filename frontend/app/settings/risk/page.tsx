"use client";

import { useState, useEffect, useReducer } from "react";
import { Save, RefreshCw } from "lucide-react";
import { useConfig } from "@/hooks/useConfig";
import { ShadowL3ExitPolicyForm } from "@/components/settings/ShadowL3ExitPolicyForm";
import { ModuleAIAnalysisAction } from "@/components/ai/ModuleAIAnalysisAction";
import { apiGet } from "@/lib/api";
import { emptyRiskDraft, riskDraftReducer, sameRiskValues, type RiskValues } from "@/lib/riskFormState";

export default function RiskSettingsPage() {
  const { config, updateConfig, isLoading, error: loadError, mutate } = useConfig("risk");
  const [draft, dispatch] = useReducer(riskDraftReducer, emptyRiskDraft);
  const form = draft.values as Record<string, any>;
  const [message, setMessage] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const assumedCapital = 100000;

  useEffect(() => {
    if (config && Object.keys(config).length > 0) {
      dispatch({ type: 'load', values: config });
    }
  }, [config]);

  const handleSave = async () => {
    const submitted = { ...draft.values };
    if (Object.values(submitted).some(v => typeof v === 'number' && !Number.isFinite(v))) {
      setSaveError('Preencha valores numéricos válidos antes de salvar.'); return;
    }
    setSaving(true);
    setSaveError(""); setMessage("");
    try {
      await updateConfig(submitted);
      const response = await apiGet<{ data: RiskValues }>("/config/risk");
      if (!sameRiskValues(submitted, response.data)) throw new Error('A leitura após salvar divergiu. Suas alterações continuam disponíveis; confira antes de tentar novamente.');
      dispatch({ type: 'saved', submitted, persisted: response.data });
      setMessage('Configuração salva e conferida.');
    } catch (e) { setSaveError(e instanceof Error ? e.message : 'Não foi possível confirmar o salvamento.'); }
    finally { setSaving(false); }
  };

  const update = (key: string, value: any) => { setMessage(''); dispatch({ type: 'edit', key, value }); };

  if (isLoading) return <div className="p-8"><div className="skeleton h-8 w-64 mb-4" /><div className="skeleton h-96 w-full" /></div>;
  if (!draft.loaded) return <div className="p-8" role="alert">{loadError ? 'Falha ao carregar a configuração salva.' : 'Configuração salva indisponível.'}<button className="btn ml-4" onClick={() => mutate()}>Tentar novamente</button></div>;

  const maxRiskPerTrade = assumedCapital * (form.capital_per_trade_pct / 100) * (form.stop_loss_atr_multiplier * 0.01);
  const circuitBreakerAmount = assumedCapital * (form.daily_loss_limit_pct / 100);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Global Risk Configuration</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">ZERO HARDCODE: All parameters dynamically control the execution engine.</p>
        </div>
        <div className="flex items-center gap-2">
        <ModuleAIAnalysisAction originModule="global_risk" originView="settings-risk" compact />
        <button onClick={handleSave} disabled={saving || !draft.dirty || !!loadError} className="btn btn-primary">
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? "Saving..." : "Save Configuration"}
        </button>
        </div>
      </div>

      {draft.dirty && <p role="status" className="text-amber-300 text-sm">Alterações pendentes — clique em Save Configuration para salvar.</p>}
      {message && <p role="status" className="text-emerald-300 text-sm">{message}</p>}
      {(saveError || loadError) && <p role="alert" className="text-red-300 text-sm">{saveError || 'Falha ao atualizar a configuração. Suas edições foram preservadas.'}</p>}
      <div className="card" inert={saving}>
        <div className="card-body p-6">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-8">
            <div className="md:col-span-3 space-y-8">
              {/* Circuit Breaker Toggle */}
              <div className="flex items-center justify-between p-4 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)]">
                <div>
                    <h4 className="font-semibold text-[14px] text-[var(--text-primary)]">Trailing Stop — Global Risk</h4>
                    <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">Configuração global independente. Para acompanhar os shadows L3, use o bloco próprio abaixo.</p>
                </div>
                <button type="button" role="switch" aria-label="Trailing Stop — Global Risk" aria-checked={!!form.trailing_stop_enabled} className={`toggle ${form.trailing_stop_enabled ? "active" : ""}`}
                  onClick={() => update("trailing_stop_enabled", !form.trailing_stop_enabled)}>
                  <div className="knob" />
                </button>
              </div>

              {/* Sliders */}
              {[
                { key: "take_profit_pct", label: "Default Take Profit", suffix: "%", min: 0.1, max: 10, step: 0.1 },
                { key: "trailing_stop_distance_pct", label: "Distância do trailing global", suffix: "%", min: 0.1, max: 100, step: 0.1 },
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
      <ShadowL3ExitPolicyForm />
    </div>
  );
}
