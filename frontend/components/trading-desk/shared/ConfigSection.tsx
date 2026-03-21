'use client';

import { useState } from 'react';
import { ChevronDown, Lock } from 'lucide-react';

type BadgeVariant = 'default' | 'locked' | 'required' | 'warning';

interface ConfigSectionProps {
  title: string;
  icon?: React.ReactNode;
  defaultOpen?: boolean;
  badge?: string;
  badgeVariant?: BadgeVariant;
  children: React.ReactNode;
}

const BADGE_STYLES: Record<BadgeVariant, React.CSSProperties> = {
  default: {
    background: 'var(--bg-active)',
    color: 'var(--text-secondary)',
    border: '1px solid var(--border-default)',
  },
  locked: {
    background: 'rgba(251, 191, 36, 0.10)',
    color: 'var(--color-warning)',
    border: '1px solid rgba(251, 191, 36, 0.25)',
  },
  required: {
    background: 'var(--accent-primary-muted)',
    color: 'var(--accent-primary)',
    border: '1px solid var(--accent-primary-border)',
  },
  warning: {
    background: 'rgba(251, 191, 36, 0.10)',
    color: 'var(--color-warning)',
    border: '1px solid rgba(251, 191, 36, 0.25)',
  },
};

function resolveBadgeVariant(badge?: string, variant?: BadgeVariant): BadgeVariant {
  if (variant) return variant;
  if (badge?.toUpperCase() === 'LOCKED') return 'locked';
  if (badge?.toUpperCase() === 'REQUIRED') return 'required';
  if (badge?.toUpperCase() === 'WARNING') return 'warning';
  return 'default';
}

export function ConfigSection({
  title,
  icon,
  defaultOpen = true,
  badge,
  badgeVariant,
  children,
}: ConfigSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  const resolvedVariant = resolveBadgeVariant(badge, badgeVariant);
  const isLocked = resolvedVariant === 'locked';

  return (
    <div
      className="card"
      style={{ overflow: 'hidden' }}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left transition-colors"
        style={{
          background: 'var(--bg-elevated)',
          borderBottom: isOpen ? '1px solid var(--border-subtle)' : 'none',
          cursor: 'pointer',
        }}
        aria-expanded={isOpen}
      >
        {/* Left: icon + title + badge */}
        <div className="flex items-center gap-3 min-w-0">
          {icon && (
            <span
              className="flex-shrink-0"
              style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center' }}
            >
              {icon}
            </span>
          )}
          <span
            className="font-semibold truncate"
            style={{ fontSize: '14px', color: 'var(--text-primary)', letterSpacing: '-0.01em' }}
          >
            {title}
          </span>
          {badge && (
            <span
              className="flex-shrink-0 flex items-center gap-1.5"
              style={{
                ...BADGE_STYLES[resolvedVariant],
                fontSize: '10px',
                fontWeight: 600,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                padding: '2px 8px',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              {isLocked && <Lock size={9} aria-hidden="true" />}
              {badge}
            </span>
          )}
        </div>

        {/* Right: chevron */}
        <ChevronDown
          size={16}
          aria-hidden="true"
          style={{
            color: 'var(--text-tertiary)',
            flexShrink: 0,
            transition: `transform var(--transition-base)`,
            transform: isOpen ? 'rotate(0deg)' : 'rotate(-90deg)',
          }}
        />
      </button>

      {/* Body — CSS height transition for smooth collapse */}
      <div
        style={{
          display: 'grid',
          gridTemplateRows: isOpen ? '1fr' : '0fr',
          transition: 'grid-template-rows var(--transition-slow, 350ms cubic-bezier(0.4, 0, 0.2, 1))',
        }}
      >
        <div style={{ overflow: 'hidden' }}>
          <div className="card-body">{children}</div>
        </div>
      </div>
    </div>
  );
}
