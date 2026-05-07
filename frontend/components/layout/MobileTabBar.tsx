"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Eye, Activity, FileText, Menu } from "lucide-react";
import { useAppStore } from "@/stores/useAppStore";

const TABS = [
  { name: "Dashboard", href: "/",                    icon: LayoutDashboard },
  { name: "Watchlist", href: "/watchlist",           icon: Eye             },
  { name: "Trading",   href: "/trading-desk/spot",  icon: Activity        },
  { name: "Reports",   href: "/reports",             icon: FileText        },
] as const;

export function MobileTabBar() {
  const pathname = usePathname();
  const { mobileNavOpen, openMobileNav } = useAppStore();

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

        {/* Menu button — opens full nav drawer */}
        <button
          onClick={openMobileNav}
          className={`mobile-tab-item${mobileNavOpen ? " active" : ""}`}
          aria-label="Open navigation menu"
          aria-expanded={mobileNavOpen}
        >
          <Menu size={22} strokeWidth={mobileNavOpen ? 2 : 1.5} />
          <span>Menu</span>
        </button>
      </div>
    </nav>
  );
}
