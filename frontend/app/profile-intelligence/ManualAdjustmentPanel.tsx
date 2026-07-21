"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle, RotateCcw, ShieldCheck } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

type IndicatorEvidence = {
  id: string; indicator: string; bucket_label: string; total_cases: number;
  wins?: number; losses?: number; role_detected?: string | null;
  range_min?: number | null; range_max?: number | null; value_text?: string | null;
  validation_status?: string | null; actionability_status?: string | null;
  evidence_json?: Record<string, unknown>;
  associated_profiles?: Array<{ id: string; name: string }>;
};
type EligibleProfile = { id: string; name: string; profile_version_id: string; version_number: number; config_hash: string };
type ManualRecord = {
  id: string; state: string; preview_hash?: string | null; diff?: unknown;
  autopilot_applied?: boolean; ml_training_mutated?: boolean;
  historical_dataset_mutated?: boolean;
  runtime_status?: string; runtime_target_profile_version_id?: string | null;
  runtime_target_score_engine_version_id?: string | null;
  runtime_confirmed_at?: string | null; runtime_confirmation_source?: string | null;
};
type WindowEvidence = { start?: string; end?: string };
type ValidationEvidence = {
  cases?: number; wins?: number; losses?: number; timeouts?: number;
  scope_cases?: number; scope_wins?: number; scope_losses?: number; scope_timeouts?: number;
  failed_checks?: string[]; checks?: Record<string, boolean>;
};

const ACTIONS = [
  "ADD_SIGNAL_CONDITION", "UPDATE_SIGNAL_THRESHOLD", "UPDATE_SIGNAL_RANGE", "REMOVE_SIGNAL_CONDITION",
  "ADD_SCORE_BONUS", "ADD_SCORE_PENALTY", "UPDATE_SCORE_WEIGHT", "UPDATE_SCORE_THRESHOLD",
  "ADD_BLOCK_RULE", "UPDATE_BLOCK_RULE", "REMOVE_BLOCK_RULE", "OBSERVE_ONLY",
] as const;

function defaultDraft(stat: IndicatorEvidence, action: string) {
  const stableId = `pi-manual-${stat.id}`;
  const condition = stat.range_min != null && stat.range_max != null
    ? { operator: "between", min: stat.range_min, max: stat.range_max }
    : stat.range_min != null
      ? { operator: ">=", value: stat.range_min }
      : stat.range_max != null
        ? { operator: "<", value: stat.range_max }
        : { operator: "==", value: stat.value_text ?? stat.bucket_label };
  if (action === "ADD_SCORE_PENALTY" || action === "ADD_SCORE_BONUS") return {
    path: "/scoring/generated_rules", current: "null",
    proposed: JSON.stringify({ rule_id: stableId, indicator: stat.indicator, ...condition, points: action === "ADD_SCORE_PENALTY" ? -10 : 10 }, null, 2),
  };
  if (action === "ADD_SIGNAL_CONDITION") return {
    path: "/signals/conditions", current: "null",
    proposed: JSON.stringify({ condition_id: stableId, field: stat.indicator, ...condition, required: false }, null, 2),
  };
  if (action === "ADD_BLOCK_RULE") return {
    path: "/block_rules/blocks", current: "null",
    // BlockEngine's flat threshold form means "minimum requirement" and
    // blocks when the condition fails.  Manual PI evidence describes the
    // condition that must trigger the block, so use the grouped/direct form.
    proposed: JSON.stringify({
      rule_id: stableId,
      id: stableId,
      name: `Bloqueio manual: ${stat.indicator}`,
      logic: "AND",
      conditions: [{ id: `${stableId}-condition`, indicator: stat.indicator, ...condition }],
    }, null, 2),
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
  const sourceEvidence = (stat.evidence_json || {}) as Record<string, unknown>;
  const discoveryWindow = (sourceEvidence.discovery_window || {}) as WindowEvidence;
  const validationWindow = (sourceEvidence.validation_window || {}) as WindowEvidence;
  const validation = (sourceEvidence.validation || {}) as ValidationEvidence;
  const failedChecks = Array.isArray(validation.failed_checks) ? validation.failed_checks : [];
  const discoveryTimeouts = Math.max(stat.total_cases - (stat.wins ?? 0) - (stat.losses ?? 0), 0);
  const validationCases = validation.cases ?? 0;
  const validationTimeouts = validation.timeouts ?? Math.max(validationCases - (validation.wins ?? 0) - (validation.losses ?? 0), 0);
  const requestedWindow = (sourceEvidence.requested_window || sourceEvidence.requested_period || {}) as WindowEvidence;
  const formatWindow = (window: WindowEvidence) => window.start && window.end ? `${window.start} → ${window.end}` : "não informado pelo run";
  const validationClass = stat.validation_status === "validated"
    ? "VALIDATED"
    : failedChecks.some(value => value.includes("support") || value.includes("distinct") || value.includes("share"))
      ? "INSUFFICIENT_SAMPLE"
      : failedChecks.some(value => value.includes("directional") || value.includes("lift") || value.includes("winrate"))
        ? "DIRECTION_NOT_CONFIRMED"
        : "VALIDATION_FAILED";

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
      <div className="mt-1 text-cyan-100/70">Hipótese de discovery: {stat.role_detected || "não classificada"}</div>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        <div className="rounded border border-cyan-400/10 p-2"><div className="font-semibold">Discovery · hipótese</div><div>Efetivo: {formatWindow(discoveryWindow)}</div><div>W/L/T {stat.wins ?? 0}/{stat.losses ?? 0}/{discoveryTimeouts} · total {stat.total_cases}</div></div>
        <div className="rounded border border-cyan-400/10 p-2"><div className="font-semibold">Validation · {validationClass}</div><div>Efetivo: {formatWindow(validationWindow)}</div><div>W/L/T {validation.wins ?? 0}/{validation.losses ?? 0}/{validationTimeouts} · total {validationCases}</div></div>
      </div>
      <div className="mt-2 text-[10px] text-cyan-100/60">Período solicitado: {formatWindow(requestedWindow)}</div>
      <div className="mt-1 text-[10px] text-cyan-100/60">Baseline comparável: source={String(sourceEvidence.source || "—")} · profile={String(sourceEvidence.profile_id || "source-level")} · mesma janela de validation · W/L/T {validation.scope_wins ?? 0}/{validation.scope_losses ?? 0}/{validation.scope_timeouts ?? 0} · total {validation.scope_cases ?? 0}</div>
    </div>
    {failedChecks.length > 0 && <div className="rounded border border-red-500/30 bg-red-500/5 p-3 text-red-200"><div className="font-semibold">Todos os failed_checks</div><div className="mt-2 space-y-1 font-mono text-[10px]">{failedChecks.map(check => <div key={check}>{check}: {validation.checks?.[check] === false ? "FAILED" : "não confirmado"}</div>)}</div></div>}
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
      <div className="flex items-center justify-between"><span className="font-semibold">Runtime</span><span className="font-mono text-cyan-300">{record.runtime_status || "NOT_APPLICABLE"}</span></div>
      {record.runtime_target_profile_version_id && <div className="break-all font-mono text-[9px] text-[var(--text-tertiary)]">profile_version_id {record.runtime_target_profile_version_id}</div>}
      {record.runtime_target_score_engine_version_id && <div className="break-all font-mono text-[9px] text-[var(--text-tertiary)]">score_engine_version_id {record.runtime_target_score_engine_version_id}</div>}
      {record.runtime_confirmed_at && <div className="font-mono text-[9px] text-green-300">Confirmado em {record.runtime_confirmed_at} por {record.runtime_confirmation_source}</div>}
      {record.preview_hash && <div className="break-all font-mono text-[9px] text-[var(--text-tertiary)]">preview {record.preview_hash}</div>}
      {record.diff !== undefined && record.diff !== null && <pre className="max-h-52 overflow-auto rounded bg-black/30 p-2 text-[9px] text-cyan-100">{JSON.stringify(record.diff, null, 2)}</pre>}
      <div className="grid grid-cols-3 gap-2 text-[9px]">{[["Auto-Pilot", record.autopilot_applied], ["Treino ML", record.ml_training_mutated], ["Histórico", record.historical_dataset_mutated]].map(([label, value]) => <div key={String(label)} className="rounded border border-green-500/20 p-2 text-center text-green-300">{String(label)}: {value ? "ALTERADO" : "intacto"}</div>)}</div>
    </div>}
    {record?.state === "PENDING_MANUAL_APPROVAL" && <div className="space-y-2"><textarea className="input min-h-20 w-full" value={justification} onChange={event => setJustification(event.target.value)} placeholder="Justificativa operacional obrigatória (mínimo 10 caracteres)" /><label className="flex items-start gap-2"><input type="checkbox" checked={riskConfirmed} onChange={event => setRiskConfirmed(event.target.checked)} /><span>Confirmo o risco e aprovo somente o diff exibido neste preview imutável.</span></label></div>}
    {record?.state === "APPLIED" && record.runtime_status === "RUNTIME_CONFIRMED" && <textarea className="input min-h-20 w-full" value={rollbackReason} onChange={event => setRollbackReason(event.target.value)} placeholder="Motivo do rollback manual (mínimo 10 caracteres)" />}
    {error && <div className="rounded border border-red-500/30 bg-red-500/5 p-2 text-red-300">{error}</div>}
    <div className="flex gap-2 border-t border-[var(--border-subtle)] pt-3">
      <button className="btn btn-secondary flex-1" onClick={onClose}>Fechar</button>
      {!record && <button className="btn btn-primary flex-1" disabled={busy || !profileId} onClick={createDraft}><ShieldCheck className="h-4 w-4" /> Criar rascunho</button>}
      {record?.state === "MANUAL_DRAFT" && <button className="btn btn-primary flex-1" disabled={busy} onClick={preview}>Gerar preview imutável</button>}
      {record?.state === "PENDING_MANUAL_APPROVAL" && <button className="btn btn-primary flex-1" disabled={busy || !riskConfirmed || justification.length < 10} onClick={apply}><CheckCircle className="h-4 w-4" /> Aprovar e aplicar</button>}
      {record?.state === "PENDING_MANUAL_APPROVAL" && <button className="btn btn-secondary" disabled={busy || justification.length < 10} onClick={reject}>Rejeitar</button>}
      {record?.state === "APPLIED" && record.runtime_status === "RUNTIME_CONFIRMED" && <button className="btn btn-secondary flex-1" disabled={busy || rollbackReason.length < 10} onClick={rollback}><RotateCcw className="h-4 w-4" /> Rollback manual</button>}
    </div>
  </div>;
}
