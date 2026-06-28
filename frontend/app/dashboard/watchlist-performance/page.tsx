"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Activity, ArrowRight, BarChart3, CheckCircle2, Clock3, RefreshCw, Search, ShieldCheck, Target } from "lucide-react";
import { apiGet } from "@/lib/api";
import {
  countByPriority,
  summarizeWatchlistPerformance,
  type WatchlistPerformanceRow,
  type WatchlistPriority,
} from "@/lib/watchlistPerformance";

const C = {
  surface: "#10121A", elevated: "#161824", border: "rgba(255,255,255,0.08)", text: "#E6E8EE",
  muted: "#8A91A4", dim: "#5A6075", green: "#22B97A", blue: "#4F7BF7", amber: "#F2A33A",
  red: "#E5484D", purple: "#9D7CF7",
} as const;

const PRIORITY_COLOR: Record<WatchlistPriority, string> = {
  "A+": "#16C784", A: "#22B97A", B: "#4F7BF7", C: "#F2A33A", D: "#E5484D", LOW_N: "#8A91A4", BLOCKED: "#5A6075",
};

const fmtRate = (value: number | null, digits = 1) => value === null ? "—" : `${(value * 100).toFixed(digits)}%`;
const fmtPnlPct = (value: number | null) => value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
const fmtUsd = (value: number) => `${value >= 0 ? "+" : "−"}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const fmtHolding = (seconds: number | null) => seconds === null ? "—" : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;

function MetricCard({ label, value, hint, icon, color = C.text }: { label: string; value: string; hint: string; icon: ReactNode; color?: string }) {
  return (
    <div className="rounded-2xl p-5" style={{ background: C.elevated, border: `1px solid ${C.border}` }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.08em]" style={{ color: C.muted }}>{label}</span>
        <span style={{ color }}>{icon}</span>
      </div>
      <div className="mt-3 text-2xl font-semibold tabular-nums" style={{ color }}>{value}</div>
      <p className="mt-1 text-[11px]" style={{ color: C.dim }}>{hint}</p>
    </div>
  );
}

function PriorityBadge({ value }: { value: WatchlistPriority }) {
  const color = PRIORITY_COLOR[value];
  return (
    <span className="inline-flex min-w-14 justify-center rounded-md px-2 py-1 text-[11px] font-bold" style={{ color, border: `1px solid ${color}66`, background: `${color}14` }}>
      {value}
    </span>
  );
}

export default function WatchlistPerformanceDashboard() {
  const [rows, setRows] = useState<WatchlistPerformanceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<WatchlistPriority | "ALL">("ALL");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true); else setLoading(true);
    setError(null);
    try {
      const data = await apiGet<WatchlistPerformanceRow[]>("/shadow-portfolio/report?order_by=ev_score&direction=desc");
      setRows(Array.isArray(data) ? data : []);
      setLastUpdated(new Date());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Falha ao carregar o ranking");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => { void load(true); }, 30_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const summary = useMemo(() => summarizeWatchlistPerformance(rows), [rows]);
  const distribution = useMemo(() => countByPriority(rows), [rows]);
  const maxBucket = Math.max(1, ...distribution.map((bucket) => bucket.count));
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (priority !== "ALL" && row.priority !== priority) return false;
      if (!normalized) return true;
      return `${row.profile_name} ${row.watchlist_name ?? ""}`.toLowerCase().includes(normalized);
    });
  }, [priority, query, rows]);

  return (
    <div className="space-y-6 pb-10" style={{ color: C.text }}>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.12em]" style={{ color: C.blue }}>
            <BarChart3 size={14} /> Performance Intelligence
          </div>
          <h1 className="mt-2 text-2xl font-bold tracking-tight">Watchlist Performance Dashboard</h1>
          <p className="mt-1 max-w-3xl text-[13px]" style={{ color: C.muted }}>
            Ranking único de Shadow Portfolio, Watchlist L3 e APIs L3, ajustado por retorno, amostra e eficiência operacional.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/dashboard/shadow-portfolio" className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[12px]" style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.muted }}>
            Shadow Portfolio <ArrowRight size={13} />
          </Link>
          <Link href="/watchlist" className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[12px]" style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.muted }}>
            Watchlist <ArrowRight size={13} />
          </Link>
          <button onClick={() => void load(true)} disabled={refreshing} className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[12px] font-medium disabled:opacity-50" style={{ color: "#fff", background: C.blue }}>
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} /> Atualizar
          </button>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
        <MetricCard label="Watchlists L3" value={String(summary.total)} hint={`${summary.ranked} com trades concluídos`} icon={<Target size={17} />} color={C.blue} />
        <MetricCard label="Maior EV Score" value={summary.topScore === null ? "—" : summary.topScore.toFixed(2)} hint={rows[0]?.profile_name ?? "Sem ranking"} icon={<Activity size={17} />} color={C.green} />
        <MetricCard label="Amostra confiável" value={String(summary.trusted)} hint="30 ou mais trades concluídos" icon={<ShieldCheck size={17} />} color={C.purple} />
        <MetricCard label="LOW_N" value={String(summary.lowN)} hint="Abaixo do limite de confiança" icon={<Clock3 size={17} />} color={C.amber} />
        <MetricCard label="P&L positivo" value={String(summary.positivePnl)} hint="P&L médio e total positivos" icon={<CheckCircle2 size={17} />} color={C.green} />
      </div>

      {error && <div className="rounded-xl px-4 py-3 text-[12px]" style={{ color: C.red, background: `${C.red}12`, border: `1px solid ${C.red}44` }}>{error}</div>}

      <section className="grid gap-4 xl:grid-cols-[1fr,1.65fr]">
        <div className="rounded-2xl p-5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
          <div className="flex items-center justify-between">
            <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em]" style={{ color: C.muted }}>Distribuição por prioridade</h2>
            <span className="text-[11px]" style={{ color: C.dim }}>{summary.total} total</span>
          </div>
          <div className="mt-5 space-y-3">
            {distribution.map((bucket) => (
              <div key={bucket.priority} className="grid grid-cols-[58px,1fr,28px] items-center gap-3">
                <PriorityBadge value={bucket.priority} />
                <div className="h-2 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.05)" }}>
                  <div className="h-full rounded-full" style={{ width: `${(bucket.count / maxBucket) * 100}%`, background: PRIORITY_COLOR[bucket.priority] }} />
                </div>
                <span className="text-right text-[11px] tabular-nums" style={{ color: C.muted }}>{bucket.count}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-2xl p-5" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
          <div className="flex items-center justify-between">
            <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em]" style={{ color: C.muted }}>Top 10 por EV Score</h2>
            <span className="text-[11px]" style={{ color: C.dim }}>score 0–100</span>
          </div>
          <div className="mt-4 space-y-2.5">
            {rows.slice(0, 10).map((row) => (
              <div key={`${row.watchlist_id}-${row.profile_id}`} className="grid grid-cols-[24px,minmax(130px,1fr),minmax(100px,1.6fr),52px] items-center gap-3 text-[11px]">
                <span className="tabular-nums" style={{ color: C.dim }}>#{row.rank_position}</span>
                <span className="truncate" title={row.profile_name}>{row.profile_name}</span>
                <div className="h-2 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.05)" }}>
                  <div className="h-full rounded-full" style={{ width: `${row.ev_score}%`, background: PRIORITY_COLOR[row.priority] }} />
                </div>
                <span className="text-right font-semibold tabular-nums" style={{ color: PRIORITY_COLOR[row.priority] }}>{row.ev_score.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl" style={{ background: C.surface, border: `1px solid ${C.border}` }}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4" style={{ borderColor: C.border }}>
          <div>
            <h2 className="text-[13px] font-semibold">Ranking operacional L3</h2>
            <p className="mt-0.5 text-[11px]" style={{ color: C.dim }}>Mesma ordem consumida pelas três superfícies</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: C.dim }} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar profile ou watchlist" className="w-60 rounded-lg py-2 pl-9 pr-3 text-[11px] outline-none" style={{ color: C.text, background: C.elevated, border: `1px solid ${C.border}` }} />
            </label>
            <select value={priority} onChange={(event) => setPriority(event.target.value as WatchlistPriority | "ALL")} className="rounded-lg px-3 py-2 text-[11px] outline-none" style={{ color: C.text, background: C.elevated, border: `1px solid ${C.border}` }}>
              <option value="ALL">Todas prioridades</option>
              {distribution.map((bucket) => <option key={bucket.priority} value={bucket.priority}>{bucket.priority} ({bucket.count})</option>)}
            </select>
            <label className="flex items-center gap-2 text-[11px]" style={{ color: C.muted }}>
              <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} /> Auto 30s
            </label>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1180px] text-left text-[11px]">
            <thead><tr style={{ color: C.dim, borderBottom: `1px solid ${C.border}` }}>
              {["Rank", "Prioridade", "EV Score", "Profile / Watchlist", "Confiança", "Trades", "Win Rate", "TP4h", "P&L médio", "P&L total", "Holding", "Motivo"].map((heading) => <th key={heading} className="px-4 py-3 font-medium uppercase tracking-[0.06em]">{heading}</th>)}
            </tr></thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={12} className="px-4 py-12 text-center" style={{ color: C.muted }}>Carregando ranking…</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={12} className="px-4 py-12 text-center" style={{ color: C.muted }}>Nenhuma watchlist encontrada.</td></tr>
              ) : filtered.map((row) => (
                <tr key={`${row.watchlist_id}-${row.profile_id}`} className="transition-colors hover:bg-white/[0.02]" style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td className="px-4 py-3 font-semibold tabular-nums" style={{ color: C.muted }}>#{row.rank_position}</td>
                  <td className="px-4 py-3"><PriorityBadge value={row.priority} /></td>
                  <td className="px-4 py-3 text-[13px] font-bold tabular-nums" style={{ color: PRIORITY_COLOR[row.priority] }}>{row.ev_score.toFixed(2)}</td>
                  <td className="max-w-64 px-4 py-3"><div className="truncate font-medium" title={row.profile_name}>{row.profile_name}</div><div className="mt-0.5 truncate" style={{ color: C.dim }}>{row.watchlist_name ?? row.watchlist_id ?? "Sem watchlist"}</div></td>
                  <td className="px-4 py-3">{row.stat_confidence}</td>
                  <td className="px-4 py-3 tabular-nums">{row.completed_trades}<span style={{ color: C.dim }}> / {row.total_trades}</span></td>
                  <td className="px-4 py-3 tabular-nums">{fmtRate(row.win_rate)}</td>
                  <td className="px-4 py-3 tabular-nums">{fmtRate(row.tp_4h_rate)}</td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: (row.avg_pnl_pct ?? 0) >= 0 ? C.green : C.red }}>{fmtPnlPct(row.avg_pnl_pct)}</td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: row.pnl_total_usdt >= 0 ? C.green : C.red }}>{fmtUsd(row.pnl_total_usdt)}</td>
                  <td className="px-4 py-3 tabular-nums">{fmtHolding(row.avg_holding_win_seconds)}</td>
                  <td className="max-w-80 px-4 py-3"><p className="truncate" title={row.priority_reason} style={{ color: C.muted }}>{row.priority_reason}</p></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <footer className="flex items-center justify-between px-5 py-3 text-[10px]" style={{ color: C.dim }}>
          <span>{filtered.length} de {rows.length} watchlists</span>
          <span>Atualizado {lastUpdated ? lastUpdated.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}</span>
        </footer>
      </section>
    </div>
  );
}