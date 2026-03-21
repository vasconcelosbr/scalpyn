'use client';

import { useEffect, useRef } from 'react';
import { RotateCcw, X, Save, AlertCircle } from 'lucide-react';

interface SaveConfigBarProps {
  isDirty: boolean;
  isSaving: boolean;
  onSave: () => void;
  onReset: () => void;
  onCancel?: () => void;
}

export function SaveConfigBar({
  isDirty,
  isSaving,
  onSave,
  onReset,
  onCancel,
}: SaveConfigBarProps) {
  // Trap focus inside bar when saving to prevent accidental navigation
  const saveButtonRef = useRef<HTMLButtonElement>(null);
  const prevDirty = useRef(isDirty);

  // Auto-focus save button when bar appears
  useEffect(() => {
    if (!prevDirty.current && isDirty) {
      // small delay to let the slide animation settle
      const id = setTimeout(() => saveButtonRef.current?.focus(), 220);
      return () => clearTimeout(id);
    }
    prevDirty.current = isDirty;
  }, [isDirty]);

  return (
    <>
      {/* Slide-up overlay bar */}
      <div
        role="region"
        aria-label="Unsaved configuration changes"
        aria-live="polite"
        style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          zIndex: 50,
          // Slide-up on dirty, slide-down on clean
          transform: isDirty ? 'translateY(0)' : 'translateY(100%)',
          transition: 'transform 220ms cubic-bezier(0.4, 0, 0.2, 1)',
          // Ensures bar is above any scrollable content
          willChange: 'transform',
        }}
      >
        {/* Top border accent line */}
        <div
          style={{
            height: '1px',
            background: isDirty
              ? 'linear-gradient(90deg, transparent, var(--accent-primary), transparent)'
              : 'var(--border-default)',
          }}
        />

        {/* Bar body */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            padding: '12px 24px',
            background: 'var(--bg-elevated)',
            boxShadow: '0 -8px 24px rgba(0, 0, 0, 0.5)',
            flexWrap: 'wrap',
          }}
        >
          {/* Left: status indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
            <AlertCircle
              size={15}
              style={{ color: 'var(--color-warning)', flexShrink: 0 }}
              aria-hidden="true"
            />
            <span
              style={{
                fontSize: '13px',
                fontWeight: 500,
                color: 'var(--text-secondary)',
              }}
            >
              You have{' '}
              <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                unsaved changes
              </span>
            </span>
          </div>

          {/* Right: action buttons */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
            {/* Reset to Defaults */}
            <button
              type="button"
              onClick={onReset}
              disabled={isSaving}
              className="btn btn-ghost"
              style={{
                fontSize: '12px',
                padding: '6px 12px',
                height: '32px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                opacity: isSaving ? 0.5 : 1,
                cursor: isSaving ? 'not-allowed' : 'pointer',
              }}
              aria-label="Reset configuration to server defaults"
            >
              <RotateCcw size={13} aria-hidden="true" />
              Reset to Defaults
            </button>

            {/* Discard / Cancel unsaved */}
            {onCancel && (
              <button
                type="button"
                onClick={onCancel}
                disabled={isSaving}
                className="btn btn-ghost"
                style={{
                  fontSize: '12px',
                  padding: '6px 12px',
                  height: '32px',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  opacity: isSaving ? 0.5 : 1,
                  cursor: isSaving ? 'not-allowed' : 'pointer',
                }}
                aria-label="Discard unsaved changes"
              >
                <X size={13} aria-hidden="true" />
                Discard
              </button>
            )}

            {/* Separator */}
            <div
              style={{
                width: '1px',
                height: '20px',
                background: 'var(--border-default)',
                flexShrink: 0,
              }}
              aria-hidden="true"
            />

            {/* Save Changes — primary action */}
            <button
              ref={saveButtonRef}
              type="button"
              onClick={onSave}
              disabled={isSaving}
              className="btn btn-primary"
              style={{
                fontSize: '13px',
                padding: '6px 16px',
                height: '32px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '7px',
                opacity: isSaving ? 0.8 : 1,
                cursor: isSaving ? 'not-allowed' : 'pointer',
                minWidth: '130px',
                justifyContent: 'center',
              }}
              aria-label={isSaving ? 'Saving configuration…' : 'Save configuration changes'}
              aria-busy={isSaving}
            >
              {isSaving ? (
                <>
                  {/* Spinner */}
                  <span
                    style={{
                      width: '13px',
                      height: '13px',
                      border: '2px solid rgba(255,255,255,0.3)',
                      borderTopColor: 'white',
                      borderRadius: '50%',
                      display: 'inline-block',
                      animation: 'spin 600ms linear infinite',
                      flexShrink: 0,
                    }}
                    aria-hidden="true"
                  />
                  Saving…
                </>
              ) : (
                <>
                  <Save size={13} aria-hidden="true" />
                  Save Changes
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Inline keyframe for spinner — only injected once */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </>
  );
}
