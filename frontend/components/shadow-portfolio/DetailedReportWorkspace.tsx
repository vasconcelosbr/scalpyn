"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FileJson,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Upload,
} from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { ModuleAIAnalysisAction } from "@/components/ai/ModuleAIAnalysisAction";

type Source = "L3" | "L3_REJECTED" | "L1_SPECTRUM";
type Outcome = "TP_HIT" | "SL_HIT";

interface Facet {
  id: string | null;
  name: string;
  count: number;
}

interface FacetsResponse {
  sources: Array<{ id: Source; label: string }>;
  watchlists: Facet[];
  profiles: Facet[];
}

interface ReportRun {
  id: string;
  status: string;
  total_trades: number;
  filters: Record<string, unknown>;
  filters_hash: string;
  trade_ids_hash: string;
  completeness: Record<string, number>;
  created_at: string;
}

interface ReportTrade {
  id: string;
  closed_at: string | null;
  symbol: string;
  source: Source;
  watchlist_name: string | null;
  profile_id: string | null;
  profile_name: string | null;
  outcome: Outcome;
  entry_price: number | null;
  exit_price: number | null;
  pnl_pct: number | null;
  pnl_usdt: number | null;
  mae_pct: number | null;
  mfe_pct: number | null;
  holding_seconds: number | null;
  entry_snapshot_present: boolean;
  exit_snapshot_present: boolean;
}

interface TradePage {
  items: ReportTrade[];
  page: number;
  page_size: number;
  total: number;
}

interface AIProvider {
  provider: "anthropic" | "openai" | "gemini";
  name: string;
  is_validated: boolean;
}

interface AnalysisJob {
  id: string;
  scope: "TRADE" | "REPORT";
  status: "PENDING" | "QUEUED" | "LEASED" | "RUNNING" | "WAITING_HUMAN" | "COMPLETED" | "FAILED" | "FAILED_RETRYABLE" | "FAILED_TERMINAL" | "CANCELLED" | "EXPIRED_RECOVERABLE";
  provider: string;
  model: string;
  configured_provider?: string;
  configured_model?: string;
  effective_provider?: string;
  effective_model?: string;
  model_resolution_reason?: string;
  prompt_key?: string;
  prompt_version?: string;
  authority?: string;
  tenant_context?: string;
  attempt?: number;
  max_attempts?: number;
  terminal_reason?: string | null;
  result?: {
    summary?: string;
    observations?: Array<Record<string, unknown>>;
    recommendations?: Array<{
      profile_id?: string;
      profile_name?: string;
      rationale?: string;
      evidence_trade_ids?: string[];
      changes?: Array<Record<string, unknown>>;
      score_assignment?: Record<string, unknown>;
      score_matrix_patch?: Record<string, unknown>;
    }>;
    [key: string]: unknown;
  } | null;
  error?: string | null;
}

interface OptimizationPlan {
  id: string;
  profile_id: string;
  objective: string;
  status: string;
  changes: Record<string, unknown>;
  risk: string;
  approval_required_text: string;
  preserves_profile_identity: boolean;
  preserves_profile_version: boolean;
  execution_result?: Record<string, unknown> | null;
}

const SOURCE_LABEL: Record<Source, string> = {
  L3: "Aprovados (L3)",
  L3_REJECTED: "Rejeitados (L3)",
  L1_SPECTRUM: "Dataset ML (L1)",
};

const PAGE_SIZE = 50;
// Must mirror backend/app/ai_orchestration/provider_registry.py's default_registry() --
// /api/ai-keys/{provider}/models lists every model the live provider API exposes, which is
// far broader than the models our ProviderModelRegistry actually recognizes (MODEL_UNKNOWN).
const CATALOG_MODELS: Record<AIProvider["provider"], string[]> = {
  anthropic: ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
  openai: ["gpt-4.1-mini"],
  gemini: ["gemini-2.5-flash"],
};
const inputClass =
  "h-10 rounded-lg border border-white/10 bg-[#0d1018] px-3 text-sm text-[#e8ebf4] outline-none transition focus:border-[#4f7bf7]/70";
const buttonClass =
  "inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-white/10 bg-[#151925] px-3 text-xs font-semibold text-[#c7cedd] transition hover:border-white/20 hover:bg-[#1b2030] disabled:cursor-not-allowed disabled:opacity-45";

function dateValue(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function initialDates(days = 7): { from: string; to: string } {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - (days - 1));
  return { from: dateValue(start), to: dateValue(today) };
}

function inclusiveEndUtc(value: string): string {
  const end = new Date(`${value}T00:00:00`);
  end.setDate(end.getDate() + 1);
  return end.toISOString();
}

function money(value: number | null): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function number(value: number | null, suffix = ""): string {
  return value == null ? "—" : `${value.toFixed(2)}${suffix}`;
}

function downloadJson(payload: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function FacetPicker({
  label,
  items,
  selected,
  onChange,
}: {
  label: string;
  items: Facet[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const valid = items.filter((item): item is Facet & { id: string } => Boolean(item.id));
  return (
    <div className="rounded-xl border border-white/[0.07] bg-[#0e1119] p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#7f899f]">{label}</span>
        <button
          className="text-[11px] text-[#7697ff] hover:text-[#9bb1ff]"
          onClick={() => onChange(selected.length === valid.length ? [] : valid.map((item) => item.id))}
        >
          {selected.length === valid.length && valid.length > 0 ? "Nenhum" : "Todos"}
        </button>
      </div>
      <div className="max-h-32 space-y-1 overflow-y-auto pr-1">
        {valid.length === 0 ? (
          <div className="py-3 text-xs text-[#687186]">Nenhum vínculo encontrado.</div>
        ) : (
          valid.map((item) => (
            <label key={item.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-white/[0.04]">
              <input
                type="checkbox"
                className="accent-[#4f7bf7]"
                checked={selected.length === 0 || selected.includes(item.id)}
                onChange={() => {
                  const effective = selected.length === 0 ? valid.map((v) => v.id) : selected;
                  onChange(
                    effective.includes(item.id) ? effective.filter((id) => id !== item.id) : [...effective, item.id],
                  );
                }}
              />
              <span className="min-w-0 flex-1 truncate text-xs text-[#c8cfde]">{item.name}</span>
              <span className="font-mono text-[10px] text-[#657087]">{item.count}</span>
            </label>
          ))
        )}
      </div>
      <div className="mt-2 text-[10px] text-[#657087]">
        {selected.length === 0 || selected.length === valid.length ? "Todos selecionados" : `${selected.length} selecionado(s)`}
      </div>
    </div>
  );
}

export default function DetailedReportWorkspace() {
  const router = useRouter();
  const initial = useMemo(() => initialDates(), []);
  const [sources, setSources] = useState<Source[]>(["L3"]);
  const [outcomes, setOutcomes] = useState<Outcome[]>(["TP_HIT", "SL_HIT"]);
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);
  const [facets, setFacets] = useState<FacetsResponse | null>(null);
  const [watchlistIds, setWatchlistIds] = useState<string[]>([]);
  const [profileIds, setProfileIds] = useState<string[]>([]);
  const [includeLegacy, setIncludeLegacy] = useState(false);
  const [run, setRun] = useState<ReportRun | null>(null);
  const [trades, setTrades] = useState<TradePage | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [provider, setProvider] = useState<AIProvider["provider"] | "">("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisJob | null>(null);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [plan, setPlan] = useState<OptimizationPlan | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [externalJson, setExternalJson] = useState("");
  const [optimizationBusy, setOptimizationBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const facetsQuery = useMemo(() => {
    const params = new URLSearchParams();
    sources.forEach((value) => params.append("sources", value));
    watchlistIds.forEach((value) => params.append("watchlist_ids", value));
    return params.toString();
  }, [sources, watchlistIds]);

  useEffect(() => {
    apiGet<FacetsResponse>(`/api/shadow-trade-reports/facets?${facetsQuery}`)
      .then(setFacets)
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Falha ao carregar filtros"));
  }, [facetsQuery]);

  useEffect(() => {
    apiGet<{ providers: AIProvider[] }>("/api/ai-keys/capabilities")
      .then(({ providers: found }) => {
        const validated = found.filter((item) => item.is_validated);
        setProviders(validated);
        if (validated[0]) setProvider(validated[0].provider);
      })
      .catch(() => setProviders([]));
  }, []);

  useEffect(() => {
    if (!provider) {
      setModels([]);
      setModel("");
      return;
    }
    apiGet<{ models: Array<{ id: string }> }>(`/api/ai-keys/${provider}/models`)
      .then(({ models: found }) => {
        const ids = found.map((item) => item.id);
        const supported = ids.filter((id) => CATALOG_MODELS[provider]?.includes(id));
        setModels(supported.length ? supported : ids);
        setModel(supported[0] ?? ids[0] ?? "");
      })
      .catch(() => {
        setModels([]);
        setModel("");
      });
  }, [provider]);

  const loadTrades = useCallback(async (runId: string, nextPage: number) => {
    const result = await apiGet<TradePage>(
      `/api/shadow-trade-reports/runs/${runId}/trades?page=${nextPage}&page_size=${PAGE_SIZE}`,
    );
    setTrades(result);
  }, []);

  const executeReport = async () => {
    if (!sources.length || !outcomes.length) {
      setError("Selecione ao menos uma origem e um resultado.");
      return;
    }
    setBusy(true);
    setError(null);
    setAnalysis(null);
    setPlan(null);
    try {
      const created = await apiPost<ReportRun>("/api/shadow-trade-reports/runs", {
        sources,
        watchlist_ids: watchlistIds,
        include_legacy_watchlist: includeLegacy,
        profile_ids: profileIds,
        outcomes,
        date_from: new Date(`${dateFrom}T00:00:00`).toISOString(),
        date_to: inclusiveEndUtc(dateTo),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      });
      setRun(created);
      setPage(1);
      await loadTrades(created.id, 1);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao executar relatório");
    } finally {
      setBusy(false);
    }
  };

  const changePage = async (nextPage: number) => {
    if (!run || nextPage < 1) return;
    setBusy(true);
    try {
      await loadTrades(run.id, nextPage);
      setPage(nextPage);
    } finally {
      setBusy(false);
    }
  };

  const fetchDownload = async (endpoint: string, filename: string) => {
    setBusy(true);
    setError(null);
    try {
      downloadJson(await apiGet(endpoint), filename);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha no download JSON");
    } finally {
      setBusy(false);
    }
  };

  const pollJob = useCallback(async (jobId: string) => {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      const current = await apiGet<AnalysisJob>(`/api/shadow-trade-analysis/jobs/${jobId}`);
      setAnalysis(current);
      if (["COMPLETED", "FAILED", "FAILED_TERMINAL", "CANCELLED"].includes(current.status)) return current;
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
    throw new Error("A análise excedeu o tempo de acompanhamento da tela.");
  }, []);

  const analyze = async (scope: "TRADE" | "REPORT", targetId: string) => {
    if (!provider || !model) {
      setError("Configure e valide uma integração de IA e selecione um modelo.");
      return;
    }
    setAnalysisBusy(true);
    setError(null);
    setPlan(null);
    try {
      const job = await apiPost<AnalysisJob>("/api/shadow-trade-analysis/jobs", {
        scope,
        trade_id: scope === "TRADE" ? targetId : null,
        report_run_id: scope === "REPORT" ? targetId : null,
        provider,
        model,
      });
      setAnalysis(job);
      await pollJob(job.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao analisar com IA");
    } finally {
      setAnalysisBusy(false);
    }
  };

  const createPlanFromAnalysis = async (index: number) => {
    if (!analysis) return;
    setOptimizationBusy(true);
    setError(null);
    try {
      setPlan(
        await apiPost<OptimizationPlan>(`/api/profile-optimizations/from-analysis/${analysis.id}`, {
          recommendation_index: index,
        }),
      );
      setConfirmation("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao criar dry-run");
    } finally {
      setOptimizationBusy(false);
    }
  };

  const createExternalPlan = async () => {
    setOptimizationBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(externalJson);
      setPlan(await apiPost<OptimizationPlan>("/api/profile-optimizations/dry-run", parsed));
      setConfirmation("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "JSON externo inválido");
    } finally {
      setOptimizationBusy(false);
    }
  };

  const approveAndExecute = async () => {
    if (!plan) return;
    setOptimizationBusy(true);
    setError(null);
    try {
      const approved = await apiPost<OptimizationPlan>(`/api/profile-optimizations/${plan.id}/approve`, {
        confirmation_text: confirmation,
      });
      setPlan(await apiPost<OptimizationPlan>(`/api/profile-optimizations/${approved.id}/execute`));
      setConfirmation("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao aplicar otimização");
    } finally {
      setOptimizationBusy(false);
    }
  };

  const presets = [1, 7, 15, 30, 90];
  const pages = trades ? Math.max(1, Math.ceil(trades.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-2xl border border-white/[0.07] bg-[#11141d] shadow-[0_24px_70px_rgba(0,0,0,0.24)]">
        <div className="border-b border-white/[0.07] bg-[linear-gradient(105deg,rgba(79,123,247,0.13),transparent_45%)] px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-[#eef1f8]"><SlidersHorizontal size={16} className="text-[#6f91ff]" /> Seleção da amostra</div>
              <p className="mt-1 text-xs text-[#798399]">A execução materializa uma seleção imutável. Downloads e análises usam exatamente os mesmos trades.</p>
            </div>
            <div className="flex gap-1.5">
              {presets.map((days) => (
                <button key={days} className={buttonClass} onClick={() => { const value = initialDates(days); setDateFrom(value.from); setDateTo(value.to); }}>
                  {days}d
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="grid gap-4 p-5 xl:grid-cols-[1.1fr_1fr_1fr]">
          <div className="space-y-4">
            <div>
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-[#7f899f]">Watchlist de origem</div>
              <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-1">
                {(Object.keys(SOURCE_LABEL) as Source[]).map((source) => (
                  <label key={source} className="flex cursor-pointer items-center gap-3 rounded-lg border border-white/[0.07] bg-[#0e1119] px-3 py-2.5 hover:border-white/15">
                    <input type="checkbox" className="accent-[#4f7bf7]" checked={sources.includes(source)} onChange={() => { setSources(sources.includes(source) ? sources.filter((item) => item !== source) : [...sources, source]); setWatchlistIds([]); setProfileIds([]); }} />
                    <span className="text-xs font-medium text-[#cbd2e1]">{SOURCE_LABEL[source]}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1.5 text-[11px] uppercase tracking-wide text-[#7f899f]">Início<input className={`${inputClass} block w-full normal-case`} type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
              <label className="space-y-1.5 text-[11px] uppercase tracking-wide text-[#7f899f]">Fim<input className={`${inputClass} block w-full normal-case`} type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
            </div>
            <div>
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-[#7f899f]">Resultado</div>
              <div className="flex gap-2">
                {(["TP_HIT", "SL_HIT"] as Outcome[]).map((outcome) => (
                  <button key={outcome} onClick={() => setOutcomes(outcomes.includes(outcome) ? outcomes.filter((item) => item !== outcome) : [...outcomes, outcome])} className={`${buttonClass} flex-1 ${outcomes.includes(outcome) ? outcome === "TP_HIT" ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300" : "border-rose-500/50 bg-rose-500/10 text-rose-300" : ""}`}>
                    {outcome === "TP_HIT" ? "TP" : "SL"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <FacetPicker label="Watchlists" items={facets?.watchlists ?? []} selected={watchlistIds} onChange={(ids) => { setWatchlistIds(ids); setProfileIds([]); }} />
          <div className="space-y-3">
            <FacetPicker label="Profiles" items={facets?.profiles ?? []} selected={profileIds} onChange={setProfileIds} />
            <label className="flex items-start gap-2 rounded-lg border border-amber-400/15 bg-amber-400/[0.05] p-3 text-xs text-[#9ba4b7]">
              <input type="checkbox" className="mt-0.5 accent-amber-400" checked={includeLegacy} onChange={(event) => setIncludeLegacy(event.target.checked)} />
              Incluir trades legados sem vínculo de watchlist
            </label>
            <button className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#4f7bf7] text-sm font-semibold text-white shadow-[0_8px_30px_rgba(79,123,247,0.25)] transition hover:bg-[#6088f8] disabled:opacity-50" onClick={executeReport} disabled={busy}>
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />} Executar relatório
            </button>
          </div>
        </div>
      </section>

      {error && <div className="flex items-start gap-2 rounded-xl border border-rose-500/25 bg-rose-500/[0.08] px-4 py-3 text-xs text-rose-200"><AlertTriangle size={15} className="mt-0.5 shrink-0" />{error}</div>}

      {run && (
        <section className="rounded-2xl border border-white/[0.07] bg-[#11141d]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-5 py-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-[#eef1f8]"><ShieldCheck size={16} className="text-emerald-400" /> Amostra materializada</div>
              <div className="mt-1 font-mono text-[10px] text-[#697388]">run {run.id} · hash {run.trade_ids_hash.slice(0, 14)}…</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 font-mono text-xs text-[#d2d8e5]">{run.total_trades} trades</span>
              <button className={buttonClass} onClick={() => fetchDownload(`/api/shadow-trade-reports/runs/${run.id}/export`, `shadow-report-${run.id}.json`)}><Download size={14} /> JSON consolidado</button>
              <button className={`${buttonClass} border-[#4f7bf7]/40 text-[#9db2ff]`} onClick={() => analyze("REPORT", run.id)} disabled={analysisBusy}><Sparkles size={14} /> Analisar seleção</button>
              <ModuleAIAnalysisAction
                originModule="shadow_portfolio"
                originView="shadow-portfolio-detailed-report"
                entityIds={[]}
                reportRunId={run.id}
                label={`Análise por IA (${run.total_trades} trades)`}
                compact
              />
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[1120px] text-left">
              <thead className="bg-[#0d1018] text-[10px] uppercase tracking-[0.11em] text-[#697388]">
                <tr>{["Fechado", "Símbolo", "Profile / Watchlist", "Resultado", "Entrada", "Saída", "P&L", "MAE / MFE", "Snapshots", "Ações"].map((label) => <th key={label} className="px-4 py-3 font-semibold">{label}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-white/[0.055]">
                {(trades?.items ?? []).map((trade) => (
                  <tr key={trade.id} className="text-xs text-[#b9c1d1] hover:bg-white/[0.025]">
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-[11px] text-[#8993a8]">{trade.closed_at ? new Date(trade.closed_at).toLocaleString("pt-BR") : "—"}</td>
                    <td className="px-4 py-3 font-semibold text-[#edf0f7]">{trade.symbol}</td>
                    <td className="max-w-[230px] px-4 py-3"><div className="truncate text-[#d2d8e5]">{trade.profile_name ?? "Sem profile"}</div><div className="truncate text-[10px] text-[#687287]">{trade.watchlist_name ?? SOURCE_LABEL[trade.source]}</div></td>
                    <td className="px-4 py-3"><span className={`rounded-md border px-2 py-1 font-semibold ${trade.outcome === "TP_HIT" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-rose-500/30 bg-rose-500/10 text-rose-300"}`}>{trade.outcome === "TP_HIT" ? "TP" : "SL"}</span></td>
                    <td className="px-4 py-3 font-mono">{money(trade.entry_price)}</td>
                    <td className="px-4 py-3 font-mono">{money(trade.exit_price)}</td>
                    <td className={`px-4 py-3 font-mono font-semibold ${(trade.pnl_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}><div>{number(trade.pnl_pct, "%")}</div><div className="text-[10px] opacity-70">{money(trade.pnl_usdt)}</div></td>
                    <td className="px-4 py-3 font-mono text-[10px]"><div className="text-rose-300">{number(trade.mae_pct, "%")}</div><div className="text-emerald-300">{number(trade.mfe_pct, "%")}</div></td>
                    <td className="px-4 py-3"><span className={trade.entry_snapshot_present && trade.exit_snapshot_present ? "text-emerald-300" : "text-amber-300"}>{trade.entry_snapshot_present ? "E✓" : "E—"} · {trade.exit_snapshot_present ? "S✓" : "S—"}</span></td>
                    <td className="px-4 py-3"><div className="flex items-center gap-1.5"><button title="Abrir gráfico e indicadores" className={buttonClass} onClick={() => router.push(`/dashboard/shadow-portfolio/${trade.id}`)}>Abrir</button><button title="Download JSON" className={buttonClass} onClick={() => fetchDownload(`/api/shadow-trade-reports/trades/${trade.id}/export`, `shadow-trade-${trade.symbol}-${trade.id}.json`)}><FileJson size={13} /></button><button title="Analisar trade com IA" className={buttonClass} onClick={() => analyze("TRADE", trade.id)} disabled={analysisBusy}><Bot size={13} /></button></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!trades?.items.length && <div className="px-5 py-12 text-center text-sm text-[#687287]">Nenhum trade corresponde à seleção.</div>}
          </div>
          <div className="flex items-center justify-between border-t border-white/[0.07] px-5 py-3 text-xs text-[#788298]"><span>Página {page} de {pages}</span><div className="flex gap-2"><button className={buttonClass} disabled={page <= 1 || busy} onClick={() => changePage(page - 1)}><ChevronLeft size={14} /> Anterior</button><button className={buttonClass} disabled={page >= pages || busy} onClick={() => changePage(page + 1)}>Próxima <ChevronRight size={14} /></button></div></div>
        </section>
      )}

      <section className="grid gap-5 xl:grid-cols-[0.92fr_1.08fr]">
        <div className="rounded-2xl border border-white/[0.07] bg-[#11141d] p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-[#eef1f8]"><Bot size={16} className="text-[#6f91ff]" /> Análise por IA</div>
          <p className="mt-1 text-xs text-[#798399]">Usa somente integrações validadas em Settings / General / Integrações de IA.</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <select className={inputClass} value={provider} onChange={(event) => setProvider(event.target.value as AIProvider["provider"])}><option value="">Provider</option>{providers.map((item) => <option key={item.provider} value={item.provider}>{item.name}</option>)}</select>
            <select className={inputClass} value={model} onChange={(event) => setModel(event.target.value)}><option value="">Modelo</option>{models.map((item) => <option key={item} value={item}>{item}</option>)}</select>
          </div>
          {!providers.length && <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] p-3 text-xs text-amber-200">Nenhum provider validado. Configure a chave em Integrações de IA.</div>}
          {analysis && <div className="mt-4 rounded-xl border border-white/[0.07] bg-[#0d1018] p-4"><div className="flex items-center justify-between"><span className="text-xs font-semibold text-[#dce2ee]">{analysis.scope === "REPORT" ? "Análise consolidada" : "Análise do trade"}</span><span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${analysis.status === "COMPLETED" ? "bg-emerald-500/10 text-emerald-300" : ["FAILED", "FAILED_TERMINAL"].includes(analysis.status) ? "bg-rose-500/10 text-rose-300" : "bg-blue-500/10 text-blue-300"}`}>{analysis.status}</span></div><div className="mt-3 flex flex-wrap gap-2 font-mono text-[9px] uppercase tracking-[0.08em]"><span className="rounded-full border border-amber-400/25 bg-amber-400/[0.07] px-2 py-1 text-amber-200">{analysis.authority ?? "ANALYSIS_ONLY"}</span><span className="rounded-full border border-white/10 px-2 py-1 text-[#8f9ab0]">{analysis.effective_provider}/{analysis.effective_model}</span><span className="rounded-full border border-white/10 px-2 py-1 text-[#8f9ab0]">{analysis.prompt_key}@{analysis.prompt_version}</span><span className="rounded-full border border-white/10 px-2 py-1 text-[#8f9ab0]">tentativa {analysis.attempt ?? 0}/{analysis.max_attempts ?? 3}</span></div>{analysisBusy && <div className="mt-3 flex items-center gap-2 text-xs text-[#8b95a9]"><Loader2 size={14} className="animate-spin" /> Processando com {analysis.provider}/{analysis.model}</div>}{analysis.error && <p className="mt-3 text-xs text-rose-300">{analysis.error}</p>}{analysis.terminal_reason && <p className="mt-2 text-xs text-rose-300">Terminal: {analysis.terminal_reason}</p>}{analysis.result?.summary && <p className="mt-3 text-sm leading-6 text-[#b7c0d1]">{analysis.result.summary}</p>}</div>}
        </div>

        <div className="rounded-2xl border border-white/[0.07] bg-[#11141d] p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-[#eef1f8]"><Sparkles size={16} className="text-amber-300" /> Recomendações estruturadas</div>
          <div className="mt-4 space-y-3">
            {(analysis?.result?.recommendations ?? []).map((recommendation, index) => (
              <div key={`${recommendation.profile_id}-${index}`} className="rounded-xl border border-white/[0.07] bg-[#0d1018] p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-semibold text-[#e1e6f0]">{recommendation.profile_name ?? recommendation.profile_id ?? "Profile"}</div><p className="mt-1 text-xs leading-5 text-[#8993a8]">{recommendation.rationale ?? "Recomendação estruturada pela IA."}</p></div><button className={buttonClass} disabled={optimizationBusy} onClick={() => createPlanFromAnalysis(index)}><SlidersHorizontal size={13} /> Criar dry-run</button></div><div className="mt-3 font-mono text-[10px] text-[#687287]">{recommendation.changes?.length ?? 0} alterações · {recommendation.evidence_trade_ids?.length ?? 0} trades de evidência</div></div>
            ))}
            {analysis?.status === "COMPLETED" && !(analysis.result?.recommendations?.length) && <div className="py-6 text-center text-xs text-[#687287]">A análise não propôs ajustes aplicáveis.</div>}
            {!analysis && <div className="py-6 text-center text-xs text-[#687287]">Execute uma análise para receber recomendações vinculadas aos profiles.</div>}
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-white/[0.07] bg-[#11141d] p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-sm font-semibold text-[#eef1f8]"><ShieldCheck size={16} className="text-emerald-400" /> Otimizações de Profile <span className="rounded-full border border-cyan-400/25 bg-cyan-400/[0.07] px-2 py-1 font-mono text-[9px] text-cyan-200">CANDIDATE ONLY</span></div><p className="mt-1 max-w-3xl text-xs leading-5 text-[#798399]">Dry-run obrigatório e vínculo estrito com o bundle de configuração. Cada execução cria uma nova versão candidata imutável; o champion e o runtime permanecem inalterados.</p></div><button className={buttonClass} onClick={() => fileRef.current?.click()}><Upload size={14} /> Carregar JSON externo</button><input ref={fileRef} className="hidden" type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; file.text().then(setExternalJson).catch(() => setError("Falha ao ler o arquivo JSON")); }} /></div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div><textarea className="min-h-56 w-full resize-y rounded-xl border border-white/10 bg-[#0b0e15] p-4 font-mono text-[11px] leading-5 text-[#aeb8ca] outline-none focus:border-[#4f7bf7]/60" value={externalJson} onChange={(event) => setExternalJson(event.target.value)} placeholder={'Cole um JSON no contrato "scalpyn.profile_optimization_patch" ou carregue um arquivo.'} /><button className={`${buttonClass} mt-2`} disabled={!externalJson.trim() || optimizationBusy} onClick={createExternalPlan}><RefreshCw size={13} className={optimizationBusy ? "animate-spin" : ""} /> Validar e criar dry-run</button></div>
          <div className="rounded-xl border border-white/[0.07] bg-[#0d1018] p-4">{plan ? <><div className="flex items-center justify-between gap-3"><div><div className="text-xs font-semibold text-[#e4e9f2]">Plano {plan.id.slice(0, 8)}</div><div className="mt-1 font-mono text-[10px] text-[#697388]">profile {plan.profile_id}</div></div><span className="rounded-full border border-[#4f7bf7]/30 bg-[#4f7bf7]/10 px-2.5 py-1 text-[10px] font-semibold text-[#9db2ff]">{plan.status}</span></div><p className="mt-3 text-xs leading-5 text-[#9aa4b7]">{plan.objective}</p><div className="mt-3 rounded-lg border border-cyan-400/20 bg-cyan-400/[0.06] p-3 text-xs text-cyan-100"><CheckCircle2 size={14} className="mr-2 inline" />ID e nome preservados; uma nova versão candidata será criada.</div><details className="mt-3"><summary className="cursor-pointer text-xs font-semibold text-[#9db2ff]">Revisar diff completo</summary><pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-black/25 p-3 text-[10px] leading-5 text-[#98a4b8]">{JSON.stringify(plan.changes, null, 2)}</pre></details>{plan.status === "DRY_RUN" && <div className="mt-4"><label className="text-[10px] uppercase tracking-[0.12em] text-[#778197]">Digite exatamente {plan.approval_required_text}</label><div className="mt-2 flex gap-2"><input className={`${inputClass} min-w-0 flex-1 font-mono`} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button className="inline-flex h-10 items-center gap-2 rounded-lg bg-emerald-500 px-4 text-xs font-bold text-[#07110d] disabled:opacity-40" disabled={confirmation !== plan.approval_required_text || optimizationBusy} onClick={approveAndExecute}><ShieldCheck size={14} /> Criar candidata</button></div></div>}{plan.status === "EXECUTED" && <div className="mt-4 rounded-lg border border-cyan-400/25 bg-cyan-400/[0.08] p-3 text-xs text-cyan-100">Versão candidata criada. Champion, flags live e runtime não foram alterados.</div>}</> : <div className="flex min-h-56 items-center justify-center text-center text-xs leading-5 text-[#687287]">Importe uma recomendação externa ou gere um dry-run a partir da análise por IA.<br />Nenhuma alteração ocorre antes da revisão e confirmação.</div>}</div>
        </div>
      </section>
    </div>
  );
}
