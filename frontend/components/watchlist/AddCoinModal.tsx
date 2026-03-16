"use client";

import React, { useState, useEffect, useRef, useMemo } from "react";
import { X, Search, Plus, TrendingUp, TrendingDown, CheckSquare, Square } from "lucide-react";

export interface SpotCurrency {
  rank: number;
  symbol: string;
  base: string;
  last_price: number;
  change_24h: number;
  volume_24h: number;
  volume_24h_formatted: string;
  market_cap?: number | null;
  market_cap_formatted?: string | null;
  is_futures?: boolean;
}

type MarketType = "spot" | "futures" | "tradfi";

interface AddCoinModalProps {
  onClose: () => void;
  onAdd: (coins: SpotCurrency[]) => void;
  existingSymbols: string[];
}

export function AddCoinModal({ onClose, onAdd, existingSymbols }: AddCoinModalProps) {
  const [currencies, setCurrencies] = useState<SpotCurrency[]>([]);
  const [filtered, setFiltered] = useState<SpotCurrency[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [marketType, setMarketType] = useState<MarketType>("spot");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const fetchCurrencies = async () => {
      setLoading(true);
      setError(null);
      setCurrencies([]);
      setFiltered([]);
      setSelected(new Set());

      if (marketType === "tradfi") {
        setLoading(false);
        return;
      }

      try {
        const baseUrl = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/v1\/?$/, "");
        const endpoint =
          marketType === "futures" ? "/market/futures-currencies" : "/market/spot-currencies";
        const response = await fetch(`${baseUrl}${endpoint}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const list: SpotCurrency[] = data.currencies || [];
        setCurrencies(list);
        setFiltered(list.slice(0, 100));
      } catch (err) {
        setError("Falha ao carregar criptomoedas. Tente novamente.");
        console.error("Failed to fetch currencies:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchCurrencies();
    setTimeout(() => searchRef.current?.focus(), 100);
  }, [marketType]);

  useEffect(() => {
    const q = search.trim().toUpperCase();
    if (!q) {
      setFiltered(currencies.slice(0, 100));
    } else {
      setFiltered(
        currencies.filter(
          (c) =>
            c.base.toUpperCase().includes(q) || c.symbol.toUpperCase().includes(q)
        )
      );
    }
  }, [search, currencies]);

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };

  const toggleSelect = (symbol: string, alreadyAdded: boolean) => {
    if (alreadyAdded) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) {
        next.delete(symbol);
      } else {
        next.add(symbol);
      }
      return next;
    });
  };

  const handleConfirm = () => {
    const toAdd = currencies.filter((c) => selected.has(c.symbol));
    if (toAdd.length > 0) onAdd(toAdd);
    else onClose();
  };

  const selectableInView = useMemo(
    () => filtered.filter((c) => !existingSymbols.includes(c.symbol)),
    [filtered, existingSymbols]
  );

  const allVisibleSelected =
    selectableInView.length > 0 && selectableInView.every((c) => selected.has(c.symbol));

  const marketTabs: { id: MarketType; label: string }[] = [
    { id: "spot", label: "Spot" },
    { id: "futures", label: "Futuros" },
    { id: "tradfi", label: "TradFi" },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={handleBackdropClick}
    >
      <div className="relative w-full max-w-2xl max-h-[90vh] flex flex-col bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-lg)] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-subtle)]">
          <div>
            <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">
              Adicionar Criptomoeda
            </h2>
            <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
              {marketType === "spot" && "Spot USDT — Gate.io · Ordenado por volume 24h"}
              {marketType === "futures" && "Futuros USDT Perpétuos — Gate.io · Ordenado por volume 24h"}
              {marketType === "tradfi" && "Ativos Tradicionais — Em breve"}
            </p>
          </div>
          <button
            className="btn-icon w-8 h-8 flex items-center justify-center hover:text-[var(--text-primary)]"
            onClick={onClose}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Market Type Filter Tabs */}
        <div className="flex gap-1 px-5 pt-3 pb-2 border-b border-[var(--border-subtle)]">
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
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="px-5 py-3 border-b border-[var(--border-subtle)]">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)]" />
            <input
              ref={searchRef}
              type="text"
              className="input pl-9 w-full text-[13px]"
              placeholder="Buscar por símbolo (BTC, ETH, SOL…)"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              disabled={marketType === "tradfi"}
            />
          </div>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center py-16 text-[var(--text-secondary)] text-[14px]">
              <span className="animate-pulse">Carregando pares{marketType === "futures" ? " futuros" : " spot"}…</span>
            </div>
          )}

          {error && !loading && (
            <div className="flex items-center justify-center py-16 text-[var(--color-loss)] text-[14px]">
              {error}
            </div>
          )}

          {!loading && !error && marketType === "tradfi" && (
            <div className="flex flex-col items-center justify-center py-16 text-[var(--text-secondary)] text-[14px] gap-2">
              <span className="text-3xl">🏦</span>
              <span>Ativos TradFi em breve</span>
              <span className="text-[12px] text-[var(--text-tertiary)]">Ações, ETFs e outros ativos tradicionais serão adicionados em futuras versões.</span>
            </div>
          )}

          {!loading && !error && marketType !== "tradfi" && (
            <table className="data-table w-full">
              <thead className="sticky top-0 bg-[var(--bg-card)] z-10">
                <tr>
                  <th className="w-10 text-center">
                    <button
                      className="btn-icon w-6 h-6 flex items-center justify-center mx-auto"
                      title="Selecionar todos visíveis"
                      onClick={() => {
                        const selectableSymbols = selectableInView.map((c) => c.symbol);
                        if (allVisibleSelected) {
                          setSelected((prev) => {
                            const next = new Set(prev);
                            selectableSymbols.forEach((s) => next.delete(s));
                            return next;
                          });
                        } else {
                          setSelected((prev) => new Set([...prev, ...selectableSymbols]));
                        }
                      }}
                    >
                      {allVisibleSelected ? (
                        <CheckSquare className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      ) : (
                        <Square className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </th>
                  <th className="w-10 text-center">#</th>
                  <th>Símbolo</th>
                  <th className="text-right">Preço</th>
                  <th className="text-right">24h %</th>
                  <th className="text-right">Mkt Cap</th>
                  <th className="text-right">Volume 24h</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center py-10 text-[var(--text-tertiary)] text-[13px]">
                      Nenhuma criptomoeda encontrada
                    </td>
                  </tr>
                )}
                {filtered.map((coin) => {
                  const alreadyAdded = existingSymbols.includes(coin.symbol);
                  const isSelected = selected.has(coin.symbol);
                  return (
                    <tr
                      key={coin.symbol}
                      className={`cursor-pointer ${alreadyAdded ? "opacity-40" : isSelected ? "bg-[var(--accent-primary)]/10" : "hover:bg-[var(--bg-hover)]"}`}
                      onClick={() => toggleSelect(coin.symbol, alreadyAdded)}
                    >
                      <td className="text-center" onClick={(e) => e.stopPropagation()}>
                        <button
                          disabled={alreadyAdded}
                          className="btn-icon w-6 h-6 flex items-center justify-center mx-auto"
                          onClick={() => toggleSelect(coin.symbol, alreadyAdded)}
                        >
                          {isSelected ? (
                            <CheckSquare className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                          ) : (
                            <Square className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                          )}
                        </button>
                      </td>
                      <td className="text-center text-[var(--text-tertiary)] text-[12px] font-mono">
                        {coin.rank}
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-[var(--text-primary)]">
                            {coin.base}
                          </span>
                          <span className="text-[11px] text-[var(--text-tertiary)]">
                            {coin.is_futures ? "/USDT Perp" : "/USDT"}
                          </span>
                        </div>
                      </td>
                      <td className="numeric price text-right">
                        {coin.last_price > 0
                          ? `$${coin.last_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`
                          : "—"}
                      </td>
                      <td
                        className={`numeric text-right text-[13px] ${
                          coin.change_24h >= 0 ? "profit" : "loss"
                        }`}
                      >
                        <span className="inline-flex items-center gap-1">
                          {coin.change_24h >= 0 ? (
                            <TrendingUp className="w-3 h-3" />
                          ) : (
                            <TrendingDown className="w-3 h-3" />
                          )}
                          {coin.change_24h >= 0 ? "+" : ""}
                          {coin.change_24h.toFixed(2)}%
                        </span>
                      </td>
                      <td className="numeric text-right text-[var(--text-secondary)] text-[12px]">
                        {coin.market_cap_formatted ?? "—"}
                      </td>
                      <td className="numeric text-right text-[var(--text-secondary)] text-[12px]">
                        {coin.volume_24h_formatted}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        {!loading && !error && marketType !== "tradfi" && (
          <div className="px-5 py-3 border-t border-[var(--border-subtle)] flex items-center justify-between gap-3">
            <span className="text-[11px] text-[var(--text-tertiary)]">
              {filtered.length !== 1
                ? `${filtered.length} pares exibidos`
                : `${filtered.length} par exibido`}
              {currencies.length > 100 && !search && ` (top 100 de ${currencies.length})`}
              {selected.size > 0 && ` · ${selected.size} selecionado${selected.size !== 1 ? "s" : ""}`}
            </span>
            <div className="flex gap-2">
              <button className="btn text-[13px] px-4 py-1.5" onClick={onClose}>
                Cancelar
              </button>
              <button
                className="btn btn-primary text-[13px] px-4 py-1.5"
                disabled={selected.size === 0}
                onClick={handleConfirm}
              >
                <Plus className="w-3.5 h-3.5 mr-1" />
                Adicionar{selected.size > 0 ? ` (${selected.size})` : ""}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
