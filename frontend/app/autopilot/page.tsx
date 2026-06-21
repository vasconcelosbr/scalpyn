"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Bot,
  RefreshCw,
  Play,
  RotateCcw,
  ChevronDown,
  ChevronRight,
  CheckCircle,
  Zap,
  Shield,
} from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────

interface Profile {
  id: string;
  name: string;
  profile_role: string | null;
  autopilot_enabled: boolean;
}

interface AutopilotStatus {
  profile_id: string;
  profile_name: string;
  profile_role: string | null;
  auto_pilot_enabled: boolean;
  dry_run_mode: boolean;
  last_mutation_at: string | null;
  last_regime: string | null;
  last_mutation_reason: string | null;
  last_analysis_summary: string | null;
  macro_risk: string | null;
  consecutive_regressions: number;
  circuit_breaker_active: boolean;
  circuit_breaker_until: string | null;
  ev_before_last_mutation: number | null;
  ev_after_last_mutation: number | null;
  performance: any;
}

interface AuditLog {
  id: string;
  action: string;
  reason: string;
  regime: string | null;
  perf_snapshot: any;
  version_id: string | null;
  trigger_source: string | null;
  celery_task_id: string | null;
  profile_name: string | null;
  created_at: string;
}

interface ProfileVersion {
  id: string;
  version_number: number;
  regime: string | null;
  ev_at_snapshot: number | null;
  win_rate_at_snapshot: number | null;
  fpr_at_snapshot: number | null;
  n_samples: number | null;
  mutation_reason: string | null;
  created_at: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtPct(v: number | null) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(2)}%`;
}

function actionBadge(action: string) {
  if (action === "MUTATED")
    return <span className="badge bullish text-[10px]">MUTATED</span>;
  if (action === "DRY_RUN_MUTATED")
    return <span className="badge bullish text-[10px] opacity-70">[DRY RUN] MUTATED</span>;
  if (action === "DRY_RUN_RULES_ADJUSTED")
    return <span className="badge range text-[10px] opacity-80">[DRY RUN] RULES ADJUSTED</span>;
  if (action === "DRY_RUN_ANALYZED")
    return <span className="badge range text-[10px] opacity-70">[DRY RUN] ANALYZED</span>;
  if (action === "RULES_ADJUSTED")
    return <span className="badge bullish text-[10px]">RULES ADJUSTED</span>;
  if (action === "CIRCUIT_BREAKER")
    return <span className="badge bearish text-[10px]">CIRCUIT BREAKER</span>;
  if (action === "KILLED")
    return <span className="badge bearish text-[10px]">KILLED</span>;
  if (action === "SCOPE_VIOLATION_BLOCKED")
    return <span className="badge bearish text-[10px]">SCOPE BLOCKED</span>;
  return <span className="badge range text-[10px]">{action}</span>;
}

function regimeBadge(regime: string | null) {
  if (!regime) return <span className="text-[var(--text-tertiary)]">—</span>;
  const color =
    regime === "TRENDING" ? "bullish" :
    regime === "VOLATILE" ? "bearish" : "range";
  return <span className={`badge ${color} text-[10px]`}>{regime}</span>;
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function AutopilotPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [status, setStatus] = useState<AutopilotStatus | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [versions, setVersions] = useState<ProfileVersion[]>([]);
  const [loadingProfiles, setLoadingProfiles] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [running, setRunning] = useState(false);
  const [runElapsed, setRunElapsed] = useState(0);
  const [toggling, setToggling] = useState(false);
  const [rollingBack, setRollingBack] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<any>(null);
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const runTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load profile list
  useEffect(() => {
    (async () => {
      try {
        const data = await apiGet("/profiles");
        const all: Profile[] = data.profiles || [];
        setProfiles(all);
        // Default: first autopilot-enabled profile
        const ap = all.find((p) => p.autopilot_enabled);
        if (ap) setSelectedId(ap.id);
        else if (all.length > 0) setSelectedId(all[0].id);
      } catch (e) {
        console.error(e);
      } finally {
        setLoadingProfiles(false);
      }
    })();
  }, []);

  // Load status + history when profile changes
  const loadDetail = useCallback(async (profileId: string) => {
    setLoadingDetail(true);
    setStatus(null);
    setAuditLogs([]);
    setVersions([]);
    setRunResult(null);
    try {
      const [s, h] = await Promise.all([
        apiGet(`/autopilot/${profileId}/status`),
        apiGet(`/autopilot/${profileId}/history`),
      ]);
      setStatus(s);
      setAuditLogs(h.audit_logs || []);
      setVersions(h.versions || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  // Toggle Auto-Pilot
  const handleToggle = async () => {
    if (!selectedId || !status) return;
    setToggling(true);
    try {
      await apiPost(`/profiles/${selectedId}/autopilot/toggle`, {
        enabled: !status.auto_pilot_enabled,
      });
      await loadDetail(selectedId);
    } catch (e: any) {
      alert(`Erro ao alterar Auto-Pilot: ${e.message}`);
    } finally {
      setToggling(false);
    }
  };

  // Manual run
  const handleRun = async () => {
    if (!selectedId) return;
    setRunning(true);
    setRunElapsed(0);
    setRunResult(null);
    runTimerRef.current = setInterval(() => setRunElapsed((s) => s + 1), 1000);
    try {
      const res = await apiPost(`/autopilot/${selectedId}/run`);
      await loadDetail(selectedId);
      setRunResult({ ...res, elapsed_s: runElapsed });
    } catch (e: any) {
      setRunResult({ error: e.message });
    } finally {
      if (runTimerRef.current) {
        clearInterval(runTimerRef.current);
        runTimerRef.current = null;
      }
      setRunning(false);
    }
  };

  // Rollback
  const handleRollback = async (versionId: string) => {
    if (!selectedId) return;
    if (!confirm("Restaurar esta versão do profile? A config atual será substituída.")) return;
    setRollingBack(versionId);
    try {
      await apiPost(`/autopilot/${selectedId}/rollback/${versionId}`);
      await loadDetail(selectedId);
    } catch (e: any) {
      alert(`Rollback falhou: ${e.message}`);
    } finally {
      setRollingBack(null);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)] flex items-center gap-2">
            <Bot className="w-6 h-6 text-blue-400" />
            Auto-Pilot
          </h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">
            Monitoramento e histórico do sistema de evolução autônoma de profiles.
          </p>
        </div>
        {selectedId && (
          <button
            className="btn btn-secondary text-[12px] flex items-center gap-1"
            onClick={() => loadDetail(selectedId)}
            disabled={loadingDetail}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loadingDetail ? "animate-spin" : ""}`} />
            Atualizar
          </button>
        )}
      </div>

      {/* Profile selector */}
      {loadingProfiles ? (
        <div className="skeleton h-10 w-64 rounded" />
      ) : (
        <div className="flex items-center gap-3 flex-wrap">
          {profiles.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedId(p.id)}
              className={`px-3 py-1.5 rounded-lg text-[12px] font-medium border transition-colors ${
                selectedId === p.id
                  ? "bg-blue-600 border-blue-500 text-white"
                  : "bg-[var(--bg-elevated)] border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]"
              }`}
            >
              {p.name}
              {p.autopilot_enabled && (
                <span className="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-green-400 align-middle" />
              )}
            </button>
          ))}
        </div>
      )}

      {loadingDetail && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => <div key={i} className="skeleton h-32 rounded-[var(--radius-lg)]" />)}
        </div>
      )}

      {!loadingDetail && status && (
        <>
          {/* Dry-run warning banner — always visible when guardrails.dry_run_mode=true */}
          {(status.dry_run_mode === true || runResult?.dry_run === true || auditLogs.some(l => l.action.startsWith("DRY_RUN"))) && (
            <div className="card p-3 border border-amber-500/40 bg-amber-500/5 flex items-start gap-3">
              <span className="text-amber-400 text-[18px] leading-none">⚠</span>
              <div>
                <span className="text-amber-400 text-[12px] font-semibold uppercase tracking-wide">Modo Simulação Ativo (dry_run_mode = true)</span>
                <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                  O Auto-Pilot está analisando mas <strong>não persiste nenhuma mudança</strong> nos profiles. Todos os ajustes são apenas simulados.
                  Para ativar escrita real: <code className="text-amber-300 bg-black/30 px-1 rounded text-[10px]">UPDATE config_profiles SET config_json = config_json || '{`{"dry_run_mode": false}`}'::jsonb WHERE config_type = 'autopilot_guardrails'</code>
                </p>
              </div>
            </div>
          )}
          {/* Status cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* State */}
            <div className="card p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Estado</span>
                {status.circuit_breaker_active
                  ? <span className="badge bearish text-[10px] flex items-center gap-1"><Shield className="w-3 h-3" />Circuit Breaker</span>
                  : status.auto_pilot_enabled
                    ? <span className="badge bullish text-[10px]">ATIVO</span>
                    : <span className="badge range text-[10px]">INATIVO</span>}
              </div>
              <div className="space-y-1.5">
                <div className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-tertiary)]">Regime</span>
                  {regimeBadge(status.last_regime)}
                </div>
                <div className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-tertiary)]">Risco macro</span>
                  <span className="text-[var(--text-primary)] font-medium">{status.macro_risk || "—"}</span>
                </div>
                <div className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-tertiary)]">Regressões</span>
                  <span className={`font-medium ${status.consecutive_regressions >= 2 ? "text-red-400" : "text-[var(--text-primary)]"}`}>
                    {status.consecutive_regressions}/3
                  </span>
                </div>
                {status.circuit_breaker_until && (
                  <div className="flex justify-between text-[12px]">
                    <span className="text-[var(--text-tertiary)]">Pausa até</span>
                    <span className="text-red-400 font-medium">{fmtDate(status.circuit_breaker_until)}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Last mutation */}
            <div className="card p-4 space-y-3">
              <span className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Última Mutação</span>
              <div className="space-y-1.5">
                <div className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-tertiary)]">Quando</span>
                  <span className="text-[var(--text-primary)] font-medium">{fmtDate(status.last_mutation_at)}</span>
                </div>
                <div className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-tertiary)]">EV antes</span>
                  <span className={`font-medium ${(status.ev_before_last_mutation ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {fmtPct(status.ev_before_last_mutation)}
                  </span>
                </div>
                <div className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-tertiary)]">EV depois</span>
                  <span className={`font-medium ${(status.ev_after_last_mutation ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {fmtPct(status.ev_after_last_mutation)}
                  </span>
                </div>
                {status.last_mutation_reason && (
                  <div className="text-[11px] text-[var(--text-tertiary)] pt-1 border-t border-[var(--border-subtle)]">
                    {status.last_mutation_reason}
                  </div>
                )}
              </div>
            </div>

            {/* Performance */}
            <div className="card p-4 space-y-3">
              <span className="text-[11px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">Performance 30d</span>
              {status.performance ? (
                <div className="space-y-1.5">
                  <div className="flex justify-between text-[12px]">
                    <span className="text-[var(--text-tertiary)]">EV médio</span>
                    <span className={`font-medium ${(status.performance.avg_ev ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {fmtPct(status.performance.avg_ev)}
                    </span>
                  </div>
                  <div className="flex justify-between text-[12px]">
                    <span className="text-[var(--text-tertiary)]">Win rate</span>
                    <span className="text-[var(--text-primary)] font-medium">
                      {status.performance.win_rate != null ? `${(status.performance.win_rate * 100).toFixed(1)}%` : "—"}
                    </span>
                  </div>
                  <div className="flex justify-between text-[12px]">
                    <span className="text-[var(--text-tertiary)]">FPR</span>
                    <span className={`font-medium ${(status.performance.fpr ?? 0) > 0.65 ? "text-red-400" : "text-[var(--text-primary)]"}`}>
                      {status.performance.fpr != null ? `${(status.performance.fpr * 100).toFixed(1)}%` : "—"}
                    </span>
                  </div>
                  <div className="flex justify-between text-[12px]">
                    <span className="text-[var(--text-tertiary)]">Amostras</span>
                    <span className="text-[var(--text-primary)] font-medium">{status.performance.n_samples ?? "—"}</span>
                  </div>
                </div>
              ) : (
                <p className="text-[12px] text-[var(--text-tertiary)]">Sem dados de performance disponíveis.</p>
              )}
            </div>
          </div>

          {/* Analysis summary */}
          {status.last_analysis_summary && (
            <div className="card p-4">
              <div className="flex items-start gap-2">
                <Zap className="w-3.5 h-3.5 text-blue-400 mt-0.5 shrink-0" />
                <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed">
                  {status.last_analysis_summary}
                </p>
              </div>
            </div>
          )}

          {/* Manual trigger + toggle */}
          <div className="flex items-center gap-3 flex-wrap">
            <button
              className="btn btn-primary text-[12px] flex items-center gap-1.5"
              onClick={handleRun}
              disabled={running || toggling}
            >
              <Play className={`w-3.5 h-3.5 ${running ? "animate-pulse" : ""}`} />
              {running ? `Analisando... ${runElapsed}s` : "Analisar agora"}
            </button>

            <button
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[12px] font-medium transition-colors ${
                status.auto_pilot_enabled
                  ? "bg-green-500/10 border-green-500/30 text-green-400 hover:bg-green-500/20"
                  : "bg-[var(--bg-elevated)] border-[var(--border-default)] text-[var(--text-tertiary)] hover:border-[var(--border-strong)]"
              }`}
              onClick={handleToggle}
              disabled={toggling || running}
            >
              <span className={`w-2 h-2 rounded-full transition-colors ${status.auto_pilot_enabled ? "bg-green-400" : "bg-[var(--text-tertiary)]"}`} />
              {toggling
                ? "Aguarde..."
                : status.auto_pilot_enabled
                ? "Auto-Pilot ativo"
                : "Auto-Pilot inativo"}
            </button>
          </div>

          {/* Run result */}
          {runResult && (
            <div className={`card p-4 border ${runResult.error ? "border-red-500/30" : "border-green-500/30"}`}>
              {runResult.error ? (
                <p className="text-[12px] text-red-400">{runResult.error}</p>
              ) : (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-green-400" />
                    <span className="text-[12px] font-semibold text-[var(--text-primary)]">
                      Ciclo concluído — {runResult.action}
                    </span>
                    {runResult.elapsed_s != null && (
                      <span className="text-[11px] text-[var(--text-tertiary)]">({runResult.elapsed_s}s)</span>
                    )}
                  </div>
                  {runResult.reason && (
                    <p className="text-[11px] text-[var(--text-secondary)] pl-6">{runResult.reason}</p>
                  )}
                  {runResult.analysis_summary && (
                    <p className="text-[11px] text-[var(--text-tertiary)] pl-6">{runResult.analysis_summary}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Audit log */}
          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Log de Decisões</h2>
              <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">Histórico de ciclos do Auto-Pilot</p>
            </div>
            {auditLogs.length === 0 ? (
              <div className="p-6 text-center text-[12px] text-[var(--text-tertiary)]">
                Nenhum ciclo registrado ainda.
              </div>
            ) : (
              <div className="divide-y divide-[var(--border-subtle)]">
                {auditLogs.map((log) => (
                  <div key={log.id}>
                    <button
                      className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-elevated)] transition-colors"
                      onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
                    >
                      <span className="shrink-0">
                        {expandedLog === log.id
                          ? <ChevronDown className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                          : <ChevronRight className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />}
                      </span>
                      <span className="flex-1 flex items-center gap-2 min-w-0">
                        {actionBadge(log.action)}
                        {regimeBadge(log.regime)}
                        <span className="text-[11px] text-[var(--text-secondary)] truncate">{log.reason || "—"}</span>
                      </span>
                      <span className="flex items-center gap-2 shrink-0">
                        {log.trigger_source && (
                          <span className="text-[9px] text-[var(--text-tertiary)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded px-1.5 py-0.5 font-mono">
                            {log.trigger_source === "manual_api" ? "manual" : "agendado"}
                          </span>
                        )}
                        <span className="text-[10px] text-[var(--text-tertiary)] font-mono">
                          {fmtDate(log.created_at)}
                        </span>
                      </span>
                    </button>
                    {expandedLog === log.id && log.perf_snapshot && (
                      <div className="px-10 pb-4 pt-1 space-y-3">
                        {/* Flat perf metrics (excluding rule_changes) */}
                        {Object.keys(log.perf_snapshot).some((k) => k !== "rule_changes") && (
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            {Object.entries(log.perf_snapshot)
                              .filter(([k]) => k !== "rule_changes")
                              .map(([k, v]) => (
                                <div key={k} className="bg-[var(--bg-secondary)] rounded p-2">
                                  <div className="text-[10px] text-[var(--text-tertiary)] mb-0.5">{k}</div>
                                  <div className="text-[11px] font-medium text-[var(--text-primary)]">
                                    {typeof v === "number" ? v.toFixed(4) : String(v)}
                                  </div>
                                </div>
                              ))}
                          </div>
                        )}
                        {/* Rule changes detail table */}
                        {Array.isArray(log.perf_snapshot.rule_changes) && log.perf_snapshot.rule_changes.length > 0 && (
                          <div>
                            <div className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider mb-1.5">
                              Regras ajustadas ({log.perf_snapshot.rule_changes.length})
                            </div>
                            <div className="overflow-x-auto rounded border border-[var(--border-subtle)]">
                              <table className="w-full text-[11px]">
                                <thead>
                                  <tr className="border-b border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
                                    {["Indicador", "Operador", "Range / Valor", "Pontos Antes → Depois", "Edge %", "Win Rate %", "N"].map((h) => (
                                      <th key={h} className="px-3 py-1.5 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider whitespace-nowrap">
                                        {h}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-[var(--border-subtle)]">
                                  {log.perf_snapshot.rule_changes.map((rc: any, i: number) => {
                                    const range =
                                      rc.min != null && rc.max != null
                                        ? `${rc.min} – ${rc.max}`
                                        : rc.value != null
                                        ? String(rc.value)
                                        : "—";
                                    const delta = rc.points_after - rc.points_before;
                                    return (
                                      <tr key={i} className="hover:bg-[var(--bg-elevated)] transition-colors">
                                        <td className="px-3 py-1.5 font-mono text-[var(--text-primary)] whitespace-nowrap">{rc.indicator ?? "—"}</td>
                                        <td className="px-3 py-1.5 text-[var(--text-secondary)] whitespace-nowrap">{rc.operator ?? "—"}</td>
                                        <td className="px-3 py-1.5 text-[var(--text-secondary)] font-mono whitespace-nowrap">{range}</td>
                                        <td className="px-3 py-1.5 whitespace-nowrap">
                                          <span className="text-[var(--text-tertiary)]">{rc.points_before}</span>
                                          <span className="mx-1 text-[var(--text-tertiary)]">→</span>
                                          <span className={`font-semibold ${delta > 0 ? "text-green-400" : "text-red-400"}`}>
                                            {rc.points_after}
                                          </span>
                                          <span className={`ml-1 text-[10px] ${delta > 0 ? "text-green-400" : "text-red-400"}`}>
                                            ({delta > 0 ? "+" : ""}{delta})
                                          </span>
                                        </td>
                                        <td className={`px-3 py-1.5 font-medium whitespace-nowrap ${(rc.edge_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                                          {rc.edge_pct != null ? `${rc.edge_pct > 0 ? "+" : ""}${rc.edge_pct.toFixed(2)}%` : "—"}
                                        </td>
                                        <td className="px-3 py-1.5 text-[var(--text-primary)] whitespace-nowrap">
                                          {rc.win_rate_pct != null ? `${rc.win_rate_pct.toFixed(1)}%` : "—"}
                                        </td>
                                        <td className="px-3 py-1.5 text-[var(--text-secondary)]">{rc.n_samples ?? "—"}</td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Version history */}
          <div className="card">
            <div className="p-4 border-b border-[var(--border-default)]">
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Histórico de Versões</h2>
              <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">Snapshots de config gerados pelo Auto-Pilot</p>
            </div>
            {versions.length === 0 ? (
              <div className="p-6 text-center text-[12px] text-[var(--text-tertiary)]">
                Nenhuma versão salva ainda.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-subtle)]">
                      {["v#", "Data", "Regime", "EV", "Win Rate", "FPR", "Amostras", "Motivo", ""].map((h) => (
                        <th key={h} className="px-4 py-2 text-left text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-wider">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-subtle)]">
                    {versions.map((v, idx) => (
                      <tr key={v.id} className="hover:bg-[var(--bg-elevated)] transition-colors">
                        <td className="px-4 py-2.5 font-mono font-semibold text-[var(--accent-primary)]">
                          v{v.version_number}
                          {idx === 0 && <span className="ml-1 text-[9px] text-green-400">ATUAL</span>}
                        </td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)] font-mono whitespace-nowrap">
                          {fmtDate(v.created_at)}
                        </td>
                        <td className="px-4 py-2.5">{regimeBadge(v.regime)}</td>
                        <td className="px-4 py-2.5">
                          <span className={`font-medium ${(v.ev_at_snapshot ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {fmtPct(v.ev_at_snapshot)}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-[var(--text-primary)]">
                          {v.win_rate_at_snapshot != null ? `${(v.win_rate_at_snapshot * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`font-medium ${(v.fpr_at_snapshot ?? 0) > 0.65 ? "text-red-400" : "text-[var(--text-primary)]"}`}>
                            {v.fpr_at_snapshot != null ? `${(v.fpr_at_snapshot * 100).toFixed(1)}%` : "—"}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-[var(--text-secondary)]">{v.n_samples ?? "—"}</td>
                        <td className="px-4 py-2.5 text-[var(--text-tertiary)] max-w-[200px] truncate" title={v.mutation_reason ?? ""}>
                          {v.mutation_reason || "—"}
                        </td>
                        <td className="px-4 py-2.5">
                          {idx !== 0 && (
                            <button
                              className="btn btn-secondary text-[10px] px-2 py-1 flex items-center gap-1 whitespace-nowrap"
                              onClick={() => handleRollback(v.id)}
                              disabled={rollingBack === v.id}
                              title="Restaurar esta versão"
                            >
                              <RotateCcw className="w-3 h-3" />
                              {rollingBack === v.id ? "..." : "Rollback"}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {!loadingDetail && !status && selectedId && (
        <div className="card p-8 text-center">
          <Bot className="w-10 h-10 text-[var(--text-tertiary)] opacity-30 mx-auto mb-3" />
          <p className="text-[13px] text-[var(--text-secondary)]">
            Nenhum dado de Auto-Pilot para este profile ainda.
          </p>
        </div>
      )}
    </div>
  );
}
