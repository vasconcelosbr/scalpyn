'use client';

import { useState, useMemo } from 'react';
import { usePositions, type Position } from '@/hooks/usePositions';

// ─── Helper functions ─────────────────────────────────────────────────────────

function formatAge(dateStr: string): string {
  if (!dateStr) return '—';
  const diffMs = Date.now() - new Date(dateStr).getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 60) return `${diffMins}m`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d`;
}

function formatPrice(price: number): string {
  if (!isFinite(price)) return '—';
  if (price === 0) return '$0.00';
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

function getLiqDistanceColor(distPct: number): string {
  if (distPct > 10) return 'var(--color-profit)';
  if (distPct >= 5) return 'var(--color-warning)';
  return 'var(--color-loss)';
}

function calcLiqDistance(pos: Position): number | null {
  const price = pos.mark_price ?? pos.current_price ?? 0;
  const liq = pos.liquidation_price;
  if (!liq || !price) return null;
  return (Math.abs(price - liq) / price) * 100;
}

function avgDaysUnderwater(positions: Position[]): number {
  const now = Date.now();
  const total = positions.reduce((sum, p) => {
    const entryMs = p.entry_at ? new Date(p.entry_at).getTime() : now;
    return sum + (now - entryMs) / 86_400_000;
  }, 0);
  return positions.length > 0 ? total / positions.length : 0;
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function PnlCell({ pct, abs }: { pct: number; abs: number }) {
  const isProfit = pct >= 0;
  const color = isProfit ? 'var(--color-profit)' : 'var(--color-loss)';
  return (
    <span style={{ color, fontFamily: 'var(--font-mono)', fontSize: '13px' }}>
      {isProfit ? '+' : ''}{pct.toFixed(2)}%
      <span style={{ display: 'block', fontSize: '11px', opacity: 0.75 }}>{formatPnl(abs)}</span>
    </span>
  );
}

function StatusBadge({ underwater }: { underwater: boolean }) {
  if (underwater) {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          borderRadius: 'var(--radius-sm)',
          background: 'var(--color-loss-muted)',
          border: '1px solid var(--color-loss-border)',
          color: 'var(--color-loss)',
          fontSize: '11px',
          fontWeight: 600,
          letterSpacing: '0.06em',
        }}
      >
        🔴 UNDERWATER
      </span>
    );
  }
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 8px',
        borderRadius: 'var(--radius-sm)',
        background: 'var(--color-profit-muted)',
        border: '1px solid var(--color-profit-border)',
        color: 'var(--color-profit)',
        fontSize: '11px',
        fontWeight: 600,
        letterSpacing: '0.06em',
      }}
    >
      ACTIVE
    </span>
  );
}

function DirectionBadge({ side }: { side: string }) {
  const isLong = side?.toLowerCase() === 'long';
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

function EmptyState({ label }: { label: string }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '48px 24px',
        gap: '12px',
        color: 'var(--text-tertiary)',
      }}
    >
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 9.776c.112-.017.227-.026.344-.026h15.812c.117 0 .232.009.344.026m-16.5 0a2.25 2.25 0 00-1.883 2.542l.857 6a2.25 2.25 0 002.227 1.932H19.05a2.25 2.25 0 002.227-1.932l.857-6a2.25 2.25 0 00-1.883-2.542m-16.5 0V6A2.25 2.25 0 016 3.75h3.879a1.5 1.5 0 011.06.44l2.122 2.12a1.5 1.5 0 001.06.44H18A2.25 2.25 0 0120.25 9v.776" />
      </svg>
      <span style={{ fontSize: '13px' }}>No open {label} positions</span>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

type ProfileFilter = 'all' | 'spot' | 'futures';
type StatusFilter = 'all' | 'active' | 'underwater';

export default function PositionsPage() {
  const { spotPositions, futuresPositions, underwaterCount, underwaterValue, nearestLiquidation, summary, isLoading } = usePositions();

  const [profileFilter, setProfileFilter] = useState<ProfileFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [assetSearch, setAssetSearch] = useState('');

  // ── Filtered positions ─────────────────────────────────────────────────────
  const filteredSpot = useMemo(() => {
    if (profileFilter === 'futures') return [];
    return spotPositions.filter((p) => {
      const matchSearch = !assetSearch || p.symbol?.toLowerCase().includes(assetSearch.toLowerCase());
      const isUnder = (p.unrealised_pnl ?? 0) < 0 || p.is_underwater === true;
      const matchStatus =
        statusFilter === 'all' ||
        (statusFilter === 'active' && !isUnder) ||
        (statusFilter === 'underwater' && isUnder);
      return matchSearch && matchStatus;
    });
  }, [spotPositions, profileFilter, statusFilter, assetSearch]);

  const filteredFutures = useMemo(() => {
    if (profileFilter === 'spot') return [];
    return futuresPositions.filter((p) => {
      const matchSearch = !assetSearch || p.symbol?.toLowerCase().includes(assetSearch.toLowerCase());
      const isUnder = (p.unrealised_pnl ?? 0) < 0 || p.is_underwater === true;
      const matchStatus =
        statusFilter === 'all' ||
        (statusFilter === 'active' && !isUnder) ||
        (statusFilter === 'underwater' && isUnder);
      return matchSearch && matchStatus;
    });
  }, [futuresPositions, profileFilter, statusFilter, assetSearch]);

  // ── Derived metrics ────────────────────────────────────────────────────────
  const totalPositionCount = spotPositions.length + futuresPositions.length;
  const freeCapitalPct =
    summary.totalCapital > 0 ? (summary.freeCapital / summary.totalCapital) * 100 : 0;

  const spotUnderwaterPositions = spotPositions.filter(
    (p) => (p.unrealised_pnl ?? 0) < 0 || p.is_underwater === true
  );
  const worstUnderwater = spotUnderwaterPositions.reduce<Position | null>((worst, p) => {
    if (!worst) return p;
    return (p.unrealised_pnl_pct ?? 0) < (worst.unrealised_pnl_pct ?? 0) ? p : worst;
  }, null);

  const totalFuturesPnl = futuresPositions.reduce((sum, p) => sum + (p.unrealised_pnl ?? 0), 0);
  const totalFuturesPnlPct =
    summary.futuresCapital > 0 ? (totalFuturesPnl / summary.futuresCapital) * 100 : 0;

  const nearestLiqDist = nearestLiquidation ? calcLiqDistance(nearestLiquidation) : null;
  const hasLiqWarning = nearestLiqDist !== null && nearestLiqDist < 10;

  // ── Pill button style helper ───────────────────────────────────────────────
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
  };

  const tdStyle: React.CSSProperties = {
    padding: '10px 12px',
    fontSize: '13px',
    color: 'var(--text-primary)',
    borderBottom: '1px solid var(--border-subtle)',
    verticalAlign: 'middle',
  };

  if (isLoading && totalPositionCount === 0) {
    return (
      <div className="space-y-6">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <h1 style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>Positions</h1>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>All open positions across spot and futures</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '80px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
          Loading positions…
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Page Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0 }}>
            Positions
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            All open positions across spot and futures
          </p>
        </div>
        {isLoading && (
          <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '6px' }}>Refreshing…</span>
        )}
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

        {/* Asset search */}
        <input
          type="text"
          placeholder="Search asset…"
          value={assetSearch}
          onChange={(e) => setAssetSearch(e.target.value)}
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

        {/* Status filter */}
        <div
          style={{
            display: 'flex',
            background: 'var(--bg-hover)',
            borderRadius: 'var(--radius-sm)',
            padding: '2px',
            gap: '2px',
            marginLeft: 'auto',
          }}
        >
          {(['all', 'active', 'underwater'] as StatusFilter[]).map((v) => (
            <button key={v} onClick={() => setStatusFilter(v)} style={pillStyle(statusFilter === v)}>
              {v === 'all' ? 'All' : v === 'active' ? 'Active' : 'Underwater'}
            </button>
          ))}
        </div>
      </div>

      {/* ── Capital Overview ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        {/* TOTAL */}
        <div className="metric-card">
          <span className="label">TOTAL CAPITAL</span>
          <span className="value" style={{ fontFamily: 'var(--font-mono)', fontSize: '22px' }}>
            {formatPrice(summary.totalCapital)}
          </span>
          <span className="caption" style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>
            {totalPositionCount} position{totalPositionCount !== 1 ? 's' : ''}
          </span>
        </div>

        {/* SPOT */}
        <div className="metric-card">
          <span className="label">SPOT DEPLOYED</span>
          <span className="value" style={{ fontFamily: 'var(--font-mono)', fontSize: '22px' }}>
            {formatPrice(summary.spotCapital)}
          </span>
          <span className="caption" style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>
            {spotPositions.length} position{spotPositions.length !== 1 ? 's' : ''}
          </span>
        </div>

        {/* FUTURES */}
        <div className="metric-card">
          <span className="label">FUTURES MARGIN</span>
          <span className="value" style={{ fontFamily: 'var(--font-mono)', fontSize: '22px' }}>
            {formatPrice(summary.futuresCapital)}
          </span>
          <span className="caption" style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>
            {futuresPositions.length} position{futuresPositions.length !== 1 ? 's' : ''}
          </span>
        </div>

        {/* FREE */}
        <div className="metric-card">
          <span className="label">FREE CAPITAL</span>
          <span className="value" style={{ fontFamily: 'var(--font-mono)', fontSize: '22px' }}>
            {formatPrice(summary.freeCapital)}
          </span>
          <span
            className="caption"
            style={{
              color: freeCapitalPct > 20 ? 'var(--color-profit)' : freeCapitalPct > 5 ? 'var(--color-warning)' : 'var(--color-loss)',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {freeCapitalPct.toFixed(1)}% free
          </span>
        </div>
      </div>

      {/* ── Spot Positions ── */}
      {profileFilter !== 'futures' && (
        <div className="card">
          <div className="card-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <h3 style={{ margin: 0, fontSize: '12px', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)' }}>
                SPOT POSITIONS
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
                {filteredSpot.length}
              </span>
            </div>
          </div>

          <div className="card-body" style={{ padding: 0 }}>
            {filteredSpot.length === 0 ? (
              <EmptyState label="spot" />
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      {['Symbol', 'Entry', 'Current', 'P&L %', 'P&L $', 'Status', 'Age'].map((col) => (
                        <th key={col} style={thStyle}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSpot.map((pos, i) => {
                      const isUnder = (pos.unrealised_pnl ?? 0) < 0 || pos.is_underwater === true;
                      const pnlPct = pos.unrealised_pnl_pct ?? 0;
                      const pnlAbs = pos.unrealised_pnl ?? 0;
                      return (
                        <tr
                          key={pos.id ?? i}
                          style={{ background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-hover)')}
                          onMouseLeave={(e) => (e.currentTarget.style.background = i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)')}
                        >
                          <td style={{ ...tdStyle, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
                            {pos.symbol ?? '—'}
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)' }}>
                            {formatPrice(pos.entry_price ?? 0)}
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)' }}>
                            {formatPrice(pos.current_price ?? pos.mark_price ?? 0)}
                          </td>
                          <td style={tdStyle}>
                            <span
                              style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: '13px',
                                color: pnlPct >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                              }}
                            >
                              {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                            </span>
                          </td>
                          <td style={tdStyle}>
                            <span
                              style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: '13px',
                                color: pnlAbs >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                              }}
                            >
                              {formatPnl(pnlAbs)}
                            </span>
                          </td>
                          <td style={tdStyle}>
                            <StatusBadge underwater={isUnder} />
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                            {formatAge(pos.entry_at ?? '')}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Underwater summary panel */}
            {underwaterCount > 0 && profileFilter !== 'futures' && (
              <div
                style={{
                  margin: '0 16px 16px',
                  padding: '12px 16px',
                  borderRadius: 'var(--radius-md)',
                  background: 'var(--color-loss-muted)',
                  border: '1px solid var(--color-loss-border)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{ color: 'var(--color-loss)', fontSize: '12px', fontWeight: 700 }}>
                    {underwaterCount} underwater position{underwaterCount !== 1 ? 's' : ''}
                  </span>
                  <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>·</span>
                  <span style={{ color: 'var(--color-loss)', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
                    {formatPnl(underwaterValue)} locked
                  </span>
                  <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>·</span>
                  <span style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                    {summary.totalCapital > 0
                      ? ((Math.abs(underwaterValue) / summary.totalCapital) * 100).toFixed(1)
                      : '0.0'}%
                  </span>
                </div>
                {worstUnderwater && (
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    <span style={{ fontWeight: 600 }}>Worst:</span>{' '}
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-loss)' }}>
                      {worstUnderwater.symbol}
                    </span>{' '}
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-loss)' }}>
                      {(worstUnderwater.unrealised_pnl_pct ?? 0).toFixed(1)}%
                    </span>
                    {worstUnderwater.entry_price && worstUnderwater.current_price && (
                      <span style={{ color: 'var(--text-tertiary)' }}>
                        {' '}(needs{' '}
                        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-warning)' }}>
                          +{((worstUnderwater.entry_price / (worstUnderwater.current_price ?? worstUnderwater.entry_price) - 1) * 100).toFixed(1)}%
                        </span>
                        {' '}to target)
                      </span>
                    )}
                  </div>
                )}
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                  Avg time underwater:{' '}
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                    {avgDaysUnderwater(spotUnderwaterPositions).toFixed(1)} days
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Futures Positions ── */}
      {profileFilter !== 'spot' && (
        <div className="card">
          <div className="card-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <h3 style={{ margin: 0, fontSize: '12px', fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-secondary)' }}>
                FUTURES POSITIONS
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
                {filteredFutures.length}
              </span>
            </div>
          </div>

          <div className="card-body" style={{ padding: 0 }}>
            {filteredFutures.length === 0 ? (
              <EmptyState label="futures" />
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      {['Symbol', 'Dir', 'Entry', 'Current', 'P&L %', 'Lev', 'Liq. Price', 'Dist'].map((col) => (
                        <th key={col} style={thStyle}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredFutures.map((pos, i) => {
                      const pnlPct = pos.unrealised_pnl_pct ?? 0;
                      const dist = calcLiqDistance(pos);
                      return (
                        <tr
                          key={pos.id ?? i}
                          style={{ background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-hover)')}
                          onMouseLeave={(e) => (e.currentTarget.style.background = i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)')}
                        >
                          <td style={{ ...tdStyle, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
                            {pos.symbol ?? '—'}
                          </td>
                          <td style={tdStyle}>
                            <DirectionBadge side={pos.side ?? 'long'} />
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)' }}>
                            {formatPrice(pos.entry_price ?? 0)}
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)' }}>
                            {formatPrice(pos.mark_price ?? pos.current_price ?? 0)}
                          </td>
                          <td style={tdStyle}>
                            <span
                              style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: '13px',
                                color: pnlPct >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                              }}
                            >
                              {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                            </span>
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                            {pos.leverage != null ? `${pos.leverage}×` : '—'}
                          </td>
                          <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', color: 'var(--color-warning)' }}>
                            {pos.liquidation_price != null ? formatPrice(pos.liquidation_price) : '—'}
                          </td>
                          <td style={tdStyle}>
                            {dist !== null ? (
                              <span
                                style={{
                                  fontFamily: 'var(--font-mono)',
                                  fontSize: '13px',
                                  fontWeight: 600,
                                  color: getLiqDistanceColor(dist),
                                }}
                              >
                                {dist.toFixed(1)}%
                              </span>
                            ) : (
                              <span style={{ color: 'var(--text-tertiary)' }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Liquidation Monitor panel */}
            <div
              style={{
                margin: '0 16px 16px',
                padding: '12px 16px',
                borderRadius: 'var(--radius-md)',
                background: hasLiqWarning ? 'rgba(248, 113, 113, 0.06)' : 'var(--bg-hover)',
                border: `1px solid ${hasLiqWarning ? 'var(--color-loss-border)' : 'var(--border-subtle)'}`,
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
              }}
            >
              {/* Header status */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span
                  style={{
                    fontSize: '11px',
                    fontWeight: 700,
                    letterSpacing: '0.08em',
                    color: hasLiqWarning ? 'var(--color-loss)' : 'var(--color-profit)',
                  }}
                >
                  {hasLiqWarning ? '⚠ LIQUIDATION RISK' : '✓ All positions SAFE'}
                </span>
              </div>

              {/* Nearest liquidation */}
              {nearestLiquidation && nearestLiqDist !== null && (
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  <span style={{ fontWeight: 600 }}>Nearest liquidation:</span>{' '}
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                    {nearestLiquidation.symbol}
                  </span>{' '}
                  <DirectionBadge side={nearestLiquidation.side ?? 'long'} />{' '}
                  <span style={{ fontFamily: 'var(--font-mono)', color: getLiqDistanceColor(nearestLiqDist) }}>
                    at {nearestLiqDist.toFixed(1)}% distance
                  </span>
                </div>
              )}

              {/* Margin & P&L row */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                <span>
                  Total margin:{' '}
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                    {formatPrice(summary.futuresCapital)}
                  </span>
                </span>
                <span>
                  Unrealized P&L:{' '}
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      color: totalFuturesPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
                    }}
                  >
                    {formatPnl(totalFuturesPnl)}{' '}
                    ({totalFuturesPnlPct >= 0 ? '+' : ''}{totalFuturesPnlPct.toFixed(2)}%)
                  </span>
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
