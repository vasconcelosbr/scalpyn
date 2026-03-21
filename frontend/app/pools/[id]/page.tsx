"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ChevronLeft, Plus, Trash2, Save, Loader2 } from "lucide-react";
import { apiFetch, apiGet, apiPost, apiDelete } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────
interface Pool {
  id: string;
  name: string;
  description: string;
  mode: string;
  market_type: string;
  is_active: boolean;
  profile_id: string | null;
  created_at: string | null;
}

interface Coin {
  id: string;
  symbol: string;
  market_type: string;
  is_active: boolean;
}

interface Profile {
  id: string;
  name: string;
}

// ── Page ───────────────────────────────────────────────────────────────────────
export default function PoolConfigPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [pool, setPool] = useState<Pool | null>(null);
  const [coins, setCoins] = useState<Coin[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState("paper");
  const [marketType, setMarketType] = useState("spot");
  const [isActive, setIsActive] = useState(true);
  const [profileId, setProfileId] = useState("");

  // Add coin form
  const [newCoin, setNewCoin] = useState("");
  const [addingCoin, setAddingCoin] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [poolsData, coinsData, profilesData] = await Promise.all([
        apiGet(`/pools`),
        apiGet(`/pools/${id}/coins`),
        apiGet(`/profiles`).catch(() => ({ profiles: [] })),
      ]);

      const found: Pool | undefined = poolsData.pools?.find(
        (p: Pool) => p.id === id
      );
      if (!found) {
        setError("Pool not found.");
        setLoading(false);
        return;
      }

      setPool(found);
      setName(found.name ?? "");
      setDescription(found.description ?? "");
      setMode(found.mode ?? "paper");
      setMarketType(found.market_type ?? "spot");
      setIsActive(found.is_active ?? true);
      setProfileId(found.profile_id ?? "");
      setCoins(coinsData.coins ?? []);
      setProfiles(profilesData.profiles ?? []);
    } catch (e: any) {
      setError(e.message ?? "Failed to load pool.");
    }
    setLoading(false);
  }, [id]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // ── Save pool metadata ─────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch(`/pools/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          mode,
          market_type: marketType,
          is_active: isActive,
          profile_id: profileId || null,
        }),
      });
      await fetchAll();
    } catch (e: any) {
      setError(e.message ?? "Failed to save.");
    }
    setSaving(false);
  };

  // ── Add coin ───────────────────────────────────────────────────────────────
  const handleAddCoin = async () => {
    const sym = newCoin.trim().toUpperCase();
    if (!sym) return;
    setAddingCoin(true);
    try {
      await apiPost(`/pools/${id}/coins`, {
        symbol: sym,
        market_type: marketType,
      });
      setNewCoin("");
      await fetchAll();
    } catch (e: any) {
      setError(e.message ?? "Failed to add coin.");
    }
    setAddingCoin(false);
  };

  // ── Remove coin ────────────────────────────────────────────────────────────
  const handleRemoveCoin = async (symbol: string) => {
    try {
      await apiDelete(`/pools/${id}/coins/${symbol}`);
      setCoins((prev) => prev.filter((c) => c.symbol !== symbol));
    } catch (e: any) {
      setError(e.message ?? "Failed to remove coin.");
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-8 w-48 rounded" />
        <div className="skeleton h-64 rounded-[var(--radius-lg)]" />
        <div className="skeleton h-48 rounded-[var(--radius-lg)]" />
      </div>
    );
  }

  if (error && !pool) {
    return (
      <div className="card">
        <div className="card-body empty-state">
          <p style={{ color: "var(--color-loss)" }}>{error}</p>
          <button className="btn btn-secondary" onClick={() => router.push("/pools")}>
            <ChevronLeft className="w-4 h-4" /> Back to Pools
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            className="btn btn-ghost"
            onClick={() => router.push("/pools")}
            aria-label="Back to pools"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <div>
            <h1 style={{ color: "var(--text-primary)" }}>{pool?.name ?? "Pool"}</h1>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", marginTop: "2px" }}>
              Configure pool settings and assets
            </p>
          </div>
        </div>
        <button
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || !name.trim()}
        >
          {saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: "12px 16px",
            background: "var(--color-loss-muted)",
            border: "1px solid var(--color-loss-border)",
            borderRadius: "var(--radius-md)",
            color: "var(--color-loss)",
            fontSize: "13px",
          }}
        >
          {error}
        </div>
      )}

      {/* ── Pool Settings ── */}
      <div className="card">
        <div className="card-header">
          <h3>Pool Settings</h3>
        </div>
        <div className="card-body space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="label">Pool Name</label>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Scalping Core"
              />
            </div>
            <div className="space-y-2">
              <label className="label">Trading Mode</label>
              <select
                className="input"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                <option value="paper">Paper Trading</option>
                <option value="live">Live Trading</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="label">Market Type</label>
              <select
                className="input"
                value={marketType}
                onChange={(e) => setMarketType(e.target.value)}
              >
                <option value="spot">Spot</option>
                <option value="futures">Futures</option>
                <option value="tradfi">TradFi</option>
              </select>
            </div>
            <div className="space-y-2">
              <label className="label">Strategy Profile</label>
              <select
                className="input"
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
              >
                <option value="">No Profile</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <label className="label">Description</label>
            <input
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description…"
            />
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={isActive}
              onClick={() => setIsActive((v) => !v)}
              className={`toggle ${isActive ? "active" : ""}`}
            >
              <span className="knob" />
            </button>
            <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
              Pool is <strong style={{ color: isActive ? "var(--color-profit)" : "var(--text-tertiary)" }}>
                {isActive ? "Active" : "Paused"}
              </strong>
            </span>
          </div>
        </div>
      </div>

      {/* ── Asset List ── */}
      <div className="card">
        <div className="card-header">
          <h3>Assets</h3>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              color: "var(--text-tertiary)",
            }}
          >
            {coins.length} symbols
          </span>
        </div>

        {/* Add coin row */}
        <div
          style={{
            padding: "12px 20px",
            borderBottom: "1px solid var(--border-subtle)",
            display: "flex",
            gap: "8px",
          }}
        >
          <input
            className="input"
            style={{ maxWidth: "200px" }}
            placeholder="e.g. BTC_USDT"
            value={newCoin}
            onChange={(e) => setNewCoin(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddCoin()}
          />
          <button
            className="btn btn-primary"
            onClick={handleAddCoin}
            disabled={addingCoin || !newCoin.trim()}
          >
            {addingCoin ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Plus className="w-4 h-4" />
            )}
            Add
          </button>
        </div>

        {coins.length === 0 ? (
          <div className="empty-state">
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <p>No assets added yet.<br />Type a symbol above and press Add.</p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Market</th>
                <th>Status</th>
                <th style={{ width: 48 }} />
              </tr>
            </thead>
            <tbody>
              {coins.map((coin) => (
                <tr key={coin.id}>
                  <td
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontWeight: 600,
                      fontSize: "13px",
                    }}
                  >
                    {coin.symbol}
                  </td>
                  <td style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
                    {coin.market_type?.toUpperCase()}
                  </td>
                  <td>
                    <span
                      className={`badge ${coin.is_active ? "bullish" : "range"}`}
                    >
                      {coin.is_active ? "Active" : "Paused"}
                    </span>
                  </td>
                  <td>
                    <button
                      className="btn btn-ghost"
                      style={{ color: "var(--color-loss)", padding: "4px 8px" }}
                      onClick={() => handleRemoveCoin(coin.symbol)}
                      aria-label={`Remove ${coin.symbol}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
