"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Brain, CheckCircle, Archive, ChevronDown, ChevronRight } from "lucide-react";

interface MetricsBlock {
  precision: number | null;
  recall: number | null;
  fpr: number | null;
  f1: number | null;
  roc_auc: number | null;
  samples: number | null;
}

interface MetricsJson {
  label_version: string | null;
  target_window_seconds: number | null;
  validation: MetricsBlock | null;
  test: MetricsBlock | null;
}

interface MlModel {
  id: string;
  version: number;
  status: "active" | "retired";
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
  model_path: string | null;
  decision_threshold: number | null;
  activated_at: string | null;
  retired_at: string | null;
  notes: string | null;
  label_version: string | null;
  metrics_json: MetricsJson | null;
  target_window_seconds: number | null;
}

function fmt(v: number | null, digits = 4): string {
  if (v == null) return "—";
  return v.toFixed(digits);
}

function fmtPct(v: number | null): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("pt-BR", {
    day: "2-digit", month: "short", year: "numeric",
  });
}

function fmtDateTime(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString("pt-BR", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
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

function HyperparamTable({ params }: { params: Record<string, unknown> | null }) {
  if (!params) return <span className="text-[#4B5563]">—</span>;
  const entries = Object.entries(params).filter(
    ([k]) => !["objective", "eval_metric", "tree_method", "device", "random_state", "missing"].includes(k)
  );
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-[#4B5563]">{k}</span>
          <span className="text-[11px] font-mono text-[#94A3B8]">
            {typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(4)) : String(v ?? "—")}
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
        // Auto-expand the active model
        const active = (data?.models ?? []).find((m: MlModel) => m.status === "active");
        if (active) setExpanded(active.id);
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

        return (
          <div
            key={m.id}
            className={`rounded-lg border transition-colors ${
              isActive
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
                {isActive ? (
                  <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#34D399]/10 text-[#34D399] border border-[#34D399]/20">
                    <CheckCircle size={9} /> ACTIVE
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
                    {(m.target_window_seconds ?? m.metrics_json?.target_window_seconds) != null && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-[#1A2035] border border-[#334155] font-mono text-[#94A3B8]">
                        janela TP: {Math.round(((m.target_window_seconds ?? m.metrics_json?.target_window_seconds) as number) / 60)} min
                      </span>
                    )}
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

                {/* Dataset */}
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">Dataset</div>
                  <div className="flex flex-wrap gap-4 text-[12px]">
                    <div><span className="text-[#4B5563]">Train: </span><span className="font-mono text-[#94A3B8]">{m.train_samples ?? "—"}</span></div>
                    <div><span className="text-[#4B5563]">Val: </span><span className="font-mono text-[#94A3B8]">{m.val_samples ?? "—"}</span></div>
                    <div><span className="text-[#4B5563]">Test: </span><span className="font-mono text-[#94A3B8]">{m.test_samples ?? "—"}</span></div>
                    <div><span className="text-[#4B5563]">Período: </span><span className="font-mono text-[#94A3B8]">{fmtDate(m.train_from)} → {fmtDate(m.train_to)}</span></div>
                    <div><span className="text-[#4B5563]">Threshold: </span><span className="font-mono text-[#60A5FA]">{fmt(m.decision_threshold, 4)}</span></div>
                  </div>
                </div>

                {/* Hyperparams */}
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#334155] mb-3">Hiperparâmetros (Optuna)</div>
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
