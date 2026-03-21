import { ChevronRight } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';

interface TradingDeskLayoutProps {
  children: ReactNode;
}

export default function TradingDeskLayout({ children }: TradingDeskLayoutProps) {
  return (
    <div className="space-y-6">
      {/* Breadcrumb header */}
      <div className="flex items-center gap-2">
        <Link
          href="/"
          className="text-xs font-medium tracking-widest uppercase transition-colors duration-[var(--transition-fast)]"
          style={{ color: 'var(--text-tertiary)' }}
          onMouseOver={(e) => (e.currentTarget.style.color = 'var(--text-secondary)')}
          onMouseOut={(e) => (e.currentTarget.style.color = 'var(--text-tertiary)')}
        >
          Home
        </Link>
        <ChevronRight size={12} style={{ color: 'var(--text-tertiary)' }} aria-hidden="true" />
        <span
          className="text-xs font-semibold tracking-widest uppercase"
          style={{ color: 'var(--accent-primary)' }}
        >
          Trading Desk
        </span>
      </div>

      {children}
    </div>
  );
}
