"use client";

import {
  useEffect,
  useMemo,
  useState,
  useRef,
  useCallback,
  memo,
} from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  PieChart,
  Pie,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  LineChart,
  Line,
} from "recharts";
import {
  TrendingUp,
  TrendingDown,
  Wallet,
  Trophy,
  Target,
  ChevronDown,
  ChevronUp,
  ArrowUpRight,
  ArrowDownRight,
  BarChart2,
  RefreshCw,
  Minus,
} from "lucide-react";
import { apiGet } from "@/lib/api";

const C = {
  profit: "#34D399",
  loss: "#F87171",
  blue: "#4F7BF7",
  surface: "#0C0D12",
  elevated: "#12131A",
  border: "rgba(255,255,255,0.07)",
  textPrimary: "#E8ECF4",
  textSecondary: "#8B92A5",
  textTertiary: "#555B6E",
} as const;

interface CapitalPoint {
  time: string;
  value: number;
  symbol?: string;
  pnl?: number;
}

interface DashboardOverview {
  today_pnl: number;
  consolidated_pnl: number;
  realized_total_pnl: number;
  unrealized_pnl: number;
  win_rate: number;
  open_positions_count: number;
  open_positions: Array<{
    id: string;
    symbol: string;
    market_type: string;
    direction: string;
    quantity: number;
    entry_price: number;
    current_price: number;
    current_value: number;
    profit_loss: number;
    profit_loss_pct: number;
  }>;
  portfolio_value: number;
  spot_value: number;
  futures_value: number;
  capital_evolution: CapitalPoint[];
  source: string;
}

interface PnlSummary {
  total_pnl: number;
  total_pnl_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_profit: number;
  avg_loss: number;
  profit_factor: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number;
  avg_holding_seconds: number;
  best_trade: number;
  worst_trade: number;
}

function fmtUsd(v: number, compact = false): string {
  if (compact && Math.abs(v) >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(v);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(v ?? 0);
}

function fmtPct(v: number, showPlus = true): string {
  const sign = showPlus && v > 0 ? "+" : "";
  return `${sign}${(v ?? 0).toFixed(2)}%`;
}

function fmtDate(iso: string, mode: "short" | "day" = "short"): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (mode === "day") return d.toLocaleDateString("en", { month: "short", day: "numeric" });
  return d.toLocaleString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function fmtHours(seconds: number): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function tone(v: number): "profit" | "loss" | "neutral" {
  if (v > 0) return "profit";
  if (v < 0) return "loss";
  return "neutral";
}

function useCountUp(target: number, duration = 800, ready = true): number {
  const [display, setDisplay] = useState(0);
  const raf = useRef<number | null>(null);
  const startRef = useRef<number | null>(null);
  const fromRef = useRef(0);

  useEffect(() => {
    if (!ready) return;
    fromRef.current = display;
    startRef.current = null;
    if (raf.current) cancelAnimationFrame(raf.current);

    const step = (ts: number) => {
      if (!startRef.current) startRef.current = ts;
      const progress = Math.min((ts - startRef.current) / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3);
      setDisplay(fromRef.current + (target - fromRef.current) * ease);
      if (progress < 1) raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [target, ready]);

  return display;
}

function MiniSparkline({ data, color }: { data: number[]; color: string }) {
  const points = useMemo(
    () => data.map((v, i) => ({ i, v })),
    [data],
  );
  if (points.length < 2) return null;
  return (
    <ResponsiveContainer width="100%" height={36}>
      <LineChart data={points} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
        <Line
          type="monotone"
          dataKey="v"
          stroke={color}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

interface KpiCardProps {
  label: string;
  mainValue: number;
  isCurrency?: boolean;
  helperText?: string;
  delta?: number;
  deltaLabel?: string;
  sparkData?: number[];
  sparkColor?: string;
  delay?: number;
  ready?: boolean;
}

const KpiCard = memo(function KpiCard({
  label,
  mainValue,
  isCurrency = true,
  helperText,
  delta,
  deltaLabel,
  sparkData,
  sparkColor,
  delay = 0,
  ready = true,
}: KpiCardProps) {
  const animated = useCountUp(mainValue, 900, ready);
  const t = tone(mainValue);
  const valueColor = t === "profit" ? C.profit : t === "loss" ? C.loss : C.textPrimary;

  return (
    <div
      className="relative rounded-2xl overflow-hidden p-5 flex flex-col gap-3"
      style={{
        background: C.elevated,
        border: `1px solid ${C.border}`,
        boxShadow: "0 4px 24px rgba(0,0,0,0.35)",
        animation: `fadeInUp 500ms ${delay}ms ease-out backwards`,
      }}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em]" style={{ color: C.textTertiary }}>
          {label}
        </span>
        {delta !== undefined && (
          <span
            className="flex items-center gap-0.5 text-[11px] font-semibold px-1.5 py-0.5 rounded-md"
            style={{
              color: delta >= 0 ? C.profit : C.loss,
              background: delta >= 0 ? "rgba(52,211,153,0.10)" : "rgba(248,113,113,0.10)",
            }}
          >
            {delta >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
            {Math.abs(delta).toFixed(2)}%
          </span>
        )}
      </div>

      <div className="flex items-end justify-between gap-3">
        <span
          className="text-[28px] font-bold leading-none tabular-nums"
          style={{ color: valueColor, fontVariantNumeric: "tabular-nums" }}
        >
          {isCurrency ? fmtUsd(animated) : `${animated.toFixed(2)}%`}
        </span>
        {sparkData && sparkData.length > 1 && (
          <div className="w-24 flex-shrink-0 opacity-70">
            <MiniSparkline data={sparkData} color={sparkColor ?? valueColor} />
          </div>
        )}
      </div>

      {helperText && (
        <p className="text-[12px] leading-tight" style={{ color: C.textTertiary }}>
          {helperText}
        </p>
      )}

      {deltaLabel && (
        <p className="text-[11px]" style={{ color: C.textSecondary }}>
          {deltaLabel}
        </p>
      )}

      <div
        className="absolute inset-x-0 bottom-0 h-[2px]"
        style={{
          background:
            t === "profit"
              ? "linear-gradient(90deg, transparent, rgba(52,211,153,0.5), transparent)"
              : t === "loss"
                ? "linear-gradient(90deg, transparent, rgba(248,113,113,0.4), transparent)"
                : "linear-gradient(90deg, transparent, rgba(79,123,247,0.35), transparent)",
        }}
      />
    </div>
  );
});

function EquityCurve({ data }: { data: CapitalPoint[] }) {
  const chartData = useMemo(
    () =>
      data.map((p) => ({
        label: fmtDate(p.time, "day"),
        value: p.value,
        pnl: p.pnl ?? 0,
        symbol: p.symbol ?? "",
      })),
    [data],
  );

  const baseline = data[0]?.value ?? 0;
  const last = data[data.length - 1]?.value ?? baseline;
  const isUp = last >= baseline;
  const lineColor = isUp ? C.profit : C.loss;

  const CustomTooltip = useCallback(
    ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; payload: { pnl: number; symbol: string } }>; label?: string }) => {
      if (!active || !payload?.length) return null;
      const val = payload[0].value;
      const pnl = payload[0].payload.pnl;
      const sym = payload[0].payload.symbol;
      return (
        <div
          className="rounded-xl px-4 py-3 shadow-xl"
          style={{
            background: "#1A1C28",
            border: "1px solid rgba(255,255,255,0.10)",
            minWidth: 170,
          }}
        >
          <p className="text-[11px] font-semibold mb-2" style={{ color: C.textSecondary }}>
            {label}
          </p>
          <p className="text-[17px] font-bold tabular-nums" style={{ color: C.textPrimary }}>
            {fmtUsd(val)}
          </p>
          {pnl !== 0 && (
            <p
              className="text-[12px] font-semibold mt-1 tabular-nums"
              style={{ color: pnl >= 0 ? C.profit : C.loss }}
            >
              {pnl >= 0 ? "+" : ""}
              {fmtUsd(pnl)}
              {sym ? ` · ${sym.replace("_USDT", "")}` : ""}
            </p>
          )}
        </div>
      );
    },
    [],
  );

  if (chartData.length < 2) {
    return (
      <div className="flex items-center justify-center h-64" style={{ color: C.textTertiary }}>
        <div className="text-center">
          <BarChart2 size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No trade history yet.</p>
        </div>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={chartData} margin={{ top: 12, right: 16, left: 4, bottom: 0 }}>
        <defs>
          <linearGradient id="equityUp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={C.profit} stopOpacity={0.20} />
            <stop offset="85%" stopColor={C.profit} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="equityDown" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={C.loss} stopOpacity={0.18} />
            <stop offset="85%" stopColor={C.loss} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: C.textTertiary, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: C.textTertiary, fontSize: 11 }}
          tickFormatter={(v: number) => `$${Math.round(v).toLocaleString()}`}
          tickLine={false}
          axisLine={false}
          width={72}
        />
        <Tooltip content={<CustomTooltip />} />
        <Area
          type="monotone"
          dataKey="value"
          stroke={lineColor}
          strokeWidth={2}
          fill={isUp ? "url(#equityUp)" : "url(#equityDown)"}
          dot={false}
          animationDuration={1200}
          animationEasing="ease-out"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function DailyPnlChart({ data }: { data: CapitalPoint[] }) {
  const daily = useMemo(() => {
    const map: Record<string, number> = {};
    for (const p of data) {
      if (!p.pnl || !p.time) continue;
      const day = p.time.slice(0, 10);
      map[day] = (map[day] ?? 0) + p.pnl;
    }
    return Object.entries(map)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, pnl]) => ({
        label: fmtDate(`${day}T00:00:00Z`, "day"),
        pnl: Math.round(pnl * 100) / 100,
      }));
  }, [data]);

  const CustomTooltip = useCallback(
    ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) => {
      if (!active || !payload?.length) return null;
      const val = payload[0].value;
      return (
        <div
          className="rounded-xl px-4 py-3 shadow-xl"
          style={{ background: "#1A1C28", border: "1px solid rgba(255,255,255,0.10)" }}
        >
          <p className="text-[11px] mb-1" style={{ color: C.textSecondary }}>{label}</p>
          <p
            className="text-[15px] font-bold tabular-nums"
            style={{ color: val >= 0 ? C.profit : C.loss }}
          >
            {val >= 0 ? "+" : ""}
            {fmtUsd(val)}
          </p>
        </div>
      );
    },
    [],
  );

  if (daily.length < 1) {
    return (
      <div className="flex items-center justify-center h-40" style={{ color: C.textTertiary }}>
        <p className="text-sm">No daily data.</p>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={daily} margin={{ top: 8, right: 8, left: 4, bottom: 0 }} barSize={14}>
        <CartesianGrid strokeDasharray="3 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: C.textTertiary, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: C.textTertiary, fontSize: 11 }}
          tickFormatter={(v: number) => `$${v >= 0 ? "+" : ""}${Math.round(v)}`}
          tickLine={false}
          axisLine={false}
          width={68}
        />
        <Tooltip content={<CustomTooltip />} />
        <Bar dataKey="pnl" radius={[3, 3, 0, 0]} animationDuration={900}>
          {daily.map((entry, i) => (
            <Cell key={i} fill={entry.pnl >= 0 ? C.profit : C.loss} opacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function WinLossChart({ winning, losing }: { winning: number; losing: number }) {
  const total = winning + losing;
  const data = [
    { name: "Wins", value: winning, color: C.profit },
    { name: "Losses", value: losing, color: C.loss },
  ];

  const CustomLabel = useCallback(
    ({ cx, cy }: { cx: number; cy: number }) => (
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central">
        <tspan x={cx} dy="-6" fontSize={20} fontWeight="700" fill={C.textPrimary}>
          {total > 0 ? Math.round((winning / total) * 100) : 0}%
        </tspan>
        <tspan x={cx} dy="20" fontSize={11} fill={C.textSecondary}>
          win rate
        </tspan>
      </text>
    ),
    [winning, total],
  );

  return (
    <ResponsiveContainer width="100%" height={160}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={52}
          outerRadius={72}
          paddingAngle={3}
          dataKey="value"
          labelLine={false}
          label={<CustomLabel cx={0} cy={0} />}
          animationBegin={300}
          animationDuration={900}
        >
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.color} opacity={0.9} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}

interface StatRowProps {
  label: string;
  value: string;
  tone?: "profit" | "loss" | "neutral";
}
function StatRow({ label, value, tone: t = "neutral" }: StatRowProps) {
  const color = t === "profit" ? C.profit : t === "loss" ? C.loss : C.textPrimary;
  return (
    <div className="flex items-center justify-between py-2" style={{ borderBottom: `1px solid ${C.border}` }}>
      <span className="text-[12px]" style={{ color: C.textSecondary }}>{label}</span>
      <span className="text-[13px] font-semibold tabular-nums" style={{ color }}>{value}</span>
    </div>
  );
}

type SortKey = "time" | "symbol" | "pnl";
type SortDir = "asc" | "desc";

function TradeTable({ data, loading }: { data: CapitalPoint[]; loading: boolean }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const trades = useMemo(() => {
    const rows = data
      .filter((p) => p.symbol && p.pnl !== undefined && p.pnl !== 0)
      .map((p) => ({ time: p.time, symbol: p.symbol ?? "", pnl: p.pnl ?? 0 }));

    const filtered = search
      ? rows.filter((r) => r.symbol.toLowerCase().includes(search.toLowerCase()))
      : rows;

    return [...filtered].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "time") cmp = a.time.localeCompare(b.time);
      else if (sortKey === "symbol") cmp = a.symbol.localeCompare(b.symbol);
      else cmp = a.pnl - b.pnl;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [data, search, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  const SortIcon = ({ k }: { k: SortKey }) =>
    sortKey === k ? (
      sortDir === "desc" ? <ChevronDown size={11} /> : <ChevronUp size={11} />
    ) : (
      <Minus size={11} className="opacity-30" />
    );

  return (
    <div className="rounded-2xl overflow-hidden" style={{ background: C.elevated, border: `1px solid ${C.border}` }}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-4 hover:opacity-80 transition-opacity"
      >
        <div className="flex items-center gap-3">
          <BarChart2 size={15} style={{ color: C.blue }} />
          <span className="font-semibold text-sm" style={{ color: C.textPrimary }}>
            Trade History
          </span>
          <span
            className="text-[11px] px-2 py-0.5 rounded-full font-medium"
            style={{ background: "rgba(79,123,247,0.12)", color: C.blue }}
          >
            {trades.length}
          </span>
        </div>
        {open ? <ChevronUp size={16} style={{ color: C.textTertiary }} /> : <ChevronDown size={16} style={{ color: C.textTertiary }} />}
      </button>

      {open && (
        <div style={{ borderTop: `1px solid ${C.border}` }}>
          <div className="px-5 py-3">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter symbol…"
              className="w-full sm:w-64 rounded-lg px-3 py-2 text-[13px] outline-none"
              style={{
                background: "rgba(255,255,255,0.04)",
                border: `1px solid ${C.border}`,
                color: C.textPrimary,
              }}
            />
          </div>

          {loading ? (
            <div className="px-5 pb-5 space-y-2">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="skeleton h-9 rounded-lg" />
              ))}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead>
                  <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                    {(
                      [
                        { key: "symbol" as SortKey, label: "Asset" },
                        { key: "time" as SortKey, label: "Time" },
                        { key: "pnl" as SortKey, label: "P&L (USDT)" },
                      ] as { key: SortKey; label: string }[]
                    ).map(({ key, label }) => (
                      <th
                        key={key}
                        onClick={() => toggleSort(key)}
                        className="px-5 py-2.5 text-left cursor-pointer select-none hover:opacity-80 transition-opacity"
                        style={{ color: C.textTertiary, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", fontSize: 10 }}
                      >
                        <span className="flex items-center gap-1">
                          {label} <SortIcon k={key} />
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="px-5 py-10 text-center text-[12px]" style={{ color: C.textTertiary }}>
                        No trades found.
                      </td>
                    </tr>
                  ) : (
                    trades.map((t, i) => (
                      <tr
                        key={i}
                        className="hover:opacity-90 transition-colors"
                        style={{
                          borderBottom: `1px solid ${C.border}`,
                          background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.01)",
                        }}
                      >
                        <td className="px-5 py-3 font-semibold" style={{ color: C.textPrimary }}>
                          {t.symbol.replace("_USDT", "")}
                          <span className="ml-1 text-[10px] font-normal" style={{ color: C.textTertiary }}>USDT</span>
                        </td>
                        <td className="px-5 py-3" style={{ color: C.textSecondary }}>
                          {fmtDate(t.time)}
                        </td>
                        <td
                          className="px-5 py-3 font-semibold tabular-nums"
                          style={{ color: t.pnl >= 0 ? C.profit : C.loss }}
                        >
                          {t.pnl >= 0 ? "+" : ""}
                          {fmtUsd(t.pnl)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SectionHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em]" style={{ color: C.textTertiary }}>
        {title}
      </h2>
      {sub && <p className="text-[12px] mt-0.5" style={{ color: C.textTertiary }}>{sub}</p>}
    </div>
  );
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-2xl p-5 ${className}`}
      style={{
        background: C.elevated,
        border: `1px solid ${C.border}`,
        boxShadow: "0 4px 20px rgba(0,0,0,0.30)",
      }}
    >
      {children}
    </div>
  );
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastFetched, setLastFetched] = useState<Date | null>(null);

  const fetchAll = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);

    try {
      const [ov, pl] = await Promise.all([
        apiGet<DashboardOverview>("/analytics/dashboard?days=30&min_value_usdt=10"),
        apiGet<PnlSummary>("/analytics/pnl"),
      ]);
      setOverview(ov);
      setPnl(pl);
      setLastFetched(new Date());
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const sparkline = useMemo(() => {
    if (!overview?.capital_evolution) return [];
    const pts = overview.capital_evolution;
    return pts.slice(-14).map((p) => p.value);
  }, [overview]);

  const ready = !loading && !!overview;

  return (
    <div className="space-y-6 pb-8" style={{ animation: "fadeIn 400ms ease-out" }}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: C.textPrimary }}>
            Performance
          </h1>
          <p className="text-[13px] mt-1" style={{ color: C.textSecondary }}>
            Portfolio analytics · Gate.io backed
          </p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {lastFetched && (
            <span className="text-[11px]" style={{ color: C.textTertiary }}>
              {lastFetched.toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button
            onClick={() => fetchAll(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium transition-opacity hover:opacity-80 disabled:opacity-40"
            style={{ background: "rgba(79,123,247,0.12)", color: C.blue, border: "1px solid rgba(79,123,247,0.20)" }}
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="rounded-2xl p-5 h-36" style={{ background: C.elevated, border: `1px solid ${C.border}` }}>
              <div className="skeleton h-3 w-20 rounded mb-4" />
              <div className="skeleton h-7 w-28 rounded mb-3" />
              <div className="skeleton h-2.5 w-24 rounded" />
            </div>
          ))}
        </div>
      ) : overview ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="Total P&L"
              mainValue={overview.realized_total_pnl}
              helperText={pnl ? `ROI ${fmtPct(pnl.total_pnl_pct)} · ${pnl.total_trades} trades` : undefined}
              delta={pnl?.total_pnl_pct}
              sparkData={sparkline}
              delay={0}
              ready={ready}
            />
            <KpiCard
              label="Today's P&L"
              mainValue={overview.today_pnl}
              helperText={`Unrealized: ${fmtUsd(overview.unrealized_pnl)}`}
              delay={80}
              ready={ready}
            />
            <KpiCard
              label="Portfolio Value"
              mainValue={overview.portfolio_value}
              helperText={`Spot ${fmtUsd(overview.spot_value, true)} · Futures ${fmtUsd(overview.futures_value, true)}`}
              delay={160}
              ready={ready}
            />
            <KpiCard
              label="Win Rate"
              mainValue={pnl?.win_rate ?? overview.win_rate}
              isCurrency={false}
              helperText={
                pnl
                  ? `${pnl.winning_trades}W · ${pnl.losing_trades}L · PF ${pnl.profit_factor?.toFixed(2) ?? "—"}`
                  : undefined
              }
              delay={240}
              ready={ready}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[1.6fr,1fr] gap-6">
            <Card>
              <div className="flex items-center justify-between mb-5">
                <div>
                  <SectionHeader title="Equity Curve" />
                </div>
                <div className="flex items-center gap-2">
                  {overview.capital_evolution.length > 0 && (
                    <span
                      className="flex items-center gap-1 text-[12px] font-semibold"
                      style={{
                        color: overview.realized_total_pnl >= 0 ? C.profit : C.loss,
                      }}
                    >
                      {overview.realized_total_pnl >= 0 ? (
                        <TrendingUp size={14} />
                      ) : (
                        <TrendingDown size={14} />
                      )}
                      {fmtUsd(overview.realized_total_pnl)}
                    </span>
                  )}
                  <span className="text-[11px] px-2 py-1 rounded-lg" style={{ background: "rgba(255,255,255,0.04)", color: C.textTertiary }}>30D</span>
                </div>
              </div>
              <EquityCurve data={overview.capital_evolution} />
            </Card>

            <Card>
              <SectionHeader title="Distribution" sub="Win vs Loss breakdown" />
              <WinLossChart
                winning={pnl?.winning_trades ?? 0}
                losing={pnl?.losing_trades ?? 0}
              />
              <div className="mt-3 flex gap-4 justify-center">
                <span className="flex items-center gap-1.5 text-[12px]" style={{ color: C.textSecondary }}>
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: C.profit }} />
                  {pnl?.winning_trades ?? 0} wins
                </span>
                <span className="flex items-center gap-1.5 text-[12px]" style={{ color: C.textSecondary }}>
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: C.loss }} />
                  {pnl?.losing_trades ?? 0} losses
                </span>
              </div>

              {pnl && (
                <div className="mt-4 space-y-0">
                  <StatRow label="Profit Factor" value={pnl.profit_factor?.toFixed(2) ?? "—"} tone={pnl.profit_factor && pnl.profit_factor > 1 ? "profit" : "loss"} />
                  <StatRow label="Sharpe Ratio" value={pnl.sharpe_ratio?.toFixed(2) ?? "—"} tone={pnl.sharpe_ratio && pnl.sharpe_ratio > 1 ? "profit" : "neutral"} />
                  <StatRow label="Max Drawdown" value={fmtUsd(pnl.max_drawdown_pct)} tone={pnl.max_drawdown_pct > 0 ? "loss" : "neutral"} />
                  <StatRow label="Best Trade" value={fmtUsd(pnl.best_trade)} tone="profit" />
                  <StatRow label="Worst Trade" value={fmtUsd(pnl.worst_trade)} tone="loss" />
                  <StatRow label="Avg Hold" value={fmtHours(pnl.avg_holding_seconds)} />
                </div>
              )}
            </Card>
          </div>

          <Card>
            <div className="flex items-center justify-between mb-5">
              <SectionHeader title="Daily P&L" sub="Realized P&L per day" />
            </div>
            <DailyPnlChart data={overview.capital_evolution} />
          </Card>

          {overview.open_positions.length > 0 && (
            <Card>
              <SectionHeader title="Open Positions" sub={`${overview.open_positions.length} active`} />
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                      {["Asset", "Direction", "Entry", "Current", "Value", "P&L"].map((h) => (
                        <th
                          key={h}
                          className="px-4 py-2.5 text-left"
                          style={{ color: C.textTertiary, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", fontSize: 10 }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {overview.open_positions.map((pos) => (
                      <tr
                        key={pos.id}
                        className="hover:bg-white/[0.02] transition-colors"
                        style={{ borderBottom: `1px solid ${C.border}` }}
                      >
                        <td className="px-4 py-3 font-semibold" style={{ color: C.textPrimary }}>
                          {pos.symbol.replace("_USDT", "")}
                          <span className="ml-1 text-[10px] font-normal" style={{ color: C.textTertiary }}>{pos.market_type}</span>
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase"
                            style={{
                              background: pos.direction === "long" ? "rgba(52,211,153,0.10)" : "rgba(248,113,113,0.10)",
                              color: pos.direction === "long" ? C.profit : C.loss,
                            }}
                          >
                            {pos.direction}
                          </span>
                        </td>
                        <td className="px-4 py-3 tabular-nums" style={{ color: C.textSecondary }}>{fmtUsd(pos.entry_price)}</td>
                        <td className="px-4 py-3 tabular-nums" style={{ color: C.textPrimary }}>{fmtUsd(pos.current_price)}</td>
                        <td className="px-4 py-3 tabular-nums" style={{ color: C.textSecondary }}>{fmtUsd(pos.current_value)}</td>
                        <td className="px-4 py-3 tabular-nums font-semibold" style={{ color: pos.profit_loss >= 0 ? C.profit : C.loss }}>
                          {pos.profit_loss >= 0 ? "+" : ""}
                          {fmtUsd(pos.profit_loss)}
                          <span className="ml-1 text-[11px]">({fmtPct(pos.profit_loss_pct)})</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          <TradeTable data={overview.capital_evolution} loading={loading} />
        </>
      ) : (
        <div
          className="flex flex-col items-center justify-center py-24 rounded-2xl"
          style={{ background: C.elevated, border: `1px solid ${C.border}` }}
        >
          <Wallet size={36} className="mb-4 opacity-30" style={{ color: C.textTertiary }} />
          <p className="text-sm" style={{ color: C.textTertiary }}>Unable to load dashboard data.</p>
          <button
            onClick={() => fetchAll()}
            className="mt-4 px-4 py-2 rounded-xl text-[13px] font-medium transition-opacity hover:opacity-80"
            style={{ background: "rgba(79,123,247,0.12)", color: C.blue }}
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
