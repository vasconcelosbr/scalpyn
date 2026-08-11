"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertTriangle, Boxes, Check, ChevronRight, CircleDot, Clock3,
  FileSearch, GitBranch, Lightbulb, Pause, RefreshCw, ShieldCheck, Square, X,
} from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import { AnalysisChatPanel } from "@/components/ai/AnalysisChatPanel";

type GraphRun = {
  id: string;
  ai_request_id: string;
  graph_definition_id: string;
  status: string;
  current_node: string | null;
  last_completed_node: string | null;
  failed_node: string | null;
  authority: string;
  state_schema_version: string;
  started_at: string | null;
  completed_at: string | null;
  terminal_reason: string | null;
  last_error_code: string | null;
  last_error_safe_message: string | null;
  error_kind: string | null;
  provider_transport_attempted: boolean | null;
  created_at: string;
  updated_at: string;
  origin_module: string | null;
  origin_view: string | null;
  request_intent: string | null;
  correlation_id: string | null;
  graph_key: string | null;
  graph_version: string | null;
};

type GraphEvent = {
  id: number;
  event_type: string;
  node_name: string | null;
  status: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

type GraphInterrupt = {
  id: string;
  interrupt_type: string;
  status: string;
  payload: Record<string, unknown>;
  allowed_edit_fields: string[];
  decision: string | null;
  created_at: string;
  resolved_at: string | null;
};

type Capabilities = {
  runtime: string;
  runtime_enabled: boolean;
  entrypoints_enabled: boolean;
  regenerative_shadow_enabled: boolean;
  real_provider_canary_enabled: boolean;
  normal_analysis_provider_enabled: boolean;
  strict_msgpack: boolean;
  live_write: boolean;
  module_flags: Record<string, boolean>;
};

type AnalysisEvidence = {
  tool?: string;
  finding?: string;
  evidence_id?: string;
  [key: string]: unknown;
};

type RunAnalysis = {
  diagnosis?: string;
  root_cause_classification?: string;
  affected_modules?: string[];
  evidence?: AnalysisEvidence[];
  discarded_hypotheses?: Array<Record<string, unknown>>;
  data_quality?: Record<string, unknown>;
  market_regime?: Record<string, unknown>;
  memory_hits?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

type AnalysisRecommendation = {
  target_module?: string;
  target_path?: string;
  operation?: string;
  current_value?: unknown;
  proposed_value?: unknown;
  confidence?: number;
  [key: string]: unknown;
};

type RunContext = {
  model: { configured_provider: string | null; configured_model: string | null; effective_provider: string | null; effective_model: string | null; resolution_reason: string | null };
  prompt: { key: string | null; version: string | null; hash: string | null };
  dataset: { id: string | null; hash: string | null; contract_version: string | null; quality_status: string | null; row_count: number | null; module_context_refs: Record<string, unknown> | null; context_manifest: { modules_consulted?: string[]; tools_called?: string[]; evidence_ids?: string[] } | null };
  bundle: { id: string | null; hash: string | null; lineage_status: string | null; lineage_refs: Record<string, unknown> | null };
  result: {
    status: string | null;
    analysis: RunAnalysis | null;
    recommendations: AnalysisRecommendation[];
    warnings: string[];
    limitations: string[];
    memory_hits: Array<Record<string, unknown>>;
  };
  usage: { tokens_input: number | null; tokens_output: number | null; actual_cost: string | null; currency: string | null; pricing_snapshot_version: string | null };
  budget_reservation: { id: string | null; status: string | null; provider: string | null; model: string | null; reserved_tokens: number | null; actual_tokens: number | null; released_tokens: number | null; reserved_cost_usd: string | null; actual_cost_usd: string | null; provider_transport_attempted: boolean | null; terminal_reason: string | null };
};

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

function when(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short", timeStyle: "medium",
  }).format(new Date(value));
}

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

function statusTone(status: string) {
  if (status === "COMPLETED") return "border-emerald-400/30 bg-emerald-400/10 text-emerald-300";
  if (status === "FAILED" || status === "CANCELLED") return "border-rose-400/30 bg-rose-400/10 text-rose-300";
  if (status === "INTERRUPTED" || status === "WAITING_SHADOW") return "border-amber-400/30 bg-amber-400/10 text-amber-300";
  return "border-cyan-400/30 bg-cyan-400/10 text-cyan-300";
}

function runStatusLabel(run: GraphRun) {
  return run.error_kind === "PROVIDER_BLOCKED" ? "Provider bloqueado" : run.status;
}

function runTitle(run: GraphRun) {
  if (run.error_kind === "PROVIDER_BLOCKED") return "Provider bloqueado";
  return run.failed_node ?? run.current_node ?? "queued";
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function AnalysisResultPanel({ context }: { context: RunContext }) {
  const { result } = context;
  const analysis = result.analysis;
  const evidence = Array.isArray(analysis?.evidence) ? analysis.evidence : [];
  const affectedModules = Array.isArray(analysis?.affected_modules) ? analysis.affected_modules : [];

  return (
    <section className="mb-4 overflow-hidden rounded-2xl border border-cyan-400/20 bg-[linear-gradient(135deg,rgba(8,145,178,.09),rgba(15,23,42,.15)_45%,rgba(16,185,129,.05))] shadow-[0_24px_80px_rgba(2,8,23,.22)] backdrop-blur">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-cyan-400/10 px-5 py-4 md:px-6">
        <div>
          <div className="mb-1 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300">
            <FileSearch size={14} /> resultado persistido
          </div>
          <h2 className="text-lg font-semibold tracking-tight">Resultado da análise</h2>
        </div>
        <span className={`rounded-full border px-3 py-1 font-mono text-[10px] uppercase ${statusTone(result.status ?? "UNKNOWN")}`}>
          {result.status ?? "indisponível"}
        </span>
      </div>

      {!analysis ? (
        <div className="px-6 py-8 text-sm text-[var(--text-muted)]">
          Esta execução não possui um resultado de análise estruturado disponível.
        </div>
      ) : (
        <div className="space-y-6 px-5 py-5 md:px-6">
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto]">
            <div>
              <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--text-muted)]">Diagnóstico</p>
              <p className="max-w-5xl text-sm leading-7 text-[var(--text-primary)] md:text-[15px]">
                {analysis.diagnosis || "Diagnóstico textual não informado."}
              </p>
            </div>
            {analysis.root_cause_classification && (
              <div className="min-w-60 rounded-xl border border-amber-400/20 bg-amber-400/[.06] px-4 py-3">
                <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-amber-300/70">Causa raiz</p>
                <p className="mt-1 font-mono text-xs font-semibold text-amber-200">{analysis.root_cause_classification}</p>
              </div>
            )}
          </div>

          {affectedModules.length > 0 && (
            <div>
              <div className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-[var(--text-muted)]"><Boxes size={13} /> Módulos afetados</div>
              <div className="flex flex-wrap gap-2">
                {affectedModules.map((module) => <span key={module} className="rounded-lg border border-cyan-400/15 bg-cyan-400/[.05] px-2.5 py-1 font-mono text-[10px] text-cyan-200">{module}</span>)}
              </div>
            </div>
          )}

          <div>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-[var(--text-muted)]"><FileSearch size={13} /> Evidências usadas</div>
              <span className="font-mono text-[10px] text-cyan-300/70">{evidence.length} itens</span>
            </div>
            {evidence.length > 0 ? (
              <ol className="grid gap-3 md:grid-cols-2">
                {evidence.map((item, index) => (
                  <li key={`${item.evidence_id ?? item.tool ?? "evidence"}-${index}`} className="rounded-xl border border-[var(--border-subtle)] bg-black/10 p-4">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <span className="font-mono text-[10px] text-cyan-300">{item.tool || `evidência ${index + 1}`}</span>
                      {item.evidence_id && <span className="max-w-40 truncate font-mono text-[9px] text-[var(--text-muted)]" title={item.evidence_id}>{item.evidence_id}</span>}
                    </div>
                    <p className="text-xs leading-5 text-[var(--text-primary)]">{item.finding || displayValue(item)}</p>
                  </li>
                ))}
              </ol>
            ) : <p className="rounded-xl border border-[var(--border-subtle)] bg-black/10 px-4 py-3 text-xs text-[var(--text-muted)]">Nenhuma evidência textual foi retornada.</p>}
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="rounded-xl border border-emerald-400/15 bg-emerald-400/[.04] p-4 lg:col-span-1">
              <div className="mb-3 flex items-center gap-2 text-xs font-medium text-emerald-200"><Lightbulb size={14} /> Recomendações</div>
              {result.recommendations.length === 0 ? (
                <p className="text-xs leading-5 text-[var(--text-muted)]">Nenhum ajuste aplicável foi proposto nesta análise.</p>
              ) : (
                <ul className="space-y-3">
                  {result.recommendations.map((recommendation, index) => (
                    <li key={`${recommendation.target_path ?? "recommendation"}-${index}`} className="border-l border-emerald-400/30 pl-3 text-xs leading-5">
                      <p className="font-mono text-[10px] text-emerald-300">{recommendation.target_module ?? "ajuste"} · {recommendation.target_path ?? recommendation.operation ?? index + 1}</p>
                      {(recommendation.current_value !== undefined || recommendation.proposed_value !== undefined) && <p className="mt-1 text-[var(--text-muted)]">{displayValue(recommendation.current_value)} → <span className="text-[var(--text-primary)]">{displayValue(recommendation.proposed_value)}</span></p>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="rounded-xl border border-amber-400/15 bg-amber-400/[.04] p-4">
              <div className="mb-3 flex items-center gap-2 text-xs font-medium text-amber-200"><AlertTriangle size={14} /> Avisos</div>
              {result.warnings.length > 0 ? <ul className="space-y-2 text-xs leading-5 text-[var(--text-muted)]">{result.warnings.map((warning, index) => <li key={index}>• {warning}</li>)}</ul> : <p className="text-xs text-[var(--text-muted)]">Nenhum aviso registrado.</p>}
            </div>
            <div className="rounded-xl border border-slate-400/15 bg-slate-400/[.03] p-4">
              <div className="mb-3 flex items-center gap-2 text-xs font-medium text-slate-200"><CircleDot size={14} /> Limitações</div>
              {result.limitations.length > 0 ? <ul className="space-y-2 text-xs leading-5 text-[var(--text-muted)]">{result.limitations.map((limitation, index) => <li key={index}>• {limitation}</li>)}</ul> : <p className="text-xs text-[var(--text-muted)]">Nenhuma limitação registrada.</p>}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default function IntelligenceRunsPage() {
  const [runs, setRuns] = useState<GraphRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<GraphEvent[]>([]);
  const [interrupts, setInterrupts] = useState<GraphInterrupt[]>([]);
  const [runContext, setRunContext] = useState<RunContext | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [gateBusy, setGateBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candidateIds, setCandidateIds] = useState("");
  const detailRequestSequence = useRef(0);

  const selected = useMemo(
    () => runs.find((run) => run.id === selectedId) ?? null,
    [runs, selectedId],
  );

  const refresh = useCallback(async (preferredId?: string | null) => {
    setError(null);
    try {
      const [runResponse, capabilityResponse] = await Promise.all([
        apiGet<{ items: GraphRun[] }>("/ai/graphs/runs?limit=100"),
        apiGet<Capabilities>("/ai/graphs/capabilities"),
      ]);
      setRuns(runResponse.items);
      setCapabilities(capabilityResponse);
      const linkedRun = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("run");
      setSelectedId((current) => preferredId ?? current ?? linkedRun ?? runResponse.items[0]?.id ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao carregar execuções");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshDetail = useCallback(async (runId: string) => {
    const requestSequence = ++detailRequestSequence.current;
    try {
      const [timeline, interruptResponse, contextResponse] = await Promise.all([
        apiGet<{ items: GraphEvent[] }>(`/ai/graphs/runs/${runId}/timeline?limit=200`),
        apiGet<{ items: GraphInterrupt[] }>(`/ai/graphs/runs/${runId}/interrupts`),
        apiGet<RunContext>(`/ai/graphs/runs/${runId}/context`),
      ]);
      if (requestSequence !== detailRequestSequence.current) return;
      setEvents(timeline.items);
      setInterrupts(interruptResponse.items);
      setRunContext(contextResponse);
    } catch (caught) {
      if (requestSequence !== detailRequestSequence.current) return;
      setError(caught instanceof Error ? caught.message : "Falha ao carregar a trilha");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (selectedId) void refreshDetail(selectedId);
    else {
      detailRequestSequence.current += 1;
      setEvents([]);
      setInterrupts([]);
      setRunContext(null);
    }
  }, [selectedId, refreshDetail]);

  useEffect(() => {
    if (!selected || TERMINAL.has(selected.status)) return;
    const timer = window.setInterval(() => {
      void refresh(selected.id);
      void refreshDetail(selected.id);
    }, 8000);
    return () => window.clearInterval(timer);
  }, [selected, refresh, refreshDetail]);

  async function decide(interrupt: GraphInterrupt, decision: "approve" | "reject" | "edit") {
    if (!selected) return;
    setActionBusy(true);
    setError(null);
    try {
      const edits = decision === "edit" || (decision === "approve" && candidateIds.trim())
        ? { candidate_version_ids: candidateIds.split(",").map((value) => value.trim()).filter(Boolean) }
        : {};
      await apiPost(`/ai/graphs/runs/${selected.id}/resume`, {
        interrupt_id: interrupt.id,
        decision,
        decision_id: crypto.randomUUID(),
        idempotency_key: `ui-resume-${crypto.randomUUID()}`,
        edits,
      });
      setCandidateIds("");
      await refresh(selected.id);
      await refreshDetail(selected.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Decisão não foi aplicada");
    } finally {
      setActionBusy(false);
    }
  }

  async function cancelRun() {
    if (!selected) return;
    setActionBusy(true);
    try {
      await apiPost(`/ai/graphs/runs/${selected.id}/cancel`, {});
      await refresh(selected.id);
      await refreshDetail(selected.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Cancelamento não foi aplicado");
    } finally {
      setActionBusy(false);
    }
  }

  async function toggleNormalProviderGate() {
    if (!capabilities) return;
    const nextValue = !capabilities.normal_analysis_provider_enabled;
    const action = nextValue ? "habilitar" : "desabilitar";
    if (!window.confirm(`Confirmar ${action} o provider apenas para analises normais deste tenant?`)) return;
    setGateBusy(true);
    setError(null);
    try {
      await apiPut(
        `/config/ai_provider_runtime?change_description=${encodeURIComponent(`Intelligence Runs: ${action} gate normal`)}`,
        { normal_analysis_provider_enabled: nextValue },
      );
      await refresh(selectedId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Gate normal nao foi alterado");
    } finally {
      setGateBusy(false);
    }
  }

  const pendingInterrupt = interrupts.find((item) => item.status === "PENDING") ?? null;

  return (
    <main className="min-h-screen bg-[var(--bg-primary)] px-4 py-5 text-[var(--text-primary)] md:px-6">
      <div className="pointer-events-none fixed inset-0 opacity-30 [background-image:linear-gradient(rgba(34,211,238,.035)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,.035)_1px,transparent_1px)] [background-size:32px_32px]" />
      <div className="relative mx-auto max-w-[1680px]">
        <header className="mb-5 flex flex-wrap items-end justify-between gap-4 border-b border-[var(--border-subtle)] pb-5">
          <div>
            <div className="mb-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.24em] text-cyan-400">
              <GitBranch size={13} /> systemic orchestration ledger
            </div>
            <h1 className="text-2xl font-semibold tracking-tight">Intelligence Runs</h1>
            <p className="mt-1 max-w-2xl text-sm text-[var(--text-muted)]">
              Trilha durável de análise, checkpoints e decisões humanas. Nenhuma execução possui autoridade de escrita live.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase ${capabilities?.strict_msgpack ? "border-emerald-400/30 text-emerald-300" : "border-rose-400/30 text-rose-300"}`}>
              {capabilities?.strict_msgpack ? "checkpoint strict" : "checkpoint bloqueado"}
            </span>
            <span className="rounded-full border border-emerald-400/30 px-3 py-1.5 font-mono text-[10px] uppercase text-emerald-300">
              live write: denied
            </span>
            <button onClick={() => void refresh(selectedId)} className="rounded-lg border border-[var(--border-subtle)] p-2 text-[var(--text-muted)] transition hover:border-cyan-400/40 hover:text-cyan-300" aria-label="Atualizar">
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-200">
            <AlertTriangle size={16} /> {error}
          </div>
        )}

        {selected && runContext && <AnalysisResultPanel context={runContext} />}
        {selected?.status === "COMPLETED" && runContext?.result.status === "COMPLETED" && (
          <AnalysisChatPanel
            runId={selected.id}
            graphLabel={`${selected.graph_key ?? "analysis"}@${selected.graph_version ?? "—"}`}
            snapshotLabel={`${runContext.dataset.contract_version ?? "snapshot"} · ${runContext.dataset.row_count ?? "—"} registros`}
            modelLabel={`${runContext.model.effective_provider ?? "—"}/${runContext.model.effective_model ?? "—"}`}
          />
        )}

        <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)_360px]">
          <section className="overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90 backdrop-blur">
            <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-3">
              <h2 className="text-sm font-medium">Execuções</h2>
              <span className="font-mono text-[10px] text-[var(--text-muted)]">{runs.length} records</span>
            </div>
            <div className="max-h-[72vh] overflow-auto p-2">
              {loading && <p className="p-4 text-sm text-[var(--text-muted)]">Carregando trilha…</p>}
              {!loading && runs.length === 0 && <p className="p-4 text-sm text-[var(--text-muted)]">Nenhuma execução registrada.</p>}
              {runs.map((run) => (
                <button key={run.id} onClick={() => setSelectedId(run.id)} className={`mb-1 w-full rounded-xl border p-3 text-left transition ${selectedId === run.id ? "border-cyan-400/35 bg-cyan-400/8" : "border-transparent hover:border-[var(--border-subtle)] hover:bg-white/[.02]"}`}>
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] ${statusTone(run.error_kind === "PROVIDER_BLOCKED" ? "INTERRUPTED" : run.status)}`}>{runStatusLabel(run)}</span>
                    <span className="font-mono text-[10px] text-[var(--text-muted)]">{shortId(run.id)}</span>
                  </div>
                  <p className="truncate text-sm font-medium">{runTitle(run)}</p>
                  <p className="mt-1 truncate font-mono text-[10px] text-cyan-300/70">{run.origin_module ?? run.graph_key ?? "canonical"}</p>
                  <div className="mt-2 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
                    <span>{run.authority}</span><span>{when(run.updated_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          </section>

          <section className="min-w-0 rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90 backdrop-blur">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-5 py-4">
              <div>
                <h2 className="text-sm font-medium">Execution trace</h2>
                <p className="mt-1 font-mono text-[10px] text-[var(--text-muted)]">{selected ? selected.id : "select a run"}</p>
              </div>
              {selected && !TERMINAL.has(selected.status) && (
                <button disabled={actionBusy} onClick={() => void cancelRun()} className="flex items-center gap-2 rounded-lg border border-rose-400/30 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-400/10 disabled:opacity-40">
                  <Square size={12} /> Cancelar
                </button>
              )}
            </div>
            <div className="max-h-[72vh] overflow-auto px-5 py-4">
              {!selected && <p className="py-12 text-center text-sm text-[var(--text-muted)]">Selecione uma execução para inspecionar a linhagem.</p>}
              {selected && events.length === 0 && <p className="py-12 text-center text-sm text-[var(--text-muted)]">Aguardando o primeiro evento.</p>}
              <ol className="relative ml-2 border-l border-cyan-400/15">
                {events.map((event, index) => (
                  <li key={event.id} className="relative pb-5 pl-7 last:pb-0">
                    <span className={`absolute -left-[7px] top-1 grid h-3.5 w-3.5 place-items-center rounded-full border ${index === events.length - 1 ? "border-cyan-300 bg-cyan-400/25" : "border-slate-600 bg-slate-900"}`}>
                      <span className="h-1 w-1 rounded-full bg-cyan-300" />
                    </span>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div><p className="text-sm font-medium">{event.node_name ?? event.event_type}</p><p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-cyan-400/70">{event.event_type}</p></div>
                      <time className="font-mono text-[10px] text-[var(--text-muted)]">{when(event.created_at)}</time>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <aside className="space-y-4">
            <section className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90 p-4 backdrop-blur">
              <div className="mb-4 flex items-center gap-2 text-sm font-medium"><ShieldCheck size={16} className="text-emerald-300" /> Authority envelope</div>
              <dl className="space-y-3 text-xs">
                <div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Authority</dt><dd className="font-mono text-cyan-300">{selected?.authority ?? "—"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Runtime</dt><dd className="font-mono">{capabilities?.runtime ?? "—"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Entrypoints</dt><dd>{capabilities?.entrypoints_enabled ? "enabled" : "disabled"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Canario real</dt><dd>{capabilities?.real_provider_canary_enabled ? "enabled" : "disabled"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Provider normal</dt><dd>{capabilities?.normal_analysis_provider_enabled ? "enabled" : "disabled"}</dd></div>
              </dl>
              <button disabled={gateBusy || !capabilities} onClick={() => void toggleNormalProviderGate()} className="mt-4 w-full rounded-lg border border-[var(--border-subtle)] py-2 text-xs text-[var(--text-muted)] hover:border-cyan-400/40 hover:text-cyan-300 disabled:opacity-40">
                {capabilities?.normal_analysis_provider_enabled ? "Desabilitar provider normal" : "Habilitar provider normal"}
              </button>
              <p className="mt-2 text-[10px] leading-4 text-[var(--text-muted)]">Este gate e separado dos canarios e nao concede escrita live.</p>
            </section>

            {selected && runContext && (
              <section className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90 p-4 text-xs backdrop-blur">
                <div className="mb-3 flex items-center gap-2 font-medium"><GitBranch size={15} className="text-cyan-300" /> Canonical lineage</div>
                <dl className="space-y-2 text-[var(--text-muted)]">
                  <div><dt className="text-[10px] uppercase tracking-wider">Graph</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{selected.graph_key ?? "—"} · {selected.graph_version ?? "—"}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Model</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.model.configured_provider}/{runContext.model.configured_model}</dd><dd className="font-mono text-cyan-300">effective: {runContext.model.effective_provider}/{runContext.model.effective_model}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Prompt</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.prompt.key}@{runContext.prompt.version}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Dataset</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.dataset.quality_status ?? "—"} · rows {runContext.dataset.row_count ?? "—"}</dd><dd className="truncate font-mono">{runContext.dataset.id ?? "—"}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Bundle</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.bundle.lineage_status ?? "—"}</dd><dd className="truncate font-mono">{runContext.bundle.id ?? "—"}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Modules consulted</dt><dd className="mt-0.5 leading-5 text-[var(--text-primary)]">{runContext.dataset.context_manifest?.modules_consulted?.join(" · ") || "—"}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Tool calls / memory</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.dataset.context_manifest?.tools_called?.length ?? 0} / {runContext.result.memory_hits.length}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Usage / cost</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.usage.tokens_input ?? "—"} in · {runContext.usage.tokens_output ?? "—"} out · {runContext.usage.actual_cost ?? "—"} {runContext.usage.currency ?? ""}</dd></div>
                  <div><dt className="text-[10px] uppercase tracking-wider">Budget reservation</dt><dd className="mt-0.5 font-mono text-[var(--text-primary)]">{runContext.budget_reservation.status ?? "—"} · reserved {runContext.budget_reservation.reserved_tokens ?? "—"} · actual {runContext.budget_reservation.actual_tokens ?? "—"} · released {runContext.budget_reservation.released_tokens ?? "—"}</dd></div>
                </dl>
              </section>
            )}

            <section className={`rounded-2xl border p-4 backdrop-blur ${pendingInterrupt ? "border-amber-400/30 bg-amber-400/[.06]" : "border-[var(--border-subtle)] bg-[var(--bg-surface)]/90"}`}>
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-medium">{pendingInterrupt ? <Pause size={16} className="text-amber-300" /> : <CircleDot size={16} className="text-[var(--text-muted)]" />} Human gate</div>
                <span className="font-mono text-[9px] uppercase text-[var(--text-muted)]">{pendingInterrupt ? "action required" : "clear"}</span>
              </div>
              {!pendingInterrupt && <p className="text-xs leading-5 text-[var(--text-muted)]">Nenhuma decisão humana pendente nesta execução.</p>}
              {pendingInterrupt && (
                <div className="space-y-3">
                  <div><p className="font-mono text-[10px] text-amber-300">{pendingInterrupt.interrupt_type}</p><p className="mt-1 text-xs text-[var(--text-muted)]">A retomada mantém dataset, bundle e autoridade originais imutáveis.</p></div>
                  {pendingInterrupt.allowed_edit_fields.includes("candidate_version_ids") && (
                    <label className="block text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Candidate version IDs
                      <textarea value={candidateIds} onChange={(event) => setCandidateIds(event.target.value)} rows={3} placeholder="UUIDs separados por vírgula" className="mt-1 w-full resize-none rounded-lg border border-[var(--border-subtle)] bg-black/20 p-2 font-mono text-[11px] normal-case tracking-normal outline-none focus:border-cyan-400/40" />
                    </label>
                  )}
                  <div className="grid grid-cols-2 gap-2">
                    <button disabled={actionBusy} onClick={() => void decide(pendingInterrupt, "reject")} className="flex items-center justify-center gap-1 rounded-lg border border-rose-400/30 py-2 text-xs text-rose-300 hover:bg-rose-400/10 disabled:opacity-40"><X size={13} /> Rejeitar</button>
                    <button disabled={actionBusy} onClick={() => void decide(pendingInterrupt, candidateIds.trim() ? "edit" : "approve")} className="flex items-center justify-center gap-1 rounded-lg bg-cyan-300 py-2 text-xs font-medium text-slate-950 hover:bg-cyan-200 disabled:opacity-40"><Check size={13} /> Aprovar</button>
                  </div>
                </div>
              )}
            </section>

            {selected && (
              <section className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]/90 p-4 text-xs backdrop-blur">
                <div className="mb-3 flex items-center gap-2 font-medium"><Activity size={15} className="text-cyan-300" /> Run facts</div>
                <div className="space-y-2 text-[var(--text-muted)]">
                  <p className="flex items-center gap-2"><Clock3 size={12} /> created {when(selected.created_at)}</p>
                  <p className="flex items-center gap-2"><ChevronRight size={12} /> Intent: {selected.request_intent ?? "—"}</p>
                  <p className="flex items-center gap-2"><ChevronRight size={12} /> Ultimo no concluido: {selected.last_completed_node ?? "—"}</p>
                  <p className="flex items-center gap-2"><AlertTriangle size={12} /> No que falhou: {selected.failed_node ?? "—"}</p>
                  {selected.error_kind === "PROVIDER_BLOCKED" && <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-amber-200"><p className="font-medium">Provider bloqueado</p><p className="mt-1 leading-5">O transporte nao foi iniciado. Verifique o intent e o gate operacional correspondente; o canario real nao corrige uma analise normal.</p></div>}
                  {selected.last_error_code && <p className="font-mono text-rose-300">{selected.last_error_code}</p>}
                  {selected.last_error_safe_message && <p className="leading-5">{selected.last_error_safe_message}</p>}
                  <p className="font-mono text-[10px]">transport attempted: {selected.provider_transport_attempted === null ? "—" : String(selected.provider_transport_attempted)}</p>
                  <p className="truncate font-mono text-[10px]">correlation: {selected.correlation_id ?? "—"}</p>
                </div>
              </section>
            )}
          </aside>
        </div>
      </div>
    </main>
  );
}
