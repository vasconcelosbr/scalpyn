"use client";

import React, { useState, useEffect, useRef } from "react";
import { Plus, Trash2, Edit2, Check, X, ChevronDown } from "lucide-react";
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

interface Watchlist {
  id: string;
  name: string;
  items: WatchlistItem[];
}

const STORAGE_KEY = "scalpyn_watchlists";
const ACTIVE_KEY = "scalpyn_active_watchlist";

const DEFAULT_WATCHLIST: Watchlist = {
  id: "default",
  name: "Minha Watchlist",
  items: [
    { symbol: "BTCUSDT", price: 64250.0, change24h: 2.4, mcap: "1.2T", vol: "35B", trend: "Bullish", score: 85, scoreLevel: "excellent" },
    { symbol: "ETHUSDT", price: 3450.5, change24h: -1.2, mcap: "400B", vol: "15B", trend: "Range", score: 65, scoreLevel: "good" },
    { symbol: "SOLUSDT", price: 142.5, change24h: 5.6, mcap: "65B", vol: "5B", trend: "Bullish", score: 92, scoreLevel: "excellent" },
    { symbol: "ADAUSDT", price: 0.45, change24h: -3.4, mcap: "15B", vol: "800M", trend: "Bearish", score: 35, scoreLevel: "low" },
  ],
};

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

function loadWatchlists(): Watchlist[] {
  if (typeof window === "undefined") return [DEFAULT_WATCHLIST];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Watchlist[];
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {
    // ignore parse errors
  }
  return [DEFAULT_WATCHLIST];
}

function saveWatchlists(lists: Watchlist[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(lists));
}

export function WatchlistTable() {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([DEFAULT_WATCHLIST]);
  const [activeId, setActiveId] = useState<string>("default");
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [showListDropdown, setShowListDropdown] = useState(false);
  const [creatingNew, setCreatingNew] = useState(false);
  const [newListName, setNewListName] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);
  const newNameRef = useRef<HTMLInputElement>(null);
  const [hydrated, setHydrated] = useState(false);

  // Load from localStorage after hydration
  useEffect(() => {
    const lists = loadWatchlists();
    const rawActive = localStorage.getItem(ACTIVE_KEY) || lists[0].id;
    const savedActive = lists.some((w) => w.id === rawActive) ? rawActive : lists[0].id;
    setWatchlists(lists);
    setActiveId(savedActive);
    setHydrated(true);
  }, []);

  // Persist to localStorage whenever watchlists change (after hydration)
  useEffect(() => {
    if (!hydrated) return;
    saveWatchlists(watchlists);
  }, [watchlists, hydrated]);

  // Persist active watchlist id
  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem(ACTIVE_KEY, activeId);
  }, [activeId, hydrated]);

  const activeWatchlist = watchlists.find((w) => w.id === activeId) ?? watchlists[0];
  const watchlist = activeWatchlist?.items ?? [];

  const updateActive = (updater: (items: WatchlistItem[]) => WatchlistItem[]) => {
    setWatchlists((prev) =>
      prev.map((wl) =>
        wl.id === activeWatchlist.id ? { ...wl, items: updater(wl.items) } : wl
      )
    );
  };

  const handleAddCoins = (coins: SpotCurrency[]) => {
    updateActive((items) => {
      const existingSymbols = new Set(items.map((i) => i.symbol));
      const newItems: WatchlistItem[] = coins
        .filter((c) => !existingSymbols.has(c.symbol))
        .map((coin) => {
          const change = coin.change_24h;
          const BASE_SCORE = 50;
          const CHANGE_MULTIPLIER = 3;
          const score = Math.max(0, Math.min(100, Math.round(BASE_SCORE + change * CHANGE_MULTIPLIER)));
          return {
            symbol: coin.symbol,
            price: coin.last_price,
            change24h: change,
            mcap: coin.market_cap_formatted ?? coin.volume_24h_formatted,
            vol: coin.volume_24h_formatted,
            trend: deriveTrend(change),
            score,
            scoreLevel: deriveScoreLevel(score),
          };
        });
      return [...items, ...newItems];
    });
    setShowModal(false);
  };

  const handleRemoveCoin = (symbol: string) => {
    updateActive((items) => items.filter((w) => w.symbol !== symbol));
    if (expandedRow === symbol) setExpandedRow(null);
  };

  const startEditName = () => {
    setNameInput(activeWatchlist.name);
    setEditingName(true);
    setTimeout(() => nameRef.current?.focus(), 50);
  };

  const confirmEditName = () => {
    const trimmed = nameInput.trim();
    if (trimmed) {
      setWatchlists((prev) =>
        prev.map((wl) => (wl.id === activeWatchlist.id ? { ...wl, name: trimmed } : wl))
      );
    }
    setEditingName(false);
  };

  const startCreateNew = () => {
    setNewListName("");
    setCreatingNew(true);
    setShowListDropdown(false);
    setTimeout(() => newNameRef.current?.focus(), 50);
  };

  const confirmCreateNew = () => {
    const trimmed = newListName.trim();
    if (!trimmed) return;
    const newId = crypto.randomUUID();
    const newList: Watchlist = { id: newId, name: trimmed, items: [] };
    setWatchlists((prev) => [...prev, newList]);
    setActiveId(newId);
    setCreatingNew(false);
  };

  const handleDeleteWatchlist = (id: string) => {
    if (watchlists.length <= 1) return;
    setWatchlists((prev) => {
      const updated = prev.filter((wl) => wl.id !== id);
      if (id === activeId) setActiveId(updated[0].id);
      return updated;
    });
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
          onAdd={handleAddCoins}
          existingSymbols={watchlist.map((w) => w.symbol)}
        />
      )}

      <div className="card">
        {/* Watchlist selector + header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-subtle)] gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {/* Watchlist name / edit */}
            {editingName ? (
              <div className="flex items-center gap-1">
                <input
                  ref={nameRef}
                  type="text"
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") confirmEditName();
                    if (e.key === "Escape") setEditingName(false);
                  }}
                  className="input text-[13px] font-semibold py-0.5 px-2 h-7 w-44"
                />
                <button className="btn-icon w-6 h-6 flex items-center justify-center" onClick={confirmEditName}>
                  <Check className="w-3.5 h-3.5 text-[var(--color-profit)]" />
                </button>
                <button className="btn-icon w-6 h-6 flex items-center justify-center" onClick={() => setEditingName(false)}>
                  <X className="w-3.5 h-3.5 text-[var(--color-loss)]" />
                </button>
              </div>
            ) : (
              <div className="relative flex items-center gap-1">
                <button
                  className="flex items-center gap-1 text-[13px] font-semibold text-[var(--text-primary)] hover:text-[var(--accent-primary)] transition-colors"
                  onClick={() => setShowListDropdown((v) => !v)}
                >
                  {activeWatchlist?.name ?? "Watchlist"}
                  <ChevronDown className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                </button>
                <button
                  className="btn-icon w-6 h-6 flex items-center justify-center opacity-60 hover:opacity-100"
                  title="Renomear watchlist"
                  onClick={startEditName}
                >
                  <Edit2 className="w-3 h-3" />
                </button>

                {/* Dropdown */}
                {showListDropdown && (
                  <div className="absolute top-full left-0 mt-1 w-56 bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-md)] shadow-2xl z-50 py-1">
                    {watchlists.map((wl) => (
                      <div
                        key={wl.id}
                        className={`flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] ${wl.id === activeId ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                        onClick={() => { setActiveId(wl.id); setShowListDropdown(false); }}
                      >
                        <span className="text-[13px] truncate flex-1">{wl.name}</span>
                        {watchlists.length > 1 && (
                          <button
                            className="btn-icon w-5 h-5 flex items-center justify-center ml-1 opacity-50 hover:opacity-100 hover:text-[var(--color-loss)]"
                            onClick={(e) => { e.stopPropagation(); handleDeleteWatchlist(wl.id); }}
                            title="Excluir watchlist"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        )}
                      </div>
                    ))}
                    <div className="border-t border-[var(--border-subtle)] mt-1 pt-1">
                      <button
                        className="flex items-center gap-2 w-full px-3 py-2 text-[13px] text-[var(--accent-primary)] hover:bg-[var(--bg-hover)]"
                        onClick={startCreateNew}
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Nova Watchlist
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Create new watchlist inline */}
            {creatingNew && (
              <div className="flex items-center gap-1 ml-2">
                <input
                  ref={newNameRef}
                  type="text"
                  value={newListName}
                  placeholder="Nome da watchlist"
                  onChange={(e) => setNewListName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") confirmCreateNew();
                    if (e.key === "Escape") setCreatingNew(false);
                  }}
                  className="input text-[13px] py-0.5 px-2 h-7 w-44"
                />
                <button className="btn-icon w-6 h-6 flex items-center justify-center" onClick={confirmCreateNew}>
                  <Check className="w-3.5 h-3.5 text-[var(--color-profit)]" />
                </button>
                <button className="btn-icon w-6 h-6 flex items-center justify-center" onClick={() => setCreatingNew(false)}>
                  <X className="w-3.5 h-3.5 text-[var(--color-loss)]" />
                </button>
              </div>
            )}

            <span className="text-[12px] text-[var(--text-tertiary)]">
              {watchlist.length} ativo{watchlist.length !== 1 ? "s" : ""}
            </span>
          </div>

          <button
            className="btn btn-primary text-[13px] px-4 py-2 shrink-0"
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
