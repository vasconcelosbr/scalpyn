"use client";

import { useEffect, useState } from "react";
import {
  Activity, AlertTriangle, CheckCircle, ArrowRight, Shield,
  Zap, Target, Radio, XCircle, Info,
} from "lucide-react";
import { apiGet } from "@/lib/api";

/* ── Types ── */
interface IntegrityData {
  feed_delay_seconds: number;
  stale_symbols_count: number;
  avg_pipeline_latency_ms: number;
}

interface PipelineMetrics {
  discovered: number;
  filtered: number;
  scored: number;
  signals_count: number;
  executed: number;
  latency_ms: number;
}

interface Alert {
  id: string;
  alert_type: "warning" | "critical" | "info";
  message: string;
  created_at: string;
}

interface Decision {
  symbol: string;
  strategy: string;
  score: number;
  decision: string;
  created_at: string;
}

/* ── Skeleton Loaders ── */
function HealthSkeleton() {
  return (
    <div className="metric-card">
      <div className="skeleton h-4 w-32 mb-3 rounded" />
      <div className="skeleton h-6 w-24 mb-2 rounded" />
      <div className="skeleton h-3 w-48 rounded" />
    </div>
  );
}

function PipelineSkeleton() {
  return (
    <div className="card">
      <div className="card-header">
        <div className="skeleton h-4 w-36 rounded" />
      </div>
      <div className="card-body flex items-center gap-4">
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton h-16 w-24 rounded" />
        ))}
      </div>
    </div>
  );
}

function AlertsSkeleton() {
  return (
    <div className="card">
      <div className="card-header">
        <div className="skeleton h-4 w-28 rounded" />
      </div>
      <div className="card-body space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton h-14 w-full rounded" />
        ))}
      </div>
    </div>
  );
}

/* ── Pipeline Step ── */
function PipelineStep({ label, count, icon: Icon, isLast }: {
  label: string;
  count: number;
  icon: React.ElementType;
  isLast?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <div
        style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-lg)",
          padding: "12px 16px",
          textAlign: "center",
          minWidth: "90px",
        }}
      >
        <Icon size={16} style={{ color: "var(--accent-primary)", margin: "0 auto 6px" }} />
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "18px",
            fontWeight: 700,
            color: "var(--text-primary)",
          }}
        >
          {count.toLocaleString()}
        </div>
        <div style={{ fontSize: "11px", color: "var(--text-tertiary)", marginTop: "2px" }}>
          {label}
        </div>
      </div>
      {!isLast && (
        <ArrowRight size={16} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
      )}
    </div>
  );
}

/* ── Alert Card ── */
function AlertCard({ alert }: { alert: Alert }) {
  const typeConfig = {
    warning: { icon: AlertTriangle, color: "var(--color-warning)", bg: "var(--color-warning-muted)", border: "rgba(251, 191, 36, 0.25)" },
    critical: { icon: XCircle, color: "var(--color-loss)", bg: "var(--color-loss-muted)", border: "rgba(248, 113, 113, 0.25)" },
    info: { icon: Info, color: "var(--accent-primary)", bg: "var(--accent-primary-muted)", border: "var(--accent-primary-border)" },
  };

  const config = typeConfig[alert.alert_type] || typeConfig.info;
  const IconComp = config.icon;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "12px 16px",
        background: config.bg,
        border: `1px solid ${config.border}`,
        borderRadius: "var(--radius-md)",
      }}
    >
      <IconComp size={16} style={{ color: config.color, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <p style={{ fontSize: "13px", color: "var(--text-primary)", lineHeight: 1.4 }}>
          {alert.message}
        </p>
        <span style={{ fontSize: "11px", color: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}>
          {new Date(alert.created_at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}
        </span>
      </div>
      <span
        style={{
          fontSize: "10px",
          fontWeight: 600,
          textTransform: "uppercase",
          color: config.color,
          background: config.bg,
          border: `1px solid ${config.border}`,
          borderRadius: "var(--radius-sm)",
          padding: "2px 6px",
        }}
      >
        {alert.alert_type}
      </span>
    </div>
  );
}

/* ── Main Page ── */
export default function BackofficePage() {
  const [integrity, setIntegrity] = useState<IntegrityData | null>(null);
  const [pipeline, setPipeline] = useState<PipelineMetrics | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      apiGet<IntegrityData>("/backoffice/data/integrity"),
      apiGet<{ items: PipelineMetrics[] }>("/backoffice/pipeline/metrics?per_page=1"),
      apiGet<{ items: Alert[] }>("/backoffice/alerts?status=active&per_page=5"),
      apiGet<{ items: Decision[] }>("/backoffice/decisions?decision=approved&per_page=5"),
    ]).then(([intRes, pipeRes, alertRes, decRes]) => {
      if (intRes.status === "fulfilled") setIntegrity(intRes.value);
      if (pipeRes.status === "fulfilled") {
        const metrics = pipeRes.value?.items;
        if (Array.isArray(metrics) && metrics.length > 0) setPipeline(metrics[0]);
      }
      if (alertRes.status === "fulfilled") setAlerts(alertRes.value?.items || []);
      if (decRes.status === "fulfilled") setDecisions(decRes.value?.items || []);
      setLoading(false);
    });
  }, []);

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-baseline gap-3">
        <h1 style={{ color: "var(--text-primary)", fontSize: "24px", fontWeight: 700 }}>
          System Operations
        </h1>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-tertiary)" }}>
          Backoffice Control Panel
        </span>
      </div>

      {/* ── System Health Panel ── */}
      {loading ? (
        <HealthSkeleton />
      ) : integrity ? (
        <div className="metric-card">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div
                className={integrity.stale_symbols_count === 0 && integrity.feed_delay_seconds < 30 ? "live-dot" : ""}
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background: integrity.stale_symbols_count === 0 && integrity.feed_delay_seconds < 30 ? "var(--color-profit)" : "var(--color-loss)",
                  flexShrink: 0,
                }}
              />
              <div>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "16px",
                    fontWeight: 700,
                    color: integrity.stale_symbols_count === 0 && integrity.feed_delay_seconds < 30 ? "var(--color-profit)" : "var(--color-loss)",
                    textTransform: "uppercase",
                  }}
                >
                  {integrity.stale_symbols_count === 0 && integrity.feed_delay_seconds < 30 ? "HEALTHY" : "DEGRADED"}
                </span>
                <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "2px" }}>
                  System Status
                </p>
              </div>
            </div>
            <div className="flex gap-6">
              <div style={{ textAlign: "center" }}>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "16px", fontWeight: 600, color: "var(--text-primary)" }}>
                  {integrity.feed_delay_seconds.toFixed(1)}s
                </div>
                <div style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>Feed Delay</div>
              </div>
              <div style={{ textAlign: "center" }}>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "16px",
                    fontWeight: 600,
                    color: integrity.stale_symbols_count > 0 ? "var(--color-warning)" : "var(--color-profit)",
                  }}
                >
                  {integrity.stale_symbols_count}
                </div>
                <div style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>Stale Feeds</div>
              </div>
              {pipeline && (
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: "16px", fontWeight: 600, color: "var(--text-primary)" }}>
                    {pipeline.latency_ms.toFixed(0)}ms
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>Avg Latency</div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="metric-card">
          <div className="empty-state">
            <Shield size={32} />
            <p>No system health data available.</p>
          </div>
        </div>
      )}

      {/* ── Pipeline Flow Visualization ── */}
      {loading ? (
        <PipelineSkeleton />
      ) : (
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <Activity size={16} style={{ color: "var(--accent-primary)" }} />
              <h3 style={{ color: "var(--text-primary)", fontSize: "14px", fontWeight: 600 }}>
                Pipeline Flow
              </h3>
            </div>
          </div>
          <div className="card-body">
            {pipeline ? (
              <div
                className="metrics-scroll"
                style={{ display: "flex", alignItems: "center", gap: "4px", overflowX: "auto", padding: "8px 0" }}
              >
                <PipelineStep label="Discovered" count={pipeline.discovered} icon={Radio} />
                <PipelineStep label="Filtered" count={pipeline.filtered} icon={Target} />
                <PipelineStep label="Scored" count={pipeline.scored} icon={Zap} />
                <PipelineStep label="Signals" count={pipeline.signals_count} icon={Activity} />
                <PipelineStep label="Executed" count={pipeline.executed} icon={CheckCircle} isLast />
              </div>
            ) : (
              <div className="empty-state">
                <Activity size={32} />
                <p>No pipeline metrics available.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Two Column Grid: Alerts + Signals ── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {/* Active Alerts */}
        {loading ? (
          <AlertsSkeleton />
        ) : (
          <div className="card">
            <div className="card-header">
              <div className="flex items-center gap-2">
                <AlertTriangle size={16} style={{ color: "var(--color-warning)" }} />
                <h3 style={{ color: "var(--text-primary)", fontSize: "14px", fontWeight: 600 }}>
                  Active Alerts
                </h3>
              </div>
              {alerts.length > 0 && (
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "12px",
                    color: "var(--color-warning)",
                    background: "var(--color-warning-muted)",
                    padding: "2px 8px",
                    borderRadius: "var(--radius-sm)",
                  }}
                >
                  {alerts.length}
                </span>
              )}
            </div>
            <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {alerts.length > 0 ? (
                alerts.map((alert) => <AlertCard key={alert.id} alert={alert} />)
              ) : (
                <div className="empty-state">
                  <CheckCircle size={32} style={{ color: "var(--color-profit)" }} />
                  <p>No active alerts. All systems operational.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Recent Signals */}
        {loading ? (
          <AlertsSkeleton />
        ) : (
          <div className="card">
            <div className="card-header">
              <div className="flex items-center gap-2">
                <Zap size={16} style={{ color: "var(--color-profit)" }} />
                <h3 style={{ color: "var(--text-primary)", fontSize: "14px", fontWeight: 600 }}>
                  Recent Signals
                </h3>
              </div>
              <span className="caption">Latest approved</span>
            </div>
            {decisions.length > 0 ? (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Strategy</th>
                      <th className="numeric">Score</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisions.map((d, idx) => (
                      <tr key={idx}>
                        <td>
                          <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text-primary)" }}>
                            {d.symbol}
                          </span>
                        </td>
                        <td>
                          <span
                            style={{
                              fontSize: "11px",
                              fontFamily: "var(--font-mono)",
                              color: "var(--accent-primary)",
                              background: "var(--accent-primary-muted)",
                              border: "1px solid var(--accent-primary-border)",
                              borderRadius: "var(--radius-sm)",
                              padding: "2px 6px",
                            }}
                          >
                            {d.strategy}
                          </span>
                        </td>
                        <td className="numeric">
                          <span
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontWeight: 600,
                              color: d.score >= 70 ? "var(--color-profit)" : d.score >= 50 ? "var(--color-warning)" : "var(--color-loss)",
                            }}
                          >
                            {d.score.toFixed(1)}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-secondary)" }}>
                            {new Date(d.created_at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="card-body">
                <div className="empty-state">
                  <Zap size={32} />
                  <p>No approved signals yet.</p>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
