'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { WatchlistTable } from '@/components/watchlist/WatchlistTable';
import {
  WatchlistDecisionTable,
  type RejectedAssetItem,
} from '@/components/watchlist/RejectedAssetTable';
import { apiFetch } from '@/lib/api';
import { useWebSocket, getCurrentUserId } from '@/hooks/useWebSocket';
import {
  Plus,
  RefreshCw,
  Trash2,
  ChevronDown,
  Settings,
  Layers,
  Zap,
  BookOpen,
  ArrowLeftRight,
  Clock,
} from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Profile {
  id: string;
  name: string;
  description?: string;
  profile_role?: string | null;
  config?: {
    filters?:        { conditions?: unknown[] };
    signals?:        { conditions?: unknown[] };
    entry_triggers?: { conditions?: unknown[] };
    scoring?:        { weights?: Record<string, number> };
    block_rules?:    { blocks?: Array<{ enabled?: boolean }> };
  };
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface PipelineWatchlist {
  id: string;
  name: string;
  level: string;
  market_mode?: string;  // 'spot' | 'futures'
  source_pool_id: string | null;
  source_watchlist_id: string | null;
  profile_id: string | null;
  profile_name?: string | null;
  auto_refresh: boolean;
  filters_json: {
    min_alpha_score?: number;
    [key: string]: unknown;
  };
  last_scanned_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  asset_count: number;
}

type WatchlistDetailTab = 'approved' | 'rejected';

interface Pool {
  id: string;
  name: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const LEVEL_COLORS: Record<string, string> = {
  POOL: 'bg-[#3A3117] text-[#FBBF24] border border-[#D97706]/35',
  L1: 'bg-[#1A2744] text-[#60A5FA] border border-[#2563EB]/40',
  L2: 'bg-[#1A3A2A] text-[#34D399] border border-[#059669]/40',
  L3: 'bg-[#3A1A2A] text-[#F472B6] border border-[#DB2777]/40',
  custom: 'bg-[#1E1E28] text-[#94A3B8] border border-[#334155]/40',
};

const WATCHLIST_LEVEL_OPTIONS = [
  { value: 'POOL', label: 'POOL — Universe filter' },
  { value: 'L1', label: 'L1 — All pool assets' },
  { value: 'L2', label: 'L2 — Score filtered' },
  { value: 'L3', label: 'L3 — Signal + score' },
  { value: 'custom', label: 'Custom' },
] as const;

function resolveWatchlistLevel(
  wl: Partial<Pick<PipelineWatchlist, 'level' | 'source_pool_id' | 'profile_id'>> | undefined,
  profiles: Profile[],
) {
  const normalized = (wl?.level || '').toUpperCase();
  if (['POOL', 'L1', 'L2', 'L3'].includes(normalized)) return normalized;
  const profile = profiles.find((item) => item.id === wl?.profile_id);
  const filterConditions = profile?.config?.filters?.conditions ?? [];
  return wl?.source_pool_id && filterConditions.length > 0 ? 'POOL' : 'custom';
}

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

function fmtVol(n: number | null) {
  if (n == null) return '—';
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtChange(n: number | null) {
  if (n == null) return '—';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

const WATCHLIST_POLL_MS = 60_000;
// Alpha score is normalized platform-wide to the 0-100 range.
const ALPHA_SCORE_FILTER_RANGE = {
  min: 0,
  max: 100,
  step: 0.1,
} as const;

/** Relative time label (e.g. "2m ago", "1h ago") */
function timeAgo(isoStr: string | null | undefined): string {
  if (!isoStr) return '—';
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'agora';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h${mins % 60 > 0 ? `${mins % 60}m` : ''}`;
  return `${Math.floor(hrs / 24)}d`;
}

/** True when last_scanned_at is >10 min old (stale pipeline). */
function isScanStale(isoStr: string | null | undefined): boolean {
  if (!isoStr) return true;
  return Date.now() - new Date(isoStr).getTime() > 10 * 60_000;
}

// ── Indicator Cell ────────────────────────────────────────────────────────────

function IndicatorCell({ value }: { value: number | boolean | string | null | undefined }) {
  if (value === null || value === undefined) {
    return <span className="text-[#4B5563]">—</span>;
  }
  if (typeof value === 'boolean') {
    return value
      ? <span className="text-[#34D399] font-semibold">✓</span>
      : <span className="text-[#F87171]">✗</span>;
  }
  if (typeof value === 'string') {
    // macd_signal: "positive" / "negative"
    if (value === '9>50>200') return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-[#34D399]/10 text-[#34D399] border border-[#34D399]/20 whitespace-nowrap">
        9›50›200
      </span>
    );
    if (value === '9>50') return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 whitespace-nowrap">
        9›50
      </span>
    );
    if (value === '9<50<200') return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-[#F87171]/10 text-[#F87171] border border-[#F87171]/20 whitespace-nowrap">
        9‹ 50‹200
      </span>
    );
    if (value === 'mix') return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#1E2433] text-[#64748B] border border-[#334155]/40 whitespace-nowrap">
        mix
      </span>
    );
    if (value === 'positive') return <span className="text-[#34D399] font-semibold">{value}</span>;
    if (value === 'negative') return <span className="text-[#F87171]">{value}</span>;
    return <span className="text-[#94A3B8]">{value}</span>;
  }
  // number
  const abs = Math.abs(value);
  const display = abs >= 1_000_000 ? fmtVol(value) : fmt(value, abs >= 10 ? 1 : 3);
  return <span className="text-[#CBD5E1]">{display}</span>;
}

// ── Create/Edit Modal ─────────────────────────────────────────────────────────

interface ModalProps {
  wl: Partial<PipelineWatchlist> | null;
  pools: Pool[];
  watchlists: PipelineWatchlist[];
  profiles: Profile[];
  onClose: () => void;
  onSave: (data: Partial<PipelineWatchlist>) => Promise<void>;
}

const ROLE_LABEL: Record<string, string> = {
  universe_filter: 'Universe Filter',
  primary_filter: 'Primary Filter',
  score_engine: 'Score Engine',
  acquisition_queue: 'Acquisition Queue',
};

function ProfilePreview({ profile }: { profile: Profile }) {
  const filterCount  = profile.config?.filters?.conditions?.length ?? 0;
  const signalCount  = profile.config?.signals?.conditions?.length ?? 0;
  const triggerCount = profile.config?.entry_triggers?.conditions?.length ?? 0;
  const blockCount   = profile.config?.block_rules?.blocks?.filter((b) => b.enabled)?.length ?? 0;
  const weights      = profile.config?.scoring?.weights ?? {};

  return (
    <div className="mt-2 p-3 rounded-lg bg-[#060A12] border border-[#1E2433] space-y-2.5">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold text-[#4B5563] uppercase tracking-wider">
        <BookOpen size={10} />
        Profile Preview
      </div>

      {/* Rule counts */}
      <div className="flex flex-wrap gap-1.5">
        {filterCount > 0 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-[#0D1F36] text-[#60A5FA] border border-[#1D4ED8]/30">
            {filterCount} filter{filterCount !== 1 ? 's' : ''}
          </span>
        )}
        {signalCount > 0 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-[#0D2B1F] text-[#34D399] border border-[#059669]/30">
            {signalCount} signal{signalCount !== 1 ? 's' : ''}
          </span>
        )}
        {triggerCount > 0 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-[#2D1B00] text-[#FBBF24] border border-[#D97706]/30">
            {triggerCount} trigger{triggerCount !== 1 ? 's' : ''}
          </span>
        )}
        {blockCount > 0 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-[#2A0A0A] text-[#F87171] border border-[#991B1B]/30">
            {blockCount} block rule{blockCount !== 1 ? 's' : ''}
          </span>
        )}
        {filterCount === 0 && signalCount === 0 && triggerCount === 0 && (
          <span className="text-[10px] text-[#334155]">Sem regras configuradas ainda</span>
        )}
      </div>

      {/* Scoring weights */}
      {Object.keys(weights).length > 0 && (
        <div className="space-y-1">
          {Object.entries(weights).map(([cat, w]) => (
            <div key={cat} className="flex items-center gap-2">
              <span className="text-[10px] text-[#4B5563] w-24 capitalize">{cat.replace('_', ' ')}</span>
              <div className="flex-1 h-1 bg-[#1A2035] rounded-full overflow-hidden">
                <div
                  className="h-full bg-[#3B82F6] rounded-full"
                  style={{ width: `${Math.min(100, Number(w))}%` }}
                />
              </div>
              <span className="text-[10px] text-[#64748B] font-mono w-6 text-right">{w}</span>
            </div>
          ))}
        </div>
      )}

      {profile.description && (
        <p className="text-[10px] text-[#4B5563] italic leading-relaxed">{profile.description}</p>
      )}
    </div>
  );
}

function WatchlistModal({ wl, pools, watchlists, profiles, onClose, onSave }: ModalProps) {
  const isNew = !wl?.id;
  const existingFilters = wl?.filters_json || {};
  const [name, setName] = useState(wl?.name ?? '');
  const [level, setLevel] = useState(isNew ? 'custom' : resolveWatchlistLevel(wl, profiles));
  const [marketMode, setMarketMode] = useState<'spot' | 'futures'>(
    wl?.market_mode === 'futures' ? 'futures' : 'spot',
  );
  const [sourcePoolId, setSourcePoolId] = useState(wl?.source_pool_id ?? '');
  const [sourceWatchlistId, setSourceWatchlistId] = useState(wl?.source_watchlist_id ?? '');
  const [profileId, setProfileId] = useState(wl?.profile_id ?? '');
  const [autoRefresh, setAutoRefresh] = useState(wl?.auto_refresh ?? true);
  const [minAlphaScore, setMinAlphaScore] = useState(
    typeof existingFilters.min_alpha_score === 'number' ? existingFilters.min_alpha_score.toString() : '',
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const selectedProfile = profiles.find((p) => p.id === profileId) ?? null;

  function handleLevelChange(newLevel: string) {
    setLevel(newLevel);
    if (newLevel === 'POOL') {
      setSourceWatchlistId('');
    } else if (newLevel === 'L1' || newLevel === 'L2' || newLevel === 'L3') {
      setSourcePoolId('');
    }
  }

  const otherWatchlists = watchlists.filter((w) => w.id !== wl?.id);
  const sourceConfigError = (() => {
    if (level === 'POOL') {
      if (!sourcePoolId) return 'POOL exige Source Pool.';
      if (sourceWatchlistId) return 'POOL não aceita Source Watchlist.';
      return null;
    }
    if (level === 'L1' || level === 'L2' || level === 'L3') {
      if (!sourceWatchlistId) return `${level} exige Source Watchlist.`;
      if (sourcePoolId) return `${level} não aceita Source Pool.`;
      return null;
    }
    return null;
  })();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (sourceConfigError) {
      setSaveError(sourceConfigError);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const trimmedMinAlphaScore = minAlphaScore.trim();
      const parsedMinAlphaScore = trimmedMinAlphaScore === '' ? null : Number.parseFloat(trimmedMinAlphaScore);
      if (
        parsedMinAlphaScore !== null &&
        (
          Number.isNaN(parsedMinAlphaScore) ||
          parsedMinAlphaScore < ALPHA_SCORE_FILTER_RANGE.min ||
          parsedMinAlphaScore > ALPHA_SCORE_FILTER_RANGE.max
        )
      ) {
        setSaveError(
          `Minimum Score Filter must be between ${ALPHA_SCORE_FILTER_RANGE.min} and ${ALPHA_SCORE_FILTER_RANGE.max}.`,
        );
        setSaving(false);
        return;
      }
      const nextFilters = { ...existingFilters };
      if (parsedMinAlphaScore === null) {
        delete nextFilters.min_alpha_score;
      } else {
        nextFilters.min_alpha_score = parsedMinAlphaScore;
      }
      await onSave({
        name,
        level,
        market_mode: marketMode,
        source_pool_id: sourcePoolId || null,
        source_watchlist_id: sourceWatchlistId || null,
        profile_id: profileId || null,
        auto_refresh: autoRefresh,
        filters_json: nextFilters,
      });
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save watchlist settings. Please try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-[#0F1117] border border-[#1E2433] rounded-xl w-full max-w-lg mx-4 shadow-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#1E2433] sticky top-0 bg-[#0F1117] z-10">
          <h2 className="text-base font-semibold text-[#E2E8F0]">
            {isNew ? 'New Pipeline Watchlist' : 'Edit Watchlist'}
          </h2>
          <button onClick={onClose} className="text-[#94A3B8] hover:text-[#E2E8F0] transition-colors text-xl leading-none">
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Name</label>
            <input
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. L2 Ranking"
              required
              data-testid="watchlist-name-input"
            />
          </div>

          {/* Level */}
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Level</label>
            <select
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={level}
              onChange={(e) => handleLevelChange(e.target.value)}
              data-testid="watchlist-level-select"
            >
              {WATCHLIST_LEVEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>

          {/* Source Pool */}
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Source Pool <span className="text-[#4B5563]">(para POOL)</span></label>
            <select
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={sourcePoolId}
              onChange={(e) => { setSourcePoolId(e.target.value); if (e.target.value) setSourceWatchlistId(''); }}
              data-testid="watchlist-pool-select"
            >
              <option value="">— None —</option>
              {pools.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Source Watchlist */}
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Source Watchlist <span className="text-[#4B5563]">(para L1 / L2 / L3)</span></label>
            <select
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={sourceWatchlistId}
              onChange={(e) => { setSourceWatchlistId(e.target.value); if (e.target.value) setSourcePoolId(''); }}
              disabled={!!sourcePoolId}
              data-testid="watchlist-source-select"
            >
              <option value="">— None —</option>
              {otherWatchlists.map((w) => (
                <option key={w.id} value={w.id}>[{resolveWatchlistLevel(w, profiles)}] {w.name}</option>
              ))}
            </select>
            {sourcePoolId && (
              <p className="text-xs text-[#4B5563] mt-1">Limpe o Source Pool acima para usar uma watchlist como fonte.</p>
            )}
          </div>

          {/* ── Profile — central source of truth ── */}
          <div className="border-t border-[#1E2433] pt-4">
            <div className="flex items-center gap-2 mb-1">
              <Zap size={12} className="text-[#FBBF24]" />
              <label className="text-xs font-semibold text-[#94A3B8]">
                Strategy Profile <span className="text-[#F87171] ml-0.5">*</span>
              </label>
            </div>
            <p className="text-[10px] text-[#4B5563] mb-2">
              O Profile é a fonte única de regras — filtros, scoring e sinais são aplicados automaticamente.
            </p>
            <select
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#FBBF24]"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              data-testid="watchlist-profile-select"
            >
              <option value="">— Selecione um Profile —</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.profile_role ? ` · ${ROLE_LABEL[p.profile_role] ?? p.profile_role}` : ''}
                </option>
              ))}
            </select>

            {/* Profile preview */}
            {selectedProfile && <ProfilePreview profile={selectedProfile} />}

            {profiles.length === 0 && (
              <p className="text-[11px] text-[#F87171] mt-1.5">
                Nenhum profile encontrado. Crie um em /profiles antes de configurar a watchlist.
              </p>
            )}
          </div>

          <div>
            <label htmlFor="watchlist-minimum-score-input" className="block text-xs text-[#64748B] mb-1">
              Minimum Score Filter
            </label>
            <input
              id="watchlist-minimum-score-input"
              type="number"
              min={ALPHA_SCORE_FILTER_RANGE.min}
              max={ALPHA_SCORE_FILTER_RANGE.max}
              step={ALPHA_SCORE_FILTER_RANGE.step}
              inputMode="decimal"
              className="w-full bg-[#0A0B10] border border-[#1E2433] rounded-lg px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none focus:border-[#3B82F6]"
              value={minAlphaScore}
              onChange={(e) => setMinAlphaScore(e.target.value)}
              placeholder="Ex: 60.0"
              data-testid="watchlist-minimum-score-input"
              aria-describedby="watchlist-minimum-score-help"
              aria-invalid={!!saveError && saveError.includes('Minimum Score Filter')}
              aria-errormessage={saveError && saveError.includes('Minimum Score Filter') ? 'watchlist-minimum-score-error' : undefined}
            />
            <p id="watchlist-minimum-score-help" className="mt-1 text-[10px] text-[#4B5563]">
              Optional. Leave empty to disable this filter. Valid range: {ALPHA_SCORE_FILTER_RANGE.min} to {ALPHA_SCORE_FILTER_RANGE.max}.
            </p>
          </div>

          {/* ── Futures Mode toggle ── */}
          <div className="border-t border-[#1E2433] pt-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <ArrowLeftRight size={12} className="text-[#F472B6]" />
                  <span className="text-xs font-semibold text-[#94A3B8]">Futures Mode</span>
                  {marketMode === 'futures' && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#F472B6]/10 text-[#F472B6] border border-[#F472B6]/25">
                      FUTURES
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-[#4B5563] mt-0.5">
                  {marketMode === 'futures'
                    ? 'Score duplo LONG/SHORT independente. Direção restrita ao L3.'
                    : 'Score Alpha padrão (spot). Ative para dual LONG/SHORT scoring.'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setMarketMode((m) => m === 'futures' ? 'spot' : 'futures')}
                className={`relative w-10 h-5 rounded-full transition-colors ${
                  marketMode === 'futures' ? 'bg-[#F472B6]' : 'bg-[#1E2433]'
                }`}
                aria-label="Toggle Futures Mode"
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                    marketMode === 'futures' ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-[#334155] accent-[#3B82F6]"
            />
            <span className="text-sm text-[#94A3B8]">Auto-refresh on load</span>
          </label>

          {saveError && (
            <p
              id={saveError.includes('Minimum Score Filter') ? 'watchlist-minimum-score-error' : undefined}
              className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2"
            >
              {saveError}
            </p>
          )}
          {!saveError && sourceConfigError && (
            <p className="text-xs text-amber-400 bg-amber-400/10 border border-amber-400/20 rounded-lg px-3 py-2">{sourceConfigError}</p>
          )}

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
              disabled={saving || !name.trim() || !!sourceConfigError}
              className="flex-1 px-4 py-2 rounded-lg bg-[#3B82F6] text-sm font-medium text-white hover:bg-[#2563EB] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="watchlist-save-btn"
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
  profiles: Profile[];
  onEdit: (wl: PipelineWatchlist) => void;
  onDelete: (id: string) => void;
  onRefreshed: () => void;
  refreshTick: number;
  liveDirections?: Record<string, string>;
}

// ── Futures Asset Table ──────────────────────────────────────────────────────
interface FuturesAsset {
  symbol: string;
  score_long: number | null;
  score_short: number | null;
  confidence_score: number | null;
  futures_direction: 'LONG' | 'SHORT' | null;
  entry_long_blocked: boolean;
  entry_short_blocked: boolean;
  price?: number | null;
  change_24h?: number | null;
}

function ScorePill({ value, color }: { value: number | null; color: string }) {
  if (value == null) return <span className="text-[#334155] text-[10px]">—</span>;
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 rounded-full bg-[#1A2035] overflow-hidden">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${Math.min(100, value)}%` }}
        />
      </div>
      <span className="text-[10px] text-[#94A3B8] tabular-nums w-7 text-right">{value.toFixed(0)}</span>
    </div>
  );
}

function FuturesAssetTable({
  assets,
  loading,
  hideNeutral,
  onToggleHideNeutral,
}: {
  assets: FuturesAsset[];
  loading: boolean;
  hideNeutral: boolean;
  onToggleHideNeutral: () => void;
}) {
  // Server already filters when hide_neutral=true; no client-side filtering needed.
  const displayed = assets;

  if (loading) {
    return (
      <div className="px-4 py-6 text-center text-sm text-[#4B5563] flex items-center justify-center gap-2">
        <RefreshCw size={13} className="animate-spin" />
        Carregando scores futures…
      </div>
    );
  }

  return (
    <div>
      {/* controls bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[#1A2035]">
        <span className="text-[10px] text-[#4B5563]">{displayed.length} asset{displayed.length !== 1 ? 's' : ''}</span>
        <button
          type="button"
          onClick={onToggleHideNeutral}
          className={`flex items-center gap-1.5 text-[10px] px-2 py-1 rounded transition-colors ${
            hideNeutral
              ? 'bg-[#F472B6]/10 text-[#F472B6] border border-[#F472B6]/25'
              : 'text-[#4B5563] hover:text-[#94A3B8] border border-[#1E2433]'
          }`}
        >
          <ArrowLeftRight size={9} />
          {hideNeutral ? 'Mostrar Neutros' : 'Ocultar Neutros'}
        </button>
      </div>

      {displayed.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-[#4B5563]">
          {hideNeutral ? 'Nenhum asset com direção definida.' : 'Nenhum asset disponível.'}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs min-w-[700px]">
            <thead>
              <tr className="border-b border-[#1A2035] bg-[#060810]">
                <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium">Symbol</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium">Direção</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium min-w-[110px]">Score LONG</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium min-w-[110px]">Score SHORT</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563] font-medium min-w-[110px]">Confidence</th>
                <th className="px-3 py-2.5 text-center text-[#4B5563] font-medium">Entry Long</th>
                <th className="px-3 py-2.5 text-center text-[#4B5563] font-medium">Entry Short</th>
                <th className="px-3 py-2.5 text-right text-[#4B5563] font-medium">Preço</th>
                <th className="px-3 py-2.5 text-right text-[#4B5563] font-medium">24h %</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map((asset) => {
                const dir = asset.futures_direction;
                const dirCls = dir === 'LONG'  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
                             : dir === 'SHORT' ? 'bg-red-500/10 text-red-400 border-red-500/25'
                             : 'bg-[#1E2433] text-[#4B5563] border-[#334155]';
                const chg = asset.change_24h;
                const chgCls = chg == null ? 'text-[#4B5563]' : chg >= 0 ? 'text-emerald-400' : 'text-red-400';
                return (
                  <tr key={asset.symbol} className="border-b border-[#1A2035]/60 hover:bg-[#0D1118] transition-colors">
                    <td className="px-3 py-2.5 font-semibold text-[#E2E8F0] tracking-wide">{asset.symbol}</td>
                    <td className="px-3 py-2.5">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded border font-semibold ${dirCls}`}>
                        {dir ?? 'NEUTRO'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5"><ScorePill value={asset.score_long} color="bg-emerald-500" /></td>
                    <td className="px-3 py-2.5"><ScorePill value={asset.score_short} color="bg-red-500" /></td>
                    <td className="px-3 py-2.5"><ScorePill value={asset.confidence_score} color="bg-[#F472B6]" /></td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-[9px] font-medium ${asset.entry_long_blocked ? 'text-red-400' : 'text-emerald-400'}`}>
                        {asset.entry_long_blocked ? '🔒 Bloq.' : '✓ Open'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`text-[9px] font-medium ${asset.entry_short_blocked ? 'text-red-400' : 'text-emerald-400'}`}>
                        {asset.entry_short_blocked ? '🔒 Bloq.' : '✓ Open'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right text-[#94A3B8]">
                      {asset.price != null ? `$${asset.price.toLocaleString(undefined, { maximumFractionDigits: 4 })}` : '—'}
                    </td>
                    <td className={`px-3 py-2.5 text-right tabular-nums ${chgCls}`}>
                      {chg != null ? `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%` : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function WatchlistRow({ wl, pools, allWatchlists, profiles, onEdit, onDelete, onRefreshed, refreshTick, liveDirections = {} }: WatchlistRowProps) {
  const displayLevel = resolveWatchlistLevel(wl, profiles);
  const isFutures = wl.market_mode === 'futures';
  const [expanded, setExpanded] = useState(false);
  const [detailTab, setDetailTab] = useState<WatchlistDetailTab>('approved');
  const [approvedItems, setApprovedItems] = useState<RejectedAssetItem[]>([]);
  const [rejectedItems, setRejectedItems] = useState<RejectedAssetItem[]>([]);
  const [futuresAssets, setFuturesAssets] = useState<FuturesAsset[]>([]);
  const [hideNeutral, setHideNeutral] = useState(false);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [loadingRejected, setLoadingRejected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const assetsRequestInFlight = useRef(false);

  const sourceName = wl.source_pool_id
    ? pools.find((p) => p.id === wl.source_pool_id)?.name ?? 'Pool'
    : wl.source_watchlist_id
    ? allWatchlists.find((w) => w.id === wl.source_watchlist_id)?.name ?? 'Watchlist'
    : '—';

  const profileName = wl.profile_id
    ? (profiles.find((p) => p.id === wl.profile_id)?.name ?? wl.profile_name ?? 'Profile')
    : null;

  const loadAssets = useCallback(async (
    { triggerParentRefresh = false, silent = false, hn = hideNeutral }: {
      triggerParentRefresh?: boolean;
      silent?: boolean;
      hn?: boolean;
    } = {}
  ) => {
    // Drop overlapping refreshes for the same expanded board so polling,
    // manual refresh, and parent-triggered reloads do not race each other.
    if (assetsRequestInFlight.current) return;
    assetsRequestInFlight.current = true;
    if (!silent) setLoadingAssets(true);
    if (!silent) setLoadingRejected(true);
    try {
      const assetsUrl = isFutures && hn
        ? `/watchlists/${wl.id}/assets?hide_neutral=true`
        : `/watchlists/${wl.id}/assets`;
      const [data, rejected] = await Promise.all([
        apiFetch<{
          approved_items: RejectedAssetItem[];
          assets: FuturesAsset[];
          total: number;
          market_mode?: string;
          is_futures?: boolean;
        }>(assetsUrl),
        apiFetch<{ items: RejectedAssetItem[] }>(`/pipeline/rejected?watchlist_id=${wl.id}`),
      ]);
      setApprovedItems(data.approved_items ?? []);
      setFuturesAssets(data.assets ?? []);
      setRejectedItems(rejected.items ?? []);
      if (triggerParentRefresh && ((data.approved_items?.length ?? 0) > 0 || (data.assets?.length ?? 0) > 0)) {
        onRefreshed();
      }
    } catch {
      // ignore
    } finally {
      assetsRequestInFlight.current = false;
      if (!silent) setLoadingAssets(false);
      if (!silent) setLoadingRejected(false);
    }
  }, [wl.id, isFutures, hideNeutral, onRefreshed]);

  useEffect(() => {
    if (expanded) {
      void loadAssets({ triggerParentRefresh: true });
    }
  }, [expanded, loadAssets]);

  useEffect(() => {
    if (expanded && wl.auto_refresh && refreshTick > 0) {
      void loadAssets({ silent: true });
    }
  }, [expanded, wl.auto_refresh, refreshTick, loadAssets]);

  // Re-fetch when hide_neutral toggle changes (server-side filter)
  useEffect(() => {
    if (expanded && isFutures) {
      void loadAssets({ silent: true, hn: hideNeutral });
    }
  }, [hideNeutral]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleRefresh() {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await apiFetch(`/watchlists/${wl.id}/refresh`, { method: 'POST' });
      await loadAssets({ silent: true });
      onRefreshed();
    } catch (err: unknown) {
      setRefreshError(err instanceof Error ? err.message : 'Failed to refresh watchlist assets. Check source pool configuration.');
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
        <LevelBadge level={displayLevel} />
        <span className="text-sm font-medium text-[#E2E8F0] flex-1">{wl.name}</span>
        {isFutures && (
          <span className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded bg-[#F472B6]/10 text-[#F472B6] border border-[#F472B6]/25 font-semibold">
            <ArrowLeftRight size={8} />
            FUTURES
          </span>
        )}
        <span className="text-xs text-[#4B5563]">from {sourceName}</span>
        {/* Freshness indicator */}
        {wl.auto_refresh && (
          <span
            className={`inline-flex items-center gap-1 text-[10px] ${
              isScanStale(wl.last_scanned_at)
                ? 'text-amber-400'
                : 'text-emerald-500'
            }`}
            title={wl.last_scanned_at ? `Last scan: ${new Date(wl.last_scanned_at).toLocaleString()}` : 'Never scanned'}
          >
            <Clock size={10} />
            {wl.last_scanned_at ? timeAgo(wl.last_scanned_at) : 'pending'}
          </span>
        )}
        {/* Profile badge */}
        {profileName && (
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-[#1A1A2E] text-[#818CF8] border border-[#4338CA]/30 cursor-pointer hover:border-[#6366F1]/50 transition-colors"
            title="Clique em Edit para trocar o profile"
            onClick={(e) => { e.stopPropagation(); onEdit(wl); }}
            data-testid={`profile-badge-${wl.id}`}
          >
            <Zap size={9} />
            {profileName}
            <ArrowLeftRight size={9} className="text-[#4B5563]" />
          </span>
        )}
        {!profileName && (
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] text-[#4B5563] border border-dashed border-[#1E2433] cursor-pointer hover:border-[#334155] hover:text-[#64748B] transition-colors"
            onClick={(e) => { e.stopPropagation(); onEdit(wl); }}
            title="Associar profile"
          >
            <Zap size={9} />
            sem profile
          </span>
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
          <div className="flex items-center gap-2 border-b border-[#1E2433] px-4 py-3">
            <button
              type="button"
              onClick={() => setDetailTab('approved')}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                detailTab === 'approved'
                  ? 'bg-[#1E2433] text-[#E2E8F0]'
                  : 'text-[#64748B] hover:text-[#94A3B8]'
              }`}
              >
                {isFutures ? 'Assets Futures' : 'Approved'}
              <span className="ml-1 text-[#4B5563]">
                {isFutures ? futuresAssets.length : approvedItems.length}
              </span>
            </button>
            <button
              type="button"
              onClick={() => setDetailTab('rejected')}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                detailTab === 'rejected'
                  ? 'bg-[#1E2433] text-[#E2E8F0]'
                  : 'text-[#64748B] hover:text-[#94A3B8]'
              }`}
              >
                Rejected
              <span className="ml-1 text-[#4B5563]">{rejectedItems.length}</span>
            </button>
          </div>
          {refreshError && (
            <div className="px-4 py-2 text-xs text-red-400 bg-red-400/10">
              Refresh error: {refreshError}
            </div>
          )}
          {detailTab === 'approved' && isFutures ? (
            <FuturesAssetTable
              assets={futuresAssets}
              loading={loadingAssets}
              hideNeutral={hideNeutral}
              onToggleHideNeutral={() => setHideNeutral((h) => !h)}
            />
          ) : detailTab === 'approved' && loadingAssets ? (
            <div className="px-4 py-6 text-center text-sm text-[#4B5563] flex items-center justify-center gap-2">
              <RefreshCw size={13} className="animate-spin" />
              Loading approved decisions…
            </div>
          ) : detailTab === 'approved' ? (
            <WatchlistDecisionTable
              items={approvedItems}
              loading={loadingAssets}
              emptyMessage="No approved assets for the current filters."
            />
          ) : (
            <WatchlistDecisionTable
              items={rejectedItems}
              loading={loadingRejected}
              emptyMessage="No rejected assets for the current filters."
            />
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
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalWl, setModalWl] = useState<Partial<PipelineWatchlist> | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

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

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [wlData, poolData, profData] = await Promise.all([
        apiFetch<{ watchlists: PipelineWatchlist[] }>('/watchlists'),
        apiFetch<{ pools: Pool[] }>('/pools'),
        apiFetch<{ profiles: Profile[] }>('/profiles'),
      ]);
      setWatchlists(wlData.watchlists);
      setPools(poolData.pools ?? []);
      setProfiles(profData.profiles ?? []);
    } catch {
      // ignore
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  const loadSilent = useCallback(() => load(true), [load]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setRefreshTick((tick) => tick + 1);
      void loadSilent();
    }, WATCHLIST_POLL_MS);
    return () => window.clearInterval(interval);
  }, [loadSilent]);

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
    await load(true);
  }

  function handleDelete(id: string) {
    setWatchlists((prev) => prev.filter((w) => w.id !== id));
  }

  const byLevel = (level: string) => watchlists.filter((w) => resolveWatchlistLevel(w, profiles) === level);
  const customWls = watchlists.filter((w) => resolveWatchlistLevel(w, profiles) === 'custom');

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
          {(['POOL', 'L1', 'L2', 'L3'] as const).map((lvl) => {
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
                      profiles={profiles}
                      onEdit={openEdit}
                      onDelete={handleDelete}
                      onRefreshed={loadSilent}
                      refreshTick={refreshTick}
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
                    profiles={profiles}
                    onEdit={openEdit}
                    onDelete={handleDelete}
                    onRefreshed={loadSilent}
                    refreshTick={refreshTick}
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
          profiles={profiles}
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
