"use client";

import { useCallback, useState } from "react";
import { History as HistoryIcon, TrendingDown, TrendingUp, Minus } from "lucide-react";

import { apiGet } from "@/lib/api";
import styles from "./capital-flow.module.css";

export type CapitalFlow = {
  state: "forte_entrada" | "entrada" | "neutro" | "saida" | "forte_saida" | "desconhecido" | "desligado";
  net_usdt: number | null;
  gross_usdt: number | null;
  ratio: number | null;
  z: number | null;
  method: string | null;
  coverage: number | null;
  window_minutes: number;
  enter_score_delta: number;
  regime_cap: string | null;
  reason?: string;
};

export type MlRegime = {
  active: boolean;
  reason?: string | null;
  mean_up_probability?: number | null;
  assets?: number;
  cap?: string | null;
  regime_effect?: string;
  objective?: "relative" | "absolute" | "relative_candle";
  max_adjust?: number;
  model?: { experiment_id?: string; created_at?: string; horizon_minutes: number; objective?: string;
    quality?: { approved: boolean; reasons: string[]; auc: number | null; brier: number | null;
      baseline_brier: number | null; test_episodes: number } } | null;
};

export type V1Regime = {
  state?: string;
  price_state?: string;
  enter_score?: number;
  capital?: CapitalFlow;
  ml?: MlRegime;
};

const ML_REASON: Record<string, string> = {
  no_recent_model: "nenhum modelo treinado nos últimos dias",
  quality_gate_failed: "modelo mais recente não passou no teste de qualidade",
  artifacts_missing: "arquivos do modelo ausentes",
  ml_disabled: "ML desligado na configuração",
  not_loaded: "ML ainda não carregado",
};

const QUALITY_REASON: Record<string, string> = {
  auc_below_min: "AUC abaixo do mínimo",
  brier_not_better_than_base_rate: "não supera a taxa base (Brier)",
  brier_improvement_ci_includes_zero: "ganho sobre a taxa base não é significativo",
  too_few_test_episodes: "poucos episódios no teste",
  days_not_consistently_above_half: "acerto não se repete dia a dia (teste de sinal)",
};

const LABEL_MODE: Record<string, string> = { endpoint: "ponto final", path_mean: "média do caminho" };

type MlModelRow = {
  family?: "observation" | "candle";
  horizon_minutes: number;
  applied: boolean;
  status: "approved" | "quality_gate_failed" | "no_recent_model";
  experiment_id?: string;
  created_at?: string;
  quality?: { approved: boolean; reasons: string[]; auc: number | null; brier: number | null;
    baseline_brier: number | null; brier_ci95?: number[] | null; test_episodes: number };
  diagnostics?: {
    test_up_frequency: number | null; train_up_frequency: number | null; calibration_up_frequency: number | null;
    test_mean_probability: number | null; direction_accuracy: number | null;
    cohort_rows: number[] | null; cohort_episodes: number[] | null; cohort_days: number[] | null;
    calibration: { method: string; pool: string; slope: number; unbounded_slope: number; bounded: boolean; rows: number } | null;
    features: number; context_features: number;
    context_coverage_test: Record<string, number>;
    top_features: [string, number][];
    walk_forward?: {
      scored_days: number;
      pooled: {
        auc: number; episodes: number; days_auc_above_half: number; sign_test_p?: number;
        auc_statistic?: string; pooled_auc_reference_only?: number;
        economic?: { quantile: number; top_mean: number; bottom_mean: number; top_minus_bottom: number;
          top_minus_bottom_ci95: number[] | null; days_spread_positive?: number; days_with_spread?: number };
      } | null;
    } | null;
    label_variants?: Record<string, {
      auc: number | null; day_auc_median?: number | null; days_auc_above_half: number | null; sign_test_p: number | null;
      top_minus_bottom: number | null; top_minus_bottom_ci95: number[] | null;
    } | null>;
  };
};
type MlLive = {
  status: string; days: number; mature?: number; pending?: number; predictions?: number;
  day_auc_median?: number | null; pooled_auc?: number; days_scored?: number; days_auc_above_half?: number;
  sign_test_p?: number | null; economic?: { top_minus_bottom: number } | null;
};
type MlModels = { enabled: boolean; applied_horizon_minutes: number; objective?: string; models: MlModelRow[]; note: string };

const f3 = (v?: number | null) => (v === null || v === undefined ? "—" : v.toFixed(3));
const sgn = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
const pc = (v?: number | null) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);

function liveLine(l: MlLive | null): string {
  if (!l) return "";
  if (l.status === "no_predictions") return "Ao vivo: nenhuma previsão registrada ainda.";
  if (l.status !== "ok") return "";
  if (l.pooled_auc === undefined) return `Ao vivo: ${l.mature ?? 0} previsões maduras, ${l.pending ?? 0} aguardando o horizonte — ainda sem amostra.`;
  const auc = l.day_auc_median ?? l.pooled_auc;
  return `Ao vivo (${l.days} d): AUC ${l.day_auc_median != null ? "mediana diária " : ""}${f3(auc)}`
    + (l.days_scored ? ` · ${l.days_auc_above_half}/${l.days_scored} dias > 0,5${l.sign_test_p != null ? ` (p ${l.sign_test_p.toFixed(3)})` : ""}` : "")
    + (l.economic ? ` · top − bottom ${sgn(l.economic.top_minus_bottom)} pp` : "")
    + ` · ${l.mature} previsões maduras`;
}

function MlModelsPanel({ data, error, live }: { data: MlModels | null; error: string; live?: MlLive | null }) {
  if (error) return <div className={styles.error}>{error}</div>;
  if (!data) return <div className={styles.muted}>Carregando modelos…</div>;
  return (
    <>
      <div className={styles.heading}>
        Modelos por horizonte (teste fora da amostra) · {data.objective === "pump_endpoint_direction_v1" ? "direção absoluta" : "acima do mercado (ajustado por beta)"}
      </div>
      <div className={styles.mlTable} role="table">
        <div className={styles.mlHead} role="row">
          <span>Horizonte</span><span>Status</span><span>AUC</span><span>Brier / base</span>
          <span>{data.objective === "pump_endpoint_direction_v1" ? "Alta teste / treino" : "Acima teste / treino"}</span><span>Episódios</span>
        </div>
        {data.models.map(m => {
          const q = m.quality; const d = m.diagnostics;
          const ctxCov = d ? Object.values(d.context_coverage_test ?? {}) : [];
          const avgCov = ctxCov.length ? ctxCov.reduce((a, b) => a + b, 0) / ctxCov.length : null;
          return (
            <div key={`${m.family ?? "observation"}-${m.horizon_minutes}`} className={styles.mlBlock}>
              <div className={styles.mlRow} role="row">
                <span>{m.horizon_minutes} min{m.family === "candle" ? " · velas" : ""}{m.applied ? " ●" : ""}</span>
                <span className={m.status === "approved" ? styles.inText : m.status === "no_recent_model" ? styles.muted : styles.outText}>
                  {m.status === "approved" ? "Aprovado" : m.status === "no_recent_model" ? "Sem modelo" : "Reprovado"}
                </span>
                <span>{f3(q?.auc)}</span>
                <span>{q ? `${f3(q.brier)} / ${f3(q.baseline_brier)}` : "—"}</span>
                <span>{d ? `${pc(d.test_up_frequency)} / ${pc(d.train_up_frequency)}` : "—"}</span>
                <span>{q?.test_episodes ?? "—"}</span>
              </div>
              {q && !q.approved && q.reasons.length > 0 && (
                <div className={styles.muted}>Motivo: {q.reasons.map(r => QUALITY_REASON[r] ?? r).join(", ")}</div>
              )}
              {d?.walk_forward?.pooled && (() => {
                const w = d.walk_forward.pooled; const e = w.economic;
                return (
                  <div className={styles.muted}>
                    Walk-forward: {d.walk_forward.scored_days} dia(s) testados um a um · AUC {w.auc_statistic === "day_median" ? "mediana diária " : ""}{f3(w.auc)} · dias com AUC &gt; 0,5: {w.days_auc_above_half}/{d.walk_forward.scored_days}
                    {w.sign_test_p !== undefined ? ` (p ${w.sign_test_p.toFixed(4)})` : ""}
                    {e ? ` · top ${Math.round(e.quantile * 100)}% − bottom ${Math.round(e.quantile * 100)}%: ${sgn(e.top_minus_bottom)} pp${e.top_minus_bottom_ci95 ? ` (IC95 por dias ${e.top_minus_bottom_ci95.map(x => x.toFixed(3)).join(" a ")})` : ""}${e.days_with_spread ? ` · ${e.days_spread_positive}/${e.days_with_spread} dias positivos` : ""}` : ""}
                  </div>
                );
              })()}
              {m.applied && liveLine(live ?? null) && <div className={styles.muted}>{liveLine(live ?? null)}</div>}
              {d?.label_variants && Object.keys(d.label_variants).length > 1 && (
                <div className={styles.muted}>
                  Rótulos comparados: {Object.entries(d.label_variants).map(([mode, v]) => v
                    ? `${LABEL_MODE[mode] ?? mode} AUC ${f3(v.day_auc_median ?? v.auc)}, ${v.days_auc_above_half ?? "—"} dias > 0,5${v.sign_test_p != null ? ` (p ${v.sign_test_p.toFixed(4)})` : ""}, spread ${v.top_minus_bottom != null ? sgn(v.top_minus_bottom) : "—"} pp`
                    : `${LABEL_MODE[mode] ?? mode} —`).join(" · ")}
                </div>
              )}
              {d && (
                <div className={styles.muted}>
                  Calibração {d.calibration?.method ?? "—"} ({d.calibration?.rows ?? "—"} linhas
                  {d.calibration ? `, inclinação ${d.calibration.slope.toFixed(2)}${d.calibration.bounded ? ` limitada de ${d.calibration.unbounded_slope.toFixed(2)}` : ""}` : ""})
                  {" · "}prob. média no teste {pc(d.test_mean_probability)}
                  {" · "}{d.features}+{d.context_features} variáveis
                  {avgCov !== null ? ` (contexto presente em ${pc(avgCov)} do teste)` : ""}
                  {d.top_features?.length ? ` · mais usadas: ${d.top_features.slice(0, 4).map(([n]) => n).join(", ")}` : ""}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className={styles.muted}>● horizonte aplicado no v1. {data.note}</div>
    </>
  );
}

export function MlStatusChip({ ml }: { ml?: MlRegime | null }) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<MlModels | null>(null);
  const [live, setLive] = useState<MlLive | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setModels(await apiGet<MlModels>("/pump-monitor/ml/models"));
      setError("");
    } catch {
      setError("Métricas dos modelos indisponíveis no momento.");
    }
    try {
      setLive(await apiGet<MlLive>("/pump-monitor/ml/live-evaluation?days=14"));
    } catch {
      setLive(null);  // optional line: absent table or timeout never hides the panel
    }
  }, []);
  if (!ml) return null;
  const q = ml.model?.quality;
  const h = ml.model?.horizon_minutes ?? 15;
  // Relative objectives (observation or candle family) rank assets against the market;
  // only the absolute one speaks about price direction. Unknown → relative (never claim "alta").
  const objective = ml.objective ?? (ml.model?.objective === "pump_endpoint_direction_v1" ? "absolute" : "relative");
  const relative = objective !== "absolute";
  const candle = objective === "relative_candle" || ml.model?.objective === "pump_relative_candle_v1";
  const adj = ml.max_adjust != null ? `±${Math.round(ml.max_adjust * 100)}%` : "limite da config";
  const lines = [
    relative
      ? `XGBoost${candle ? " (velas de 5 min, só preço)" : ""} — probabilidade de o ativo terminar acima do mercado em ${h} min (retorno descontado do beta × mediana do universo, sem alvo de %).`
      : `XGBoost — probabilidade de o preço terminar acima em ${h} min (direção absoluta, sem alvo de %).`,
    ml.active
      ? (relative ? `Ativo: ajusta o score (${adj}) e confirma a seta só com convicção; regime não é afetado (objetivo relativo).` : `Ativo: ajusta o score (${adj}), seta de direção e regime.`)
      : `Sem efeito: ${ML_REASON[ml.reason ?? ""] ?? ml.reason ?? "—"}`,
    q ? `Teste fora da amostra: AUC ${q.auc?.toFixed(3) ?? "—"} · Brier ${q.brier?.toFixed(4) ?? "—"} vs base ${q.baseline_brier?.toFixed(4) ?? "—"} · ${q.test_episodes} episódios` : "",
    q && !q.approved ? `Reprovado: ${q.reasons.map(r => QUALITY_REASON[r] ?? r).join(", ")}` : "",
    ml.model?.created_at ? `Modelo de ${new Date(ml.model.created_at).toLocaleString("pt-BR")}` : "",
    ml.active && !relative && ml.mean_up_probability != null ? `Média do universo: ${(ml.mean_up_probability * 100).toFixed(0)}% de alta (${ml.assets} ativos)${ml.cap ? ` → regime limitado a ${ml.cap}` : ""}` : "",
  ].filter(Boolean).join("\n");
  return (
    <div className={styles.wrap}>
      <button type="button" className={`${styles.chip} ${styles.mlChip} ${ml.active ? styles.in : styles.flat}`}
        title={lines} data-testid="ml-status" aria-expanded={open}
        onClick={() => { if (!open) void load(); setOpen(v => !v); }}>
        <span className={styles.label}>ML {h}m</span>
        <strong>{ml.active ? "Ativo" : "Sem efeito"}</strong>
        {ml.active && !relative && ml.mean_up_probability != null && (
          <span className={styles.metric}>{(ml.mean_up_probability * 100).toFixed(0)}% alta</span>
        )}
        <HistoryIcon size={13} className={styles.historyIcon} />
      </button>
      {open && <div className={styles.panel}><MlModelsPanel data={models} error={error} live={live} /></div>}
    </div>
  );
}

type HistoryHour = { hour: string; net_usdt: number; ratio: number | null; minutes: number };
type CapitalHistory = {
  days: number;
  top_inflow: HistoryHour[];
  top_outflow: HistoryHour[];
  hour_of_day: { hour: number; avg_ratio: number; days: number }[];
  note: string;
};

const LABEL: Record<CapitalFlow["state"], string> = {
  forte_entrada: "Forte entrada",
  entrada: "Entrada",
  neutro: "Neutro",
  saida: "Saída",
  forte_saida: "Forte saída",
  desconhecido: "Sem dado suficiente",
  desligado: "Desligado",
};

const TONE: Record<CapitalFlow["state"], string> = {
  forte_entrada: styles.strongIn,
  entrada: styles.in,
  neutro: styles.flat,
  saida: styles.out,
  forte_saida: styles.strongOut,
  desconhecido: styles.flat,
  desligado: styles.flat,
};

const REGIME: Record<string, string> = {
  favoravel: "favorável", neutro: "neutro", desfavoravel: "desfavorável",
  desconhecido: "desconhecido", desligado: "desligado",
};

function usd(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  const abs = Math.abs(value);
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const n = abs >= 1e6 ? `${(abs / 1e6).toFixed(2)}M` : abs >= 1e3 ? `${(abs / 1e3).toFixed(1)}k` : abs.toFixed(0);
  return `${sign}$${n}`;
}

function pct(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function hourLabel(iso: string) {
  const [date, time] = iso.split("T");
  const [, m, d] = date.split("-");
  return `${d}/${m} ${time.slice(0, 2)}h`;
}

export default function CapitalFlowIndicator({ capital, regime }: { capital?: CapitalFlow | null; regime?: V1Regime | null }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<CapitalHistory | null>(null);
  const [error, setError] = useState("");

  const loadHistory = useCallback(async () => {
    try {
      const offset = -new Date().getTimezoneOffset();
      setHistory(await apiGet<CapitalHistory>(`/pump-monitor/capital-flow/history?days=7&top=5&tz_offset_minutes=${offset}`));
      setError("");
    } catch {
      setError("Histórico indisponível no momento.");
    }
  }, []);

  if (!capital) return null;
  const state = capital.state;
  const Icon = state === "forte_entrada" || state === "entrada" ? TrendingUp
    : state === "forte_saida" || state === "saida" ? TrendingDown : Minus;
  const delta = capital.enter_score_delta;
  const capped = regime?.price_state && regime.state && regime.price_state !== regime.state;
  const title = [
    `Fluxo líquido a mercado em USDT do universo, últimos ${capital.window_minutes} min (Gate).`,
    `compra − venda: ${usd(capital.net_usdt)} de ${usd(capital.gross_usdt).replace("+", "")} negociados (${pct(capital.ratio)})`,
    capital.z !== null ? `z-score: ${capital.z.toFixed(2)} (vs. histórico recente)` : "z-score: aquecendo — usando limiar absoluto",
    capital.coverage !== null ? `cobertura: ${(capital.coverage * 100).toFixed(0)}%` : "",
    `efeito no v1: entrada exige ${regime?.enter_score ?? "—"}${delta ? ` (${delta > 0 ? "+" : ""}${delta})` : ""}`,
    capped ? `regime limitado: ${REGIME[regime?.price_state ?? ""] ?? regime?.price_state} → ${REGIME[regime?.state ?? ""] ?? regime?.state}` : "",
  ].filter(Boolean).join("\n");

  const maxAbs = Math.max(0.0001, ...(history?.hour_of_day ?? []).map(h => Math.abs(h.avg_ratio)));

  return (
    <div className={styles.wrap}>
      <button type="button" className={`${styles.chip} ${TONE[state]}`} title={title}
        onClick={() => { if (!open) void loadHistory(); setOpen(v => !v); }} aria-expanded={open} data-testid="capital-flow-indicator">
        <Icon size={14} />
        <span className={styles.label}>Capital {capital.window_minutes}m</span>
        <strong>{LABEL[state]}</strong>
        {capital.net_usdt !== null && state !== "desligado" && (
          <span className={styles.metric}>{usd(capital.net_usdt)} · {pct(capital.ratio)}</span>
        )}
        <span className={styles.effect}>
          regime {REGIME[regime?.state ?? ""] ?? "—"}{capped ? "*" : ""} · entrada ≥ {regime?.enter_score ?? "—"}
        </span>
        <HistoryIcon size={13} className={styles.historyIcon} />
      </button>
      {open && (
        <div className={styles.panel}>
          {error && <div className={styles.error}>{error}</div>}
          {!history && !error && <div className={styles.muted}>Carregando histórico…</div>}
          {history && (
            <>
              <div className={styles.columns}>
                <div>
                  <div className={styles.heading}>Maiores entradas (últimos {history.days} dias)</div>
                  {history.top_inflow.length === 0 && <div className={styles.muted}>Ainda sem horas completas.</div>}
                  {history.top_inflow.map(h => (
                    <div key={h.hour} className={styles.row}><span>{hourLabel(h.hour)}</span>
                      <span className={styles.inText}>{pct(h.ratio)}</span><span>{usd(h.net_usdt)}</span></div>
                  ))}
                </div>
                <div>
                  <div className={styles.heading}>Maiores saídas (últimos {history.days} dias)</div>
                  {history.top_outflow.length === 0 && <div className={styles.muted}>Ainda sem horas completas.</div>}
                  {history.top_outflow.map(h => (
                    <div key={h.hour} className={styles.row}><span>{hourLabel(h.hour)}</span>
                      <span className={styles.outText}>{pct(h.ratio)}</span><span>{usd(h.net_usdt)}</span></div>
                  ))}
                </div>
              </div>
              <div className={styles.heading}>Média por hora do dia (horário local)</div>
              <div className={styles.bars} role="img" aria-label="Fluxo médio por hora do dia">
                {Array.from({ length: 24 }, (_, hr) => {
                  const h = history.hour_of_day.find(x => x.hour === hr);
                  const v = h?.avg_ratio ?? 0;
                  const height = h ? Math.max(4, (Math.abs(v) / maxAbs) * 100) : 0;
                  return (
                    <div key={hr} className={styles.barCell}
                      title={h ? `${String(hr).padStart(2, "0")}h: ${pct(v)} (${h.days} dia(s))` : `${String(hr).padStart(2, "0")}h: sem dado`}>
                      <div className={styles.barTrack}>
                        <div className={`${styles.bar} ${v >= 0 ? styles.barIn : styles.barOut}`}
                          style={{ height: `${height / 2}%`, [v >= 0 ? "bottom" : "top"]: "50%" }} />
                      </div>
                      <span className={styles.barLabel}>{hr % 3 === 0 ? String(hr).padStart(2, "0") : ""}</span>
                    </div>
                  );
                })}
              </div>
              <div className={styles.muted}>{history.note}</div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
