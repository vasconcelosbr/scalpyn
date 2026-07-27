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
    sampler_config?: Record<string, number>;
    permissions?: Record<string, boolean>;
  } | null;
  activation?: {
    template_id: string;
    mode: string;
    can_activate: boolean;
  };
  replay: { supported: boolean; reason: string };
  dependencies: Record<string, string | null>;
};

type PolicyResponse = {
  configured: boolean;
  data?: Record<string, unknown> | null;
  error?: string;
};

type ProfileOption = {
  profile_id: string;
  profile_name: string;
};

type ProfileRankingResponse = {
  profiles?: ProfileOption[];
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

export default function BayesianIntelligencePanel() {
  const [moduleStatus, setModuleStatus] = useState<ModuleStatus | null>(null);
  const [profiles, setProfiles] = useState<ProfileOption[]>([]);
  const [profileId, setProfileId] = useState("");
  const [latest, setLatest] = useState<AnalysisRun | null>(null);
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

  const loadProfile = useCallback(async (selectedProfileId: string) => {
    if (!selectedProfileId) return;
    const [latestResult, effectsResult, candidatesResult, studiesResult, auditResult] =
      await Promise.all([
        apiGet(`/profile-intelligence/${selectedProfileId}/bayesian/latest`).catch(
          () => ({ item: null }),
        ),
        apiGet(`/profile-intelligence/${selectedProfileId}/bayesian/effects`).catch(
          () => ({ items: [] }),
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
    setLatest(latestResult?.item ?? null);
    setEffects(effectsResult?.items ?? []);
    setCandidates(candidatesResult?.items ?? []);
    setStudies(studiesResult?.items ?? []);
    setAuditEvents(auditResult?.items ?? []);
  }, []);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResult, profilesResult] = await Promise.allSettled([
        apiGet<ModuleStatus>("/profile-intelligence/bayesian/status"),
        apiGet<ProfileRankingResponse>(
          "/profile-intelligence/profiles/ranking?lookback_days=60&limit=100",
        ),
      ]);

      if (statusResult.status === "rejected") {
        throw statusResult.reason;
      }

      setModuleStatus(statusResult.value);
      const items =
        profilesResult.status === "fulfilled"
          ? profilesResult.value?.profiles ?? []
          : [];
      setProfiles(items);

      if (profilesResult.status === "rejected") {
        const rankingMessage =
          profilesResult.reason instanceof Error
            ? profilesResult.reason.message
            : "erro inesperado";
        setError(`Ranking de profiles indisponível: ${rankingMessage}`);
      }

      const selected = profileId || items[0]?.profile_id || "";
      setProfileId(selected);
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
    void loadInitial();
    // The first load intentionally selects the first available profile.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!profileId) return;
    void loadProfile(profileId);
  }, [loadProfile, profileId]);

  useEffect(() => {
    if (!profileId || !latest || terminalStatuses.has(latest.status)) return;
    const interval = window.setInterval(() => {
      void loadProfile(profileId);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [latest, loadProfile, profileId]);

  const canAnalyze =
    moduleStatus?.flags?.enabled === true &&
    moduleStatus?.flags?.analysis_enabled === true &&
    moduleStatus?.policy_configured === true &&
    Boolean(profileId);

  const runAnalysis = async () => {
    if (!canAnalyze || running) return;
    setRunning(true);
    setError(null);
    try {
      const from = new Date(`${windowFrom}T00:00:00.000Z`);
      const to = new Date(`${windowTo}T23:59:59.999Z`);
      await apiPost(`/profile-intelligence/${profileId}/bayesian/analyze`, {
        window_from: from.toISOString(),
        window_to: to.toISOString(),
        random_seed: Date.now() % 2147483647,
        idempotency_key: crypto.randomUUID(),
      });
      await loadProfile(profileId);
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
                    {moduleStatus?.activation?.template_id || "analysis_only_v1"}
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
          <button
            type="button"
            onClick={() => void openPolicyEditor()}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-emerald-300/20 bg-emerald-300/8 px-3 py-2 text-xs font-medium text-emerald-100 transition hover:bg-emerald-300/12"
          >
            <FileJson2 className="h-4 w-4" />
            Editar / importar política
          </button>
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
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-2 text-sm text-[var(--text-primary)]"
          >
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
                onChange={(event) => setWindowFrom(event.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-2 text-xs text-[var(--text-primary)]"
              />
            </label>
            <label className="text-[11px] text-[var(--text-muted)]">
              Fim
              <input
                type="date"
                value={windowTo}
                onChange={(event) => setWindowTo(event.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-2 text-xs text-[var(--text-primary)]"
              />
            </label>
          </div>
          <button
            type="button"
            disabled={!canAnalyze || running}
            onClick={runAnalysis}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2.5 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {running ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Solicitar análise
          </button>
          <p className="mt-3 text-[11px] leading-4 text-[var(--text-muted)]">
            A API cria um run idempotente; o worker dedicado processa o dataset
            sem bloquear esta tela.
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
              <StatusPill value={latest?.status} />
              <button
                type="button"
                onClick={() => void loadProfile(profileId)}
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
          {latest?.error_message && (
            <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/8 p-3 font-mono text-[11px] text-rose-200">
              {latest.error_message}
            </div>
          )}
        </div>
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
              <Atom className="mx-auto h-7 w-7 text-[var(--text-muted)]" />
              <p className="mt-3 text-sm text-[var(--text-secondary)]">
                Nenhum efeito posterior elegível foi persistido.
              </p>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                Runs insuficientes ou não convergentes não geram recomendações.
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
                    "Lift PnL",
                    "P(efeito +)",
                    "IC 95%",
                    "Evidência",
                    "Amostra direta",
                    "Amostra compartilhada",
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
