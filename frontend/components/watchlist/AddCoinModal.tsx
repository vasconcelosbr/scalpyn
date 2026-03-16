"use client";

import React, { useState, useEffect, useRef } from "react";
import { X, Search, Plus, TrendingUp, TrendingDown } from "lucide-react";

export interface SpotCurrency {
  rank: number;
  symbol: string;
  base: string;
  last_price: number;
  change_24h: number;
  volume_24h: number;
  volume_24h_formatted: string;
}

interface AddCoinModalProps {
  onClose: () => void;
  onAdd: (coin: SpotCurrency) => void;
  existingSymbols: string[];
}

export function AddCoinModal({ onClose, onAdd, existingSymbols }: AddCoinModalProps) {
  const [currencies, setCurrencies] = useState<SpotCurrency[]>([]);
  const [filtered, setFiltered] = useState<SpotCurrency[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const fetchCurrencies = async () => {
      try {
        const baseUrl = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/v1\/?$/, "");
        const response = await fetch(`${baseUrl}/market/spot-currencies`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const list: SpotCurrency[] = data.currencies || [];
        setCurrencies(list);
        setFiltered(list.slice(0, 100));
      } catch (err) {
        setError("Falha ao carregar criptomoedas. Tente novamente.");
        console.error("Failed to fetch spot currencies:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchCurrencies();
    setTimeout(() => searchRef.current?.focus(), 100);
  }, []);

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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={handleBackdropClick}
    >
      <div className="relative w-full max-w-2xl max-h-[85vh] flex flex-col bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-lg)] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-subtle)]">
          <div>
            <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">
              Adicionar Criptomoeda
            </h2>
            <p className="text-[12px] text-[var(--text-secondary)] mt-0.5">
              Spot USDT — Gate.io · Ordenado por volume 24h
            </p>
          </div>
          <button
            className="btn-icon w-8 h-8 flex items-center justify-center hover:text-[var(--text-primary)]"
            onClick={onClose}
          >
            <X className="w-4 h-4" />
          </button>
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
            />
          </div>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center py-16 text-[var(--text-secondary)] text-[14px]">
              <span className="animate-pulse">Carregando pares spot…</span>
            </div>
          )}

          {error && !loading && (
            <div className="flex items-center justify-center py-16 text-[var(--color-loss)] text-[14px]">
              {error}
            </div>
          )}

          {!loading && !error && (
            <table className="data-table w-full">
              <thead className="sticky top-0 bg-[var(--bg-card)] z-10">
                <tr>
                  <th className="w-12 text-center">#</th>
                  <th>Símbolo</th>
                  <th className="text-right">Preço</th>
                  <th className="text-right">24h %</th>
                  <th className="text-right">Volume 24h</th>
                  <th className="w-16"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={6} className="text-center py-10 text-[var(--text-tertiary)] text-[13px]">
                      Nenhuma criptomoeda encontrada
                    </td>
                  </tr>
                )}
                {filtered.map((coin) => {
                  const alreadyAdded = existingSymbols.includes(coin.symbol);
                  return (
                    <tr key={coin.symbol} className={alreadyAdded ? "opacity-40" : ""}>
                      <td className="text-center text-[var(--text-tertiary)] text-[12px] font-mono">
                        {coin.rank}
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-[var(--text-primary)]">
                            {coin.base}
                          </span>
                          <span className="text-[11px] text-[var(--text-tertiary)]">/USDT</span>
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
                        {coin.volume_24h_formatted}
                      </td>
                      <td className="text-center">
                        <button
                          disabled={alreadyAdded}
                          className={`btn-icon w-7 h-7 flex items-center justify-center transition-colors ${
                            alreadyAdded
                              ? "cursor-default"
                              : "hover:bg-[var(--accent-primary)] hover:text-white hover:border-[var(--accent-primary)]"
                          }`}
                          title={alreadyAdded ? "Já adicionado" : "Adicionar à Watchlist"}
                          onClick={() => !alreadyAdded && onAdd(coin)}
                        >
                          <Plus className="w-3.5 h-3.5" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        {!loading && !error && (
          <div className="px-5 py-3 border-t border-[var(--border-subtle)] text-[11px] text-[var(--text-tertiary)]">
            {filtered.length !== 1
              ? `${filtered.length} pares exibidos`
              : `${filtered.length} par exibido`}
            {currencies.length > 100 && !search && ` (top 100 de ${currencies.length})`}
          </div>
        )}
      </div>
    </div>
  );
}
