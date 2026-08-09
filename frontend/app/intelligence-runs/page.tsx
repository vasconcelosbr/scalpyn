"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, Check, ChevronRight, CircleDot, Clock3,
  GitBranch, Pause, RefreshCw, ShieldCheck, Square, X,
} from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";

type GraphRun = {
  id: string;
  ai_request_id: string;
  graph_definition_id: string;
  status: string;
  current_node: string | null;
  authority: string;
  state_schema_version: string;
  started_at: string | null;
  completed_at: string | null;
  terminal_reason: string | null;
  last_error_code: string | null;
  last_error_safe_message: string | null;
  created_at: string;
  updated_at: string;
  origin_module: string | null;
  origin_view: string | null;
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
  strict_msgpack: boolean;
  live_write: boolean;
  module_flags: Record<string, boolean>;
};

type RunContext = {
  model: { configured_provider: string | null; configured_model: string | null; effective_provider: string | null; effective_model: string | null; resolution_reason: string | null };
  prompt: { key: string | null; version: string | null; hash: string | null };
  dataset: { id: string | null; hash: string | null; contract_version: string | null; quality_status: string | null; row_count: number | null; module_context_refs: Record<string, unknown> | null; context_manifest: { modules_consulted?: string[]; tools_called?: string[]; evidence_ids?: string[] } | null };
  bundle: { id: string | null; hash: string | null; lineage_status: string | null; lineage_refs: Record<string, unknown> | null };
  result: { status: string | null; warnings: string[]; limitations: string[]; memory_hits: Array<Record<string, unknown>> };
  usage: { tokens_input: number | null; tokens_output: number | null; actual_cost: string | null; currency: string | null; pricing_snapshot_version: string | null };
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

export default function IntelligenceRunsPage() {
  const [runs, setRuns] = useState<GraphRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<GraphEvent[]>([]);
  const [interrupts, setInterrupts] = useState<GraphInterrupt[]>([]);
  const [runContext, setRunContext] = useState<RunContext | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candidateIds, setCandidateIds] = useState("");

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
    try {
      const [timeline, interruptResponse, contextResponse] = await Promise.all([
        apiGet<{ items: GraphEvent[] }>(`/ai/graphs/runs/${runId}/timeline?limit=200`),
        apiGet<{ items: GraphInterrupt[] }>(`/ai/graphs/runs/${runId}/interrupts`),
        apiGet<RunContext>(`/ai/graphs/runs/${runId}/context`),
      ]);
      setEvents(timeline.items);
      setInterrupts(interruptResponse.items);
      setRunContext(contextResponse);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao carregar a trilha");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (selectedId) void refreshDetail(selectedId);
    else { setEvents([]); setInterrupts([]); setRunContext(null); }
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
                    <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] ${statusTone(run.status)}`}>{run.status}</span>
                    <span className="font-mono text-[10px] text-[var(--text-muted)]">{shortId(run.id)}</span>
                  </div>
                  <p className="truncate text-sm font-medium">{run.current_node ?? "queued"}</p>
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
                <div className="flex justify-between gap-3"><dt className="text-[var(--text-muted)]">Provider canary</dt><dd>{capabilities?.real_provider_canary_enabled ? "enabled" : "disabled"}</dd></div>
              </dl>
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
                  <p className="flex items-center gap-2"><ChevronRight size={12} /> {selected.terminal_reason ?? selected.current_node ?? "queued"}</p>
                  {selected.last_error_code && <p className="font-mono text-rose-300">{selected.last_error_code}</p>}
                </div>
              </section>
            )}
          </aside>
        </div>
      </div>
    </main>
  );
}
