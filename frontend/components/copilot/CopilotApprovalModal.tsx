"use client";

import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import type { CopilotActionPlan } from "@/lib/copilot";
import { APPROVAL_TEXT, formatCell, isApprovalValid } from "@/lib/copilot";

export function CopilotApprovalModal({ plan, busy, onClose, onConfirm }: {
  plan: CopilotActionPlan;
  busy: boolean;
  onClose: () => void;
  onConfirm: (confirmation: string) => Promise<void>;
}) {
  const [confirmation, setConfirmation] = useState("");
  const valid = isApprovalValid(confirmation);
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-labelledby="copilot-approval-title">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-2xl border border-amber-500/40 bg-[var(--bg-surface)] p-5 shadow-2xl">
        <div className="flex items-start justify-between">
          <div><h2 id="copilot-approval-title" className="flex items-center gap-2 font-semibold text-amber-300"><AlertTriangle size={18} /> Aprovação de mudança</h2>
            <p className="mt-1 text-xs text-[var(--text-muted)]">O profile ativo não será alterado. Será criado um candidato shadow versionado.</p></div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Fechar"><X size={18} /></button>
        </div>
        <div className="mt-4 space-y-3 text-xs">
          <div className="rounded-xl bg-[var(--bg-elevated)] p-3"><strong>{plan.objective}</strong><p className="mt-2">Risco: {plan.risk}</p></div>
          <div className="max-h-64 space-y-2 overflow-auto">{plan.changes.map((change) => (
            <div key={change.path} className="rounded-xl border border-[var(--border-subtle)] p-3">
              <div className="font-mono text-cyan-300">{change.path}</div>
              <div className="mt-1 grid grid-cols-2 gap-2 font-mono"><span className="text-rose-400">{formatCell(change.old_value)}</span><span className="text-emerald-400">{formatCell(change.new_value)}</span></div>
            </div>
          ))}</div>
          <pre className="overflow-auto rounded-xl bg-[#080b12] p-3 font-mono text-slate-300">{JSON.stringify(plan.rollback_plan, null, 2)}</pre>
          <label className="block"><span>Digite <strong className="font-mono text-amber-300">{APPROVAL_TEXT}</strong></span>
            <input autoFocus value={confirmation} onChange={(event) => setConfirmation(event.target.value)}
              className="mt-2 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-3 py-2 font-mono outline-none focus:border-amber-400" />
          </label>
          <button type="button" disabled={!valid || busy} onClick={() => onConfirm(confirmation)}
            className="w-full rounded-xl bg-amber-400 px-4 py-3 font-semibold text-black disabled:cursor-not-allowed disabled:opacity-40">
            {busy ? "Revalidando e executando" : "Aprovar e criar candidato shadow"}
          </button>
        </div>
      </div>
    </div>
  );
}
