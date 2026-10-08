"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { ChevronLeft, Plus, Trash2, Save, Loader2, Search, ListPlus } from "lucide-react";
import { apiFetch, apiGet, apiPost, apiDelete } from "@/lib/api";
import AddCryptosModal from "@/components/pools/AddCryptosModal";
import RadarFeedHistory from "@/components/pools/RadarFeedHistory";

// ── Types ──────────────────────────────────────────────────────────────────────
interface Pool {
  id: string;
  name: string;
  description: string;
  mode: string;
  market_type: string;
  is_active: boolean;
  profile_id: string | null;
  overrides: Record<string, any>;
  created_at: string | null;
}

interface Coin {
  id: string;
  symbol: string;
  market_type: string;
  is_active: boolean;
  // Task #232 — execution-only gate (separate from is_active which is
  // the ingestion gate). Toggled via the "Tradable" switch in the
  // assets table; defaults to false so trading is opt-in per symbol.
  is_tradable: boolean;
  origin?: string;
  discovered_at?: string | null;
}

interface Profile {
  id: string;
  name: string;
}

interface PipelineWatchlist {
  id: string;
  name: string;
  level: string;
  source_pool_id: string | null;
  profile_id: string | null;
}

interface SearchResult {
  symbol: string;
  base: string;
  quote: string;
  market_type: string;
}

interface DiscoverResult {
  found: number;
  added: number;
  removed: number;
  kept_manual: number;
}

// ── Page ───────────────────────────────────────────────────────────────────────
export default function PoolConfigPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [pool, setPool] = useState<Pool | null>(null);
  const [coins, setCoins] = useState<Coin[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [pipelineWatchlists, setPipelineWatchlists] = useState<PipelineWatchlist[]>([]);
  const [watchlistId, setWatchlistId] = useState<string>("");
  const [assignedWatchlistId, setAssignedWatchlistId] = useState<string>("");
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

  // Auto-refresh settings (stored in pool.overrides)
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [autoAdd, setAutoAdd] = useState(true);
  const [autoRemove, setAutoRemove] = useState(false);
  const [notifyChanges, setNotifyChanges] = useState(false);

  // Market Catalyst Radar settings (stored in pool.overrides)
  const [radarEnabled, setRadarEnabled] = useState(false);
  // Minimum hold after the last radar sighting (backend default 300 s, 0 = strict).
  const [radarMinHold, setRadarMinHold] = useState(300);
  const radarMinHoldValid = Number.isInteger(radarMinHold) && radarMinHold >= 0 && radarMinHold <= 3600;

  // Pump Monitor sync (observation-only pool; stored in pool.overrides)
  const [pmSync, setPmSync] = useState(false);
  const [pmParams, setPmParams] = useState({
    min_score: 50, exit_consecutive_cycles: 3, min_hold_seconds: 300,
  });
  const pmValid = pmParams.min_score >= 0 && pmParams.min_score <= 100;

  // Discovery settings (stored in pool.overrides)
  const [maxAssets, setMaxAssets] = useState<number>(0);

  // Add coin form
  const [newCoin, setNewCoin] = useState("");
  const [addingCoin, setAddingCoin] = useState(false);

  // Autocomplete
  const [suggestions, setSuggestions] = useState<SearchResult[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Discover
  const [discovering, setDiscovering] = useState(false);
  const [discoverResult, setDiscoverResult] = useState<DiscoverResult | null>(null);

  // Add Cryptos Modal
  const [showAddCryptosModal, setShowAddCryptosModal] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [poolsData, coinsData, profilesData, watchlistsData] = await Promise.all([
        apiGet(`/pools`),
        apiGet(`/pools/${id}/coins`),
        apiGet(`/profiles`).catch(() => ({ profiles: [] })),
        apiGet(`/watchlists`).catch(() => ({ watchlists: [] })),
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

      const wls: PipelineWatchlist[] = watchlistsData.watchlists ?? [];
      setPipelineWatchlists(wls);
      const linkedWl = wls.find(w => w.source_pool_id === id);
      if (linkedWl) {
        setWatchlistId(linkedWl.id);
        setAssignedWatchlistId(linkedWl.id);
      } else {
        setWatchlistId("");
        setAssignedWatchlistId("");
      }

      // Load auto-refresh settings from overrides
      const ov = found.overrides ?? {};
      setAutoRefresh(Boolean(ov.auto_refresh));
      setAutoAdd(ov.auto_add !== false);
      setAutoRemove(Boolean(ov.auto_remove));
      setNotifyChanges(Boolean(ov.notify_on_changes));
      setMaxAssets(Number(ov.max_assets) || 0);
      setRadarEnabled(Boolean(ov.radar_enabled));
      setRadarMinHold(Number(ov.radar_min_hold_seconds ?? 300));
      setPmSync(Boolean(ov.pump_monitor_sync_enabled));
      setPmParams(p => ({
        min_score: Number(ov.pump_monitor_min_score ?? p.min_score),
        exit_consecutive_cycles: Number(ov.pump_monitor_exit_consecutive_cycles ?? p.exit_consecutive_cycles),
        min_hold_seconds: Number(ov.pump_monitor_min_hold_seconds ?? p.min_hold_seconds),
      }));
    } catch (e: any) {
      setError(e.message ?? "Failed to load pool.");
    }
    setLoading(false);
  }, [id]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // ── Save pool metadata (includes auto-refresh overrides) ──────────────────
  const handleSave = async () => {
    if (!name.trim()) return;
    if (radarEnabled && !radarMinHoldValid) {
      setError("Radar: a permanência mínima deve ser um inteiro entre 0 e 3600 segundos.");
      return;
    }
    if (pmSync && !pmValid) {
      setError("Pump Monitor: o score mínimo deve estar entre 0 e 100.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const currentOverrides = pool?.overrides ?? {};
      await apiFetch(`/pools/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          mode,
          market_type: marketType,
          is_active: isActive,
          profile_id: profileId || null,
          overrides: {
            ...currentOverrides,
            auto_refresh: autoRefresh,
            auto_add: autoAdd,
            auto_remove: autoRemove,
            notify_on_changes: notifyChanges,
            max_assets: maxAssets,
            radar_enabled: radarEnabled,
            ...(radarEnabled ? { radar_min_hold_seconds: radarMinHold } : {}),
            pump_monitor_sync_enabled: pmSync,
            ...(pmSync ? {
              observation_only: true,
              pump_monitor_min_score: pmParams.min_score,
              pump_monitor_exit_consecutive_cycles: pmParams.exit_consecutive_cycles,
              pump_monitor_min_hold_seconds: pmParams.min_hold_seconds,
            } : {}),
          },
        }),
      });

      // Update pipeline watchlist association if changed
      if (watchlistId !== assignedWatchlistId) {
        if (assignedWatchlistId) {
          await apiFetch(`/watchlists/${assignedWatchlistId}`, {
            method: "PUT",
            body: JSON.stringify({ source_pool_id: null }),
          });
        }
        if (watchlistId) {
          await apiFetch(`/watchlists/${watchlistId}`, {
            method: "PUT",
            body: JSON.stringify({ source_pool_id: id }),
          });
        }
      }

      await fetchAll();
    } catch (e: any) {
      setError(e.message ?? "Failed to save.");
    }
    setSaving(false);
  };

  // ── Autocomplete ───────────────────────────────────────────────────────────
  const handleSearchInput = (value: string) => {
    setNewCoin(value);
    setDiscoverResult(null);

    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (value.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const data = await apiGet(
          `/exchange/search?q=${encodeURIComponent(value.trim())}&market=${marketType}`
        );
        setSuggestions(data.results ?? []);
        setShowSuggestions(true);
      } catch {
        setSuggestions([]);
      } finally {
        setSearchLoading(false);
      }
    }, 300);
  };

  const handleSelectSuggestion = (sym: string) => {
    setNewCoin(sym);
    setSuggestions([]);
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  // ── Add coin ───────────────────────────────────────────────────────────────
  const handleAddCoin = async () => {
    const sym = newCoin.trim().toUpperCase();
    if (!sym) return;
    setAddingCoin(true);
    setSuggestions([]);
    setShowSuggestions(false);
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

  // ── Toggle is_tradable (Task #232 execution gate) ─────────────────────────
  const handleToggleTradable = async (coin: Coin, next: boolean) => {
    // Optimistic update — if the request fails we revert and surface
    // the error in the global error banner.
    setCoins((prev) =>
      prev.map((c) => (c.id === coin.id ? { ...c, is_tradable: next } : c))
    );
    try {
      await apiPost(`/pools/${id}/coins/${coin.symbol}/tradable`, {
        tradable: next,
      });
    } catch (e: any) {
      setCoins((prev) =>
        prev.map((c) =>
          c.id === coin.id ? { ...c, is_tradable: !next } : c
        )
      );
      setError(e.message ?? "Failed to update trading authorisation.");
    }
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

  // ── Discover assets ────────────────────────────────────────────────────────
  const handleDiscover = async () => {
    setDiscovering(true);
    setDiscoverResult(null);
    setError(null);
    try {
      const result = await apiFetch<DiscoverResult>(`/pools/${id}/discover`, {
        method: "POST",
      });
      setDiscoverResult(result);
      await fetchAll();
    } catch (e: any) {
      setError(e.message ?? "Discovery failed.");
    }
    setDiscovering(false);
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

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="label">Pipeline Watchlist</label>
              <select
                className="input"
                value={watchlistId}
                onChange={(e) => setWatchlistId(e.target.value)}
              >
                <option value="">Nenhuma Watchlist</option>
                {pipelineWatchlists.map((wl) => (
                  <option key={wl.id} value={wl.id}>
                    [{wl.level.toUpperCase()}] {wl.name}
                  </option>
                ))}
              </select>
              {assignedWatchlistId && assignedWatchlistId === watchlistId && (
                <p style={{ fontSize: "11px", color: "var(--color-profit)" }}>
                  ✓ Vinculada: {pipelineWatchlists.find(w => w.id === assignedWatchlistId)?.name}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <label className="label">Max Assets for Discovery</label>
              <input
                className="input"
                type="number"
                min={0}
                step={1}
                value={maxAssets}
                onChange={(e) => setMaxAssets(Math.max(0, parseInt(e.target.value, 10) || 0))}
                placeholder="0 = unlimited"
              />
              <p style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>
                Maximum assets to include during Discover/Scan. 0 = no limit.
              </p>
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

      {/* ── Auto-Refresh Settings ── */}
      <div className="card">
        <div className="card-header">
          <h3>Auto-Refresh</h3>
          <span style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>
            Discover assets automatically every 60 seconds
          </span>
        </div>
        <div className="card-body space-y-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={autoRefresh}
              onClick={() => setAutoRefresh((v) => !v)}
              className={`toggle ${autoRefresh ? "active" : ""}`}
            >
              <span className="knob" />
            </button>
            <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
              Auto-refresh every 60 seconds
            </span>
          </div>

          {autoRefresh && (
            <div
              style={{
                paddingLeft: "8px",
                borderLeft: "2px solid var(--border-subtle)",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
              }}
            >
              <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={autoAdd}
                  onChange={(e) => setAutoAdd(e.target.checked)}
                  style={{ accentColor: "var(--accent-primary)" }}
                />
                <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                  Auto-add new assets matching criteria
                </span>
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={autoRemove}
                  onChange={(e) => setAutoRemove(e.target.checked)}
                  style={{ accentColor: "var(--accent-primary)" }}
                />
                <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                  Auto-remove assets below criteria
                </span>
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={notifyChanges}
                  onChange={(e) => setNotifyChanges(e.target.checked)}
                  style={{ accentColor: "var(--accent-primary)" }}
                />
                <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                  Notify on changes
                </span>
              </label>
            </div>
          )}
        </div>
      </div>

      {/* ── Market Catalyst Radar ── */}
      {marketType === "spot" && (
        <div className="card">
          <div className="card-header">
            <h3>Market Catalyst Radar</h3>
            <span style={{ fontSize: "12px", color: "var(--text-tertiary)" }}>
              Sincronização com os sinais atuais do radar
            </span>
          </div>
          <div className="card-body space-y-3">
            <div className="flex items-center gap-3">
              <button
                type="button"
                role="switch"
                aria-checked={radarEnabled}
                onClick={() => setRadarEnabled((v) => !v)}
                className={`toggle ${radarEnabled ? "active" : ""}`}
              >
                <span className="knob" />
              </button>
              <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                Sincronizar ativos do Market Catalyst Radar
              </span>
            </div>
            <p style={{ fontSize: "12px", color: "var(--text-tertiary)", margin: 0 }}>
              Adds/removes assets automatically based on the top buy-pressure
              ranking from mdatahub. Requires a Market Catalyst Radar API key
              configured under Settings → General → Provedores de Dados de
              Mercado.
            </p>
            {radarEnabled && (
              <div role="status" data-testid="radar-feed-health" style={{ fontSize: "13px", color: pool?.overrides?.radar_feed_health?.status === "healthy" ? "var(--text-secondary)" : "var(--warning, #fbbf24)" }}>
                {pool?.overrides?.radar_feed_health?.status === "healthy"
                  ? "Radar sincronizado. Ativos fora da lista permanecem apenas para acompanhar trades abertos."
                  : pool?.overrides?.radar_feed_health?.status === "unavailable"
                    ? "Radar indisponível. Novas candidaturas estão suspensas; trades abertos continuam sendo acompanhados."
                    : "Aguardando confirmação do radar. Trades abertos continuam sendo acompanhados."}
              </div>
            )}
            {radarEnabled && (
              <label data-testid="radar-min-hold" style={{ display: "flex", flexDirection: "column", fontSize: "12px", color: "var(--text-secondary)", maxWidth: "420px" }}>
                Permanência mínima após o último sinal (s)
                <input className="input" type="number" min={0} max={3600} step={1} value={radarMinHold}
                  onChange={(e) => setRadarMinHold(Number(e.target.value))} style={{ width: "140px" }} />
                <span style={{ color: "var(--text-tertiary)" }}>
                  O ativo continua elegível do pool à L3 por este tempo depois de sair do radar, mesmo com Shadow aberto
                  (um segundo Shadow do mesmo ativo continua bloqueado). 0 = só enquanto estiver no radar.
                </span>
              </label>
            )}
          </div>
        </div>
      )}

      {/* ── Pump Monitor sync (observation only) ── */}
      {marketType === "spot" && (
        <div className="card" data-testid="pump-monitor-sync">
          <div className="card-header">
            <h3>Pump Monitor</h3>
            <span style={{ fontSize: "12px", color: "var(--warning, #fbbf24)" }}>
              Pool de observação — não alimenta execução, L3 nem bots
            </span>
          </div>
          <div className="card-body space-y-3">
            <div className="flex items-center gap-3">
              <button type="button" role="switch" aria-checked={pmSync}
                onClick={() => setPmSync((v) => !v)} className={`toggle ${pmSync ? "active" : ""}`}>
                <span className="knob" />
              </button>
              <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                Sincronizar ativos do Pump Monitor
              </span>
            </div>
            {pmSync && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "12px" }}>
                {([
                  ["min_score", "Pump Score mínimo"],
                  ["exit_consecutive_cycles", "Ciclos seguidos para sair"], ["min_hold_seconds", "Permanência mínima (s)"],
                ] as const).map(([key, label]) => (
                  <label key={key} style={{ display: "flex", flexDirection: "column", fontSize: "12px", color: "var(--text-secondary)" }}>
                    {label}
                    <input className="input" type="number" value={pmParams[key]}
                      min={key === "exit_consecutive_cycles" ? 1 : 0} max={key === "min_score" ? 100 : undefined}
                      step={key === "min_score" ? "any" : 1}
                      onChange={(e) => setPmParams((p) => ({ ...p, [key]: Number(e.target.value) }))}
                      style={{ width: "140px" }} />
                  </label>
                ))}
              </div>
            )}
            {pmSync && !pmValid && (
              <p role="alert" style={{ fontSize: "12px", color: "var(--warning, #fbbf24)", margin: 0 }}>
                O Pump Score mínimo deve estar entre 0 e 100.
              </p>
            )}
            {pmSync && (
              <div role="status" style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                {pool?.overrides?.pump_monitor_feed_health?.status === "healthy"
                  ? "Sincronizado com o Pump Score do Pump Monitor (a cada ciclo de 30 s)."
                  : pool?.overrides?.pump_monitor_feed_health?.status === "unavailable"
                    ? "Pump Monitor sem dados suficientes. A composição atual foi mantida."
                    : "Aguardando o primeiro ciclo do Pump Monitor."}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Asset List ── */}
      {pool && marketType === "spot" && <RadarFeedHistory poolId={id} poolName={pool.name} />}
      {pool && marketType === "spot" && pool.overrides?.pump_monitor_sync_enabled && (
        <RadarFeedHistory poolId={id} poolName={pool.name} source="pump_monitor" />
      )}
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

        {/* Add coin row + Discover button */}
        <div
          style={{
            padding: "12px 20px",
            borderBottom: "1px solid var(--border-subtle)",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
          }}
        >
          {/* Input row */}
          <div style={{ display: "flex", gap: "8px", position: "relative" }}>
            <div style={{ position: "relative", maxWidth: "260px", flex: 1 }}>
              <input
                ref={inputRef}
                className="input"
                style={{ width: "100%", paddingRight: searchLoading ? "32px" : undefined }}
                placeholder="e.g. BTC_USDT"
                value={newCoin}
                onChange={(e) => handleSearchInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAddCoin();
                  if (e.key === "Escape") setShowSuggestions(false);
                }}
                onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
                onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
                autoComplete="off"
              />
              {searchLoading && (
                <Loader2
                  className="w-3.5 h-3.5 animate-spin"
                  style={{
                    position: "absolute",
                    right: "10px",
                    top: "50%",
                    transform: "translateY(-50%)",
                    color: "var(--text-tertiary)",
                  }}
                />
              )}
              {/* Autocomplete dropdown */}
              {showSuggestions && suggestions.length > 0 && (
                <div
                  style={{
                    position: "absolute",
                    top: "calc(100% + 4px)",
                    left: 0,
                    right: 0,
                    background: "var(--bg-elevated)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-md)",
                    boxShadow: "var(--shadow-lg)",
                    zIndex: 50,
                    overflow: "hidden",
                  }}
                >
                  {suggestions.map((s) => (
                    <button
                      key={s.symbol}
                      type="button"
                      onMouseDown={() => handleSelectSuggestion(s.symbol)}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        padding: "8px 12px",
                        fontSize: "13px",
                        color: "var(--text-primary)",
                        background: "transparent",
                        border: "none",
                        cursor: "pointer",
                        borderBottom: "1px solid var(--border-subtle)",
                        fontFamily: "var(--font-mono)",
                      }}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-hover)";
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                      }}
                    >
                      {s.symbol}
                      <span style={{ color: "var(--text-tertiary)", fontSize: "11px", marginLeft: "8px" }}>
                        {s.market_type}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
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

          {/* Discover + Add Cryptos buttons */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <button
              className="btn btn-primary"
              onClick={handleDiscover}
              disabled={discovering || radarEnabled}
              title={radarEnabled ? "Os ativos deste pool são sincronizados pelo radar." : undefined}
              style={{ gap: "6px" }}
            >
              {discovering ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Search className="w-4 h-4" />
              )}
              {discovering ? "Discovering…" : "Discover Assets"}
            </button>

            <button
              className="btn btn-secondary"
              onClick={() => setShowAddCryptosModal(true)}
              style={{ gap: "6px" }}
            >
              <ListPlus className="w-4 h-4" />
              Adicionar Criptos Manualmente
            </button>

            {discoverResult && (
              <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                Found{" "}
                <strong style={{ color: "var(--text-primary)" }}>{discoverResult.found}</strong>{" "}
                assets. Added{" "}
                <strong style={{ color: "var(--color-profit)" }}>{discoverResult.added}</strong>{" "}
                new
                {discoverResult.removed > 0 && (
                  <>
                    {", removed "}
                    <strong style={{ color: "var(--color-loss)" }}>{discoverResult.removed}</strong>
                  </>
                )}
                {"."}
              </span>
            )}
          </div>
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
            <p>No assets added yet.<br />Type a symbol above and press Add, or use Discover Assets.</p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Market</th>
                <th>Status</th>
                <th title="Trading authorisation (Task #232) — separate from ingestion. Disabled by default; flip on to allow live execution for this symbol.">
                  Tradable
                </th>
                <th>Origin</th>
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
                      type="button"
                      role="switch"
                      aria-checked={coin.is_tradable}
                      disabled={!coin.is_active}
                      onClick={() => handleToggleTradable(coin, !coin.is_tradable)}
                      className={`toggle ${coin.is_tradable ? "active" : ""}`}
                      title={
                        !coin.is_active
                          ? "Activate the symbol first to authorise trading"
                          : coin.is_tradable
                            ? "Live trading authorised — click to revoke"
                            : "Click to authorise live trading for this symbol"
                      }
                      style={!coin.is_active ? { opacity: 0.4, cursor: "not-allowed" } : undefined}
                    >
                      <span className="knob" />
                    </button>
                  </td>
                  <td>
                    <span
                      className={`badge ${coin.origin === "discovered" ? "bullish" : coin.origin === "radar" ? "bullish" : "range"}`}
                      style={
                        coin.origin === "discovered"
                          ? { background: "var(--color-profit-muted)", color: "var(--color-profit)", borderColor: "var(--color-profit-border)" }
                          : coin.origin === "radar"
                            ? { background: "var(--color-warning-muted)", color: "var(--color-warning)", borderColor: "var(--color-warning)" }
                            : { background: "var(--accent-primary-muted)", color: "var(--accent-primary)", borderColor: "var(--accent-primary)" }
                      }
                    >
                      {coin.origin === "discovered" ? "Discovered" : coin.origin === "radar" ? "Radar" : "Manual"}
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

      {/* ── Add Cryptos Modal ── */}
      {showAddCryptosModal && (
        <AddCryptosModal
          poolId={id}
          poolMarketType={marketType}
          existingSymbols={coins.map((c) => c.symbol)}
          onClose={() => setShowAddCryptosModal(false)}
          onAdded={() => fetchAll()}
        />
      )}
    </div>
  );
}
