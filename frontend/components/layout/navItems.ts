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
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface NavItem {
  name: string;
  href: string;
  icon: LucideIcon;
}

export interface NavSection {
  label: string;
  items: NavItem[];
}

export const OVERVIEW_ITEMS: NavItem[] = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "Watchlist", href: "/watchlist", icon: Eye },
];

export const TRADING_ITEMS: NavItem[] = [
  { name: "Spot", href: "/trading-desk/spot", icon: Activity },
  { name: "Futures", href: "/trading-desk/futures", icon: CandlestickChart },
  { name: "Trades & P&L", href: "/trades", icon: TrendingUp },
  { name: "Reports", href: "/reports", icon: FileText },
  { name: "Pools", href: "/pools", icon: Layers },
  { name: "Profiles", href: "/profiles", icon: Sliders },
];

export const BACKOFFICE_ITEMS: NavItem[] = [
  { name: "Exec Dashboard", href: "/dashboard", icon: BarChart2 },
  { name: "Operations", href: "/backoffice", icon: Monitor },
  { name: "Asset Trace", href: "/assets", icon: Search },
  { name: "Decision Log", href: "/decisions", icon: ClipboardList },
  { name: "Data Monitor", href: "/data", icon: Database },
  { name: "Alert Center", href: "/alerts", icon: AlertTriangle },
  { name: "Replay", href: "/replay", icon: PlayCircle },
  { name: "Admin", href: "/admin", icon: Users },
];

export const CONFIG_ITEMS: NavItem[] = [
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

export const NAV_SECTIONS: NavSection[] = [
  { label: "Overview", items: OVERVIEW_ITEMS },
  { label: "Trading", items: TRADING_ITEMS },
  { label: "Back Office", items: BACKOFFICE_ITEMS },
  { label: "Configuration", items: CONFIG_ITEMS },
];
