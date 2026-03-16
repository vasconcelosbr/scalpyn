"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";

function formatCurrency(v: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(v);
}

function formatPercent(v: number) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function formatDuration(seconds: number | null) {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

export default function TradesPage() {
  const [activeTab, setActiveTab] = useState("open");
  const [openTrades, setOpenTrades] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [closingId, setClosingId] = useState<string | null>(null);

  const fetchData = () => {
    setLoading(true);
    Promise.allSettled([
      apiGet("/trades/open"),
      apiGet("/trades/history?limit=100"),
    ]).then(([openRes, histRes]) => {
      if (openRes.status === "fulfilled") setOpenTrades(openRes.value?.positions || []);
      if (histRes.status === "fulfilled") setHistory(histRes.value?.trades || []);
      setLoading(false);
    });
  };

  useEffect(() => { fetchData(); }, []);

  const handleClose = async (tradeId: string) => {
    setClosingId(tradeId);
    try {
      await apiPost(`/trades/${tradeId}/close`);
      fetchData();
    } catch (e: any) {
      alert(`Failed to close: ${e.message}`);
    }
    setClosingId(null);
  };

  return (
    <div className="space-y-6 flex flex-col h-full w-full">
      <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Trades Review</h1>

      <div className="flex gap-4 border-b border-[var(--border-subtle)] pb-px">
        {[
          { id: "open", label: "Active Positions", count: openTrades.length },
          { id: "history", label: "Trade History", count: history.length },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`pb-3 text-[13px] font-semibold tracking-wide px-1 transition-colors relative flex gap-2 items-center ${activeTab === tab.id ? "text-[var(--accent-primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
          >
            {tab.label}
            <span className="bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-primary)] py-0.5 px-2 rounded-full text-[11px] font-mono">{tab.count}</span>
            {activeTab === tab.id && <span className="absolute bottom-[-1px] left-0 right-0 h-[2px] bg-[var(--accent-primary)]" />}
          </button>
        ))}
      </div>

      <div className="card w-full">
        {loading ? (
          <div className="p-8"><div className="skeleton h-48 w-full" /></div>
        ) : activeTab === "open" ? (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol / Direction</th>
                  <th className="text-right">Entry Price</th>
                  <th className="text-right">Quantity</th>
                  <th className="text-right">Invested</th>
                  <th className="text-right">TP / SL</th>
                  <th className="text-center w-[100px]">Actions</th>
                </tr>
              </thead>
              <tbody>
                {openTrades.length === 0 ? (
                  <tr><td colSpan={6} className="text-center py-12 text-[var(--text-tertiary)]">No open positions</td></tr>
                ) : openTrades.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <div className="font-semibold">{t.symbol}</div>
                      <span className={`badge ${t.direction === "long" ? "bullish" : "bearish"}`}>{t.direction}</span>
                      <span className="caption ml-2">{t.market_type}</span>
                    </td>
                    <td className="numeric">{formatCurrency(t.entry_price)}</td>
                    <td className="numeric text-[var(--text-secondary)]">{t.quantity?.toFixed(6)}</td>
                    <td className="numeric text-[var(--text-secondary)]">{formatCurrency(t.invested_value)}</td>
                    <td className="text-right">
                      <div className="text-[var(--color-profit)] data-value text-[12px]">TP: {t.take_profit_price ? formatCurrency(t.take_profit_price) : "—"}</div>
                      <div className="text-[var(--color-loss)] data-value text-[12px]">SL: {t.stop_loss_price ? formatCurrency(t.stop_loss_price) : "—"}</div>
                    </td>
                    <td className="text-center">
                      <button
                        className="btn btn-danger py-1.5 px-3 text-[12px]"
                        onClick={() => handleClose(t.id)}
                        disabled={closingId === t.id}
                      >
                        {closingId === t.id ? "..." : "Close"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol & Side</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">Holding</th>
                  <th className="text-right">Score</th>
                  <th className="text-right">Realized P&L</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 ? (
                  <tr><td colSpan={7} className="text-center py-12 text-[var(--text-tertiary)]">No trade history yet</td></tr>
                ) : history.map((t) => (
                  <tr key={t.id}>
                    <td className="data-value text-[13px] text-[var(--text-secondary)]">
                      {t.exit_at ? new Date(t.exit_at).toLocaleDateString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                    </td>
                    <td>
                      <div className="font-semibold">{t.symbol}</div>
                      <span className={`badge ${t.direction === "long" ? "bullish" : "bearish"}`}>{t.direction}</span>
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">{formatCurrency(t.entry_price)}</td>
                    <td className="numeric">{t.exit_price ? formatCurrency(t.exit_price) : "—"}</td>
                    <td className="numeric text-[var(--text-secondary)] text-[12px]">{formatDuration(t.holding_seconds)}</td>
                    <td className="numeric text-[var(--accent-primary)]">{t.alpha_score_at_entry?.toFixed(0) ?? "—"}</td>
                    <td className="text-right">
                      <div className={`data-value ${(t.profit_loss ?? 0) >= 0 ? "profit" : "loss"}`}>
                        {(t.profit_loss ?? 0) >= 0 ? "+" : ""}{formatCurrency(t.profit_loss ?? 0)}
                      </div>
                      <div className={`percentage text-[12px] mt-0.5 ${(t.profit_loss_pct ?? 0) >= 0 ? "profit" : "loss"}`}>
                        {formatPercent(t.profit_loss_pct ?? 0)}
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
  );
}
