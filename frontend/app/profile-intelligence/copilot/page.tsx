"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bot, Send, User } from "lucide-react";

import { apiFetch, apiGet, apiPost } from "@/lib/api";
import type { CopilotActionPlan, CopilotQuery, CopilotSkill, SchemaTable } from "@/lib/copilot";
import { ActionPlanPanel, ContextPanel, EvidencePanel, SchemaMap, SqlViewer } from "@/components/copilot/CopilotPanels";
import { CopilotApprovalModal } from "@/components/copilot/CopilotApprovalModal";
import { CopilotSkillLibrary } from "@/components/copilot/CopilotSkillLibrary";

interface ChatMessage { role: "user" | "assistant"; content: string }
interface ChatResponse {
  session_id: string;
  answer: string;
  queries: CopilotQuery[];
  evidence: unknown[];
  action_plan: CopilotActionPlan | null;
  skills_used: CopilotSkill[];
}
export default function CopilotPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([{
    role: "assistant",
    content: "Co-Pilot pronto. Posso investigar dados reais, mapear relações e preparar mudanças em DRY_RUN. Nenhuma escrita ocorre sem confirmação explícita.",
  }]);
  const [input, setInput] = useState("");
  const [provider, setProvider] = useState<"anthropic" | "openai">("anthropic");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [queries, setQueries] = useState<CopilotQuery[]>([]);
  const [evidence, setEvidence] = useState<unknown[]>([]);
  const [actionPlan, setActionPlan] = useState<CopilotActionPlan | null>(null);
  const [skills, setSkills] = useState<CopilotSkill[]>([]);
  const [tables, setTables] = useState<SchemaTable[]>([]);
  const [context, setContext] = useState<Record<string, unknown>>({ environment: "backend", lookback_days: 7 });
  const [sqlDraft, setSqlDraft] = useState("SELECT id, status, run_at, lookback_days, total_profiles, total_closed_trades, base_win_rate\nFROM profile_intelligence_runs\nORDER BY run_at DESC\nLIMIT 20");
  const [loading, setLoading] = useState(false);
  const [sqlRunning, setSqlRunning] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      apiGet<{ tables: SchemaTable[] }>("/copilot/schema-map").catch(() => ({ tables: [] })),
      apiGet<CopilotSkill[]>("/copilot/skills").catch(() => []),
      apiGet<Record<string, unknown>>("/profile-intelligence/overview").catch(() => ({})),
    ]).then(([schema, skillRows, overview]) => {
      if (!active) return;
      setTables(schema.tables);
      setSkills(skillRows);
      setContext((current) => ({ ...current, ...overview }));
    });
    return () => { active = false; };
  }, []);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || loading) return;
    setInput("");
    setError(null);
    setMessages((current) => [...current, { role: "user", content: message }]);
    setLoading(true);
    try {
      const response = await apiPost<ChatResponse>("/copilot/chat", {
        message,
        session_id: sessionId,
        provider,
        context: { screen: "profile_intelligence_copilot", lookback_days: Number(context.lookback_days) || 7 },
      });
      setSessionId(response.session_id);
      setMessages((current) => [...current, { role: "assistant", content: response.answer }]);
      setQueries((current) => [...response.queries, ...current]);
      setEvidence(response.evidence);
      if (response.action_plan) setActionPlan(response.action_plan);
      if (response.skills_used.length) {
        const fresh = await apiGet<CopilotSkill[]>("/copilot/skills");
        setSkills(fresh);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Falha ao consultar o Co-Pilot");
    } finally {
      setLoading(false);
    }
  }

  async function runSql() {
    setSqlRunning(true);
    setError(null);
    try {
      const result = await apiPost<CopilotQuery>("/copilot/query", {
        sql: sqlDraft, params: {}, reason: "SQL Analyst UI", session_id: sessionId,
      });
      setQueries((current) => [result, ...current]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Query bloqueada");
    } finally {
      setSqlRunning(false);
    }
  }

  async function approveAndExecute(confirmation: string) {
    if (!actionPlan) return;
    setApprovalBusy(true);
    setError(null);
    try {
      await apiPost(`/copilot/actions/${actionPlan.id}/approve`, { confirmation_text: confirmation });
      const executed = await apiPost<CopilotActionPlan>(`/copilot/actions/${actionPlan.id}/execute`, {});
      setActionPlan(executed);
      setApprovalOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Execução bloqueada");
    } finally {
      setApprovalBusy(false);
    }
  }

  async function toggleSkill(skill: CopilotSkill) {
    await apiFetch(`/copilot/skills/${skill.id}`, {
      method: "PATCH", body: JSON.stringify({ status: skill.status === "ACTIVE" ? "INACTIVE" : "ACTIVE" }),
    });
    setSkills(await apiGet<CopilotSkill[]>("/copilot/skills"));
  }

  async function approveSkill(skill: CopilotSkill) {
    await apiPost(`/copilot/skills/${skill.id}/approve`, {});
    setSkills(await apiGet<CopilotSkill[]>("/copilot/skills"));
  }

  return (
    <main className="min-h-screen bg-[var(--bg-primary)] p-4 text-[var(--text-primary)] md:p-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href="/profile-intelligence" className="rounded-lg border border-[var(--border-subtle)] p-2" aria-label="Voltar"><ArrowLeft size={18} /></Link>
          <div><h1 className="flex items-center gap-2 text-xl font-semibold"><Bot className="text-cyan-400" /> Co-Pilot</h1>
            <p className="text-xs text-[var(--text-muted)]">Profile Intelligence · operador técnico assistido</p></div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="rounded-full bg-emerald-500/15 px-3 py-1 text-emerald-400">Leitura ampla</span>
          <span className="rounded-full bg-amber-500/15 px-3 py-1 text-amber-400">Escrita com aprovação</span>
          <select value={provider} onChange={(event) => setProvider(event.target.value as "anthropic" | "openai")}
            className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 py-1">
            <option value="anthropic">Anthropic</option><option value="openai">OpenAI</option>
          </select>
        </div>
      </header>

      {error && <div className="mb-4 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{error}</div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.7fr)]">
        <div className="space-y-4">
          <section className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
            <div className="max-h-[38rem] min-h-[26rem] space-y-4 overflow-auto p-4">
              {messages.map((message, index) => (
                <div key={index} className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                  {message.role === "assistant" && <div className="mt-1 rounded-full bg-cyan-500/15 p-2 text-cyan-400"><Bot size={16} /></div>}
                  <div className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === "user" ? "bg-cyan-500 text-black" : "bg-[var(--bg-elevated)]"}`}>{message.content}</div>
                  {message.role === "user" && <div className="mt-1 rounded-full bg-cyan-500/15 p-2 text-cyan-400"><User size={16} /></div>}
                </div>
              ))}
              {loading && <div className="text-sm text-[var(--text-muted)]">Consultando skills, ferramentas e banco…</div>}
            </div>
            <form onSubmit={sendMessage} className="flex gap-2 border-t border-[var(--border-subtle)] p-3">
              <textarea value={input} onChange={(event) => setInput(event.target.value)} rows={2}
                onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }}
                className="flex-1 resize-none rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3 py-2 text-sm outline-none focus:border-cyan-500"
                placeholder="Analise os profiles L3 nos últimos 7 dias…" />
              <button type="submit" disabled={loading || !input.trim()} className="rounded-xl bg-cyan-400 px-4 text-black disabled:opacity-40" aria-label="Enviar"><Send size={18} /></button>
            </form>
          </section>
          <SqlViewer queries={queries} draft={sqlDraft} onDraft={setSqlDraft} onRun={runSql} running={sqlRunning} />
          <EvidencePanel evidence={evidence} />
        </div>
        <aside className="space-y-4">
          <ContextPanel context={context} />
          <ActionPlanPanel plan={actionPlan} onApprove={() => setApprovalOpen(true)} />
          <CopilotSkillLibrary skills={skills} onToggle={toggleSkill} onApprove={approveSkill} />
          <SchemaMap tables={tables} />
        </aside>
      </div>
      {approvalOpen && actionPlan && <CopilotApprovalModal plan={actionPlan} busy={approvalBusy} onClose={() => setApprovalOpen(false)} onConfirm={approveAndExecute} />}
    </main>
  );
}
