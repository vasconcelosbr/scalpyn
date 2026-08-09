"use client";

import Link from "next/link";
import { BrainCircuit, ExternalLink, Loader2, ShieldCheck, X } from "lucide-react";
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

type AnalysisMode = "LOCAL" | "SYSTEMIC" | "ROOT_CAUSE_AUDIT" | "REGENERATIVE";

type Capabilities = {
  runtime_enabled: boolean;
  entrypoints_enabled: boolean;
  module_flags: Record<string, boolean>;
};

type RunResponse = {
  id: string;
  ai_request_id: string;
  status: string;
};

type ModelCatalogResponse = {
  provider: string;
  models: Array<{ id: string; name: string }>;
};

type ModelApprovalResponse = {
  id: string;
  max_cost_usd: string;
  expires_at: string;
  content_hash: string;
};

const PROVIDER_MODELS: Record<string, string> = {
  anthropic: "claude-haiku-4-5-20251001",
  openai: "gpt-4.1-mini",
  gemini: "gemini-2.5-flash",
};

export function ModuleAIAnalysisAction({
  originModule,
  originView,
  entityIds = [],
  supportsRegenerative = false,
  compact = false,
}: {
  originModule: ModuleKey;
  originView: string;
  entityIds?: string[];
  supportsRegenerative?: boolean;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { data: capabilities } = useSWR<Capabilities>(
    "/ai/graphs/capabilities",
    (path: string) => apiGet<Capabilities>(path),
    { revalidateOnFocus: false },
  );
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AnalysisMode>("SYSTEMIC");
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState(PROVIDER_MODELS.anthropic);
  const [maxCostUsd, setMaxCostUsd] = useState("");
  const [inputCostPerMillion, setInputCostPerMillion] = useState("");
  const [outputCostPerMillion, setOutputCostPerMillion] = useState("");
  const [maxInputTokens, setMaxInputTokens] = useState("");
  const [maxOutputTokens, setMaxOutputTokens] = useState("");
  const [requestTokenLimit, setRequestTokenLimit] = useState("");
  const [dailyTokenLimit, setDailyTokenLimit] = useState("");
  const [monthlyTokenLimit, setMonthlyTokenLimit] = useState("");
  const [pricingSourceUrl, setPricingSourceUrl] = useState("");
  const [approvalPhrase, setApprovalPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<RunResponse | null>(null);
  const { data: modelCatalog } = useSWR<ModelCatalogResponse>(
    open ? `/ai-keys/${provider}/models` : null,
    (path: string) => apiGet<ModelCatalogResponse>(path),
    { revalidateOnFocus: false },
  );

  const enabled = Boolean(
    capabilities?.runtime_enabled
    && capabilities.entrypoints_enabled
    && capabilities.module_flags?.[originModule]
  );

  function selectProvider(value: string) {
    setProvider(value);
    setModel(PROVIDER_MODELS[value] ?? "");
    setApprovalPhrase("");
  }

  async function submit() {
    const approvedCost = Number(maxCostUsd);
    const inputRate = Number(inputCostPerMillion);
    const outputRate = Number(outputCostPerMillion);
    const maxInput = Number(maxInputTokens);
    const maxOutput = Number(maxOutputTokens);
    const requestLimit = Number(requestTokenLimit);
    const dailyLimit = Number(dailyTokenLimit);
    const monthlyLimit = Number(monthlyTokenLimit);
    if (
      approvalPhrase.trim() !== "APROVO MODELO E CUSTO"
      || !question.trim()
      || !model.trim()
      || !Number.isFinite(approvedCost)
      || approvedCost <= 0
      || !Number.isFinite(inputRate)
      || inputRate < 0
      || !Number.isFinite(outputRate)
      || outputRate < 0
      || !Number.isInteger(maxInput)
      || maxInput <= 0
      || !Number.isInteger(maxOutput)
      || maxOutput <= 0
      || !Number.isInteger(requestLimit)
      || requestLimit < maxInput + maxOutput
      || !Number.isInteger(dailyLimit)
      || dailyLimit < requestLimit
      || !Number.isInteger(monthlyLimit)
      || monthlyLimit < dailyLimit
      || !pricingSourceUrl.startsWith("https://")
    ) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await apiPost<ModelApprovalResponse>("/ai/modules/model-approvals", {
        provider,
        model: model.trim(),
        max_cost_usd: maxCostUsd,
        input_cost_per_million: inputCostPerMillion,
        output_cost_per_million: outputCostPerMillion,
        pricing_source_url: pricingSourceUrl,
        pricing_observed_at: new Date().toISOString(),
        approval_phrase: approvalPhrase.trim(),
        scope: "SYSTEMIC_MODULE_ANALYSIS",
        module: originModule,
        max_input_tokens: maxInput,
        max_output_tokens: maxOutput,
        request_token_limit: requestLimit,
        daily_token_limit: dailyLimit,
        monthly_token_limit: monthlyLimit,
      });
      const created = await apiPost<RunResponse>("/ai/modules/analysis-runs", {
        origin_module: originModule,
        origin_view: originView,
        entity_ids: entityIds,
        filters: {},
        analysis_mode: mode,
        question: question.trim(),
        authority: mode === "REGENERATIVE" ? "SHADOW_ONLY" : "ANALYSIS_ONLY",
        provider,
        model: model.trim(),
        model_approval_id: approval.id,
        idempotency_key: `module-analysis-${crypto.randomUUID()}`,
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
          <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-cyan-400/20 bg-[#0A0E16] shadow-2xl shadow-cyan-950/40">
            <div className="flex items-start justify-between border-b border-white/10 px-5 py-4">
              <div>
                <div className="flex items-center gap-2 text-cyan-300"><BrainCircuit size={17} /><span className="text-sm font-semibold">Análise sistêmica</span></div>
                <p className="mt-1 text-xs text-slate-400">{originModule} · sem autoridade de escrita live</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-200" aria-label="Fechar"><X size={17} /></button>
            </div>

            <div className="space-y-4 p-5">
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
                  <label className="block text-xs text-slate-300">
                    Objetivo da análise
                    <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} placeholder="Descreva a pergunta. A análise usará somente evidências persistidas e declarará ausências." className="mt-2 w-full resize-none rounded-xl border border-white/10 bg-black/20 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-cyan-400/50" />
                  </label>

                  <div className="grid gap-3 sm:grid-cols-3">
                    <label className="text-xs text-slate-300">Modo
                      <select value={mode} onChange={(event) => setMode(event.target.value as AnalysisMode)} className="mt-2 w-full rounded-lg border border-white/10 bg-[#0E1521] px-3 py-2 text-sm">
                        <option value="LOCAL">Local</option><option value="SYSTEMIC">Sistêmico</option><option value="ROOT_CAUSE_AUDIT">Causa raiz</option>
                        {supportsRegenerative && <option value="REGENERATIVE">Regenerativo Shadow</option>}
                      </select>
                    </label>
                    <label className="text-xs text-slate-300">Provider
                      <select value={provider} onChange={(event) => selectProvider(event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-[#0E1521] px-3 py-2 text-sm">
                        <option value="anthropic">Anthropic</option><option value="openai">OpenAI</option><option value="gemini">Gemini</option>
                      </select>
                    </label>
                    <label className="text-xs text-slate-300">Modelo do catálogo
                      <select value={model} onChange={(event) => { setModel(event.target.value); setApprovalPhrase(""); }} className="mt-2 w-full rounded-lg border border-white/10 bg-[#0E1521] px-3 py-2 text-sm">
                        {(modelCatalog?.models ?? [{ id: model, name: model }]).map((entry) => (
                          <option key={entry.id} value={entry.id}>{entry.name}</option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="grid gap-3 rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 sm:grid-cols-2">
                    <label className="text-xs leading-5 text-amber-100/80">Custo máximo aprovado (USD)
                      <input inputMode="decimal" value={maxCostUsd} onChange={(event) => { setMaxCostUsd(event.target.value); setApprovalPhrase(""); }} placeholder="0.01" className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">USD / milhão de tokens de entrada
                      <input inputMode="decimal" value={inputCostPerMillion} onChange={(event) => { setInputCostPerMillion(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">USD / milhão de tokens de saída
                      <input inputMode="decimal" value={outputCostPerMillion} onChange={(event) => { setOutputCostPerMillion(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Máximo de tokens de entrada
                      <input inputMode="numeric" value={maxInputTokens} onChange={(event) => { setMaxInputTokens(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Máximo de tokens de saída
                      <input inputMode="numeric" value={maxOutputTokens} onChange={(event) => { setMaxOutputTokens(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Limite desta solicitação
                      <input inputMode="numeric" value={requestTokenLimit} onChange={(event) => { setRequestTokenLimit(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Limite diário de tokens
                      <input inputMode="numeric" value={dailyTokenLimit} onChange={(event) => { setDailyTokenLimit(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Limite mensal de tokens
                      <input inputMode="numeric" value={monthlyTokenLimit} onChange={(event) => { setMonthlyTokenLimit(event.target.value); setApprovalPhrase(""); }} className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Fonte oficial do preço
                      <input type="url" value={pricingSourceUrl} onChange={(event) => { setPricingSourceUrl(event.target.value); setApprovalPhrase(""); }} placeholder="https://..." className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <label className="text-xs leading-5 text-amber-100/80">Confirmação humana
                      <input value={approvalPhrase} onChange={(event) => setApprovalPhrase(event.target.value)} placeholder="APROVO MODELO E CUSTO" className="mt-1.5 w-full rounded-lg border border-amber-300/20 bg-black/20 px-3 py-2 text-sm text-amber-50" />
                    </label>
                    <p className="text-[11px] leading-4 text-amber-100/60 sm:col-span-2">A aprovação é persistida, expira e vale somente para esta análise. A autoridade permanece analysis-only ou shadow-only.</p>
                  </div>
                  {error && <p className="rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-200">{error}</p>}
                </>
              )}
            </div>

            {!run && (
              <div className="flex items-center justify-end gap-2 border-t border-white/10 px-5 py-4">
                <button type="button" onClick={() => setOpen(false)} className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:text-slate-100">Cancelar</button>
                <button type="button" onClick={() => void submit()} disabled={busy || approvalPhrase.trim() !== "APROVO MODELO E CUSTO" || !maxCostUsd || !inputCostPerMillion || !outputCostPerMillion || !maxInputTokens || !maxOutputTokens || !requestTokenLimit || !dailyTokenLimit || !monthlyTokenLimit || !pricingSourceUrl || !question.trim() || !model.trim()} className="inline-flex items-center gap-2 rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-cyan-950 disabled:cursor-not-allowed disabled:opacity-40">
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
