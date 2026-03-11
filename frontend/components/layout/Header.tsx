"use client";

import { useAppStore } from '@/stores/useAppStore';
import { Bell, User, ChevronRight } from 'lucide-react';
import { usePathname } from 'next/navigation';

export function Header() {
  const mode = useAppStore((state) => state.mode);
  const pathname = usePathname();
  
  // Format pathname as breadcrumb style
  const pathParts = pathname.split('/').filter(Boolean);
  const pageTitle = pathParts.length > 0 
    ? pathParts[pathParts.length - 1].charAt(0).toUpperCase() + pathParts[pathParts.length - 1].slice(1)
    : 'Dashboard';

  return (
    <header className="fixed top-0 left-[240px] max-lg:left-[64px] max-md:left-0 right-0 h-[56px] border-b border-[var(--border-subtle)] flex items-center justify-between px-6 z-40 backdrop-blur-md bg-[#0C0D12D9] transition-all duration-350 ease-out">
      
      {/* Left side: Page Title & Breadcrumb */}
      <div className="flex flex-col justify-center">
        <div className="flex items-center gap-2 text-[11px] text-[var(--text-tertiary)] uppercase tracking-[0.05em] font-medium mb-0.5">
          <span>Scalpyn</span>
          <ChevronRight className="w-3 h-3" />
          {pathParts.length > 1 && (
            <>
              <span>{pathParts[0]}</span>
              <ChevronRight className="w-3 h-3" />
            </>
          )}
          <span className="text-[var(--text-secondary)]">{pageTitle}</span>
        </div>
      </div>

      {/* Right side controls */}
      <div className="flex items-center gap-4">
        
        {/* Mode Indicator */}
        {mode === 'live' ? (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--color-profit-muted)] border border-[var(--color-profit-border)] rounded-full text-[var(--color-profit)] text-[11px] font-semibold tracking-[0.05em] uppercase">
            <span className="live-dot bg-[var(--color-profit)]" /> LIVE TRADING
          </div>
        ) : (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--color-warning-muted)] border border-[rgba(251,191,36,0.3)] rounded-full text-[var(--color-warning)] text-[11px] font-semibold tracking-[0.05em] uppercase">
            <span className="live-dot bg-[var(--color-warning)]" /> PAPER TRADING
          </div>
        )}

        {/* Notifications */}
        <button className="btn-icon relative ml-2">
          <Bell className="w-4 h-4" />
          <span className="absolute top-2 right-2.5 w-1.5 h-1.5 bg-[var(--color-loss)] rounded-full border border-[var(--bg-surface)]" />
        </button>
        
        {/* User avatar duplicate (Top level click target) */}
        <div className="w-8 h-8 rounded-full bg-[var(--bg-hover)] border border-[var(--border-default)] flex items-center justify-center overflow-hidden cursor-pointer hover:border-[var(--border-strong)] transition-colors">
          <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Scalpyn" alt="User Avatar" className="w-full h-full object-cover" />
        </div>
      </div>
    </header>
  );
}
