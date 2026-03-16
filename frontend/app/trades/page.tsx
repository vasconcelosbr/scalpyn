"use client";

import { useState, useEffect, useCallback } from "react";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { getAuthHeaders } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

type MarketFilter = "all" | "spot" | "futures" | "tradfi";

interface Position {
  id: string;
  symbol: string;
  side: string;
  market_type: string;
  exchange: string;
  entry_price: number;
  quantity: number;
  invested_value: number;
  take_profit_price: number | null;
  stop_loss_price: number | null;
  alpha_score_at_entry: number | null;
  entry_at: string | null;
}

interface TradeRecord {
  id: string;
  symbol: string;
  side: string;
  market_type: string;
  exchange: string;
  entry_price: number;
  exit_price: number | null;
  quantity: number;
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

const FILTER_OPTIONS: { label: string; value: MarketFilter }[] = [
  { label: "All", value: "all" },
  { label: "Spot", value: "spot" },
  { label: "Futures", value: "futures" },
  { label: "TradFi", value: "tradfi" },
];

export default function TradesPage() {
  const [activeTab, setActiveTab] = useState<"open" | "history">("open");
  const [marketFilter, setMarketFilter] = useState<MarketFilter>("all");
  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<TradeRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPositions = useCallback(async (filter: MarketFilter) => {
    setLoading(true);
    setError(null);
    try {
      const qs = filter !== "all" ? `?market_type=${filter}` : "";
      const res = await fetch(`${API_URL}/trades/positions${qs}`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) throw new Error("Failed to load positions");
      const data = await res.json();
      setPositions(data.positions ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error loading positions");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchHistory = useCallback(async (filter: MarketFilter) => {
    setLoading(true);
    setError(null);
    try {
      const qs = filter !== "all" ? `?market_type=${filter}` : "";
      const res = await fetch(`${API_URL}/trades/${qs}`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) throw new Error("Failed to load trade history");
      const data = await res.json();
      setHistory(data.trades ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error loading history");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "open") {
      fetchPositions(marketFilter);
    } else {
      fetchHistory(marketFilter);
    }
  }, [activeTab, marketFilter, fetchPositions, fetchHistory]);

  return (
    <div className="space-y-6 flex flex-col h-full w-full">
      <div className="flex justify-between items-center mb-2">
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Trades Review</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-4 border-b border-[var(--border-subtle)] pb-px relative">
        <button
          onClick={() => setActiveTab("history")}
          className={`pb-3 text-[13px] font-semibold tracking-wide px-1 transition-colors relative ${activeTab === "history" ? "text-[var(--accent-primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
        >
          Trade History
          {activeTab === "history" && <span className="absolute bottom-[-1px] left-0 right-0 h-[2px] bg-[var(--accent-primary)]" />}
        </button>
        <button
          onClick={() => setActiveTab("open")}
          className={`pb-3 text-[13px] font-semibold tracking-wide px-1 transition-colors relative flex gap-2 items-center ${activeTab === "open" ? "text-[var(--accent-primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
        >
          Active Positions
          <span className="bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-primary)] py-0.5 px-2 rounded-full text-[11px] font-mono leading-none">
            {positions.length}
          </span>
          {activeTab === "open" && <span className="absolute bottom-[-1px] left-0 right-0 h-[2px] bg-[var(--accent-primary)]" />}
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

      <div className="card w-full">
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

        {!loading && !error && activeTab === "open" && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol / Side</th>
                  <th>Type</th>
                  <th className="text-right">Entry Price</th>
                  <th className="text-right">Invested</th>
                  <th className="text-right">Take Profit</th>
                  <th className="text-right">Stop Loss</th>
                  <th className="text-right">α Score</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.id}>
                    <td>
                      <div className="font-semibold">{pos.symbol}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`badge ${pos.side.toLowerCase() === "buy" || pos.side.toLowerCase() === "long" ? "bullish" : "bearish"}`}>
                          {pos.side}
                        </span>
                        <span className="caption text-[11px] text-[var(--text-tertiary)]">{pos.exchange}</span>
                      </div>
                    </td>
                    <td>
                      <span className="caption capitalize">{pos.market_type}</span>
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">{formatCurrency(pos.entry_price)}</td>
                    <td className="numeric text-[var(--text-primary)]">{formatCurrency(pos.invested_value)}</td>
                    <td className="numeric text-[var(--accent-primary)]">
                      {pos.take_profit_price ? formatCurrency(pos.take_profit_price) : "—"}
                    </td>
                    <td className="numeric text-[var(--color-loss)]">
                      {pos.stop_loss_price ? formatCurrency(pos.stop_loss_price) : "—"}
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">
                      {pos.alpha_score_at_entry != null ? pos.alpha_score_at_entry.toFixed(1) : "—"}
                    </td>
                  </tr>
                ))}
                {positions.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                      No open positions with balance &gt; $1.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && activeTab === "history" && (
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
                </tr>
              </thead>
              <tbody>
                {history.map((trade) => (
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
                  </tr>
                ))}
                {history.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                      No trade history found.
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

