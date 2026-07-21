"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, RotateCcw, ShieldCheck } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

type IndicatorEvidence = {
  id: string; indicator: string; bucket_label: string; total_cases: number;
  wins?: number; losses?: number; role_detected?: string | null;
  validation_status?: string | null; actionability_status?: string | null;
  evidence_json?: Record<string, unknown>;
  associated_profiles?: Array<{ id: string; name: string }>;
};
type EligibleProfile = { id: string; name: string; profile_version_id: string; version_number: number; config_hash: string };
type ManualRecord = {
  id: string; state: string; preview_hash?: string | null; diff?: unknown;
  autopilot_applied?: boolean; ml_training_mutated?: boolean;
  historical_dataset_mutated?: boolean;
};

const ACTIONS = [
  "ADD_SIGNAL_CONDITION", "UPDATE_SIGNAL_THRESHOLD", "UPDATE_SIGNAL_RANGE", "REMOVE_SIGNAL_CONDITION",
  "ADD_SCORE_BONUS", "ADD_SCORE_PENALTY", "UPDATE_SCORE_WEIGHT", "UPDATE_SCORE_THRESHOLD",
  "ADD_BLOCK_RULE", "UPDATE_BLOCK_RULE", "REMOVE_BLOCK_RULE", "OBSERVE_ONLY",
] as const;

function defaultDraft(stat: IndicatorEvidence, action: string) {
  const stableId = `pi-manual-${stat.id}`;
  if (action === "ADD_SCORE_PENALTY" || action === "ADD_SCORE_BONUS") return {
    path: "/scoring/generated_rules", current: "null",
    proposed: JSON.stringify({ rule_id: stableId, indicator: stat.indicator, operator: "==", value: stat.bucket_label, score: action === "ADD_SCORE_PENALTY" ? -10 : 10 }, null, 2),
  };
  if (action === "ADD_SIGNAL_CONDITION") return {
    path: "/signals/conditions", current: "null",
    proposed: JSON.stringify({ condition_id: stableId, field: stat.indicator, operator: "==", value: stat.bucket_label, required: false }, null, 2),
  };
  if (action === "ADD_BLOCK_RULE") return {
    path: "/block_rules/blocks", current: "null",
    proposed: JSON.stringify({ rule_id: stableId, indicator: stat.indicator, operator: "==", value: stat.bucket_label }, null, 2),
  };
  return { path: "", current: "null", proposed: "null" };
}

function parseJson(value: string, label: string) {
  try { return JSON.parse(value); } catch { throw new Error(`${label} precisa ser JSON válido.`); }
}

export default function ManualAdjustmentPanel({ stat, onClose }: { stat: IndicatorEvidence; onClose: () => void }) {
  const defaultAction = stat.role_detected === "losing_indicator" ? "ADD_SCORE_PENALTY" : "ADD_SIGNAL_CONDITION";
  const [action, setAction] = useState(defaultAction);
  const initial = useMemo(() => defaultDraft(stat, action), [stat, action]);
  const [path, setPath] = useState(initial.path);
  const [currentValue, setCurrentValue] = useState(initial.current);
  const [proposedValue, setProposedValue] = useState(initial.proposed);
  const [profiles, setProfiles] = useState<EligibleProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [record, setRecord] = useState<ManualRecord | null>(null);
  const [justification, setJustification] = useState("");
  const [riskConfirmed, setRiskConfirmed] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [idempotencyKey] = useState(() => `pi-manual:${stat.id}:${crypto.randomUUID()}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const warnings = useMemo(() => {
    const values: Array<Record<string, unknown>> = [];
    if (stat.validation_status && stat.validation_status !== "validated") values.push({ code: stat.validation_status, source: "validation_status" });
    if (stat.actionability_status && stat.actionability_status !== "validated") values.push({ code: stat.actionability_status, source: "actionability_status" });
    if (!stat.evidence_json?.ai_review) values.push({ code: "AI_REVIEW_MISSING", source: "evidence" });
    return values;
  }, [stat]);

  useEffect(() => {
    apiGet<{ items: EligibleProfile[] }>("/profile-intelligence/manual-adjustments/eligible-profiles")
      .then(result => {
        const associated = new Set((stat.associated_profiles || []).map(profile => profile.id));
        const ordered = [...(result.items || [])].sort((a, b) => Number(associated.has(b.id)) - Number(associated.has(a.id)));
        setProfiles(ordered); setProfileId(ordered[0]?.id || "");
      })
      .catch(value => setError(value instanceof Error ? value.message : String(value)));
  }, [stat]);

  const changeAction = (nextAction: string) => {
    const next = defaultDraft(stat, nextAction);
    setAction(nextAction); setPath(next.path);
    setCurrentValue(next.current); setProposedValue(next.proposed);
  };

  const execute = async (fn: () => Promise<ManualRecord>) => {
    setBusy(true); setError(null);
    try { setRecord(await fn()); } catch (value) { setError(value instanceof Error ? value.message : String(value)); }
    finally { setBusy(false); }
  };
  const createDraft = () => execute(() => apiPost<ManualRecord>("/profile-intelligence/manual-adjustments", {
    profile_id: profileId, action_type: action, target_path: action === "OBSERVE_ONLY" ? null : path,
    current_value: action === "OBSERVE_ONLY" ? null : parseJson(currentValue, "Valor atual"),
    proposed_value: action === "OBSERVE_ONLY" ? null : parseJson(proposedValue, "Valor proposto"),
    run_id: typeof stat.evidence_json?.run_id === "string" ? stat.evidence_json.run_id : null, indicator_stat_id: stat.id,
    evidence_json: { indicator: stat.indicator, bucket: stat.bucket_label, cases: stat.total_cases, wins: stat.wins, losses: stat.losses, source: stat.evidence_json || {} },
    statistical_warnings: warnings,
    idempotency_key: idempotencyKey,
  }));
  const preview = () => execute(() => apiPost<ManualRecord>(`/profile-intelligence/manual-adjustments/${record?.id}/preview`, {}));
  const apply = () => execute(() => apiPost<ManualRecord>(`/profile-intelligence/manual-adjustments/${record?.id}/approve-and-apply`, { preview_hash: record?.preview_hash, justification, confirm_risk: riskConfirmed }));
  const reject = () => execute(() => apiPost<ManualRecord>(`/profile-intelligence/manual-adjustments/${record?.id}/reject`, { reason: justification }));
  const rollback = () => execute(() => apiPost<ManualRecord>(`/profile-intelligence/manual-adjustments/${record?.id}/rollback`, { reason: rollbackReason }));

  return <div className="space-y-4 text-[11px]">
    <div className="rounded border border-cyan-400/20 bg-cyan-400/5 p-3">
      <div className="font-mono text-[13px] font-semibold text-cyan-100">{stat.indicator} · {stat.bucket_label}</div>
      <div className="mt-1 text-cyan-100/70">Discovery/validation: {stat.total_cases} casos · W/L/T {stat.wins ?? 0}/{stat.losses ?? 0}/{Math.max(stat.total_cases - (stat.wins ?? 0) - (stat.losses ?? 0), 0)}</div>
    </div>
    {warnings.length > 0 && <div className="rounded border border-yellow-500/30 bg-yellow-500/5 p-3 text-yellow-200">
      <div className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4" /> Avisos estatísticos — não executam nem bloqueiam o operador</div>
      <div className="mt-2 space-y-1 font-mono text-[10px]">{warnings.map((warning, index) => <div key={`${warning.code}-${index}`}>{String(warning.code)}</div>)}</div>
      <div className="mt-2">A aplicação exige justificativa e confirmação explícita de risco.</div>
    </div>}
    {!record && <>
      <label className="block"><span className="mb-1 block text-[10px] uppercase text-[var(--text-tertiary)]">Profile L3 existente</span><select className="input w-full" value={profileId} onChange={event => setProfileId(event.target.value)}>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · v{profile.version_number}</option>)}</select></label>
      <label className="block"><span className="mb-1 block text-[10px] uppercase text-[var(--text-tertiary)]">Ação manual</span><select className="input w-full" value={action} onChange={event => changeAction(event.target.value)}>{ACTIONS.map(value => <option key={value}>{value}</option>)}</select></label>
      {action !== "OBSERVE_ONLY" && <>
        <label className="block"><span className="mb-1 block text-[10px] uppercase text-[var(--text-tertiary)]">Path estável (sem índices)</span><input className="input w-full font-mono" value={path} onChange={event => setPath(event.target.value)} placeholder="/signals/conditions/by_id/.../value" /></label>
        <div className="grid gap-3 md:grid-cols-2">
          <label><span className="mb-1 block text-[10px] uppercase text-[var(--text-tertiary)]">Valor atual (JSON)</span><textarea className="input min-h-28 w-full font-mono" value={currentValue} onChange={event => setCurrentValue(event.target.value)} /></label>
          <label><span className="mb-1 block text-[10px] uppercase text-[var(--text-tertiary)]">Valor proposto (JSON)</span><textarea className="input min-h-28 w-full font-mono" value={proposedValue} onChange={event => setProposedValue(event.target.value)} /></label>
        </div>
      </>}
    </>}
    {record && <div className="space-y-3 rounded border border-[var(--border-default)] p-3">
      <div className="flex items-center justify-between"><span className="font-semibold">Estado</span><span className="font-mono text-cyan-300">{record.state}</span></div>
      {record.preview_hash && <div className="break-all font-mono text-[9px] text-[var(--text-tertiary)]">preview {record.preview_hash}</div>}
      {record.diff !== undefined && record.diff !== null && <pre className="max-h-52 overflow-auto rounded bg-black/30 p-2 text-[9px] text-cyan-100">{JSON.stringify(record.diff, null, 2)}</pre>}
      <div className="grid grid-cols-3 gap-2 text-[9px]">{[["Auto-Pilot", record.autopilot_applied], ["Treino ML", record.ml_training_mutated], ["Histórico", record.historical_dataset_mutated]].map(([label, value]) => <div key={String(label)} className="rounded border border-green-500/20 p-2 text-center text-green-300">{String(label)}: {value ? "ALTERADO" : "intacto"}</div>)}</div>
    </div>}
    {record?.state === "PENDING_MANUAL_APPROVAL" && <div className="space-y-2"><textarea className="input min-h-20 w-full" value={justification} onChange={event => setJustification(event.target.value)} placeholder="Justificativa operacional obrigatória (mínimo 10 caracteres)" /><label className="flex items-start gap-2"><input type="checkbox" checked={riskConfirmed} onChange={event => setRiskConfirmed(event.target.checked)} /><span>Confirmo o risco e aprovo somente o diff exibido neste preview imutável.</span></label></div>}
    {record?.state === "APPLIED" && <textarea className="input min-h-20 w-full" value={rollbackReason} onChange={event => setRollbackReason(event.target.value)} placeholder="Motivo do rollback manual (mínimo 10 caracteres)" />}
    {error && <div className="rounded border border-red-500/30 bg-red-500/5 p-2 text-red-300">{error}</div>}
    <div className="flex gap-2 border-t border-[var(--border-subtle)] pt-3">
      <button className="btn btn-secondary flex-1" onClick={onClose}>Fechar</button>
      {!record && <button className="btn btn-primary flex-1" disabled={busy || !profileId} onClick={createDraft}><ShieldCheck className="h-4 w-4" /> Criar rascunho</button>}
      {record?.state === "MANUAL_DRAFT" && <button className="btn btn-primary flex-1" disabled={busy} onClick={preview}>Gerar preview imutável</button>}
      {record?.state === "PENDING_MANUAL_APPROVAL" && <button className="btn btn-primary flex-1" disabled={busy || !riskConfirmed || justification.length < 10} onClick={apply}><CheckCircle className="h-4 w-4" /> Aprovar e aplicar</button>}
      {record?.state === "PENDING_MANUAL_APPROVAL" && <button className="btn btn-secondary" disabled={busy || justification.length < 10} onClick={reject}>Rejeitar</button>}
      {record?.state === "APPLIED" && <button className="btn btn-secondary flex-1" disabled={busy || rollbackReason.length < 10} onClick={rollback}><RotateCcw className="h-4 w-4" /> Rollback manual</button>}
    </div>
  </div>;
}
