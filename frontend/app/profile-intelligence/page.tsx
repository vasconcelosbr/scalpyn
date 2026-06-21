"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  Brain, RefreshCw, Play, Settings, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, AlertTriangle, CheckCircle, Copy,
  ExternalLink, X, Zap, BarChart3, Eye, Users, Power, RotateCcw,
} from "lucide-react";
import { apiGet, apiPost, apiPut } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────

interface PIOverview {
  total_runs?: number;
  last_run_at?: string | null;
  last_run_status?: string | null;
  total_profiles_analyzed?: number;
  total_closed_trades?: number;
  total_combinations?: number;
  total_suggestions_pending?: number;
  total_suggestions_high_confidence?: number;
  base_win_rate?: number | null;
  best_profile_name?: string | null;
  best_profile_win_rate?: number | null;
  best_combination_name?: string | null;
  best_combination_champion_score?: number | null;
  ml_challengers?: Record<string, {
    available: boolean;
    implemented: boolean;
    installed: boolean;
    operational: boolean;
    status: string;
    effective_contribution: number;
    can_train: boolean;
    can_infer: boolean;
    can_generate_suggestions: boolean;
    influences_autopilot: boolean;
  }>;
}

interface PIRun {
  id: string;
  run_at: string;
  status: string;
  lookback_days: number;
  total_closed_trades?: number;
  total_combinations?: number;
  total_suggestions?: number;
  base_win_rate?: number | null;
  error_message?: string | null;
  trigger_source?: string | null;
}

interface ProfileRanking {
  profile_id: string;
  profile_name: string;
  total_trades?: number;
  closed_trades?: number;
  open_trades?: number;
  wins?: number;
  losses?: number;
  win_rate?: number | null;
  avg_pnl_pct?: number | null;
  pnl_total_pct?: number | null;
  avg_holding_seconds?: number | null;
  avg_mae_pct?: number | null;
  avg_mfe_pct?: number | null;
  tp_15m_rate?: number | null;
  tp_30m_rate?: number | null;
  tp_60m_rate?: number | null;
  confidence_level?: string | null;
}

interface IndicatorStat {
  id: string;
  indicator: string;
  bucket_label: string;
  total_cases: number;
  wins?: number;
  losses?: number;
  win_rate?: number | null;
  loss_rate?: number | null;
  avg_pnl_pct?: number | null;
  avg_mae_pct?: number | null;
  avg_mfe_pct?: number | null;
  tp_30m_rate?: number | null;
  avg_holding_seconds?: number | null;
  lift_vs_base?: number | null;
  confidence_level?: string | null;
  role_detected?: string | null;
  source_profiles?: any;
  source_profile_ids?: string[];
  validation_status?: string | null;
  actionability_status?: string | null;
  target_section?: string | null;
  evidence_json?: any;
}

interface Combination {
  id: string;
  combination_type: string;
  setup_family?: string | null;
  suggested_name?: string | null;
  rules_json?: any[];
  total_cases?: number;
  wins?: number;
  losses?: number;
  win_rate?: number | null;
  avg_pnl_pct?: number | null;
  avg_mae_pct?: number | null;
  avg_mfe_pct?: number | null;
  tp_30m_rate?: number | null;
  lift_vs_base?: number | null;
  champion_score?: number | null;
  confidence_level?: string | null;
  overfit_risk?: boolean;
  status?: string | null;
  discovery_metrics_json?: any;
  validation_metrics_json?: any;
  degradation_pct?: number | null;
  signals_json?: any;
  block_rules_json?: any;
  created_at?: string;
  source_profiles?: string[];
  source_profile_ids?: string[];
}

interface Suggestion {
  id: string;
  suggested_profile_name: string;
  suggested_profile_description?: string | null;
  suggested_profile_family?: string | null;
  confidence_score?: number | null;
  confidence_level?: string | null;
  status: string;
  evidence_summary_json?: any;
  quantitative_explanation?: string | null;
  ai_explanation?: string | null;
  risk_notes?: string | null;
  suggested_config_json?: any;
  suggested_signals_json?: any;
  suggested_block_rules_json?: any;
  source_combination_id?: string | null;
  source_type?: string | null;
  source_model_type?: string | null;
  source_model_id?: string | null;
  source_run_id?: string | null;
  profile_id?: string | null;
  profile_name?: string | null;
  source_profiles?: string[];
  source_profile_ids?: string[];
  target_section?: string | null;
  target_field?: string | null;
  validation_status?: string | null;
  actionability_status?: string | null;
  blocked_reason?: string | null;
  expected_impact?: any;
  risk_level?: string | null;
  evidence_count?: number | null;
  rollback_available?: boolean;
  diff_json?: any;
  created_at?: string;
}

interface AuditEntry {
  id: string;
  event_type: string;
  run_id?: string | null;
  suggestion_id?: string | null;
  combination_id?: string | null;
  event_description?: string | null;
  model_provider?: string | null;
  model_name?: string | null;
  payload_json?: any;
  result_json?: any;
  created_at: string;
}

interface CreateProfileDryRun {
  status: "dry_run";
  profile_payload: {
    name: string;
    description: string;
    profile_type: string;
    is_shadow_only: boolean;
    live_trading_enabled: boolean;
    config: {
      signals?: { logic: string; conditions: any[] };
      scoring?: { selected_rule_ids: string[]; weights: any; generated_rules: any[] };
      block_rules?: { blocks: any[] };
    };
  };
  master_rules_to_create: any[];
  master_rules_to_reuse: any[];
  master_rules_missing: any[];
  selected_rule_ids: string[];
  warnings: string[];
  blocked_reasons: string[];
  overfit_risk: boolean;
  confidence_level: string | null;
  confidence_score: number;
}

interface CreateProfileResult {
  status: "created" | "already_created";
  profile_id: string;
  profile_name: string;
  profile_url: string;
  audit_id: string | null;
  created_master_rules: any[];
  reused_master_rules: any[];
  warnings: string[];
}

interface PISettings {
  min_support?: number;
  min_closed_trades?: number;
  min_lift?: number;
  min_win_rate?: number;
  max_avg_mae?: number;
  max_avg_holding_seconds?: number;
  required_tp_30m_rate?: number;
  max_combinations_per_run?: number;
  enable_anthropic_explanations?: boolean;
  enable_optuna?: boolean;
  enable_association_rules?: boolean;
  enable_dynamic_combinations?: boolean;
  enable_lightgbm?: boolean;
  enable_catboost?: boolean;
}

interface AutopilotCandidate {
  id: string;
  profile_id: string;
  profile_name: string;
  origin_profile_id?: string | null;
  watchlist_id?: string | null;
  watchlist_name?: string | null;
  state: string;
  version_number: number;
  observed_trades: number;
  observed_win_rate?: number | null;
  observed_avg_pnl_pct?: number | null;
  approval_status: string;
  approval_required: boolean;
  approved_by?: string | null;
  approved_at?: string | null;
  approval_reason?: string | null;
  promotion_blocked_reason?: string | null;
  rollback_available: boolean;
  rollback_payload?: Record<string, any> | null;
  evidence?: Record<string, any>;
  reason?: string | null;
  updated_at: string;
}

interface AutopilotStatus {
  enabled: boolean;
  enabled_at?: string | null;
  disabled_at?: string | null;
  last_cycle_at?: string | null;
  settings: Record<string, number>;
  candidate_counts: Record<string, number>;
  latest_cycle?: {
    id: string;
    status: string;
    checkpoint?: string | null;
    window_start: string;
    completed_at?: string | null;
    metrics?: Record<string, number>;
    errors?: any[];
  } | null;
  latest_report?: any;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtPct(v: number | null | undefined, decimals = 1) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(decimals)}%`;
}

// For fields already stored as percentage points (pnl_pct, mae_pct, mfe_pct)
function fmtPctRaw(v: number | null | undefined, decimals = 2) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(decimals)}%`;
}

function fmtNum(v: number | null | undefined, decimals = 1) {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

function safeVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return v.toFixed(3);
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function fmtSeconds(s: number | null | undefined) {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

function confidenceBadge(level: string | null | undefined) {
  if (level === "HIGH") return <span className="badge bullish text-[10px]">HIGH</span>;
  if (level === "MEDIUM") return <span className="badge range text-[10px]">MEDIUM</span>;
  if (level === "NO_DATA" || level == null) return <span className="badge text-[10px]" style={{background:"rgba(107,114,128,0.2)",color:"#9ca3af"}}>NO DATA</span>;
  return <span className="badge bearish text-[10px]">LOW</span>;
}

function statusBadge(status: string | null | undefined) {
  if (!status) return <span className="text-[var(--text-tertiary)]">—</span>;
  const s = status.toLowerCase();
  if (s === "completed") return <span className="badge bullish text-[10px]">COMPLETED</span>;
  if (s === "completed_with_errors") return <span className="badge range text-[10px]">COMPLETED*</span>;
  if (s === "running") return <span className="badge range text-[10px] animate-pulse">RUNNING</span>;
  if (s === "failed") return <span className="badge bearish text-[10px]">FAILED</span>;
  if (s === "queued") return <span className="badge range text-[10px]">QUEUED</span>;
  if (s === "validated" || s === "approved" || s === "applied") {
    return <span className="badge bullish text-[10px]">{status.toUpperCase()}</span>;
  }
  if (s === "blocked" || s === "rejected" || s === "expired") {
    return <span className="badge bearish text-[10px]">{status.toUpperCase()}</span>;
  }
  if (s === "exploratory_only") {
    return <span className="badge range text-[10px]">EXPLORATÓRIO</span>;
  }
  if (s === "reverted") {
    return <span className="badge range text-[10px]">REVERTIDO</span>;
  }
  return <span className="badge range text-[10px]">{status.toUpperCase()}</span>;
}

const BLOCKED_REASON_LABELS: Record<string, string> = {
  blocked_no_validation:            "Aguardando trades de validação out-of-sample",
  blocked_low_discovery_support:    "Discovery com amostras insuficientes",
  blocked_low_validation_support:   "Validação com amostras insuficientes",
  blocked_missing_feature:          "Indicador não disponível nos dados históricos",
  blocked_validation_lift:          "Lift de validação abaixo do mínimo exigido",
  blocked_validation_winrate:       "Win rate de validação abaixo do mínimo exigido",
  blocked_single_symbol_dependency: "Dependência de símbolo único — risco de overfitting",
  blocked_single_day_dependency:    "Dependência de dia único — risco de overfitting",
  migration_requires_registry_review: "Combinação legada — revisão de registro necessária",
  exploratory_only:                 "Apenas exploratório — validação pendente",
};

function blockedReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "Validação pendente";
  return BLOCKED_REASON_LABELS[reason] ?? reason;
}

function combinationTypeBadge(type: string) {
  const map: Record<string, string> = {
    counterfactual_seed: "bullish",
    counterfactual_dynamic: "range",
    association_rule: "bullish",
    optuna: "range",
    existing_profile: "range",
    ai_suggested: "bullish",
  };
  return (
    <span className={`badge ${map[type] || "range"} text-[10px]`}>
      {type.replace(/_/g, " ")}
    </span>
  );
}

function validationBadge(combination: Combination) {
  const validation = combination.validation_metrics_json || {};
  const status = validation.validation_status || combination.status;
  if (status === "validated") {
    return <span className="badge bullish text-[10px]">VALIDADO</span>;
  }
  if (!status || status === "discovered") {
    return <span className="badge range text-[10px]">EXPLORATÓRIO</span>;
  }
  return <span className="badge bearish text-[10px]">BLOQUEADO</span>;
}

function combinationIsActionable(combination: Combination) {
  if (!["counterfactual_dynamic", "association_rule", "optuna"].includes(combination.combination_type)) {
    return true;
  }
  const validation = combination.validation_metrics_json || {};
  if (validation.validation_status !== "validated") return false;
  return combination.combination_type !== "association_rule"
    || validation.actionability_status === "positive_signal_candidate";
}

function suggestionIsActionable(suggestion: Suggestion) {
  return suggestion.validation_status === "validated"
    && !["exploratory_only", "not_actionable"].includes(
      suggestion.actionability_status || "",
    )
    && Boolean(suggestion.source_type)
    && Boolean(suggestion.source_run_id)
    && Boolean(suggestion.profile_id)
    && Boolean(suggestion.rollback_available);
}

function winRateColor(wr: number | null | undefined) {
  if (wr == null) return "text-[var(--text-tertiary)]";
  if (wr >= 0.55) return "text-green-400";
  if (wr >= 0.40) return "text-yellow-400";
  return "text-red-400";
}

function pnlColor(v: number | null | undefined) {
  if (v == null) return "text-[var(--text-tertiary)]";
  return v >= 0 ? "text-green-400" : "text-red-400";
}

function ChampionScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="text-[var(--text-tertiary)]">—</span>;
  const pct = Math.min(100, Math.max(0, score));
  const color = pct >= 60 ? "bg-green-500" : pct >= 35 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1.5 bg-[var(--bg-hover)] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[11px] text-[var(--text-secondary)] w-8 text-right">{pct.toFixed(0)}</span>
    </div>
  );
}

const TABS = ["Overview", "Auto-Pilot", "Profiles", "Indicators", "Combinations", "Suggestions", "Audit", "Settings"] as const;
type Tab = typeof TABS[number];

const DEFAULT_RUN_PAYLOAD = {
  lookback_days: 7,
  min_closed_trades: 30,
  include_counterfactual: true,
  include_dynamic_combinations: true,
  include_association_rules: true,
  include_optuna: false,
  include_ai_explanation: false,
  max_combinations: 500,
};

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function ProfileIntelligencePage() {
  const [activeTab, setActiveTab] = useState<Tab>("Overview");

  // Data state
  const [overview, setOverview] = useState<PIOverview | null>(null);
  const [runs, setRuns] = useState<PIRun[]>([]);
  const [profiles, setProfiles] = useState<ProfileRanking[]>([]);
  const [winners, setWinners] = useState<IndicatorStat[]>([]);
  const [losers, setLosers] = useState<IndicatorStat[]>([]);
  const [combinations, setCombinations] = useState<Combination[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [settings, setSettings] = useState<PISettings>({});
  const [autopilot, setAutopilot] = useState<AutopilotStatus | null>(null);
  const [autopilotCandidates, setAutopilotCandidates] = useState<AutopilotCandidate[]>([]);
  const [autopilotAudit, setAutopilotAudit] = useState<any[]>([]);
  const [togglingAutopilot, setTogglingAutopilot] = useState(false);
  const [runningAutopilot, setRunningAutopilot] = useState(false);
  const [candidateActionId, setCandidateActionId] = useState<string | null>(null);
  const [selectedAutopilotCandidate, setSelectedAutopilotCandidate] = useState<AutopilotCandidate | null>(null);

  // Loading
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [loadingTab, setLoadingTab] = useState(false);

  // UI state
  const [indicatorSubTab, setIndicatorSubTab] = useState<"winners" | "losers">("winners");
  const [expandedIndicator, setExpandedIndicator] = useState<string | null>(null);
  const [selectedCombination, setSelectedCombination] = useState<Combination | null>(null);
  const [selectedSuggestion, setSelectedSuggestion] = useState<Suggestion | null>(null);
  const [expandedAudit, setExpandedAudit] = useState<string | null>(null);
  const [showRunModal, setShowRunModal] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [runPayload, setRunPayload] = useState({ ...DEFAULT_RUN_PAYLOAD });
  const [runResult, setRunResult] = useState<any>(null);
  const [running, setRunning] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [explainingId, setExplainingId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  // Create-profile flow
  const [createProfileSuggestion, setCreateProfileSuggestion] = useState<Suggestion | null>(null);
  const [createDryRunResult, setCreateDryRunResult] = useState<CreateProfileDryRun | null>(null);
  const [createDryRunLoading, setCreateDryRunLoading] = useState(false);
  const [createProfileLoading, setCreateProfileLoading] = useState(false);
  const [createProfileResult, setCreateProfileResult] = useState<CreateProfileResult | null>(null);
  const [confirmLowConfidence, setConfirmLowConfidence] = useState(false);
  const [confirmOverfitRisk, setConfirmOverfitRisk] = useState(false);

  // Sort state for profiles table
  const [profileSort, setProfileSort] = useState<{ col: string; asc: boolean }>({ col: "win_rate", asc: false });

  // Duplicate profile group drawer
  const [selectedDuplicateGroup, setSelectedDuplicateGroup] = useState<ProfileRanking[] | null>(null);

  // Combination dedup toggle
  const [hideComboDuplicates, setHideComboDuplicates] = useState(true);

  // Generate suggestion from combination
  const [generatingSuggestion, setGeneratingSuggestion] = useState(false);
  const [lastGeneratedSuggestionId, setLastGeneratedSuggestionId] = useState<string | null>(null);

  const showToast = (msg: string, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 4000);
  };

  const handleGenerateSuggestion = async (combinationId: string) => {
    setGeneratingSuggestion(true);
    setLastGeneratedSuggestionId(null);
    try {
      const res = await apiPost(`/api/profile-intelligence/combinations/${combinationId}/create-suggestion`, {});
      showToast(res.created ? "Suggestion gerada com sucesso" : "Suggestion já existia — abrindo", true);
      setLastGeneratedSuggestionId(res.suggestion?.id || null);
      const data = await apiGet("/api/profile-intelligence/suggestions?limit=50");
      setSuggestions(data.suggestions || []);
    } catch (e: any) {
      showToast(`Erro ao gerar suggestion: ${e.message || "unknown"}`, false);
    } finally {
      setGeneratingSuggestion(false);
    }
  };

  // ── Data fetching ───────────────────────────────────────────────────────────

  const loadOverview = useCallback(async () => {
    setLoadingOverview(true);
    try {
      const [ov, r, ap] = await Promise.all([
        apiGet("/profile-intelligence/overview").catch(() => apiGet("/profile-intelligence/")),
        apiGet("/profile-intelligence/runs").catch(() => ({ runs: [] })),
        apiGet("/profile-intelligence/autopilot").catch(() => null),
      ]);
      setOverview(ov || {});
      setRuns(r?.runs || r || []);
      setAutopilot(ap);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  const loadTab = useCallback(async (tab: Tab) => {
    if (tab === "Overview") return;
    setLoadingTab(true);
    try {
      if (tab === "Auto-Pilot") {
        const [status, candidates, events] = await Promise.all([
          apiGet("/profile-intelligence/autopilot"),
          apiGet("/profile-intelligence/autopilot/candidates?limit=100"),
          apiGet("/profile-intelligence/autopilot/audit?limit=100"),
        ]);
        setAutopilot(status);
        setAutopilotCandidates(candidates?.candidates || []);
        setAutopilotAudit(events?.events || []);
      } else if (tab === "Profiles") {
        const d = await apiGet("/profile-intelligence/profiles/ranking?lookback_days=60&limit=30");
        setProfiles(d?.profiles || d || []);
      } else if (tab === "Indicators") {
        const [w, l] = await Promise.all([
          apiGet("/profile-intelligence/indicators/top-winners?limit=50"),
          apiGet("/profile-intelligence/indicators/top-losers?limit=50"),
        ]);
        setWinners(w?.indicators || w || []);
        setLosers(l?.indicators || l || []);
      } else if (tab === "Combinations") {
        const d = await apiGet("/profile-intelligence/combinations?limit=100");
        setCombinations(d?.combinations || d || []);
      } else if (tab === "Suggestions") {
        const d = await apiGet("/profile-intelligence/suggestions?limit=50");
        setSuggestions(d?.suggestions || d || []);
      } else if (tab === "Audit") {
        const d = await apiGet("/profile-intelligence/audit?limit=100");
        setAudit(d?.events || d?.audit_log || d?.logs || []);
      } else if (tab === "Settings") {
        const d = await apiGet("/profile-intelligence/settings");
        setSettings(d?.settings || d || {});
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingTab(false);
    }
  }, []);

  useEffect(() => { loadOverview(); }, [loadOverview]);
  useEffect(() => { loadTab(activeTab); }, [activeTab, loadTab]);

  const handleRefresh = () => {
    loadOverview();
    if (activeTab !== "Overview") loadTab(activeTab);
  };

  const handleToggleAutopilot = async () => {
    const next = !autopilot?.enabled;
    setTogglingAutopilot(true);
    try {
      const result = await apiPut("/profile-intelligence/autopilot", { enabled: next });
      setAutopilot(prev => ({ ...(prev || {} as AutopilotStatus), ...result, enabled: next }));
      showToast(
        next && result?.cycle_status === "queued"
          ? "Auto-Pilot global ligado. Primeiro ciclo enfileirado."
          : next
            ? "Auto-Pilot global ligado."
            : "Auto-Pilot global desligado."
      );
      await loadOverview();
      if (next && result?.cycle_status === "queued") {
        setTimeout(() => { loadOverview(); }, 3000);
      }
    } catch (e: any) {
      showToast(`Erro ao alterar Auto-Pilot: ${e.message}`, false);
    } finally {
      setTogglingAutopilot(false);
    }
  };

  const handleRunAutopilot = async () => {
    setRunningAutopilot(true);
    try {
      await apiPost("/profile-intelligence/autopilot/run-cycle", {});
      showToast("Ciclo do Auto-Pilot enfileirado. Aguardando conclusão...");
      // Polling até o ciclo concluir (máx 60 tentativas × 3s = 3min)
      const prevCycleAt = autopilot?.last_cycle_at;
      let attempts = 0;
      while (attempts < 60) {
        await new Promise((r) => setTimeout(r, 3000));
        attempts++;
        try {
          const fresh = await apiGet("/profile-intelligence/autopilot");
          setAutopilot((prev) => ({ ...(prev || {} as AutopilotStatus), ...fresh }));
          const done = fresh?.latest_cycle?.status === "completed" ||
                       fresh?.latest_cycle?.status === "failed" ||
                       (fresh?.last_cycle_at && fresh.last_cycle_at !== prevCycleAt);
          if (done) break;
        } catch {
          // ignora erros de polling
        }
      }
      await loadTab("Auto-Pilot");
      showToast("Ciclo do Auto-Pilot concluído.");
    } catch (e: any) {
      showToast(`Erro ao iniciar ciclo: ${e.message}`, false);
    } finally {
      setRunningAutopilot(false);
    }
  };

  const refreshAutopilot = async () => {
    await loadTab("Auto-Pilot");
  };

  const authenticatedUserId = () => {
    const raw = typeof window !== "undefined" ? localStorage.getItem("user") : null;
    if (!raw) return null;
    try {
      const user = JSON.parse(raw);
      return user?.id || user?.user_id || null;
    } catch {
      return null;
    }
  };

  const handleApproveCandidate = async (candidate: AutopilotCandidate) => {
    const confirmed = window.confirm(
      "Confirmo que revisei as métricas shadow e autorizo este candidato para ativação live."
    );
    if (!confirmed) return;
    const reason = window.prompt("Informe o motivo técnico da aprovação:");
    const approvedBy = authenticatedUserId();
    if (!reason?.trim() || !approvedBy) {
      showToast("Aprovação exige motivo e usuário autenticado.", false);
      return;
    }
    setCandidateActionId(candidate.id);
    try {
      await apiPost(`/profile-intelligence/autopilot/candidates/${candidate.id}/approve`, {
        approved_by: approvedBy,
        approval_reason: reason.trim(),
        confirm_risk: true,
        approval_source: "profile_intelligence_ui",
      });
      showToast("Candidato aprovado. A ativação live continua separada.");
      await refreshAutopilot();
    } catch (e: any) {
      showToast(`Erro ao aprovar candidato: ${e.message}`, false);
    } finally {
      setCandidateActionId(null);
    }
  };

  const handleRejectCandidate = async (candidate: AutopilotCandidate) => {
    const reason = window.prompt("Informe o motivo da rejeição:");
    if (!reason?.trim()) return;
    setCandidateActionId(candidate.id);
    try {
      await apiPost(`/profile-intelligence/autopilot/candidates/${candidate.id}/reject`, {
        rejection_reason: reason.trim(),
      });
      showToast("Candidato rejeitado.");
      await refreshAutopilot();
    } catch (e: any) {
      showToast(`Erro ao rejeitar candidato: ${e.message}`, false);
    } finally {
      setCandidateActionId(null);
    }
  };

  const handleActivateCandidate = async (candidate: AutopilotCandidate) => {
    if (!window.confirm("Ativar este candidato aprovado em live agora?")) return;
    setCandidateActionId(candidate.id);
    try {
      await apiPost(`/profile-intelligence/autopilot/candidates/${candidate.id}/activate`, {});
      showToast("Candidato ativado em live.");
      await refreshAutopilot();
    } catch (e: any) {
      showToast(`Ativação bloqueada: ${e.message}`, false);
    } finally {
      setCandidateActionId(null);
    }
  };

  const handleRollbackCandidate = async (candidate: AutopilotCandidate) => {
    if (!window.confirm("Executar rollback e restaurar o profile incumbent?")) return;
    setCandidateActionId(candidate.id);
    try {
      await apiPost(`/profile-intelligence/autopilot/candidates/${candidate.id}/rollback`, {});
      showToast("Rollback executado.");
      await refreshAutopilot();
    } catch (e: any) {
      showToast(`Erro no rollback: ${e.message}`, false);
    } finally {
      setCandidateActionId(null);
    }
  };

  // ── Run Analysis ─────────────────────────────────────────────────────────────

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const res = await apiPost("/profile-intelligence/run", runPayload);
      setRunResult({ ok: true, run_id: res?.run_id, status: res?.status });
      showToast(`Análise iniciada. Run ID: ${res?.run_id || "—"}`);
      setTimeout(() => { loadOverview(); }, 2000);
    } catch (e: any) {
      setRunResult({ ok: false, error: e.message });
      showToast(`Erro: ${e.message}`, false);
    } finally {
      setRunning(false);
    }
  };

  // ── Explain with AI ──────────────────────────────────────────────────────────

  const handleExplain = async (suggestionId: string) => {
    setExplainingId(suggestionId);
    try {
      const res = await apiPost(`/profile-intelligence/suggestions/${suggestionId}/explain`);
      showToast("Explicação gerada com sucesso.");
      // Refresh suggestion detail
      if (selectedSuggestion?.id === suggestionId) {
        const updated = await apiGet(`/profile-intelligence/suggestions/${suggestionId}`);
        setSelectedSuggestion(updated?.suggestion || updated);
      }
      // Refresh list
      const d = await apiGet("/profile-intelligence/suggestions?limit=50");
      setSuggestions(d?.suggestions || d || []);
    } catch (e: any) {
      showToast(`Erro ao explicar: ${e.message}`, false);
    } finally {
      setExplainingId(null);
    }
  };

  // ── Create Profile from Suggestion ──────────────────────────────────────────

  const handleOpenCreateProfile = async (suggestion: Suggestion) => {
    setCreateProfileSuggestion(suggestion);
    setCreateDryRunResult(null);
    setCreateProfileResult(null);
    setConfirmLowConfidence(false);
    setConfirmOverfitRisk(false);
    setCreateDryRunLoading(true);
    try {
      const res = await apiPost(`/profile-intelligence/suggestions/${suggestion.id}/create-profile`, {
        dry_run: true,
        mode: "SHADOW_ONLY",
        confirm_low_confidence: suggestion.confidence_level !== "LOW",
        confirm_overfit_risk: false,
        create_missing_master_rules: true,
        reuse_existing_master_rules: true,
      });
      setCreateDryRunResult(res as CreateProfileDryRun);
    } catch (e: any) {
      showToast(`Dry-run falhou: ${e.message}`, false);
      setCreateProfileSuggestion(null);
    } finally {
      setCreateDryRunLoading(false);
    }
  };

  const handleConfirmCreateProfile = async () => {
    if (!createProfileSuggestion) return;
    setCreateProfileLoading(true);
    try {
      const res = await apiPost(`/profile-intelligence/suggestions/${createProfileSuggestion.id}/create-profile`, {
        dry_run: false,
        mode: "SHADOW_ONLY",
        confirm_low_confidence: confirmLowConfidence || createProfileSuggestion.confidence_level !== "LOW",
        confirm_overfit_risk: confirmOverfitRisk || !(createDryRunResult?.overfit_risk),
        create_missing_master_rules: true,
        reuse_existing_master_rules: true,
      });
      setCreateProfileResult(res as CreateProfileResult);
      showToast(`Profile criado: ${(res as any).profile_name}`);
      // Refresh suggestions list
      const d = await apiGet("/profile-intelligence/suggestions?limit=50");
      setSuggestions(d?.suggestions || d || []);
      // Refresh audit
      const a = await apiGet("/profile-intelligence/audit?limit=100");
      setAudit(a?.events || []);
    } catch (e: any) {
      showToast(`Erro ao criar profile: ${e.message}`, false);
    } finally {
      setCreateProfileLoading(false);
    }
  };

  // ── Save Settings ─────────────────────────────────────────────────────────────

  const handleSaveSettings = async () => {
    setSavingSettings(true);
    try {
      const response = await apiPut("/profile-intelligence/settings", settings);
      setSettings(response?.settings || settings);
      showToast("Configurações salvas.");
    } catch (e: any) {
      showToast(`Erro: ${e.message}`, false);
    } finally {
      setSavingSettings(false);
    }
  };

  // ── Profiles sort ────────────────────────────────────────────────────────────

  const sortedProfiles = [...profiles].sort((a, b) => {
    const col = profileSort.col as keyof ProfileRanking;
    const av = (a[col] as number) ?? -Infinity;
    const bv = (b[col] as number) ?? -Infinity;
    return profileSort.asc ? av - bv : bv - av;
  });

  const toggleProfileSort = (col: string) => {
    setProfileSort(prev => prev.col === col ? { col, asc: !prev.asc } : { col, asc: false });
  };

  // Group sorted profiles by name — collapses duplicate-named profiles into one row
  const profileGroups = useMemo(() => {
    const map = new Map<string, ProfileRanking[]>();
    for (const p of sortedProfiles) {
      if (!map.has(p.profile_name)) map.set(p.profile_name, []);
      map.get(p.profile_name)!.push(p);
    }
    return Array.from(map.values());
  }, [sortedProfiles]);

  function aggregateGroup(items: ProfileRanking[]): ProfileRanking & { _count: number } {
    const totalClosed = items.reduce((s, p) => s + (p.closed_trades ?? 0), 0);
    const totalOpen = items.reduce((s, p) => s + (p.open_trades ?? 0), 0);
    const totalWins = items.reduce((s, p) => s + (p.wins ?? 0), 0);
    const totalLosses = items.reduce((s, p) => s + (p.losses ?? 0), 0);
    const withPnl = items.filter(p => p.avg_pnl_pct != null);
    const withMae = items.filter(p => p.avg_mae_pct != null);
    const withTp = items.filter(p => p.tp_30m_rate != null);
    const levels = items.map(p => p.confidence_level);
    const bestLevel = levels.includes("HIGH") ? "HIGH" : levels.includes("MEDIUM") ? "MEDIUM" : levels.includes("LOW") ? "LOW" : null;
    return {
      profile_id: items[0].profile_id,
      profile_name: items[0].profile_name,
      closed_trades: totalClosed,
      open_trades: totalOpen,
      wins: totalWins,
      losses: totalLosses,
      win_rate: totalWins + totalLosses > 0 ? totalWins / (totalWins + totalLosses) : null,
      avg_pnl_pct: withPnl.length > 0 ? withPnl.reduce((s, p) => s + p.avg_pnl_pct!, 0) / withPnl.length : null,
      avg_mae_pct: withMae.length > 0 ? withMae.reduce((s, p) => s + p.avg_mae_pct!, 0) / withMae.length : null,
      tp_30m_rate: withTp.length > 0 ? withTp.reduce((s, p) => s + p.tp_30m_rate!, 0) / withTp.length : null,
      confidence_level: bestLevel,
      _count: items.length,
    };
  }

  // ── Combination deduplication ────────────────────────────────────────────────

  function buildComboCanonicalKey(rules_json: any[] | null | undefined): string {
    if (!rules_json?.length) return "";
    return rules_json
      .map(r => {
        const indicator = r.indicator || r.field || r.item || "";
        const operator = r.operator || "";
        const raw = r.value;
        const value = typeof raw === "number"
          ? raw.toFixed(8).replace(/\.?0+$/, "")
          : String(raw ?? "");
        return `${indicator}|${operator}|${value}`;
      })
      .sort()
      .join("||");
  }

  const displayedCombinations = useMemo(() => {
    if (!hideComboDuplicates) return combinations;
    const best = new Map<string, Combination>();
    for (const c of combinations) {
      const key = buildComboCanonicalKey(c.rules_json) || c.id;
      const existing = best.get(key);
      if (!existing || (c.champion_score ?? 0) > (existing.champion_score ?? 0)) {
        best.set(key, c);
      }
    }
    return [...best.values()].sort((a, b) => (b.champion_score ?? 0) - (a.champion_score ?? 0));
  }, [combinations, hideComboDuplicates]);

  const hiddenComboCount = combinations.length - displayedCombinations.length;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-5 pb-10">

      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg text-[13px] font-medium shadow-lg ${toast.ok ? "bg-green-600 text-white" : "bg-red-600 text-white"}`}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <div className="flex justify-between items-start gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)] flex items-center gap-2">
            <Brain className="w-6 h-6 text-blue-400" />
            Profile Intelligence
          </h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Mapa de oportunidades, indicadores campeões e combinações ocultas
          </p>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            {overview?.last_run_at && (
              <span className="text-[11px] text-[var(--text-tertiary)]">
                Última execução: {fmtDate(overview.last_run_at)}
              </span>
            )}
            {overview?.last_run_status && statusBadge(overview.last_run_status)}
            {(overview?.total_suggestions_pending ?? 0) > 0 && (
              <span className="badge range text-[10px]">
                {overview!.total_suggestions_pending} sugestões pendentes
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            className={`btn text-[12px] flex items-center gap-1.5 ${
              autopilot?.enabled
                ? "bg-green-600/20 text-green-400 border border-green-500/40"
                : "btn-secondary"
            }`}
            onClick={handleToggleAutopilot}
            disabled={togglingAutopilot}
            title="Controle global por conta"
          >
            <Power className="w-3.5 h-3.5" />
            Auto-Pilot {autopilot?.enabled ? "Ligado" : "Desligado"}
          </button>
          <button
            className="btn btn-secondary text-[12px] flex items-center gap-1.5"
            onClick={handleRefresh}
            disabled={loadingOverview || loadingTab}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${(loadingOverview || loadingTab) ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            className="btn btn-secondary text-[12px] flex items-center gap-1.5"
            onClick={() => setShowSettingsModal(true)}
          >
            <Settings className="w-3.5 h-3.5" />
            Settings
          </button>
          <button
            className="btn btn-primary text-[12px] flex items-center gap-1.5"
            onClick={() => { setRunResult(null); setShowRunModal(true); }}
          >
            <Play className="w-3.5 h-3.5" />
            Run Analysis
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[var(--border-default)] overflow-x-auto">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2.5 text-[13px] font-medium whitespace-nowrap border-b-2 transition-colors ${
              activeTab === tab
                ? "border-[var(--accent-primary)] text-[var(--accent-primary)]"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* ── TAB: Overview ──────────────────────────────────────────────────────── */}
      {activeTab === "Overview" && (
        <div className="space-y-5">
          {loadingOverview ? (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[...Array(10)].map((_, i) => <div key={i} className="skeleton h-24 rounded-[var(--radius-lg)]" />)}
            </div>
          ) : (
            <>
              {/* Executive cards */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {[
                  { label: "Profiles Analisados", value: overview?.total_profiles_analyzed ?? "—", hint: "Profiles com shadow trades no período" },
                  { label: "Trades Fechados", value: overview?.total_closed_trades ?? "—", hint: "Shadow trades com desfecho (TP/SL/Timeout)" },
                  { label: "Win Rate Base", value: overview?.base_win_rate != null ? `${(overview.base_win_rate * 100).toFixed(1)}%` : "—", hint: "Win rate geral de todos os trades fechados" },
                  { label: "Melhor Profile", value: overview?.best_profile_name || "—", sub: overview?.best_profile_win_rate != null ? `${(overview.best_profile_win_rate * 100).toFixed(1)}% WR` : "", hint: "Profile com maior win rate" },
                  { label: "Melhor Combinação", value: overview?.best_combination_name || "—", sub: overview?.best_combination_champion_score != null ? `Score: ${overview.best_combination_champion_score.toFixed(0)}` : "", hint: "Combinação com maior champion score" },
                  { label: "Combinações", value: overview?.total_combinations ?? "—", hint: "Combinações de indicadores descobertas" },
                  { label: "Sugestões Pendentes", value: overview?.total_suggestions_pending ?? "—", hint: "Sugestões aguardando revisão" },
                  { label: "Alta Confiança", value: overview?.total_suggestions_high_confidence ?? "—", hint: "Sugestões com ≥100 trades de suporte" },
                  { label: "Total de Runs", value: overview?.total_runs ?? "—", hint: "Execuções do PI Engine" },
                  { label: "Status", value: overview?.last_run_status ? overview.last_run_status.toUpperCase() : "—", hint: "Status da última execução" },
                ].map((card, i) => (
                  <div key={i} className="card p-3 space-y-1" title={card.hint}>
                    <div className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">{card.label}</div>
                    <div className="text-[18px] font-bold text-[var(--text-primary)] truncate">{String(card.value)}</div>
                    {card.sub && <div className="text-[11px] text-[var(--text-secondary)]">{card.sub}</div>}
                  </div>
                ))}
              </div>

              {/* Runs history */}
              <div className="card">
                <div className="p-4 border-b border-[var(--border-default)]">
                  <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Histórico de Execuções</h2>
                </div>
                {runs.length === 0 ? (
                  <EmptyState message="Nenhuma execução encontrada. Execute uma análise para começar." onRun={() => setShowRunModal(true)} />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-[12px]">
                      <thead>
                        <tr className="border-b border-[var(--border-subtle)]">
                          {["Data", "Origem", "Status", "Lookback", "Trades", "Combinações", "Sugestões", "Win Rate", "Erro"].map(h => (
                            <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border-subtle)]">
                        {runs.map(run => (
                          <tr key={run.id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                            <td className="px-4 py-2.5 font-mono text-[var(--text-secondary)] whitespace-nowrap">{fmtDate(run.run_at)}</td>
                            <td className="px-4 py-2.5">
                              {run.trigger_source === "manual"
                                ? <span className="badge range text-[10px]">manual</span>
                                : run.trigger_source === "beat"
                                  ? <span className="badge bullish text-[10px]">auto</span>
                                  : <span className="text-[var(--text-tertiary)] text-[10px]">—</span>}
                            </td>
                            <td className="px-4 py-2.5">{statusBadge(run.status)}</td>
                            <td className="px-4 py-2.5 text-[var(--text-secondary)]">{run.lookback_days}d</td>
                            <td className="px-4 py-2.5 text-[var(--text-primary)]">{run.total_closed_trades ?? "—"}</td>
                            <td className="px-4 py-2.5 text-[var(--text-primary)]">{run.total_combinations ?? "—"}</td>
                            <td className="px-4 py-2.5 text-[var(--text-primary)]">{run.total_suggestions ?? "—"}</td>
                            <td className="px-4 py-2.5">{run.base_win_rate != null ? `${(run.base_win_rate * 100).toFixed(1)}%` : "—"}</td>
                            <td className="px-4 py-2.5 text-red-400 max-w-[180px] truncate" title={run.error_message || ""}>{run.error_message || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Safety notice */}
              <div className="card p-4 border-yellow-500/20 bg-yellow-500/5">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-yellow-400 mt-0.5 shrink-0" />
                  <div className="text-[12px] text-[var(--text-secondary)] space-y-1">
                    <p><strong className="text-yellow-400">Aviso operacional:</strong> O Profile Intelligence Engine é analítico. Sugestões são <em>hipóteses</em>, não recomendações operacionais.</p>
                    <p>Com o Auto-Pilot ligado, clones continuam evoluindo em Shadow. Qualquer ativação live exige aprovação humana explícita e uma ação separada de ativação.</p>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === "Auto-Pilot" && (
        <div className="space-y-5">
          <div className="card p-4">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <div className="flex items-center gap-2">
                  <Power className={`w-5 h-5 ${autopilot?.enabled ? "text-green-400" : "text-[var(--text-tertiary)]"}`} />
                  <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
                    Auto-Pilot global {autopilot?.enabled ? "ligado" : "desligado"}
                  </h2>
                </div>
                <p className="text-[12px] text-[var(--text-secondary)] mt-1 max-w-3xl">
                  Calibra clones versionados, testa candidatos em Shadow e prepara recomendações.
                  Promoção live automática está bloqueada; aprovação e ativação são ações humanas separadas.
                </p>
                <p className="text-[11px] text-[var(--text-tertiary)] mt-2">
                  Último ciclo: {fmtDate(autopilot?.last_cycle_at)}
                  {autopilot?.latest_cycle?.status ? ` · ${autopilot.latest_cycle.status}` : ""}
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  className="btn btn-secondary text-[12px] flex items-center gap-1.5"
                  onClick={handleRunAutopilot}
                  disabled={!autopilot?.enabled || runningAutopilot}
                  title={!autopilot?.enabled ? "Ligue o Auto-Pilot para executar um ciclo" : "Executar ciclo agora"}
                >
                  <RotateCcw className={`w-3.5 h-3.5 ${runningAutopilot ? "animate-spin" : ""}`} />
                  Executar ciclo
                </button>
                <button
                  className={`btn text-[12px] ${autopilot?.enabled ? "btn-secondary" : "btn-primary"}`}
                  onClick={handleToggleAutopilot}
                  disabled={togglingAutopilot}
                >
                  {autopilot?.enabled ? "Desligar" : "Ligar"}
                </button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {[
              ["Coletando", autopilot?.candidate_counts?.SHADOW_COLLECTING ?? 0],
              ["Prontos", autopilot?.candidate_counts?.SHADOW_READY ?? 0],
              ["Aguardando aprovação", autopilot?.candidate_counts?.PENDING_HUMAN_APPROVAL ?? 0],
              ["Aprovados", autopilot?.candidate_counts?.APPROVED_FOR_LIVE ?? 0],
              ["Live", autopilot?.candidate_counts?.LIVE_ACTIVATED ?? 0],
              ["Rollbacks", autopilot?.candidate_counts?.ROLLED_BACK ?? 0],
            ].map(([label, value]) => (
              <div className="card p-3" key={String(label)}>
                <div className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">{label}</div>
                <div className="text-xl font-bold text-[var(--text-primary)] mt-1">{value}</div>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Candidatos e versões</h2>
              <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
                Win Rate inclui a contagem de trades; P&amp;L usa representação decimal canônica.
              </p>
            </div>
            {loadingTab ? <TableSkeleton /> : autopilotCandidates.length === 0 ? (
              <div className="p-8 text-center text-[12px] text-[var(--text-tertiary)]">Nenhum candidato criado.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-subtle)]">
                      {["Profile", "Versão", "Estado", "Trades", "Win Rate", "P&L médio", "Rollback", "Atualizado", "Ações"].map(h => (
                        <th key={h} className="px-4 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-tertiary)] whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-subtle)]">
                    {autopilotCandidates.map(candidate => (
                      <tr key={candidate.id}>
                        <td className="px-4 py-2.5 text-[var(--text-primary)]">{candidate.profile_name}</td>
                        <td className="px-4 py-2.5 font-mono">v{candidate.version_number}</td>
                        <td className="px-4 py-2.5">{statusBadge(candidate.state)}</td>
                        <td className="px-4 py-2.5">{candidate.observed_trades}</td>
                        <td className={`px-4 py-2.5 ${winRateColor(candidate.observed_win_rate)}`}>
                          {candidate.observed_win_rate == null ? "—" : `${(candidate.observed_win_rate * 100).toFixed(1)}%`}
                        </td>
                        <td className={`px-4 py-2.5 ${pnlColor(candidate.observed_avg_pnl_pct)}`}>
                          {fmtPct(candidate.observed_avg_pnl_pct, 2)}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={candidate.rollback_available ? "text-green-400" : "text-red-400"}>
                            {candidate.rollback_available ? "Disponível" : "Ausente"}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 whitespace-nowrap">{fmtDate(candidate.updated_at)}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex gap-1.5 flex-wrap min-w-[250px]">
                            <button className="btn btn-secondary text-[10px]" onClick={() => setSelectedAutopilotCandidate(candidate)}>
                              Ver detalhes
                            </button>
                            {candidate.state === "PENDING_HUMAN_APPROVAL" && (
                              <>
                                <button
                                  className="btn btn-primary text-[10px]"
                                  disabled={candidateActionId === candidate.id || !candidate.rollback_available}
                                  onClick={() => handleApproveCandidate(candidate)}
                                >
                                  Aprovar
                                </button>
                                <button
                                  className="btn btn-secondary text-[10px] text-red-400"
                                  disabled={candidateActionId === candidate.id}
                                  onClick={() => handleRejectCandidate(candidate)}
                                >
                                  Rejeitar
                                </button>
                              </>
                            )}
                            {candidate.state === "APPROVED_FOR_LIVE" && (
                              <button
                                className="btn btn-primary text-[10px]"
                                disabled={candidateActionId === candidate.id}
                                onClick={() => handleActivateCandidate(candidate)}
                              >
                                Ativar live
                              </button>
                            )}
                            {candidate.state === "LIVE_ACTIVATED" && (
                              <button
                                className="btn btn-secondary text-[10px]"
                                disabled={candidateActionId === candidate.id}
                                onClick={() => handleRollbackCandidate(candidate)}
                              >
                                Rollback
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Relatório Executivo</h2>
            </div>
            <pre className="p-4 text-[11px] text-[var(--text-secondary)] overflow-auto max-h-[420px]">
              {autopilot?.latest_report
                ? JSON.stringify(autopilot.latest_report, null, 2)
                : "Nenhum relatório diário disponível."}
            </pre>
          </div>

          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Auditoria imutável</h2>
            </div>
            <div className="divide-y divide-[var(--border-subtle)] max-h-[420px] overflow-auto">
              {autopilotAudit.map(event => (
                <div key={event.id} className="p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-[11px] text-[var(--text-primary)]">{event.event_type}</span>
                    <span className="text-[10px] text-[var(--text-tertiary)]">{fmtDate(event.created_at)}</span>
                  </div>
                  <div className="text-[11px] text-[var(--text-secondary)] mt-1">
                    {event.decision || "—"} · {event.reason || "Sem motivo informado"}
                  </div>
                </div>
              ))}
              {!autopilotAudit.length && <div className="p-6 text-center text-[12px] text-[var(--text-tertiary)]">Sem eventos.</div>}
            </div>
          </div>
        </div>
      )}

      {/* ── TAB: Profiles ──────────────────────────────────────────────────────── */}
      {selectedAutopilotCandidate && (
        <Modal
          title={`Candidato · ${selectedAutopilotCandidate.profile_name}`}
          onClose={() => setSelectedAutopilotCandidate(null)}
        >
          <div className="space-y-3 text-[12px]">
            <div className="grid grid-cols-2 gap-2">
              {[
                ["Estado", selectedAutopilotCandidate.state],
                ["Trades", selectedAutopilotCandidate.observed_trades],
                ["Win Rate", selectedAutopilotCandidate.observed_win_rate == null ? "—" : `${(selectedAutopilotCandidate.observed_win_rate * 100).toFixed(1)}%`],
                ["P&L médio", fmtPct(selectedAutopilotCandidate.observed_avg_pnl_pct, 2)],
                ["Aprovação", selectedAutopilotCandidate.approval_status],
                ["Rollback", selectedAutopilotCandidate.rollback_available ? "Disponível" : "Ausente"],
              ].map(([label, value]) => (
                <div key={String(label)} className="bg-[var(--bg-elevated)] rounded p-2">
                  <div className="text-[10px] text-[var(--text-tertiary)]">{label}</div>
                  <div className="text-[var(--text-primary)]">{String(value)}</div>
                </div>
              ))}
            </div>
            <div>
              <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Motivo</div>
              <div className="text-[var(--text-secondary)]">{selectedAutopilotCandidate.reason || "—"}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Evidências e riscos</div>
              <pre className="mt-1 p-3 rounded bg-[var(--bg-elevated)] overflow-auto max-h-[320px] text-[10px]">
                {JSON.stringify(selectedAutopilotCandidate.evidence || {}, null, 2)}
              </pre>
            </div>
          </div>
        </Modal>
      )}

      {activeTab === "Profiles" && (
        <div className="card">
          <div className="p-4 border-b border-[var(--border-default)]">
            <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Ranking de Profiles</h2>
            <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">Performance baseada em shadow trades fechados. Clique nas colunas para ordenar.</p>
          </div>
          {loadingTab ? <TableSkeleton /> : profiles.length === 0 ? (
            <EmptyState message="Nenhum profile com dados suficientes. Execute uma análise primeiro." onRun={() => setShowRunModal(true)} />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="border-b border-[var(--border-subtle)]">
                    {[
                      ["profile_name", "Profile"],
                      ["closed_trades", "Fechados"],
                      ["open_trades", "Abertos"],
                      ["wins", "Wins"],
                      ["losses", "Losses"],
                      ["win_rate", "Win Rate"],
                      ["avg_pnl_pct", "Avg P&L"],
                      ["tp_30m_rate", "TP ≤30m"],
                      ["avg_mae_pct", "Avg MAE"],
                      ["confidence_level", "Confidence"],
                      ["", "Ações"],
                    ].map(([col, label]) => (
                      <th
                        key={col}
                        className={`px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap ${col ? "cursor-pointer hover:text-[var(--text-primary)]" : ""}`}
                        onClick={() => col && toggleProfileSort(col)}
                      >
                        {label}
                        {profileSort.col === col && (
                          <span className="ml-1">{profileSort.asc ? "↑" : "↓"}</span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)]">
                  {profileGroups.map(group => {
                    const isDuplicate = group.length > 1;
                    const agg = isDuplicate ? aggregateGroup(group) : { ...group[0], _count: 1 };
                    return (
                      <tr
                        key={agg.profile_id}
                        className={`hover:bg-[var(--bg-elevated)] transition-colors ${isDuplicate ? "bg-yellow-500/5" : ""}`}
                      >
                        <td className="px-4 py-2.5 font-medium text-[var(--text-primary)] whitespace-nowrap">
                          <div className="flex items-center gap-2">
                            <span>{agg.profile_name}</span>
                            {isDuplicate && (
                              <span
                                className="badge text-[9px] px-1.5 py-0.5 flex items-center gap-1 cursor-pointer hover:opacity-80"
                                style={{ background: "rgba(234,179,8,0.15)", color: "#eab308", border: "1px solid rgba(234,179,8,0.3)" }}
                                onClick={() => setSelectedDuplicateGroup(group)}
                                title="Profiles com nome duplicado"
                              >
                                <Users className="w-2.5 h-2.5" />
                                {group.length} IDs
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{agg.closed_trades ?? "—"}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{agg.open_trades ?? "—"}</td>
                        <td className="px-4 py-2.5 text-green-400">{agg.wins ?? "—"}</td>
                        <td className="px-4 py-2.5 text-red-400">{agg.losses ?? "—"}</td>
                        <td className={`px-4 py-2.5 font-semibold ${winRateColor(agg.win_rate)}`}>{fmtPct(agg.win_rate)}</td>
                        <td className={`px-4 py-2.5 font-medium ${pnlColor(agg.avg_pnl_pct)}`}>{fmtPctRaw(agg.avg_pnl_pct, 2)}</td>
                        <td className="px-4 py-2.5 text-[var(--text-primary)]">{fmtPct(agg.tp_30m_rate)}</td>
                        <td className={`px-4 py-2.5 ${pnlColor(agg.avg_mae_pct)}`}>{fmtPctRaw(agg.avg_mae_pct, 2)}</td>
                        <td className="px-4 py-2.5">{confidenceBadge(agg.confidence_level)}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1.5">
                            {isDuplicate ? (
                              <button
                                className="btn btn-secondary text-[10px] px-2 py-1 flex items-center gap-1 whitespace-nowrap"
                                onClick={() => setSelectedDuplicateGroup(group)}
                              >
                                <Users className="w-3 h-3" />
                                Ver {group.length} IDs
                              </button>
                            ) : (
                              <a href="/profiles" className="btn btn-secondary text-[10px] px-2 py-1 flex items-center gap-1 whitespace-nowrap w-fit">
                                <ExternalLink className="w-3 h-3" />
                                Profiles
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── TAB: Indicators ────────────────────────────────────────────────────── */}
      {activeTab === "Indicators" && (
        <div className="space-y-4">
          <div className="flex gap-2">
            {(["winners", "losers"] as const).map(sub => (
              <button
                key={sub}
                onClick={() => setIndicatorSubTab(sub)}
                className={`px-4 py-2 rounded-lg text-[12px] font-medium border transition-colors ${
                  indicatorSubTab === sub
                    ? "bg-blue-600 border-blue-500 text-white"
                    : "bg-[var(--bg-elevated)] border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]"
                }`}
              >
                {sub === "winners" ? <span className="flex items-center gap-1.5"><TrendingUp className="w-3.5 h-3.5" /> Top Winners</span> : <span className="flex items-center gap-1.5"><TrendingDown className="w-3.5 h-3.5" /> Top Losers</span>}
              </button>
            ))}
          </div>

          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">
                {indicatorSubTab === "winners" ? "Indicadores com Melhor Performance" : "Indicadores com Pior Performance"}
              </h2>
              <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
                {indicatorSubTab === "winners" ? "Candidatos a Signal ou Scoring Rule" : "Candidatos a Score Penalty (−10 pts) — não viram Block Rule diretamente"}
              </p>
            </div>
            {loadingTab ? <TableSkeleton /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-subtle)]">
                      {indicatorSubTab === "winners"
                        ? ["Indicador / Bucket", "Cases", "W/L", "Win Rate", "Lift vs Base", "Avg P&L", "TP ≤30m", "Avg MAE", "Confidence", "Role", ""].map(h => (
                          <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                        ))
                        : ["Indicador / Bucket", "Cases", "W/L", "Loss Rate", "Lift", "Avg P&L", "Confidence", "Ação Sugerida", ""].map(h => (
                          <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                        ))
                      }
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-subtle)]">
                    {(indicatorSubTab === "winners" ? winners : losers).map(stat => (
                      <>
                        <tr
                          key={stat.id}
                          className="hover:bg-[var(--bg-elevated)] transition-colors cursor-pointer"
                          onClick={() => setExpandedIndicator(expandedIndicator === stat.id ? null : stat.id)}
                        >
                          <td className="px-4 py-2.5">
                            <div className="font-medium text-[var(--text-primary)] font-mono">{stat.indicator}</div>
                            <div className="text-[10px] text-[var(--text-tertiary)]">{stat.bucket_label}</div>
                          </td>
                          <td className="px-4 py-2.5 text-[var(--text-secondary)]">{stat.total_cases}</td>
                          <td className="px-4 py-2.5">
                            <span className="text-green-400">{stat.wins ?? "—"}</span>
                            <span className="text-[var(--text-tertiary)]">/</span>
                            <span className="text-red-400">{stat.losses ?? "—"}</span>
                          </td>
                          {indicatorSubTab === "winners" ? (
                            <>
                              <td className={`px-4 py-2.5 font-semibold ${winRateColor(stat.win_rate)}`}>{fmtPct(stat.win_rate)}</td>
                              <td className={`px-4 py-2.5 font-medium ${(stat.lift_vs_base ?? 0) >= 1.2 ? "text-green-400" : "text-[var(--text-secondary)]"}`}>
                                {stat.lift_vs_base != null ? `${stat.lift_vs_base.toFixed(2)}x` : "—"}
                              </td>
                              <td className={`px-4 py-2.5 ${pnlColor(stat.avg_pnl_pct)}`}>{fmtPctRaw(stat.avg_pnl_pct, 2)}</td>
                              <td className="px-4 py-2.5 text-[var(--text-primary)]">{fmtPct(stat.tp_30m_rate)}</td>
                              <td className={`px-4 py-2.5 ${pnlColor(stat.avg_mae_pct)}`}>{fmtPctRaw(stat.avg_mae_pct, 2)}</td>
                              <td className="px-4 py-2.5">{confidenceBadge(stat.confidence_level)}</td>
                              <td className="px-4 py-2.5">
                                {stat.role_detected && (
                                  <span className={`badge text-[10px] ${stat.role_detected === "winning_indicator" ? "bullish" : stat.role_detected === "scoring_candidate" ? "range" : "range"}`}>
                                    {stat.role_detected.replace(/_/g, " ")}
                                  </span>
                                )}
                              </td>
                              <td className="px-4 py-2.5">
                                <button className="text-[10px] text-[var(--accent-primary)] hover:underline flex items-center gap-1">
                                  {expandedIndicator === stat.id ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                                  Evidence
                                </button>
                              </td>
                            </>
                          ) : (
                            <>
                              <td className="px-4 py-2.5 text-red-400 font-semibold">{fmtPct(stat.loss_rate)}</td>
                              <td className={`px-4 py-2.5 font-medium ${(stat.lift_vs_base ?? 1) < 1 ? "text-red-400" : "text-[var(--text-secondary)]"}`}>
                                {stat.lift_vs_base != null ? `${stat.lift_vs_base.toFixed(2)}x` : "—"}
                              </td>
                              <td className={`px-4 py-2.5 ${pnlColor(stat.avg_pnl_pct)}`}>{fmtPctRaw(stat.avg_pnl_pct, 2)}</td>
                              <td className="px-4 py-2.5">{confidenceBadge(stat.confidence_level)}</td>
                              <td className="px-4 py-2.5">
                                {stat.total_cases < 30
                                  ? <span className="badge range text-[10px]">Low Sample</span>
                                  : stat.role_detected === "losing_indicator"
                                  ? <span className="badge bearish text-[10px]">Score Penalty</span>
                                  : <span className="badge range text-[10px]">Monitor</span>}
                              </td>
                              <td className="px-4 py-2.5">
                                <button className="text-[10px] text-[var(--accent-primary)] hover:underline flex items-center gap-1">
                                  {expandedIndicator === stat.id ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                                  Evidence
                                </button>
                              </td>
                            </>
                          )}
                        </tr>
                        {expandedIndicator === stat.id && (
                          <tr key={`${stat.id}-expanded`}>
                            <td colSpan={indicatorSubTab === "winners" ? 10 : 9} className="px-6 pb-4 pt-2 bg-[var(--bg-elevated)]">
                              <div className="text-[11px] text-[var(--text-secondary)] space-y-2">
                                <div className="font-semibold text-[var(--text-primary)] mb-1">Evidence — {stat.bucket_label}</div>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                                  {[
                                    ["Cases", stat.total_cases],
                                    ["Win Rate", fmtPct(stat.win_rate)],
                                    ["Lift vs Base", stat.lift_vs_base != null ? `${stat.lift_vs_base.toFixed(2)}x` : "—"],
                                    ["Avg P&L", fmtPctRaw(stat.avg_pnl_pct, 3)],
                                    ["TP ≤30m", fmtPct(stat.tp_30m_rate)],
                                    ["Avg MAE", fmtPctRaw(stat.avg_mae_pct, 3)],
                                    ["Avg MFE", fmtPctRaw(stat.avg_mfe_pct, 3)],
                                    ["Avg Holding", fmtSeconds(stat.avg_holding_seconds)],
                                  ].map(([k, v]) => (
                                    <div key={String(k)} className="bg-[var(--bg-surface)] rounded p-2">
                                      <div className="text-[10px] text-[var(--text-tertiary)]">{k}</div>
                                      <div className="text-[12px] font-medium text-[var(--text-primary)]">{String(v)}</div>
                                    </div>
                                  ))}
                                </div>
                                {stat.source_profiles && (
                                  <div className="text-[10px] text-[var(--text-tertiary)]">
                                    Source profiles: {Array.isArray(stat.source_profiles) ? stat.source_profiles.join(", ") : JSON.stringify(stat.source_profiles)}
                                  </div>
                                )}
                                <div className="text-[10px] text-[var(--text-tertiary)]">
                                  Validation: {stat.validation_status || "exploratory_only"} · Actionability: {stat.actionability_status || "exploratory_only"} · Target: {stat.target_section || "—"}
                                </div>
                                {stat.total_cases < 30 && (
                                  <div className="text-[10px] text-yellow-400">⚠️ Evidência insuficiente — não usar como base de decisão operacional.</div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </>
                    ))}
                  </tbody>
                </table>
                {(indicatorSubTab === "winners" ? winners : losers).length === 0 && !loadingTab && (
                  <EmptyState message="Nenhum dado. Execute uma análise para popular os indicadores." onRun={() => setShowRunModal(true)} />
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: Combinations ──────────────────────────────────────────────────── */}
      {activeTab === "Combinations" && (
        <div className="space-y-4">
          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <div className="flex items-center justify-between gap-4 flex-wrap">
                <div>
                  <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Combinações Descobertas</h2>
                  <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
                    Candidatos — evidência parcial, requerem validação em shadow antes de qualquer uso operacional.
                  </p>
                </div>
                <label className="flex items-center gap-2 text-[12px] cursor-pointer select-none shrink-0">
                  <div
                    className={`relative w-8 h-4 rounded-full transition-colors ${hideComboDuplicates ? "bg-blue-600" : "bg-[var(--bg-hover)]"}`}
                    onClick={() => setHideComboDuplicates(v => !v)}
                  >
                    <div className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${hideComboDuplicates ? "translate-x-4" : "translate-x-0.5"}`} />
                  </div>
                  <span className="text-[var(--text-secondary)]">
                    Ocultar duplicatas
                    {hideComboDuplicates && hiddenComboCount > 0 && (
                      <span className="ml-1 text-[10px] text-[var(--text-tertiary)]">({hiddenComboCount} ocultadas)</span>
                    )}
                  </span>
                </label>
              </div>
            </div>
            {loadingTab ? <TableSkeleton /> : combinations.length === 0 ? (
              <EmptyState message="Nenhuma combinação descoberta. Execute uma análise com lookback maior ou reduza min_closed_trades." onRun={() => setShowRunModal(true)} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-subtle)]">
                      {["Nome / Família", "Tipo", "Validation", "Discovery Trades", "Validation Trades", "Validation Lift", "Champion Score", "Confidence", "Overfit", ""].map(h => (
                        <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-subtle)]">
                    {displayedCombinations.map(c => (
                      <tr key={c.id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                        <td className="px-4 py-2.5">
                          <div className="font-medium text-[var(--text-primary)] max-w-[200px] truncate">{c.suggested_name || `combination_${c.id.slice(0, 8)}`}</div>
                          {c.setup_family && <div className="text-[10px] text-[var(--text-tertiary)]">{c.setup_family}</div>}
                        </td>
                        <td className="px-4 py-2.5">{combinationTypeBadge(c.combination_type)}</td>
                        <td className="px-4 py-2.5">{validationBadge(c)}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{c.discovery_metrics_json?.total_cases ?? c.total_cases ?? "—"}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{c.validation_metrics_json?.total_cases ?? "—"}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">
                          {c.validation_metrics_json?.lift != null ? `${Number(c.validation_metrics_json.lift).toFixed(2)}x` : "—"}
                        </td>
                        <td className="px-4 py-2.5 min-w-[120px]"><ChampionScoreBar score={c.champion_score} /></td>
                        <td className="px-4 py-2.5">{confidenceBadge(c.confidence_level)}</td>
                        <td className="px-4 py-2.5">
                          {c.overfit_risk ? <span className="text-yellow-400 text-[11px]">⚠️ Sim</span> : <span className="text-[var(--text-tertiary)] text-[11px]">—</span>}
                        </td>
                        <td className="px-4 py-2.5">
                          <button
                            className="btn btn-secondary text-[10px] px-2 py-1 whitespace-nowrap"
                            onClick={() => setSelectedCombination(c)}
                          >
                            Detalhes
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: Suggestions ───────────────────────────────────────────────────── */}
      {activeTab === "Suggestions" && (
        <div className="space-y-4">
          <div className="card p-3 border-yellow-500/20 bg-yellow-500/5">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-yellow-400 mt-0.5 shrink-0" />
              <p className="text-[11px] text-[var(--text-secondary)]">
                Sugestões são hipóteses analíticas geradas com base em shadow trades. Requerem validação antes de qualquer uso operacional. <strong className="text-yellow-400">Live trading permanece desativado.</strong>
              </p>
            </div>
          </div>

          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Sugestões de Novos Profiles</h2>
              <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">Exploratório não é aplicável. Validado ainda exige revisão humana e cria somente profile SHADOW_ONLY.</p>
            </div>
            {loadingTab ? <TableSkeleton /> : suggestions.length === 0 ? (
              <EmptyState message="Nenhuma sugestão disponível. Execute uma análise ou aguarde mais shadow trades fechados." onRun={() => setShowRunModal(true)} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-subtle)]">
                      {["Nome Sugerido", "Origem", "Profile", "Validation", "Risco", "Status", "Ações"].map(h => (
                        <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-subtle)]">
                    {suggestions.map(s => (
                      <tr key={s.id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                        <td className="px-4 py-2.5 font-medium text-[var(--text-primary)] max-w-[220px] truncate">{s.suggested_profile_name}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)] font-mono text-[10px]">{s.source_type || "—"}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{s.profile_name || "—"}</td>
                        <td className="px-4 py-2.5">{statusBadge(s.validation_status)}</td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{s.risk_level || "—"}</td>
                        <td className="px-4 py-2.5">{statusBadge(s.status)}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <button
                              className="btn btn-secondary text-[10px] px-2 py-1 whitespace-nowrap"
                              onClick={() => setSelectedSuggestion(s)}
                            >
                              <Eye className="w-3 h-3" />
                            </button>
                            <button
                              className="btn btn-secondary text-[10px] px-2 py-1 whitespace-nowrap flex items-center gap-1"
                              onClick={() => handleExplain(s.id)}
                              disabled={explainingId === s.id}
                            >
                              <Zap className={`w-3 h-3 ${explainingId === s.id ? "animate-pulse" : ""}`} />
                              {explainingId === s.id ? "..." : "IA"}
                            </button>
                            <button
                              className={`btn text-[10px] px-2 py-1 whitespace-nowrap flex items-center gap-1 ${
                                ["created", "applied"].includes(s.status)
                                  ? "btn-secondary opacity-50 cursor-not-allowed"
                                  : !suggestionIsActionable(s)
                                  ? "btn-secondary opacity-40 cursor-not-allowed"
                                  : "btn-primary"
                              }`}
                              onClick={() => suggestionIsActionable(s) && handleOpenCreateProfile(s)}
                              disabled={["created", "applied"].includes(s.status) || !suggestionIsActionable(s)}
                              title={!suggestionIsActionable(s) ? blockedReasonLabel(s.blocked_reason) : "Criar profile SHADOW_ONLY"}
                            >
                              <BarChart3 className="w-3 h-3" />
                              {["created", "applied"].includes(s.status) ? "Aplicado" : "Criar"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB: Audit ─────────────────────────────────────────────────────────── */}
      {activeTab === "Audit" && (
        <div className="card">
          <div className="p-4 border-b border-[var(--border-default)]">
            <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Audit Trail</h2>
            <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">Log imutável de eventos do Profile Intelligence Engine.</p>
          </div>
          {loadingTab ? <TableSkeleton /> : audit.length === 0 ? (
            <div className="p-8 text-center text-[12px] text-[var(--text-tertiary)]">Nenhum evento registrado ainda.</div>
          ) : (
            <div className="divide-y divide-[var(--border-subtle)]">
              {audit.map(entry => (
                <div key={entry.id}>
                  <button
                    className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-elevated)] transition-colors"
                    onClick={() => setExpandedAudit(expandedAudit === entry.id ? null : entry.id)}
                  >
                    <span className="shrink-0 text-[var(--text-tertiary)]">
                      {expandedAudit === entry.id ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    </span>
                    <span className="flex-1 flex items-center gap-2 min-w-0 flex-wrap">
                      <span className={`badge text-[10px] ${entry.event_type.includes("error") || entry.event_type.includes("failed") ? "bearish" : entry.event_type.includes("completed") || entry.event_type.includes("finished") ? "bullish" : entry.event_type.includes("anthropic") || entry.event_type.includes("ai_") ? "range" : "range"}`}>
                        {entry.event_type}
                      </span>
                      {entry.event_description && (
                        <span className="text-[11px] text-[var(--text-secondary)] truncate">{entry.event_description}</span>
                      )}
                      {entry.model_provider && (
                        <span className="text-[10px] text-purple-400">{entry.model_provider}/{entry.model_name}</span>
                      )}
                    </span>
                    <span className="text-[10px] text-[var(--text-tertiary)] shrink-0 font-mono">{fmtDate(entry.created_at)}</span>
                  </button>
                  {expandedAudit === entry.id && (
                    <div className="px-10 pb-4 pt-2 space-y-2">
                      {entry.run_id && <div className="text-[10px] text-[var(--text-tertiary)]">Run: <span className="font-mono">{entry.run_id}</span></div>}
                      {entry.suggestion_id && <div className="text-[10px] text-[var(--text-tertiary)]">Suggestion: <span className="font-mono">{entry.suggestion_id}</span></div>}
                      {entry.result_json && (
                        <div>
                          <div className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">Result</div>
                          <pre className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-input)] rounded p-2 overflow-x-auto max-h-40">
                            {JSON.stringify(entry.result_json, null, 2)}
                          </pre>
                        </div>
                      )}
                      {entry.payload_json && (
                        <div>
                          <div className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">Payload</div>
                          <pre className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-input)] rounded p-2 overflow-x-auto max-h-40">
                            {JSON.stringify(entry.payload_json, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── TAB: Settings ──────────────────────────────────────────────────────── */}
      {activeTab === "Settings" && (
        <div className="space-y-4 max-w-2xl">
          <div className="card p-4 border-blue-500/20 bg-blue-500/5">
            <div className="flex items-start gap-2">
              <CheckCircle className="w-4 h-4 text-blue-400 mt-0.5 shrink-0" />
              <p className="text-[11px] text-[var(--text-secondary)]">
                Configurações afetam os próximos runs. Sugestões geradas são sempre analíticas e não ativam live trading.
              </p>
            </div>
          </div>

          {loadingTab ? (
            <div className="skeleton h-64 rounded-[var(--radius-lg)]" />
          ) : (
            <div className="card p-5 space-y-4">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Parâmetros do Engine</h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[
                  { key: "min_closed_trades", label: "Min Closed Trades", step: 1 },
                  { key: "min_lift", label: "Min Lift", step: 0.1 },
                  { key: "min_win_rate", label: "Min Win Rate", step: 0.01 },
                  { key: "max_avg_mae", label: "Max Avg MAE", step: 0.1 },
                  { key: "max_avg_holding_seconds", label: "Max Avg Holding (s)", step: 60 },
                  { key: "required_tp_30m_rate", label: "Required TP ≤30m Rate", step: 0.01 },
                  { key: "max_combinations_per_run", label: "Max Combinations per Run", step: 50 },
                ].map(({ key, label, step }) => (
                  <div key={key}>
                    <label className="block text-[11px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider mb-1">{label}</label>
                    <input
                      type="number"
                      step={step}
                      value={(settings as any)[key] ?? ""}
                      onChange={e => setSettings(s => ({ ...s, [key]: parseFloat(e.target.value) || 0 }))}
                      className="w-full bg-[var(--bg-input)] border border-[var(--border-default)] rounded-lg px-3 py-2 text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                    />
                  </div>
                ))}
              </div>

              <div className="space-y-3 pt-2 border-t border-[var(--border-subtle)]">
                <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">Features Opcionais</h3>
                {[
                  { key: "enable_dynamic_combinations", label: "Dynamic Combinations", hint: "Gera combinações dinâmicas de buckets top-winners" },
                  { key: "enable_association_rules", label: "Association Rules", hint: "mlxtend apriori para encontrar co-ocorrências" },
                  { key: "enable_anthropic_explanations", label: "Anthropic AI Explanations", hint: "⚠️ Consome tokens da cota Anthropic" },
                  { key: "enable_optuna", label: "Optuna Search", hint: "⚠️ Pesado — aumenta tempo de análise significativamente" },
                ].map(({ key, label, hint }) => (
                  <div key={key} className="flex items-center justify-between">
                    <div>
                      <div className="text-[13px] text-[var(--text-primary)]">{label}</div>
                      <div className="text-[11px] text-[var(--text-tertiary)]">{hint}</div>
                    </div>
                    <button
                      onClick={() => setSettings(s => ({ ...s, [key]: !(s as any)[key] }))}
                      className={`relative w-11 h-6 rounded-full transition-colors ${(settings as any)[key] ? "bg-[var(--accent-primary)]" : "bg-[var(--bg-hover)]"} border border-[var(--border-strong)]`}
                    >
                      <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${(settings as any)[key] ? "translate-x-5" : "translate-x-0"}`} />
                    </button>
                  </div>
                ))}

                <div className="grid grid-cols-1 gap-3 pt-2">
                  {["LightGBM", "CatBoost"].map(model => (
                    <div
                      key={model}
                      className="rounded-lg border border-yellow-500/25 bg-yellow-500/5 p-3"
                      title="Este recurso ainda não possui implementação backend. Não treina, não infere, não gera sugestões e não influencia o Auto-Pilot."
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-[13px] font-medium text-[var(--text-primary)]">{model}</div>
                          <div className="text-[11px] text-yellow-400">Status: Não implementado</div>
                        </div>
                        <button
                          type="button"
                          disabled
                          aria-label={`${model} não implementado`}
                          className="relative w-11 h-6 rounded-full bg-[var(--bg-hover)] border border-[var(--border-strong)] opacity-50 cursor-not-allowed"
                        >
                          <span className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white/70 shadow" />
                        </button>
                      </div>
                      <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">
                        Contribuição atual: zero. Não treina, não executa inferência, não gera sugestões e não influencia o Auto-Pilot.
                      </div>
                      <div className="mt-1 text-[10px] text-[var(--text-tertiary)]">
                        Desabilitado até backend, trainer, predictor e model registry serem implementados.
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <button
                className="btn btn-primary text-[13px] w-full"
                onClick={handleSaveSettings}
                disabled={savingSettings}
              >
                {savingSettings ? "Salvando..." : "Salvar Configurações"}
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Combination Detail Drawer ─────────────────────────────────────────── */}
      {selectedCombination && (
        <Drawer title={selectedCombination.suggested_name || "Detalhe da Combinação"} onClose={() => { setSelectedCombination(null); setLastGeneratedSuggestionId(null); }}>
          <div className="space-y-4">
            {/* Alerts */}
            {selectedCombination.overfit_risk && (
              <div className="card p-3 border-yellow-500/30 bg-yellow-500/5 text-[11px] text-yellow-400 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" /> <span>⚠️ Risco de overfitting detectado. Resultados de discovery podem não generalizar.</span>
              </div>
            )}
            {selectedCombination.confidence_level === "LOW" && (
              <div className="card p-3 border-red-500/30 bg-red-500/5 text-[11px] text-red-400 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" /> <span>Evidência insuficiente — não usar como base de decisão operacional (LOW confidence).</span>
              </div>
            )}
            {!combinationIsActionable(selectedCombination) && (
              <div className="card p-3 border-red-500/30 bg-red-500/5 text-[11px] text-red-400 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>
                  {blockedReasonLabel(
                    selectedCombination.validation_metrics_json?.blocked_reason
                    || selectedCombination.validation_metrics_json?.actionability_status
                  )}
                </span>
              </div>
            )}

            {selectedCombination.source_profiles?.length ? (
              <div className="text-[11px] text-[var(--text-secondary)]">
                <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Source profiles</div>
                {selectedCombination.source_profiles.join(", ")}
              </div>
            ) : null}

            {/* Discovery vs Validation */}
            <div className="grid grid-cols-2 gap-3">
              {[
                ["Discovery", selectedCombination.discovery_metrics_json],
                ["Validation", selectedCombination.validation_metrics_json],
              ].map(([label, metrics]) => (
                <div key={String(label)} className="card p-3">
                  <div className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase mb-2">{String(label)}</div>
                  {metrics ? (
                    <div className="space-y-1">
                      {Object.entries(metrics as Record<string, unknown>).map(([k, v]) => (
                        <div key={k} className="flex justify-between text-[11px]">
                          <span className="text-[var(--text-tertiary)]">{k}</span>
                          <span className="text-[var(--text-primary)] font-medium">
                            {typeof v === "number"
                              ? (k.includes("rate") ? fmtPct(v, 2) : k.includes("pnl") || k.includes("mae") || k.includes("mfe") ? fmtPctRaw(v, 2) : v.toFixed(3))
                              : safeVal(v)}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : <div className="text-[11px] text-[var(--text-tertiary)]">—</div>}
                </div>
              ))}
            </div>

            {/* Degradation */}
            {selectedCombination.degradation_pct != null && (
              <div className="flex justify-between text-[12px]">
                <span className="text-[var(--text-secondary)]">Degradação discovery → validation</span>
                <span className={`font-semibold ${Math.abs(selectedCombination.degradation_pct) > 20 ? "text-red-400" : "text-green-400"}`}>
                  {selectedCombination.degradation_pct.toFixed(1)}%
                </span>
              </div>
            )}

            {/* Rules */}
            {selectedCombination.rules_json && selectedCombination.rules_json.length > 0 && (
              <div>
                <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-2">Regras ({selectedCombination.rules_json.length})</div>
                <div className="space-y-1">
                  {selectedCombination.rules_json.map((r: any, i: number) => (
                    <div key={i} className="font-mono text-[11px] bg-[var(--bg-input)] rounded px-3 py-1.5 text-[var(--text-primary)]">
                      {r.indicator || r.field} {r.operator} {r.value ?? `[${r.range_min}, ${r.range_max}]`}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Generate Suggestion */}
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
              <p className="text-[11px] text-[var(--text-tertiary)]">
                Esta é uma hipótese analítica. Gere uma Suggestion para validar em shadow antes de ativar.
              </p>
              <button
                onClick={() => handleGenerateSuggestion(selectedCombination.id)}
                disabled={generatingSuggestion || !combinationIsActionable(selectedCombination)}
                className="btn btn-secondary text-[12px] w-full"
              >
                {generatingSuggestion
                  ? "Gerando..."
                  : combinationIsActionable(selectedCombination)
                    ? "Generate Suggestion"
                    : "Sugestão bloqueada por validation"}
              </button>
              {lastGeneratedSuggestionId && (
                <button
                  onClick={() => { setActiveTab("Suggestions"); setSelectedCombination(null); setLastGeneratedSuggestionId(null); }}
                  className="btn btn-primary text-[12px] w-full"
                >
                  Open Suggestion →
                </button>
              )}
            </div>
          </div>
        </Drawer>
      )}

      {/* ── Suggestion Detail Drawer ──────────────────────────────────────────── */}
      {selectedSuggestion && (
        <Drawer title={selectedSuggestion.suggested_profile_name} onClose={() => setSelectedSuggestion(null)}>
          <div className="space-y-4">
            {/* Safety */}
            <div className="card p-3 border-yellow-500/20 bg-yellow-500/5 text-[11px] text-yellow-400 flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>Perfis gerados por inteligência devem nascer como SHADOW_ONLY. Live trading permanece desativado.</span>
            </div>

            {/* Meta */}
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              {[
                ["Família", selectedSuggestion.suggested_profile_family || "—"],
                ["Origem", selectedSuggestion.source_type || "—"],
                ["Run", selectedSuggestion.source_run_id || "—"],
                ["Profile", selectedSuggestion.profile_name || "—"],
                ["Validation", selectedSuggestion.validation_status || "—"],
                ["Actionability", selectedSuggestion.actionability_status || "—"],
                ["Risco", selectedSuggestion.risk_level || "—"],
                ["Status", selectedSuggestion.status],
                ["Rollback", selectedSuggestion.rollback_available ? "Disponível" : "Ausente"],
              ].map(([k, v]) => (
                <div key={k} className="bg-[var(--bg-elevated)] rounded p-2">
                  <div className="text-[10px] text-[var(--text-tertiary)]">{k}</div>
                  <div className="text-[var(--text-primary)] font-medium">{v}</div>
                </div>
              ))}
            </div>

            {selectedSuggestion.blocked_reason && (
              <div className="card p-3 border-red-500/30 bg-red-500/5 text-[11px] text-red-400">
                Bloqueado: {selectedSuggestion.blocked_reason}
              </div>
            )}

            {selectedSuggestion.source_profiles?.length ? (
              <div className="text-[11px] text-[var(--text-secondary)]">
                <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Source profiles</div>
                {selectedSuggestion.source_profiles.join(", ")}
              </div>
            ) : null}

            {/* Evidence */}
            {selectedSuggestion.evidence_summary_json && (
              <div>
                <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-2">Evidence Summary</div>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(selectedSuggestion.evidence_summary_json as Record<string, unknown>).map(([k, v]) => (
                    <div key={k} className="bg-[var(--bg-elevated)] rounded p-2 text-[11px]">
                      <div className="text-[10px] text-[var(--text-tertiary)]">{k}</div>
                      <div className="text-[var(--text-primary)] font-medium">
                        {typeof v === "number"
                          ? (k.includes("rate") || k.includes("win") ? fmtPct(v, 3) : k.includes("pnl") || k.includes("mae") || k.includes("mfe") ? fmtPctRaw(v, 3) : v.toFixed(3))
                          : safeVal(v)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Quantitative explanation */}
            {selectedSuggestion.quantitative_explanation && (
              <div>
                <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">Explicação Quantitativa</div>
                <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed">{selectedSuggestion.quantitative_explanation}</p>
              </div>
            )}

            {/* AI explanation */}
            {selectedSuggestion.ai_explanation ? (
              <div>
                <div className="text-[11px] font-semibold text-blue-400 uppercase mb-1 flex items-center gap-1"><Zap className="w-3 h-3" /> Explicação IA</div>
                <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 text-[12px] text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
                  {selectedSuggestion.ai_explanation}
                </div>
              </div>
            ) : (
              <div>
                <button
                  className="btn btn-secondary text-[12px] flex items-center gap-1.5 w-full"
                  onClick={() => handleExplain(selectedSuggestion.id)}
                  disabled={explainingId === selectedSuggestion.id}
                >
                  <Zap className={`w-3.5 h-3.5 ${explainingId === selectedSuggestion.id ? "animate-pulse" : ""}`} />
                  {explainingId === selectedSuggestion.id ? "Gerando explicação..." : "Explicar com IA"}
                </button>
                <p className="text-[10px] text-[var(--text-tertiary)] mt-1">Requer chave Anthropic configurada. Usa apenas dados calculados — não inventa métricas.</p>
              </div>
            )}

            {/* Risk notes */}
            {selectedSuggestion.risk_notes && (
              <div className="card p-3 border-yellow-500/20 bg-yellow-500/5">
                <div className="text-[10px] font-semibold text-yellow-400 uppercase mb-1">Notas de Risco</div>
                <p className="text-[11px] text-[var(--text-secondary)]">{selectedSuggestion.risk_notes}</p>
              </div>
            )}

            {/* Config JSON */}
            {selectedSuggestion.suggested_config_json && (
              <div>
                <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">Config Sugerida</div>
                <pre className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-input)] rounded p-3 overflow-x-auto max-h-48">
                  {JSON.stringify(selectedSuggestion.suggested_config_json, null, 2)}
                </pre>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2 pt-2 border-t border-[var(--border-subtle)]">
              <button
                className="btn btn-secondary text-[12px] flex items-center gap-1.5 flex-1"
                onClick={() => { navigator.clipboard.writeText(JSON.stringify(selectedSuggestion.suggested_config_json, null, 2)); showToast("JSON copiado!"); }}
              >
                <Copy className="w-3.5 h-3.5" /> Copy JSON
              </button>
              <button
                className={`btn text-[12px] flex items-center gap-1.5 flex-1 ${
                  ["created", "applied"].includes(selectedSuggestion.status) || !suggestionIsActionable(selectedSuggestion)
                    ? "btn-secondary opacity-40 cursor-not-allowed"
                    : "btn-primary"
                }`}
                disabled={["created", "applied"].includes(selectedSuggestion.status) || !suggestionIsActionable(selectedSuggestion)}
                onClick={() => { setSelectedSuggestion(null); handleOpenCreateProfile(selectedSuggestion); }}
              >
                <BarChart3 className="w-3.5 h-3.5" />
                {["created", "applied"].includes(selectedSuggestion.status) ? "Aplicado" : "Criar Profile"}
              </button>
            </div>
          </div>
        </Drawer>
      )}

      {/* ── Create Profile Confirmation Modal ────────────────────────────────── */}
      {createProfileSuggestion && (
        <Modal
          title={createProfileResult ? "Profile Criado" : "Confirmar Criação de Profile"}
          onClose={() => { setCreateProfileSuggestion(null); setCreateDryRunResult(null); setCreateProfileResult(null); }}
        >
          <div className="space-y-4">

            {/* Safety notice — always shown */}
            <div className="card p-3 border-yellow-500/20 bg-yellow-500/5">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-yellow-400 shrink-0 mt-0.5" />
                <div className="text-[11px] text-yellow-400 space-y-0.5">
                  <p><strong>Perfis gerados por inteligência nascem como SHADOW_ONLY.</strong></p>
                  <p>Live trading permanece desativado. Este perfil é uma hipótese analítica.</p>
                </div>
              </div>
            </div>

            {/* Success state */}
            {createProfileResult && (
              <div className="space-y-4">
                <div className="card p-4 border-green-500/30 bg-green-500/5">
                  <div className="flex items-center gap-2 text-green-400 mb-2">
                    <CheckCircle className="w-5 h-5" />
                    <span className="font-semibold text-[13px]">Profile criado com sucesso!</span>
                  </div>
                  <div className="text-[11px] text-[var(--text-secondary)] space-y-1">
                    <div>Nome: <strong className="text-[var(--text-primary)]">{createProfileResult.profile_name}</strong></div>
                    <div>ID: <span className="font-mono text-[10px]">{createProfileResult.profile_id}</span></div>
                    {createProfileResult.audit_id && <div>Audit: <span className="font-mono text-[10px]">{createProfileResult.audit_id}</span></div>}
                    <div className="text-green-400">is_shadow_only: true | live_trading_enabled: false</div>
                  </div>
                </div>
                {createProfileResult.warnings?.length > 0 && (
                  <div className="text-[11px] text-yellow-400 space-y-1">
                    {createProfileResult.warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
                  </div>
                )}
                <div className="flex gap-2">
                  <a href="/profiles" className="btn btn-primary text-[12px] flex items-center gap-1.5 flex-1 justify-center">
                    <ExternalLink className="w-3.5 h-3.5" /> Abrir em Profiles
                  </a>
                  <button
                    className="btn btn-secondary text-[12px] flex-1"
                    onClick={() => { setCreateProfileSuggestion(null); setCreateDryRunResult(null); setCreateProfileResult(null); }}
                  >
                    Fechar
                  </button>
                </div>
              </div>
            )}

            {/* Loading dry-run */}
            {!createProfileResult && createDryRunLoading && (
              <div className="text-center py-6 text-[12px] text-[var(--text-secondary)]">
                <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-[var(--accent-primary)]" />
                Verificando viabilidade da criação...
              </div>
            )}

            {/* Dry-run preview */}
            {!createProfileResult && !createDryRunLoading && createDryRunResult && (
              <div className="space-y-4">
                {/* Profile preview */}
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  {[
                    ["Nome", createDryRunResult.profile_payload?.name || "—"],
                    ["Tipo", createDryRunResult.profile_payload?.profile_type || "GENERATED"],
                    ["Shadow Only", "true"],
                    ["Live Trading", "DESATIVADO"],
                    ["Confidence", `${createDryRunResult.confidence_level} (${createDryRunResult.confidence_score?.toFixed(1)})`],
                    ["Overfit Risk", createDryRunResult.overfit_risk ? "⚠️ Sim" : "Não"],
                  ].map(([k, v]) => (
                    <div key={k} className="bg-[var(--bg-elevated)] rounded p-2">
                      <div className="text-[10px] text-[var(--text-tertiary)]">{k}</div>
                      <div className={`font-medium ${k === "Live Trading" ? "text-red-400" : "text-[var(--text-primary)]"}`}>{v}</div>
                    </div>
                  ))}
                </div>

                {/* Signals */}
                {(createDryRunResult.profile_payload?.config?.signals?.conditions?.length ?? 0) > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">
                      Signals ({createDryRunResult.profile_payload.config.signals?.conditions?.length ?? 0})
                    </div>
                    <div className="space-y-1">
                      {createDryRunResult.profile_payload.config.signals?.conditions?.map((c: any, i: number) => (
                        <div key={i} className="font-mono text-[10px] bg-[var(--bg-input)] rounded px-2 py-1.5 text-[var(--text-primary)]">
                          {c.indicator || c.field} {c.operator} {c.value}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Block rules */}
                {(createDryRunResult.profile_payload?.config?.block_rules?.blocks?.length ?? 0) > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">
                      Block Rules ({createDryRunResult.profile_payload.config.block_rules?.blocks?.length ?? 0})
                    </div>
                    <div className="space-y-1">
                      {createDryRunResult.profile_payload.config.block_rules?.blocks?.map((b: any, i: number) => (
                        <div key={i} className="font-mono text-[10px] bg-[var(--bg-input)] rounded px-2 py-1.5 text-red-400">
                          BLOCK: {b.indicator || b.field} {b.operator} {b.value}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Master rules */}
                {(createDryRunResult.master_rules_to_create?.length > 0 || createDryRunResult.master_rules_to_reuse?.length > 0) && (
                  <div>
                    <div className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase mb-1">Scoring Rules Master</div>
                    <div className="space-y-1">
                      {createDryRunResult.master_rules_to_create?.map((r: any) => (
                        <div key={r.id} className="text-[10px] bg-green-500/10 border border-green-500/20 rounded px-2 py-1 text-green-400">
                          + NOVA: {r.name || r.indicator} ({r.points}pts)
                        </div>
                      ))}
                      {createDryRunResult.master_rules_to_reuse?.map((r: any) => (
                        <div key={r.id} className="text-[10px] bg-[var(--bg-elevated)] rounded px-2 py-1 text-[var(--text-secondary)]">
                          ↩ Reutilizar: {r.name || r.indicator}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Warnings */}
                {createDryRunResult.warnings?.length > 0 && (
                  <div className="space-y-1">
                    {createDryRunResult.warnings.map((w, i) => (
                      <div key={i} className="text-[10px] text-yellow-400 flex items-start gap-1">
                        <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />{w}
                      </div>
                    ))}
                  </div>
                )}

                {/* Confirmation toggles for gated cases */}
                {createDryRunResult.confidence_level === "LOW" && (
                  <label className="flex items-center gap-2 text-[12px] cursor-pointer">
                    <input type="checkbox" checked={confirmLowConfidence} onChange={e => setConfirmLowConfidence(e.target.checked)} className="w-4 h-4" />
                    <span className="text-yellow-400">Confirmo: estou ciente de que esta sugestão tem LOW confidence (menos de 30 trades).</span>
                  </label>
                )}
                {createDryRunResult.overfit_risk && (
                  <label className="flex items-center gap-2 text-[12px] cursor-pointer">
                    <input type="checkbox" checked={confirmOverfitRisk} onChange={e => setConfirmOverfitRisk(e.target.checked)} className="w-4 h-4" />
                    <span className="text-yellow-400">Confirmo: estou ciente do risco de overfitting nesta combinação.</span>
                  </label>
                )}

                {/* Final actions */}
                <div className="flex gap-2 pt-1 border-t border-[var(--border-subtle)]">
                  <button
                    className="btn btn-secondary text-[12px] flex-1"
                    onClick={() => { setCreateProfileSuggestion(null); setCreateDryRunResult(null); }}
                  >
                    Cancelar
                  </button>
                  <button
                    className={`btn btn-primary text-[12px] flex-1 flex items-center justify-center gap-1.5 ${
                      createProfileLoading ? "opacity-70" : ""
                    } ${
                      (createDryRunResult.confidence_level === "LOW" && !confirmLowConfidence) ||
                      (createDryRunResult.overfit_risk && !confirmOverfitRisk)
                        ? "opacity-40 cursor-not-allowed" : ""
                    }`}
                    disabled={
                      createProfileLoading ||
                      (createDryRunResult.confidence_level === "LOW" && !confirmLowConfidence) ||
                      (createDryRunResult.overfit_risk && !confirmOverfitRisk)
                    }
                    onClick={handleConfirmCreateProfile}
                  >
                    <BarChart3 className={`w-3.5 h-3.5 ${createProfileLoading ? "animate-pulse" : ""}`} />
                    {createProfileLoading ? "Criando..." : "Criar Profile SHADOW_ONLY"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </Modal>
      )}

      {/* ── Duplicate Profile Group Drawer ───────────────────────────────────── */}
      {selectedDuplicateGroup && (
        <Drawer
          title={`${selectedDuplicateGroup[0].profile_name} — ${selectedDuplicateGroup.length} UUIDs`}
          onClose={() => setSelectedDuplicateGroup(null)}
        >
          <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-3 text-[12px] text-yellow-400 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>
              Existem <strong>{selectedDuplicateGroup.length} profiles com o mesmo nome</strong>.
              As métricas abaixo são individuais por UUID. Considere renomear ou consolidar
              em <a href="/profiles" className="underline hover:opacity-80">/profiles</a>.
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="border-b border-[var(--border-subtle)]">
                  {["UUID", "Fechados", "Wins", "Loss", "Win Rate", "Avg P&L", "Avg MAE", "Confidence"].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)]">
                {selectedDuplicateGroup.map(p => (
                  <tr key={p.profile_id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[10px] text-[var(--text-secondary)]">{p.profile_id.slice(0, 8)}…</span>
                        <button
                          className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors"
                          title="Copiar UUID completo"
                          onClick={() => { navigator.clipboard.writeText(p.profile_id); showToast("UUID copiado", true); }}
                        >
                          <Copy className="w-3 h-3" />
                        </button>
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-[var(--text-secondary)]">{p.closed_trades ?? "—"}</td>
                    <td className="px-3 py-2.5 text-green-400">{p.wins ?? "—"}</td>
                    <td className="px-3 py-2.5 text-red-400">{p.losses ?? "—"}</td>
                    <td className={`px-3 py-2.5 font-semibold ${winRateColor(p.win_rate)}`}>{fmtPct(p.win_rate)}</td>
                    <td className={`px-3 py-2.5 ${pnlColor(p.avg_pnl_pct)}`}>{fmtPctRaw(p.avg_pnl_pct, 2)}</td>
                    <td className={`px-3 py-2.5 ${pnlColor(p.avg_mae_pct)}`}>{fmtPctRaw(p.avg_mae_pct, 2)}</td>
                    <td className="px-3 py-2.5">{confidenceBadge(p.confidence_level)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pt-2 border-t border-[var(--border-subtle)]">
            <a
              href="/profiles"
              className="btn btn-secondary text-[12px] flex items-center gap-1.5 w-full justify-center"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Gerenciar em /profiles
            </a>
          </div>
        </Drawer>
      )}

      {/* ── Run Analysis Modal ────────────────────────────────────────────────── */}
      {showRunModal && (
        <Modal title="Run Analysis" onClose={() => { setShowRunModal(false); setRunResult(null); }}>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              {[
                { key: "lookback_days", label: "Lookback (dias)", step: 1 },
                { key: "min_closed_trades", label: "Min Closed Trades", step: 1 },
                { key: "max_combinations", label: "Max Combinations", step: 50 },
              ].map(({ key, label, step }) => (
                <div key={key}>
                  <label className="block text-[11px] font-medium text-[var(--text-tertiary)] uppercase tracking-wider mb-1">{label}</label>
                  <input
                    type="number"
                    step={step}
                    value={(runPayload as any)[key]}
                    onChange={e => setRunPayload(p => ({ ...p, [key]: parseInt(e.target.value) || 0 }))}
                    className="w-full bg-[var(--bg-input)] border border-[var(--border-default)] rounded-lg px-3 py-2 text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                  />
                </div>
              ))}
            </div>

            <div className="space-y-2">
              {[
                { key: "include_counterfactual", label: "Counterfactual Miner" },
                { key: "include_dynamic_combinations", label: "Dynamic Combinations" },
                { key: "include_association_rules", label: "Association Rules" },
                { key: "include_optuna", label: "Optuna Search (pesado)" },
                { key: "include_ai_explanation", label: "AI Explanation (consome tokens)" },
              ].map(({ key, label }) => (
                <label key={key} className="flex items-center justify-between text-[12px] cursor-pointer">
                  <span className="text-[var(--text-secondary)]">{label}</span>
                  <input
                    type="checkbox"
                    checked={(runPayload as any)[key]}
                    onChange={e => setRunPayload(p => ({ ...p, [key]: e.target.checked }))}
                    className="w-4 h-4 rounded"
                  />
                </label>
              ))}
            </div>

            {runResult && (
              <div className={`card p-3 border ${runResult.ok ? "border-green-500/30 bg-green-500/5" : "border-red-500/30 bg-red-500/5"}`}>
                {runResult.ok ? (
                  <div className="flex items-center gap-2 text-[12px] text-green-400">
                    <CheckCircle className="w-4 h-4" />
                    <div>
                      <div>Análise iniciada com sucesso.</div>
                      <div className="text-[10px] font-mono mt-0.5">Run ID: {runResult.run_id}</div>
                      <div className="text-[10px] text-[var(--text-tertiary)] mt-0.5">Resultados disponíveis em alguns minutos. Use Refresh para atualizar.</div>
                    </div>
                  </div>
                ) : (
                  <div className="text-[12px] text-red-400 flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>{runResult.error}</span>
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button
                className="btn btn-secondary text-[12px] flex-1"
                onClick={() => { setShowRunModal(false); setRunResult(null); }}
              >
                Fechar
              </button>
              <button
                className="btn btn-primary text-[12px] flex-1 flex items-center justify-center gap-1.5"
                onClick={handleRun}
                disabled={running}
              >
                <Play className={`w-3.5 h-3.5 ${running ? "animate-pulse" : ""}`} />
                {running ? "Iniciando..." : "Iniciar Análise"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ── Settings Modal (from header button) ──────────────────────────────── */}
      {showSettingsModal && (
        <Modal title="Engine Settings" onClose={() => setShowSettingsModal(false)}>
          <div className="text-[12px] text-[var(--text-secondary)] space-y-2">
            <p>Acesse a aba <strong className="text-[var(--text-primary)]">Settings</strong> para configurar o engine.</p>
            <button
              className="btn btn-primary text-[12px] w-full"
              onClick={() => { setShowSettingsModal(false); setActiveTab("Settings"); }}
            >
              Ir para Settings
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── Reusable sub-components (inline) ──────────────────────────────────────────

function EmptyState({ message, onRun }: { message: string; onRun?: () => void }) {
  return (
    <div className="p-10 text-center">
      <Brain className="w-10 h-10 text-[var(--text-tertiary)] opacity-30 mx-auto mb-3" />
      <p className="text-[13px] text-[var(--text-secondary)] max-w-sm mx-auto">{message}</p>
      {onRun && (
        <button className="btn btn-primary mt-4 text-[12px] flex items-center gap-1.5 mx-auto" onClick={onRun}>
          <Play className="w-3.5 h-3.5" /> Executar análise
        </button>
      )}
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="p-4 space-y-2">
      {[...Array(5)].map((_, i) => <div key={i} className="skeleton h-9 rounded" />)}
    </div>
  );
}

function Drawer({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/50" onClick={onClose} />
      <div className="w-full max-w-xl bg-[var(--bg-surface)] border-l border-[var(--border-default)] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-default)] shrink-0">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] truncate pr-4">{title}</h2>
          <button onClick={onClose} className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4 custom-scrollbar">
          {children}
        </div>
      </div>
    </div>
  );
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-lg)] shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-default)] shrink-0">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">{title}</h2>
          <button onClick={onClose} className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="overflow-y-auto p-5 custom-scrollbar">
          {children}
        </div>
      </div>
    </div>
  );
}
