"use client";

import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  X,
  Search,
  Plus,
  TrendingUp,
  TrendingDown,
  CheckSquare,
  Square,
  Loader2,
  ChevronLeft,
  ChevronRight,
  EyeOff,
  Eye,
} from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

export interface AssetSearchResult {
  symbol: string;
  name: string;
  price: number;
  market_cap: number;
  volume_24h: number;
  change_24h: number;
  type: "spot" | "futures" | "tradfi";
  already_in_pool: boolean;
}

type MarketType = "spot" | "futures" | "tradfi";

interface AddCryptosModalProps {
  poolId: string;
  poolMarketType: string;
  onClose: () => void;
  onAdded: () => void;
  existingSymbols: string[];
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatValue(value: number | null | undefined): string {
  if (value == null || value <= 0) return "—";
  if (value >= 1_000_000_000_000) return `$${(value / 1_000_000_000_000).toFixed(2)}T`;
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(2)}K`;
  return `$${value.toFixed(2)}`;
}

function formatPrice(price: number): string {
  if (price <= 0) return "—";
  return `$${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function AddCryptosModal({
  poolId,
  poolMarketType,
  onClose,
  onAdded,
  existingSymbols,
}: AddCryptosModalProps) {
  // State per tab — persist selections across tab switches
  const [marketType, setMarketType] = useState<MarketType>(
    (poolMarketType as MarketType) || "spot"
  );

  const [results, setResults] = useState<AssetSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [total, setTotal] = useState(0);
  const [hideExisting, setHideExisting] = useState(false);
  const [adding, setAdding] = useState(false);

  // Selection state persisted per tab
  const [selectionsByTab, setSelectionsByTab] = useState<Record<MarketType, Set<string>>>({
    spot: new Set(),
    futures: new Set(),
    tradfi: new Set(),
  });

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const PER_PAGE = 50;

  const selected = selectionsByTab[marketType];

  // ── Fetch assets from backend ──────────────────────────────────────────────

  const fetchAssets = useCallback(
    async (query: string, pageNum: number, mType: MarketType) => {
      if (mType === "tradfi") {
        setResults([]);
        setTotal(0);
        setTotalPages(0);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          query,
          type: mType,
          pool_id: poolId,
          page: String(pageNum),
          per_page: String(PER_PAGE),
        });
        const data = await apiGet(`/assets/search?${params.toString()}`);
        setResults(data.results ?? []);
        setTotal(data.total ?? 0);
        setTotalPages(data.total_pages ?? 0);
      } catch (err: any) {
        setError(err.message ?? "Failed to search assets");
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [poolId]
  );

  // Initial load when tab changes
  useEffect(() => {
    setSearch("");
    setPage(1);
    fetchAssets("", 1, marketType);
    setTimeout(() => searchRef.current?.focus(), 100);
  }, [marketType, fetchAssets]);

  // Debounced search
  const handleSearchChange = (value: string) => {
    setSearch(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      fetchAssets(value.trim(), 1, marketType);
    }, 400);
  };

  // Pagination
  const handlePageChange = (newPage: number) => {
    setPage(newPage);
    fetchAssets(search.trim(), newPage, marketType);
  };

  // ── Selection ──────────────────────────────────────────────────────────────

  const toggleSelect = (symbol: string, alreadyAdded: boolean) => {
    if (alreadyAdded) return;
    setSelectionsByTab((prev) => {
      const next = new Set(prev[marketType]);
      if (next.has(symbol)) {
        next.delete(symbol);
      } else {
        next.add(symbol);
      }
      return { ...prev, [marketType]: next };
    });
  };

  // Filter displayed results
  const displayResults = useMemo(() => {
    if (!hideExisting) return results;
    return results.filter((r) => !r.already_in_pool);
  }, [results, hideExisting]);

  const selectableInView = useMemo(
    () => displayResults.filter((r) => !r.already_in_pool),
    [displayResults]
  );

  const allVisibleSelected =
    selectableInView.length > 0 &&
    selectableInView.every((r) => selected.has(r.symbol));

  const toggleSelectAll = () => {
    const symbols = selectableInView.map((r) => r.symbol);
    setSelectionsByTab((prev) => {
      const next = new Set(prev[marketType]);
      if (allVisibleSelected) {
        symbols.forEach((s) => next.delete(s));
      } else {
        symbols.forEach((s) => next.add(s));
      }
      return { ...prev, [marketType]: next };
    });
  };

  // Total selections across all tabs
  const totalSelected =
    selectionsByTab.spot.size + selectionsByTab.futures.size + selectionsByTab.tradfi.size;

  // ── Confirm: bulk add ──────────────────────────────────────────────────────

  const handleConfirm = async () => {
    const assets = Object.entries(selectionsByTab).flatMap(([mType, syms]) =>
      Array.from(syms).map((sym) => ({ symbol: sym, market_type: mType }))
    );

    if (assets.length === 0) {
      onClose();
      return;
    }

    setAdding(true);
    setError(null);
    try {
      await apiPost(`/pools/${poolId}/coins/bulk`, { assets });
      onAdded();
      onClose();
    } catch (err: any) {
      setError(err.message ?? "Failed to add assets");
    } finally {
      setAdding(false);
    }
  };

  // ── Backdrop close ─────────────────────────────────────────────────────────

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };

  // ── Tab config ─────────────────────────────────────────────────────────────

  const marketTabs: { id: MarketType; label: string }[] = [
    { id: "spot", label: "Spot" },
    { id: "futures", label: "Futures" },
    { id: "tradfi", label: "TradFi" },
  ];

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={handleBackdropClick}
    >
      <div className="relative w-full max-w-3xl max-h-[90vh] flex flex-col bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-lg)] shadow-2xl overflow-hidden">
        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-subtle)]">
          <div>
            <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">
              Adicionar Criptos Manualmente
            </h2>
            <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
              Busque e selecione ativos para adicionar ao pool
            </p>
          </div>
          <button
            className="btn-icon w-8 h-8 flex items-center justify-center hover:text-[var(--text-primary)]"
            onClick={onClose}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ── Market Tabs ── */}
        <div className="flex items-center justify-between px-5 pt-3 pb-2 border-b border-[var(--border-subtle)]">
          <div className="flex gap-1">
            {marketTabs.map((tab) => (
              <button
                key={tab.id}
                className={`px-4 py-1.5 text-[12px] font-medium rounded-[var(--radius-sm)] transition-colors ${
                  marketType === tab.id
                    ? "bg-[var(--accent-primary)] text-white"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]"
                }`}
                onClick={() => setMarketType(tab.id)}
              >
                {tab.label}
                {selectionsByTab[tab.id].size > 0 && (
                  <span className="ml-1.5 px-1.5 py-0.5 text-[10px] rounded-full bg-white/20">
                    {selectionsByTab[tab.id].size}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Hide existing toggle */}
          <label className="flex items-center gap-2 cursor-pointer text-[12px] text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={hideExisting}
              onChange={(e) => setHideExisting(e.target.checked)}
              style={{ accentColor: "var(--accent-primary)" }}
            />
            {hideExisting ? (
              <EyeOff className="w-3.5 h-3.5" />
            ) : (
              <Eye className="w-3.5 h-3.5" />
            )}
            Ocultar já adicionados
          </label>
        </div>

        {/* ── Search ── */}
        <div className="px-5 py-3 border-b border-[var(--border-subtle)]">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)]" />
            <input
              ref={searchRef}
              type="text"
              className="input pl-9 w-full text-[13px]"
              placeholder="Buscar por símbolo (BTC, ETH, SOL…)"
              value={search}
              onChange={(e) => handleSearchChange(e.target.value)}
              disabled={marketType === "tradfi"}
            />
            {loading && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin text-[var(--text-tertiary)]" />
            )}
          </div>
        </div>

        {/* ── Error ── */}
        {error && (
          <div
            className="mx-5 mt-3 px-3 py-2 text-[13px] rounded-[var(--radius-md)]"
            style={{
              background: "var(--color-loss-muted)",
              color: "var(--color-loss)",
              border: "1px solid var(--color-loss-border)",
            }}
          >
            {error}
          </div>
        )}

        {/* ── Table ── */}
        <div className="flex-1 overflow-y-auto">
          {loading && results.length === 0 && (
            <div className="flex items-center justify-center py-16 text-[var(--text-secondary)] text-[14px]">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              <span>Carregando ativos…</span>
            </div>
          )}

          {!loading && marketType === "tradfi" && (
            <div className="flex flex-col items-center justify-center py-16 text-[var(--text-secondary)] text-[14px] gap-2">
              <span className="text-3xl">🏦</span>
              <span>Ativos TradFi em breve</span>
              <span className="text-[12px] text-[var(--text-tertiary)]">
                Ações, ETFs e outros ativos tradicionais serão adicionados em futuras versões.
              </span>
            </div>
          )}

          {!loading && !error && marketType !== "tradfi" && displayResults.length === 0 && (
            <div className="flex items-center justify-center py-16 text-[var(--text-tertiary)] text-[13px]">
              {search
                ? `Nenhum ativo encontrado para "${search}"`
                : "Nenhum ativo disponível"}
            </div>
          )}

          {marketType !== "tradfi" && displayResults.length > 0 && (
            <table className="data-table w-full">
              <thead className="sticky top-0 bg-[var(--bg-card)] z-10">
                <tr>
                  <th className="w-10 text-center">
                    <button
                      className="btn-icon w-6 h-6 flex items-center justify-center mx-auto"
                      title="Selecionar todos visíveis"
                      onClick={toggleSelectAll}
                    >
                      {allVisibleSelected ? (
                        <CheckSquare className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      ) : (
                        <Square className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </th>
                  <th>Symbol</th>
                  <th className="text-right">Preço</th>
                  <th className="text-right">24h %</th>
                  <th className="text-right">Market Cap</th>
                  <th className="text-right" title="Volume 24h em USDT (ticker spot Gate.io). Não inclui futuros perpétuos nem outras exchanges.">Vol 24h (Gate Spot)</th>
                </tr>
              </thead>
              <tbody>
                {displayResults.map((asset) => {
                  const alreadyAdded =
                    asset.already_in_pool || existingSymbols.includes(asset.symbol);
                  const isSelected = selected.has(asset.symbol);
                  return (
                    <tr
                      key={asset.symbol}
                      className={`cursor-pointer ${
                        alreadyAdded
                          ? "opacity-40"
                          : isSelected
                          ? "bg-[var(--accent-primary)]/10"
                          : "hover:bg-[var(--bg-hover)]"
                      }`}
                      onClick={() => toggleSelect(asset.symbol, alreadyAdded)}
                    >
                      <td className="text-center" onClick={(e) => e.stopPropagation()}>
                        <button
                          disabled={alreadyAdded}
                          className="btn-icon w-6 h-6 flex items-center justify-center mx-auto"
                          onClick={() => toggleSelect(asset.symbol, alreadyAdded)}
                        >
                          {alreadyAdded ? (
                            <CheckSquare className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                          ) : isSelected ? (
                            <CheckSquare className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                          ) : (
                            <Square className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                          )}
                        </button>
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-[13px] text-[var(--text-primary)] font-mono">
                            {asset.name}
                          </span>
                          <span className="text-[11px] text-[var(--text-tertiary)]">
                            /USDT{asset.type === "futures" ? " Perp" : ""}
                          </span>
                          {alreadyAdded && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-hover)] text-[var(--text-tertiary)]">
                              No pool
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="numeric price text-right text-[13px]">
                        {formatPrice(asset.price)}
                      </td>
                      <td
                        className={`numeric text-right text-[13px] ${
                          asset.change_24h >= 0 ? "profit" : "loss"
                        }`}
                      >
                        <span className="inline-flex items-center gap-1">
                          {asset.change_24h >= 0 ? (
                            <TrendingUp className="w-3 h-3" />
                          ) : (
                            <TrendingDown className="w-3 h-3" />
                          )}
                          {asset.change_24h >= 0 ? "+" : ""}
                          {asset.change_24h.toFixed(2)}%
                        </span>
                      </td>
                      <td className="numeric text-right text-[var(--text-secondary)] text-[12px]">
                        {formatValue(asset.market_cap)}
                      </td>
                      <td className="numeric text-right text-[var(--text-secondary)] text-[12px]">
                        {formatValue(asset.volume_24h)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Pagination ── */}
        {!loading && marketType !== "tradfi" && totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 px-5 py-2 border-t border-[var(--border-subtle)]">
            <button
              className="btn btn-ghost text-[12px] px-2 py-1"
              disabled={page <= 1}
              onClick={() => handlePageChange(page - 1)}
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <span className="text-[12px] text-[var(--text-secondary)]">
              Página {page} de {totalPages}
              <span className="text-[var(--text-tertiary)] ml-1">({total} ativos)</span>
            </span>
            <button
              className="btn btn-ghost text-[12px] px-2 py-1"
              disabled={page >= totalPages}
              onClick={() => handlePageChange(page + 1)}
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* ── Footer ── */}
        {marketType !== "tradfi" && (
          <div className="px-5 py-3 border-t border-[var(--border-subtle)] flex items-center justify-between gap-3">
            <span className="text-[11px] text-[var(--text-tertiary)]">
              {displayResults.length} ativos exibidos
              {totalSelected > 0 &&
                ` · ${totalSelected} selecionado${totalSelected !== 1 ? "s" : ""}`}
            </span>
            <div className="flex gap-2">
              <button className="btn text-[13px] px-4 py-1.5" onClick={onClose}>
                Cancelar
              </button>
              <button
                className="btn btn-primary text-[13px] px-4 py-1.5"
                disabled={totalSelected === 0 || adding}
                onClick={handleConfirm}
              >
                {adding ? (
                  <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                ) : (
                  <Plus className="w-3.5 h-3.5 mr-1" />
                )}
                {adding
                  ? "Adicionando…"
                  : `Adicionar ao Pool${totalSelected > 0 ? ` (${totalSelected})` : ""}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
