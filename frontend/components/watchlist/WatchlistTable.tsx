"use client";

import React, { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { AddCoinModal, SpotCurrency } from "./AddCoinModal";

interface WatchlistItem {
  symbol: string;
  price: number;
  change24h: number;
  mcap: string;
  vol: string;
  trend: string;
  score: number;
  scoreLevel: string;
}

const INITIAL_WATCHLIST: WatchlistItem[] = [
  { symbol: "BTCUSDT", price: 64250.0, change24h: 2.4, mcap: "1.2T", vol: "35B", trend: "Bullish", score: 85, scoreLevel: "excellent" },
  { symbol: "ETHUSDT", price: 3450.5, change24h: -1.2, mcap: "400B", vol: "15B", trend: "Range", score: 65, scoreLevel: "good" },
  { symbol: "SOLUSDT", price: 142.5, change24h: 5.6, mcap: "65B", vol: "5B", trend: "Bullish", score: 92, scoreLevel: "excellent" },
  { symbol: "ADAUSDT", price: 0.45, change24h: -3.4, mcap: "15B", vol: "800M", trend: "Bearish", score: 35, scoreLevel: "low" },
];

function deriveTrend(change24h: number): string {
  if (change24h >= 2) return "Bullish";
  if (change24h <= -2) return "Bearish";
  return "Range";
}

function deriveScoreLevel(score: number): string {
  if (score >= 80) return "excellent";
  if (score >= 60) return "good";
  if (score >= 40) return "neutral";
  return "low";
}

export function WatchlistTable() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>(INITIAL_WATCHLIST);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  const handleAddCoin = (coin: SpotCurrency) => {
    if (watchlist.some((w) => w.symbol === coin.symbol)) return;
    const change = coin.change_24h;
    const BASE_SCORE = 50;
    const CHANGE_MULTIPLIER = 3;
    const score = Math.max(0, Math.min(100, Math.round(BASE_SCORE + change * CHANGE_MULTIPLIER)));
    const newItem: WatchlistItem = {
      symbol: coin.symbol,
      price: coin.last_price,
      change24h: change,
      mcap: coin.volume_24h_formatted,
      vol: coin.volume_24h_formatted,
      trend: deriveTrend(change),
      score,
      scoreLevel: deriveScoreLevel(score),
    };
    setWatchlist((prev) => [...prev, newItem]);
    setShowModal(false);
  };

  const handleRemoveCoin = (symbol: string) => {
    setWatchlist((prev) => prev.filter((w) => w.symbol !== symbol));
    if (expandedRow === symbol) setExpandedRow(null);
  };

  const getTrendBadge = (trend: string) => {
    switch (trend) {
      case "Bullish": return <span className="badge bullish">Bullish</span>;
      case "Bearish": return <span className="badge bearish">Bearish</span>;
      default: return <span className="badge range">Range</span>;
    }
  };

  return (
    <>
      {showModal && (
        <AddCoinModal
          onClose={() => setShowModal(false)}
          onAdd={handleAddCoin}
          existingSymbols={watchlist.map((w) => w.symbol)}
        />
      )}

      <div className="card">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-subtle)]">
          <span className="text-[13px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
            {watchlist.length} ativo{watchlist.length !== 1 ? "s" : ""}
          </span>
          <button
            className="btn btn-primary text-[13px] px-4 py-2"
            onClick={() => setShowModal(true)}
          >
            <Plus className="w-4 h-4 mr-1.5" />
            Adicionar Cripto
          </button>
        </div>

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
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {watchlist.map((coin) => (
                <React.Fragment key={coin.symbol}>
                  <tr
                    className={`expandable ${expandedRow === coin.symbol ? "bg-[var(--bg-active)]" : ""}`}
                    onClick={() => setExpandedRow(expandedRow === coin.symbol ? null : coin.symbol)}
                  >
                    <td className="font-semibold">{coin.symbol}</td>
                    <td className="numeric price">{formatCurrency(coin.price)}</td>
                    <td className={`numeric percentage ${coin.change24h >= 0 ? "profit" : "loss"}`}>
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
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        className="btn-icon w-7 h-7 flex items-center justify-center hover:bg-[var(--color-loss-muted)] hover:text-[var(--color-loss)] hover:border-[var(--color-loss-border)]"
                        title="Remover da Watchlist"
                        onClick={() => handleRemoveCoin(coin.symbol)}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>

                  {/* Expandable Meta Row */}
                  <tr key={`${coin.symbol}-details`} className="bg-[var(--bg-hover)] border-none">
                    <td colSpan={8} className="p-0 border-none">
                      <div className={`overflow-hidden transition-all duration-300 ease-in-out ${expandedRow === coin.symbol ? "max-h-[300px] opacity-100 p-4 border-b border-[var(--border-subtle)]" : "max-h-0 opacity-0"}`}>
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                          <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                            <div className="label mb-1">RSI (14)</div>
                            <div className={`data-value text-[16px] ${coin.score >= 50 ? "profit" : "loss"}`}>64.2</div>
                          </div>
                          <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                            <div className="label mb-1">MACD</div>
                            <div className="data-value text-[16px] profit">+12.4</div>
                          </div>
                          <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                            <div className="label mb-1">ADX (14)</div>
                            <div className="data-value text-[16px] text-white">28.5</div>
                          </div>
                          <div className="bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-[var(--radius-md)] p-3">
                            <div className="label mb-1">VWAP Diff</div>
                            <div className={`data-value text-[16px] ${coin.score >= 50 ? "profit" : "loss"}`}>+1.2%</div>
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

              {watchlist.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                    Sua watchlist está vazia. Clique em <strong>Adicionar Cripto</strong> para começar.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
