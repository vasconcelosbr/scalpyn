"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { 
  BarChart2, 
  Settings, 
  Activity, 
  Layers, 
  List, 
  Home,
  ShieldCheck,
  TrendingUp,
  FileText,
  Target,
  Zap,
  ShieldOff,
  Shield,
  Brain,
  Repeat,
  Bell,
  Eye,
  LayoutDashboard,
  Sliders
} from 'lucide-react';
import { useState } from 'react';

const OVERVIEW_ITEMS = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Watchlist', href: '/watchlist', icon: Eye },
];

const TRADING_ITEMS = [
  { name: 'Trades & P&L', href: '/trades', icon: TrendingUp },
  { name: 'Reports', href: '/reports', icon: FileText },
  { name: 'Pools', href: '/pools', icon: Layers },
  { name: 'Profiles', href: '/profiles', icon: Sliders },
];

const CONFIG_ITEMS = [
  { name: 'General', href: '/settings/general', icon: Settings },
  { name: 'Indicators', href: '/settings/indicators', icon: Activity },
  { name: 'Score Engine', href: '/settings/score', icon: Target },
  { name: 'Signal Rules', href: '/settings/signal', icon: Zap },
  { name: 'Block Rules', href: '/settings/block', icon: ShieldOff },
  { name: 'Risk Management', href: '/settings/risk', icon: Shield },
  { name: 'Strategies', href: '/settings/strategies', icon: Brain },
  { name: 'Exchanges', href: '/settings/exchanges', icon: Repeat },
  { name: 'Notifications', href: '/settings/notifications', icon: Bell },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  const NavItem = ({ item }: { item: any }) => {
    const isActive = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
    return (
      <Link href={item.href} className={`nav-item ${isActive ? 'active' : ''}`}>
        <item.icon className="icon" />
        {!collapsed && <span>{item.name}</span>}
      </Link>
    );
  };

  return (
    <aside className={`sidebar ${collapsed ? 'w-[64px]' : 'w-[240px] max-lg:w-[64px] max-md:hidden'} fixed left-0 top-0 bottom-0 bg-[var(--bg-surface)] border-r border-[var(--border-subtle)] flex flex-col z-50 transition-all duration-350 ease-out`}>
      <div className="flex h-[56px] items-center px-4 border-b border-[var(--border-subtle)] cursor-pointer" onClick={() => setCollapsed(!collapsed)}>
        <div className="w-[32px] h-[32px] shrink-0 bg-[var(--accent-primary-muted)] text-[var(--accent-primary)] rounded-[var(--radius-sm)] flex items-center justify-center border border-[var(--accent-primary-border)] font-bold text-lg leading-none">
          S
        </div>
        {!collapsed && <span className="ml-3 font-bold text-[13px] tracking-[0.1em] text-[var(--text-primary)]">SCALPYN</span>}
      </div>
      
      <nav className="flex-1 overflow-y-auto overflow-x-hidden space-y-1 pb-4 custom-scrollbar flex flex-col">
        {!collapsed && <div className="nav-group-label">Overview</div>}
        {collapsed && <div className="h-4"></div>}
        <div className="flex flex-col">
          {OVERVIEW_ITEMS.map((item) => <NavItem key={item.name} item={item} />)}
        </div>

        {!collapsed && <div className="nav-group-label mt-2">Trading</div>}
        {collapsed && <div className="h-4 border-b border-[var(--border-subtle)] mx-4 mb-2"></div>}
        <div className="flex flex-col">
          {TRADING_ITEMS.map((item) => <NavItem key={item.name} item={item} />)}
        </div>

        {!collapsed && <div className="nav-group-label mt-2">Configuration</div>}
        {collapsed && <div className="h-4 border-b border-[var(--border-subtle)] mx-4 mb-2"></div>}
        <div className="flex flex-col">
          {CONFIG_ITEMS.map((item) => <NavItem key={item.name} item={item} />)}
        </div>
      </nav>
      
      <div className="border-t border-[var(--border-subtle)] p-4 flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <div className="w-[32px] h-[32px] shrink-0 rounded-full bg-[var(--bg-hover)] border border-[var(--border-default)] flex items-center justify-center overflow-hidden">
            <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Scalpyn" alt="User Avatar" className="w-full h-full object-cover" />
          </div>
          {!collapsed && (
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-[var(--text-primary)] truncate">Ricardo T.</div>
              <div className="text-[11px] text-[var(--text-secondary)] bg-[var(--bg-active)] px-2 py-0.5 rounded-full inline-block mt-0.5">Admin</div>
            </div>
          )}
        </div>
        
        <button 
          onClick={() => {
            localStorage.removeItem('token');
            window.location.href = '/';
          }}
          className={`flex items-center justify-center gap-2 w-full py-2 rounded-md text-[var(--color-loss)] hover:bg-[var(--color-loss-muted)] hover:border-[var(--color-loss-border)] border border-transparent transition-colors ${collapsed ? '' : 'px-3'}`}
          title="Log Out"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
          {!collapsed && <span className="text-[13px] font-medium tracking-wide">Log Out</span>}
        </button>
      </div>
    </aside>
  );
}
