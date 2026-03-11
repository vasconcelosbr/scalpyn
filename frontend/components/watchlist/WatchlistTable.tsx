"use client";

// Add React import at the top implicitly handled by Next.js or explicit if needed
import React, { useState } from "react";
import { formatCurrency, formatPercent } from "@/lib/utils";

const MOCK_WATCHLIST = [
  { symbol: "BTCUSDT", price: 64250.0, change24h: 2.4, mcap: "1.2T", vol: "35B", trend: "Bullish", score: 85, scoreLevel: "excellent" },
  { symbol: "ETHUSDT", price: 3450.5, change24h: -1.2, mcap: "400B", vol: "15B", trend: "Range", score: 65, scoreLevel: "good" },
  { symbol: "SOLUSDT", price: 142.5, change24h: 5.6, mcap: "65B", vol: "5B", trend: "Bullish", score: 92, scoreLevel: "excellent" },
  { symbol: "ADAUSDT", price: 0.45, change24h: -3.4, mcap: "15B", vol: "800M", trend: "Bearish", score: 35, scoreLevel: "low" },
];

export function WatchlistTable() {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  const getTrendBadge = (trend: string) => {
    switch (trend) {
      case "Bullish": return <span className="badge bullish">Bullish</span>;
      case "Bearish": return <span className="badge bearish">Bearish</span>;
      default: return <span className="badge range">Range</span>;
    }
  };

  return (
    <div className="card">
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th className="sorted desc">Symbol</th>
              <th className="text-right">Live Price</th>
              <th className="text-right">24h %</th>
              <th className="text-right">Market Cap</th>
              <th className="text-right">Volume</th>
              <th>Trend</th>
              <th>Alpha Score</th>
            </tr>
          </thead>
          <tbody>
            {MOCK_WATCHLIST.map((coin) => (
              <React.Fragment key={coin.symbol}>
                <tr 
                  className={`expandable ${expandedRow === coin.symbol ? 'bg-[var(--bg-active)]' : ''}`}
                  onClick={() => setExpandedRow(expandedRow === coin.symbol ? null : coin.symbol)}
                >
                  <td className="font-semibold">{coin.symbol}</td>
                  <td className="numeric price">{formatCurrency(coin.price)}</td>
                  <td className={`numeric percentage ${coin.change24h >= 0 ? 'profit' : 'loss'}`}>
                    {formatPercent(coin.change24h)}
                  </td>
                  <td className="numeric text-[var(--text-secondary)]">{coin.mcap}</td>
                  <td className="numeric text-[var(--text-secondary)]">{coin.vol}</td>
                  <td>{getTrendBadge(coin.trend)}</td>
                  <td>
                    <div className="score-bar" data-level={coin.scoreLevel}>
                      <span className="score-label">{coin.score}</span>
                      <div className="bar-track">
                        <div className="bar-fill" style={{ width: `${coin.score}%` }} />
                      </div>
                    </div>
                  </td>
                </tr>
                
                {/* Expandable Meta Row */}
                <tr key={`${coin.symbol}-details`} className="bg-[var(--bg-hover)] border-none">
                  <td colSpan={7} className="p-0 border-none">
                    <div className={`overflow-hidden transition-all duration-300 ease-in-out ${expandedRow === coin.symbol ? 'max-h-[300px] opacity-100 p-4 border-b border-[var(--border-subtle)]' : 'max-h-0 opacity-0'}`}>
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                        <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                          <div className="label mb-1">RSI (14)</div>
                          <div className={`data-value text-[16px] ${coin.score >= 50 ? 'profit' : 'loss'}`}>64.2</div>
                        </div>
                        <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                          <div className="label mb-1">MACD</div>
                          <div className={`data-value text-[16px] profit`}>+12.4</div>
                        </div>
                        <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                          <div className="label mb-1">ADX (14)</div>
                          <div className="data-value text-[16px] text-white">28.5</div>
                        </div>
                        <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                          <div className="label mb-1">VWAP Diff</div>
                          <div className={`data-value text-[16px] ${coin.score >= 50 ? 'profit' : 'loss'}`}>+1.2%</div>
                        </div>
                        <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                          <div className="label mb-1">Vol / Avg</div>
                          <div className="data-value text-[16px] text-white">2.4x</div>
                        </div>
                      </div>
                    </div>
                  </td>
                </tr>
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
