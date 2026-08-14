"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle, Bot, Check, ChevronDown, CircleStop, Database, FileKey,
  LoaderCircle, MessageSquareText, Plus, Send, ShieldCheck, Sparkles, X,
} from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import {
  activeChatTurnForReattach,
  assistantForGraphRun,
  cacheReconciliationStates,
  consumeChatEventStream,
  isChatTurnTerminal,
  pollExactChatTurn,
} from "@/lib/analysis-chat-runtime";

type DataMode =
  | "FROZEN_ANALYSIS_ONLY"
  | "ALLOW_READONLY_REFRESH"
  | "CREATE_CHILD_ANALYSIS"
  | "DRAFT_PROPOSAL";

type ChatFlags = {
  enabled: boolean;
  readonly_refresh_enabled: boolean;
  child_analysis_enabled: boolean;
  proposals_enabled: boolean;
  governed_actions_enabled: boolean;
  live_config_write_enabled: boolean;
  streaming_enabled: boolean;
  summary_enabled: boolean;
  budget_enforcement_enabled: boolean;
  provider_max_cost_usd: string;
  proposal_max_output_tokens: number;
  request_token_limit: number;
  daily_token_limit: number;
  monthly_token_limit: number;
};

type Conversation = {
  conversation_id: string;
  thread_id: string;
  parent_analysis_run_id: string;
  title: string | null;
  status: string;
  message_count: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_cost_usd: string;
  updated_at: string;
};

type PendingInterrupt = {
  id: string;
  interrupt_type: string;
  status: string;
  allowed_edit_fields: string[];
};

type ChatMessage = {
  id: string;
  sequence_number: number;
  role: "USER" | "ASSISTANT" | "SYSTEM";
  message_type: string;
  status: string;
  content: string | null;
  graph_run_id: string | null;
  answer_type: string | null;
  data_mode: DataMode | null;
  evidence_refs: Array<{ evidence_id?: string; module?: string; label?: string; source?: string }>;
  modules_consulted: string[];
  warnings: string[];
  limitations: string[];
  suggested_questions: string[];
  new_data_queried: boolean;
  provider_transport_attempted: boolean | null;
  tokens_input: number | null;
  tokens_output: number | null;
  cost_usd: string | null;
  effective_provider: string | null;
  effective_model: string | null;
  budget_reservation: {
    status: string;
    reserved_tokens: number;
    actual_tokens: number | null;
    released_tokens: number;
    actual_cost_usd: string | null;
    provider_transport_attempted: boolean;
    terminal_reason: string | null;
  } | null;
  pending_interrupt: PendingInterrupt | null;
  proposal: {
    proposal_id: string;
    operation_type: string;
    target_type: string;
    target_id: string;
    target: { profile_id?: string; profile_ids?: string[]; profile_name?: string; config_type?: string; pool_id?: string | null };
    objective: string;
    risk: string | null;
    changes: Array<{ op: string; path: string; old_value: unknown; value: unknown; reason: string }>;
    status: string;
    rollback_available: boolean;
    execution_result: Record<string, unknown> | null;
    candidate_validation?: {
      decision: string;
      validation_scope: string;
      risk_validation: string;
      strategy_validation: string;
      policy_semantic_validation: { risk: string; strategy: string };
    } | null;
    validation_scope?: string | null;
    policy_semantic_validation?: { risk: string; strategy: string } | null;
  } | null;
};

type AcceptedTurn = {
  assistant_message: ChatMessage;
  graph_run_id: string;
  stream_after_event_id: number;
};

type AcceptedDecision = {
  status: string;
  reused: boolean;
  graph_run_id: string;
  stream_after_event_id: number;
};

type Props = {
  runId: string;
  graphLabel: string;
  snapshotLabel: string;
  modelLabel: string;
};

const TERMINAL_MESSAGE = new Set(["COMPLETED", "BLOCKED", "FAILED", "CANCELLED"]);
const STREAM_RECONNECT_ATTEMPTS = 4;
const TURN_POLL_ATTEMPTS = 120;
const TURN_RETRY_DELAY_MS = 1000;

function token() {
  return typeof window === "undefined" ? null : localStorage.getItem("token");
}

function modeLabel(mode: DataMode) {
  return {
    FROZEN_ANALYSIS_ONLY: "Snapshot original",
    ALLOW_READONLY_REFRESH: "Atualizar read-only",
    CREATE_CHILD_ANALYSIS: "Análise filha",
    DRAFT_PROPOSAL: "Executar alteração",
  }[mode];
}

function statusLabel(message: ChatMessage) {
  if (message.status === "BLOCKED") return "Provider bloqueado";
  if (message.status === "STREAMING") return "Pensando…";
  if (message.status === "INTERRUPTED") return "Confirmação humana necessária";
  if (message.status === "COMPLETED") return "Resposta concluída";
  return message.status;
}

function policyValidationLabel(value: string) {
  const labels: Record<string, string> = {
    PASS: "validado",
    VETO: "vetado",
    NOT_PERFORMED: "não validado (execução bloqueada)",
  };
  return labels[value] ?? value;
}

export function AnalysisChatPanel({ runId, graphLabel, snapshotLabel, modelLabel }: Props) {
  const [open, setOpen] = useState(false);
  const [flags, setFlags] = useState<ChatFlags | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<DataMode>("FROZEN_ANALYSIS_ONLY");
  const [busy, setBusy] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [rollbackArmed, setRollbackArmed] = useState<string | null>(null);
  const [submittedInterrupts, setSubmittedInterrupts] = useState<Set<string>>(() => new Set());
  const streamAbort = useRef<AbortController | null>(null);
  const streamCursor = useRef(0);
  const activeConversationRef = useRef<string | null>(null);
  const activeStreamRunRef = useRef<string | null>(null);
  const activeAnalysisRunRef = useRef(runId);
  activeAnalysisRunRef.current = runId;

  const loadConversations = useCallback(async () => {
    try {
      const requestedRunId = runId;
      const response = await apiGet<{ items: Conversation[]; feature_flags: ChatFlags }>(
        `/intelligence-runs/${runId}/conversations`,
      );
      if (activeAnalysisRunRef.current !== requestedRunId) return;
      setFlags(response.feature_flags);
      setConversations(response.items);
      setConversationId((current) => (
        current && response.items.some((item) => item.conversation_id === current)
          ? current
          : response.items[0]?.conversation_id ?? null
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível carregar o chat");
    }
  }, [runId]);

  const loadMessages = useCallback(async (id: string) => {
    const response = await apiGet<{ items: ChatMessage[] }>(
      `/intelligence-conversations/${id}/messages`,
    );
    if (activeConversationRef.current === id) {
      setMessages(response.items);
    }
    return response.items;
  }, []);

  useEffect(() => {
    setOpen(false);
    setConversationId(null);
    setMessages([]);
    setFlags(null);
    activeConversationRef.current = null;
    activeStreamRunRef.current = null;
    streamAbort.current?.abort();
    streamCursor.current = 0;
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    activeConversationRef.current = conversationId;
    activeStreamRunRef.current = null;
    streamAbort.current?.abort();
    streamCursor.current = 0;
    if (!conversationId || !open) return;
    let cancelled = false;
    let reattachedRunId: string | null = null;
    void (async () => {
      try {
        const rows = await loadMessages(conversationId);
        if (cancelled || activeConversationRef.current !== conversationId) return;
        const activeTurn = activeChatTurnForReattach(rows);
        if (!activeTurn?.graph_run_id) return;
        reattachedRunId = activeTurn.graph_run_id;
        activeStreamRunRef.current = reattachedRunId;
        // Run scoping makes a zero cursor safe: only this turn is replayed.
        streamCursor.current = 0;
        setBusy(true);
        if (flags?.streaming_enabled) {
          await consumeStream(conversationId, reattachedRunId);
        } else {
          const controller = new AbortController();
          streamAbort.current = controller;
          await pollTurnUntilTerminal(
            conversationId, reattachedRunId, controller.signal,
          );
        }
      } catch (caught) {
        if (
          !cancelled
          && (caught as Error).name !== "AbortError"
          && activeConversationRef.current === conversationId
        ) {
          setError(caught instanceof Error ? caught.message : "Falha ao acompanhar a mensagem ativa");
        }
      } finally {
        if (
          !cancelled
          && reattachedRunId
          && isCurrentTurn(conversationId, reattachedRunId)
        ) {
          setBusy(false);
          setStreamText("");
        }
      }
    })();
    return () => {
      cancelled = true;
      if (reattachedRunId) {
        streamAbort.current?.abort();
        setBusy(false);
        setStreamText("");
      }
    };
  }, [conversationId, flags?.streaming_enabled, loadMessages, open]); // eslint-disable-line react-hooks/exhaustive-deps

  function selectConversation(id: string) {
    activeConversationRef.current = id;
    activeStreamRunRef.current = null;
    streamAbort.current?.abort();
    streamCursor.current = 0;
    setStreamText("");
    setConversationId(id);
  }

  async function createConversationRecord() {
    const created = await apiPost<Conversation>(`/intelligence-runs/${runId}/conversations`, {
      title: "Chat da Análise",
    });
    await loadConversations();
    selectConversation(created.conversation_id);
    setMessages([]);
    setOpen(true);
    return created.conversation_id;
  }

  async function createConversation() {
    setBusy(true);
    setError(null);
    try {
      await createConversationRecord();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível criar a conversa");
    } finally {
      setBusy(false);
    }
  }

  function isCurrentTurn(id: string, graphRunId: string) {
    return (
      activeConversationRef.current === id
      && activeStreamRunRef.current === graphRunId
    );
  }

  async function delayForTurn(signal: AbortSignal) {
    await new Promise<void>((resolve, reject) => {
      if (signal.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      const onAbort = () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      const timer = window.setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, TURN_RETRY_DELAY_MS);
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  async function refreshTurn(id: string, graphRunId: string) {
    if (!isCurrentTurn(id, graphRunId)) return true;
    const rows = await loadMessages(id);
    return isChatTurnTerminal(assistantForGraphRun(rows, graphRunId));
  }

  async function pollTurnUntilTerminal(
    id: string,
    graphRunId: string,
    signal: AbortSignal,
  ) {
    await pollExactChatTurn<ChatMessage>({
      graphRunId,
      attempts: TURN_POLL_ATTEMPTS,
      signal,
      isCurrent: () => isCurrentTurn(id, graphRunId),
      loadMessages: () => loadMessages(id),
      delay: delayForTurn,
    });
  }

  async function consumeStream(id: string, graphRunId: string) {
    streamAbort.current?.abort();
    const controller = new AbortController();
    streamAbort.current = controller;
    activeStreamRunRef.current = graphRunId;

    for (let attempt = 0; attempt < STREAM_RECONNECT_ATTEMPTS; attempt += 1) {
      if (!isCurrentTurn(id, graphRunId)) return;
      try {
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        const accessToken = token();
        if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
        if (streamCursor.current) headers["Last-Event-ID"] = String(streamCursor.current);
        const response = await fetch(
          `/api/intelligence-conversations/${id}/stream?graph_run_id=${encodeURIComponent(graphRunId)}`,
          { headers, signal: controller.signal, cache: "no-store" },
        );
        if (!response.ok || !response.body) {
          throw new Error(`Streaming indisponível (${response.status})`);
        }
        const streamOutcome = await consumeChatEventStream({
          body: response.body,
          isCurrent: () => isCurrentTurn(id, graphRunId),
          onEventId: (eventId) => { streamCursor.current = eventId; },
          onToken: (chunk) => {
            if (isCurrentTurn(id, graphRunId)) {
              setStreamText((current) => current + chunk);
            }
          },
          onTerminal: async () => {
            await loadMessages(id);
            if (isCurrentTurn(id, graphRunId)) setStreamText("");
          },
        });
        if (streamOutcome === "terminal") return;
      } catch (caught) {
        if ((caught as Error).name === "AbortError") throw caught;
        // Reconnect below from the latest acknowledged event id.
      }
      if (!isCurrentTurn(id, graphRunId)) return;
      try {
        if (await refreshTurn(id, graphRunId)) return;
      } catch (caught) {
        if ((caught as Error).name === "AbortError") throw caught;
      }
      await delayForTurn(controller.signal);
    }

    await pollTurnUntilTerminal(id, graphRunId, controller.signal);
  }

  async function sendMessage() {
    if (!draft.trim()) return;
    setBusy(true);
    setError(null);
    setStreamText("");
    try {
      let activeConversationId = conversationId;
      if (!activeConversationId) {
        activeConversationId = await createConversationRecord();
      }
      const accepted = await apiPost<AcceptedTurn>(`/intelligence-conversations/${activeConversationId}/messages`, {
        message: draft.trim(),
        data_mode: mode,
        idempotency_key: crypto.randomUUID(),
        response_language: "pt-BR",
      });
      streamCursor.current = Math.max(0, Number(accepted.stream_after_event_id) || 0);
      activeStreamRunRef.current = accepted.graph_run_id;
      setDraft("");
      await loadMessages(activeConversationId);
      if (flags?.streaming_enabled) {
        await consumeStream(activeConversationId, accepted.graph_run_id);
      }
      else {
        const controller = new AbortController();
        streamAbort.current = controller;
        await pollTurnUntilTerminal(
          activeConversationId, accepted.graph_run_id, controller.signal,
        );
      }
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setError(caught instanceof Error ? caught.message : "A pergunta não foi processada");
      }
    } finally {
      setBusy(false);
      setStreamText("");
    }
  }

  async function decide(message: ChatMessage, decision: "approve" | "reject") {
    if (!conversationId || !message.pending_interrupt) return;
    const interruptId = message.pending_interrupt.id;
    if (submittedInterrupts.has(interruptId)) return;
    setSubmittedInterrupts((current) => new Set(current).add(interruptId));
    setBusy(true);
    setError(null);
    try {
      const accepted = await apiPost<AcceptedDecision>(
        `/intelligence-conversations/${conversationId}/messages/${message.id}/decision`,
        {
          interrupt_id: interruptId,
          decision,
          decision_id: crypto.randomUUID(),
          idempotency_key: `chat-decision-${crypto.randomUUID()}`,
          edits: {},
        },
      );
      streamCursor.current = Math.max(0, Number(accepted.stream_after_event_id) || 0);
      activeStreamRunRef.current = accepted.graph_run_id;
      await loadMessages(conversationId);
      await consumeStream(conversationId, accepted.graph_run_id);
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : "A decisão não foi processada";
      if (detail.includes("GRAPH_INTERRUPT_ALREADY_RESOLVED")) {
        await loadMessages(conversationId);
      } else {
        setSubmittedInterrupts((current) => {
          const next = new Set(current);
          next.delete(interruptId);
          return next;
        });
        setError(detail);
      }
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!conversationId) return;
    streamAbort.current?.abort();
    await apiPost(`/intelligence-conversations/${conversationId}/cancel`, {});
    await loadMessages(conversationId);
  }

  async function rollbackProposal(message: ChatMessage) {
    if (!conversationId || !message.proposal) return;
    if (rollbackArmed !== message.proposal.proposal_id) {
      setRollbackArmed(message.proposal.proposal_id);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiPost(
        `/intelligence-conversations/${conversationId}/proposals/${message.proposal.proposal_id}/rollback`,
        { confirmation_text: "CONFIRMO ROLLBACK" },
      );
      setRollbackArmed(null);
      await loadMessages(conversationId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "O rollback não foi processado");
    } finally {
      setBusy(false);
    }
  }

  if (!flags?.enabled) return null;

  return (
    <section className="mb-5 overflow-hidden rounded-2xl border border-cyan-300/20 bg-[linear-gradient(145deg,rgba(6,182,212,.08),rgba(3,7,18,.72)_48%,rgba(16,185,129,.05))] shadow-[0_28px_90px_rgba(2,8,23,.36)]">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div>
          <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300">
            <Sparkles size={13} /> Chat da Análise
          </div>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Base: {graphLabel} · Snapshot: {snapshotLabel} · Modelo: {modelLabel} · Authority: {flags.governed_actions_enabled ? "ANÁLISE + ALTERAÇÃO CONFIRMADA" : "ANALYSIS_ONLY"}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => void createConversation()} disabled={busy} className="inline-flex items-center gap-2 rounded-lg border border-cyan-300/25 px-3 py-2 text-xs text-cyan-200 hover:bg-cyan-300/10 disabled:opacity-40">
            <Plus size={13} /> Novo chat
          </button>
          <button onClick={() => setOpen((value) => !value)} className="inline-flex items-center gap-2 rounded-lg bg-cyan-300 px-3 py-2 text-xs font-semibold text-slate-950 hover:bg-cyan-200">
            <MessageSquareText size={14} /> Conversar sobre esta análise <ChevronDown size={13} className={open ? "rotate-180" : ""} />
          </button>
        </div>
      </div>

      {open && (
        <div className="grid border-t border-cyan-300/10 lg:grid-cols-[250px_minmax(0,1fr)]">
          <aside className="border-b border-cyan-300/10 p-3 lg:border-b-0 lg:border-r">
            <p className="mb-2 px-2 font-mono text-[9px] uppercase tracking-[0.18em] text-[var(--text-muted)]">Conversas</p>
            <div className="space-y-1">
              {conversations.map((item) => (
                <button key={item.conversation_id} onClick={() => selectConversation(item.conversation_id)} className={`w-full rounded-xl border p-3 text-left ${conversationId === item.conversation_id ? "border-cyan-300/25 bg-cyan-300/[.07]" : "border-transparent hover:bg-white/[.03]"}`}>
                  <p className="truncate text-xs font-medium">{item.title || "Chat da Análise"}</p>
                  <p className="mt-1 font-mono text-[9px] text-[var(--text-muted)]">{item.message_count} mensagens · US$ {item.total_cost_usd}</p>
                </button>
              ))}
              {conversations.length === 0 && <p className="px-2 py-4 text-xs text-[var(--text-muted)]">Crie a primeira conversa para explorar o resultado.</p>}
            </div>
          </aside>

          <div className="flex min-h-[440px] flex-col">
            <div className="max-h-[58vh] flex-1 space-y-4 overflow-auto p-4 md:p-5">
              {messages.map((message) => (
                <article key={message.id} className={`max-w-[92%] rounded-2xl border p-4 ${message.role === "USER" ? "ml-auto border-cyan-300/20 bg-cyan-300/[.08]" : message.status === "BLOCKED" ? "border-amber-300/25 bg-amber-300/[.07]" : "border-[var(--border-subtle)] bg-black/20"}`}>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.14em] text-cyan-300">{message.role === "USER" ? <MessageSquareText size={12} /> : <Bot size={12} />} {message.role === "USER" ? "Você" : statusLabel(message)}</span>
                    {message.data_mode && <span className="rounded-full border border-white/10 px-2 py-0.5 font-mono text-[8px] text-[var(--text-muted)]">{modeLabel(message.data_mode)}</span>}
                  </div>
                  <p className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-primary)]">{message.content || (message.status === "STREAMING" ? "Consultando evidências…" : "Aguardando processamento…")}</p>
                  {message.role === "ASSISTANT" && message.evidence_refs.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {message.evidence_refs.map((evidence, index) => (
                        <span key={`${evidence.evidence_id}-${index}`} title={evidence.evidence_id} className="inline-flex items-center gap-1 rounded-md border border-emerald-300/15 bg-emerald-300/[.05] px-2 py-1 font-mono text-[8px] text-emerald-200">
                          <FileKey size={10} /> {evidence.module || "evidence"} · {evidence.label || evidence.evidence_id?.slice(0, 8)}
                        </span>
                      ))}
                    </div>
                  )}
                  {message.new_data_queried && <p className="mt-3 flex items-center gap-1.5 text-[10px] text-cyan-200"><Database size={11} /> Dados read-only atualizados, separados do snapshot original.</p>}
                  {message.role === "ASSISTANT"
                    && message.data_mode === "DRAFT_PROPOSAL"
                    && TERMINAL_MESSAGE.has(message.status)
                    && !message.proposal
                    && !message.pending_interrupt && (
                    <div className="mt-3 rounded-xl border border-amber-300/20 bg-amber-300/[.06] p-3">
                      <p className="text-xs font-semibold text-amber-100">Prévia executável não gerada</p>
                      <p className="mt-1 text-[10px] leading-5 text-[var(--text-muted)]">
                        A resposta terminou sem um diff aplicável. Nenhum perfil foi alterado. Reenvie a solicitação para gerar uma nova prévia auditável.
                      </p>
                    </div>
                  )}
                  {message.proposal && (
                    <div className="mt-3 rounded-xl border border-cyan-300/20 bg-cyan-300/[.05] p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-xs font-semibold text-cyan-100">Prévia da alteração</p>
                        <span className="rounded-full border border-cyan-300/20 px-2 py-0.5 font-mono text-[8px] text-cyan-200">{message.proposal.status}</span>
                      </div>
                      <p className="mt-2 text-xs text-[var(--text-primary)]">{message.proposal.objective}</p>
                      <p className="mt-1 font-mono text-[9px] text-[var(--text-muted)]">
                        {message.proposal.target.profile_name
                          || message.proposal.target.config_type
                          || (message.proposal.target.profile_ids?.length
                            ? `${message.proposal.target.profile_ids.length} perfis`
                            : message.proposal.target_id)}
                      </p>
                      <div className="mt-3 space-y-2">
                        {message.proposal.changes.map((change, index) => (
                          <div key={`${change.path}-${index}`} className="rounded-lg border border-white/[.06] bg-black/20 p-2">
                            <div className="flex items-center gap-2 font-mono text-[9px] text-cyan-200">
                              <span>{change.op.toUpperCase()}</span><span>{change.path}</span>
                            </div>
                            <div className="mt-1 grid gap-1 text-[10px] text-[var(--text-muted)] md:grid-cols-2">
                              <span>Antes: {JSON.stringify(change.old_value)}</span>
                              <span>Depois: {JSON.stringify(change.value)}</span>
                            </div>
                            <p className="mt-1 text-[10px] text-[var(--text-muted)]">{change.reason}</p>
                          </div>
                        ))}
                      </div>
                      {message.proposal.risk && <p className="mt-2 text-[10px] text-amber-200">Risco: {message.proposal.risk}</p>}
                      {cacheReconciliationStates(message.proposal.execution_result).map((cacheState) => {
                        const completed = cacheState.status === "COMPLETED";
                        const notRequired = cacheState.status === "NOT_REQUIRED";
                        const exhausted = cacheState.retryState === "EXHAUSTED";
                        const persistedSubject = cacheState.kind === "ROLLBACK"
                          ? "Rollback persistido"
                          : "Alteração persistida";
                        const headline = notRequired
                          ? "Cache não aplicável a alterações de perfil"
                          : completed
                          ? (cacheState.kind === "ROLLBACK"
                            ? "Runtime reconciliado após rollback"
                            : "Runtime reconciliado")
                          : exhausted
                            ? `${persistedSubject}, cache não reconciliado — intervenção necessária`
                            : `${persistedSubject}, cache pendente`;
                        return (
                          <div
                            key={cacheState.kind}
                            className={`mt-2 rounded-lg border p-2 text-[10px] ${completed || notRequired
                              ? "border-emerald-300/20 bg-emerald-300/[.05] text-emerald-100"
                              : exhausted
                                ? "border-rose-300/25 bg-rose-300/[.07] text-rose-100"
                                : "border-amber-300/20 bg-amber-300/[.06] text-amber-100"}`}
                          >
                            <p className="font-semibold">{headline}</p>
                            <p className="mt-1 font-mono text-[8px] opacity-80">
                              status: {cacheState.status}
                              {cacheState.retryState ? ` · retry: ${cacheState.retryState}` : ""}
                              {cacheState.attempts !== null
                                ? ` · tentativas: ${cacheState.attempts}${cacheState.maxAttempts !== null ? `/${cacheState.maxAttempts}` : ""}`
                                : ""}
                              {cacheState.nextRetryAt ? ` · próxima: ${cacheState.nextRetryAt}` : ""}
                            </p>
                          </div>
                        );
                      })}
                      {(message.proposal.policy_semantic_validation
                        || message.proposal.candidate_validation?.policy_semantic_validation) && (
                        <div className="mt-2 rounded-lg border border-white/[.06] bg-black/20 p-2 font-mono text-[9px] text-[var(--text-muted)]">
                          <p>Escopo: {message.proposal.validation_scope || message.proposal.candidate_validation?.validation_scope}</p>
                          <p>
                            Risk policy: {policyValidationLabel((message.proposal.policy_semantic_validation
                              || message.proposal.candidate_validation?.policy_semantic_validation)?.risk || "NOT_PERFORMED")}
                          </p>
                          <p>
                            Strategy policy: {policyValidationLabel((message.proposal.policy_semantic_validation
                              || message.proposal.candidate_validation?.policy_semantic_validation)?.strategy || "NOT_PERFORMED")}
                          </p>
                        </div>
                      )}
                      {message.proposal.rollback_available && (
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <button disabled={busy} onClick={() => void rollbackProposal(message)} className="rounded-lg border border-rose-300/25 px-3 py-1.5 text-xs text-rose-200">
                            {rollbackArmed === message.proposal.proposal_id ? "Confirmar rollback" : "Desfazer alteração"}
                          </button>
                          {rollbackArmed === message.proposal.proposal_id && (
                            <button onClick={() => setRollbackArmed(null)} className="px-2 py-1 text-[10px] text-[var(--text-muted)]">Cancelar</button>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  {message.warnings.map((warning) => <p key={warning} className="mt-2 text-[10px] text-amber-200">⚠ {warning}</p>)}
                  {message.limitations.map((limitation) => <p key={limitation} className="mt-1 text-[10px] text-[var(--text-muted)]">Limitação: {limitation}</p>)}
                  {message.pending_interrupt && !submittedInterrupts.has(message.pending_interrupt.id) && (
                    <div className="mt-3 rounded-xl border border-amber-300/20 bg-amber-300/[.06] p-3">
                      <p className="text-xs text-amber-100">Confirmação: {message.pending_interrupt.interrupt_type}</p>
                      <p className="mt-1 text-[10px] text-[var(--text-muted)]">
                        {message.pending_interrupt.interrupt_type === "PROPOSAL_APPROVAL"
                          ? "A confirmação aplicará exatamente o diff exibido acima e criará um rollback auditável."
                          : "Esta confirmação autoriza apenas gerar e validar a prévia; ainda não altera o sistema."}
                      </p>
                      <div className="mt-3 flex gap-2">
                        <button disabled={busy} onClick={() => void decide(message, "reject")} className="inline-flex items-center gap-1 rounded-lg border border-rose-300/25 px-3 py-1.5 text-xs text-rose-200"><X size={12} /> Rejeitar</button>
                        <button disabled={busy} onClick={() => void decide(message, "approve")} className="inline-flex items-center gap-1 rounded-lg bg-amber-200 px-3 py-1.5 text-xs font-medium text-amber-950"><Check size={12} /> Confirmar</button>
                      </div>
                    </div>
                  )}
                  {message.role === "ASSISTANT" && message.budget_reservation && (
                    <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-t border-white/[.06] pt-2 font-mono text-[8px] text-[var(--text-muted)]">
                      <span>{message.effective_provider}/{message.effective_model}</span>
                      <span>{message.tokens_input ?? 0} in · {message.tokens_output ?? 0} out</span>
                      <span>US$ {message.cost_usd ?? "0"}</span>
                      <span>budget: {message.budget_reservation.status}</span>
                    </div>
                  )}
                </article>
              ))}
              {streamText && <article className="max-w-[92%] rounded-2xl border border-cyan-300/15 bg-black/20 p-4"><p className="mb-2 flex items-center gap-2 text-[10px] text-cyan-200"><LoaderCircle size={12} className="animate-spin" /> Resposta em streaming</p><p className="whitespace-pre-wrap text-sm leading-6">{streamText}</p></article>}
            </div>

            {error && <div className="mx-4 mb-2 flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/[.06] px-3 py-2 text-xs text-rose-200"><AlertTriangle size={13} /> {error}</div>}
            <div className="border-t border-cyan-300/10 p-4">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <select value={mode} onChange={(event) => setMode(event.target.value as DataMode)} className="rounded-lg border border-[var(--border-subtle)] bg-black/20 px-2 py-1.5 text-[10px] text-[var(--text-primary)] outline-none">
                  <option value="FROZEN_ANALYSIS_ONLY">Automático: análise ou ação governada</option>
                  <option value="ALLOW_READONLY_REFRESH" disabled={!flags.readonly_refresh_enabled}>Atualização read-only</option>
                  <option value="CREATE_CHILD_ANALYSIS" disabled={!flags.child_analysis_enabled}>Análise filha</option>
                  <option value="DRAFT_PROPOSAL" disabled={!flags.proposals_enabled || !flags.governed_actions_enabled}>Executar alteração</option>
                </select>
                <span className="flex items-center gap-1 font-mono text-[9px] text-emerald-200"><ShieldCheck size={11} /> {flags.live_config_write_enabled ? "alteração mediante confirmação" : "alteração desativada"} · {flags.budget_enforcement_enabled ? `teto US$ ${flags.provider_max_cost_usd}` : "orçamento somente auditável"}</span>
              </div>
              <div className="flex gap-2">
                <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} rows={2} placeholder={mode === "DRAFT_PROPOSAL" ? "Descreva a alteração desejada, o perfil ou configuração e o valor…" : "Pergunte sobre o diagnóstico, evidências ou limitações…"} className="min-h-14 flex-1 resize-none rounded-xl border border-[var(--border-subtle)] bg-black/20 px-3 py-2 text-sm outline-none placeholder:text-[var(--text-muted)] focus:border-cyan-300/30" />
                <div className="flex flex-col gap-2">
                  <button disabled={busy || !draft.trim()} onClick={() => void sendMessage()} className="grid flex-1 place-items-center rounded-xl bg-cyan-300 px-4 text-slate-950 disabled:opacity-40" aria-label="Enviar"><Send size={16} /></button>
                  <button disabled={!busy} onClick={() => void cancel()} className="grid flex-1 place-items-center rounded-xl border border-rose-300/20 px-4 text-rose-200 disabled:opacity-30" aria-label="Cancelar"><CircleStop size={15} /></button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
