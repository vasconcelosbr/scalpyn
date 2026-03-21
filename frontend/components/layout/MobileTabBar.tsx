"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Eye, BarChart3, LineChart, Settings } from "lucide-react";

const TABS = [
  { name: "Dashboard", href: "/",           icon: LayoutDashboard },
  { name: "Watchlist", href: "/watchlist",  icon: Eye             },
  { name: "Positions", href: "/trading-desk/positions", icon: BarChart3 },
  { name: "Analytics", href: "/analytics",  icon: LineChart       },
  { name: "Settings",  href: "/settings/general", icon: Settings  },
] as const;

export function MobileTabBar() {
  const pathname = usePathname();

  return (
    <nav className="mobile-tab-bar" aria-label="Mobile navigation">
      <div className="mobile-tab-bar-inner">
        {TABS.map((tab) => {
          const isActive =
            pathname === tab.href ||
            (tab.href !== "/" && pathname.startsWith(tab.href));
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`mobile-tab-item${isActive ? " active" : ""}`}
              aria-current={isActive ? "page" : undefined}
            >
              <tab.icon size={22} strokeWidth={isActive ? 2 : 1.5} />
              <span>{tab.name}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
