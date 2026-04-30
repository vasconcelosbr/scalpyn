'use client';

import { useEffect } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

/** Route-level error boundary for /trading-desk/* — degrades gracefully instead of the global Next.js overlay. */
export default function TradingDeskError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('[trading-desk] render error:', error);
  }, [error]);

  return (
    <div style={{ paddingBottom: '80px' }}>
      <div
        role="alert"
        style={{
          padding: '20px 24px',
          background: 'var(--color-loss-muted)',
          border: '1px solid var(--color-loss-border)',
          borderRadius: 'var(--radius-lg)',
          marginTop: '16px',
          display: 'flex',
          alignItems: 'flex-start',
          gap: '16px',
        }}
      >
        <AlertTriangle
          size={24}
          style={{ color: 'var(--color-loss)', flexShrink: 0, marginTop: '2px' }}
        />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div
            style={{
              fontSize: '15px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-sans)',
            }}
          >
            Something went wrong loading the trading desk
          </div>
          <div
            style={{
              fontSize: '13px',
              color: 'var(--text-secondary)',
              fontFamily: 'var(--font-sans)',
              lineHeight: 1.5,
            }}
          >
            The page hit an unexpected error while rendering. You can try reloading the section,
            or refresh the whole page if it persists.
          </div>
          {error?.message && (
            <div
              style={{
                marginTop: '4px',
                padding: '10px 12px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-sm)',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
                color: 'var(--text-secondary)',
                wordBreak: 'break-word',
                whiteSpace: 'pre-wrap',
              }}
            >
              {error.message}
              {error.digest && (
                <div style={{ marginTop: '6px', color: 'var(--text-tertiary)', fontSize: '11px' }}>
                  digest: {error.digest}
                </div>
              )}
            </div>
          )}
          <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => reset()}
              style={{ gap: '6px', fontSize: '13px', padding: '8px 14px' }}
            >
              <RefreshCw size={14} />
              Retry
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                if (typeof window !== 'undefined') window.location.reload();
              }}
              style={{ gap: '6px', fontSize: '13px', padding: '8px 14px' }}
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
