"use client";

import { useEffect, useState } from "react";
import { Download, FileText } from "lucide-react";
import { apiGet } from "@/lib/api";

function fmtC(v: number) { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(v); }
function fmtP(v: number) { return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`; }

export default function ReportsPage() {
  const [metrics, setMetrics] = useState<any>(null);
  const [reports, setReports] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  const fetchData = async () => {
    setLoading(true);
    const [m, r] = await Promise.allSettled([
      apiGet(`/reports/metrics?days=${days}`),
      apiGet(`/reports/trades?limit=200`),
    ]);
    if (m.status === "fulfilled") setMetrics(m.value);
    if (r.status === "fulfilled") setReports(r.value?.reports || []);
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, [days]);

  const handleExport = () => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
    const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";
    window.open(`/api/reports/trades/export`, "_blank");
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Reporting & Analytics</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Performance metrics and trade reports with indicator snapshots at entry.</p>
        </div>
        <div className="flex gap-2">
          <div className="flex gap-1 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-md)] p-0.5">
            {[7, 30, 90].map((d) => (
              <button key={d} onClick={() => setDays(d)} className={`px-3 py-1 text-[12px] font-medium rounded-[var(--radius-sm)] transition-colors ${days === d ? "bg-[var(--accent-primary)] text-white" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}>
                {d}D
              </button>
            ))}
          </div>
          <button onClick={handleExport} className="btn btn-primary">
            <Download className="w-4 h-4 mr-2" />Export CSV
          </button>
        </div>
      </div>

      {/* Metric Cards */}
      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
          {[
            { label: "Total P&L", value: fmtC(metrics.total_pnl), color: metrics.total_pnl >= 0 ? "profit" : "loss" },
            { label: "Total P&L %", value: fmtP(metrics.total_pnl_pct), color: metrics.total_pnl_pct >= 0 ? "profit" : "loss" },
            { label: "Win Rate", value: `${metrics.win_rate}%`, color: metrics.win_rate >= 50 ? "profit" : "loss" },
            { label: "Sharpe Ratio", value: metrics.sharpe_ratio?.toFixed(2) ?? "—", color: "" },
            { label: "Max Drawdown", value: fmtC(metrics.max_drawdown_pct), color: "loss" },
            { label: "Profit Factor", value: metrics.profit_factor?.toFixed(2) ?? "—", color: "" },
          ].map((m, i) => (
            <div key={i} className="metric-card">
              <span className="label">{m.label}</span>
              <span className={`value text-[20px] ${m.color}`}>{m.value}</span>
            </div>
          ))}
        </div>
      )}

      {/* Reports Table */}
      <div className="card">
        <div className="card-header">
          <h3>Trade Reports with Indicator Snapshots</h3>
          <span className="caption">{reports.length} trades</span>
        </div>
        <div className="overflow-x-auto">
          {loading ? (
            <div className="p-8"><div className="skeleton h-48 w-full" /></div>
          ) : (
            <table className="data-table text-[12px]">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol</th>
                  <th>Dir</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">P&L</th>
                  <th className="text-right">Score</th>
                  <th className="text-right">RSI</th>
                  <th className="text-right">ADX</th>
                  <th className="text-right">ATR%</th>
                  <th className="text-right">MACD</th>
                  <th className="text-right">Vol Spike</th>
                </tr>
              </thead>
              <tbody>
                {reports.length === 0 ? (
                  <tr><td colSpan={12} className="text-center py-12 text-[var(--text-tertiary)]">
                    <FileText className="w-8 h-8 mx-auto mb-2 opacity-30" />No reports yet. Reports appear after trades are closed.
                  </td></tr>
                ) : reports.map((r, i) => (
                  <tr key={i}>
                    <td className="text-[var(--text-secondary)]">{r.date ? new Date(r.date).toLocaleDateString("en", { month: "short", day: "numeric" }) : "—"}</td>
                    <td className="font-semibold">{r.symbol}</td>
                    <td><span className={`badge ${r.direction === "long" ? "bullish" : "bearish"}`}>{r.direction}</span></td>
                    <td className="numeric">{fmtC(r.entry_price)}</td>
                    <td className="numeric">{r.exit_price ? fmtC(r.exit_price) : "—"}</td>
                    <td className={`numeric ${r.profit_loss >= 0 ? "profit" : "loss"}`}>{fmtP(r.profit_loss_pct)}</td>
                    <td className="numeric text-[var(--accent-primary)]">{r.alpha_score?.toFixed(0) ?? "—"}</td>
                    <td className="numeric">{r.rsi?.toFixed(1) ?? "—"}</td>
                    <td className="numeric">{r.adx?.toFixed(1) ?? "—"}</td>
                    <td className="numeric">{r.atr_pct?.toFixed(2) ?? "—"}%</td>
                    <td className="numeric">{r.macd?.toFixed(4) ?? "—"}</td>
                    <td className="numeric">{r.volume_spike?.toFixed(1) ?? "—"}x</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
