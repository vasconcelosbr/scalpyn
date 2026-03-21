'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import { apiGet } from '@/lib/api';

// ─── Types ────────────────────────────────────────────────────────────────────

interface TradeHistoryItem {
  id: string;
  symbol: string;
  profile: string; // "spot" | "futures"
  direction: string; // "long" | "short"
  entry_price: number | string;
  exit_price: number | string;
  quantity: number | string;
  profit_loss: number | string;
  entry_at: string;
  exit_at: string;
  engine_meta?: {
    score_at_entry?: number;
    close_reason?: string;
  };
}

type ProfileFilter = 'all' | 'spot' | 'futures';
type DateRangeFilter = 'last7d' | 'last30d' | 'all';
type SortKey = keyof TradeHistoryItem | 'pnl_pct' | 'hold_time';
type SortDir = 'asc' | 'desc';

// ─── Helper functions ─────────────────────────────────────────────────────────

function toNum(v: number | string | undefined | null): number {
  if (v === undefined || v === null) return 0;
  const n = typeof v === 'string' ? parseFloat(v) : v;
  return isFinite(n) ? n : 0;
}

function formatDuration(start: string, end: string): string {
  if (!start || !end) return '—';
  const diffMs = new Date(end).getTime() - new Date(start).getTime();
  if (diffMs < 0) return '—';
  const totalMins = Math.floor(diffMs / 60_000);
  const mins = totalMins % 60;
  const hours = Math.floor(totalMins / 60) % 24;
  const days = Math.floor(totalMins / 1440);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function calcPnlPct(
  entry: number | string,
  exit: number | string,
  direction: string
): number {
  const e = toNum(entry);
  const x = toNum(exit);
  if (!e) return 0;
  const raw = ((x - e) / e) * 100;
  return direction?.toLowerCase() === 'short' ? -raw : raw;
}

function formatPrice(price: number): string {
  if (!isFinite(price) || price === 0) return '$0.00';
  const abs = Math.abs(price);
  if (abs < 0.01) return `$${price.toFixed(6)}`;
  if (abs < 1) return `$${price.toFixed(4)}`;
  if (abs < 1000) return `$${price.toFixed(2)}`;
  if (abs < 1_000_000) return `$${(price / 1000).toFixed(2)}K`;
  return `$${(price / 1_000_000).toFixed(2)}M`;
}

function formatPnl(pnl: number): string {
  const abs = Math.abs(pnl);
  const formatted = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(abs);
  return pnl >= 0 ? `+$${formatted}` : `-$${formatted}`;
}

function exportCSV(trades: TradeHistoryItem[]): void {
  const headers = [
    'ID',
    'Profile',
    'Symbol',
    'Direction',
    'Entry Price',
    'Exit Price',
    'Quantity',
    'P&L ($)',
    'P&L (%)',
    'Entry At',
    'Exit At',
    'Hold Time',
    'Score',
    'Close Reason',
  ];

  const rows = trades.map((t) => {
    const pnlPct = calcPnlPct(t.entry_price, t.exit_price, t.direction);
    return [
      t.id,
      t.profile,
      t.symbol,
      t.direction,
      toNum(t.entry_price).toFixed(8),
      toNum(t.exit_price).toFixed(8),
      toNum(t.quantity).toFixed(8),
      toNum(t.profit_loss).toFixed(2),
      pnlPct.toFixed(2),
      t.entry_at,
      t.exit_at,
      formatDuration(t.entry_at, t.exit_at),
      t.engine_meta?.score_at_entry?.toFixed(2) ?? '',
      t.engine_meta?.close_reason ?? '',
    ]
      .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
      .join(',');
  });

  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `scalpyn_trades_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ProfileBadge({ profile }: { profile: string }) {
  const isSpot = profile?.toLowerCase() === 'spot';
  return (
    <span
      style={{
        display: 'inline-flex',
        padding: '2px 8px',
        borderRadius: 'var(--radius-sm)',
        background: isSpot ? 'rgba(79, 123, 247, 0.12)' : 'rgba(168, 85, 247, 0.12)',
        border: `1px solid ${isSpot ? 'rgba(79, 123, 247, 0.3)' : 'rgba(168, 85, 247, 0.3)'}`,
        color: isSpot ? 'var(--accent-primary)' : '#a855f7',
        fontSize: '11px',
        fontWeight: 700,
        letterSpacing: '0.06em',
      }}
    >
      {isSpot ? 'SPOT' : 'FUTURES'}
    </span>
  );
}

function DirectionBadge({ direction }: { direction: string }) {
  const isLong = direction?.toLowerCase() === 'long';
  return (
    <span
      style={{
        display: 'inline-flex',
        padding: '2px 8px',
        borderRadius: 'var(--radius-sm)',
        background: isLong ? 'var(--color-profit-muted)' : 'var(--color-loss-muted)',
        border: `1px solid ${isLong ? 'var(--color-profit-border)' : 'var(--color-loss-border)'}`,
        color: isLong ? 'var(--color-profit)' : 'var(--color-loss)',
        fontSize: '11px',
        fontWeight: 700,
        letterSpacing: '0.06em',
        fontFamily: 'var(--font-mono)',
      }}
    >
      {isLong ? 'LONG' : 'SHORT'}
    </span>
  );
}

function ReasonBadge({ reason }: { reason: string | undefined }) {
  if (!reason) return <span style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>—</span>;

  const colorMap: Record<string, { bg: string; border: string; color: string }> = {
    TP1:       { bg: 'rgba(52, 211, 153, 0.1)',  border: 'rgba(52, 211, 153, 0.25)',  color: 'var(--color-profit)' },
    TP2:       { bg: 'rgba(52, 211, 153, 0.1)',  border: 'rgba(52, 211, 153, 0.25)',  color: 'var(--color-profit)' },
    TP3:       { bg: 'rgba(52, 211, 153, 0.1)',  border: 'rgba(52, 211, 153, 0.25)',  color: 'var(--color-profit)' },
    TARGET:    { bg: 'rgba(52, 211, 153, 0.1)',  border: 'rgba(52, 211, 153, 0.25)',  color: 'var(--color-profit)' },
    TRAILING:  { bg: 'rgba(251, 191, 36, 0.1)',  border: 'rgba(251, 191, 36, 0.25)',  color: 'var(--color-warning)' },
    AI_SELL:   { bg: 'rgba(79, 123, 247, 0.1)',  border: 'rgba(79, 123, 247, 0.25)',  color: 'var(--accent-primary)' },
    STOP:      { bg: 'rgba(248, 113, 113, 0.1)', border: 'rgba(248, 113, 113, 0.25)', color: 'var(--color-loss)' },
    EMERGENCY: { bg: 'rgba(248, 113, 113, 0.15)', border: 'rgba(248, 113, 113, 0.4)', color: 'var(--color-loss)' },
  };

  const styles = colorMap[reason.toUpperCase()] ?? {
    bg: 'var(--bg-hover)',
    border: 'var(--border-default)',
    color: 'var(--text-secondary)',
  };

  return (
    <span
      style={{
        display: 'inline-flex',
        padding: '2px 8px',
        borderRadius: 'var(--radius-sm)',
        background: styles.bg,
        border: `1px solid ${styles.border}`,
        color: styles.color,
        fontSize: '11px',
        fontWeight: 700,
        letterSpacing: '0.06em',
        fontFamily: 'var(--font-mono)',
      }}
    >
      {reason.toUpperCase()}
    </span>
  );
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) {
    return (
      <span style={{ opacity: 0.3, fontSize: '10px', marginLeft: '4px' }}>↕</span>
    );
  }
  return (
    <span style={{ fontSize: '10px', marginLeft: '4px', color: 'var(--accent-primary)' }}>
      {dir === 'asc' ? '↑' : '↓'}
    </span>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '64px 24px',
        gap: '12px',
        color: 'var(--text-tertiary)',
      }}
    >
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
      </svg>
      <span style={{ fontSize: '13px' }}>No trade history found</span>
      <span style={{ fontSize: '12px' }}>Completed trades will appear here</span>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function TradeHistoryPage() {
  const [trades, setTrades] = useState<TradeHistoryItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [profileFilter, setProfileFilter] = useState<ProfileFilter>('all');
  const [dateRange, setDateRange] = useState<DateRangeFilter>('last30d');
  const [symbolSearch, setSymbolSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('exit_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // ── Fetch ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    setIsLoading(true);
    setError(null);
    apiGet<{ trades?: TradeHistoryItem[]; data?: TradeHistoryItem[] } | TradeHistoryItem[]>(
      '/trades/history?limit=100'
    )
      .then((res) => {
        const list = Array.isArray(res)
          ? res
          : Array.isArray((res as any).trades)
          ? (res as any).trades
          : Array.isArray((res as any).data)
          ? (res as any).data
          : [];
        setTrades(list);
      })
      .catch((err) => setError(err?.message ?? 'Failed to load trade history'))
      .finally(() => setIsLoading(false));
  }, []);

  // ── Filtered & sorted trades ───────────────────────────────────────────────
  const filtered = useMemo(() => {
    const now = Date.now();
    const cutoff =
      dateRange === 'last7d'
        ? now - 7 * 86_400_000
        : dateRange === 'last30d'
        ? now - 30 * 86_400_000
        : 0;

    return trades
      .filter((t) => {
        const matchProfile = profileFilter === 'all' || t.profile?.toLowerCase() === profileFilter;
        const matchDate = !cutoff || new Date(t.exit_at).getTime() >= cutoff;
        const matchSymbol =
          !symbolSearch || t.symbol?.toLowerCase().includes(symbolSearch.toLowerCase());
        return matchProfile && matchDate && matchSymbol;
      })
      .sort((a, b) => {
        let va: any;
        let vb: any;

        if (sortKey === 'pnl_pct') {
          va = calcPnlPct(a.entry_price, a.exit_price, a.direction);
          vb = calcPnlPct(b.entry_price, b.exit_price, b.direction);
        } else if (sortKey === 'hold_time') {
          va = new Date(a.exit_at).getTime() - new Date(a.entry_at).getTime();
          vb = new Date(b.exit_at).getTime() - new Date(b.entry_at).getTime();
        } else if (sortKey === 'exit_at' || sortKey === 'entry_at') {
          va = new Date((a as any)[sortKey]).getTime();
          vb = new Date((b as any)[sortKey]).getTime();
        } else if (sortKey === 'profit_loss') {
          va = toNum(a.profit_loss);
          vb = toNum(b.profit_loss);
        } else {
          va = (a as any)[sortKey] ?? '';
          vb = (b as any)[sortKey] ?? '';
        }

        if (va < vb) return sortDir === 'asc' ? -1 : 1;
        if (va > vb) return sortDir === 'asc' ? 1 : -1;
        return 0;
      });
  }, [trades, profileFilter, dateRange, symbolSearch, sortKey, sortDir]);

  // ── Summary metrics ────────────────────────────────────────────────────────
  const summary = useMemo(() => {
    if (filtered.length === 0) {
      return { totalPnl: 0, winRate: 0, wins: 0, losses: 0, avgPnl: 0, avgPnlPct: 0, bestTrade: null as TradeHistoryItem | null };
    }
    const totalPnl = filtered.reduce((sum, t) => sum + toNum(t.profit_loss), 0);
    const wins = filtered.filter((t) => toNum(t.profit_loss) >= 0);
    const losses = filtered.filter((t) => toNum(t.profit_loss) < 0);
    const winRate = (wins.length / filtered.length) * 100;
    const avgPnl = totalPnl / filtered.length;
    const avgPnlPct =
      filtered.reduce((sum, t) => sum + calcPnlPct(t.entry_price, t.exit_price, t.direction), 0) /
      filtered.length;
    const bestTrade = filtered.reduce<TradeHistoryItem | null>((best, t) => {
      if (!best) return t;
      return toNum(t.profit_loss) > toNum(best.profit_loss) ? t : best;
    }, null);
    return { totalPnl, winRate, wins: wins.length, losses: losses.length, avgPnl, avgPnlPct, bestTrade };
  }, [filtered]);

  // ── Sort handler ───────────────────────────────────────────────────────────
  const handleSort = useCallback(
    (key: SortKey) => {
      if (sortKey === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortKey(key);
        setSortDir('desc');
      }
    },
    [sortKey]
  );

  // ── Style helpers ──────────────────────────────────────────────────────────
  function pillStyle(active: boolean): React.CSSProperties {
    return {
      padding: '5px 14px',
      borderRadius: 'var(--radius-sm)',
      fontSize: '12px',
      fontWeight: 600,
      letterSpacing: '0.05em',
      cursor: 'pointer',
      border: 'none',
      transition: 'background 0.15s, color 0.15s',
      background: active ? 'var(--accent-primary)' : 'transparent',
      color: active ? '#fff' : 'var(--text-secondary)',
    };
  }

  const thStyle: React.CSSProperties = {
    padding: '8px 12px',
    textAlign: 'left',
    fontSize: '11px',
    fontWeight: 600,
    letterSpacing: '0.08em',
    color: 'var(--text-tertiary)',
    borderBottom: '1px solid var(--border-subtle)',
    whiteSpace: 'nowrap',
    cursor: 'pointer',
    userSelect: 'none',
  };

  const tdStyle: React.CSSProperties = {
    padding: '10px 12px',
    fontSize: '13px',
    color: 'var(--text-primary)',
    borderBottom: '1px solid var(--border-subtle)',
    verticalAlign: 'middle',
  };

  // ── Columns definition ─────────────────────────────────────────────────────
  const columns: { label: string; key: SortKey }[] = [
    { label: 'Profile', key: 'profile' },
    { label: 'Symbol', key: 'symbol' },
    { label: 'Dir', key: 'direction' },
    { label: 'Entry', key: 'entry_price' },
    { label: 'Exit', key: 'exit_price' },
    { label: 'Hold Time', key: 'hold_time' },
    { label: 'Score', key: 'id' }, // score is in engine_meta — use id as placeholder
    { label: 'P&L %', key: 'pnl_pct' },
    { label: 'P&L $', key: 'profit_loss' },
    { label: 'Reason', key: 'id' },
  ];

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* ── Page Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1
            style={{
              fontSize: '24px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              letterSpacing: '-0.02em',
              margin: 0,
            }}
          >
            Trade History
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            Completed trades across spot and futures profiles
          </p>
        </div>
      </div>

      {/* ── Filter Bar ── */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '12px',
          alignItems: 'center',
          padding: '10px 14px',
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
        }}
      >
        {/* Profile filter */}
        <div
          style={{
            display: 'flex',
            background: 'var(--bg-hover)',
            borderRadius: 'var(--radius-sm)',
            padding: '2px',
            gap: '2px',
          }}
        >
          {(['all', 'spot', 'futures'] as ProfileFilter[]).map((v) => (
            <button key={v} onClick={() => setProfileFilter(v)} style={pillStyle(profileFilter === v)}>
              {v === 'all' ? 'All' : v === 'spot' ? 'Spot' : 'Futures'}
            </button>
          ))}
        </div>

        {/* Date range filter */}
        <div
          style={{
            display: 'flex',
            background: 'var(--bg-hover)',
            borderRadius: 'var(--radius-sm)',
            padding: '2px',
            gap: '2px',
          }}
        >
          {(
            [
              { value: 'last7d', label: 'Last 7 days' },
              { value: 'last30d', label: 'Last 30 days' },
              { value: 'all', label: 'All time' },
            ] as { value: DateRangeFilter; label: string }[]
          ).map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setDateRange(value)}
              style={pillStyle(dateRange === value)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Symbol search */}
        <input
          type="text"
          placeholder="Search symbol…"
          value={symbolSearch}
          onChange={(e) => setSymbolSearch(e.target.value)}
          style={{
            background: 'var(--bg-input)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-sm)',
            padding: '5px 10px',
            color: 'var(--text-primary)',
            fontSize: '13px',
            outline: 'none',
            width: '160px',
            fontFamily: 'inherit',
          }}
        />

        {/* Export CSV */}
        <button
          onClick={() => exportCSV(filtered)}
          disabled={filtered.length === 0}
          style={{
            marginLeft: 'auto',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 14px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-hover)',
            border: '1px solid var(--border-default)',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            fontWeight: 600,
            cursor: filtered.length === 0 ? 'not-allowed' : 'pointer',
            opacity: filtered.length === 0 ? 0.5 : 1,
            transition: 'border-color 0.15s, color 0.15s',
          }}
          onMouseEnter={(e) => {
            if (filtered.length > 0) {
              e.currentTarget.style.borderColor = 'var(--border-strong)';
              e.currentTarget.style.color = 'var(--text-primary)';
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-default)';
            e.currentTarget.style.color = 'var(--text-secondary)';
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
          </svg>
          Export CSV
        </button>
      </div>

      {/* ── P&L Summary Cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        {/* TOTAL P&L */}
        <div className="metric-card">
          <span className="label">TOTAL P&L</span>
          <span
            className="value"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '22px',
              color: summary.totalPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
            }}
          >
            {formatPnl(summary.totalPnl)}
          </span>
          <span className="caption" style={{ color: 'var(--text-tertiary)', fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
            {filtered.length} trade{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>

        {/* WIN RATE */}
        <div className="metric-card">
          <span className="label">WIN RATE</span>
          <span
            className="value"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '22px',
              color: summary.winRate >= 50 ? 'var(--color-profit)' : 'var(--color-loss)',
            }}
          >
            {summary.winRate.toFixed(1)}%
          </span>
          <span className="caption" style={{ color: 'var(--text-tertiary)', fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
            {summary.wins}W / {summary.losses}L
          </span>
        </div>

        {/* AVG P&L/TRADE */}
        <div className="metric-card">
          <span className="label">AVG P&L / TRADE</span>
          <span
            className="value"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '22px',
              color: summary.avgPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
            }}
          >
            {formatPnl(summary.avgPnl)}
          </span>
          <span
            className="caption"
            style={{
              color: summary.avgPnlPct >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {summary.avgPnlPct >= 0 ? '+' : ''}{summary.avgPnlPct.toFixed(2)}% avg
          </span>
        </div>

        {/* BEST TRADE */}
        <div className="metric-card">
          <span className="label">BEST TRADE</span>
          <span
            className="value"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '22px',
              color: 'var(--color-profit)',
            }}
          >
            {summary.bestTrade ? formatPnl(toNum(summary.bestTrade.profit_loss)) : '$0.00'}
          </span>
          {summary.bestTrade && (
            <span className="caption" style={{ color: 'var(--text-tertiary)', fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
              {summary.bestTrade.symbol} · {summary.bestTrade.direction?.toUpperCase()}
            </span>
          )}
        </div>
      </div>

      {/* ── Trade History Table ── */}
      <div className="card">
        <div className="card-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <h3
              style={{
                margin: 0,
                fontSize: '12px',
                fontWeight: 700,
                letterSpacing: '0.1em',
                color: 'var(--text-secondary)',
              }}
            >
              TRADE HISTORY
            </h3>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                minWidth: '22px',
                height: '22px',
                padding: '0 6px',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-hover)',
                border: '1px solid var(--border-default)',
                color: 'var(--text-secondary)',
                fontSize: '11px',
                fontWeight: 700,
                fontFamily: 'var(--font-mono)',
              }}
            >
              {filtered.length}
            </span>
          </div>
        </div>

        <div className="card-body" style={{ padding: 0 }}>
          {isLoading ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '64px',
                color: 'var(--text-tertiary)',
                fontSize: '13px',
              }}
            >
              Loading trade history…
            </div>
          ) : error ? (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '48px',
                gap: '8px',
                color: 'var(--color-loss)',
                fontSize: '13px',
              }}
            >
              <span>Failed to load trade history</span>
              <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{error}</span>
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState />
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={thStyle} onClick={() => handleSort('profile')}>
                      Profile <SortIcon active={sortKey === 'profile'} dir={sortDir} />
                    </th>
                    <th style={thStyle} onClick={() => handleSort('symbol')}>
                      Symbol <SortIcon active={sortKey === 'symbol'} dir={sortDir} />
                    </th>
                    <th style={thStyle} onClick={() => handleSort('direction')}>
                      Dir <SortIcon active={sortKey === 'direction'} dir={sortDir} />
                    </th>
                    <th style={thStyle} onClick={() => handleSort('entry_price')}>
                      Entry <SortIcon active={sortKey === 'entry_price'} dir={sortDir} />
                    </th>
                    <th style={thStyle} onClick={() => handleSort('exit_price')}>
                      Exit <SortIcon active={sortKey === 'exit_price'} dir={sortDir} />
                    </th>
                    <th style={thStyle} onClick={() => handleSort('hold_time')}>
                      Hold Time <SortIcon active={sortKey === 'hold_time'} dir={sortDir} />
                    </th>
                    <th style={thStyle}>
                      Score
                    </th>
                    <th style={thStyle} onClick={() => handleSort('pnl_pct')}>
                      P&L % <SortIcon active={sortKey === 'pnl_pct'} dir={sortDir} />
                    </th>
                    <th style={thStyle} onClick={() => handleSort('profit_loss')}>
                      P&L $ <SortIcon active={sortKey === 'profit_loss'} dir={sortDir} />
                    </th>
                    <th style={thStyle}>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((trade, i) => {
                    const pnl = toNum(trade.profit_loss);
                    const pnlPct = calcPnlPct(trade.entry_price, trade.exit_price, trade.direction);
                    const score = trade.engine_meta?.score_at_entry;
                    return (
                      <tr
                        key={trade.id ?? i}
                        style={{
                          background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)',
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-hover)')}
                        onMouseLeave={(e) =>
                          (e.currentTarget.style.background =
                            i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)')
                        }
                      >
                        {/* Profile */}
                        <td style={tdStyle}>
                          <ProfileBadge profile={trade.profile} />
                        </td>

                        {/* Symbol */}
                        <td
                          style={{
                            ...tdStyle,
                            fontWeight: 600,
                            fontFamily: 'var(--font-mono)',
                            color: 'var(--text-primary)',
                          }}
                        >
                          {trade.symbol ?? '—'}
                        </td>

                        {/* Direction */}
                        <td style={tdStyle}>
                          <DirectionBadge direction={trade.direction} />
                        </td>

                        {/* Entry */}
                        <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {formatPrice(toNum(trade.entry_price))}
                        </td>

                        {/* Exit */}
                        <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {formatPrice(toNum(trade.exit_price))}
                        </td>

                        {/* Hold Time */}
                        <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>
                          {formatDuration(trade.entry_at, trade.exit_at)}
                        </td>

                        {/* Score */}
                        <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {score != null ? score.toFixed(2) : '—'}
                        </td>

                        {/* P&L % */}
                        <td style={tdStyle}>
                          <span
                            style={{
                              fontFamily: 'var(--font-mono)',
                              fontSize: '13px',
                              fontWeight: 600,
                              color: pnlPct >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                            }}
                          >
                            {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                          </span>
                        </td>

                        {/* P&L $ */}
                        <td style={tdStyle}>
                          <span
                            style={{
                              fontFamily: 'var(--font-mono)',
                              fontSize: '13px',
                              fontWeight: 600,
                              color: pnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                            }}
                          >
                            {formatPnl(pnl)}
                          </span>
                        </td>

                        {/* Reason */}
                        <td style={tdStyle}>
                          <ReasonBadge reason={trade.engine_meta?.close_reason} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
