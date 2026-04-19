"use client";

import { useEffect, useState } from "react";
import { BarChart3, CheckCircle, Clock, AlertTriangle, TrendingUp, Layers } from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Bar, BarChart, Legend,
} from "recharts";
import { apiGet } from "@/lib/api";
import { formatPercent } from "@/lib/utils";

/* ── Types ── */
interface DashboardData {
  total_assets_analyzed: number;
  approval_rate: number;
  avg_latency_ms: number;
  error_rate: number;
  strategies: StrategyRow[];
}

interface StrategyRow {
  strategy: string;
  runs: number;
  approved: number;
  rejected: number;
  avg_latency: number;
}

interface PipelinePoint {
  timestamp: string;
  discovered: number;
  scored: number;
  approved: number;
}

/* ── Skeleton Loaders ── */
function MetricSkeleton() {
  return (
    <div className="metric-card">
      <div className="skeleton h-3 w-24 mb-3 rounded" />
      <div className="skeleton h-8 w-32 rounded" />
      <div className="skeleton h-3 w-16 mt-2 rounded" />
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="card">
      <div className="card-header">
        <div className="skeleton h-4 w-40 rounded" />
      </div>
      <div className="card-body" style={{ height: "300px", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div className="skeleton h-full w-full rounded" />
      </div>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="card">
      <div className="card-header">
        <div className="skeleton h-4 w-48 rounded" />
      </div>
      <div className="card-body space-y-3">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton h-10 w-full rounded" />
        ))}
      </div>
    </div>
  );
}

/* ── Custom Tooltip ── */
function PipelineTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-default)",
        borderRadius: "var(--radius-md)",
        padding: "10px 14px",
        boxShadow: "var(--shadow-md)",
      }}
    >
      <p style={{ fontSize: "11px", color: "var(--text-tertiary)", marginBottom: "6px" }}>
        {label}
      </p>
      {payload.map((entry: any) => (
        <p
          key={entry.dataKey}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "13px",
            color: entry.color,
            marginBottom: "2px",
          }}
        >
          {entry.name}: {entry.value}
        </p>
      ))}
    </div>
  );
}

/* ── Strategy Badge ── */
function StrategyBadge({ strategy }: { strategy: string }) {
  const level = strategy.toUpperCase();
  let bg = "var(--accent-primary-muted)";
  let color = "var(--accent-primary)";
  let border = "var(--accent-primary-border)";

  if (level.includes("L1")) {
    bg = "var(--accent-primary-muted)";
    color = "var(--accent-primary)";
    border = "var(--accent-primary-border)";
  } else if (level.includes("L2")) {
    bg = "var(--color-warning-muted)";
    color = "var(--color-warning)";
    border = "rgba(251, 191, 36, 0.25)";
  } else if (level.includes("L3")) {
    bg = "var(--color-profit-muted)";
    color = "var(--color-profit)";
    border = "rgba(52, 211, 153, 0.25)";
  }

  return (
    <span
      style={{
        background: bg,
        color: color,
        border: `1px solid ${border}`,
        borderRadius: "var(--radius-sm)",
        padding: "2px 8px",
        fontSize: "11px",
        fontFamily: "var(--font-mono)",
        fontWeight: 600,
      }}
    >
      {strategy}
    </span>
  );
}

/* ── Main Page ── */
export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [pipeline, setPipeline] = useState<PipelinePoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      apiGet<DashboardData>("/backoffice/dashboard"),
      apiGet<{ data: PipelinePoint[] }>("/backoffice/pipeline/history?hours=24"),
    ]).then(([dashRes, pipeRes]) => {
      if (dashRes.status === "fulfilled") setDashboard(dashRes.value);
      if (pipeRes.status === "fulfilled") setPipeline(pipeRes.value?.data || []);
      setLoading(false);
    });
  }, []);

  const chartData = pipeline.map((p) => ({
    ...p,
    time: new Date(p.timestamp).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit", hour12: false }),
  }));

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-baseline gap-3">
        <h1 style={{ color: "var(--text-primary)", fontSize: "24px", fontWeight: 700 }}>
          Executive Dashboard
        </h1>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-tertiary)" }}>
          {new Date().toLocaleDateString("en", { weekday: "long", month: "long", day: "numeric" })}
        </span>
      </div>

      {/* ── KPI Metric Cards ── */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => <MetricSkeleton key={i} />)}
        </div>
      ) : dashboard ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <Layers size={14} style={{ color: "var(--accent-primary)" }} />
              <span className="label">Total Assets Analyzed</span>
            </div>
            <span
              className="data-value"
              style={{ fontSize: "28px", fontWeight: 700, color: "var(--text-primary)" }}
            >
              {dashboard.total_assets_analyzed.toLocaleString()}
            </span>
          </div>

          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle size={14} style={{ color: "var(--color-profit)" }} />
              <span className="label">Approval Rate</span>
            </div>
            <span
              className="data-value"
              style={{
                fontSize: "28px",
                fontWeight: 700,
                color: dashboard.approval_rate >= 50 ? "var(--color-profit)" : "var(--color-loss)",
              }}
            >
              {dashboard.approval_rate.toFixed(1)}%
            </span>
          </div>

          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <Clock size={14} style={{ color: "var(--color-warning)" }} />
              <span className="label">Avg Latency</span>
            </div>
            <span
              className="data-value"
              style={{
                fontSize: "28px",
                fontWeight: 700,
                color: dashboard.avg_latency_ms < 500 ? "var(--color-profit)" : "var(--color-warning)",
              }}
            >
              {dashboard.avg_latency_ms.toFixed(0)}
              <span style={{ fontSize: "14px", color: "var(--text-secondary)", marginLeft: "4px" }}>ms</span>
            </span>
          </div>

          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle size={14} style={{ color: "var(--color-loss)" }} />
              <span className="label">Error Rate</span>
            </div>
            <span
              className="data-value"
              style={{
                fontSize: "28px",
                fontWeight: 700,
                color: dashboard.error_rate < 5 ? "var(--color-profit)" : "var(--color-loss)",
              }}
            >
              {dashboard.error_rate.toFixed(2)}%
            </span>
          </div>
        </div>
      ) : (
        <div className="empty-state">
          <BarChart3 size={40} />
          <p>No dashboard data available yet.</p>
        </div>
      )}

      {/* ── Pipeline Over Time Chart ── */}
      {loading ? (
        <ChartSkeleton />
      ) : (
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <TrendingUp size={16} style={{ color: "var(--accent-primary)" }} />
              <h3 style={{ color: "var(--text-primary)", fontSize: "14px", fontWeight: 600 }}>
                Pipeline Over Time (24h)
              </h3>
            </div>
          </div>
          <div className="card-body" style={{ height: "300px" }}>
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gradDiscovered" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#4F7BF7" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#4F7BF7" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="gradScored" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#FBBF24" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#FBBF24" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="gradApproved" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#34D399" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#34D399" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                  <XAxis
                    dataKey="time"
                    tick={{ fontSize: 11, fill: "var(--text-tertiary)" }}
                    axisLine={{ stroke: "var(--border-subtle)" }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip content={<PipelineTooltip />} />
                  <Legend
                    wrapperStyle={{ fontSize: "12px", color: "var(--text-secondary)" }}
                  />
                  <Area
                    type="monotone"
                    dataKey="discovered"
                    name="Discovered"
                    stroke="#4F7BF7"
                    strokeWidth={1.5}
                    fill="url(#gradDiscovered)"
                    dot={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="scored"
                    name="Scored"
                    stroke="#FBBF24"
                    strokeWidth={1.5}
                    fill="url(#gradScored)"
                    dot={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="approved"
                    name="Approved"
                    stroke="#34D399"
                    strokeWidth={1.5}
                    fill="url(#gradApproved)"
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="empty-state" style={{ height: "100%" }}>
                <TrendingUp size={40} />
                <p>No pipeline data for the last 24 hours.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Strategy Performance Table ── */}
      {loading ? (
        <TableSkeleton />
      ) : dashboard?.strategies && dashboard.strategies.length > 0 ? (
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <BarChart3 size={16} style={{ color: "var(--accent-primary)" }} />
              <h3 style={{ color: "var(--text-primary)", fontSize: "14px", fontWeight: 600 }}>
                Strategy Performance
              </h3>
            </div>
            <span className="caption">{dashboard.strategies.length} strategies</span>
          </div>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th className="numeric">Runs</th>
                  <th className="numeric">Approved</th>
                  <th className="numeric">Rejected</th>
                  <th className="numeric">Avg Latency</th>
                </tr>
              </thead>
              <tbody>
                {dashboard.strategies.map((row, idx) => (
                  <tr key={idx}>
                    <td>
                      <StrategyBadge strategy={row.strategy} />
                    </td>
                    <td className="numeric">{row.runs.toLocaleString()}</td>
                    <td className="numeric profit">{row.approved.toLocaleString()}</td>
                    <td className="numeric loss">{row.rejected.toLocaleString()}</td>
                    <td className="numeric">
                      <span style={{ fontFamily: "var(--font-mono)" }}>
                        {row.avg_latency.toFixed(0)}ms
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : !loading && (
        <div className="card">
          <div className="card-body">
            <div className="empty-state">
              <BarChart3 size={40} />
              <p>No strategy data available yet.</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
