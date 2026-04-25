"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAppStore } from "@/stores/useAppStore";
import { useAuthStore } from "@/stores/useAuthStore";
import {
  BarChart2,
  Settings,
  Layers,
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
  Sliders,
  Sparkles,
  Monitor,
  Search,
  ClipboardList,
  AlertTriangle,
  Database,
  PlayCircle,
  Users,
  Activity,
  CandlestickChart,
  LogOut,
  X,
} from "lucide-react";

const OVERVIEW_ITEMS = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "Watchlist", href: "/watchlist", icon: Eye },
];

const TRADING_ITEMS = [
  { name: "Spot", href: "/trading-desk/spot", icon: Activity },
  { name: "Futures", href: "/trading-desk/futures", icon: CandlestickChart },
  { name: "Trades & P&L", href: "/trades", icon: TrendingUp },
  { name: "Reports", href: "/reports", icon: FileText },
  { name: "Pools", href: "/pools", icon: Layers },
  { name: "Profiles", href: "/profiles", icon: Sliders },
];

const BACKOFFICE_ITEMS = [
  { name: "Exec Dashboard", href: "/dashboard", icon: BarChart2 },
  { name: "Operations", href: "/backoffice", icon: Monitor },
  { name: "Asset Trace", href: "/assets", icon: Search },
  { name: "Decision Log", href: "/decisions", icon: ClipboardList },
  { name: "Data Monitor", href: "/data", icon: Database },
  { name: "Alert Center", href: "/alerts", icon: AlertTriangle },
  { name: "Replay", href: "/replay", icon: PlayCircle },
  { name: "Admin", href: "/admin", icon: Users },
];

const CONFIG_ITEMS = [
  { name: "General", href: "/settings/general", icon: Settings },
  { name: "Score Engine", href: "/settings/score", icon: Target },
  { name: "Signal Rules", href: "/settings/signal", icon: Zap },
  { name: "Block Rules", href: "/settings/block", icon: ShieldOff },
  { name: "Risk Management", href: "/settings/risk", icon: Shield },
  { name: "Strategies", href: "/settings/strategies", icon: Brain },
  { name: "AI Skills", href: "/settings/skills", icon: Sparkles },
  { name: "Exchanges", href: "/settings/exchanges", icon: Repeat },
  { name: "Notifications", href: "/settings/notifications", icon: Bell },
];

const NAV_SECTIONS = [
  { label: "Overview", items: OVERVIEW_ITEMS },
  { label: "Trading", items: TRADING_ITEMS },
  { label: "Back Office", items: BACKOFFICE_ITEMS },
  { label: "Configuration", items: CONFIG_ITEMS },
];

export function MobileNavDrawer() {
  const pathname = usePathname();
  const router = useRouter();
  const { mobileNavOpen, closeMobileNav } = useAppStore();
  const { user, logout } = useAuthStore();

  useEffect(() => {
    if (mobileNavOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => { document.body.style.overflow = ""; };
  }, [mobileNavOpen]);

  useEffect(() => {
    closeMobileNav();
  }, [pathname]);

  function handleLogout() {
    logout();
    closeMobileNav();
    router.push("/login");
  }

  const initials = user?.name
    ? user.name.split(" ").slice(0, 2).map((w: string) => w[0].toUpperCase()).join("")
    : "S";

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={closeMobileNav}
        className="md:hidden fixed inset-0 z-[70] bg-black/60 backdrop-blur-sm transition-opacity duration-300"
        style={{
          opacity: mobileNavOpen ? 1 : 0,
          pointerEvents: mobileNavOpen ? "auto" : "none",
        }}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <aside
        className="md:hidden fixed top-0 left-0 bottom-0 w-[280px] z-[80] flex flex-col"
        style={{
          background: "var(--bg-surface)",
          borderRight: "1px solid var(--border-subtle)",
          transform: mobileNavOpen ? "translateX(0)" : "translateX(-100%)",
          transition: "transform 0.3s cubic-bezier(0.4,0,0.2,1)",
          willChange: "transform",
        }}
        aria-label="Mobile navigation"
      >
        {/* Header */}
        <div
          className="flex h-[56px] items-center justify-between px-4 shrink-0"
          style={{ borderBottom: "1px solid var(--border-subtle)" }}
        >
          <div className="flex items-center gap-3">
            <div
              className="w-[32px] h-[32px] shrink-0 rounded-[var(--radius-sm)] flex items-center justify-center font-bold text-lg leading-none"
              style={{
                background: "var(--accent-primary-muted)",
                color: "var(--accent-primary)",
                border: "1px solid var(--accent-primary-border)",
              }}
            >
              S
            </div>
            <span
              className="font-bold text-[13px] tracking-[0.1em]"
              style={{ color: "var(--text-primary)" }}
            >
              SCALPYN
            </span>
          </div>
          <button
            onClick={closeMobileNav}
            className="w-8 h-8 flex items-center justify-center rounded-lg transition-colors"
            style={{ color: "var(--text-secondary)" }}
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>

        {/* Nav sections */}
        <nav className="flex-1 overflow-y-auto pb-4" style={{ overscrollBehavior: "contain" }}>
          {NAV_SECTIONS.map((section) => (
            <div key={section.label} className="mt-1">
              <div
                className="px-4 py-2 text-[10px] font-semibold uppercase tracking-[0.08em]"
                style={{ color: "var(--text-tertiary)" }}
              >
                {section.label}
              </div>
              {section.items.map((item) => {
                const isActive =
                  pathname === item.href ||
                  (item.href !== "/" && pathname.startsWith(item.href));
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="flex items-center gap-3 px-4 py-2.5 mx-2 rounded-lg text-[13px] font-medium transition-colors"
                    style={{
                      color: isActive ? "var(--accent-primary)" : "var(--text-secondary)",
                      background: isActive ? "var(--accent-primary-muted)" : "transparent",
                    }}
                  >
                    <item.icon size={16} strokeWidth={isActive ? 2 : 1.5} className="shrink-0" />
                    <span>{item.name}</span>
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer: user + logout */}
        <div
          className="shrink-0 p-4 flex flex-col gap-3"
          style={{ borderTop: "1px solid var(--border-subtle)" }}
        >
          <div className="flex items-center gap-3">
            <div
              className="w-[32px] h-[32px] shrink-0 rounded-full flex items-center justify-center text-[11px] font-bold"
              style={{
                background: "var(--accent-primary-muted)",
                border: "1px solid var(--accent-primary-border)",
                color: "var(--accent-primary)",
              }}
            >
              {initials}
            </div>
            <div className="flex-1 min-w-0">
              <div
                className="text-[13px] font-semibold truncate"
                style={{ color: "var(--text-primary)" }}
              >
                {user?.name ?? "User"}
              </div>
              <div
                className="text-[11px] truncate"
                style={{ color: "var(--text-tertiary)" }}
              >
                {user?.email ?? ""}
              </div>
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="flex items-center justify-center gap-2 w-full py-2 px-3 rounded-lg text-[13px] font-medium border border-transparent transition-colors"
            style={{ color: "var(--color-loss)" }}
          >
            <LogOut size={14} />
            Log Out
          </button>
        </div>
      </aside>
    </>
  );
}
