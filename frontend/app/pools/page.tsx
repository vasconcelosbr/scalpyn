"use client";

import { useState, useEffect, useCallback } from "react";
import { Plus, X, Trash2 } from "lucide-react";
import { formatPercent } from "@/lib/utils";
import { getAuthHeaders } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

interface Pool {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  mode: string;
  overrides: Record<string, string>;
}

interface PoolCoin {
  id: string;
  symbol: string;
  market_type: string;
  is_active: boolean;
}

// ── Init Pool Vector Modal ────────────────────────────────────────────────────
function InitPoolModal({ onClose, onCreated }: { onClose: () => void; onCreated: (pool: Pool) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState<"paper" | "live">("paper");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/pools/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ name: name.trim(), description: description.trim(), mode, is_active: true }),
      });
      if (!res.ok) throw new Error("Failed to create pool");
      const pool: Pool = await res.json();
      onCreated(pool);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error creating pool");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-lg)] w-full max-w-md p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-5">
          <h2 className="text-lg font-bold text-[var(--text-primary)]">Init Pool Vector</h2>
          <button className="btn-icon w-7 h-7 flex items-center justify-center" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="label mb-1 block">Pool Name</label>
            <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Altcoin Alpha" required />
          </div>
          <div>
            <label className="label mb-1 block">Description</label>
            <input className="input w-full" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional description" />
          </div>
          <div>
            <label className="label mb-1 block">Mode</label>
            <div className="flex gap-3">
              {(["paper", "live"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`flex-1 py-2 rounded-[var(--radius-sm)] text-[13px] font-semibold border transition-colors capitalize ${
                    mode === m
                      ? "bg-[var(--accent-primary)] text-white border-[var(--accent-primary)]"
                      : "bg-transparent text-[var(--text-secondary)] border-[var(--border-default)]"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
          {error && <p className="text-[var(--color-loss)] text-[12px]">{error}</p>}
          <div className="flex gap-3 pt-2">
            <button type="button" className="btn flex-1" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary flex-1" disabled={loading}>
              {loading ? "Creating…" : "Create Pool"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Configure Nodes Modal ─────────────────────────────────────────────────────
function ConfigureNodesModal({ pool, onClose }: { pool: Pool; onClose: () => void }) {
  const [coins, setCoins] = useState<PoolCoin[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
  const [newMarketType, setNewMarketType] = useState("spot");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchCoins = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/pools/${pool.id}/coins`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error("Failed to load coins");
      const data = await res.json();
      setCoins(data.coins ?? []);
    } catch {
      setCoins([]);
    }
  }, [pool.id]);

  useEffect(() => { fetchCoins(); }, [fetchCoins]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSymbol.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/pools/${pool.id}/coins`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ symbol: newSymbol.trim().toUpperCase(), market_type: newMarketType }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? "Failed to add coin");
      }
      setNewSymbol("");
      await fetchCoins();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error adding coin");
    } finally {
      setLoading(false);
    }
  };

  const handleRemove = async (symbol: string) => {
    try {
      await fetch(`${API_URL}/pools/${pool.id}/coins/${symbol}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      await fetchCoins();
    } catch {
      // ignore
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-lg)] w-full max-w-lg p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-5">
          <div>
            <h2 className="text-lg font-bold text-[var(--text-primary)]">Configure Nodes</h2>
            <p className="text-[var(--text-secondary)] text-[12px] mt-0.5">{pool.name}</p>
          </div>
          <button className="btn-icon w-7 h-7 flex items-center justify-center" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Add symbol form */}
        <form onSubmit={handleAdd} className="flex gap-2 mb-4">
          <input
            className="input flex-1"
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value)}
            placeholder="Symbol (e.g. BTCUSDT)"
          />
          <select
            className="input w-28"
            value={newMarketType}
            onChange={(e) => setNewMarketType(e.target.value)}
          >
            <option value="spot">Spot</option>
            <option value="futures">Futures</option>
            <option value="tradfi">TradFi</option>
          </select>
          <button type="submit" className="btn btn-primary px-4" disabled={loading}>
            <Plus className="w-4 h-4" />
          </button>
        </form>

        {error && <p className="text-[var(--color-loss)] text-[12px] mb-3">{error}</p>}

        <div className="space-y-1 max-h-64 overflow-y-auto">
          {coins.length === 0 && (
            <p className="text-[var(--text-tertiary)] text-[13px] py-4 text-center">No assets tracked. Add a symbol above.</p>
          )}
          {coins.map((coin) => (
            <div key={coin.id} className="flex items-center justify-between px-3 py-2 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)]">
              <div>
                <span className="font-semibold text-[13px] text-[var(--text-primary)]">{coin.symbol}</span>
                <span className="caption ml-2 capitalize">{coin.market_type}</span>
              </div>
              <button
                className="btn-icon w-6 h-6 flex items-center justify-center hover:text-[var(--color-loss)]"
                onClick={() => handleRemove(coin.symbol)}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex justify-end pt-4">
          <button className="btn btn-primary px-6" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}

// ── Override Rules Modal ──────────────────────────────────────────────────────
function OverrideRulesModal({ pool, onClose, onSaved }: { pool: Pool; onClose: () => void; onSaved: (pool: Pool) => void }) {
  const [overrides, setOverrides] = useState<Record<string, string>>(pool.overrides ?? {});
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAddRule = (e: React.FormEvent) => {
    e.preventDefault();
    const k = newKey.trim();
    const v = newValue.trim();
    if (!k) return;
    setOverrides((prev) => ({ ...prev, [k]: v }));
    setNewKey("");
    setNewValue("");
  };

  const handleRemoveRule = (key: string) => {
    setOverrides((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/pools/${pool.id}/overrides`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify(overrides),
      });
      if (!res.ok) throw new Error("Failed to save overrides");
      onSaved({ ...pool, overrides });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error saving overrides");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-[var(--radius-lg)] w-full max-w-lg p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-5">
          <div>
            <h2 className="text-lg font-bold text-[var(--text-primary)]">Override Rules</h2>
            <p className="text-[var(--text-secondary)] text-[12px] mt-0.5">{pool.name}</p>
          </div>
          <button className="btn-icon w-7 h-7 flex items-center justify-center" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-[var(--text-secondary)] text-[12px] mb-4">
          Set pool-specific rule overrides as key/value pairs. These override global signal and block rules for this pool.
        </p>

        {/* Add rule form */}
        <form onSubmit={handleAddRule} className="flex gap-2 mb-4">
          <input
            className="input flex-1"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="Rule key (e.g. min_alpha_score)"
          />
          <input
            className="input flex-1"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder="Value (e.g. 70)"
          />
          <button type="submit" className="btn btn-primary px-4">
            <Plus className="w-4 h-4" />
          </button>
        </form>

        <div className="space-y-1 max-h-52 overflow-y-auto mb-4">
          {Object.keys(overrides).length === 0 && (
            <p className="text-[var(--text-tertiary)] text-[13px] py-4 text-center">No overrides set. Add a rule above.</p>
          )}
          {Object.entries(overrides).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between px-3 py-2 rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border-subtle)]">
              <div className="flex gap-3 text-[13px]">
                <span className="font-semibold text-[var(--accent-primary)]">{k}</span>
                <span className="text-[var(--text-secondary)]">=</span>
                <span className="text-[var(--text-primary)]">{v}</span>
              </div>
              <button
                className="btn-icon w-6 h-6 flex items-center justify-center hover:text-[var(--color-loss)]"
                onClick={() => handleRemoveRule(k)}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>

        {error && <p className="text-[var(--color-loss)] text-[12px] mb-3">{error}</p>}

        <div className="flex gap-3">
          <button className="btn flex-1" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary flex-1" onClick={handleSave} disabled={loading}>
            {loading ? "Saving…" : "Save Overrides"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function PoolsPage() {
  const [pools, setPools] = useState<Pool[]>([]);
  const [loading, setLoading] = useState(false);
  const [showInitModal, setShowInitModal] = useState(false);
  const [configurePool, setConfigurePool] = useState<Pool | null>(null);
  const [overridePool, setOverridePool] = useState<Pool | null>(null);

  const fetchPools = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/pools/`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error("Failed to load pools");
      const data = await res.json();
      setPools(data.pools ?? []);
    } catch {
      setPools([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPools(); }, [fetchPools]);

  const handlePoolCreated = (pool: Pool) => {
    setPools((prev) => [...prev, pool]);
    setShowInitModal(false);
  };

  const handleOverrideSaved = (updated: Pool) => {
    setPools((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    setOverridePool(null);
  };

  return (
    <>
      {showInitModal && (
        <InitPoolModal onClose={() => setShowInitModal(false)} onCreated={handlePoolCreated} />
      )}
      {configurePool && (
        <ConfigureNodesModal pool={configurePool} onClose={() => setConfigurePool(null)} />
      )}
      {overridePool && (
        <OverrideRulesModal pool={overridePool} onClose={() => setOverridePool(null)} onSaved={handleOverrideSaved} />
      )}

      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Strategy Pools</h1>
            <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Manage isolated subsets of coins and override strategies.</p>
          </div>
          <button className="btn btn-primary" onClick={() => setShowInitModal(true)}>
            <Plus className="w-4 h-4 mr-1.5" />
            Init Pool Vector
          </button>
        </div>

        {loading && (
          <div className="text-center py-12 text-[var(--text-tertiary)] text-[13px]">Loading pools…</div>
        )}

        {!loading && pools.length === 0 && (
          <div className="card">
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <p className="text-[var(--text-secondary)] text-[13px]">No pools yet. Click <strong>Init Pool Vector</strong> to create one.</p>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {pools.map((pool) => (
            <div key={pool.id} className="card flex flex-col">
              <div className="card-body flex-1 p-6">
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h3 className="text-lg font-bold text-[var(--text-primary)] tracking-tight">{pool.name}</h3>
                    <div className="flex items-center gap-2 mt-2">
                      {pool.mode === "live" ? (
                        <span className="badge bullish">LIVE</span>
                      ) : (
                        <span className="badge range">PAPER</span>
                      )}
                      {pool.is_active ? (
                        <span className="caption flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-[var(--color-profit)]"></span>Active</span>
                      ) : (
                        <span className="caption flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-[var(--color-neutral)]"></span>Paused</span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="space-y-4 mb-4 mt-2">
                  {pool.description && (
                    <p className="text-[12px] text-[var(--text-secondary)]">{pool.description}</p>
                  )}
                  {Object.keys(pool.overrides ?? {}).length > 0 && (
                    <div className="flex justify-between items-center text-[13px] border-b border-[var(--border-subtle)] pb-2">
                      <span className="text-[var(--text-secondary)] font-medium">Override Rules</span>
                      <span className="data-value text-[var(--accent-primary)]">{Object.keys(pool.overrides).length}</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 border-t border-[var(--border-default)]">
                <button
                  className="py-3.5 text-[13px] font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] border-r border-[var(--border-default)] transition-colors"
                  onClick={() => setConfigurePool(pool)}
                >
                  Configure Nodes
                </button>
                <button
                  className="py-3.5 text-[13px] font-semibold text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors"
                  onClick={() => setOverridePool(pool)}
                >
                  Override Rules
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

