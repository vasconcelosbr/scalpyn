"use client";

import { useEffect, useState } from "react";
import { Activity, Target, TrendingUp, Wallet, Clock, Zap } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { apiGet } from "@/lib/api";

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
      if (scoresRes.status === "fulfilled") setScores((scoresRes.value?.scores || []).slice(0, 5));
      setLoading(false);
    });
  }, []);

  const todayPnl = summary?.total_pnl ?? 0;
  const openPos = summary?.open_positions ?? 0;
  const winRate = summary?.win_rate ?? 0;
  const tradesCount = summary?.trades_count ?? 0;

  const scoreLevel = (s: number) => s >= 80 ? "excellent" : s >= 60 ? "good" : s >= 40 ? "neutral" : "low";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Overview</h1>

      {/* Metrics Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2"><h3 className="label">Total P&L (Today)</h3><TrendingUp className="w-4 h-4 text-[var(--text-tertiary)]" /></div>
          <div className={`value ${todayPnl >= 0 ? "profit" : "loss"}`}>{todayPnl >= 0 ? "+" : ""}{formatCurrency(todayPnl)}</div>
        </div>
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2"><h3 className="label">Open Positions</h3><Target className="w-4 h-4 text-[var(--text-tertiary)]" /></div>
          <div className="value">{openPos}</div>
        </div>
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2"><h3 className="label">Win Rate (Today)</h3><Activity className="w-4 h-4 text-[var(--text-tertiary)]" /></div>
          <div className="value">{winRate.toFixed(1)}%</div>
          <p className="change text-[var(--text-secondary)]">{tradesCount} trades today</p>
        </div>
        <div className="metric-card">
          <div className="flex items-center justify-between pb-2"><h3 className="label">Bot Status</h3><Wallet className="w-4 h-4 text-[var(--text-tertiary)]" /></div>
          <div className="flex items-center gap-2 mt-1">
            <span className="live-dot bg-[var(--color-profit)] w-2 h-2" />
            <span className="font-semibold text-[var(--color-profit)] text-sm tracking-widest uppercase">ACTIVE</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Chart */}
        <div className="xl:col-span-2 card">
          <div className="card-header"><h3>Capital Evolution</h3></div>
          <div className="card-body h-[320px] w-full pt-6">
            {capital.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={capital} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="profitGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#34D399" stopOpacity={0.15} />
                      <stop offset="100%" stopColor="#34D399" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="4 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                  <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fill: "#555B6E", fontSize: 11, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => v ? new Date(v).toLocaleDateString("en", { month: "short", day: "numeric" }) : ""} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: "#555B6E", fontSize: 11, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
                  <Tooltip contentStyle={{ backgroundColor: "#1A1B25", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontFamily: "JetBrains Mono", color: "#8B92A5" }} />
                  <Area type="monotone" dataKey="value" stroke="#34D399" strokeWidth={2} fillOpacity={1} fill="url(#profitGradient)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-[var(--text-tertiary)] text-[13px]">
                {loading ? "Loading chart..." : "No trade history yet. Capital evolution will appear after your first trades."}
              </div>
            )}
          </div>
        </div>

        {/* Top Ranked + Activity */}
        <div className="flex flex-col gap-6">
          <div className="card">
            <div className="card-header"><h3>Top Ranked Assets</h3></div>
            <div className="p-0">
              <table className="data-table">
                <thead><tr><th>Symbol</th><th className="w-[120px]">Score</th></tr></thead>
                <tbody>
                  {scores.length > 0 ? scores.map((asset) => (
                    <tr key={asset.symbol}>
                      <td className="font-semibold">{asset.symbol}</td>
                      <td>
                        <div className="score-bar" data-level={scoreLevel(asset.score)}>
                          <span className="score-label">{asset.score.toFixed(0)}</span>
                          <div className="bar-track"><div className="bar-fill" style={{ width: `${asset.score}%` }} /></div>
                        </div>
                      </td>
                    </tr>
                  )) : (
                    <tr><td colSpan={2} className="text-center py-6 text-[var(--text-tertiary)] text-[13px]">{loading ? "Loading..." : "No scores available yet."}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card flex-1">
            <div className="card-header"><h3>Open Positions</h3></div>
            <div className="card-body space-y-3">
              {summary?.open_positions_data?.length > 0 ? summary.open_positions_data.map((pos: any) => (
                <div key={pos.id} className="flex gap-3 items-center">
                  <div className="w-6 h-6 rounded-full bg-[var(--color-profit-muted)] text-[var(--color-profit)] flex items-center justify-center shrink-0">
                    <TrendingUp className="w-3 h-3" />
                  </div>
                  <div>
                    <div className="font-semibold text-[13px]">{pos.direction?.toUpperCase()} {pos.symbol}</div>
                    <div className="data-value text-[var(--text-secondary)] text-[12px]">Entry @ {formatCurrency(pos.entry_price)}</div>
                  </div>
                </div>
              )) : (
                <div className="text-center py-4 text-[var(--text-tertiary)] text-[13px]">No open positions</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
