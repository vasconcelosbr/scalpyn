"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, Brain, Download, Eye, Loader2, Play, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet, apiPost } from "@/lib/api";
import type { IndicatorEvidence, ManualAdjustmentPrefill } from "./ManualAdjustmentPanel";

type Num = number | null;
type OutcomeSummary = { n: number; min: Num; p25: Num; median: Num; mean: Num; p75: Num; p90: Num; max: Num };
export type ScoreStat = {
  score: string; origin: string; total: number; present: number; missing: number; coverage: number;
  tp: OutcomeSummary; sl: OutcomeSummary; timeout: OutcomeSummary;
  delta_mean_tp_sl: Num; delta_median_tp_sl: Num; standardized_effect_size: Num;
  auc: Num; auc_discrimination: Num; direction: string | null; ks_statistic: Num;
  pnl_correlation: Num; confidence: string;
};
type Scope = {
  source: string; profile_id: string; profile_name: string; profile_version_id: string;
  version_number: number; score_engine_version_id: string; profile_config_hash: string;
  score_engine_config_hash: string; timeframe?: string | null; trades: number;
  effective_from?: string; effective_to?: string;
};
type Threshold = {
  name: string; score: string; threshold: number; pass_rate: Num; volume_reduction: Num;
  lift: Num; win_rate_delta: Num; passed: {
    trades: number; tp: number; sl: number; timeout: number; win_rate: Num;
    avg_pnl_pct: Num; pnl_sum_pct?: Num; avg_mae_pct?: Num; avg_mfe_pct?: Num; avg_holding_seconds?: Num;
  };
};
type Recommendation = {
  informational_only: boolean; action: string; score: string; current_threshold: Num;
  proposed_threshold: number; confidence: string; risk: string; missing_rate: number;
  concentration?: { distinct_symbols: number; distinct_days: number; max_single_symbol_share: number; max_single_day_share: number };
  outcomes?: Record<string, number>; effect: Threshold;
};
export type ScoreOverview = {
  status: string; read_only: boolean; dataset?: string; association_not_causation?: boolean;
  lookback_days?: number; closed_trades?: number; outcomes?: Record<string, number>;
  scope?: Scope; available_scopes?: Scope[]; score_statistics?: ScoreStat[];
  current_thresholds?: Threshold[]; recommendation?: Recommendation | null;
  summary?: { strongest_separation?: ScoreStat | null; weakest_separation?: ScoreStat | null; most_permissive_threshold?: Threshold | null; most_discriminatory_threshold?: Threshold | null; coverage?: number };
};
type Bucket = { lower: number; upper: number; include_upper: boolean; trades: number; tp: number; sl: number; timeout: number; win_rate: Num; avg_pnl_pct: Num; avg_mae_pct: Num; avg_mfe_pct: Num; avg_holding_seconds: Num };
type DistributionResponse = { distribution?: { score: string; mode: string; buckets: Bucket[]; deterministic: boolean; persisted: boolean } };
type SimulationResponse = {
  current_threshold?: Threshold | null;
  difference_vs_current?: { threshold_delta: Num; passed_trades_delta: Num; win_rate_delta: Num; avg_pnl_pct_delta: Num; volume_reduction_delta: Num } | null;
  simulation?: Threshold & { eliminated_trades: number; baseline: { win_rate: Num; avg_pnl_pct: Num }; passed: Threshold["passed"] };
};
type VersionComparison = { status: string; current?: { scope: Scope; metrics: Record<string, Num> }; previous?: { scope: Scope; metrics: Record<string, Num> }; allowed_manual_states?: string[] };
type GlobalMetric = { closed: number; tp: number; sl: number; timeout: number; tp_rate: Num; sl_rate: Num; avg_pnl_pct: Num };
type GlobalOverview = {
  status: string; dataset_contract: string; cutoff_at: string; row_count: number; truncated: boolean;
  sources: Record<string, GlobalMetric>;
  quadrants: Record<string, GlobalMetric & { rapid_sl?: number; distinct_symbols: number; distinct_days: number }>;
  profiles: Array<{ profile_id: string; profile_name: string; profile_version_id: string; score_engine_version_id: string }>;
  policy: Record<string, number>;
};
type OptimizationRun = {
  id: string; status: string; cutoff_at: string; model?: string | null;
  evidence?: { candidate_count?: number; row_count?: number };
  executive_report?: {
    executive_summary?: string; global_diagnosis?: string[]; risks?: string[]; safeguards?: string[];
    profile_recommendations?: Array<{ profile_id: string; diagnosis: string; selected_candidate_ids: string[] }>;
  };
  adjustment_envelope?: { contract?: string; changes?: Array<{ candidate_id: string; profile_id: string; profile_name: string; evidence: Record<string, number | string | string[]> }> };
  replays?: Array<{ id: string; profile_id: string; status: string; delta_metrics: Record<string, number | null>; gates: Record<string, boolean> }>;
  challengers?: Array<{ id: string; profile_id: string; status: string; champion_profile_version_id: string; challenger_profile_version_id: string }>;
};
type PerformancePoint = { metric_date: string; variant: "champion" | "challenger"; closed: number; tp_rate: Num; sl_rate: Num; rapid_sl_rate: Num; pnl_sum_pct: Num };

const SCORE_LABELS: Record<string, string> = {
  liquidity_score: "Liquidity Score", market_structure_score: "Market Structure Score",
  momentum_score: "Momentum Score", signal_score: "Signal Score", score: "Score", alpha_score: "Alpha Score",
};
const pct = (value: Num, digits = 1) => value == null ? "—" : `${(value * 100).toFixed(digits)}%`;
const num = (value: Num, digits = 2) => value == null ? "—" : value.toFixed(digits);

function GlobalOptimizationPanel({ lookbackDays }: { lookbackDays: number }) {
  const [overview, setOverview] = useState<GlobalOverview | null>(null);
  const [run, setRun] = useState<OptimizationRun | null>(null);
  const [performance, setPerformance] = useState<PerformancePoint[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [globalData, runs, curve] = await Promise.all([
      apiGet<GlobalOverview>(`/profile-intelligence/score-intelligence/global-overview?lookback_days=${lookbackDays}`),
      apiGet<{ items: OptimizationRun[] }>("/profile-intelligence/score-intelligence/optimization-runs?limit=1"),
      apiGet<{ items: PerformancePoint[] }>("/profile-intelligence/score-intelligence/performance-evolution"),
    ]);
    setOverview(globalData); setPerformance(curve.items || []);
    if (runs.items?.[0]) {
      setRun(await apiGet<OptimizationRun>(`/profile-intelligence/score-intelligence/optimization-runs/${runs.items[0].id}`));
    }
  }, [lookbackDays]);

  useEffect(() => {
    void refresh().catch(value => setError(value instanceof Error ? value.message : String(value)));
  }, [refresh]);

  const action = async (kind: "analysis" | "replay" | "challengers") => {
    setBusy(kind); setError(null);
    try {
      if (kind === "analysis") {
        const created = await apiPost<OptimizationRun>("/profile-intelligence/score-intelligence/global-analysis", {
          lookback_days: lookbackDays,
          idempotency_key: `pi-score-ui:${crypto.randomUUID()}`,
        });
        setRun(await apiGet<OptimizationRun>(`/profile-intelligence/score-intelligence/optimization-runs/${created.id}`));
      } else if (run) {
        await apiPost(`/profile-intelligence/score-intelligence/optimization-runs/${run.id}/${kind === "replay" ? "replay" : "challengers"}`, {});
        setRun(await apiGet<OptimizationRun>(`/profile-intelligence/score-intelligence/optimization-runs/${run.id}`));
      }
      await refresh();
    } catch (value) { setError(value instanceof Error ? value.message : String(value)); }
    finally { setBusy(null); }
  };

  const download = () => {
    if (!run?.adjustment_envelope) return;
    const blob = new Blob([JSON.stringify(run.adjustment_envelope, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = `profile-score-adjustments-${run.id}.json`; link.click();
    URL.revokeObjectURL(url);
  };

  const curve = useMemo(() => {
    const byDate = new Map<string, Record<string, string | number | null>>();
    for (const point of performance) {
      const item = byDate.get(point.metric_date) || { date: point.metric_date };
      item[`${point.variant}_tp`] = point.tp_rate == null ? null : point.tp_rate * 100;
      item[`${point.variant}_sl`] = point.sl_rate == null ? null : point.sl_rate * 100;
      item[`${point.variant}_rapid_sl`] = point.rapid_sl_rate == null ? null : point.rapid_sl_rate * 100;
      byDate.set(point.metric_date, item);
    }
    return Array.from(byDate.values());
  }, [performance]);

  const quadrants = overview?.quadrants || {};
  const quadrantLabels: Record<string, string> = {
    approved_tp: "Aprovados → TP", approved_sl: "Aprovados → SL",
    rejected_rapid_sl: "Rejeitados → SL rápido", rejected_tp: "Rejeitados → TP",
  };
  return <section className="space-y-4">
    <div className="card border-violet-400/20 bg-violet-400/[.025] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2 text-[13px] font-semibold"><Brain className="h-4 w-4 text-violet-300" />Diagnóstico global de todos os profiles</div><p className="mt-1 text-[10px] text-[var(--text-tertiary)]">L1_SPECTRUM + L3 + L3_LAB + L3_REJECTED · propostas limitadas · replay e challenger obrigatórios.</p></div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" disabled={!!busy} onClick={() => void action("analysis")}>{busy === "analysis" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Brain className="h-3.5 w-3.5" />} Analisar com IA</button>
          <button className="btn btn-secondary" disabled={!run || !!busy || run.status === "AI_FAILED" || Boolean(run.challengers?.length)} onClick={() => void action("replay")}><Play className="h-3.5 w-3.5" /> Replay point-in-time</button>
          <button className="btn btn-secondary" disabled={!run?.replays?.some(item => item.status === "REPLAY_READY") || !!busy} onClick={() => void action("challengers")}><ShieldCheck className="h-3.5 w-3.5" /> Criar challengers shadow</button>
          <button className="btn btn-secondary" disabled={!run?.adjustment_envelope} onClick={download}><Download className="h-3.5 w-3.5" /> JSON</button>
        </div>
      </div>
      {overview && <><div className="mt-4 grid gap-2 md:grid-cols-4">{Object.entries(quadrants).map(([key,value]) => <div key={key} className="rounded border border-white/10 p-3"><div className="text-[9px] uppercase text-slate-500">{quadrantLabels[key] || key}</div><div className="mt-1 font-mono text-[15px]">{value.closed}</div><div className="mt-1 text-[9px] text-slate-400">TP {value.tp} · SL {value.sl} · símbolos {value.distinct_symbols} · dias {value.distinct_days}</div></div>)}</div><div className="mt-2 text-[9px] font-mono text-slate-500">{overview.dataset_contract} · cutoff {overview.cutoff_at} · rows {overview.row_count}{overview.truncated ? " · TRUNCATED" : ""} · profiles {overview.profiles.length}</div></>}
      {run?.executive_report && <div className="mt-4 rounded border border-violet-400/20 bg-black/20 p-4"><div className="text-[9px] uppercase tracking-widest text-violet-300">Relatório executivo · {run.model || "IA configurada"}</div><p className="mt-2 text-[12px] leading-5">{run.executive_report.executive_summary}</p><div className="mt-3 grid gap-3 lg:grid-cols-2"><div>{(run.executive_report.global_diagnosis || []).map((item,index) => <div key={index} className="mt-1 text-[10px] text-slate-300">• {item}</div>)}</div><div>{(run.executive_report.profile_recommendations || []).map(item => <div key={item.profile_id} className="mb-2 rounded border border-white/10 p-2 text-[10px]"><span className="font-mono text-cyan-300">{item.profile_id.slice(0,8)}…</span> · {item.diagnosis}<div className="mt-1 text-slate-500">{item.selected_candidate_ids.length} ajustes selecionados</div></div>)}</div></div></div>}
      {run?.replays?.length ? <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">{run.replays.map(item => <div key={item.id} className={`rounded border p-3 text-[10px] ${item.status === "REPLAY_READY" ? "border-green-500/30 bg-green-500/5" : "border-yellow-500/30 bg-yellow-500/5"}`}><div className="font-semibold">{item.status} · {item.profile_id.slice(0,8)}…</div><div className="mt-1 font-mono">SL evitados {String(item.delta_metrics.prevented_sl ?? "—")} · TP perdidos {String(item.delta_metrics.lost_tp ?? "—")}</div><div className="mt-1">retenção {pct(item.delta_metrics.volume_retention as number | null)} · redução SL {pct(item.delta_metrics.sl_reduction_rate as number | null)}</div></div>)}</div> : null}
      {run?.challengers?.length ? <div className="mt-3 text-[10px] text-cyan-200">{run.challengers.length} challenger(s): {run.challengers.map(item => `${item.status} ${item.profile_id.slice(0,8)}…`).join(" · ")}</div> : null}
      {error && <div className="mt-3 text-[10px] text-red-300">ERROR · {error}</div>}
    </div>
    <div className="card p-4"><div><h3 className="text-[13px] font-semibold">Evolução após as mudanças · Champion × Challenger</h3><p className="text-[10px] text-[var(--text-tertiary)]">TP%, SL% e SL rápido% por data; somente trades shadow fechados das versões pareadas.</p></div><div className="mt-3 h-72">{curve.length ? <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 800, height: 288 }}><LineChart data={curve}><CartesianGrid strokeDasharray="3 3" stroke="#ffffff12" /><XAxis dataKey="date" tick={{ fontSize: 9 }} /><YAxis unit="%" tick={{ fontSize: 9 }} /><Tooltip /><Legend /><Line type="monotone" dataKey="champion_tp" name="Champion TP" stroke="#38bdf8" /><Line type="monotone" dataKey="challenger_tp" name="Challenger TP" stroke="#22c55e" /><Line type="monotone" dataKey="champion_sl" name="Champion SL" stroke="#fb7185" strokeDasharray="4 3" /><Line type="monotone" dataKey="challenger_sl" name="Challenger SL" stroke="#f97316" /><Line type="monotone" dataKey="challenger_rapid_sl" name="Challenger SL rápido" stroke="#eab308" /></LineChart></ResponsiveContainer> : <div className="flex h-full items-center justify-center text-[11px] text-[var(--text-tertiary)]">COLLECTION_IN_PROGRESS · o gráfico aparecerá após os primeiros pares fechados.</div>}</div></div>
  </section>;
}

function scopeQuery(scope: Scope | undefined, lookbackDays: number) {
  const params = new URLSearchParams({ lookback_days: String(lookbackDays) });
  if (scope) {
    params.set("source", scope.source); params.set("profile_id", scope.profile_id);
    params.set("profile_version_id", scope.profile_version_id);
    params.set("score_engine_version_id", scope.score_engine_version_id);
    if (scope.timeframe) params.set("timeframe", scope.timeframe);
  }
  return params;
}

export function ScoreIntelligenceOverviewCard({ data, onOpen }: { data: ScoreOverview | null; onOpen: () => void }) {
  if (!data || data.status === "EMPTY") return <div className="card p-4"><div className="text-[13px] font-semibold">Score Intelligence — TP × SL</div><div className="mt-2 text-[11px] text-[var(--text-tertiary)]">EMPTY · nenhum escopo point-in-time fechado disponível.</div></div>;
  const strongest = data.summary?.strongest_separation;
  const weakest = data.summary?.weakest_separation;
  const discriminatory = data.summary?.most_discriminatory_threshold;
  const scope = data.scope;
  return <div className="card border-cyan-400/20 bg-cyan-400/[.025] p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><div className="flex items-center gap-2 text-[13px] font-semibold"><BarChart3 className="h-4 w-4 text-cyan-300" />Score Intelligence — TP × SL</div><div className="mt-1 text-[10px] text-[var(--text-tertiary)]">{data.status} · point-in-time · associação observacional</div></div>
      <button className="btn btn-secondary text-[11px]" onClick={onOpen}>Abrir análise completa dos scores</button>
    </div>
    <div className="mt-3 grid gap-2 md:grid-cols-4 xl:grid-cols-8">
      <div title="Diferença entre médias TP e SL" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Maior separação</div><div className="mt-1 font-mono text-cyan-200">{strongest ? `${SCORE_LABELS[strongest.score]} ${num(strongest.delta_mean_tp_sl)}` : "—"}</div></div>
      <div title="Menor separação absoluta entre TP e SL" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Menor separação</div><div className="mt-1 font-mono">{weakest ? `${SCORE_LABELS[weakest.score]} ${num(weakest.delta_mean_tp_sl)}` : "—"}</div></div>
      <div title="Threshold persistido na versão exata do Score Engine" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Mais permissivo</div><div className="mt-1 font-mono">{data.summary?.most_permissive_threshold ? `${data.summary.most_permissive_threshold.name} ≥ ${data.summary.most_permissive_threshold.threshold}` : "—"}</div></div>
      <div title="Maior lift entre os thresholds persistidos" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Mais discriminatório</div><div className="mt-1 font-mono">{discriminatory ? `${discriminatory.name} ≥ ${discriminatory.threshold} · ${num(discriminatory.lift)}x` : "—"}</div></div>
      <div title="Somente trades fechados; OPEN/NOT_MATURED não entram" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Amostra / cobertura</div><div className="mt-1 font-mono">{data.closed_trades ?? 0} · {pct(data.summary?.coverage ?? null)}</div></div>
      <div title="Janela pedida e período efetivamente coberto pelo escopo" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Período efetivo</div><div className="mt-1 font-mono">{data.lookback_days ?? 0}d · {scope?.effective_from?.slice(0, 10) || "—"} → {scope?.effective_to?.slice(0, 10) || "—"}</div></div>
      <div title="Versão exata do profile" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Profile version</div><div className="mt-1 font-mono">{scope ? `v${scope.version_number} · ${scope.profile_version_id.slice(0, 8)}…` : "—"}</div></div>
      <div title="Versão exata do Score Engine" className="rounded border border-white/10 p-2"><div className="text-[9px] uppercase text-slate-500">Score Engine</div><div className="mt-1 font-mono">{scope ? `${scope.score_engine_version_id.slice(0, 8)}…` : "—"}</div></div>
    </div>
  </div>;
}

export default function ScoreIntelligencePanel({ initialData, onCreateManual }: { initialData?: ScoreOverview | null; onCreateManual: (stat: IndicatorEvidence, prefill: ManualAdjustmentPrefill) => void }) {
  const [data, setData] = useState<ScoreOverview | null>(initialData || null);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState<string | null>(null);
  const [lookbackDays, setLookbackDays] = useState(30);
  const [selectedScopeKey, setSelectedScopeKey] = useState("");
  const [selectedScore, setSelectedScore] = useState("score");
  const [bucketMode, setBucketMode] = useState("fixed");
  const [distributionData, setDistributionData] = useState<DistributionResponse | null>(null);
  const [simulationThreshold, setSimulationThreshold] = useState("65");
  const [simulation, setSimulation] = useState<SimulationResponse | null>(null);
  const [comparison, setComparison] = useState<VersionComparison | null>(null);
  const [recommendationState, setRecommendationState] = useState<"ACTIVE" | "OBSERVING" | "IGNORED">("ACTIVE");
  const selectedScope = useMemo(() => data?.available_scopes?.find(scope => `${scope.source}:${scope.profile_version_id}:${scope.score_engine_version_id}` === selectedScopeKey) || data?.scope, [data, selectedScopeKey]);

  const load = useCallback(async (scope?: Scope) => {
    setLoading(true); setError(null);
    try {
      const result = await apiGet<ScoreOverview>(`/profile-intelligence/score-intelligence/overview?${scopeQuery(scope, lookbackDays)}`);
      setData(result);
      const effective = result.scope;
      if (effective) setSelectedScopeKey(`${effective.source}:${effective.profile_version_id}:${effective.score_engine_version_id}`);
      const firstCovered = result.score_statistics?.find(item => item.present > 0);
      if (firstCovered) setSelectedScore(firstCovered.score);
    } catch (value) { setError(value instanceof Error ? value.message : String(value)); }
    finally { setLoading(false); }
  }, [lookbackDays]);

  useEffect(() => {
    if (initialData) return;
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [initialData, load]);
  useEffect(() => {
    if (!selectedScope || !selectedScore) return;
    const params = scopeQuery(selectedScope, lookbackDays); params.set("score", selectedScore); params.set("bucket_mode", bucketMode);
    void Promise.all([
      apiGet<DistributionResponse>(`/profile-intelligence/score-intelligence/distribution?${params}`).catch(() => null),
      apiGet<VersionComparison>(`/profile-intelligence/score-intelligence/version-comparison?lookback_days=90&source=${encodeURIComponent(selectedScope.source)}&profile_id=${selectedScope.profile_id}`).catch(() => null),
    ]).then(([dist, version]) => { setDistributionData(dist); setComparison(version); });
  }, [selectedScope, selectedScore, bucketMode, lookbackDays]);

  const runSimulation = async () => {
    if (!selectedScope) return;
    const threshold = Number(simulationThreshold);
    if (!Number.isFinite(threshold)) { setError("Threshold inválido."); return; }
    setError(null);
    try {
      setSimulation(await apiPost<SimulationResponse>("/profile-intelligence/score-intelligence/simulate-threshold", {
        score: selectedScore, threshold, lookback_days: lookbackDays, source: selectedScope.source,
        profile_id: selectedScope.profile_id, profile_version_id: selectedScope.profile_version_id,
        score_engine_version_id: selectedScope.score_engine_version_id, timeframe: selectedScope.timeframe || null,
      }));
    } catch (value) { setError(value instanceof Error ? value.message : String(value)); }
  };

  const openManual = () => {
    const recommendation = data?.recommendation;
    if (!recommendation || !selectedScope) return;
    const action = recommendation.action === "UPDATE_SCORE_THRESHOLD" ? "UPDATE_SCORE_THRESHOLD" : "OBSERVE_ONLY";
    onCreateManual({
      indicator: recommendation.score, bucket_label: `threshold >= ${recommendation.proposed_threshold}`,
      total_cases: recommendation.effect.passed.trades, wins: recommendation.effect.passed.tp,
      losses: recommendation.effect.passed.sl, role_detected: "score_intelligence",
      validation_status: data.status === "READY" ? "validated" : "insufficient_sample",
      actionability_status: "manual_review_only", associated_profiles: [{ id: selectedScope.profile_id, name: selectedScope.profile_name }],
      evidence_json: { source: selectedScope.source, profile_id: selectedScope.profile_id, profile_version_id: selectedScope.profile_version_id, score_engine_version_id: selectedScope.score_engine_version_id, profile_config_hash: selectedScope.profile_config_hash, score_engine_config_hash: selectedScope.score_engine_config_hash, recommendation, dataset: data.dataset, association_not_causation: true },
    }, {
      profileId: selectedScope.profile_id, action,
      path: action === "UPDATE_SCORE_THRESHOLD" ? "/scoring/thresholds/buy" : undefined,
      currentValue: recommendation.current_threshold, proposedValue: recommendation.proposed_threshold,
      suggestedJustification: `Revisão manual Score Intelligence: ${recommendation.score} no escopo ${selectedScope.source}, profile_version ${selectedScope.profile_version_id}, score_engine_version ${selectedScope.score_engine_version_id}. Associação observacional; validar risco antes de aplicar.`,
    });
  };

  if (loading && !data) return <div className="card p-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin" /><div className="mt-2 text-[11px] text-[var(--text-tertiary)]">LOADING</div></div>;
  if (error && !data) return <div className="card border-red-500/30 p-4 text-red-300">ERROR · {error}</div>;
  if (!data || data.status === "EMPTY") return <div className="card p-8 text-center text-[12px] text-[var(--text-tertiary)]">EMPTY · Nenhum trade fechado point-in-time para o escopo.</div>;
  const stats = data.score_statistics || [];
  const buckets = distributionData?.distribution?.buckets || [];

  return <div className="space-y-4">
    <div className="rounded-xl border border-cyan-400/20 bg-[#071217] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="font-mono text-[10px] uppercase tracking-[.25em] text-cyan-300">Score Intelligence · TP × SL × TIMEOUT</div><h2 className="mt-1 text-[16px] font-semibold">Scores de entrada, sem recálculo histórico</h2><p className="mt-1 text-[11px] text-slate-400">Associação observacional; não representa causalidade comprovada. Nenhum resultado alimenta ML.</p></div><span title="Estados: READY, INSUFFICIENT_SAMPLE, PARTIAL_COVERAGE, EMPTY, ERROR" className="rounded border border-green-500/30 bg-green-500/5 px-3 py-1 font-mono text-[10px] text-green-300">READ-ONLY</span></div>
      <div className="mt-4 grid gap-3 md:grid-cols-[1fr_120px_auto]">
        <select className="input" value={selectedScopeKey} onChange={event => { const scope = data.available_scopes?.find(item => `${item.source}:${item.profile_version_id}:${item.score_engine_version_id}` === event.target.value); setSelectedScopeKey(event.target.value); if (scope) void load(scope); }}>
          {(data.available_scopes || []).map(scope => <option key={`${scope.source}:${scope.profile_version_id}:${scope.score_engine_version_id}`} value={`${scope.source}:${scope.profile_version_id}:${scope.score_engine_version_id}`}>{scope.source} · {scope.profile_name} · profile v{scope.version_number} · {scope.timeframe || "—"}</option>)}
        </select>
        <select className="input" value={lookbackDays} onChange={event => setLookbackDays(Number(event.target.value))}>{[30, 60, 90, 180].map(days => <option key={days} value={days}>{days} dias</option>)}</select>
        <button className="btn btn-secondary" onClick={() => void load(selectedScope)}>Atualizar</button>
      </div>
      {selectedScope && <div className="mt-3 grid gap-2 md:grid-cols-4 text-[9px] font-mono text-slate-400"><div>source {selectedScope.source}</div><div>profile_version {selectedScope.profile_version_id.slice(0, 8)}…</div><div>score_engine {selectedScope.score_engine_version_id.slice(0, 8)}…</div><div>{selectedScope.effective_from?.slice(0, 10)} → {selectedScope.effective_to?.slice(0, 10)}</div></div>}
    </div>

    {data.status !== "READY" && <div className="flex gap-2 rounded border border-yellow-500/30 bg-yellow-500/5 p-3 text-[11px] text-yellow-200"><AlertTriangle className="h-4 w-4 shrink-0" /><div>{data.status}. Resultados permanecem observacionais; somente OBSERVE_ONLY é recomendado.</div></div>}

    <GlobalOptimizationPanel lookbackDays={lookbackDays} />

    <section className="card overflow-hidden"><div className="border-b border-[var(--border-default)] p-4"><h3 className="text-[13px] font-semibold">TP × SL × TIMEOUT</h3><p className="text-[10px] text-[var(--text-tertiary)]">Null permanece ausente; percentis são calculados sobre valores presentes.</p></div><div className="overflow-x-auto"><table className="w-full text-[10px]"><thead><tr className="border-b border-[var(--border-subtle)]">{["Score","TP N","TP Min","TP P25","TP Med","TP Média","TP P75","TP P90","TP Max","SL N","SL Min","SL P25","SL Med","SL Média","SL P75","SL P90","SL Max","TIMEOUT N","TIMEOUT Média","Δ média","Δ mediana","Missing","Cobertura"].map(label => <th key={label} className="whitespace-nowrap px-3 py-2 text-left uppercase text-[var(--text-tertiary)]">{label}</th>)}</tr></thead><tbody className="divide-y divide-[var(--border-subtle)]">{stats.map(item => <tr key={item.score}><td className="px-3 py-2"><div className="font-semibold">{SCORE_LABELS[item.score]}</div><div className="font-mono text-[8px] text-[var(--text-tertiary)]">{item.origin}</div></td>{[item.tp.n,item.tp.min,item.tp.p25,item.tp.median,item.tp.mean,item.tp.p75,item.tp.p90,item.tp.max,item.sl.n,item.sl.min,item.sl.p25,item.sl.median,item.sl.mean,item.sl.p75,item.sl.p90,item.sl.max,item.timeout.n,item.timeout.mean,item.delta_mean_tp_sl,item.delta_median_tp_sl,item.missing].map((value,index) => <td key={index} className="px-3 py-2 font-mono">{typeof value === "number" ? ([0,8,16,20].includes(index) ? value : value.toFixed(2)) : "—"}</td>)}<td className="px-3 py-2">{pct(item.coverage)}</td></tr>)}</tbody></table></div></section>

    <section className="grid gap-4 lg:grid-cols-2"><div className="card p-4"><h3 className="text-[13px] font-semibold">Poder discriminatório</h3><div className="mt-3 space-y-2">{stats.map(item => <div key={item.score} className="grid grid-cols-[1fr_repeat(4,78px)] gap-2 rounded border border-[var(--border-subtle)] p-2 text-[10px]"><span>{SCORE_LABELS[item.score]}</span><span title="TP mean - SL mean">Δ {num(item.delta_mean_tp_sl)}</span><span title="Área sob a curva individual; maior score tratado como TP">AUC {num(item.auc,3)}</span><span title="Confiança definida por amostra TP/SL e cobertura">{item.confidence}</span><span title="Valores presentes">N {item.present}</span></div>)}</div></div><div className="card p-4"><h3 className="text-[13px] font-semibold">Thresholds atuais</h3><p className="mt-1 text-[10px] text-[var(--text-tertiary)]">Origem: versão exata do Score Engine. Win rate dos aprovados representa a precisão observada.</p><div className="mt-3 space-y-2">{(data.current_thresholds || []).map(item => <div key={item.name} className="rounded border border-[var(--border-subtle)] p-3 text-[10px]"><div className="flex justify-between"><span className="font-semibold">Score {item.name} ≥ {item.threshold}</span><span>{item.passed.trades} aprovados · pass rate {pct(item.pass_rate)}</span></div><div className="mt-1 grid grid-cols-5 gap-2 text-[var(--text-secondary)]"><span>TP {item.passed.tp}</span><span>SL {item.passed.sl}</span><span>TIMEOUT {item.passed.timeout}</span><span>Precision {pct(item.passed.win_rate)}</span><span>WR {pct(item.passed.win_rate)}</span></div><div className="mt-1">Lift {num(item.lift)} · redução {pct(item.volume_reduction)} · P&L {num(item.passed.avg_pnl_pct)}%</div></div>)}{!data.current_thresholds?.length && <div className="text-[11px] text-[var(--text-tertiary)]">Nenhum threshold numérico persistido para este score engine.</div>}</div></div></section>

    <section className="card p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-[13px] font-semibold">Distribuição por faixas</h3><p className="text-[10px] text-[var(--text-tertiary)]">Buckets determinísticos e exclusivos do Profile Intelligence; não persistidos no ML.</p></div><div className="flex gap-2"><select className="input" value={selectedScore} onChange={event => setSelectedScore(event.target.value)}>{stats.filter(item => item.present > 0).map(item => <option key={item.score} value={item.score}>{SCORE_LABELS[item.score]}</option>)}</select><select className="input" value={bucketMode} onChange={event => setBucketMode(event.target.value)}><option value="fixed">Faixas fixas</option><option value="quantile">Quantis</option><option value="current_threshold">Threshold atual</option></select></div></div><div className="mt-3 overflow-x-auto"><table className="w-full text-[10px]"><thead><tr>{["Faixa","Trades","TP","SL","TIMEOUT","Win Rate","Avg P&L","Avg MAE","Avg MFE","Holding médio"].map(label => <th key={label} className="px-2 py-2 text-left uppercase text-[var(--text-tertiary)]">{label}</th>)}</tr></thead><tbody>{buckets.map((bucket,index) => <tr key={index} className="border-t border-[var(--border-subtle)]"><td className="px-2 py-2 font-mono">{num(bucket.lower)}–{num(bucket.upper)}</td><td>{bucket.trades}</td><td>{bucket.tp}</td><td>{bucket.sl}</td><td>{bucket.timeout}</td><td>{pct(bucket.win_rate)}</td><td>{num(bucket.avg_pnl_pct)}%</td><td>{num(bucket.avg_mae_pct)}%</td><td>{num(bucket.avg_mfe_pct)}%</td><td>{num(bucket.avg_holding_seconds,0)}s</td></tr>)}</tbody></table></div></section>

    <section className="card p-4"><div className="flex items-center gap-2"><SlidersHorizontal className="h-4 w-4 text-blue-300" /><h3 className="text-[13px] font-semibold">Simulador read-only</h3></div><div className="mt-3 flex flex-wrap gap-2"><select className="input" value={selectedScore} onChange={event => setSelectedScore(event.target.value)}>{stats.filter(item => item.present > 0).map(item => <option key={item.score} value={item.score}>{SCORE_LABELS[item.score]}</option>)}</select><input className="input w-32" value={simulationThreshold} onChange={event => setSimulationThreshold(event.target.value)} inputMode="decimal" /><button className="btn btn-primary" onClick={() => void runSimulation()}>Simular</button></div>{simulation?.simulation && <><div className="mt-4 grid gap-2 md:grid-cols-4">{[["Aprovados",simulation.simulation.passed.trades],["Eliminados",simulation.simulation.eliminated_trades],["TP / SL / T",`${simulation.simulation.passed.tp} / ${simulation.simulation.passed.sl} / ${simulation.simulation.passed.timeout}`],["Win rate",pct(simulation.simulation.passed.win_rate)],["Avg P&L",`${num(simulation.simulation.passed.avg_pnl_pct)}%`],["P&L acumulado",`${num(simulation.simulation.passed.pnl_sum_pct ?? null)}%`],["Avg MAE",`${num(simulation.simulation.passed.avg_mae_pct ?? null)}%`],["Avg MFE",`${num(simulation.simulation.passed.avg_mfe_pct ?? null)}%`],["Redução",pct(simulation.simulation.volume_reduction)],["Holding",`${num(simulation.simulation.passed.avg_holding_seconds ?? null,0)}s`]].map(([label,value]) => <div key={String(label)} className="rounded border border-[var(--border-subtle)] p-2"><div className="text-[9px] uppercase text-[var(--text-tertiary)]">{label}</div><div className="mt-1 font-mono">{String(value)}</div></div>)}</div><div className="mt-3 rounded border border-cyan-500/20 bg-cyan-500/5 p-3 text-[10px] text-cyan-100">{simulation.difference_vs_current ? <>Diferença vs threshold atual: threshold {num(simulation.difference_vs_current.threshold_delta)} · trades {num(simulation.difference_vs_current.passed_trades_delta,0)} · WR {pct(simulation.difference_vs_current.win_rate_delta)} · Avg P&amp;L {num(simulation.difference_vs_current.avg_pnl_pct_delta)}% · redução {pct(simulation.difference_vs_current.volume_reduction_delta)}</> : <>Diferença vs threshold atual indisponível: não existe threshold persistido para este componente na versão selecionada.</>}</div></>}</section>

    {data.recommendation && recommendationState !== "IGNORED" && <section className="card border-violet-400/20 bg-violet-400/[.025] p-4"><div className="flex items-center gap-2"><Brain className="h-4 w-4 text-violet-300" /><h3 className="text-[13px] font-semibold">Recomendação informativa</h3></div><div className="mt-3 grid gap-2 md:grid-cols-4 text-[11px]"><div>Profile <strong>{selectedScope?.profile_name || "—"}</strong></div><div>Profile version <strong>{selectedScope?.profile_version_id.slice(0,8) || "—"}…</strong></div><div>Score Engine <strong>{selectedScope?.score_engine_version_id.slice(0,8) || "—"}…</strong></div><div>Source / período <strong>{selectedScope?.source || "—"} · {data.lookback_days}d</strong></div><div>Score <strong>{SCORE_LABELS[data.recommendation.score]}</strong></div><div>Atual <strong>{data.recommendation.current_threshold ?? "—"}</strong></div><div>Faixa observada <strong>≥ {data.recommendation.proposed_threshold}</strong></div><div>Confiança <strong>{data.recommendation.confidence}</strong></div><div>Amostra <strong>{data.closed_trades}</strong></div><div>WR observado <strong>{pct(data.recommendation.effect.passed.win_rate)}</strong></div><div>P&amp;L médio <strong>{num(data.recommendation.effect.passed.avg_pnl_pct)}%</strong></div><div>Redução <strong>{pct(data.recommendation.effect.volume_reduction)}</strong></div><div>Missing <strong>{pct(data.recommendation.missing_rate)}</strong></div><div>Risco <strong>{data.recommendation.risk}</strong></div><div>Concentração símbolo <strong>{pct(data.recommendation.concentration?.max_single_symbol_share ?? null)}</strong></div><div>TP/SL/T <strong>{data.recommendation.outcomes?.TP_HIT || 0}/{data.recommendation.outcomes?.SL_HIT || 0}/{data.recommendation.outcomes?.TIMEOUT || 0}</strong></div></div><div className="mt-3 flex flex-wrap gap-2"><button className="btn btn-primary" onClick={openManual}>Criar ajuste manual</button><button className="btn btn-secondary" onClick={() => setRecommendationState("OBSERVING")}><Eye className="h-3.5 w-3.5" /> Observar</button><button className="btn btn-secondary" onClick={() => setRecommendationState("IGNORED")}>Ignorar recomendação</button></div>{recommendationState === "OBSERVING" && <div className="mt-2 text-[10px] text-cyan-300">OBSERVE_ONLY · nenhuma alteração foi aplicada.</div>}</section>}

    <section className="card p-4"><h3 className="text-[13px] font-semibold">Comparação por versão</h3>{comparison?.current && comparison?.previous ? <div className="mt-3 grid gap-3 md:grid-cols-2">{[["Versão atual",comparison.current],["Versão anterior",comparison.previous]].map(([label,item]) => { const typed=item as {scope:Scope;metrics:Record<string,Num>}; return <div key={String(label)} className="rounded border border-[var(--border-subtle)] p-3 text-[10px]"><div className="font-semibold">{String(label)} · v{typed.scope.version_number}</div><div className="mt-2 grid grid-cols-2 gap-1"><span>Trades {typed.metrics.trades}</span><span>WR {pct(typed.metrics.win_rate)}</span><span>TP {typed.metrics.tp}</span><span>SL {typed.metrics.sl}</span><span>TIMEOUT {typed.metrics.timeout}</span><span>Avg P&amp;L {num(typed.metrics.avg_pnl_pct)}%</span><span>P&amp;L acumulado {num(typed.metrics.pnl_sum_pct)}%</span><span>MAE {num(typed.metrics.avg_mae_pct)}%</span><span>MFE {num(typed.metrics.avg_mfe_pct)}%</span><span>Holding {num(typed.metrics.avg_holding_seconds,0)}s</span><span>Score engine {typed.scope.score_engine_version_id.slice(0,8)}…</span></div></div>;})}</div> : <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">{comparison?.status || "COLLECTING"} · ainda não há duas versões comparáveis no mesmo profile/source.</div>}</section>

    <section className="card p-4"><h3 className="text-[13px] font-semibold">Evidência técnica</h3><div className="mt-2 grid gap-2 md:grid-cols-3 text-[10px] text-[var(--text-secondary)]"><div>Dataset: {data.dataset}</div><div>Closed: {data.closed_trades}</div><div>TP/SL/TIMEOUT: {data.outcomes?.TP_HIT || 0}/{data.outcomes?.SL_HIT || 0}/{data.outcomes?.TIMEOUT || 0}</div><div title="Valores preenchidos / total">Cobertura: {pct(data.summary?.coverage ?? null)}</div><div title="Point-in-time significa score capturado na entrada">Point-in-time: preservado</div><div>ML mutation: nenhuma</div></div><div className="mt-3 overflow-x-auto"><table className="w-full text-[10px]"><thead><tr>{["Score","N","Missing","Effect size","KS","P&L corr","Direção"].map(label => <th key={label} className="px-2 py-2 text-left uppercase text-[var(--text-tertiary)]">{label}</th>)}</tr></thead><tbody>{stats.map(item => <tr key={item.score} className="border-t border-[var(--border-subtle)]"><td className="px-2 py-2">{SCORE_LABELS[item.score]}</td><td>{item.present}</td><td>{item.missing}</td><td>{num(item.standardized_effect_size,3)}</td><td title="Kolmogorov-Smirnov">{num(item.ks_statistic,3)}</td><td>{num(item.pnl_correlation,3)}</td><td>{item.direction || "—"}</td></tr>)}</tbody></table></div>{error && <div className="mt-3 text-red-300">ERROR · {error}</div>}</section>
  </div>;
}
