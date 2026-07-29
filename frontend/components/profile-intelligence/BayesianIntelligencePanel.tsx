"use client";

import {
  type ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  Activity,
  AlertTriangle,
  Atom,
  Braces,
  CheckCircle2,
  Clock3,
  Database,
  FileJson2,
  Fingerprint,
  FlaskConical,
  Layers3,
  LoaderCircle,
  LockKeyhole,
  Play,
  Power,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import {
  ALL_PROFILES_VALUE,
  analysisTargets,
  deduplicateProfileOptions,
  type BayesianProfileOption,
} from "@/lib/profile-bayesian-batch";

type ModuleStatus = {
  flags: Record<string, boolean>;
  authority: Record<string, boolean | string>;
  policy_configured: boolean;
  policy_error?: string | null;
  policy?: {
    policy_version?: string;
    mode?: string;
    max_trades?: number;
    max_runtime_seconds?: number;
    diagnostic_gates?: {
      max_rhat?: number;
      min_mcmc_effective_sample_size?: number;
      max_divergences?: number;
    };
    sampler_config?: Record<string, number>;
    split_config?: Record<string, unknown>;
    population_config?: Record<string, unknown>;
    bayesian_model?: Record<string, unknown>;
    permissions?: Record<string, boolean>;
  } | null;
  activation?: {
    template_id: string;
    mode: string;
    can_activate: boolean;
    can_upgrade?: boolean;
  };
  replay: { supported: boolean; reason: string };
  dependencies: Record<string, string | null>;
};

type PolicyResponse = {
  configured: boolean;
  data?: Record<string, unknown> | null;
  error?: string;
};

type ProfileRankingResponse = {
  profiles?: BayesianProfileOption[];
};

type BatchRequestState = {
  status: "submitting" | "submitted" | "partial";
  total: number;
  accepted: number;
  failed: number;
  currentProfile?: string;
  failures: string[];
};

type ConsolidatedIndicator = {
  indicator: string;
  regime?: string | null;
  profiles_included: number;
  total_direct_sample_size: number;
  direction_counts: {
    POSITIVE: number;
    NEGATIVE: number;
    NEUTRAL: number;
  };
  consensus_direction: string;
  weighted_tp_lift?: number | null;
  weighted_pnl_lift?: number | null;
  weighted_probability_positive?: number | null;
  highest_evidence_grade: string;
};

type BatchAnalysis = {
  batch_id: string;
  legacy_batch: boolean;
  status: string;
  progress: number;
  counts: {
    total: number;
    terminal: number;
    pending: number;
    active: number;
    valid: number;
    warnings: number;
    not_converged: number;
    failed: number;
  };
  profile_runs: Array<{
    id: string;
    profile_id: string;
    profile_name: string;
    status: string;
    diagnostic_status?: string | null;
    row_count?: number | null;
    error_message?: string | null;
  }>;
  report: {
    status: "PARTIAL" | "FINAL";
    eligible_profiles: number;
    excluded_profiles: number;
    indicator_count: number;
    indicators: ConsolidatedIndicator[];
    direction_summary: {
      POSITIVE: number;
      NEGATIVE: number;
      NEUTRAL: number;
      MIXED: number;
    };
    evidence_summary: {
      INSUFFICIENT: number;
      WEAK: number;
      MODERATE: number;
      STRONG: number;
      VERY_STRONG: number;
    };
    methodology: string;
    language: "association_not_causation";
  };
};

type AnalysisRun = {
  id: string;
  status: string;
  diagnostic_status?: string | null;
  dataset_hash?: string | null;
  row_count?: number | null;
  window_from?: string | null;
  window_to?: string | null;
  warnings?: string[];
  error_message?: string | null;
  created_at?: string;
  finished_at?: string | null;
  manifest?: {
    temporal_split?: {
      effective_embargo_seconds?: number;
      counts?: {
        discovery?: number;
        validation?: number;
        final_holdout?: number;
      };
      final_holdout_used_for_fit?: boolean;
      final_holdout_used_for_grading?: boolean;
    };
    preflight?: {
      power_analysis?: {
        status?: string;
        minimum_detectable_net_ev_pct?: number;
        posterior_probability?: number;
        practical_rope_pct?: number;
      };
      maximum_plausible_edge_pct?: number;
      feature_quality?: {
        approved_for_both_windows?: string[];
        excluded_from_model?: string[];
      };
      outcome_counts?: {
        discovery?: Record<string, number>;
        validation?: Record<string, number>;
      };
    };
  } | null;
};

type BayesianDiagnostic = {
  id: string;
  analysis_run_id: string;
  model_name: string;
  status: string;
  rhat_max?: number | null;
  effective_sample_size_min?: number | null;
  divergences: number;
  posterior_predictive_check?: Record<string, unknown>;
  credible_intervals?: Record<string, unknown>;
  sampling_warnings?: string[];
  details?: Record<string, unknown>;
  created_at?: string;
};

type IndicatorEffect = {
  id: string;
  indicator: string;
  regime?: string | null;
  effect_direction: string;
  estimated_tp_lift?: number | null;
  estimated_pnl_lift?: number | null;
  probability_positive_effect?: number | null;
  credible_interval_95?: [number | null, number | null];
  direct_sample_size: number;
  shared_sample_size: number;
  effective_sample_size?: number | null;
  evidence_grade: string;
  diagnostic_status: string;
  recommendation: string;
};

type Candidate = {
  id: string;
  status: string;
  changes: Array<{
    target_path: string;
    current_value: unknown;
    candidate_value: unknown;
    justification: string;
  }>;
  created_at?: string;
};

type OptimizationStudy = {
  id: string;
  status: string;
  sampler: string;
  total_trials: number;
  valid_trials: number;
  directions: string[];
  warnings?: string[];
  error_message?: string | null;
  created_at?: string;
};

type AuditEvent = {
  id: string;
  event_type: string;
  previous_status?: string | null;
  new_status?: string | null;
  payload?: Record<string, unknown>;
  created_at?: string;
};

const terminalStatuses = new Set([
  "COMPLETED",
  "COMPLETED_WITH_WARNINGS",
  "FAILED",
  "CANCELLED",
]);

function fmtDate(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function fmtNumber(value?: number | null, digits = 2) {
  return value == null || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

function fmtPct(value?: number | null, digits = 1) {
  return value == null || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function statusTone(status?: string | null) {
  if (status === "VALID" || status === "COMPLETED") {
    return "border-emerald-400/30 bg-emerald-400/10 text-emerald-200";
  }
  if (status?.includes("WARNING") || status === "INSUFFICIENT_EVIDENCE") {
    return "border-amber-400/30 bg-amber-400/10 text-amber-100";
  }
  if (status === "FAILED" || status === "NOT_CONVERGED") {
    return "border-rose-400/30 bg-rose-400/10 text-rose-200";
  }
  return "border-cyan-400/20 bg-cyan-400/8 text-cyan-100";
}

function StatusPill({ value }: { value?: string | null }) {
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-1 font-mono text-[10px] tracking-[0.12em] ${statusTone(
        value,
      )}`}
    >
      {value || "SEM EXECUÇÃO"}
    </span>
  );
}

function diagnosticModelLabel(modelName: string) {
  if (modelName === "tp_probability") return "Probabilidade de TP";
  if (modelName === "net_pnl") return "PnL líquido";
  if (modelName === "outcome_discovery")
    return "Outcome multinomial · discovery";
  if (modelName === "net_pnl_discovery")
    return "PnL condicional · discovery";
  if (modelName === "outcome_validation")
    return "Outcome multinomial · validation";
  if (modelName === "net_pnl_validation")
    return "PnL condicional · validation";
  if (modelName === "preflight") return "Pré-validação";
  return modelName.replaceAll("_", " ");
}

function diagnosticMetricTone(passed?: boolean) {
  if (passed === true) return "text-emerald-200";
  if (passed === false) return "text-rose-200";
  return "text-[var(--text-muted)]";
}

export default function BayesianIntelligencePanel() {
  const [moduleStatus, setModuleStatus] = useState<ModuleStatus | null>(null);
  const [profiles, setProfiles] = useState<BayesianProfileOption[]>([]);
  const [profileId, setProfileId] = useState("");
  const [analysisSelection, setAnalysisSelection] = useState("");
  const [batchRequest, setBatchRequest] = useState<BatchRequestState | null>(
    null,
  );
  const [batchAnalysis, setBatchAnalysis] = useState<BatchAnalysis | null>(null);
  const [latest, setLatest] = useState<AnalysisRun | null>(null);
  const [diagnostics, setDiagnostics] = useState<BayesianDiagnostic[]>([]);
  const [effects, setEffects] = useState<IndicatorEffect[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [studies, setStudies] = useState<OptimizationStudy[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [activatingPolicy, setActivatingPolicy] = useState(false);
  const [policyEditorOpen, setPolicyEditorOpen] = useState(false);
  const [policyDraft, setPolicyDraft] = useState("");
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [policyNotice, setPolicyNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const defaultWindow = useMemo(() => {
    const end = new Date();
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - 90);
    return {
      from: start.toISOString().slice(0, 10),
      to: end.toISOString().slice(0, 10),
    };
  }, []);
  const [windowFrom, setWindowFrom] = useState(defaultWindow.from);
  const [windowTo, setWindowTo] = useState(defaultWindow.to);
  const activeProfileName = useMemo(
    () =>
      profiles.find((profile) => profile.profile_id === profileId)
        ?.profile_name ?? "profile selecionado",
    [profileId, profiles],
  );

  const loadProfile = useCallback(async (selectedProfileId: string) => {
    if (!selectedProfileId) return;
    const [latestResult, candidatesResult, studiesResult, auditResult] =
      await Promise.all([
        apiGet(`/profile-intelligence/${selectedProfileId}/bayesian/latest`).catch(
          () => ({ item: null, diagnostics: [] }),
        ),
        apiGet(
          `/profile-intelligence/${selectedProfileId}/bayesian/candidates`,
        ).catch(() => ({ items: [] })),
        apiGet(
          `/profile-intelligence/${selectedProfileId}/optimization?limit=25`,
        ).catch(() => ({ items: [] })),
        apiGet(
          `/profile-intelligence/${selectedProfileId}/bayesian/audit?limit=100`,
        ).catch(() => ({ items: [] })),
      ]);
    const latestRun = latestResult?.item ?? null;
    const effectsResult = latestRun?.id
      ? await apiGet(
          `/profile-intelligence/${selectedProfileId}/bayesian/effects?analysis_run_id=${encodeURIComponent(
            latestRun.id,
          )}`,
        ).catch(() => ({ items: [] }))
      : { items: [] };

    setLatest(latestRun);
    setDiagnostics(latestResult?.diagnostics ?? []);
    setEffects(effectsResult?.items ?? []);
    setCandidates(candidatesResult?.items ?? []);
    setStudies(studiesResult?.items ?? []);
    setAuditEvents(auditResult?.items ?? []);
  }, []);

  const loadBatch = useCallback(async () => {
    const result = await apiGet<{ item: BatchAnalysis | null }>(
      "/profile-intelligence/bayesian/batches/latest",
    ).catch(() => ({ item: null }));
    setBatchAnalysis(result.item);
    return result.item;
  }, []);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResult, profilesResult, batchResult] =
        await Promise.allSettled([
          apiGet<ModuleStatus>("/profile-intelligence/bayesian/status"),
          apiGet<ProfileRankingResponse>(
            "/profile-intelligence/profiles/ranking?lookback_days=60&limit=100",
          ),
          apiGet<{ item: BatchAnalysis | null }>(
            "/profile-intelligence/bayesian/batches/latest",
          ),
        ]);

      if (statusResult.status === "rejected") {
        throw statusResult.reason;
      }

      setModuleStatus(statusResult.value);
      const rawItems =
        profilesResult.status === "fulfilled"
          ? profilesResult.value?.profiles ?? []
          : [];
      const items = deduplicateProfileOptions(rawItems);
      setProfiles(items);
      const latestBatch =
        batchResult.status === "fulfilled" ? batchResult.value.item : null;
      setBatchAnalysis(latestBatch);

      if (profilesResult.status === "rejected") {
        const rankingMessage =
          profilesResult.reason instanceof Error
            ? profilesResult.reason.message
            : "erro inesperado";
        setError(`Ranking de profiles indisponível: ${rankingMessage}`);
      }

      const selected = profileId || items[0]?.profile_id || "";
      setProfileId(selected);
      setAnalysisSelection((current) =>
        current ||
        (latestBatch?.status === "RUNNING" ? ALL_PROFILES_VALUE : selected),
      );
      await loadProfile(selected);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Não foi possível carregar Bayesian Intelligence.",
      );
    } finally {
      setLoading(false);
    }
  }, [loadProfile, profileId]);

  useEffect(() => {
    // Async loader synchronizes this client panel with the authenticated API.
    void loadInitial();
    // The first load intentionally selects the first available profile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!profileId) return;
    // Async loader synchronizes the selected profile with the API.
    void loadProfile(profileId);
  }, [loadProfile, profileId]);

  useEffect(() => {
    if (!profileId || !latest || terminalStatuses.has(latest.status)) return;
    const interval = window.setInterval(() => {
      void loadProfile(profileId);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [latest, loadProfile, profileId]);

  useEffect(() => {
    if (analysisSelection !== ALL_PROFILES_VALUE) return;
    void loadBatch();
  }, [analysisSelection, loadBatch]);

  useEffect(() => {
    if (batchAnalysis?.status !== "RUNNING") return;
    const interval = window.setInterval(() => {
      void loadBatch();
    }, 10000);
    return () => window.clearInterval(interval);
  }, [batchAnalysis?.status, loadBatch]);

  const canAnalyze =
    moduleStatus?.flags?.enabled === true &&
    moduleStatus?.flags?.analysis_enabled === true &&
    moduleStatus?.policy_configured === true &&
    analysisTargets(analysisSelection, profiles).length > 0;
  const diagnosticGates = moduleStatus?.policy?.diagnostic_gates;
  const isActiveRun = Boolean(
    latest && !terminalStatuses.has(latest.status),
  );
  const posteriorWithheld = latest?.diagnostic_status === "NOT_CONVERGED";
  const isActiveBatch = batchAnalysis?.status === "RUNNING";

  const runAnalysis = async () => {
    if (!canAnalyze || running) return;
    const targets = analysisTargets(analysisSelection, profiles);
    if (targets.length === 0) return;
    const isBatchSelection = analysisSelection === ALL_PROFILES_VALUE;
    if (isBatchSelection && isActiveBatch) return;
    setRunning(true);
    setError(null);
    setBatchRequest(
      isBatchSelection
        ? {
            status: "submitting",
            total: targets.length,
            accepted: 0,
            failed: 0,
            failures: [],
          }
        : null,
    );
    try {
      const from = new Date(`${windowFrom}T00:00:00.000Z`);
      const to = new Date(`${windowTo}T23:59:59.999Z`);
      const randomSeed = Date.now() % 2147483647;
      if (isBatchSelection) {
        const response = await apiPost<{
          total: number;
          enqueued: number;
          enqueue_failures: string[];
        }>("/profile-intelligence/bayesian/batches", {
          profile_ids: targets.map((target) => target.profile_id),
          window_from: from.toISOString(),
          window_to: to.toISOString(),
          random_seed: randomSeed,
          idempotency_key: crypto.randomUUID(),
        });
        const failures = response.enqueue_failures ?? [];
        setBatchRequest({
          status: failures.length > 0 ? "partial" : "submitted",
          total: response.total,
          accepted: response.enqueued,
          failed: failures.length,
          failures,
        });
        if (response.enqueued === 0) {
          throw new Error("Nenhuma análise do lote pôde ser enfileirada.");
        }
        await loadBatch();
      } else {
        await apiPost(
          `/profile-intelligence/${targets[0].profile_id}/bayesian/analyze`,
          {
            window_from: from.toISOString(),
            window_to: to.toISOString(),
            random_seed: randomSeed,
            idempotency_key: crypto.randomUUID(),
          },
        );
      }

      const profileToRefresh = profileId || targets[0]?.profile_id || "";
      if (profileToRefresh) await loadProfile(profileToRefresh);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "A análise não pôde ser enfileirada.",
      );
    } finally {
      setRunning(false);
    }
  };

  const activateAnalysisOnly = async () => {
    if (activatingPolicy) return;
    setActivatingPolicy(true);
    setError(null);
    setPolicyNotice(null);
    try {
      await apiPost(
        "/profile-intelligence/bayesian/policy/activate-analysis-only",
        {},
      );
      setPolicyNotice(
        "Política analysis-only ativada. Otimização, candidates e shadow permanecem bloqueados.",
      );
      await loadInitial();
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Não foi possível ativar a política protegida.",
      );
    } finally {
      setActivatingPolicy(false);
    }
  };

  const upgradeAnalysisV2 = async () => {
    if (activatingPolicy) return;
    setActivatingPolicy(true);
    setError(null);
    setPolicyNotice(null);
    try {
      await apiPost(
        "/profile-intelligence/bayesian/policy/upgrade-analysis-v2",
        {},
      );
      setPolicyNotice(
        "Política analysis_only_v2 ativada: holdout temporal, hierarquia não centrada e EV líquido coerente.",
      );
      await loadInitial();
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Não foi possível atualizar a política para v2.",
      );
    } finally {
      setActivatingPolicy(false);
    }
  };

  const openPolicyEditor = async () => {
    setError(null);
    try {
      const response = await apiGet<PolicyResponse>(
        "/profile-intelligence/bayesian/policy",
      );
      setPolicyDraft(
        response.data ? JSON.stringify(response.data, null, 2) : "",
      );
      setPolicyEditorOpen(true);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Não foi possível carregar a política.",
      );
    }
  };

  const importPolicyFile = async (
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(null);
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      setPolicyDraft(JSON.stringify(parsed, null, 2));
      setPolicyEditorOpen(true);
    } catch {
      setError("O arquivo selecionado não contém um JSON válido.");
    }
  };

  const savePolicy = async () => {
    if (savingPolicy) return;
    setSavingPolicy(true);
    setError(null);
    setPolicyNotice(null);
    try {
      const parsed = JSON.parse(policyDraft) as Record<string, unknown>;
      await apiPut("/profile-intelligence/bayesian/policy", parsed);
      setPolicyNotice("Política validada e salva com auditoria.");
      setPolicyEditorOpen(false);
      await loadInitial();
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "A política não pôde ser validada e salva.",
      );
    } finally {
      setSavingPolicy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-cyan-400/15 bg-[#061013]">
        <LoaderCircle className="h-6 w-6 animate-spin text-cyan-300" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="relative overflow-hidden rounded-2xl border border-cyan-300/15 bg-[#061014] shadow-[inset_0_1px_0_rgba(255,255,255,.04),0_24px_80px_rgba(0,0,0,.22)]">
        <div className="pointer-events-none absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(34,211,238,.055)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,.055)_1px,transparent_1px)] [background-size:36px_36px]" />
        <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-cyan-300/10 blur-3xl" />
        <div className="relative grid gap-6 p-5 lg:grid-cols-[1.3fr_.7fr]">
          <div>
            <div className="mb-4 flex items-center gap-3">
              <div className="grid h-11 w-11 place-items-center rounded-xl border border-cyan-300/25 bg-cyan-300/10">
                <Atom className="h-5 w-5 text-cyan-200" />
              </div>
              <div>
                <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-cyan-300">
                  Profile Intelligence · Statistical Lab
                </div>
                <h2 className="mt-1 text-xl font-semibold tracking-tight text-slate-50">
                  Bayesian Intelligence
                </h2>
              </div>
            </div>
            <p className="max-w-3xl text-sm leading-6 text-slate-300">
              Evidência associativa com incerteza explícita. Esta camada analisa
              snapshots históricos e pode recomendar candidates; não altera profiles
              ativos, decisões de trading ou modelos L1/L3.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {[
                ["ANALYSIS_ONLY", moduleStatus?.authority?.analysis === true],
                ["CANDIDATE_ONLY", moduleStatus?.authority?.profile_mutation === false],
                ["NO ML TRAINING", moduleStatus?.authority?.ml_training === false],
                ["NO AUTO PROMOTION", moduleStatus?.authority?.automatic_activation === false],
              ].map(([label, safe]) => (
                <span
                  key={String(label)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10px] tracking-[0.12em] ${
                    safe
                      ? "border-emerald-400/25 bg-emerald-400/8 text-emerald-200"
                      : "border-rose-400/25 bg-rose-400/8 text-rose-200"
                  }`}
                >
                  <ShieldCheck className="h-3 w-3" />
                  {String(label)}
                </span>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-white/8 bg-black/20 p-4 backdrop-blur">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-slate-400">
                Rollout interlock
              </span>
              <LockKeyhole className="h-4 w-4 text-amber-300" />
            </div>
            <div className="mt-4 space-y-2">
              {[
                ["Módulo", moduleStatus?.flags?.enabled],
                ["Análise", moduleStatus?.flags?.analysis_enabled],
                ["Otimização", moduleStatus?.flags?.optimization_enabled],
                ["Candidates", moduleStatus?.flags?.candidate_creation_enabled],
                ["Shadow", moduleStatus?.flags?.shadow_submission_enabled],
              ].map(([label, active]) => (
                <div
                  key={String(label)}
                  className="flex items-center justify-between border-b border-white/6 pb-2 text-xs last:border-0 last:pb-0"
                >
                  <span className="text-slate-400">{String(label)}</span>
                  <span
                    className={`font-mono ${active ? "text-emerald-300" : "text-slate-500"}`}
                  >
                    {active ? "ENABLED" : "OFF"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-400/25 bg-rose-400/8 p-3 text-sm text-rose-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {policyNotice && (
        <div className="flex items-start gap-2 rounded-xl border border-emerald-400/25 bg-emerald-400/8 p-3 text-sm text-emerald-100">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          {policyNotice}
        </div>
      )}

      {!moduleStatus?.policy_configured && (
        <div className="relative overflow-hidden rounded-xl border border-amber-300/25 bg-amber-300/8 p-4">
          <div className="pointer-events-none absolute -right-10 -top-16 h-40 w-40 rounded-full bg-amber-300/10 blur-3xl" />
          <div className="relative flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-start gap-3">
              <Braces className="mt-0.5 h-5 w-5 shrink-0 text-amber-200" />
              <div>
                <div className="text-sm font-semibold text-amber-100">
                  Política quantitativa ainda não configurada
                </div>
                <p className="mt-1 max-w-3xl text-xs leading-5 text-amber-100/70">
                  Ative o preset versionado{" "}
                  <span className="font-mono text-amber-100">
                    {moduleStatus?.activation?.template_id || "analysis_only_v2"}
                  </span>
                  . Ele libera somente análises offline; otimização, candidates,
                  shadow, ML e trading continuam bloqueados.
                </p>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void activateAnalysisOnly()}
                disabled={activatingPolicy}
                className="inline-flex items-center gap-2 rounded-lg border border-amber-200/35 bg-amber-200/15 px-3.5 py-2.5 text-xs font-semibold text-amber-50 transition hover:bg-amber-200/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {activatingPolicy ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Power className="h-4 w-4" />
                )}
                Ativar análise protegida
              </button>
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-white/10 bg-black/15 px-3.5 py-2.5 text-xs font-medium text-amber-100/80 transition hover:bg-black/25">
                <Upload className="h-4 w-4" />
                Importar JSON
                <input
                  type="file"
                  accept="application/json,.json"
                  onChange={(event) => void importPolicyFile(event)}
                  className="sr-only"
                />
              </label>
            </div>
          </div>
        </div>
      )}

      {moduleStatus?.policy_configured && (
        <div className="flex flex-col gap-3 rounded-xl border border-emerald-400/20 bg-emerald-400/7 p-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
            <div>
              <div className="text-sm font-semibold text-emerald-100">
                Política de análise protegida ativa
              </div>
              <p className="mt-1 text-xs text-emerald-100/65">
                <span className="font-mono text-emerald-200">
                  {moduleStatus.policy?.policy_version || "custom"}
                </span>
                {" · "}
                limite de {moduleStatus.policy?.max_trades ?? "—"} trades por run
                {" · "}
                sem autoridade sobre profiles ativos ou trading.
              </p>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {moduleStatus.activation?.can_upgrade && (
              <button
                type="button"
                onClick={() => void upgradeAnalysisV2()}
                disabled={activatingPolicy}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:opacity-40"
              >
                {activatingPolicy ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4" />
                )}
                Atualizar para v2
              </button>
            )}
            <button
              type="button"
              onClick={() => void openPolicyEditor()}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-emerald-300/20 bg-emerald-300/8 px-3 py-2 text-xs font-medium text-emerald-100 transition hover:bg-emerald-300/12"
            >
              <FileJson2 className="h-4 w-4" />
              Editar / importar política
            </button>
          </div>
        </div>
      )}

      {policyEditorOpen && (
        <section className="overflow-hidden rounded-xl border border-cyan-300/20 bg-[#071115]">
          <div className="flex items-center justify-between border-b border-white/8 px-4 py-3">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300">
                Policy workspace
              </div>
              <h3 className="mt-1 text-sm font-semibold text-slate-100">
                JSON validado no backend
              </h3>
            </div>
            <button
              type="button"
              onClick={() => setPolicyEditorOpen(false)}
              className="rounded-lg border border-white/8 p-2 text-slate-400 transition hover:text-slate-100"
              aria-label="Fechar editor da política"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="grid gap-4 p-4 lg:grid-cols-[1fr_280px]">
            <textarea
              value={policyDraft}
              onChange={(event) => setPolicyDraft(event.target.value)}
              spellCheck={false}
              className="min-h-[360px] w-full resize-y rounded-lg border border-white/10 bg-black/30 p-4 font-mono text-xs leading-5 text-cyan-50 outline-none transition focus:border-cyan-300/35"
              aria-label="Política Bayesian Intelligence em JSON"
            />
            <aside className="rounded-lg border border-white/8 bg-white/[0.025] p-4">
              <div className="text-xs font-semibold text-slate-100">
                Interlocks obrigatórios
              </div>
              <ul className="mt-3 space-y-2 text-[11px] leading-4 text-slate-400">
                <li>• mode deve permanecer analysis_only.</li>
                <li>• Search space deve permanecer vazio.</li>
                <li>• Otimização, candidates, replay e shadow devem ficar falsos.</li>
                <li>• Limites e objetos internos são validados antes do commit.</li>
              </ul>
              <label className="mt-4 flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300 transition hover:bg-white/5">
                <Upload className="h-4 w-4" />
                Substituir por arquivo
                <input
                  type="file"
                  accept="application/json,.json"
                  onChange={(event) => void importPolicyFile(event)}
                  className="sr-only"
                />
              </label>
              <button
                type="button"
                onClick={() => void savePolicy()}
                disabled={savingPolicy || !policyDraft.trim()}
                className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2.5 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {savingPolicy ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                Validar e salvar
              </button>
            </aside>
          </div>
        </section>
      )}

      <section className="grid gap-4 xl:grid-cols-[340px_1fr]">
        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
                Analysis request
              </div>
              <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                Escopo reproduzível
              </h3>
            </div>
            <FlaskConical className="h-5 w-5 text-cyan-300" />
          </div>
          <label className="mt-4 block text-[11px] uppercase tracking-wider text-[var(--text-muted)]">
            Profile
          </label>
          <select
            value={analysisSelection || profileId}
            disabled={running}
            onChange={(event) => {
              const value = event.target.value;
              setAnalysisSelection(value);
              if (value !== ALL_PROFILES_VALUE) {
                setProfileId(value);
              }
            }}
            className="mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-2 text-sm text-[var(--text-primary)]"
          >
            {profiles.length > 0 && (
              <option value={ALL_PROFILES_VALUE}>
                Todos os profiles — {profiles.length}
              </option>
            )}
            {profiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.profile_name}
              </option>
            ))}
          </select>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <label className="text-[11px] text-[var(--text-muted)]">
              Início
              <input
                type="date"
                value={windowFrom}
                disabled={running}
                onChange={(event) => setWindowFrom(event.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-2 text-xs text-[var(--text-primary)]"
              />
            </label>
            <label className="text-[11px] text-[var(--text-muted)]">
              Fim
              <input
                type="date"
                value={windowTo}
                disabled={running}
                onChange={(event) => setWindowTo(event.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-2 text-xs text-[var(--text-primary)]"
              />
            </label>
          </div>
          <button
            type="button"
            disabled={
              !canAnalyze ||
              running ||
              (analysisSelection === ALL_PROFILES_VALUE && isActiveBatch)
            }
            onClick={runAnalysis}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2.5 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {running ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {running && batchRequest
              ? "Criando lote persistente"
              : analysisSelection === ALL_PROFILES_VALUE
                ? isActiveBatch
                  ? `Lote em execução ${batchAnalysis?.counts.terminal ?? 0}/${batchAnalysis?.counts.total ?? 0}`
                  : "Solicitar análise de todos"
                : "Solicitar análise"}
          </button>
          {batchRequest && (
            <div
              className={`mt-3 rounded-lg border px-3 py-2.5 ${
                batchRequest.status === "partial"
                  ? "border-amber-300/25 bg-amber-300/8"
                  : batchRequest.status === "submitted"
                    ? "border-emerald-300/25 bg-emerald-300/8"
                    : "border-cyan-300/25 bg-cyan-300/8"
              }`}
              role="status"
              aria-live="polite"
            >
              <div className="flex items-center justify-between gap-3 text-[11px]">
                <span className="font-semibold text-[var(--text-primary)]">
                  {batchRequest.status === "submitting"
                    ? "Preparando lote"
                    : batchRequest.status === "submitted"
                      ? "Lote enfileirado"
                      : "Lote parcialmente enfileirado"}
                </span>
                <span className="font-mono text-cyan-200">
                  {batchRequest.accepted}/{batchRequest.total}
                </span>
              </div>
              <p className="mt-1 text-[10px] leading-4 text-[var(--text-muted)]">
                O lote fica persistido e o relatório consolidado é atualizado
                conforme cada profile termina.
              </p>
              {batchRequest.failures.length > 0 && (
                <p className="mt-1 text-[10px] leading-4 text-amber-200">
                  Falharam: {batchRequest.failures.join(", ")}
                </p>
              )}
            </div>
          )}
          <p className="mt-3 text-[11px] leading-4 text-[var(--text-muted)]">
            {analysisSelection === ALL_PROFILES_VALUE
              ? "Os profiles são processados sequencialmente, sem elevar a concorrência científica do worker."
              : "A API cria um run idempotente; o worker dedicado processa o dataset sem bloquear esta tela."}
          </p>
        </div>

        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
                Latest posterior
              </div>
              <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                Estado da análise
              </h3>
            </div>
            <div className="flex items-center gap-2">
              {analysisSelection === ALL_PROFILES_VALUE && (
                <span className="hidden rounded-md border border-[var(--border-default)] px-2 py-1 font-mono text-[9px] uppercase tracking-wider text-[var(--text-muted)] sm:inline">
                  Exibindo {activeProfileName}
                </span>
              )}
              <StatusPill value={latest?.status} />
              <button
                type="button"
                onClick={() => {
                  void loadProfile(profileId);
                  if (analysisSelection === ALL_PROFILES_VALUE) {
                    void loadBatch();
                  }
                }}
                className="rounded-lg border border-[var(--border-default)] p-2 text-[var(--text-secondary)] hover:text-cyan-200"
                aria-label="Atualizar Bayesian Intelligence"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              {
                label: "Diagnóstico",
                value: latest?.diagnostic_status || "—",
                icon: Activity,
              },
              {
                label: "Trades diretos",
                value: latest?.row_count ?? "—",
                icon: Database,
              },
              {
                label: "Candidates",
                value: candidates.length,
                icon: Layers3,
              },
              {
                label: "Eventos auditados",
                value: auditEvents.length,
                icon: Fingerprint,
              },
            ].map((item) => (
              <div
                key={item.label}
                className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] p-3"
              >
                <item.icon className="h-4 w-4 text-cyan-300" />
                <div className="mt-3 font-mono text-base text-[var(--text-primary)]">
                  {item.value}
                </div>
                <div className="mt-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  {item.label}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 grid gap-2 text-xs text-[var(--text-secondary)] md:grid-cols-2">
            <div className="flex items-center gap-2 rounded-lg border border-[var(--border-default)] px-3 py-2">
              <Clock3 className="h-4 w-4 text-cyan-300" />
              Janela: {fmtDate(latest?.window_from)} → {fmtDate(latest?.window_to)}
            </div>
            <div className="flex min-w-0 items-center gap-2 rounded-lg border border-[var(--border-default)] px-3 py-2">
              <Fingerprint className="h-4 w-4 shrink-0 text-cyan-300" />
              <span className="truncate font-mono">
                {latest?.dataset_hash || "dataset hash ainda indisponível"}
              </span>
            </div>
          </div>
          {latest?.manifest?.temporal_split?.counts && (
            <div className="mt-2 grid gap-2 text-[11px] text-[var(--text-secondary)] sm:grid-cols-4">
              {[
                [
                  "Discovery",
                  latest.manifest.temporal_split.counts.discovery,
                ],
                [
                  "Validation",
                  latest.manifest.temporal_split.counts.validation,
                ],
                [
                  "Holdout lacrado",
                  latest.manifest.temporal_split.counts.final_holdout,
                ],
                [
                  "Embargo",
                  `${latest.manifest.temporal_split.effective_embargo_seconds ?? "—"}s`,
                ],
              ].map(([label, value]) => (
                <div
                  key={String(label)}
                  className="rounded-lg border border-[var(--border-default)] px-3 py-2"
                >
                  <div className="font-mono text-cyan-100">{value}</div>
                  <div className="mt-1 text-[9px] uppercase tracking-wider text-[var(--text-muted)]">
                    {label}
                  </div>
                </div>
              ))}
            </div>
          )}
          {latest?.manifest?.preflight && (
            <div className="mt-2 grid gap-2 text-[11px] text-[var(--text-secondary)] sm:grid-cols-4">
              {[
                [
                  "MDE EV líquido",
                  fmtNumber(
                    latest.manifest.preflight.power_analysis
                      ?.minimum_detectable_net_ev_pct,
                    4,
                  ),
                ],
                [
                  "Limite de edge",
                  fmtNumber(
                    latest.manifest.preflight.maximum_plausible_edge_pct,
                    4,
                  ),
                ],
                [
                  "Features aprovadas",
                  latest.manifest.preflight.feature_quality
                    ?.approved_for_both_windows?.length ?? "—",
                ],
                [
                  "Features excluídas",
                  latest.manifest.preflight.feature_quality?.excluded_from_model
                    ?.length ?? "—",
                ],
              ].map(([label, value]) => (
                <div
                  key={String(label)}
                  className="rounded-lg border border-[var(--border-default)] px-3 py-2"
                >
                  <div className="font-mono text-cyan-100">{value}</div>
                  <div className="mt-1 text-[9px] uppercase tracking-wider text-[var(--text-muted)]">
                    {label}
                  </div>
                </div>
              ))}
            </div>
          )}
          {(latest?.warnings?.length ?? 0) > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {latest?.warnings?.map((warning) => (
                <span
                  key={warning}
                  className="rounded-md border border-amber-400/20 bg-amber-400/8 px-2 py-1 font-mono text-[9px] text-amber-200"
                >
                  {warning}
                </span>
              ))}
            </div>
          )}
          {latest?.error_message && (
            <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/8 p-3 font-mono text-[11px] text-rose-200">
              {latest.error_message}
            </div>
          )}
        </div>
      </section>

      {analysisSelection === ALL_PROFILES_VALUE && (
        <section className="overflow-hidden rounded-xl border border-cyan-300/20 bg-[var(--bg-card)]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-default)] p-4">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300">
                Cross-profile evidence
              </div>
              <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                Relatório consolidado de todos os profiles
              </h3>
            </div>
            <div className="flex items-center gap-2">
              {batchAnalysis?.report.status === "PARTIAL" && (
                <span className="rounded-full border border-amber-400/25 bg-amber-400/8 px-2.5 py-1 font-mono text-[9px] uppercase tracking-wider text-amber-200">
                  Relatório parcial
                </span>
              )}
              <StatusPill value={batchAnalysis?.status} />
              <button
                type="button"
                onClick={() => void loadBatch()}
                className="rounded-lg border border-[var(--border-default)] p-2 text-[var(--text-secondary)] hover:text-cyan-200"
                aria-label="Atualizar relatório consolidado"
              >
                <RefreshCw
                  className={`h-4 w-4 ${isActiveBatch ? "animate-spin" : ""}`}
                />
              </button>
            </div>
          </div>

          {!batchAnalysis ? (
            <div className="grid min-h-44 place-items-center p-6 text-center">
              <div>
                <Layers3 className="mx-auto h-7 w-7 text-[var(--text-muted)]" />
                <p className="mt-3 text-sm text-[var(--text-secondary)]">
                  Solicite a análise de todos para criar o relatório consolidado.
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-5 p-4">
              <div>
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="text-[var(--text-secondary)]">
                    Progresso persistido do lote
                  </span>
                  <span className="font-mono text-cyan-100">
                    {batchAnalysis.counts.terminal}/{batchAnalysis.counts.total}
                  </span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/5">
                  <div
                    className="h-full rounded-full bg-cyan-300 transition-[width]"
                    style={{
                      width: `${Math.max(
                        0,
                        Math.min(100, batchAnalysis.progress * 100),
                      )}%`,
                    }}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
                {[
                  ["Concluídos", batchAnalysis.counts.terminal],
                  ["Válidos", batchAnalysis.counts.valid],
                  ["Ativos", batchAnalysis.counts.active],
                  ["Pendentes", batchAnalysis.counts.pending],
                  ["Não convergentes", batchAnalysis.counts.not_converged],
                  ["Falhas", batchAnalysis.counts.failed],
                ].map(([label, value]) => (
                  <div
                    key={String(label)}
                    className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] p-3"
                  >
                    <div className="font-mono text-base text-cyan-100">
                      {value}
                    </div>
                    <div className="mt-1 text-[9px] uppercase tracking-wider text-[var(--text-muted)]">
                      {label}
                    </div>
                  </div>
                ))}
              </div>

              <div className="flex items-start gap-3 rounded-xl border border-cyan-300/15 bg-cyan-300/5 p-4">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" />
                <div>
                  <p className="text-xs font-semibold text-cyan-100">
                    Síntese descritiva, não causal
                  </p>
                  <p className="mt-1 text-[11px] leading-5 text-[var(--text-muted)]">
                    Médias ponderadas pelo N direto de cada profile. Não
                    representa posterior conjunto. Runs pendentes, falhos ou
                    não convergentes ficam excluídos automaticamente.
                  </p>
                </div>
              </div>

              {batchAnalysis.report.indicators.length === 0 ? (
                <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-4 text-xs leading-5 text-amber-100/80">
                  Ainda não há efeitos convergentes elegíveis para consolidar.
                  O relatório será atualizado conforme os profiles válidos
                  terminarem.
                </div>
              ) : (
                <>
                  <div className="grid gap-3 lg:grid-cols-2">
                    <div className="rounded-xl border border-[var(--border-default)] bg-black/10 p-4">
                      <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-300">
                        Leitura de direção
                      </div>
                      <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">
                        Consenso positivo em{" "}
                        <span className="font-mono text-emerald-200">
                          {batchAnalysis.report.direction_summary.POSITIVE}
                        </span>{" "}
                        indicadores; negativo em{" "}
                        <span className="font-mono text-rose-200">
                          {batchAnalysis.report.direction_summary.NEGATIVE}
                        </span>
                        ; neutro em{" "}
                        <span className="font-mono text-slate-200">
                          {batchAnalysis.report.direction_summary.NEUTRAL}
                        </span>{" "}
                        e misto em{" "}
                        <span className="font-mono text-amber-200">
                          {batchAnalysis.report.direction_summary.MIXED}
                        </span>
                        .
                      </p>
                    </div>
                    <div className="rounded-xl border border-[var(--border-default)] bg-black/10 p-4">
                      <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-300">
                        Força da evidência
                      </div>
                      <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">
                        Evidência forte ou muito forte em{" "}
                        <span className="font-mono text-cyan-100">
                          {batchAnalysis.report.evidence_summary.STRONG +
                            batchAnalysis.report.evidence_summary.VERY_STRONG}
                        </span>{" "}
                        indicadores; moderada em{" "}
                        <span className="font-mono text-cyan-100">
                          {batchAnalysis.report.evidence_summary.MODERATE}
                        </span>
                        . Esta leitura não autoriza alteração de profile.
                      </p>
                    </div>
                  </div>
                  <div className="overflow-x-auto rounded-xl border border-[var(--border-default)]">
                  <table className="w-full min-w-[980px] text-left text-xs">
                    <thead className="bg-[var(--bg-elevated)] text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                      <tr>
                        {[
                          "Indicador",
                          "Consenso",
                          "Profiles",
                          "N direto",
                          "Lift TP pond.",
                          "Lift EV pond.",
                          "P(EV > ROPE) pond.",
                          "Direções + / − / neutra",
                          "Maior evidência",
                        ].map((label) => (
                          <th key={label} className="px-4 py-3 font-medium">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {batchAnalysis.report.indicators.map((indicator) => (
                        <tr
                          key={`${indicator.indicator}:${indicator.regime ?? ""}`}
                          className="border-t border-[var(--border-default)] text-[var(--text-secondary)]"
                        >
                          <td className="px-4 py-3 font-mono text-[var(--text-primary)]">
                            {indicator.indicator}
                          </td>
                          <td className="px-4 py-3">
                            <StatusPill value={indicator.consensus_direction} />
                          </td>
                          <td className="px-4 py-3 font-mono">
                            {indicator.profiles_included}
                          </td>
                          <td className="px-4 py-3 font-mono">
                            {indicator.total_direct_sample_size}
                          </td>
                          <td className="px-4 py-3 font-mono">
                            {fmtNumber(indicator.weighted_tp_lift, 4)}
                          </td>
                          <td className="px-4 py-3 font-mono">
                            {fmtNumber(indicator.weighted_pnl_lift, 4)}
                          </td>
                          <td className="px-4 py-3 font-mono">
                            {fmtPct(
                              indicator.weighted_probability_positive,
                            )}
                          </td>
                          <td className="px-4 py-3 font-mono">
                            {indicator.direction_counts.POSITIVE} /{" "}
                            {indicator.direction_counts.NEGATIVE} /{" "}
                            {indicator.direction_counts.NEUTRAL}
                          </td>
                          <td className="px-4 py-3">
                            <StatusPill
                              value={indicator.highest_evidence_grade}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                </>
              )}

              <div>
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--text-muted)]">
                  Execuções por profile
                </div>
                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {batchAnalysis.profile_runs.map((run) => (
                    <div
                      key={run.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-default)] bg-black/10 px-3 py-2"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-xs text-[var(--text-primary)]">
                          {run.profile_name}
                        </div>
                        <div className="mt-0.5 font-mono text-[9px] text-[var(--text-muted)]">
                          {run.row_count ?? "—"} trades diretos
                        </div>
                      </div>
                      <StatusPill
                        value={run.diagnostic_status || run.status}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      <section className="overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-default)] p-4">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
              Convergence ledger
            </div>
            <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
              Resultado dos diagnósticos
            </h3>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
            <ShieldCheck className="h-3.5 w-3.5 text-cyan-300" />
            Gates carregados da política ativa
          </div>
        </div>

        {!latest ? (
          <div className="grid min-h-40 place-items-center p-6 text-center">
            <div>
              <FlaskConical className="mx-auto h-7 w-7 text-[var(--text-muted)]" />
              <p className="mt-3 text-sm text-[var(--text-secondary)]">
                Solicite uma análise para gerar o posterior.
              </p>
            </div>
          </div>
        ) : isActiveRun ? (
          <div className="p-5">
            <div className="flex items-start gap-3 rounded-xl border border-cyan-300/20 bg-cyan-300/5 p-4">
              <LoaderCircle className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-cyan-300" />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-cyan-100">
                  Amostragem em processamento
                </p>
                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                  Fase atual:{" "}
                  <span className="font-mono text-cyan-200">{latest.status}</span>.
                  O worker publicará R-hat, ESS e divergências ao concluir os
                  modelos.
                </p>
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-cyan-300/70" />
                </div>
              </div>
            </div>
          </div>
        ) : diagnostics.length === 0 ? (
          <div className="grid min-h-40 place-items-center p-6 text-center">
            <div>
              <AlertTriangle className="mx-auto h-7 w-7 text-amber-300" />
              <p className="mt-3 text-sm text-[var(--text-secondary)]">
                A execução terminou sem um ledger diagnóstico disponível.
              </p>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                Verifique o erro da execução e os eventos de auditoria.
              </p>
            </div>
          </div>
        ) : (
          <div className="p-4">
            {posteriorWithheld && (
              <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-400/25 bg-amber-400/8 p-4">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-300" />
                <div>
                  <p className="text-sm font-semibold text-amber-100">
                    Posterior retido pelo gate de convergência
                  </p>
                  <p className="mt-1 text-xs leading-5 text-amber-100/75">
                    A análise foi executada, mas pelo menos um diagnóstico não
                    satisfez a política quantitativa. Os efeitos e recomendações
                    permanecem bloqueados para evitar evidência estatística
                    inválida.
                  </p>
                </div>
              </div>
            )}

            <div className="grid gap-3 xl:grid-cols-2">
              {diagnostics.map((diagnostic) => {
                const rhatPassed =
                  diagnostic.rhat_max != null &&
                  diagnosticGates?.max_rhat != null
                    ? diagnostic.rhat_max <= diagnosticGates.max_rhat
                    : undefined;
                const essPassed =
                  diagnostic.effective_sample_size_min != null &&
                  diagnosticGates?.min_mcmc_effective_sample_size != null
                    ? diagnostic.effective_sample_size_min >=
                      diagnosticGates.min_mcmc_effective_sample_size
                    : undefined;
                const divergencesPassed =
                  diagnosticGates?.max_divergences != null
                    ? diagnostic.divergences <=
                      diagnosticGates.max_divergences
                    : undefined;

                return (
                  <article
                    key={diagnostic.id}
                    className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)] p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-300">
                          {diagnostic.model_name}
                        </div>
                        <h4 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                          {diagnosticModelLabel(diagnostic.model_name)}
                        </h4>
                      </div>
                      <StatusPill value={diagnostic.status} />
                    </div>

                    <dl className="mt-4 grid grid-cols-3 divide-x divide-[var(--border-default)] rounded-lg border border-[var(--border-default)] bg-black/10">
                      <div className="p-3">
                        <dt className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                          R-hat máx.
                        </dt>
                        <dd
                          className={`mt-1 font-mono text-sm ${diagnosticMetricTone(
                            rhatPassed,
                          )}`}
                        >
                          {fmtNumber(diagnostic.rhat_max, 3)}
                        </dd>
                        <div className="mt-1 font-mono text-[9px] text-[var(--text-muted)]">
                          limite ≤{" "}
                          {fmtNumber(diagnosticGates?.max_rhat, 3)}
                        </div>
                      </div>
                      <div className="p-3">
                        <dt className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                          ESS mín.
                        </dt>
                        <dd
                          className={`mt-1 font-mono text-sm ${diagnosticMetricTone(
                            essPassed,
                          )}`}
                        >
                          {fmtNumber(
                            diagnostic.effective_sample_size_min,
                            0,
                          )}
                        </dd>
                        <div className="mt-1 font-mono text-[9px] text-[var(--text-muted)]">
                          mínimo ≥{" "}
                          {fmtNumber(
                            diagnosticGates?.min_mcmc_effective_sample_size,
                            0,
                          )}
                        </div>
                      </div>
                      <div className="p-3">
                        <dt className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                          Divergências
                        </dt>
                        <dd
                          className={`mt-1 font-mono text-sm ${diagnosticMetricTone(
                            divergencesPassed,
                          )}`}
                        >
                          {diagnostic.divergences}
                        </dd>
                        <div className="mt-1 font-mono text-[9px] text-[var(--text-muted)]">
                          máximo ≤{" "}
                          {fmtNumber(
                            diagnosticGates?.max_divergences,
                            0,
                          )}
                        </div>
                      </div>
                    </dl>

                    {(diagnostic.sampling_warnings?.length ?? 0) > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {diagnostic.sampling_warnings?.map((warning) => (
                          <span
                            key={warning}
                            className="rounded-md border border-amber-400/20 bg-amber-400/8 px-2 py-1 font-mono text-[9px] text-amber-200"
                          >
                            {warning}
                          </span>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-default)] p-4">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
              Indicator associations
            </div>
            <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
              Efeitos posteriores
            </h3>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
            <Sparkles className="h-3.5 w-3.5 text-cyan-300" />
            Associação observada — não implica causalidade
          </div>
        </div>
        {effects.length === 0 ? (
          <div className="grid min-h-52 place-items-center p-6 text-center">
            <div>
              {posteriorWithheld ? (
                <LockKeyhole className="mx-auto h-7 w-7 text-amber-300" />
              ) : isActiveRun ? (
                <LoaderCircle className="mx-auto h-7 w-7 animate-spin text-cyan-300" />
              ) : (
                <Atom className="mx-auto h-7 w-7 text-[var(--text-muted)]" />
              )}
              <p className="mt-3 text-sm text-[var(--text-secondary)]">
                {posteriorWithheld
                  ? "Efeitos não publicados: convergência reprovada."
                  : isActiveRun
                    ? "Efeitos aguardando a conclusão da amostragem."
                    : "Nenhum efeito posterior elegível foi persistido."}
              </p>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                {posteriorWithheld
                  ? "Consulte o ledger acima para identificar cada gate reprovado."
                  : "Runs insuficientes ou não convergentes não geram recomendações."}
              </p>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-xs">
              <thead className="bg-[var(--bg-elevated)] text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                <tr>
                  {[
                    "Indicador",
                    "Direção",
                    "Lift TP",
                    "Lift EV líquido",
                    "P(EV > ROPE)",
                    "IC 95%",
                    "Evidência",
                    "Discovery",
                    "Validation",
                    "ESS",
                    "Status",
                  ].map((label) => (
                    <th key={label} className="px-4 py-3 font-medium">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {effects.map((effect) => {
                  const probability = effect.probability_positive_effect ?? 0;
                  return (
                    <tr
                      key={effect.id}
                      className="border-t border-[var(--border-default)] text-[var(--text-secondary)]"
                    >
                      <td className="px-4 py-3 font-mono text-[var(--text-primary)]">
                        {effect.indicator}
                      </td>
                      <td className="px-4 py-3">{effect.effect_direction}</td>
                      <td className="px-4 py-3 font-mono">
                        {fmtNumber(effect.estimated_tp_lift, 4)}
                      </td>
                      <td className="px-4 py-3 font-mono">
                        {fmtNumber(effect.estimated_pnl_lift, 4)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-white/8">
                            <div
                              className="h-full rounded-full bg-cyan-300"
                              style={{ width: `${Math.max(0, Math.min(100, probability * 100))}%` }}
                            />
                          </div>
                          <span className="font-mono">{fmtPct(probability)}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono">
                        [{fmtNumber(effect.credible_interval_95?.[0], 4)},{" "}
                        {fmtNumber(effect.credible_interval_95?.[1], 4)}]
                      </td>
                      <td className="px-4 py-3">
                        <StatusPill value={effect.evidence_grade} />
                      </td>
                      <td className="px-4 py-3 font-mono">
                        {effect.direct_sample_size}
                      </td>
                      <td className="px-4 py-3 font-mono">
                        {effect.shared_sample_size}
                      </td>
                      <td className="px-4 py-3 font-mono">
                        {fmtNumber(effect.effective_sample_size, 0)}
                      </td>
                      <td className="px-4 py-3">
                        <StatusPill value={effect.diagnostic_status} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <div className="flex items-center justify-between border-b border-[var(--border-default)] p-4">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
                Offline optimization
              </div>
              <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                Studies e restrições
              </h3>
            </div>
            <StatusPill value={studies[0]?.status} />
          </div>
          {studies.length === 0 ? (
            <p className="p-4 text-xs leading-5 text-[var(--text-muted)]">
              Nenhum estudo iniciado. A otimização permanece desativada e o
              replay confiável ainda é um gate obrigatório.
            </p>
          ) : (
            <div className="space-y-3 p-4">
              {studies.slice(0, 3).map((study) => (
                <div
                  key={study.id}
                  className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[11px] text-cyan-200">
                      {study.sampler}
                    </span>
                    <StatusPill value={study.status} />
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    <div className="text-[var(--text-muted)]">
                      Trials concluídos
                      <div className="mt-1 font-mono text-[var(--text-primary)]">
                        {study.total_trials}
                      </div>
                    </div>
                    <div className="text-[var(--text-muted)]">
                      Trials válidos
                      <div className="mt-1 font-mono text-[var(--text-primary)]">
                        {study.valid_trials}
                      </div>
                    </div>
                  </div>
                  {study.error_message && (
                    <p className="mt-3 font-mono text-[10px] text-amber-200">
                      {study.error_message}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <div className="flex items-center justify-between border-b border-[var(--border-default)] p-4">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
                Candidate comparison
              </div>
              <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                Atual × candidato
              </h3>
            </div>
            <span className="font-mono text-xs text-cyan-200">
              {candidates.length}
            </span>
          </div>
          {candidates.length === 0 ? (
            <p className="p-4 text-xs leading-5 text-[var(--text-muted)]">
              Nenhum candidate Bayesiano. Apenas evidência forte, mudanças
              autorizadas e flags explícitas permitem criar drafts.
            </p>
          ) : (
            <div className="max-h-80 space-y-3 overflow-y-auto p-4">
              {candidates.map((candidate) => (
                <div
                  key={candidate.id}
                  className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[10px] text-[var(--text-muted)]">
                      {candidate.id}
                    </span>
                    <StatusPill value={candidate.status} />
                  </div>
                  <div className="mt-3 space-y-2">
                    {(candidate.changes || []).map((change) => (
                      <div
                        key={change.target_path}
                        className="grid grid-cols-[1fr_auto_auto] items-center gap-2 text-[11px]"
                      >
                        <span className="truncate font-mono text-cyan-200">
                          {change.target_path}
                        </span>
                        <span className="text-[var(--text-muted)]">
                          {JSON.stringify(change.current_value)}
                        </span>
                        <span className="text-[var(--text-primary)]">
                          → {JSON.stringify(change.candidate_value)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
        <div className="border-b border-[var(--border-default)] p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--text-muted)]">
            Immutable audit
          </div>
          <h3 className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
            Linha do tempo Bayesiana
          </h3>
        </div>
        {auditEvents.length === 0 ? (
          <p className="p-4 text-xs text-[var(--text-muted)]">
            Nenhum evento auditado para este profile.
          </p>
        ) : (
          <div className="divide-y divide-[var(--border-default)]">
            {auditEvents.slice(0, 10).map((event) => (
              <div
                key={event.id}
                className="grid gap-2 p-4 text-xs md:grid-cols-[170px_1fr_auto]"
              >
                <span className="font-mono text-cyan-200">{event.event_type}</span>
                <span className="text-[var(--text-secondary)]">
                  {event.previous_status || "—"} → {event.new_status || "—"}
                </span>
                <span className="text-[var(--text-muted)]">
                  {fmtDate(event.created_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <CheckCircle2 className="h-5 w-5 text-emerald-300" />
          <h3 className="mt-3 text-sm font-semibold text-[var(--text-primary)]">
            Diagnóstico antes da recomendação
          </h3>
          <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">
            R-hat, ESS, divergências e posterior predictive checks bloqueiam
            recomendações quando a convergência não é aceitável.
          </p>
        </div>
        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <Database className="h-5 w-5 text-cyan-300" />
          <h3 className="mt-3 text-sm font-semibold text-[var(--text-primary)]">
            Dataset point-in-time
          </h3>
          <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">
            O hash, a política TP/SL, os IDs das observações e as exclusões ficam
            registrados. Ausência nunca vira zero.
          </p>
        </div>
        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <ShieldCheck className="h-5 w-5 text-amber-300" />
          <h3 className="mt-3 text-sm font-semibold text-[var(--text-primary)]">
            Replay fail-closed
          </h3>
          <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">
            O replay genérico atual é um stub. Candidates não avançam para shadow
            até existir um adapter de replay histórico confiável.
          </p>
        </div>
      </section>
    </div>
  );
}
