"use client";

import Link from "next/link";
import { BrainCircuit, CheckCircle2, ExternalLink, Loader2, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";

import { apiGet, apiPost } from "@/lib/api";

type ModuleKey =
  | "strategy_profiles"
  | "ml_models"
  | "shadow_portfolio"
  | "score_engine"
  | "global_risk"
  | "strategies"
  | "social_score";

type Capabilities = {
  runtime_enabled: boolean;
  entrypoints_enabled: boolean;
  module_flags: Record<string, boolean>;
};

type AnalysisProfile = {
  id: string;
  slug: string;
  name: string;
  description: string;
  provider: string;
  model: string;
  analysis_mode: "LOCAL" | "SYSTEMIC" | "ROOT_CAUSE_AUDIT";
  authority: "ANALYSIS_ONLY";
  max_cost_usd: string;
  pricing_valid_until: string;
  profile_version: number;
  profile_hash: string;
};

type AnalysisProfilesResponse = {
  profiles: AnalysisProfile[];
};

type RunResponse = {
  id: string;
  ai_request_id: string;
  status: string;
};

const MODE_LABELS: Record<AnalysisProfile["analysis_mode"], string> = {
  LOCAL: "Local",
  SYSTEMIC: "Sistêmica",
  ROOT_CAUSE_AUDIT: "Causa raiz",
};

function formatUsd(value: string) {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export function ModuleAIAnalysisAction({
  originModule,
  originView,
  entityIds = [],
  compact = false,
}: {
  originModule: ModuleKey;
  originView: string;
  entityIds?: string[];
  supportsRegenerative?: boolean;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<RunResponse | null>(null);
  const { data: capabilities } = useSWR<Capabilities>(
    "/ai/graphs/capabilities",
    (path: string) => apiGet<Capabilities>(path),
    { revalidateOnFocus: false },
  );
  const {
    data: profileData,
    error: profileError,
    isLoading: profilesLoading,
  } = useSWR<AnalysisProfilesResponse>(
    open ? "/ai/modules/analysis-profiles" : null,
    (path: string) => apiGet<AnalysisProfilesResponse>(path),
    { revalidateOnFocus: false },
  );

  const enabled = Boolean(
    capabilities?.runtime_enabled
    && capabilities.entrypoints_enabled
    && capabilities.module_flags?.[originModule]
  );
  const profiles = profileData?.profiles ?? [];
  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId) ?? profiles[0] ?? null;

  async function submit() {
    if (!selectedProfile) return;
    setBusy(true);
    setError(null);
    try {
      const created = await apiPost<RunResponse>("/ai/modules/analysis-runs/from-profile", {
        origin_module: originModule,
        origin_view: originView,
        entity_ids: entityIds,
        filters: {},
        analysis_profile_id: selectedProfile.id,
        idempotency_key: `module-analysis-profile-${crypto.randomUUID()}`,
      });
      setRun(created);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível criar a análise.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={!enabled}
        title={enabled ? "Criar Intelligence Run" : "Módulo de IA desativado por feature flag"}
        className={`inline-flex items-center justify-center gap-2 rounded-lg border border-cyan-400/30 bg-cyan-400/10 font-medium text-cyan-200 transition hover:border-cyan-300/60 hover:bg-cyan-400/15 disabled:cursor-not-allowed disabled:border-slate-700 disabled:bg-slate-800/40 disabled:text-slate-500 ${compact ? "px-2.5 py-1.5 text-xs" : "px-3.5 py-2 text-sm"}`}
      >
        <BrainCircuit size={compact ? 14 : 16} /> Análise por IA
      </button>

      {open && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Criar análise por IA">
          <div className="flex max-h-[calc(100vh-2rem)] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-cyan-400/20 bg-[#0A0E16] shadow-2xl shadow-cyan-950/40">
            <div className="flex items-start justify-between border-b border-white/10 px-5 py-4">
              <div>
                <div className="flex items-center gap-2 text-cyan-300"><BrainCircuit size={17} /><span className="text-sm font-semibold">Análise sistêmica</span></div>
                <p className="mt-1 text-xs text-slate-400">{originModule} · escolha um perfil e crie a análise</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-200" aria-label="Fechar"><X size={17} /></button>
            </div>

            <div className="space-y-4 overflow-y-auto p-5">
              {run ? (
                <div className="rounded-xl border border-emerald-400/25 bg-emerald-400/10 p-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-emerald-200"><ShieldCheck size={16} /> Intelligence Run criada</div>
                  <p className="mt-2 font-mono text-xs text-emerald-100/70">{run.id}</p>
                  <Link href={`/intelligence-runs?run=${run.id}`} className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-300 px-3 py-2 text-xs font-semibold text-emerald-950">
                    Abrir execução <ExternalLink size={13} />
                  </Link>
                </div>
              ) : (
                <>
                  <div>
                    <p className="text-sm font-medium text-slate-100">Escolha o perfil da análise</p>
                    <p className="mt-1 text-xs text-slate-400">O objetivo, o modelo, os limites e a aprovação de custo já estão definidos no perfil.</p>
                  </div>

                  {profilesLoading && (
                    <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-5 text-sm text-slate-400">
                      <Loader2 size={16} className="animate-spin" /> Carregando perfis disponíveis…
                    </div>
                  )}

                  {!profilesLoading && profiles.length > 0 && (
                    <div className="space-y-2" role="radiogroup" aria-label="Perfis de análise">
                      {profiles.map((profile) => {
                        const selected = profile.id === selectedProfile?.id;
                        return (
                          <button
                            key={profile.id}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            onClick={() => setSelectedProfileId(profile.id)}
                            className={`w-full rounded-xl border p-4 text-left transition ${selected ? "border-cyan-300/60 bg-cyan-300/10" : "border-white/10 bg-white/[0.025] hover:border-white/20 hover:bg-white/[0.04]"}`}
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div>
                                <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                                  {selected && <CheckCircle2 size={15} className="text-cyan-300" />}
                                  {profile.name}
                                </div>
                                <p className="mt-1.5 text-xs leading-5 text-slate-400">{profile.description}</p>
                              </div>
                              <span className="shrink-0 rounded-full border border-cyan-400/20 bg-cyan-400/5 px-2 py-1 font-mono text-[10px] text-cyan-200">{MODE_LABELS[profile.analysis_mode]}</span>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-slate-400">
                              <span className="rounded-md bg-black/20 px-2 py-1">{profile.provider} · {profile.model}</span>
                              <span className="rounded-md bg-black/20 px-2 py-1">até {formatUsd(profile.max_cost_usd)} por execução</span>
                              <span className="rounded-md bg-emerald-400/10 px-2 py-1 text-emerald-300">analysis-only</span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {!profilesLoading && profiles.length === 0 && (
                    <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 px-4 py-3 text-xs text-amber-100/80">Nenhum perfil de análise válido está disponível.</p>
                  )}
                  {profileError && <p className="rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-200">Não foi possível carregar os perfis governados.</p>}
                  {selectedProfile && (
                    <p className="rounded-lg border border-emerald-400/15 bg-emerald-400/5 px-3 py-2 text-[11px] leading-4 text-emerald-100/70">
                      Ao clicar em criar, você seleciona este perfil e aprova até {formatUsd(selectedProfile.max_cost_usd)} para esta execução. Nenhuma autoridade de escrita live é concedida.
                    </p>
                  )}
                  {error && <p className="rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-200">{error}</p>}
                </>
              )}
            </div>

            {!run && (
              <div className="flex items-center justify-end gap-2 border-t border-white/10 px-5 py-4">
                <button type="button" onClick={() => setOpen(false)} className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:text-slate-100">Cancelar</button>
                <button type="button" onClick={() => void submit()} disabled={busy || !selectedProfile} className="inline-flex items-center gap-2 rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-cyan-950 disabled:cursor-not-allowed disabled:opacity-40">
                  {busy && <Loader2 size={15} className="animate-spin" />} Criar Intelligence Run
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
