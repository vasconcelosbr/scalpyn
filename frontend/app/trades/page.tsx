"use client";

import { useState } from "react";
import { formatCurrency, formatPercent } from "@/lib/utils";

const MOCK_OPEN = [
  { id: "t1", symbol: "BTCUSDT", type: "Spot", side: "Long", entry: 64100.5, current: 64250.0, target: 65062.0, pl: 149.50, plPct: 0.23 },
  { id: "t2", symbol: "ETHUSDT", type: "Futures", side: "Short", entry: 3500.0, current: 3450.5, target: 3400.0, pl: 49.50, plPct: 1.41 },
];

const MOCK_HISTORY = [
  { id: "h1", date: "2026-03-10 14:30", symbol: "SOLUSDT", side: "Long", entry: 135.0, exit: 142.5, pl: 7.5, plPct: 5.55, time: "2h" },
  { id: "h2", date: "2026-03-09 10:15", symbol: "ADAUSDT", side: "Long", entry: 0.48, exit: 0.45, pl: -0.03, plPct: -6.25, time: "1d 4h" },
];

export default function TradesPage() {
  const [activeTab, setActiveTab] = useState("open");

  return (
    <div className="space-y-6 flex flex-col h-full w-full">
      <div className="flex justify-between items-center mb-2">
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Trades Review</h1>
      </div>

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
          <span className="bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-primary)] py-0.5 px-2 rounded-full text-[11px] font-mono leading-none">2</span>
          {activeTab === "open" && <span className="absolute bottom-[-1px] left-0 right-0 h-[2px] bg-[var(--accent-primary)]" />}
        </button>
      </div>

      <div className="card w-full">
        {activeTab === "open" && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol/Side</th>
                  <th className="text-right">Entry Price</th>
                  <th className="text-right">Current Price</th>
                  <th className="text-right">Target (TP)</th>
                  <th className="text-right">Unrealized P&L</th>
                  <th className="text-center w-[100px]">Actions</th>
                </tr>
              </thead>
              <tbody>
                {MOCK_OPEN.map((pos) => (
                  <tr key={pos.id}>
                    <td>
                      <div className="font-semibold">{pos.symbol}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`badge ${pos.side === 'Long' ? 'bullish' : 'bearish'}`}>
                          {pos.side}
                        </span>
                        <span className="caption">{pos.type}</span>
                      </div>
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">{formatCurrency(pos.entry)}</td>
                    <td className={`numeric ${pos.pl >= 0 ? 'price-up' : 'price-down'} text-[var(--text-primary)]`}>{formatCurrency(pos.current)}</td>
                    <td className="numeric text-[var(--accent-primary)]">{formatCurrency(pos.target)}</td>
                    <td className="text-right">
                      <div className={`data-value ${pos.pl >= 0 ? 'profit' : 'loss'}`}>
                        {formatCurrency(pos.pl)}
                      </div>
                      <div className={`percentage text-[12px] mt-0.5 ${pos.plPct >= 0 ? 'profit' : 'loss'}`}>
                        {formatPercent(pos.plPct)}
                      </div>
                    </td>
                    <td className="text-center">
                      <button className="btn btn-danger py-1.5 px-3">
                        Close
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {activeTab === "history" && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="sorted desc">Date</th>
                  <th>Symbol & Side</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">Time Held</th>
                  <th className="text-right">Realized P&L</th>
                </tr>
              </thead>
              <tbody>
                {MOCK_HISTORY.map((trade) => (
                  <tr key={trade.id}>
                    <td className="data-value text-[13px] text-[var(--text-secondary)]">{trade.date}</td>
                    <td>
                      <div className="font-semibold">{trade.symbol}</div>
                      <div className="flex items-center gap-1 mt-1">
                        <span className={`badge ${trade.side === 'Long' ? 'bullish' : 'bearish'}`}>{trade.side}</span>
                      </div>
                    </td>
                    <td className="numeric text-[var(--text-secondary)]">{formatCurrency(trade.entry)}</td>
                    <td className="numeric text-[var(--text-primary)]">{formatCurrency(trade.exit)}</td>
                    <td className="numeric text-[var(--text-secondary)] text-[12px]">{trade.time}</td>
                    <td className="text-right">
                      <div className={`data-value ${trade.pl >= 0 ? 'profit' : 'loss'}`}>
                        {trade.pl > 0 ? '+' : ''}{formatCurrency(trade.pl)}
                      </div>
                      <div className={`percentage text-[12px] mt-0.5 ${trade.plPct >= 0 ? 'profit' : 'loss'}`}>
                        {formatPercent(trade.plPct)}
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
