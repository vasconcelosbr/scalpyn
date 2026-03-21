"use client";

import { useEffect, useState } from "react";
import { Activity, Target, TrendingUp, Wallet } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency } from "@/lib/utils";
import { apiGet } from "@/lib/api";

// ── Skeleton loader ────────────────────────────────────────────────────────────
function MetricSkeleton() {
  return (
    <div className="metric-card">
      <div className="skeleton h-3 w-24 mb-3 rounded" />
      <div className="skeleton h-8 w-32 rounded" />
      <div className="skeleton h-3 w-16 mt-2 rounded" />
    </div>
  );
}

// ── Score level helper ─────────────────────────────────────────────────────────
function scoreLevel(s: number): string {
  if (s >= 80) return "excellent";
  if (s >= 60) return "good";
  if (s >= 40) return "neutral";
  if (s >= 25) return "low";
  return "critical";
}

// ── Custom recharts tooltip ────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label }: any) {
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
      <p style={{ fontSize: "11px", color: "var(--text-tertiary)", marginBottom: "4px" }}>
        {label ? new Date(label).toLocaleDateString("en", { month: "short", day: "numeric" }) : ""}
      </p>
      <p
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "15px",
          fontWeight: 600,
          color: "var(--color-profit)",
          margin: 0,
        }}
      >
        {formatCurrency(payload[0].value)}
      </p>
    </div>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────────
function EmptyChart() {
  return (
    <div className="empty-state" style={{ height: "100%" }}>
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
      </svg>
      <p>No capital history yet.<br />Start trading to see your performance curve.</p>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function Home() {
  const [summary, setSummary] = useState<any>(null);
  const [capital, setCapital] = useState<any[]>([]);
  const [scores, setScores] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      apiGet("/analytics/daily-summary"),
      apiGet("/analytics/capital?days=30"),
      apiGet("/market/scores"),
    ]).then(([sumRes, capRes, scoresRes]) => {
      if (sumRes.status === "fulfilled") setSummary(sumRes.value);
      if (capRes.status === "fulfilled") setCapital(capRes.value?.data || []);
      if (scoresRes.status === "fulfilled")
        setScores((scoresRes.value?.scores || []).slice(0, 8));
      setLoading(false);
    });
  }, []);

  const todayPnl = summary?.total_pnl ?? 0;
  const openPos = summary?.open_positions ?? 0;
  const winRate = summary?.win_rate ?? 0;
  const tradesCount = summary?.trades_count ?? 0;

  return (
    <div className="space-y-6">
      {/* ── Page title ── */}
      <div className="flex items-baseline gap-3">
        <h1 style={{ color: "var(--text-primary)" }}>Overview</h1>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "12px",
            color: "var(--text-tertiary)",
          }}
        >
          {new Date().toLocaleDateString("en", {
            weekday: "long",
            month: "long",
            day: "numeric",
          })}
        </span>
      </div>

      {/* ── Metric cards — mobile: horizontal scroll, desktop: grid ── */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => <MetricSkeleton key={i} />)}
        </div>
      ) : (
        <>
          {/* Desktop grid */}
          <div className="hidden md:grid grid-cols-2 xl:grid-cols-4 gap-4">
            <MetricCards
              todayPnl={todayPnl}
              openPos={openPos}
              winRate={winRate}
              tradesCount={tradesCount}
            />
          </div>
          {/* Mobile: horizontal scroll */}
          <div className="flex md:hidden metrics-scroll">
            <MetricCards
              todayPnl={todayPnl}
              openPos={openPos}
              winRate={winRate}
              tradesCount={tradesCount}
            />
          </div>
        </>
      )}

      {/* ── Chart + Rankings ── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Capital chart */}
        <div className="xl:col-span-2 card">
          <div className="card-header">
            <h3>Capital Evolution</h3>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--text-tertiary)",
              }}
            >
              30d
            </span>
          </div>
          <div className="card-body" style={{ height: "300px", paddingTop: "24px" }}>
            {capital.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={capital} margin={{ top: 0, right: 4, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="capGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#34D399" stopOpacity={0.18} />
                      <stop offset="100%" stopColor="#34D399" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(255,255,255,0.04)"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="time"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#555B6E", fontSize: 10, fontFamily: "JetBrains Mono" }}
                    tickFormatter={(v) =>
                      v
                        ? new Date(v).toLocaleDateString("en", {
                            month: "short",
                            day: "numeric",
                          })
                        : ""
                    }
                  />
                  <YAxis
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "#555B6E", fontSize: 10, fontFamily: "JetBrains Mono" }}
                    tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#34D399"
                    strokeWidth={1.5}
                    fillOpacity={1}
                    fill="url(#capGrad)"
                    dot={false}
                    activeDot={{ r: 4, fill: "#34D399", strokeWidth: 0 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyChart />
            )}
          </div>
        </div>

        {/* Top ranked assets */}
        <div className="card">
          <div className="card-header">
            <h3>Top Alpha Scores</h3>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--text-tertiary)",
              }}
            >
              live
            </span>
          </div>
          <div style={{ padding: 0 }}>
            {loading ? (
              <div style={{ padding: "20px" }} className="space-y-3">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div key={i} className="skeleton h-8 rounded" />
                ))}
              </div>
            ) : scores.length > 0 ? (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th style={{ width: 130 }}>Score</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((asset) => (
                    <tr key={asset.symbol}>
                      <td
                        style={{
                          fontWeight: 600,
                          fontFamily: "var(--font-mono)",
                          fontSize: "13px",
                        }}
                      >
                        {asset.symbol.replace("_USDT", "")}
                        <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}>
                          /USDT
                        </span>
                      </td>
                      <td>
                        <div className="score-bar" data-level={scoreLevel(asset.score)}>
                          <div className="bar-track">
                            <div
                              className="bar-fill"
                              style={{ width: `${asset.score}%` }}
                            />
                          </div>
                          <span className="score-label">{asset.score.toFixed(0)}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="empty-state">
                <svg
                  width="32"
                  height="32"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                >
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                <p>No alpha scores available.<br />Scores update every 30 seconds.</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Recent open positions ── */}
      {!loading && summary?.open_positions_data?.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h3>Open Positions</h3>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "12px",
                fontWeight: 600,
                color: "var(--text-secondary)",
              }}
            >
              {openPos} active
            </span>
          </div>
          <div style={{ padding: 0 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="numeric">Entry</th>
                  <th className="numeric">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {summary.open_positions_data.slice(0, 8).map((pos: any) => {
                  const pnl = pos.unrealised_pnl ?? 0;
                  return (
                    <tr key={pos.id}>
                      <td style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                        {pos.symbol}
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            pos.direction?.toLowerCase() === "long" ||
                            pos.side?.toLowerCase() === "buy"
                              ? "bullish"
                              : "bearish"
                          }`}
                        >
                          {pos.direction?.toUpperCase() ?? pos.side?.toUpperCase()}
                        </span>
                      </td>
                      <td className="numeric">{formatCurrency(pos.entry_price)}</td>
                      <td className={`numeric ${pnl >= 0 ? "profit" : "loss"}`}>
                        {pnl >= 0 ? "+" : ""}
                        {formatCurrency(pnl)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Metric card set ────────────────────────────────────────────────────────────
function MetricCards({
  todayPnl,
  openPos,
  winRate,
  tradesCount,
}: {
  todayPnl: number;
  openPos: number;
  winRate: number;
  tradesCount: number;
}) {
  return (
    <>
      <div className="metric-card">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span className="label">Today's P&amp;L</span>
          <TrendingUp
            size={16}
            style={{ color: "var(--text-tertiary)", flexShrink: 0 }}
          />
        </div>
        <div
          className={`value ${todayPnl >= 0 ? "profit" : "loss"}`}
          style={{ marginTop: "8px" }}
        >
          {todayPnl >= 0 ? "+" : ""}
          {formatCurrency(todayPnl)}
        </div>
        <p className={`change ${todayPnl >= 0 ? "up" : "down"}`} style={{ marginTop: "4px" }}>
          {todayPnl >= 0 ? "▲" : "▼"} vs. yesterday
        </p>
      </div>

      <div className="metric-card">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span className="label">Open Positions</span>
          <Target size={16} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
        </div>
        <div className="value" style={{ marginTop: "8px" }}>
          {openPos}
        </div>
        <p className="change" style={{ marginTop: "4px", color: "var(--text-tertiary)" }}>
          spot + futures
        </p>
      </div>

      <div className="metric-card">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span className="label">Win Rate</span>
          <Activity size={16} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
        </div>
        <div className="value" style={{ marginTop: "8px" }}>
          {winRate.toFixed(1)}%
        </div>
        <p className="change" style={{ marginTop: "4px", color: "var(--text-tertiary)" }}>
          {tradesCount} trades today
        </p>
      </div>

      <div className="metric-card">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span className="label">Engine Status</span>
          <Wallet size={16} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            marginTop: "12px",
          }}
        >
          <span
            className="live-dot"
            style={{ width: "8px", height: "8px", background: "var(--color-profit)" }}
          />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "14px",
              fontWeight: 700,
              color: "var(--color-profit)",
              letterSpacing: "0.1em",
            }}
          >
            ACTIVE
          </span>
        </div>
        <p className="change" style={{ marginTop: "4px", color: "var(--text-tertiary)" }}>
          scanning markets
        </p>
      </div>
    </>
  );
}
