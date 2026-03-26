"use client";

import React, { useState, useEffect, useRef } from "react";
import { Plus, Trash2, Edit2, Check, X, ChevronDown, RefreshCw, TrendingUp, Zap, List, Sliders } from "lucide-react";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { AddCoinModal, SpotCurrency } from "./AddCoinModal";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";

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

interface RankedAsset {
  symbol: string;
  name: string;
  price: number;
  change_24h: number;
  market_cap?: number | null;
  score: number;
  rating: string;
  score_breakdown: {
    liquidity: number;
    market_structure: number;
    momentum: number;
    signal: number;
  };
}

interface Signal {
  symbol: string;
  name: string;
  price: number;
  change_24h: number;
  market_cap?: number | null;
  action: string;
  score: number;
  confidence: number;
  rating: string;
  matched_conditions: string[];
}

interface Watchlist {
  id: string;
  name: string;
  symbols: string[];
  symbol_count: number;
}

interface Profile {
  id: string;
  name: string;
  description?: string;
  config?: any;
}

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
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [watchlistItems, setWatchlistItems] = useState<WatchlistItem[]>([]);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [showListDropdown, setShowListDropdown] = useState(false);
  const [creatingNew, setCreatingNew] = useState(false);
  const [newListName, setNewListName] = useState("");
  const [loading, setLoading] = useState(true);
  const [scoreSort, setScoreSort] = useState<"asc" | "desc" | null>("desc");
  const nameRef = useRef<HTMLInputElement>(null);
  const newNameRef = useRef<HTMLInputElement>(null);
  
  // L1/L2/L3 Tabs
  const [activeTab, setActiveTab] = useState<"L1" | "L2" | "L3">("L1");
  const [rankedAssets, setRankedAssets] = useState<RankedAsset[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [rankingLoading, setRankingLoading] = useState(false);
  const [signalsLoading, setSignalsLoading] = useState(false);
  const [l1Loading, setL1Loading] = useState(false);
  
  // Profile assignment - separate for L1, L2 and L3
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [l1Profile, setL1Profile] = useState<Profile | null>(null);
  const [l2Profile, setL2Profile] = useState<Profile | null>(null);
  const [l3Profile, setL3Profile] = useState<Profile | null>(null);
  const [showL1ProfileSelector, setShowL1ProfileSelector] = useState(false);
  const [showL2ProfileSelector, setShowL2ProfileSelector] = useState(false);
  const [showL3ProfileSelector, setShowL3ProfileSelector] = useState(false);

  // Load watchlists from backend on mount
  useEffect(() => {
    loadWatchlists();
  }, []);

  // Load watchlist items when activeId changes
  useEffect(() => {
    if (activeId) {
      loadWatchlistItems(activeId);
    }
  }, [activeId]);

  const loadWatchlists = async () => {
    setLoading(true);
    try {
      const data = await apiGet("/custom-watchlists");
      const lists = data.watchlists || [];
      setWatchlists(lists);
      
      // Set active to first watchlist if available
      if (lists.length > 0 && !activeId) {
        setActiveId(lists[0].id);
      } else if (lists.length === 0) {
        // Create default watchlist if none exist
        await createWatchlist("Minha Watchlist");
      }
    } catch (e) {
      console.error("Failed to load watchlists:", e);
    }
    setLoading(false);
  };

  const loadWatchlistItems = async (watchlistId: string) => {
    try {
      const watchlist = watchlists.find(w => w.id === watchlistId);
      if (!watchlist) return;

      // Get market data for symbols
      const marketData = await apiGet("/watchlist");
      const marketMap = new Map(
        (marketData.watchlist || []).map((m: any) => [m.symbol, m])
      );

      // Build items from watchlist symbols + market data
      const items: WatchlistItem[] = watchlist.symbols.map(symbol => {
        const market = marketMap.get(symbol) as any;
        if (market) {
          const change = market.change_24h || 0;
          return {
            symbol,
            price: market.price || 0,
            change24h: change,
            mcap: market.market_cap_formatted || formatLargeNumber(market.market_cap),
            vol: market.volume_24h_formatted || formatLargeNumber(market.volume_24h),
            trend: deriveTrend(change),
            score: market.score || 50,
            scoreLevel: deriveScoreLevel(market.score || 50),
          };
        }
        return {
          symbol,
          price: 0,
          change24h: 0,
          mcap: "-",
          vol: "-",
          trend: "Range",
          score: 50,
          scoreLevel: "neutral",
        };
      });

      setWatchlistItems(items);
    } catch (e) {
      console.error("Failed to load watchlist items:", e);
    }
  };

  const createWatchlist = async (name: string) => {
    try {
      const data = await apiPost("/custom-watchlists", { name, symbols: [] });
      setWatchlists(prev => [...prev, data]);
      setActiveId(data.id);
      return data;
    } catch (e) {
      console.error("Failed to create watchlist:", e);
    }
  };

  const updateWatchlistName = async (id: string, name: string) => {
    try {
      await apiPut(`/custom-watchlists/${id}`, { name });
      setWatchlists(prev => prev.map(w => w.id === id ? { ...w, name } : w));
    } catch (e) {
      console.error("Failed to update watchlist:", e);
    }
  };

  const deleteWatchlist = async (id: string) => {
    if (watchlists.length <= 1) return;
    try {
      await apiDelete(`/custom-watchlists/${id}`);
      setWatchlists(prev => {
        const updated = prev.filter(w => w.id !== id);
        if (id === activeId && updated.length > 0) {
          setActiveId(updated[0].id);
        }
        return updated;
      });
    } catch (e) {
      console.error("Failed to delete watchlist:", e);
    }
  };

  const addSymbolsToWatchlist = async (symbols: string[]) => {
    if (!activeId) return;
    try {
      const data = await apiPost(`/custom-watchlists/${activeId}/symbols`, { symbols });
      setWatchlists(prev => prev.map(w => w.id === activeId ? { ...w, symbols: data.symbols, symbol_count: data.symbol_count } : w));
      await loadWatchlistItems(activeId);
    } catch (e) {
      console.error("Failed to add symbols:", e);
    }
  };

  const removeSymbolFromWatchlist = async (symbol: string) => {
    if (!activeId) return;
    try {
      await apiDelete(`/custom-watchlists/${activeId}/symbols/${symbol}`);
      setWatchlists(prev => prev.map(w => {
        if (w.id === activeId) {
          const newSymbols = w.symbols.filter(s => s !== symbol);
          return { ...w, symbols: newSymbols, symbol_count: newSymbols.length };
        }
        return w;
      }));
      setWatchlistItems(prev => prev.filter(item => item.symbol !== symbol));
    } catch (e) {
      console.error("Failed to remove symbol:", e);
    }
  };

  // L1/L2/L3 Functions
  const loadProfiles = async () => {
    try {
      const data = await apiGet("/profiles");
      setProfiles(data.profiles || []);
    } catch (e) {
      console.error("Failed to load profiles:", e);
    }
  };

  const loadAssignedProfiles = async (watchlistId: string) => {
    try {
      const data = await apiGet(`/custom-watchlists/${watchlistId}/profiles`);
      // Find profile details from profiles list
      if (data.L1) {
        const l1 = profiles.find(p => p.id === data.L1.id) || { id: data.L1.id, name: data.L1.name };
        setL1Profile(l1);
      } else {
        setL1Profile(null);
      }
      if (data.L2) {
        const l2 = profiles.find(p => p.id === data.L2.id) || { id: data.L2.id, name: data.L2.name };
        setL2Profile(l2);
      } else {
        setL2Profile(null);
      }
      if (data.L3) {
        const l3 = profiles.find(p => p.id === data.L3.id) || { id: data.L3.id, name: data.L3.name };
        setL3Profile(l3);
      } else {
        setL3Profile(null);
      }
    } catch (e) {
      setL1Profile(null);
      setL2Profile(null);
      setL3Profile(null);
    }
  };

  const assignL1Profile = async (profileId: string | null) => {
    if (!activeId) return;
    try {
      await apiPut(`/custom-watchlists/${activeId}/profile/L1`, { profile_id: profileId });
      if (profileId) {
        const profile = profiles.find(p => p.id === profileId);
        setL1Profile(profile || null);
      } else {
        setL1Profile(null);
      }
      setShowL1ProfileSelector(false);
      loadL1Filtered();
    } catch (e) {
      console.error("Failed to assign L1 profile:", e);
    }
  };

  const assignL2Profile = async (profileId: string | null) => {
    if (!activeId) return;
    try {
      await apiPut(`/custom-watchlists/${activeId}/profile/L2`, { profile_id: profileId });
      if (profileId) {
        const profile = profiles.find(p => p.id === profileId);
        setL2Profile(profile || null);
      } else {
        setL2Profile(null);
      }
      setShowL2ProfileSelector(false);
      loadRanking();
    } catch (e) {
      console.error("Failed to assign L2 profile:", e);
    }
  };

  const assignL3Profile = async (profileId: string | null) => {
    if (!activeId) return;
    try {
      await apiPut(`/custom-watchlists/${activeId}/profile/L3`, { profile_id: profileId });
      if (profileId) {
        const profile = profiles.find(p => p.id === profileId);
        setL3Profile(profile || null);
      } else {
        setL3Profile(null);
      }
      setShowL3ProfileSelector(false);
      loadSignals();
    } catch (e) {
      console.error("Failed to assign L3 profile:", e);
    }
  };

  const loadRanking = async () => {
    if (!activeId) return;
    setRankingLoading(true);
    try {
      const data = await apiGet(`/custom-watchlists/${activeId}/ranking?top_n=50`);
      setRankedAssets(data.assets || []);
      // Update L2 profile from response
      if (data.profile_id) {
        const profile = profiles.find(p => p.id === data.profile_id);
        if (profile) setL2Profile(profile);
      }
    } catch (e) {
      console.error("Failed to load ranking:", e);
      setRankedAssets([]);
    }
    setRankingLoading(false);
  };

  const loadSignals = async () => {
    if (!activeId) return;
    setSignalsLoading(true);
    try {
      const data = await apiGet(`/custom-watchlists/${activeId}/signals`);
      setSignals(data.signals || []);
      // Update L3 profile from response
      if (data.profile_id) {
        const profile = profiles.find(p => p.id === data.profile_id);
        if (profile) setL3Profile(profile);
      }
    } catch (e) {
      console.error("Failed to load signals:", e);
      setSignals([]);
    }
    setSignalsLoading(false);
  };

  const loadL1Filtered = async () => {
    if (!activeId) return;
    setL1Loading(true);
    try {
      const data = await apiGet(`/custom-watchlists/${activeId}/filtered`);
      // Transform to WatchlistItem format
      const items: WatchlistItem[] = (data.assets || []).map((a: any) => ({
        symbol: a.symbol,
        price: a.price || 0,
        change24h: a.change_24h || 0,
        mcap: formatLargeNumber(a.market_cap),
        vol: formatLargeNumber(a.volume_24h),
        trend: a.trend || "Range",
        score: a.score || 0,
        scoreLevel: a.score_level || "neutral"
      }));
      setWatchlistItems(items);
      // Update L1 profile from response
      if (data.profile_id) {
        const profile = profiles.find(p => p.id === data.profile_id);
        if (profile) setL1Profile(profile);
      }
    } catch (e) {
      console.error("Failed to load L1 filtered:", e);
    }
    setL1Loading(false);
  };

  // Load profiles and assigned profiles when activeId changes
  useEffect(() => {
    if (activeId) {
      loadProfiles().then(() => {
        loadAssignedProfiles(activeId);
      });
    }
  }, [activeId]);

  // Load data when tab or activeId changes
  useEffect(() => {
    if (!activeId) return;
    if (activeTab === "L1") {
      // Load L1 filtered if profile is assigned, otherwise load raw
      if (l1Profile) {
        loadL1Filtered();
      } else {
        loadWatchlistItems(activeId);
      }
    } else if (activeTab === "L2") {
      loadRanking();
    } else if (activeTab === "L3") {
      loadSignals();
    }
  }, [activeTab, activeId, l1Profile?.id]);

  const activeWatchlist = watchlists.find(w => w.id === activeId);
  
  const sortedItems = scoreSort
    ? [...watchlistItems].sort((a, b) =>
        scoreSort === "desc" ? b.score - a.score : a.score - b.score
      )
    : watchlistItems;

  const cycleScoreSort = () => {
    setScoreSort(prev => (prev === "desc" ? "asc" : "desc"));
  };

  const handleAddCoins = (coins: SpotCurrency[]) => {
    const symbols = coins.map(c => c.symbol);
    addSymbolsToWatchlist(symbols);
    setShowModal(false);
  };

  const startEditName = () => {
    if (!activeWatchlist) return;
    setNameInput(activeWatchlist.name);
    setEditingName(true);
    setTimeout(() => nameRef.current?.focus(), 50);
  };

  const confirmEditName = () => {
    const trimmed = nameInput.trim();
    if (trimmed && activeId) {
      updateWatchlistName(activeId, trimmed);
    }
    setEditingName(false);
  };

  const startCreateNew = () => {
    setNewListName("");
    setCreatingNew(true);
    setShowListDropdown(false);
    setTimeout(() => newNameRef.current?.focus(), 50);
  };

  const confirmCreateNew = async () => {
    const trimmed = newListName.trim();
    if (!trimmed) return;
    await createWatchlist(trimmed);
    setCreatingNew(false);
  };

  const getTrendBadge = (trend: string) => {
    switch (trend) {
      case "Bullish": return <span className="badge bullish">Bullish</span>;
      case "Bearish": return <span className="badge bearish">Bearish</span>;
      default: return <span className="badge range">Range</span>;
    }
  };

  if (loading) {
    return (
      <div className="card">
        <div className="card-body p-8 text-center">
          <RefreshCw className="w-6 h-6 animate-spin mx-auto text-[var(--text-tertiary)]" />
          <p className="mt-2 text-[var(--text-secondary)]">Loading watchlists...</p>
        </div>
      </div>
    );
  }

  return (
    <>
      {showModal && (
        <AddCoinModal
          onClose={() => setShowModal(false)}
          onAdd={handleAddCoins}
          existingSymbols={watchlistItems.map(w => w.symbol)}
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
                  onClick={() => setShowListDropdown(v => !v)}
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
                    {watchlists.map(wl => (
                      <div
                        key={wl.id}
                        className={`flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] ${wl.id === activeId ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                        onClick={() => { setActiveId(wl.id); setShowListDropdown(false); }}
                      >
                        <span className="text-[13px] truncate flex-1">{wl.name}</span>
                        <span className="text-[11px] text-[var(--text-tertiary)] mr-2">{wl.symbol_count}</span>
                        {watchlists.length > 1 && (
                          <button
                            className="btn-icon w-5 h-5 flex items-center justify-center ml-1 opacity-50 hover:opacity-100 hover:text-[var(--color-loss)]"
                            onClick={(e) => { e.stopPropagation(); deleteWatchlist(wl.id); }}
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
              {sortedItems.length} ativo{sortedItems.length !== 1 ? "s" : ""}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              className="btn btn-primary text-[13px] px-4 py-2 shrink-0"
              onClick={() => setShowModal(true)}
            >
              <Plus className="w-4 h-4 mr-1.5" />
              Adicionar Cripto
            </button>
          </div>
        </div>

        {/* L1/L2/L3 Tabs */}
        <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4">
          <div className="flex">
            <button
              className={`flex items-center gap-1.5 px-4 py-2.5 text-[13px] font-medium transition-colors ${
                activeTab === "L1"
                  ? "text-[var(--accent-primary)] border-b-2 border-[var(--accent-primary)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              }`}
              onClick={() => setActiveTab("L1")}
            >
              <List className="w-3.5 h-3.5" />
              L1 Assets
              {l1Profile && <span className="text-[10px] opacity-70">• {l1Profile.name}</span>}
            </button>
            <button
              className={`flex items-center gap-1.5 px-4 py-2.5 text-[13px] font-medium transition-colors ${
                activeTab === "L2"
                  ? "text-[var(--accent-primary)] border-b-2 border-[var(--accent-primary)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              }`}
              onClick={() => setActiveTab("L2")}
            >
              <TrendingUp className="w-3.5 h-3.5" />
              L2 Ranking
              {l2Profile && <span className="text-[10px] opacity-70">• {l2Profile.name}</span>}
            </button>
            <button
              className={`flex items-center gap-1.5 px-4 py-2.5 text-[13px] font-medium transition-colors ${
                activeTab === "L3"
                  ? "text-[var(--accent-primary)] border-b-2 border-[var(--accent-primary)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              }`}
              onClick={() => setActiveTab("L3")}
            >
              <Zap className="w-3.5 h-3.5" />
              L3 Signals
              {l3Profile && <span className="text-[10px] opacity-70">• {l3Profile.name}</span>}
            </button>
          </div>
          
          {/* Profile selector for current tab */}
          {activeTab === "L1" && (
            <div className="relative">
              <button
                className="btn btn-secondary text-[12px] px-3 py-1.5 flex items-center gap-1"
                onClick={() => setShowL1ProfileSelector(!showL1ProfileSelector)}
              >
                <Sliders className="w-3.5 h-3.5" />
                {l1Profile ? l1Profile.name : "Select L1 Profile"}
              </button>
              
              {showL1ProfileSelector && (
                <div className="absolute right-0 top-full mt-1 w-56 bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-md)] shadow-2xl z-50 py-1">
                  <div
                    className={`px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] text-[13px] ${!l1Profile ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                    onClick={() => assignL1Profile(null)}
                  >
                    No Profile (All Assets)
                  </div>
                  {profiles.map(p => (
                    <div
                      key={p.id}
                      className={`px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] text-[13px] ${l1Profile?.id === p.id ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                      onClick={() => assignL1Profile(p.id)}
                    >
                      {p.name}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          
          {activeTab === "L2" && (
            <div className="relative">
              <button
                className="btn btn-secondary text-[12px] px-3 py-1.5 flex items-center gap-1"
                onClick={() => setShowL2ProfileSelector(!showL2ProfileSelector)}
              >
                <Sliders className="w-3.5 h-3.5" />
                {l2Profile ? l2Profile.name : "Select L2 Profile"}
              </button>
              
              {showL2ProfileSelector && (
                <div className="absolute right-0 top-full mt-1 w-56 bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-md)] shadow-2xl z-50 py-1">
                  <div
                    className={`px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] text-[13px] ${!l2Profile ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                    onClick={() => assignL2Profile(null)}
                  >
                    No Profile (Default)
                  </div>
                  {profiles.map(p => (
                    <div
                      key={p.id}
                      className={`px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] text-[13px] ${l2Profile?.id === p.id ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                      onClick={() => assignL2Profile(p.id)}
                    >
                      {p.name}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          
          {activeTab === "L3" && (
            <div className="relative">
              <button
                className="btn btn-secondary text-[12px] px-3 py-1.5 flex items-center gap-1"
                onClick={() => setShowL3ProfileSelector(!showL3ProfileSelector)}
              >
                <Sliders className="w-3.5 h-3.5" />
                {l3Profile ? l3Profile.name : "Select L3 Profile"}
              </button>
              
              {showL3ProfileSelector && (
                <div className="absolute right-0 top-full mt-1 w-56 bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-md)] shadow-2xl z-50 py-1">
                  <div
                    className={`px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] text-[13px] ${!l3Profile ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                    onClick={() => assignL3Profile(null)}
                  >
                    No Profile (Default)
                  </div>
                  {profiles.map(p => (
                    <div
                      key={p.id}
                      className={`px-3 py-2 cursor-pointer hover:bg-[var(--bg-hover)] text-[13px] ${l3Profile?.id === p.id ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}
                      onClick={() => assignL3Profile(p.id)}
                    >
                      {p.name}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* L1 - Raw Assets */}
        {activeTab === "L1" && (
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
                <th>
                  <button
                    onClick={cycleScoreSort}
                    className="flex items-center gap-1 font-semibold hover:text-[var(--accent-primary)] transition-colors"
                  >
                    Alpha Score
                    <span className="text-[10px] opacity-70">
                      {scoreSort === "desc" ? "▼" : "▲"}
                    </span>
                  </button>
                </th>
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map(coin => (
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
                        onClick={() => removeSymbolFromWatchlist(coin.symbol)}
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

              {sortedItems.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                    Sua watchlist está vazia. Clique em <strong>Adicionar Cripto</strong> para começar.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        )}

        {/* L2 - Ranking */}
        {activeTab === "L2" && (
          <div className="overflow-x-auto">
            {rankingLoading ? (
              <div className="p-8 text-center">
                <RefreshCw className="w-6 h-6 animate-spin mx-auto text-[var(--text-tertiary)]" />
                <p className="mt-2 text-[var(--text-secondary)] text-[13px]">Computing ranking...</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="w-12">#</th>
                    <th>Symbol</th>
                    <th className="text-right">Price</th>
                    <th className="text-right">24h %</th>
                    <th className="text-right">Market Cap</th>
                    <th>
                      <span className="flex items-center gap-1">
                        Alpha Score
                        <span className="text-[10px] opacity-70">▼</span>
                      </span>
                    </th>
                    <th>Rating</th>
                    <th>Score Breakdown</th>
                  </tr>
                </thead>
                <tbody>
                  {rankedAssets.map((asset, idx) => (
                    <tr key={asset.symbol}>
                      <td className="text-[var(--text-tertiary)] font-mono">{idx + 1}</td>
                      <td className="font-semibold">{asset.symbol}</td>
                      <td className="numeric price">{formatCurrency(asset.price)}</td>
                      <td className={`numeric percentage ${asset.change_24h >= 0 ? "profit" : "loss"}`}>
                        {formatPercent(asset.change_24h)}
                      </td>
                      <td className="numeric text-[var(--text-secondary)]">{formatLargeNumber(asset.market_cap)}</td>
                      <td>
                        <div className="score-bar" data-level={deriveScoreLevel(asset.score)}>
                          <span className="score-label">{asset.score.toFixed(1)}</span>
                          <div className="bar-track">
                            <div className="bar-fill" style={{ width: `${asset.score}%` }} />
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${
                          asset.rating === "STRONG_BUY" ? "bullish" :
                          asset.rating === "BUY" ? "bullish" :
                          asset.rating === "NEUTRAL" ? "range" : "bearish"
                        }`}>
                          {asset.rating}
                        </span>
                      </td>
                      <td>
                        <div className="flex gap-1 text-[10px]">
                          <span className="px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
                            L:{asset.score_breakdown.liquidity.toFixed(0)}
                          </span>
                          <span className="px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400">
                            MS:{asset.score_breakdown.market_structure.toFixed(0)}
                          </span>
                          <span className="px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">
                            Mo:{asset.score_breakdown.momentum.toFixed(0)}
                          </span>
                          <span className="px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400">
                            S:{asset.score_breakdown.signal.toFixed(0)}
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {rankedAssets.length === 0 && (
                    <tr>
                      <td colSpan={8} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                        {l2Profile 
                          ? "No assets passed the L1 filters. Try adjusting your profile."
                          : "Select a L2 profile to generate ranking."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* L3 - Signals */}
        {activeTab === "L3" && (
          <div className="overflow-x-auto">
            {signalsLoading ? (
              <div className="p-8 text-center">
                <RefreshCw className="w-6 h-6 animate-spin mx-auto text-[var(--text-tertiary)]" />
                <p className="mt-2 text-[var(--text-secondary)] text-[13px]">Evaluating signals...</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th className="text-right">Price</th>
                    <th className="text-right">24h %</th>
                    <th className="text-right">Market Cap</th>
                    <th>Action</th>
                    <th>Score</th>
                    <th>Confidence</th>
                    <th>Matched Conditions</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map(signal => (
                    <tr key={signal.symbol}>
                      <td className="font-semibold">{signal.symbol}</td>
                      <td className="numeric price">{formatCurrency(signal.price)}</td>
                      <td className={`numeric percentage ${signal.change_24h >= 0 ? "profit" : "loss"}`}>
                        {formatPercent(signal.change_24h)}
                      </td>
                      <td className="numeric text-[var(--text-secondary)]">{formatLargeNumber(signal.market_cap)}</td>
                      <td>
                        <span className={`badge ${
                          signal.action === "LONG" ? "bullish" :
                          signal.action === "SHORT" ? "bearish" : "range"
                        }`}>
                          {signal.action}
                        </span>
                      </td>
                      <td>
                        <div className="score-bar" data-level={deriveScoreLevel(signal.score)}>
                          <span className="score-label">{signal.score.toFixed(1)}</span>
                          <div className="bar-track">
                            <div className="bar-fill" style={{ width: `${signal.score}%` }} />
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="flex items-center gap-1">
                          <div className="w-16 h-1.5 rounded-full bg-[var(--bg-secondary)] overflow-hidden">
                            <div 
                              className="h-full bg-[var(--accent-primary)] rounded-full"
                              style={{ width: `${signal.confidence * 100}%` }}
                            />
                          </div>
                          <span className="text-[11px] text-[var(--text-secondary)]">
                            {(signal.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {signal.matched_conditions.slice(0, 3).map((cond, i) => (
                            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]">
                              {cond}
                            </span>
                          ))}
                          {signal.matched_conditions.length > 3 && (
                            <span className="text-[10px] text-[var(--text-tertiary)]">
                              +{signal.matched_conditions.length - 3}
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {signals.length === 0 && (
                    <tr>
                      <td colSpan={8} className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">
                        {l3Profile 
                          ? "No signals triggered. Conditions not met or no assets passed filters."
                          : "Select a L3 profile to generate signals."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function formatLargeNumber(num: number | null | undefined): string {
  if (!num) return "-";
  if (num >= 1e12) return `${(num / 1e12).toFixed(1)}T`;
  if (num >= 1e9) return `${(num / 1e9).toFixed(1)}B`;
  if (num >= 1e6) return `${(num / 1e6).toFixed(1)}M`;
  if (num >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
  return num.toFixed(0);
}
