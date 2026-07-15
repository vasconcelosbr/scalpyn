"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { buildModelDatasetAudit, type AuditWindow } from "@/lib/mlModelAudit";
import { Brain, CheckCircle, Archive, ChevronDown, ChevronRight, ShieldCheck } from "lucide-react";

interface MetricsBlock {
  precision: number | null;
  recall: number | null;
  fpr: number | null;
  f1: number | null;
  roc_auc: number | null;
  samples: number | null;
  weighted_roc_auc?: number | null;
  weighted_brier?: number | null;
  effective_snapshots?: number | null;
}

interface IntelligenceFinding {
  indicator: string;
  action: "PRIORITIZE" | "BLOCK_CANDIDATE" | "OBSERVE";
  bucket: { lower_exclusive: number | null; upper_inclusive: number | null };
  validation: { lift: number; effective_cases: number };
  test: { lift: number; effective_cases: number; positive_rate: number; net_return_pct: number };
}

interface MetricsJson {
  label_version: string | null;
  target_window_seconds: number | null;
  validation: MetricsBlock | null;
  test: MetricsBlock | null;
  intelligence_gate?: {
    status: "APPROVED" | "REJECTED" | "BLOCKED";
    reasons: string[];
    execution_authority: false;
  } | null;
  promotion_gate?: { status: string; reasons: string[] } | null;
  indicator_intelligence?: {
    scope: string;
    execution_authority: false;
    findings: IntelligenceFinding[];
  } | null;
}

interface MlModel {
  id: string;
  version: number;
  status: "active" | "candidate" | "retired";
  model_lane?: string | null;
  hyperparams: Record<string, unknown> | null;
  train_samples: number | null;
  val_samples: number | null;
  test_samples: number | null;
  precision_score: number | null;
  recall_score: number | null;
  f1_score: number | null;
  roc_auc: number | null;
  win_fast_capture_rate: number | null;
  false_positive_rate: number | null;
  train_from: string | null;
  train_to: string | null;
  dataset_query_cutoff: string | null;
  model_path: string | null;
  decision_threshold: number | null;
  activated_at: string | null;
  retired_at: string | null;
  notes: string | null;
  label_version: string | null;
  metrics_json: MetricsJson | null;
  target_window_seconds: number | null;
  descriptive_status?: string | null;
  predictive_status?: string | null;
  calibration_authority?: boolean;
  rule_generation_authority?: boolean;
  autopilot_authority?: boolean;
  execution_authority?: boolean;
  governance_reason?: Record<string, unknown> | null;
}

function fmt(v: number | null, digits = 4): string {
  if (v == null) return "—";
  return v.toFixed(digits);
}

function fmtPct(v: number | null): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDateTime(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString("pt-BR", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtAuditWindow(window: AuditWindow): string {
  if (!window.from) return "Não registrado";
  if (!window.to) return `${fmtDateTime(window.from)} → fim não registrado`;
  return `${fmtDateTime(window.from)} → ${fmtDateTime(window.to)}`;
}

function windowEvidence(window: AuditWindow): string | null {
  if (window.evidence === "boundary") return "limites do split";
  if (window.evidence === "missing") return "modelo legado";
  return null;
}

function MetricBadge({ label, value, good }: { label: string; value: string; good: boolean | null }) {
  const cls = good === null
    ? "text-[#94A3B8]"
    : good ? "text-[#34D399]" : "text-[#F87171]";
  return (
    <div className="flex flex-col items-center gap-0.5 min-w-[72px]">
      <span className={`text-[15px] font-bold font-mono ${cls}`}>{value}</span>
      <span className="text-[10px] text-[#4B5563] uppercase tracking-wide">{label}</span>
    </div>
  );
}

function HyperparamValue({ v }: { v: unknown }) {
  if (v == null) return <span className="text-[#4B5563]">—</span>;
  if (typeof v === "number") {
    return <span>{Number.isInteger(v) ? v : v.toFixed(4)}</span>;
  }
  if (typeof v === "boolean") {
    return <span>{v ? "true" : "false"}</span>;
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-[#4B5563]">[]</span>;
    // Short primitive arrays: inline
    if (v.length <= 4 && v.every((x) => typeof x !== "object" || x === null)) {
      return <span>[{v.join(", ")}]</span>;
    }
    return (
      <details className="inline">
        <summary className="cursor-pointer text-[#60A5FA] hover:underline">[{v.length} items]</summary>
        <pre className="mt-1 text-[10px] text-[#94A3B8] whitespace-pre-wrap break-all max-h-48 overflow-auto bg-[#060810] p-2 rounded">
          {JSON.stringify(v, null, 2)}
        </pre>
      </details>
    );
  }
  if (typeof v === "object") {
    const keys = Object.keys(v as object);
    if (keys.length === 0) return <span className="text-[#4B5563]">{"{}"}</span>;
    // Small objects (≤4 keys, all primitive): inline
    const allPrimitive = keys.every((k) => typeof (v as Record<string, unknown>)[k] !== "object");
    if (keys.length <= 4 && allPrimitive) {
      return <span>{keys.map((k) => `${k}:${(v as Record<string, unknown>)[k]}`).join(" ")}</span>;
    }
    return (
      <details className="inline">
        <summary className="cursor-pointer text-[#60A5FA] hover:underline">{"{"}…{keys.length} keys{"}"}</summary>
        <pre className="mt-1 text-[10px] text-[#94A3B8] whitespace-pre-wrap break-all max-h-48 overflow-auto bg-[#060810] p-2 rounded">
          {JSON.stringify(v, null, 2)}
        </pre>
      </details>
    );
  }
  return <span>{String(v)}</span>;
}

function HyperparamTable({ params }: { params: Record<string, unknown> | null }) {
  if (!params) return <span className="text-[#4B5563]">—</span>;
  const entries = Object.entries(params).filter(
    ([k]) => !["objective", "eval_metric", "tree_method", "device", "random_state", "missing"].includes(k)
  );
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-start justify-between gap-2">
          <span className="text-[11px] text-[#4B5563] shrink-0">{k}</span>
          <span className="text-[11px] font-mono text-[#94A3B8] text-right">
            <HyperparamValue v={v} />
          </span>
        </div>
      ))}
    </div>
  );
}

export default function MlModelsPage() {
  const [models, setModels] = useState<MlModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    apiGet("/api/ml/models")
      .then((data) => {
        setModels(data?.models ?? []);
        const featured = (data?.models ?? []).find((m: MlModel) =>
          m.status === "active" || m.descriptive_status === "DESCRIPTIVE_VALIDATED"
        );
        if (featured) setExpanded(featured.id);
      })
      .catch((e) => setError(e?.message ?? "Erro ao carregar modelos"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="p-6 flex items-center gap-3 text-[#4B5563]">
        <Brain size={18} className="animate-pulse" />
        <span className="text-sm">Carregando modelos...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 text-[#F87171] text-sm">{error}</div>
    );
  }

  return (
    <div className="p-6 space-y-4 max-w-5xl">
      <div className="flex items-center gap-3 mb-2">
        <Brain size={20} className="text-[#60A5FA]" />
        <h1 className="text-[17px] font-semibold text-[#E2E8F0] tracking-wide">ML Models</h1>
        <span className="text-[11px] text-[#4B5563] ml-1">{models.length} versão{models.length !== 1 ? "ões" : ""}</span>
      </div>

      {models.length === 0 && (
        <div className="text-[#4B5563] text-sm py-8 text-center border border-dashed border-[#1A2035] rounded-lg">
          Nenhum modelo treinado ainda.
        </div>
      )}

      {models.map((m) => {
        const isExpanded = expanded === m.id;
        const isActive = m.status === "active";
        const isIntelligence = [
          "L3_INTELLIGENCE",
          "L3_APPROVED_INTELLIGENCE",
          "L3_CONTEXTUAL_INTELLIGENCE",
        ].includes(m.model_lane ?? "");
        const isPredictiveApproved =
          isIntelligence && m.predictive_status === "PREDICTIVE_APPROVED_FOR_INTELLIGENCE";
        const isDescriptiveValidated =
          isIntelligence && m.descriptive_status === "DESCRIPTIVE_VALIDATED";
        const actionableFindings = (m.metrics_json?.indicator_intelligence?.findings ?? [])
          .filter((finding) => finding.action !== "OBSERVE")
          .slice(0, 8);
        const datasetAudit = buildModelDatasetAudit(m);

        return (
          <div
            key={m.id}
            className={`rounded-lg border transition-colors ${
              isActive || isPredictiveApproved
                ? "border-[#34D399]/30 bg-[#060E18]"
                : "border-[#1A2035] bg-[#060810]"
            }`}
          >
            {/* Header row */}
            <div
              className="flex items-center gap-4 px-4 py-3 cursor-pointer select-none"
              onClick={() => setExpanded(isExpanded ? null : m.id)}
            >
              {isExpanded
                ? <ChevronDown size={14} className="text-[#60A5FA] shrink-0" />
                : <ChevronRight size={14} className="text-[#334155] shrink-0" />
              }

              <div className="flex items-center gap-2 min-w-[80px]">
                <span className="text-[13px] font-bold font-mono text-[#E2E8F0]">v{m.version}</span>
                {isPredictiveApproved ? (
                  <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#34D399]/10 text-[#34D399] border border-[#34D399]/20">
                    <ShieldCheck size={9} /> PREDICTIVE APPROVED
                  </span>
                ) : isDescriptiveValidated ? (
                  <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#F59E0B]/10 text-[#FBBF24] border border-[#F59E0B]/20">
                    RELATÓRIO DESCRITIVO
                  </span>
                ) : isActive ? (
                  <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#34D399]/10 text-[#34D399] border border-[#34D399]/20">
                    <CheckCircle size={9} /> ACTIVE
                  </span>
                ) : m.status === "candidate" ? (
                  <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#F59E0B]/10 text-[#FBBF24] border border-[#F59E0B]/20">
                    CANDIDATE
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#1A2035] text-[#4B5563] border border-[#1A2035]">
                    <Archive size={9} /> RETIRED
                  </span>
                )}
              </div>

              {/* Quick metrics strip */}
              <div className="flex items-center gap-5 flex-1">
                <MetricBadge label="F1" value={fmt(m.f1_score, 3)} good={m.f1_score != null ? m.f1_score >= 0.5 : null} />
                <MetricBadge label="AUC" value={fmt(m.roc_auc, 3)} good={m.roc_auc != null ? m.roc_auc >= 0.6 : null} />
                <MetricBadge label="Precision" value={fmtPct(m.precision_score)} good={m.precision_score != null ? m.precision_score >= 0.5 : null} />
                <MetricBadge label="Recall" value={fmtPct(m.recall_score)} good={m.recall_score != null ? m.recall_score >= 0.4 : null} />
                <MetricBadge label="Threshold" value={fmt(m.decision_threshold, 3)} good={null} />
              </div>

              <div className="text-[11px] text-[#4B5563] shrink-0">
                {fmtDateTime(m.activated_at)}
              </div>
            </div>

            {/* Expanded detail */}
            {isExpanded && (
              <div className="border-t border-[#1A2035] px-5 py-4 space-y-5">

                {/* Label version badge */}
                {(m.label_version || m.metrics_json?.label_version) && (
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-[10px] px-2 py-0.5 rounded bg-[#1A2035] border border-[#334155] font-mono text-[#94A3B8]">
                      label: {m.label_version ?? m.metrics_json?.label_version ?? "—"}
                    </span>
                    {m.model_lane && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-[#0C1020] border border-[#334155] font-mono text-[#60A5FA]">
                        lane: {m.model_lane}
                      </span>
                    )}
                    {(m.target_window_seconds ?? m.metrics_json?.target_window_seconds) != null && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-[#1A2035] border border-[#334155] font-mono text-[#94A3B8]">
                        janela TP: {Math.round(((m.target_window_seconds ?? m.metrics_json?.target_window_seconds) as number) / 60)} min
                      </span>
                    )}
                  </div>
                )}

                {isIntelligence && (
                  <div className={`rounded-md border p-4 ${isPredictiveApproved ? "border-[#34D399]/20 bg-[#07140F]" : "border-[#F87171]/30 bg-[#160A0D]"}`}>
                    <div className="flex items-center justify-between gap-3">
                      <div className={`flex items-center gap-2 ${isPredictiveApproved ? "text-[#34D399]" : "text-[#F87171]"}`}>
                        <ShieldCheck size={15} />
                        <span className="text-[11px] font-semibold uppercase tracking-widest">
                          {isPredictiveApproved ? "Modelo preditivo aprovado para inteligência" : "Modelo preditivo reprovado"}
                        </span>
                      </div>
                      <span className="text-[10px] font-mono text-[#94A3B8]">
                        execução: bloqueada
                      </span>
                    </div>
                    <p className="mt-2 text-[11px] leading-relaxed text-[#64748B]">
                      {isPredictiveApproved
                        ? "Pode publicar evidência estruturada; execução e auto-pilot continuam bloqueados."
                        : "Relatório histórico/descritivo somente. Não gera regras, não calibra Profiles e não participa do auto-pilot."}
                    </p>
                  </div>
                )}

                {isDescriptiveValidated && actionableFindings.length > 0 && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">
                      Inteligência de indicadores — validação e hold-out concordantes
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {actionableFindings.map((finding, index) => (
                        <div
                          key={`${finding.indicator}-${index}`}
                          className="flex items-center justify-between gap-3 rounded-md border border-[#1A2035] bg-[#080E1C] px-3 py-2.5"
                        >
                          <div className="min-w-0">
                            <div className="truncate font-mono text-[11px] text-[#CBD5E1]">
                              {finding.indicator}
                            </div>
                            <div className="mt-0.5 text-[10px] text-[#475569]">
                              lift test {finding.test.lift >= 0 ? "+" : ""}{fmtPct(finding.test.lift)} · N efetivo {finding.test.effective_cases.toFixed(0)}
                            </div>
                            <div className="mt-0.5 text-[10px] font-mono text-[#64748B]">
                              intervalo: ({finding.bucket.lower_exclusive ?? "−∞"}, {finding.bucket.upper_inclusive ?? "+∞"}]
                            </div>
                          </div>
                          <span className={`shrink-0 rounded px-2 py-1 text-[9px] font-semibold tracking-wide ${
                            finding.action === "PRIORITIZE"
                              ? "bg-[#34D399]/10 text-[#34D399]"
                              : "bg-[#F87171]/10 text-[#F87171]"
                          }`}>
                            {finding.action === "PRIORITIZE" ? "PRIORIZAR SINAL" : "BLOQUEIO CANDIDATO"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Validation metrics (precision_score/recall_score columns carry val metrics for challenger models) */}
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">
                    {m.metrics_json?.validation ? "Métricas — validação (val set)" : "Métricas"}
                  </div>
                  <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
                    {[
                      { label: "Precision", value: fmtPct(m.metrics_json?.validation?.precision ?? m.precision_score), good: (m.metrics_json?.validation?.precision ?? m.precision_score ?? 0) >= 0.5 },
                      { label: "Recall",    value: fmtPct(m.metrics_json?.validation?.recall ?? m.recall_score),       good: (m.metrics_json?.validation?.recall ?? m.recall_score ?? 0) >= 0.4 },
                      { label: "F1",        value: fmt(m.metrics_json?.validation?.f1 ?? m.f1_score, 4),               good: (m.metrics_json?.validation?.f1 ?? m.f1_score ?? 0) >= 0.5 },
                      { label: "ROC AUC",   value: fmt(m.metrics_json?.validation?.roc_auc ?? m.roc_auc, 4),           good: (m.metrics_json?.validation?.roc_auc ?? m.roc_auc ?? 0) >= 0.6 },
                      { label: "Capture",   value: fmtPct(m.win_fast_capture_rate),                                    good: (m.win_fast_capture_rate ?? 0) >= 0.5 },
                      { label: "FPR",       value: fmtPct(m.metrics_json?.validation?.fpr ?? m.false_positive_rate),  good: (m.metrics_json?.validation?.fpr ?? m.false_positive_rate ?? 1) <= 0.4 },
                    ].map((item) => (
                      <div key={item.label} className="bg-[#0C1020] rounded-md p-3 flex flex-col items-center gap-1">
                        <span className={`text-[16px] font-bold font-mono ${item.good ? "text-[#34D399]" : "text-[#F87171]"}`}>
                          {item.value}
                        </span>
                        <span className="text-[10px] text-[#4B5563] uppercase tracking-wide">{item.label}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Test set metrics — only available post-migration-104 */}
                {m.metrics_json?.test && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">Métricas — test set (hold-out)</div>
                    <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
                      {[
                        { label: "Precision", value: fmtPct(m.metrics_json.test.precision), good: (m.metrics_json.test.precision ?? 0) >= 0.5 },
                        { label: "Recall",    value: fmtPct(m.metrics_json.test.recall),    good: (m.metrics_json.test.recall ?? 0) >= 0.4 },
                        { label: "F1",        value: fmt(m.metrics_json.test.f1, 4),        good: (m.metrics_json.test.f1 ?? 0) >= 0.5 },
                        { label: "ROC AUC",   value: fmt(m.metrics_json.test.roc_auc, 4),   good: (m.metrics_json.test.roc_auc ?? 0) >= 0.6 },
                        { label: "FPR",       value: fmtPct(m.metrics_json.test.fpr),       good: (m.metrics_json.test.fpr ?? 1) <= 0.4 },
                      ].map((item) => (
                        <div key={item.label} className="bg-[#080E1C] border border-[#1A2035] rounded-md p-3 flex flex-col items-center gap-1">
                          <span className={`text-[16px] font-bold font-mono ${item.good ? "text-[#34D399]" : "text-[#F87171]"}`}>
                            {item.value}
                          </span>
                          <span className="text-[10px] text-[#4B5563] uppercase tracking-wide">{item.label}</span>
                        </div>
                      ))}
                    </div>
                    {m.metrics_json.test.samples != null && (
                      <div className="mt-2 text-[10px] text-[#4B5563] font-mono">test samples: {m.metrics_json.test.samples}</div>
                    )}
                  </div>
                )}

                {/* Dataset chronology */}
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">Auditoria temporal do dataset</div>
                  <div className="grid gap-x-8 gap-y-2 text-[11px] sm:grid-cols-2">
                    {[
                      { label: "Período total elegível", window: datasetAudit.datasetWindow },
                      { label: "Janela de treino", window: datasetAudit.trainWindow },
                      { label: "Janela de validação", window: datasetAudit.validationWindow },
                      { label: "Janela de teste", window: datasetAudit.testWindow },
                    ].map(({ label, window }) => (
                      <div key={label} className="flex min-w-0 items-start justify-between gap-3 border-b border-[#111827] py-1.5">
                        <span className="shrink-0 text-[#4B5563]">{label}</span>
                        <span className="min-w-0 text-right font-mono text-[#94A3B8]">
                          {fmtAuditWindow(window)}
                          {windowEvidence(window) && (
                            <span className="ml-1 text-[9px] text-[#64748B]">({windowEvidence(window)})</span>
                          )}
                        </span>
                      </div>
                    ))}
                    <div className="flex min-w-0 items-start justify-between gap-3 border-b border-[#111827] py-1.5">
                      <span className="shrink-0 text-[#4B5563]">Cutoff da consulta</span>
                      <span className="text-right font-mono text-[#94A3B8]">{fmtDateTime(datasetAudit.cutoff)}</span>
                    </div>
                    <div className="flex min-w-0 items-start justify-between gap-3 border-b border-[#111827] py-1.5">
                      <span className="shrink-0 text-[#4B5563]">Threshold</span>
                      <span className="text-right font-mono text-[#60A5FA]">{fmt(m.decision_threshold, 4)}</span>
                    </div>
                  </div>
                </div>

                {/* Dataset reconciliation */}
                <div>
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="text-[10px] uppercase tracking-widest text-[#334155]">Reconciliação das amostras</div>
                    <div className={`text-[10px] font-mono ${
                      datasetAudit.reconciles === true
                        ? "text-[#34D399]"
                        : datasetAudit.reconciles === false
                          ? "text-[#F87171]"
                          : "text-[#64748B]"
                    }`}>
                      {datasetAudit.reconciles === true
                        ? "FECHA COM A BASE DO SPLIT"
                        : datasetAudit.reconciles === false
                          ? "DIVERGÊNCIA DE CONTAGEM"
                          : "RECONCILIAÇÃO INCOMPLETA"}
                    </div>
                  </div>
                  <div className="mb-2 flex flex-wrap gap-x-6 gap-y-1 text-[10px] font-mono text-[#64748B]">
                    <span>contrato econômico: <strong className="text-[#94A3B8]">{datasetAudit.includedTradeCount ?? "—"}</strong></span>
                    <span>rejeitado por features: <strong className="text-[#94A3B8]">{datasetAudit.featureRejectedCount ?? "—"}</strong></span>
                    <span>base do split: <strong className="text-[#94A3B8]">{datasetAudit.datasetRows ?? "—"}</strong></span>
                  </div>
                  <div className="overflow-x-auto border-y border-[#1A2035]">
                    <table className="w-full min-w-[620px] table-fixed text-left">
                      <thead>
                        <tr className="text-[9px] uppercase tracking-widest text-[#334155]">
                          {datasetAudit.reconciliationRows.map((row) => (
                            <th key={row.key} className="px-2 py-2 font-medium">{row.label}</th>
                          ))}
                          <th className="px-2 py-2 font-medium">Total reconciliado</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr className="border-t border-[#111827] font-mono text-[12px] text-[#CBD5E1]">
                          {datasetAudit.reconciliationRows.map((row) => (
                            <td key={row.key} className="px-2 py-2.5">{row.count ?? "—"}</td>
                          ))}
                          <td className="px-2 py-2.5 font-semibold text-[#60A5FA]">{datasetAudit.reconciledTotal ?? "—"}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Hyperparams */}
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">
                    {Number(m.hyperparams?.n_trials ?? 0) > 0 && m.hyperparams?.best_trial_number != null
                      ? "Hiperparâmetros (Optuna)"
                      : "Configuração fixa"}
                  </div>
                  <HyperparamTable params={m.hyperparams as Record<string, unknown> | null} />
                </div>

                {/* Notes */}
                {m.notes && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-2">Notes</div>
                    <p className="text-[11px] font-mono text-[#4B5563] leading-relaxed break-all">{m.notes}</p>
                  </div>
                )}

                {/* Model path */}
                {m.model_path && (
                  <div className="text-[11px] font-mono text-[#334155] break-all">{m.model_path}</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
