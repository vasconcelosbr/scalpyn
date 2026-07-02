"use client";

import { Copy, Database, Play, ShieldCheck } from "lucide-react";
import type { CopilotActionPlan, CopilotQuery, SchemaTable } from "@/lib/copilot";
import { formatCell } from "@/lib/copilot";

const panel = "rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4";

export function ContextPanel({ context }: { context: Record<string, unknown> }) {
  return (
    <section className={panel}>
      <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Contexto operacional</h2>
      <dl className="grid grid-cols-2 gap-3 text-xs">
        {Object.entries(context).map(([key, value]) => (
          <div key={key} className="rounded-xl bg-[var(--bg-elevated)] p-3">
            <dt className="text-[var(--text-muted)]">{key.replaceAll("_", " ")}</dt>
            <dd className="mt-1 break-words font-mono text-[var(--text-primary)]">{formatCell(value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
export function SqlViewer({ queries, draft, onDraft, onRun, running }: {
  queries: CopilotQuery[];
  draft: string;
  onDraft: (value: string) => void;
  onRun: () => void;
  running: boolean;
}) {
  return (
    <section className={panel}>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><Database size={16} /> SQL Viewer</h2>
        <span className="text-xs text-[var(--text-muted)]">READ_ONLY · auditado</span>
      </div>
      <textarea
        value={draft}
        onChange={(event) => onDraft(event.target.value)}
        className="min-h-28 w-full rounded-xl border border-[var(--border-subtle)] bg-[#080b12] p-3 font-mono text-xs text-emerald-300 outline-none focus:border-emerald-500"
        placeholder="SELECT ..."
      />
      <button type="button" onClick={onRun} disabled={running || !draft.trim()}
        className="mt-2 inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-3 py-2 text-xs font-semibold text-black disabled:opacity-40">
        <Play size={14} /> {running ? "Executando" : "Executar leitura"}
      </button>
      <div className="mt-4 space-y-4">
        {queries.map((query, index) => (
          <article key={query.id ?? `${query.query_hash}-${index}`} className="overflow-hidden rounded-xl border border-[var(--border-subtle)]">
            <div className="flex items-center justify-between bg-[var(--bg-elevated)] px-3 py-2 text-xs">
              <span className="font-mono text-emerald-400">{query.classification}</span>
              <span className="text-[var(--text-muted)]">{query.execution_ms} ms · {query.rows_returned} linhas{query.truncated ? " · truncado" : ""}</span>
            </div>
            <div className="relative bg-[#080b12] p-3">
              <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-slate-300">{query.query}</pre>
              <button type="button" onClick={() => navigator.clipboard.writeText(query.query)} aria-label="Copiar SQL"
                className="absolute right-2 top-2 rounded p-1 text-slate-400 hover:text-white"><Copy size={14} /></button>
            </div>
            {query.rows.length > 0 && (
              <div className="max-h-72 overflow-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-[var(--bg-elevated)]">
                    <tr>{query.columns.map((column) => <th key={column} className="px-3 py-2 font-medium">{column}</th>)}</tr>
                  </thead>
                  <tbody>{query.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-t border-[var(--border-subtle)]">
                      {query.columns.map((column) => <td key={column} className="max-w-64 truncate px-3 py-2 font-mono">{formatCell(row[column])}</td>)}
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

export function EvidencePanel({ evidence }: { evidence: unknown[] }) {
  return (
    <section className={panel}>
      <h2 className="mb-3 text-sm font-semibold">Evidências</h2>
      {evidence.length === 0 ? <p className="text-xs text-[var(--text-muted)]">As evidências das ferramentas aparecerão aqui.</p> : (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-xl bg-[#080b12] p-3 font-mono text-xs text-slate-300">
          {JSON.stringify(evidence, null, 2)}
        </pre>
      )}
    </section>
  );
}

export function ActionPlanPanel({ plan, onApprove }: { plan: CopilotActionPlan | null; onApprove: () => void }) {
  return (
    <section className={panel}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><ShieldCheck size={16} /> Action Plan</h2>
      {!plan ? <p className="text-xs text-[var(--text-muted)]">Nenhuma alteração proposta.</p> : (
        <div className="space-y-3 text-xs">
          <div className="flex items-center justify-between"><strong>{plan.objective}</strong><span className="rounded-full bg-amber-500/15 px-2 py-1 font-mono text-amber-400">{plan.status}</span></div>
          <div className="space-y-2">{plan.changes.map((change) => (
            <div key={change.path} className="rounded-xl bg-[var(--bg-elevated)] p-3">
              <div className="font-mono text-cyan-400">{change.path}</div>
              <div className="mt-1 grid grid-cols-2 gap-2 font-mono"><span className="text-rose-400">− {formatCell(change.old_value)}</span><span className="text-emerald-400">+ {formatCell(change.new_value)}</span></div>
              <p className="mt-2 text-[var(--text-muted)]">{change.reason}</p>
            </div>
          ))}</div>
          <p><span className="text-[var(--text-muted)]">Risco:</span> {plan.risk}</p>
          <p><span className="text-[var(--text-muted)]">Rollback:</span> {formatCell(plan.rollback_plan)}</p>
          {plan.status === "DRY_RUN" && <button type="button" onClick={onApprove}
            className="rounded-lg bg-amber-400 px-3 py-2 font-semibold text-black">Revisar e aprovar</button>}
          {plan.execution_result && <pre className="rounded-xl bg-[#080b12] p-3 font-mono text-emerald-300">{JSON.stringify(plan.execution_result, null, 2)}</pre>}
        </div>
      )}
    </section>
  );
}

export function SchemaMap({ tables }: { tables: SchemaTable[] }) {
  return (
    <section className={panel}>
      <h2 className="mb-3 text-sm font-semibold">Database Relationship Analyzer</h2>
      <div className="max-h-[32rem] space-y-2 overflow-auto">
        {tables.map((table) => (
          <details key={table.name} className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 text-xs">
            <summary className="cursor-pointer font-mono text-cyan-300">{table.name} <span className="text-[var(--text-muted)]">PK {table.primary_key ?? "—"}</span></summary>
            <p className="mt-2 text-[var(--text-muted)]">{table.important_columns.join(" · ") || "Sem chaves reconhecidas"}</p>
            {table.relationships.map((relationship, index) => (
              <div key={`${relationship.source_column}-${relationship.target_table}-${index}`} className="mt-2 font-mono">
                {relationship.source_column} → {relationship.target_table}.{relationship.target_column} <span className="text-amber-400">[{relationship.type}]</span>
              </div>
            ))}
          </details>
        ))}
      </div>
    </section>
  );
}
