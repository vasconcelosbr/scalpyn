"use client";

import { useState, useEffect, useCallback } from "react";
import { Download } from "lucide-react";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { getAuthHeaders } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

type MarketFilter = "all" | "spot" | "futures" | "tradfi";

interface TradeRecord {
  id: string;
  symbol: string;
  side: string;
  market_type: string;
  exchange: string;
  entry_price: number;
  exit_price: number | null;
  invested_value: number;
  profit_loss: number | null;
  profit_loss_pct: number | null;
  holding_seconds: number | null;
  alpha_score_at_entry: number | null;
  entry_at: string | null;
  exit_at: string | null;
}

function formatHolding(seconds: number | null): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d ${h % 24}h`;
  if (h > 0) return `${h}h`;
  return `${Math.floor(seconds / 60)}m`;
}

function exportCSV(trades: TradeRecord[]) {
  const headers = ["Exit Date", "Symbol", "Side", "Type", "Exchange", "Entry", "Exit", "Time Held", "P&L", "P&L %", "α Score"];
  const rows = trades.map((t) => [
    t.exit_at ? new Date(t.exit_at).toLocaleString() : "",
    t.symbol,
    t.side,
    t.market_type,
    t.exchange,
    t.entry_price,
    t.exit_price ?? "",
    formatHolding(t.holding_seconds),
    t.profit_loss ?? "",
    t.profit_loss_pct != null ? formatPercent(t.profit_loss_pct) : "",
    t.alpha_score_at_entry ?? "",
  ]);
  const csv = [headers, ...rows].map((r) => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "scalpyn_trades.csv";
  a.click();
  URL.revokeObjectURL(url);
}

const FILTER_OPTIONS: { label: string; value: MarketFilter }[] = [
  { label: "All", value: "all" },
  { label: "Spot", value: "spot" },
  { label: "Futures", value: "futures" },
  { label: "TradFi", value: "tradfi" },
];

export default function ReportsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>("all");
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTrades = useCallback(async (filter: MarketFilter) => {
    setLoading(true);
    setError(null);
    try {
      const qs = filter !== "all" ? `?market_type=${filter}` : "";
      const res = await fetch(`${API_URL}/trades/${qs}`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) throw new Error("Failed to load trades");
      const data = await res.json();
      setTrades(data.trades ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error loading trades");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTrades(marketFilter);
  }, [marketFilter, fetchTrades]);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Reporting & Analytics</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Executed trades history across all market types.</p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => exportCSV(trades)}
          disabled={trades.length === 0}
        >
          <Download className="w-4 h-4 mr-2" />
          Export CSV
        </button>
      </div>

      {/* Market Type Filter */}
      <div className="flex items-center gap-2">
        {FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setMarketFilter(opt.value)}
            className={`px-3 py-1.5 rounded-full text-[12px] font-semibold transition-colors border ${
              marketFilter === opt.value
                ? "bg-[var(--accent-primary)] text-white border-[var(--accent-primary)]"
                : "bg-transparent text-[var(--text-secondary)] border-[var(--border-default)] hover:text-[var(--text-primary)]"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="card">
        {loading && (
          <div className="flex items-center justify-center py-16 text-[var(--text-tertiary)] text-[13px]">
            Loading…
          </div>
        )}

        {!loading && error && (
          <div className="flex items-center justify-center py-16 text-[var(--color-loss)] text-[13px]">
            {error}
          </div>
        )}

        {!loading && !error && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="sorted desc">Exit Date</th>
                  <th>Symbol & Side</th>
                  <th>Type</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">Time Held</th>
                  <th className="text-right">Realized P&L</th>
                  <th className="text-right">α Score</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => (
                  <tr key={trade.id}>
                    <td className="data-value text-[13px] text-[var(--text-secondary)]">
                      {trade.exit_at ? new Date(trade.exit_at).toLocaleString() : "—"}
                    </td>
                    <td>
                      <div className="font-semibold">{trade.symbol}</div>
                      <div className="flex items-center gap-1 mt-1">
                        <span className={`badge ${trade.side.toLowerCase() === "buy" || trade.side.toLowerCase() === "long" ? "bullish" : "bearish"}`}>
                          {trade.side}
                        </span>
                        <span className="caption text-[11px] text-[var(--text-tertiary)] ml-1">{trade.exchange}</span>
                      </div>
                    </td>
                    <td>
                      <span className="caption capitalize">{trade.market_type}</span>
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">{formatCurrency(trade.entry_price)}</td>
                    <td className="numeric text-[var(--text-primary)]">
                      {trade.exit_price ? formatCurrency(trade.exit_price) : "—"}
                    </td>
                    <td className="numeric text-[var(--text-secondary)] text-[12px]">
                      {formatHolding(trade.holding_seconds)}
                    </td>
                    <td className="text-right">
                      {trade.profit_loss != null ? (
                        <>
                          <div className={`data-value ${trade.profit_loss >= 0 ? "profit" : "loss"}`}>
                            {trade.profit_loss > 0 ? "+" : ""}{formatCurrency(trade.profit_loss)}
                          </div>
                          {trade.profit_loss_pct != null && (
                            <div className={`percentage text-[12px] mt-0.5 ${trade.profit_loss_pct >= 0 ? "profit" : "loss"}`}>
                              {formatPercent(trade.profit_loss_pct)}
                            </div>
                          )}
                        </>
                      ) : (
                        <span className="text-[var(--text-tertiary)]">—</span>
                      )}
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">
                      {trade.alpha_score_at_entry != null ? trade.alpha_score_at_entry.toFixed(1) : "—"}
                    </td>
                  </tr>
                ))}
                {trades.length === 0 && (
                  <tr>
                    <td colSpan={8} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                      No executed trades found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

