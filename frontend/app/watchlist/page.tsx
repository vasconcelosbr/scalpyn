'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { WatchlistTable } from '@/components/watchlist/WatchlistTable';
import { apiFetch } from '@/lib/api';
import { useWebSocket, getCurrentUserId } from '@/hooks/useWebSocket';
import {
  Plus,
  RefreshCw,
  Trash2,
  ChevronDown,
  ChevronRight,
  Settings,
  Layers,
} from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface PipelineWatchlist {
  id: string;
  name: string;
  level: string;
  source_pool_id: string | null;
  source_watchlist_id: string | null;
  profile_id: string | null;
  auto_refresh: boolean;
  filters_json: Record<string, any>;
  created_at: string | null;
  updated_at: string | null;
  asset_count: number;
}

interface PipelineAsset {
  id: string;
  watchlist_id: string;
  symbol: string;
  current_price: number | null;
  price_change_24h: number | null;
  volume_24h: number | null;
  market_cap: number | null;
  alpha_score: number | null;
  entered_at: string | null;
  previous_level: string | null;
  level_change_at: string | null;
  level_direction: string | null;
}

interface Pool {
  id: string;
  name: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const LEVEL_COLORS: Record<string, string> = {
  L1: 'bg-[#1A2744] text-[#60A5FA] border border-[#2563EB]/40',
  L2: 'bg-[#1A3A2A] text-[#34D399] border border-[#059669]/40',
  L3: 'bg-[#3A1A2A] text-[#F472B6] border border-[#DB2777]/40',
  custom: 'bg-[#1E1E28] text-[#94A3B8] border border-[#334155]/40',
};

function LevelBadge({ level }: { level: string }) {
  const cls = LEVEL_COLORS[level] ?? LEVEL_COLORS.custom;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {level}
    </span>
  );
}

function fmt(n: number | null, decimals = 2) {
  if (n == null) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals });
}

function fmtPrice(n: number | null) {
  if (n == null) return '—';
  if (n >= 1000) return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (n >= 1) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(6)}`;
}

function fmtChange(n: number | null) {
  if (n == null) return '—';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

// ── Create/Edit Modal ─────────────────────────────────────────────────────────

interface ModalProps {
  wl: Partial<PipelineWatchlist> | null;
  pools: Pool[];
  watchlists: PipelineWatchlist[];
  onClose: () => void;
  onSave: (data: Partial<PipelineWatchlist>) => Promise<void>;
}

function WatchlistModal({ wl, pools, watchlists, onClose, onSave }: ModalProps) {
  const isNew = !wl?.id;
  const [name, setName] = useState(wl?.name ?? '');
  const [level, setLevel] = useState(wl?.level ?? 'custom');
  const [sourcePoolId, setSourcePoolId] = useState(wl?.source_pool_id ?? '');
  const [sourceWatchlistId, setSourceWatchlistId] = useState(wl?.source_watchlist_id ?? '');
  const [minScore, setMinScore] = useState(String(wl?.filters_json?.min_score ?? ''));
  const [requireSignal, setRequireSignal] = useState(Boolean(wl?.filters_json?.require_signal));
  const [autoRefresh, setAutoRefresh] = useState(wl?.auto_refresh ?? true);
  const [saving, setSaving] = useState(false);

  const otherWatchlists = watchlists.filter((w) => w.id !== wl?.id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    const filters: Record<string, any> = {};
    if (minScore !== '') filters.min_score = parseFloat(minScore);
    if (requireSignal) filters.require_signal = true;
    await onSave({
      name,
      level,
      source_pool_id: sourcePoolId || null,
      source_watchlist_id: sourceWatchlistId || null,
      auto_refresh: autoRefresh,
      filters_json: filters,
    });
    setSaving(false);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[#0F1117] border border-[#1E2433] rounded-xl w-full max-w-md mx-4 shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1E2433]">
          <h2 className="text-base font-semibold text-[#E2E8F0]">
            {isNew ? 'New Pipeline Watchlist' : 'Edit Watchlist'}
          </h2>
          <button onClick={onClose} className="text-[#94A3B8] hover:text-[#E2E8F0] transition-colors text-xl leading-none">
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Name</label>
            <input
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. L2 Ranking"
              required
            />
          </div>

          <div>
            <label className="block text-xs text-[#64748B] mb-1">Level</label>
            <select
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={level}
              onChange={(e) => setLevel(e.target.value)}
            >
              <option value="L1">L1 — All pool assets</option>
              <option value="L2">L2 — Score filtered</option>
              <option value="L3">L3 — Signal + score</option>
              <option value="custom">Custom</option>
            </select>
          </div>

          <div>
            <label className="block text-xs text-[#64748B] mb-1">Source Pool (optional)</label>
            <select
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={sourcePoolId}
              onChange={(e) => { setSourcePoolId(e.target.value); if (e.target.value) setSourceWatchlistId(''); }}
            >
              <option value="">— None —</option>
              {pools.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {!sourcePoolId && (
            <div>
              <label className="block text-xs text-[#64748B] mb-1">Source Watchlist (optional)</label>
              <select
                className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
                value={sourceWatchlistId}
                onChange={(e) => setSourceWatchlistId(e.target.value)}
              >
                <option value="">— None —</option>
                {otherWatchlists.map((w) => (
                  <option key={w.id} value={w.id}>{w.name} ({w.level})</option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label className="block text-xs text-[#64748B] mb-1">Min Alpha Score (0–100)</label>
            <input
              type="number"
              min="0"
              max="100"
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              placeholder="Leave blank for no filter"
            />
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={requireSignal}
              onChange={(e) => setRequireSignal(e.target.checked)}
              className="rounded border-[#334155] accent-[#3B82F6]"
            />
            <span className="text-sm text-[#94A3B8]">Require signal score ≥ 50</span>
          </label>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-[#334155] accent-[#3B82F6]"
            />
            <span className="text-sm text-[#94A3B8]">Auto-refresh on load</span>
          </label>

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-lg border border-[#1E2433] text-sm text-[#94A3B8] hover:text-[#E2E8F0] hover:border-[#334155] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim()}
              className="flex-1 px-4 py-2 rounded-lg bg-[#3B82F6] text-sm font-medium text-white hover:bg-[#2563EB] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? 'Saving…' : isNew ? 'Create' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Watchlist Row (with expandable asset table) ───────────────────────────────

interface WatchlistRowProps {
  wl: PipelineWatchlist;
  pools: Pool[];
  allWatchlists: PipelineWatchlist[];
  onEdit: (wl: PipelineWatchlist) => void;
  onDelete: (id: string) => void;
  onRefreshed: () => void;
  liveDirections?: Record<string, string>;  // symbol → "up" | "down" (transient, 3s)
}

function WatchlistRow({ wl, pools, allWatchlists, onEdit, onDelete, onRefreshed, liveDirections = {} }: WatchlistRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [assets, setAssets] = useState<PipelineAsset[]>([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const sourceName = wl.source_pool_id
    ? pools.find((p) => p.id === wl.source_pool_id)?.name ?? 'Pool'
    : wl.source_watchlist_id
    ? allWatchlists.find((w) => w.id === wl.source_watchlist_id)?.name ?? 'Watchlist'
    : '—';

  const loadAssets = useCallback(async (triggerParentRefresh = false) => {
    setLoadingAssets(true);
    try {
      const data = await apiFetch<{ assets: PipelineAsset[] }>(`/watchlists/${wl.id}/assets`);
      setAssets(data.assets);
      if (triggerParentRefresh && data.assets.length > 0) {
        onRefreshed();
      }
    } catch {
      // ignore
    } finally {
      setLoadingAssets(false);
    }
  }, [wl.id, onRefreshed]);

  useEffect(() => {
    if (expanded) loadAssets(true);
  }, [expanded, loadAssets]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await apiFetch(`/watchlists/${wl.id}/refresh`, { method: 'POST' });
      await loadAssets();
      onRefreshed();
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  }

  async function handleDelete() {
    if (!confirm(`Delete "${wl.name}"?`)) return;
    setDeleting(true);
    try {
      await apiFetch(`/watchlists/${wl.id}`, { method: 'DELETE' });
      onDelete(wl.id);
    } catch {
      setDeleting(false);
    }
  }

  return (
    <div className="border border-[#1E2433] rounded-xl overflow-hidden bg-[#0A0B10]">
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-[#0F1117] transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-[#4B5563] transition-transform" style={{ transform: expanded ? 'rotate(0)' : 'rotate(-90deg)' }}>
          <ChevronDown size={16} />
        </span>
        <LevelBadge level={wl.level} />
        <span className="text-sm font-medium text-[#E2E8F0] flex-1">{wl.name}</span>
        <span className="text-xs text-[#4B5563]">from {sourceName}</span>
        {wl.filters_json?.min_score != null && (
          <span className="text-xs text-[#64748B] bg-[#1E2433] px-2 py-0.5 rounded">
            score ≥ {wl.filters_json.min_score}
          </span>
        )}
        {wl.filters_json?.require_signal && (
          <span className="text-xs text-[#64748B] bg-[#1E2433] px-2 py-0.5 rounded">signal</span>
        )}
        {/* Actions — stop propagation so clicks don't toggle expand */}
        <div className="flex items-center gap-1 ml-2" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            title="Refresh pipeline"
            className="p-1.5 rounded hover:bg-[#1E2433] text-[#64748B] hover:text-[#94A3B8] transition-colors disabled:opacity-40"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={() => onEdit(wl)}
            title="Edit"
            className="p-1.5 rounded hover:bg-[#1E2433] text-[#64748B] hover:text-[#94A3B8] transition-colors"
          >
            <Settings size={14} />
          </button>
          <button
            onClick={handleDelete}
            disabled={deleting}
            title="Delete"
            className="p-1.5 rounded hover:bg-[#2A1A1A] text-[#64748B] hover:text-[#F87171] transition-colors disabled:opacity-40"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {/* Asset table */}
      {expanded && (
        <div className="border-t border-[#1E2433]">
          {loadingAssets ? (
            <div className="px-4 py-6 text-center text-sm text-[#4B5563]">Loading assets…</div>
          ) : assets.length === 0 ? (
            <div className="px-4 py-6 text-center">
              <p className="text-sm text-[#4B5563]">No assets. Click refresh to resolve the pipeline.</p>
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="mt-3 px-4 py-1.5 text-xs rounded-lg bg-[#1E2433] text-[#94A3B8] hover:bg-[#263048] transition-colors disabled:opacity-40"
              >
                {refreshing ? 'Refreshing…' : 'Refresh Now'}
              </button>
            </div>
          ) : (
            <div className="table-scroll overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#1E2433] bg-[#0A0B10]">
                    <th className="px-4 py-2 text-left text-[#4B5563] font-medium">Symbol</th>
                    <th className="px-4 py-2 text-right text-[#4B5563] font-medium">Price</th>
                    <th className="px-4 py-2 text-right text-[#4B5563] font-medium">24h %</th>
                    <th className="px-4 py-2 text-right text-[#4B5563] font-medium">Volume 24h</th>
                    <th className="px-4 py-2 text-right text-[#4B5563] font-medium">Alpha</th>
                    <th className="px-4 py-2 text-right text-[#4B5563] font-medium">Direction</th>
                  </tr>
                </thead>
                <tbody>
                  {assets.map((asset) => {
                    const effectiveDirection = liveDirections[asset.symbol] ?? asset.level_direction;
                    const rowCls = effectiveDirection === 'up'
                      ? 'row-level-up'
                      : effectiveDirection === 'down'
                      ? 'row-level-down'
                      : '';
                    const changePos = (asset.price_change_24h ?? 0) >= 0;
                    return (
                      <tr
                        key={asset.id}
                        className={`border-b border-[#1E2433]/50 hover:bg-[#0F1117] transition-colors ${rowCls}`}
                      >
                        <td className="px-4 py-2.5 font-medium text-[#E2E8F0]">{asset.symbol}</td>
                        <td className="px-4 py-2.5 text-right text-[#94A3B8]">{fmtPrice(asset.current_price)}</td>
                        <td className={`px-4 py-2.5 text-right font-medium ${changePos ? 'text-[#34D399]' : 'text-[#F87171]'}`}>
                          {fmtChange(asset.price_change_24h)}
                        </td>
                        <td className="px-4 py-2.5 text-right text-[#64748B]">
                          {asset.volume_24h != null ? `$${(asset.volume_24h / 1_000_000).toFixed(1)}M` : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <span className={`font-semibold ${(asset.alpha_score ?? 0) >= 75 ? 'text-[#34D399]' : (asset.alpha_score ?? 0) >= 50 ? 'text-[#FBBF24]' : 'text-[#94A3B8]'}`}>
                            {asset.alpha_score != null ? asset.alpha_score.toFixed(1) : '—'}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          {asset.level_direction === 'up' && (
                            <span className="text-[#34D399] font-bold">↑ up</span>
                          )}
                          {asset.level_direction === 'down' && (
                            <span className="text-[#F87171] font-bold">↓ down</span>
                          )}
                          {!asset.level_direction && <span className="text-[#4B5563]">—</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Pipeline Tab ──────────────────────────────────────────────────────────────

function PipelineTab() {
  const [watchlists, setWatchlists] = useState<PipelineWatchlist[]>([]);
  const [pools, setPools] = useState<Pool[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalWl, setModalWl] = useState<Partial<PipelineWatchlist> | null>(null);
  const [showModal, setShowModal] = useState(false);

  // ── Live level-change highlights (transient, 3s per symbol) ──────────────
  const [liveDirections, setLiveDirections] = useState<Record<string, string>>({});
  const clearTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const userId = typeof window !== 'undefined' ? getCurrentUserId() : undefined;
  const { lastMessage } = useWebSocket('alerts', userId);

  useEffect(() => {
    if (!lastMessage || lastMessage.type !== 'level_change') return;
    const { symbol, direction } = lastMessage as { type: string; symbol: string; direction: string };
    if (!symbol || !direction) return;

    // Apply highlight
    setLiveDirections((prev) => ({ ...prev, [symbol]: direction }));

    // Clear after 3 seconds
    if (clearTimers.current[symbol]) clearTimeout(clearTimers.current[symbol]);
    clearTimers.current[symbol] = setTimeout(() => {
      setLiveDirections((prev) => {
        const next = { ...prev };
        delete next[symbol];
        return next;
      });
    }, 3000);
  }, [lastMessage]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      Object.values(clearTimers.current).forEach(clearTimeout);
    };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [wlData, poolData] = await Promise.all([
        apiFetch<{ watchlists: PipelineWatchlist[] }>('/watchlists'),
        apiFetch<{ pools: Pool[] }>('/pools'),
      ]);
      setWatchlists(wlData.watchlists);
      setPools(poolData.pools ?? []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function openCreate() { setModalWl({}); setShowModal(true); }
  function openEdit(wl: PipelineWatchlist) { setModalWl(wl); setShowModal(true); }
  function closeModal() { setShowModal(false); setModalWl(null); }

  async function handleSave(data: Partial<PipelineWatchlist>) {
    if (modalWl?.id) {
      const updated = await apiFetch<PipelineWatchlist>(`/watchlists/${modalWl.id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
      setWatchlists((prev) => prev.map((w) => w.id === updated.id ? updated : w));
    } else {
      const created = await apiFetch<PipelineWatchlist>('/watchlists', {
        method: 'POST',
        body: JSON.stringify(data),
      });
      setWatchlists((prev) => [...prev, created]);
    }
    closeModal();
  }

  function handleDelete(id: string) {
    setWatchlists((prev) => prev.filter((w) => w.id !== id));
  }

  const byLevel = (level: string) => watchlists.filter((w) => w.level === level);
  const customWls = watchlists.filter((w) => !['L1', 'L2', 'L3'].includes(w.level));

  return (
    <div className="space-y-6">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-[#64748B]">
          {watchlists.length === 0
            ? 'No pipeline watchlists yet. Create one to start filtering assets.'
            : `${watchlists.length} watchlist${watchlists.length !== 1 ? 's' : ''}`}
        </p>
        <button
          onClick={openCreate}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[#3B82F6] text-sm font-medium text-white hover:bg-[#2563EB] transition-colors"
        >
          <Plus size={14} />
          New Watchlist
        </button>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 rounded-xl bg-[#0F1117] border border-[#1E2433] animate-pulse" />
          ))}
        </div>
      ) : watchlists.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-[#1E2433] rounded-xl">
          <Layers size={40} className="mx-auto text-[#1E2433] mb-4" />
          <p className="text-[#4B5563] text-sm mb-4">Build your institutional asset funnel</p>
          <button
            onClick={openCreate}
            className="px-6 py-2.5 rounded-lg bg-[#1E2433] text-sm text-[#94A3B8] hover:bg-[#263048] hover:text-[#E2E8F0] transition-colors"
          >
            Create first watchlist
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          {(['L1', 'L2', 'L3'] as const).map((lvl) => {
            const lvlWls = byLevel(lvl);
            if (lvlWls.length === 0) return null;
            const totalAssets = lvlWls.reduce((sum, w) => sum + (w.asset_count ?? 0), 0);
            return (
              <div key={lvl}>
                <div className="flex items-center gap-2 mb-3">
                  <LevelBadge level={lvl} />
                  <span className="text-xs text-[#4B5563]">{lvlWls.length} watchlist{lvlWls.length !== 1 ? 's' : ''}</span>
                  {totalAssets > 0 && (
                    <span className="ml-1 px-2 py-0.5 rounded-full text-xs font-medium bg-[#1E2433] text-[#94A3B8] border border-[#2A3448]">
                      {totalAssets} ativo{totalAssets !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
                <div className="space-y-2">
                  {lvlWls.map((wl) => (
                    <WatchlistRow
                      key={wl.id}
                      wl={wl}
                      pools={pools}
                      allWatchlists={watchlists}
                      onEdit={openEdit}
                      onDelete={handleDelete}
                      onRefreshed={load}
                      liveDirections={liveDirections}
                    />
                  ))}
                </div>
              </div>
            );
          })}
          {customWls.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <LevelBadge level="custom" />
                <span className="text-xs text-[#4B5563]">{customWls.length} watchlist{customWls.length !== 1 ? 's' : ''}</span>
              </div>
              <div className="space-y-2">
                {customWls.map((wl) => (
                  <WatchlistRow
                    key={wl.id}
                    wl={wl}
                    pools={pools}
                    allWatchlists={watchlists}
                    onEdit={openEdit}
                    onDelete={handleDelete}
                    onRefreshed={load}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {showModal && (
        <WatchlistModal
          wl={modalWl}
          pools={pools}
          watchlists={watchlists}
          onClose={closeModal}
          onSave={handleSave}
        />
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

type Tab = 'scanner' | 'pipeline';

export default function WatchlistPage() {
  const [activeTab, setActiveTab] = useState<Tab>('scanner');

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[#E2E8F0]">Watchlist</h1>
          <p className="text-[#94A3B8] mt-1 text-sm">
            {activeTab === 'scanner'
              ? 'Real-time Alpha Score rankings and technical indicators.'
              : '4-level institutional asset funnel — Pool → L1 → L2 → L3.'}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="page-tabs">
        <button
          className={`page-tab${activeTab === 'scanner' ? ' active' : ''}`}
          onClick={() => setActiveTab('scanner')}
        >
          Market Scanner
        </button>
        <button
          className={`page-tab${activeTab === 'pipeline' ? ' active' : ''}`}
          onClick={() => setActiveTab('pipeline')}
        >
          Pipeline
        </button>
      </div>

      {/* Tab content */}
      {activeTab === 'scanner' && <WatchlistTable />}
      {activeTab === 'pipeline' && <PipelineTab />}
    </div>
  );
}
