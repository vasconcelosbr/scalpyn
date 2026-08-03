"use client";

import { useState } from "react";
import { Activity, Clock3, Radio, Save, ShieldCheck } from "lucide-react";

import { useConfig } from "@/hooks/useConfig";

interface SocialScoreConfig {
  enabled: boolean;
  spot_weight: number;
  futures_weight: number;
  max_age_seconds: number;
  mode: "symmetric";
  formula_version: "confidence_adjusted_v1";
}

const DEFAULT_CONFIG: SocialScoreConfig = {
  enabled: false,
  spot_weight: 0.2,
  futures_weight: 0.2,
  max_age_seconds: 86_400,
  mode: "symmetric",
  formula_version: "confidence_adjusted_v1",
};

function PercentControl({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const pct = Math.round(value * 100);
  return (
    <label className="block rounded-xl border border-[#1E2A36] bg-[#080D12] p-4">
      <span className="flex items-center justify-between text-xs text-[#9BA8B6]">
        {label}
        <strong className="font-mono text-sm text-[#58D6B1]">{pct}%</strong>
      </span>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={pct}
        onChange={(event) => onChange(Number(event.target.value) / 100)}
        className="mt-4 w-full accent-[#32C69A]"
      />
      <div className="mt-2 flex justify-between font-mono text-[9px] text-[#3E4B59]">
        <span>técnico</span>
        <span>social</span>
      </div>
    </label>
  );
}

export default function SocialScoreSettingsPage() {
  const { config, updateConfig, isLoading, error } = useConfig("social_score");
  const [overrides, setOverrides] = useState<Partial<SocialScoreConfig>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const form: SocialScoreConfig = { ...DEFAULT_CONFIG, ...config, ...overrides };

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      await updateConfig(form);
      setNotice("Configuração registrada com auditoria.");
    } catch (saveError) {
      setNotice(saveError instanceof Error ? saveError.message : "Falha ao salvar a configuração.");
    } finally {
      setSaving(false);
    }
  }

  if (isLoading) return <div className="p-8 text-sm text-[#64748B]">Carregando Social Score…</div>;

  return (
    <main className="min-h-screen bg-[#05080C] px-5 py-8 text-[#E7EDF3] md:px-10">
      <div className="mx-auto max-w-5xl">
        <header className="relative overflow-hidden rounded-2xl border border-[#1B2A32] bg-[#081016] p-6 md:p-8">
          <div className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-[#2FD2A0]/10 blur-3xl" />
          <div className="relative flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.24em] text-[#4FD2AB]">
                <Radio size={13} /> Social Intelligence / point-in-time
              </div>
              <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Social Score</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-[#82909E]">
                Modificador pós-gates técnicos. Atenção é observacional; somente sentimento ajustado pela confiança entra no score.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setOverrides((current) => ({ ...current, enabled: !form.enabled }))}
              className={`flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
                form.enabled
                  ? "border-[#35D39F]/40 bg-[#16362D] text-[#70E8C2]"
                  : "border-[#334155] bg-[#0C1219] text-[#94A3B8]"
              }`}
            >
              <span className={`h-2.5 w-2.5 rounded-full ${form.enabled ? "bg-[#42E3B0] shadow-[0_0_18px_#42E3B0]" : "bg-[#475569]"}`} />
              <span>
                <span className="block text-xs font-semibold">{form.enabled ? "LIVE habilitado" : "Shadow / desligado"}</span>
                <span className="block text-[10px] opacity-70">Clique para alternar</span>
              </span>
            </button>
          </div>
        </header>

        <section className="mt-5 grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-2xl border border-[#1B2631] bg-[#090D13] p-5">
            <div className="mb-5 flex items-center gap-2 text-sm font-semibold">
              <Activity size={16} className="text-[#4FD2AB]" /> Influência por mercado
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <PercentControl
                label="Spot"
                value={form.spot_weight}
                onChange={(value) => setOverrides((current) => ({ ...current, spot_weight: value }))}
              />
              <PercentControl
                label="Futures"
                value={form.futures_weight}
                onChange={(value) => setOverrides((current) => ({ ...current, futures_weight: value }))}
              />
            </div>
            <div className="mt-4 rounded-xl border border-[#20303B] bg-[#071117] p-4 font-mono text-[11px] leading-6 text-[#7C929F]">
              <div>sentimento ajustado = 50 + confiança × (sentimento − 50)</div>
              <div>score final = técnico × (1 − peso) + social × peso</div>
              <div>SHORT usa o complemento: 100 − sentimento ajustado</div>
            </div>
          </div>

          <div className="space-y-5">
            <label className="block rounded-2xl border border-[#1B2631] bg-[#090D13] p-5">
              <span className="flex items-center gap-2 text-sm font-semibold">
                <Clock3 size={16} className="text-[#F2B84B]" /> Validade estrita
              </span>
              <div className="mt-5 flex items-end gap-3">
                <input
                  type="number"
                  min={1}
                  max={168}
                  value={Math.round(form.max_age_seconds / 3600)}
                  onChange={(event) => setOverrides((current) => ({
                    ...current,
                    max_age_seconds: Math.max(1, Number(event.target.value)) * 3600,
                  }))}
                  className="w-28 rounded-lg border border-[#263542] bg-[#05090D] px-3 py-2 font-mono text-lg text-[#F2C66D] outline-none focus:border-[#D9A441]"
                />
                <span className="pb-2 text-xs text-[#64748B]">horas</span>
              </div>
              <p className="mt-3 text-xs leading-5 text-[#5E6B78]">Ao expirar, o sistema preserva exatamente o score técnico e registra o fallback.</p>
            </label>

            <div className="rounded-2xl border border-[#1B2631] bg-[#090D13] p-5">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <ShieldCheck size={16} className="text-[#60A5FA]" /> Guardas ativas
              </div>
              <ul className="mt-4 space-y-2 text-xs leading-5 text-[#71808E]">
                <li>• Não resgata rejeição técnica.</li>
                <li>• Recomendação textual não executa regra.</li>
                <li>• Ausente, futuro ou vencido = técnico puro.</li>
                <li>• ML atual não recebe estas features.</li>
              </ul>
            </div>
          </div>
        </section>

        <footer className="mt-5 flex items-center justify-between rounded-xl border border-[#1B2631] bg-[#080C11] px-5 py-4">
          <span className={`text-xs ${error ? "text-red-400" : "text-[#64748B]"}`}>
            {notice ?? (error ? "Não foi possível carregar a configuração." : "Alterações são auditadas por usuário.")}
          </span>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="flex items-center gap-2 rounded-lg bg-[#2EC99A] px-4 py-2 text-sm font-semibold text-[#03120D] transition-colors hover:bg-[#4BDEB2] disabled:opacity-50"
          >
            <Save size={14} /> {saving ? "Salvando…" : "Salvar"}
          </button>
        </footer>
      </div>
    </main>
  );
}
