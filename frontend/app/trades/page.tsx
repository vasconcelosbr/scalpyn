"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarDays, History, Wallet } from "lucide-react";
import { apiGet } from "@/lib/api";

interface ActivePosition {
  id: string;
  source: string;
  market_type: "spot" | "futures";
  symbol: string;
  direction: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  invested_value: number;
  current_value: number;
  profit_loss: number;
  profit_loss_pct: number;
}

interface TradeHistoryItem {
  id: string;
  symbol: string;
  direction: string;
  market_type: "spot" | "futures";
  entry_price: number;
  exit_price: number | null;
  invested_value: number;
  profit_loss: number | null;
  profit_loss_pct: number | null;
  exit_at: string | null;
  holding_seconds: number | null;
}

interface OpenPositionsResponse {
  positions: ActivePosition[];
  count: number;
  source: string;
}

interface HistoryResponse {
  trades: TradeHistoryItem[];
  total: number;
  summary?: {
    win_rate: number;
    total_pnl: number;
    avg_profit_pct: number;
  };
}

type HistoryPreset = "7" | "30" | "90" | "custom";

function formatCurrency(value: number | null | undefined) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value ?? 0);
}

function formatPercent(value: number | null | undefined) {
  const numeric = value ?? 0;
  return `${numeric >= 0 ? "+" : ""}${numeric.toFixed(2)}%`;
}

function formatDuration(seconds: number | null) {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

function endOfDay(date: string) {
  return date ? `${date}T23:59:59.999999` : "";
}

export default function TradesPage() {
  const [activeTab, setActiveTab] = useState<"open" | "history">("open");
  const [openData, setOpenData] = useState<OpenPositionsResponse | null>(null);
  const [historyData, setHistoryData] = useState<HistoryResponse | null>(null);
  const [loadingOpen, setLoadingOpen] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [preset, setPreset] = useState<HistoryPreset>("7");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  useEffect(() => {
    apiGet<OpenPositionsResponse>("/trades/open?min_value_usdt=10")
      .then(setOpenData)
      .finally(() => setLoadingOpen(false));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ limit: "500" });
    if (preset === "custom") {
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endOfDay(endDate));
    } else {
      params.set("period_days", preset);
    }

    apiGet<HistoryResponse>(`/trades/history?${params.toString()}`)
      .then(setHistoryData)
      .finally(() => setLoadingHistory(false));
  }, [preset, startDate, endDate]);

  const activePositions = openData?.positions ?? [];
  const history = historyData?.trades ?? [];
  const historySummary = useMemo(() => historyData?.summary, [historyData]);

  return (
    <div className="space-y-6 flex flex-col h-full w-full">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Trades Review</h1>
          <p className="text-[13px] text-[var(--text-secondary)] mt-1">
            Active Gate positions and closed trade history with period filters.
          </p>
        </div>
        {openData ? <span className="caption">Source: {openData.source === "exchange" ? "Gate.io" : "Local DB fallback"}</span> : null}
      </div>

      <div className="flex gap-4 border-b border-[var(--border-subtle)] pb-px">
        {[
          { id: "open" as const, label: "Active Positions", count: activePositions.length },
          { id: "history" as const, label: "Trade History", count: history.length },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`pb-3 text-[13px] font-semibold tracking-wide px-1 transition-colors relative flex gap-2 items-center ${
              activeTab === tab.id ? "text-[var(--accent-primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {tab.label}
            <span className="bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-primary)] py-0.5 px-2 rounded-full text-[11px] font-mono">
              {tab.count}
            </span>
            {activeTab === tab.id ? <span className="absolute bottom-[-1px] left-0 right-0 h-[2px] bg-[var(--accent-primary)]" /> : null}
          </button>
        ))}
      </div>

      {activeTab === "history" ? (
        <div className="card">
          <div className="card-body p-4 flex flex-wrap items-end gap-3">
            {(["7", "30", "90"] as const).map((value) => (
            <button
              key={value}
              onClick={() => {
                setLoadingHistory(true);
                setPreset(value);
              }}
                className={`px-3 py-1.5 rounded-[var(--radius-sm)] text-[12px] font-medium border ${
                  preset === value
                    ? "bg-[var(--accent-primary)] text-white border-[var(--accent-primary)]"
                    : "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-default)]"
                }`}
              >
                {value}D
              </button>
            ))}
            <button
              onClick={() => {
                setLoadingHistory(true);
                setPreset("custom");
              }}
              className={`px-3 py-1.5 rounded-[var(--radius-sm)] text-[12px] font-medium border ${
                preset === "custom"
                  ? "bg-[var(--accent-primary)] text-white border-[var(--accent-primary)]"
                  : "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-default)]"
              }`}
            >
              Date Range
            </button>
            <div className="flex items-center gap-2 ml-auto flex-wrap">
              <div className="flex flex-col gap-1">
                <label className="label text-[11px]">Start</label>
                <input
                  type="date"
                  className="input h-9 text-[12px]"
                  value={startDate}
                  onChange={(event) => {
                    setLoadingHistory(true);
                    setPreset("custom");
                    setStartDate(event.target.value);
                  }}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="label text-[11px]">End</label>
                <input
                  type="date"
                  className="input h-9 text-[12px]"
                  value={endDate}
                  onChange={(event) => {
                    setLoadingHistory(true);
                    setPreset("custom");
                    setEndDate(event.target.value);
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {activeTab === "history" && historySummary ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <History size={14} style={{ color: "var(--accent-primary)" }} />
              <span className="label">Trades</span>
            </div>
            <div className="data-value text-[24px]">{history.length}</div>
          </div>
          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <Wallet size={14} style={{ color: "var(--accent-primary)" }} />
              <span className="label">Total P&amp;L</span>
            </div>
            <div className={`data-value text-[24px] ${historySummary.total_pnl >= 0 ? "profit" : "loss"}`}>
              {formatCurrency(historySummary.total_pnl)}
            </div>
          </div>
          <div className="metric-card">
            <div className="flex items-center gap-2 mb-2">
              <CalendarDays size={14} style={{ color: "var(--accent-primary)" }} />
              <span className="label">Win Rate</span>
            </div>
            <div className={`data-value text-[24px] ${historySummary.win_rate >= 50 ? "profit" : "loss"}`}>
              {historySummary.win_rate.toFixed(2)}%
            </div>
          </div>
        </div>
      ) : null}

      <div className="card w-full">
        {activeTab === "open" ? (
          loadingOpen ? (
            <div className="p-8">
              <div className="skeleton h-48 w-full" />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th className="text-right">Entry</th>
                    <th className="text-right">Current</th>
                    <th className="text-right">Quantity</th>
                    <th className="text-right">Value</th>
                    <th className="text-right">P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {activePositions.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="text-center py-12 text-[var(--text-tertiary)]">
                        No live positions above 10 USDT.
                      </td>
                    </tr>
                  ) : (
                    activePositions.map((position) => (
                      <tr key={position.id}>
                        <td>
                          <div className="font-semibold">{position.symbol}</div>
                          <div className="flex items-center gap-2 mt-1">
                            <span className={`badge ${position.direction === "short" ? "bearish" : "bullish"}`}>
                              {position.direction}
                            </span>
                            <span className="caption">{position.market_type}</span>
                          </div>
                        </td>
                        <td className="numeric">{formatCurrency(position.entry_price)}</td>
                        <td className="numeric">{formatCurrency(position.current_price)}</td>
                        <td className="numeric text-[var(--text-secondary)]">{position.quantity?.toFixed(6)}</td>
                        <td className="numeric">{formatCurrency(position.current_value)}</td>
                        <td className="text-right">
                          <div className={`data-value ${position.profit_loss >= 0 ? "profit" : "loss"}`}>
                            {formatCurrency(position.profit_loss)}
                          </div>
                          <div className={`text-[12px] mt-0.5 ${position.profit_loss >= 0 ? "profit" : "loss"}`}>
                            {formatPercent(position.profit_loss_pct)}
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )
        ) : loadingHistory ? (
          <div className="p-8">
            <div className="skeleton h-48 w-full" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol &amp; Side</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">Holding</th>
                  <th className="text-right">Invested</th>
                  <th className="text-right">Realized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-center py-12 text-[var(--text-tertiary)]">
                      No trade history found for the selected range.
                    </td>
                  </tr>
                ) : (
                  history.map((trade) => (
                    <tr key={trade.id}>
                      <td className="text-[var(--text-secondary)]">
                        {trade.exit_at
                          ? new Date(trade.exit_at).toLocaleString("en", {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })
                          : "—"}
                      </td>
                      <td>
                        <div className="font-semibold">{trade.symbol}</div>
                        <div className="flex items-center gap-2 mt-1">
                          <span className={`badge ${trade.direction === "short" ? "bearish" : "bullish"}`}>
                            {trade.direction}
                          </span>
                          <span className="caption">{trade.market_type}</span>
                        </div>
                      </td>
                      <td className="numeric">{formatCurrency(trade.entry_price)}</td>
                      <td className="numeric">{trade.exit_price ? formatCurrency(trade.exit_price) : "—"}</td>
                      <td className="numeric text-[var(--text-secondary)]">{formatDuration(trade.holding_seconds)}</td>
                      <td className="numeric">{formatCurrency(trade.invested_value)}</td>
                      <td className="text-right">
                        <div className={`data-value ${(trade.profit_loss ?? 0) >= 0 ? "profit" : "loss"}`}>
                          {formatCurrency(trade.profit_loss)}
                        </div>
                        <div className={`text-[12px] mt-0.5 ${(trade.profit_loss_pct ?? 0) >= 0 ? "profit" : "loss"}`}>
                          {formatPercent(trade.profit_loss_pct)}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
