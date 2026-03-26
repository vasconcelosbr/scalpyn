"use client";

import { useAppStore } from '@/stores/useAppStore';
import { useAuthStore } from '@/stores/useAuthStore';
import { getToken } from '@/lib/auth';
import { Bell, ChevronRight, LogOut } from 'lucide-react';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

export function Header() {
  const mode = useAppStore((state) => state.mode);
  const { user, logout, setUser } = useAuthStore();
  const pathname = usePathname();
  const router = useRouter();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Hydrate auth store from localStorage on mount
  useEffect(() => {
    if (!user && typeof window !== 'undefined') {
      const stored = localStorage.getItem('user');
      const token = getToken();
      if (stored && token) {
        try {
          setUser(JSON.parse(stored), token);
        } catch {
          localStorage.removeItem('user');
        }
      }
    }
  }, [user, setUser]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Format pathname as breadcrumb style
  const pathParts = pathname.split('/').filter(Boolean);
  const pageTitle =
    pathParts.length > 0
      ? pathParts[pathParts.length - 1].charAt(0).toUpperCase() +
        pathParts[pathParts.length - 1].slice(1)
      : 'Dashboard';

  // Derive user initials
  const initials = user?.name
    ? user.name
        .split(' ')
        .slice(0, 2)
        .map((w) => w[0].toUpperCase())
        .join('')
    : '?';

  function handleLogout() {
    logout();
    router.push('/login');
  }

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
        <div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--color-profit-muted)] border border-[var(--color-profit-border)] rounded-full text-[var(--color-profit)] text-[11px] font-semibold tracking-[0.05em] uppercase">
          <span className="live-dot bg-[var(--color-profit)]" /> LIVE TRADING
        </div>

        {/* Notifications */}
        <button className="btn-icon relative ml-2">
          <Bell className="w-4 h-4" />
          <span className="absolute top-2 right-2.5 w-1.5 h-1.5 bg-[var(--color-loss)] rounded-full border border-[var(--bg-surface)]" />
        </button>

        {/* User Avatar with dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen((v) => !v)}
            className="w-8 h-8 rounded-full bg-[var(--accent-primary-muted)] border border-[var(--accent-primary-border)] flex items-center justify-center cursor-pointer hover:border-[var(--border-strong)] transition-colors text-[var(--accent-primary)] text-[11px] font-bold"
            title={user?.name ?? 'User'}
          >
            {initials}
          </button>

          {dropdownOpen && (
            <div
              style={{
                position: 'absolute',
                right: 0,
                top: 'calc(100% + 8px)',
                minWidth: '180px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-md)',
                boxShadow: 'var(--shadow-md)',
                zIndex: 50,
                overflow: 'hidden',
              }}
            >
              {user && (
                <div
                  style={{
                    padding: '12px 14px',
                    borderBottom: '1px solid var(--border-subtle)',
                  }}
                >
                  <p style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-primary)', margin: 0 }}>
                    {user.name}
                  </p>
                  <p style={{ fontSize: '11px', color: 'var(--text-tertiary)', margin: '2px 0 0' }}>
                    {user.email}
                  </p>
                </div>
              )}
              <button
                onClick={handleLogout}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  width: '100%',
                  padding: '10px 14px',
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  color: 'var(--color-loss)',
                  fontSize: '13px',
                  fontWeight: '500',
                  textAlign: 'left',
                }}
              >
                <LogOut className="w-4 h-4" />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
