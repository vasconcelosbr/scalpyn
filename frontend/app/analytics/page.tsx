"use client";

import { useEffect, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet } from "@/lib/api";

function fmtC(v: number) { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(v); }

export default function AnalyticsPage() {
  const [capital, setCapital] = useState<any[]>([]);
  const [pnl, setPnl] = useState<any>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.allSettled([
      apiGet(`/analytics/capital?days=${days}`),
      apiGet("/analytics/pnl"),
    ]).then(([capRes, pnlRes]) => {
      if (capRes.status === "fulfilled") setCapital(capRes.value?.data || []);
      if (pnlRes.status === "fulfilled") setPnl(pnlRes.value);
      setLoading(false);
    });
  }, [days]);

  const dailyPnl = capital.filter((d) => d.pnl !== undefined).map((d) => ({
    time: d.time ? new Date(d.time).toLocaleDateString("en", { month: "short", day: "numeric" }) : "",
    pnl: d.pnl,
    symbol: d.symbol,
  }));

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Analytics</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">P&L evolution, capital growth, and performance breakdown.</p>
        </div>
        <div className="flex gap-1 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)] p-0.5">
          {[7, 30, 90, 365].map((d) => (
            <button key={d} onClick={() => setDays(d)} className={`px-3 py-1.5 text-[12px] font-medium rounded-[var(--radius-sm)] transition-colors ${days === d ? "bg-[var(--accent-primary)] text-white" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}>
              {d === 365 ? "1Y" : `${d}D`}
            </button>
          ))}
        </div>
      </div>

      {/* Summary Metrics */}
      {pnl && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="metric-card">
            <span className="label">Total P&L</span>
            <span className={`value text-[22px] ${pnl.total_pnl >= 0 ? "profit" : "loss"}`}>{fmtC(pnl.total_pnl)}</span>
          </div>
          <div className="metric-card">
            <span className="label">Total Trades</span>
            <span className="value text-[22px]">{pnl.total_trades}</span>
            <span className="caption">{pnl.winning_trades}W / {pnl.losing_trades}L</span>
          </div>
          <div className="metric-card">
            <span className="label">Best Trade</span>
            <span className="value text-[22px] profit">{fmtC(pnl.best_trade)}</span>
          </div>
          <div className="metric-card">
            <span className="label">Worst Trade</span>
            <span className="value text-[22px] loss">{fmtC(pnl.worst_trade)}</span>
          </div>
        </div>
      )}

      {/* Capital Evolution Chart */}
      <div className="card">
        <div className="card-header"><h3>Capital Evolution</h3></div>
        <div className="card-body h-[360px]">
          {loading ? (
            <div className="skeleton h-full w-full" />
          ) : capital.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={capital} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <defs>
                  <linearGradient id="gradCap" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#34D399" stopOpacity={0.2} />
                    <stop offset="100%" stopColor="#34D399" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="4 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fill: "#555B6E", fontSize: 11, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => v ? new Date(v).toLocaleDateString("en", { month: "short", day: "numeric" }) : ""} />
                <YAxis axisLine={false} tickLine={false} tick={{ fill: "#555B6E", fontSize: 11, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
                <Tooltip contentStyle={{ backgroundColor: "#1A1B25", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontFamily: "JetBrains Mono", color: "#8B92A5" }} formatter={(v: number) => [fmtC(v), "Capital"]} />
                <Area type="monotone" dataKey="value" stroke="#34D399" strokeWidth={2} fill="url(#gradCap)" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-full text-[var(--text-tertiary)] text-[13px]">No data yet. Charts appear after trades are closed.</div>
          )}
        </div>
      </div>

      {/* Per-Trade P&L Bar Chart */}
      {dailyPnl.length > 0 && (
        <div className="card">
          <div className="card-header"><h3>Per-Trade P&L</h3></div>
          <div className="card-body h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dailyPnl} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="4 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fill: "#555B6E", fontSize: 10, fontFamily: "JetBrains Mono" }} />
                <YAxis axisLine={false} tickLine={false} tick={{ fill: "#555B6E", fontSize: 11, fontFamily: "JetBrains Mono" }} tickFormatter={(v) => `$${v}`} />
                <Tooltip contentStyle={{ backgroundColor: "#1A1B25", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", fontFamily: "JetBrains Mono", color: "#8B92A5" }} formatter={(v: number) => [fmtC(v), "P&L"]} labelFormatter={(l) => l} />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]} fill="#34D399" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
