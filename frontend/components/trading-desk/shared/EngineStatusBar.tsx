'use client';

import Link from 'next/link';
import { Pause, Play, BarChart3, AlertTriangle, TrendingUp, TrendingDown } from 'lucide-react';
import { useEngineStatus, type TradingProfile } from '@/hooks/useEngineStatus';
import { useState } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

interface EngineStatusBarProps {
  profile: TradingProfile;
}

type EngineState = 'running' | 'paused' | 'stopped';

type MacroRegime =
  | 'STRONG_RISK_ON'
  | 'RISK_ON'
  | 'NEUTRAL'
  | 'RISK_OFF'
  | 'STRONG_RISK_OFF';

// ── Helpers ──────────────────────────────────────────────────────────────────

function resolveEngineState(isRunning: boolean, isPaused: boolean): EngineState {
  if (isPaused) return 'paused';
  if (isRunning) return 'running';
  return 'stopped';
}

const STATE_CONFIG: Record<
  EngineState,
  { label: string; dotColor: string; borderColor: string; textColor: string }
> = {
  running: {
    label: 'RUNNING',
    dotColor: 'var(--color-profit)',
    borderColor: 'var(--color-profit)',
    textColor: 'var(--color-profit)',
  },
  paused: {
    label: 'PAUSED',
    dotColor: 'var(--color-warning)',
    borderColor: 'var(--color-warning)',
    textColor: 'var(--color-warning)',
  },
  stopped: {
    label: 'STOPPED',
    dotColor: 'var(--text-tertiary)',
    borderColor: 'var(--border-default)',
    textColor: 'var(--text-tertiary)',
  },
};

const REGIME_CONFIG: Record<MacroRegime, { color: string; bg: string; border: string }> = {
  STRONG_RISK_ON: {
    color: 'var(--color-profit)',
    bg: 'var(--color-profit-muted)',
    border: 'var(--color-profit-border)',
  },
  RISK_ON: {
    color: '#6EE7B7',
    bg: 'rgba(110, 231, 183, 0.10)',
    border: 'rgba(110, 231, 183, 0.25)',
  },
  NEUTRAL: {
    color: 'var(--text-secondary)',
    bg: 'var(--bg-active)',
    border: 'var(--border-default)',
  },
  RISK_OFF: {
    color: 'var(--color-warning)',
    bg: 'var(--color-warning-muted)',
    border: 'rgba(251, 191, 36, 0.25)',
  },
  STRONG_RISK_OFF: {
    color: 'var(--color-loss)',
    bg: 'var(--color-loss-muted)',
    border: 'var(--color-loss-border)',
  },
};

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null || isNaN(n)) return '—';
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return '—';
  return `${n.toFixed(1)}%`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusDot({ state }: { state: EngineState }) {
  const cfg = STATE_CONFIG[state];
  return (
    <span
      style={{
        display: 'inline-block',
        width: '8px',
        height: '8px',
        borderRadius: '50%',
        background: cfg.dotColor,
        flexShrink: 0,
        animation: state === 'running' ? 'pulse 2s ease-in-out infinite' : 'none',
      }}
      aria-hidden="true"
    />
  );
}

function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: React.ReactNode;
  color?: string;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
      <span className="label">{label}</span>
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '13px',
          fontWeight: 600,
          letterSpacing: '-0.02em',
          color: color ?? 'var(--text-primary)',
        }}
      >
        {value}
      </span>
    </div>
  );
}

function Divider() {
  return (
    <div
      style={{
        width: '1px',
        height: '32px',
        background: 'var(--border-subtle)',
        alignSelf: 'center',
        flexShrink: 0,
      }}
    />
  );
}

function MacroRegimeBadge({ regime }: { regime: string }) {
  const key = regime as MacroRegime;
  const cfg = REGIME_CONFIG[key] ?? REGIME_CONFIG['NEUTRAL'];

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '5px',
        padding: '3px 10px',
        borderRadius: '100px',
        fontSize: '10px',
        fontWeight: 700,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        color: cfg.color,
        background: cfg.bg,
        border: `1px solid ${cfg.border}`,
        whiteSpace: 'nowrap',
      }}
    >
      {regime.replace(/_/g, ' ')}
    </span>
  );
}

// ── Spot body ────────────────────────────────────────────────────────────────

function SpotBody({
  positions,
  capital,
}: {
  positions: Record<string, any>[];
  capital: { total: number; used: number; free: number } | null;
}) {
  const underwaterCount = positions.filter(
    (p) => p.unrealized_pnl != null && p.unrealized_pnl < 0
  ).length;

  const deployedPct =
    capital && capital.total > 0 ? (capital.used / capital.total) * 100 : null;

  return (
    <>
      <Metric label="Active Positions" value={positions.length} />
      <Divider />
      {underwaterCount > 0 ? (
        <Metric
          label="Underwater"
          value={
            <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <AlertTriangle size={12} />
              {underwaterCount}
            </span>
          }
          color="var(--color-warning)"
        />
      ) : (
        <Metric label="Underwater" value={underwaterCount} color="var(--text-secondary)" />
      )}
      <Divider />
      <Metric
        label="Capital Free"
        value={capital ? `$${fmt(capital.free)}` : '—'}
        color="var(--color-profit)"
      />
      <Metric
        label="Capital Total"
        value={capital ? `$${fmt(capital.total)}` : '—'}
      />
      {deployedPct != null && (
        <Metric
          label="Deployed"
          value={fmtPct(deployedPct)}
          color={deployedPct > 85 ? 'var(--color-warning)' : 'var(--text-secondary)'}
        />
      )}
    </>
  );
}

// ── Futures body ─────────────────────────────────────────────────────────────

function FuturesBody({
  positions,
  capital,
  balance,
}: {
  positions: Record<string, any>[];
  capital: { total: number; used: number; free: number } | null;
  balance: Record<string, any> | null;
}) {
  const longs = positions.filter((p) => p.side === 'long' || p.direction === 'long').length;
  const shorts = positions.filter((p) => p.side === 'short' || p.direction === 'short').length;

  const unrealizedPnl: number | null = balance?.unrealized_pnl ?? null;
  const pnlPositive = unrealizedPnl != null && unrealizedPnl >= 0;

  const marginUsed = capital?.used ?? null;
  const marginTotal = capital?.total ?? null;
  const marginPct =
    marginUsed != null && marginTotal != null && marginTotal > 0
      ? (marginUsed / marginTotal) * 100
      : null;

  const macroRegime: string | null = balance?.macro_regime ?? balance?.regime ?? null;

  return (
    <>
      <Metric label="Open Positions" value={positions.length} />
      <Divider />
      <Metric
        label="Longs"
        value={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            <TrendingUp size={12} />
            {longs}
          </span>
        }
        color="var(--color-profit)"
      />
      <Metric
        label="Shorts"
        value={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            <TrendingDown size={12} />
            {shorts}
          </span>
        }
        color="var(--color-loss)"
      />
      <Divider />
      <Metric
        label="P&L Today"
        value={unrealizedPnl != null ? `${pnlPositive ? '+' : ''}$${fmt(unrealizedPnl)}` : '—'}
        color={
          unrealizedPnl == null
            ? 'var(--text-secondary)'
            : pnlPositive
            ? 'var(--color-profit)'
            : 'var(--color-loss)'
        }
      />
      <Divider />
      <Metric
        label="Margin Used"
        value={marginUsed != null ? `$${fmt(marginUsed)}` : '—'}
      />
      <Metric
        label="Margin Total"
        value={marginTotal != null ? `$${fmt(marginTotal)}` : '—'}
      />
      {marginPct != null && (
        <Metric
          label="Margin %"
          value={fmtPct(marginPct)}
          color={
            marginPct > 80
              ? 'var(--color-loss)'
              : marginPct > 60
              ? 'var(--color-warning)'
              : 'var(--text-secondary)'
          }
        />
      )}
      {macroRegime && (
        <>
          <Divider />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span className="label">Macro Regime</span>
            <MacroRegimeBadge regime={macroRegime} />
          </div>
        </>
      )}
    </>
  );
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

function SkeletonBar() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '16px',
        padding: '14px 20px',
        background: 'var(--bg-elevated)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--border-default)',
      }}
    >
      {[80, 100, 120, 90, 110].map((w, i) => (
        <div
          key={i}
          className="skeleton"
          style={{ height: '32px', width: `${w}px`, borderRadius: 'var(--radius-sm)' }}
        />
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function EngineStatusBar({ profile }: EngineStatusBarProps) {
  const { isRunning, isPaused, positions, capital, balance, isLoading, startEngine, pauseEngine, resumeEngine } =
    useEngineStatus(profile);

  const [actionLoading, setActionLoading] = useState<'start' | 'pause' | 'resume' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const engineState = resolveEngineState(isRunning, isPaused);
  const stateCfg = STATE_CONFIG[engineState];

  if (isLoading) return <SkeletonBar />;

  async function handleStart() {
    setActionLoading('start');
    setError(null);
    try {
      await startEngine();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('Failed to start engine:', err);
    } finally {
      setActionLoading(null);
    }
  }

  async function handlePause() {
    setActionLoading('pause');
    setError(null);
    try {
      await pauseEngine();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('Failed to pause engine:', err);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleResume() {
    setActionLoading('resume');
    setError(null);
    try {
      await resumeEngine();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      console.error('Failed to resume engine:', err);
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <>
      {error && (
        <div
          style={{
            padding: '12px 16px',
            background: 'var(--color-loss-muted)',
            border: '1px solid var(--color-loss-border)',
            borderRadius: 'var(--radius-lg)',
            marginBottom: '12px',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
          }}
          role="alert"
        >
          <AlertTriangle size={16} style={{ color: 'var(--color-loss)', flexShrink: 0, marginTop: '2px' }} />
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontWeight: 600, fontSize: '13px', color: 'var(--text-primary)' }}>
              Failed to start {profile} engine
            </span>
            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {error}
            </span>
            {(error.includes('config') || error.includes('connection')) && (
              <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                Make sure you have configured your {profile === 'spot' ? 'Spot' : 'Futures'} Engine settings and connected your Gate.io API keys.
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => setError(null)}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              padding: '4px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 'var(--radius-sm)',
              flexShrink: 0,
            }}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      )}
      <div
        style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0',
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border-default)',
        borderLeft: `3px solid ${stateCfg.borderColor}`,
        borderRadius: 'var(--radius-lg)',
        overflow: 'hidden',
        flexWrap: 'wrap',
      }}
      role="status"
      aria-label={`${profile} engine status: ${engineState}`}
    >
      {/* Left: status indicator */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '14px 20px',
          borderRight: '1px solid var(--border-subtle)',
          flexShrink: 0,
        }}
      >
        <StatusDot state={engineState} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1px' }}>
          <span
            className="label"
            style={{ color: 'var(--text-tertiary)' }}
          >
            {profile === 'spot' ? 'SPOT ENGINE' : 'FUTURES ENGINE'}
          </span>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '11px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: stateCfg.textColor,
            }}
          >
            {stateCfg.label}
          </span>
        </div>
      </div>

      {/* Middle: metrics */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '20px',
          padding: '14px 20px',
          flex: 1,
          flexWrap: 'wrap',
          minWidth: 0,
        }}
      >
        {profile === 'spot' ? (
          <SpotBody positions={positions} capital={capital} />
        ) : (
          <FuturesBody positions={positions} capital={capital} balance={balance} />
        )}
      </div>

      {/* Right: action buttons */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '14px 16px',
          borderLeft: '1px solid var(--border-subtle)',
          flexShrink: 0,
        }}
      >
        {/* Pause / Resume / Start toggle */}
        {engineState === 'running' ? (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handlePause}
            disabled={actionLoading !== null}
            style={{ gap: '6px', fontSize: '12px', padding: '6px 12px', height: '32px' }}
            aria-label="Pause engine"
          >
            <Pause size={13} />
            {actionLoading === 'pause' ? 'Pausing…' : 'Pause Engine'}
          </button>
        ) : engineState === 'paused' ? (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleResume}
            disabled={actionLoading !== null}
            style={{
              gap: '6px',
              fontSize: '12px',
              padding: '6px 12px',
              height: '32px',
              color: 'var(--color-warning)',
              borderColor: 'rgba(251, 191, 36, 0.25)',
            }}
            aria-label="Resume engine"
          >
            <Play size={13} />
            {actionLoading === 'resume' ? 'Resuming…' : 'Resume'}
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleStart}
            disabled={actionLoading !== null}
            style={{ gap: '6px', fontSize: '12px', padding: '6px 12px', height: '32px' }}
            aria-label="Start engine"
          >
            <Play size={13} />
            {actionLoading === 'start' ? 'Starting…' : 'Start Engine'}
          </button>
        )}

        {/* View positions link */}
        <Link
          href="/trading-desk/positions"
          className="btn btn-secondary"
          style={{ gap: '6px', fontSize: '12px', padding: '6px 12px', height: '32px', textDecoration: 'none' }}
        >
          <BarChart3 size={13} />
          View Positions
        </Link>
      </div>
    </div>
    </>
  );
}
