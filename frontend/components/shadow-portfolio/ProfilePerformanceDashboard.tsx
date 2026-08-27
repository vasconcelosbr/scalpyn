"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock3,
  RefreshCw,
  Search,
  ShieldCheck,
  Target,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ApiError, apiGet } from "@/lib/api";
import {
  STATUS_LABEL,
  TREND_LABEL,
  formatHolding,
  formatRate,
  formatSigned,
  formatUsd,
  filterProfileRows,
  heatmapColor,
  historyMetricValue,
  profileDailyPerformanceRequestPath,
  profilePerformanceRequestPath,
  sortProfileRows,
  type ProfileMonitorStatus,
  type ProfileDailyPerformanceResponse,
  type ProfileDailyRange,
  type ProfilePerformanceHighlight,
  type ProfilePerformanceMetric,
  type ProfilePerformanceResponse,
  type ProfilePerformanceRow,
  type ProfilePerformanceSortKey,
} from "@/lib/profilePerformance";


const C = {
  surface: "#10121A",
  elevated: "#161824",
  border: "rgba(255,255,255,0.07)",
  borderStrong: "rgba(255,255,255,0.13)",
  text: "#E6E8EE",
  muted: "#8A91A4",
  dim: "#5A6075",
  green: "#22B97A",
  red: "#E5484D",
  blue: "#4F7BF7",
  amber: "#F2A33A",
  purple: "#9D7CF7",
} as const;

const STATUS_COLOR: Record<ProfileMonitorStatus, string> = {
  POSITIVE: C.green,
  STABLE: C.blue,
  ATTENTION: C.amber,
  DETERIORATING: C.red,
  LOW_SAMPLE: C.dim,
};

const METRIC_LABEL: Record<ProfilePerformanceMetric, string> = {
  ev_score: "EV Score",
  win_rate: "Win Rate TP/SL",
  pnl_usdt: "P&L",
  trades: "Trades",
  holding_seconds: "Holding",
};

type RangeDays = 7 | 14 | 30;
type SortDirection = "asc" | "desc";


function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

function shiftIsoDay(value: string, amount: number): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
}

function displayDate(value: string): string {
  return new Date(`${value}T12:00:00Z`).toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).toUpperCase();
}

function compactDate(value: string): string {
  return new Date(`${value}T12:00:00Z`).toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "UTC",
  });
}

function deltaColor(value: number | null): string {
  if (value == null || value === 0) return C.muted;
  return value > 0 ? C.green : C.red;
}

function SummaryCard({
  label,
  value,
  sub,
  accent = C.blue,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  accent?: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-xl border bg-[#10121a] p-4" style={{ borderColor: C.border }}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: C.muted }}>{label}</span>
        <span style={{ color: accent }}>{icon}</span>
      </div>
      <div className="truncate text-[23px] font-semibold tabular-nums" style={{ color: C.text }}>{value}</div>
      <div className="mt-1 truncate text-[10.5px]" style={{ color: C.muted }} title={sub}>{sub}</div>
    </div>
  );
}

function Delta({ value, suffix = "" }: { value: number | null; suffix?: string }) {
  if (value == null) return <span style={{ color: C.dim }}>—</span>;
  const positive = value >= 0;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-[11px]" style={{ color: deltaColor(value) }}>
      {positive ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
      {formatSigned(value, suffix)}
    </span>
  );
}

function HighlightCard({
  label,
  item,
  detail,
  accent,
}: {
  label: string;
  item: ProfilePerformanceHighlight | null;
  detail: (value: ProfilePerformanceHighlight) => string;
  accent: string;
}) {
  return (
    <div className="min-w-0 rounded-xl border bg-[#10121a] p-4" style={{ borderColor: C.border }}>
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: C.muted }}>{label}</div>
      {item ? (
        <>
          <div className="truncate text-[12px] font-semibold" style={{ color: C.text }} title={item.profile_name}>{item.profile_name}</div>
          <div className="mt-2 text-[12px] font-semibold tabular-nums" style={{ color: accent }}>{detail(item)}</div>
        </>
      ) : (
        <div className="py-2 text-xs" style={{ color: C.dim }}>Sem dados suficientes</div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: ProfileMonitorStatus }) {
  const color = STATUS_COLOR[status];
  return (
    <span
      className="inline-flex whitespace-nowrap rounded-md border px-2 py-1 text-[9px] font-bold uppercase tracking-[0.08em]"
      style={{ color, borderColor: `${color}55`, background: `${color}16` }}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function TrendSparkline({ row }: { row: ProfilePerformanceRow }) {
  const color = row.trend === "IMPROVING" ? C.green : row.trend === "DETERIORATING" ? C.red : C.blue;
  const title = `${TREND_LABEL[row.trend]} · slope ${row.trend_evidence.slope.toFixed(2)} · variação ${formatSigned(row.trend_evidence.net_change)}`;
  return (
    <div className="h-9 w-[94px]" title={title}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={row.history} margin={{ top: 4, right: 2, bottom: 4, left: 2 }}>
          <Tooltip
            formatter={(value) => [Number(value).toFixed(2), "EV"]}
            labelFormatter={(label) => displayDate(String(label))}
            contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, borderRadius: 8, fontSize: 10 }}
          />
          <Line type="monotone" dataKey="ev_score" stroke={color} strokeWidth={1.8} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function LoadingDashboard() {
  return (
    <div className="space-y-5" aria-label="Carregando performance dos profiles">
      <div className="h-16 animate-pulse rounded-xl bg-white/[0.035]" />
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => <div key={index} className="h-[112px] animate-pulse rounded-xl bg-white/[0.035]" />)}
      </div>
      <div className="h-[420px] animate-pulse rounded-xl bg-white/[0.035]" />
      <div className="h-[330px] animate-pulse rounded-xl bg-white/[0.035]" />
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-red-500/25 bg-red-500/[0.06] p-8 text-center">
      <AlertTriangle className="mx-auto mb-3 text-red-400" size={24} />
      <div className="text-sm font-semibold text-red-200">Não foi possível carregar a performance dos profiles.</div>
      <div className="mx-auto mt-2 max-w-2xl text-xs text-red-200/65">{message}</div>
      <button onClick={onRetry} className="mt-4 inline-flex items-center gap-2 rounded-md border border-red-400/25 px-3 py-2 text-xs text-red-200 hover:bg-red-400/10">
        <RefreshCw size={13} /> Tentar novamente
      </button>
    </div>
  );
}

export function EmptyProfilesState() {
  return (
    <div className="rounded-xl border bg-[#10121a] px-6 py-16 text-center" style={{ borderColor: C.border }}>
      <BarChart3 className="mx-auto mb-3" style={{ color: C.dim }} size={28} />
      <div className="text-sm font-semibold" style={{ color: C.text }}>Nenhum profile L3 disponível.</div>
      <div className="mt-2 text-xs" style={{ color: C.muted }}>A aba continuará disponível quando profiles e shadow trades forem registrados.</div>
    </div>
  );
}

function L3DailyEvolution({ asOf }: { asOf: string }) {
  const [range, setRange] = useState<ProfileDailyRange>("7d");
  const [data, setData] = useState<ProfileDailyPerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(() => {
        if (cancelled) return null;
        setLoading(true);
        setError(null);
        return apiGet<ProfileDailyPerformanceResponse>(profileDailyPerformanceRequestPath(asOf, range));
      })
      .then((response) => {
        if (!cancelled && response) setData(response);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(caught instanceof ApiError ? caught.toDescriptiveString() : caught instanceof Error ? caught.message : "Erro desconhecido");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [asOf, range]);

  const currentData = data?.range === range && data.as_of === asOf ? data : null;
  const points = useMemo(() => currentData?.points ?? [], [currentData]);
  const tablePoints = useMemo(() => [...points].reverse(), [points]);
  const ranges: { value: ProfileDailyRange; label: string }[] = [
    { value: "7d", label: "7d" },
    { value: "15d", label: "15d" },
    { value: "30d", label: "30d" },
    { value: "90d", label: "90d" },
    { value: "total", label: "Total" },
  ];

  return (
    <section className="rounded-xl border bg-[#10121a]" style={{ borderColor: C.border }}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-4" style={{ borderColor: C.border }}>
        <div>
          <h2 className="m-0 text-sm font-semibold" style={{ color: C.text }}>Evolução diária L3</h2>
          <p className="mt-1 text-[10.5px]" style={{ color: C.muted }}>Win Rate TP/SL; stop móvel e prazo operacional ficam fora da base. P&amp;L Total realizado em cada dia UTC.</p>
        </div>
        <div className="flex gap-1" aria-label="Período da evolução diária L3">
          {ranges.map((option) => (
            <button
              key={option.value}
              onClick={() => setRange(option.value)}
              className="rounded-md border px-3 py-2 text-[10px] font-semibold"
              style={{
                color: range === option.value ? "white" : C.muted,
                background: range === option.value ? C.blue : C.elevated,
                borderColor: range === option.value ? C.blue : C.border,
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error && !currentData ? (
        <div className="px-4 py-12 text-center text-xs text-red-300">Não foi possível carregar a evolução diária: {error}</div>
      ) : loading && !currentData ? (
        <div className="m-4 h-72 animate-pulse rounded-lg bg-white/[0.035]" aria-label="Carregando evolução diária L3" />
      ) : points.length === 0 ? (
        <div className="px-4 py-12 text-center text-xs" style={{ color: C.muted }}>Sem trades L3 finalizados no período selecionado.</div>
      ) : (
        <div className="grid gap-0 xl:grid-cols-[minmax(0,1.6fr)_minmax(360px,0.8fr)]">
          <div className="min-w-0 border-b p-4 xl:border-b-0 xl:border-r" style={{ borderColor: C.border }}>
            <div className="h-[320px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={points} margin={{ top: 10, right: 8, bottom: 4, left: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="date" tickFormatter={compactDate} minTickGap={26} tick={{ fill: C.dim, fontSize: 9 }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="rate" domain={[0, 1]} tickFormatter={(value) => `${Math.round(Number(value) * 100)}%`} tick={{ fill: C.dim, fontSize: 9 }} axisLine={false} tickLine={false} width={38} />
                  <YAxis yAxisId="pnl" orientation="right" tickFormatter={(value) => `$${Number(value).toFixed(0)}`} tick={{ fill: C.dim, fontSize: 9 }} axisLine={false} tickLine={false} width={48} />
                  <Tooltip
                    formatter={(value, name) => [name === "Win Rate TP/SL" ? formatRate(Number(value)) : formatUsd(Number(value)), name]}
                    labelFormatter={(value) => displayDate(String(value))}
                    contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, borderRadius: 8, fontSize: 10 }}
                  />
                  <Bar yAxisId="pnl" dataKey="pnl_usdt" name="P&L Total do dia" barSize={10} radius={[3, 3, 0, 0]}>
                    {points.map((point) => <Cell key={point.date} fill={point.pnl_usdt >= 0 ? C.green : C.red} fillOpacity={0.52} />)}
                  </Bar>
                  <Line yAxisId="rate" type="monotone" dataKey="win_rate" name="Win Rate TP/SL" stroke={C.blue} strokeWidth={2.2} dot={{ r: 2.2, fill: C.blue }} activeDot={{ r: 4 }} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-4 text-[10px]" style={{ color: C.muted }}>
              <span className="inline-flex items-center gap-2"><span className="h-0.5 w-5 bg-[#4f7bf7]" /> Win Rate TP/SL</span>
              <span className="inline-flex items-center gap-2"><span className="h-2.5 w-3 rounded-sm bg-[#22b97a]/60" /> P&amp;L positivo</span>
              <span className="inline-flex items-center gap-2"><span className="h-2.5 w-3 rounded-sm bg-[#e5484d]/60" /> P&amp;L negativo</span>
            </div>
          </div>

          <div className="max-h-[390px] overflow-auto">
            <table className="w-full min-w-[430px] border-collapse text-[11px]">
              <thead className="sticky top-0 z-10 bg-[#0d0f16] text-[9.5px] uppercase tracking-[0.08em]" style={{ color: C.muted }}>
                <tr>
                  <th className="px-3 py-3 text-left">Data</th>
                  <th className="px-3 py-3 text-right">Finalizados</th>
                  <th className="px-3 py-3 text-right">Win Rate TP/SL</th>
                  <th className="px-3 py-3 text-right">P&amp;L Total do dia</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.055]">
                {tablePoints.map((point) => (
                  <tr key={point.date}>
                    <td className="whitespace-nowrap px-3 py-2.5" style={{ color: C.text }}>{displayDate(point.date)}</td>
                    <td className="px-3 py-2.5 text-right font-mono" style={{ color: C.muted }} title={`${point.wins} TP · Win Rate exclui stop móvel e prazo operacional`}>{point.closed_trades.toLocaleString("pt-BR")}</td>
                    <td className="px-3 py-2.5 text-right font-mono font-semibold" style={{ color: point.win_rate == null ? C.dim : C.blue }}>{formatRate(point.win_rate)}</td>
                    <td className="px-3 py-2.5 text-right font-mono font-semibold" style={{ color: deltaColor(point.pnl_usdt) }}>{formatUsd(point.pnl_usdt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {error && currentData ? <div className="border-t px-4 py-2 text-[10px] text-amber-200" style={{ borderColor: C.border }}>Atualização falhou: {error}. Mantendo os últimos dados carregados.</div> : null}
    </section>
  );
}

function SortHeader({
  label,
  column,
  active,
  direction,
  onSort,
  title,
}: {
  label: string;
  column: ProfilePerformanceSortKey;
  active: ProfilePerformanceSortKey;
  direction: SortDirection;
  onSort: (column: ProfilePerformanceSortKey) => void;
  title?: string;
}) {
  return (
    <th className="whitespace-nowrap px-3 py-3 text-right" title={title}>
      <button className="inline-flex items-center gap-1 hover:text-white" onClick={() => onSort(column)}>
        {label}
        <span style={{ color: active === column ? C.blue : C.dim }}>{active === column ? (direction === "desc" ? "▼" : "▲") : "⇅"}</span>
      </button>
    </th>
  );
}

function ProfileMonitorTable({
  rows,
  rangeDays,
  sortKey,
  sortDirection,
  onSort,
  onOpen,
}: {
  rows: ProfilePerformanceRow[];
  rangeDays: RangeDays;
  sortKey: ProfilePerformanceSortKey;
  sortDirection: SortDirection;
  onSort: (column: ProfilePerformanceSortKey) => void;
  onOpen: (profileId: string) => void;
}) {
  if (rows.length === 0) {
    return <div className="py-14 text-center text-sm" style={{ color: C.muted }}>Nenhum profile corresponde aos filtros selecionados.</div>;
  }
  return (
    <div className="overflow-auto rounded-xl border" style={{ borderColor: C.border }}>
      <table className="w-full min-w-[1320px] border-collapse text-left text-[11px]">
        <thead className="sticky top-0 z-20 bg-[#0d0f16] text-[9.5px] uppercase tracking-[0.08em]" style={{ color: C.muted }}>
          <tr>
            <th className="w-12 px-3 py-3 text-right">Rank</th>
            <th className="sticky left-0 z-30 min-w-[260px] bg-[#0d0f16] px-3 py-3">Profile</th>
            <SortHeader label="Trades" column="trades" active={sortKey} direction={sortDirection} onSort={onSort} title="Amostra acumulada de trades; o Win Rate usa apenas TP e SL." />
            <SortHeader label="EV Score" column="ev_score" active={sortKey} direction={sortDirection} onSort={onSort} />
            <SortHeader label="Δ EV" column="ev_delta" active={sortKey} direction={sortDirection} onSort={onSort} title="Variação do EV acumulado em relação a D-1." />
            <SortHeader label="Win Rate TP/SL" column="win_rate" active={sortKey} direction={sortDirection} onSort={onSort} />
            <SortHeader label="Δ WR" column="win_rate_delta_pp" active={sortKey} direction={sortDirection} onSort={onSort} title="Variação em pontos percentuais contra D-1." />
            <SortHeader label="P&L Dia" column="pnl_day_usdt" active={sortKey} direction={sortDirection} onSort={onSort} />
            <SortHeader label={`P&L ${rangeDays}d`} column="pnl_period_usdt" active={sortKey} direction={sortDirection} onSort={onSort} />
            <SortHeader label="Holding" column="holding_seconds" active={sortKey} direction={sortDirection} onSort={onSort} title="Média do holding dos trades positivos, igual ao ranking canônico." />
            <th className="px-3 py-3 text-center">Tendência</th>
            <th className="px-3 py-3 text-center">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.055] bg-[#10121a]">
          {rows.map((row) => (
            <tr
              key={row.profile_id}
              tabIndex={0}
              className="cursor-pointer outline-none transition hover:bg-white/[0.025] focus:bg-white/[0.035]"
              onClick={() => onOpen(row.profile_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpen(row.profile_id);
                }
              }}
            >
              <td className="px-3 py-3 text-right font-mono font-semibold" style={{ color: C.muted }}>{row.rank}</td>
              <td className="sticky left-0 z-10 max-w-[300px] bg-[#10121a] px-3 py-3">
                <div className="truncate font-semibold" style={{ color: C.text }} title={row.profile_name}>{row.profile_name}</div>
                <div className="mt-1 flex items-center gap-2 text-[9.5px]" style={{ color: C.dim }}>
                  <span>{row.watchlist_name ?? "L3"}</span>
                  <span>·</span>
                  <span className="font-semibold">{row.sample_status}</span>
                </div>
              </td>
              <td
                className="px-3 py-3 text-right font-mono"
                title={`${row.trades} trades · Finalizados: ${row.closed_trades} · Abertos: ${row.open_trades}\nTP: ${row.tp} · SL: ${row.sl} · Timeout: ${row.timeout}\nAmostra: ${row.sample_status}`}
              >
                <div>{row.trades.toLocaleString("pt-BR")}</div>
                <div className="text-[9px]" style={{ color: C.dim }}>{row.sample_status}</div>
              </td>
              <td className="px-3 py-3 text-right font-mono font-bold" style={{ color: row.ev_score >= 60 ? C.green : row.ev_score >= 30 ? C.amber : C.red }}>{row.ev_score.toFixed(1)}</td>
              <td className="px-3 py-3 text-right"><Delta value={row.ev_delta} /></td>
              <td className="px-3 py-3 text-right font-mono">{formatRate(row.win_rate)}</td>
              <td className="px-3 py-3 text-right"><Delta value={row.win_rate_delta_pp} suffix=" pp" /></td>
              <td className="px-3 py-3 text-right font-mono" style={{ color: deltaColor(row.pnl_day_usdt) }}>{formatUsd(row.pnl_day_usdt)}</td>
              <td className="px-3 py-3 text-right font-mono" style={{ color: deltaColor(row.pnl_period_usdt) }}>{formatUsd(row.pnl_period_usdt)}</td>
              <td className="px-3 py-3 text-right font-mono" title="Holding médio dos trades concluídos com P&L positivo.">{formatHolding(row.holding_seconds)}</td>
              <td className="px-3 py-3"><div className="flex justify-center"><TrendSparkline row={row} /></div></td>
              <td className="px-3 py-3 text-center"><StatusBadge status={row.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function heatmapTooltip(row: ProfilePerformanceRow, point: ProfilePerformanceRow["history"][number]): string {
  return [
    row.profile_name,
    displayDate(point.date),
    `EV Score: ${point.ev_score.toFixed(1)}`,
    `Win Rate TP/SL: ${formatRate(point.win_rate)}`,
    `Trades: ${point.trades} (${point.closed_trades} finalizados)`,
    `P&L: ${formatUsd(point.pnl_usdt)}`,
  ].join("\n");
}

function ProfileHistoryHeatmap({
  rows,
  metric,
  onMetricChange,
  onOpen,
}: {
  rows: ProfilePerformanceRow[];
  metric: ProfilePerformanceMetric;
  onMetricChange: (metric: ProfilePerformanceMetric) => void;
  onOpen: (profileId: string) => void;
}) {
  const dates = rows[0]?.history.map((point) => point.date) ?? [];
  const values = rows.flatMap((row) => row.history.map((point) => historyMetricValue(point, metric))).filter((value): value is number => value != null && Number.isFinite(value));
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 0;

  return (
    <section className="rounded-xl border bg-[#10121a]" style={{ borderColor: C.border }}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-4" style={{ borderColor: C.border }}>
        <div>
          <h2 className="m-0 text-sm font-semibold" style={{ color: C.text }}>Histórico dos Profiles</h2>
          <p className="mt-1 text-[10.5px]" style={{ color: C.muted }}>Profile × data · passe o mouse para auditar as métricas do dia.</p>
        </div>
        <label className="flex items-center gap-2 text-[10px] uppercase tracking-[0.08em]" style={{ color: C.muted }}>
          Métrica
          <select
            value={metric}
            onChange={(event) => onMetricChange(event.target.value as ProfilePerformanceMetric)}
            className="rounded-md border bg-[#161824] px-3 py-2 text-xs normal-case tracking-normal outline-none"
            style={{ color: C.text, borderColor: C.borderStrong }}
          >
            {(Object.keys(METRIC_LABEL) as ProfilePerformanceMetric[]).map((key) => <option key={key} value={key}>{METRIC_LABEL[key]}</option>)}
          </select>
        </label>
      </div>
      {rows.length === 0 ? (
        <div className="py-12 text-center text-sm" style={{ color: C.muted }}>Sem histórico para os filtros selecionados.</div>
      ) : (
        <div className="overflow-auto">
          <div className="min-w-max p-4">
            <div className="mb-2 grid gap-1" style={{ gridTemplateColumns: `240px repeat(${dates.length}, 38px)` }}>
              <div />
              {dates.map((value) => <div key={value} className="text-center text-[9px]" style={{ color: C.dim }}>{value.slice(8)}</div>)}
            </div>
            <div className="space-y-1">
              {rows.map((row) => (
                <div key={row.profile_id} className="grid gap-1" style={{ gridTemplateColumns: `240px repeat(${dates.length}, 38px)` }}>
                  <button onClick={() => onOpen(row.profile_id)} className="truncate pr-3 text-left text-[10.5px] font-medium hover:text-white" style={{ color: C.muted }} title={row.profile_name}>{row.profile_name}</button>
                  {row.history.map((point) => {
                    const value = historyMetricValue(point, metric);
                    return (
                      <button
                        key={point.date}
                        onClick={() => onOpen(row.profile_id)}
                        aria-label={`${row.profile_name} ${point.date} ${METRIC_LABEL[metric]}`}
                        className="h-7 rounded-[4px] border border-white/[0.04] transition hover:scale-105 hover:border-white/30"
                        style={{ background: heatmapColor(metric, value, minimum, maximum) }}
                        title={heatmapTooltip(row, point)}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function DetailLineChart({
  row,
  dataKey,
  label,
  color,
  formatter,
}: {
  row: ProfilePerformanceRow;
  dataKey: "ev_score" | "win_rate" | "pnl_usdt";
  label: string;
  color: string;
  formatter: (value: number | null) => string;
}) {
  return (
    <div className="rounded-lg border bg-black/10 p-3" style={{ borderColor: C.border }}>
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: C.muted }}>{label}</div>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={row.history} margin={{ top: 6, right: 8, left: -20, bottom: 0 }}>
            <XAxis dataKey="date" tickFormatter={(value) => String(value).slice(8)} tick={{ fill: C.dim, fontSize: 9 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: C.dim, fontSize: 9 }} axisLine={false} tickLine={false} width={44} />
            <Tooltip
              formatter={(value) => [formatter(Number(value)), label]}
              labelFormatter={(value) => displayDate(String(value))}
              contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, borderRadius: 8, fontSize: 10 }}
            />
            <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function ProfileDrawer({
  row,
  rangeDays,
  onRangeChange,
  onClose,
}: {
  row: ProfilePerformanceRow;
  rangeDays: RangeDays;
  onRangeChange: (value: RangeDays) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/55" role="dialog" aria-modal="true" aria-label={`Detalhes de ${row.profile_name}`}>
      <button className="min-w-0 flex-1 cursor-default" onClick={onClose} aria-label="Fechar detalhes" />
      <aside className="h-full w-full max-w-[640px] overflow-y-auto border-l bg-[#0d0f16] p-5 shadow-2xl" style={{ borderColor: C.borderStrong }}>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-[0.12em]" style={{ color: C.muted }}>Performance do Profile</div>
            <h2 className="mt-1 break-words text-lg font-semibold" style={{ color: C.text }}>{row.profile_name}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2"><StatusBadge status={row.status} /><span className="text-[10px]" style={{ color: C.muted }}>{row.sample_status} · prioridade {row.priority}</span></div>
          </div>
          <button onClick={onClose} className="rounded-md border p-2 hover:bg-white/5" style={{ color: C.muted, borderColor: C.border }} aria-label="Fechar"><X size={16} /></button>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            ["EV Score", row.ev_score.toFixed(1)],
            ["Win Rate TP/SL", formatRate(row.win_rate)],
            ["Trades", row.trades.toLocaleString("pt-BR")],
            ["Holding", formatHolding(row.holding_seconds)],
            ["TP", row.tp.toLocaleString("pt-BR")],
            ["SL", row.sl.toLocaleString("pt-BR")],
            ["Timeout", row.timeout.toLocaleString("pt-BR")],
            [`P&L ${rangeDays}d`, formatUsd(row.pnl_period_usdt)],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border bg-[#10121a] p-3" style={{ borderColor: C.border }}>
              <div className="text-[9px] uppercase tracking-[0.08em]" style={{ color: C.dim }}>{label}</div>
              <div className="mt-1 truncate font-mono text-sm font-semibold" style={{ color: C.text }}>{value}</div>
            </div>
          ))}
        </div>

        <div className="mt-5 flex items-center justify-between gap-3">
          <div className="text-xs font-semibold" style={{ color: C.text }}>Performance temporal</div>
          <div className="flex gap-1">
            {([7, 14, 30] as RangeDays[]).map((value) => (
              <button key={value} onClick={() => onRangeChange(value)} className="rounded-md border px-2.5 py-1 text-[10px] font-semibold" style={{ color: rangeDays === value ? "white" : C.muted, background: rangeDays === value ? C.blue : C.elevated, borderColor: rangeDays === value ? C.blue : C.border }}>{value}d</button>
            ))}
          </div>
        </div>
        <div className="mt-3 space-y-3">
          <DetailLineChart row={row} dataKey="ev_score" label="EV Score" color={C.blue} formatter={(value) => value == null ? "—" : value.toFixed(1)} />
          <DetailLineChart row={row} dataKey="win_rate" label="Win Rate TP/SL" color={C.green} formatter={formatRate} />
          <DetailLineChart row={row} dataKey="pnl_usdt" label="P&L diário" color={C.purple} formatter={formatUsd} />
        </div>

        <div className="mt-5 rounded-lg border p-3 text-[11px] leading-relaxed" style={{ color: C.muted, borderColor: C.border }}>
          <div className="font-semibold" style={{ color: C.text }}>Leitura da tendência</div>
          <div className="mt-1">{TREND_LABEL[row.trend]} · slope {row.trend_evidence.slope.toFixed(2)} · variação líquida {formatSigned(row.trend_evidence.net_change)} em {row.trend_evidence.points} pontos.</div>
          <div className="mt-2">{row.priority_reason}</div>
        </div>
      </aside>
    </div>
  );
}


export default function ProfilePerformanceDashboard() {
  const [asOf, setAsOf] = useState(todayUtc);
  const [rangeDays, setRangeDays] = useState<RangeDays>(7);
  const [data, setData] = useState<ProfilePerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [profileId, setProfileId] = useState("");
  const [status, setStatus] = useState<"ALL" | ProfileMonitorStatus>("ALL");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<ProfilePerformanceSortKey>("ev_score");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [heatmapMetric, setHeatmapMetric] = useState<ProfilePerformanceMetric>("ev_score");
  const [detailProfileId, setDetailProfileId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(() => {
        if (cancelled) return null;
        setLoading(true);
        setError(null);
        return apiGet<ProfilePerformanceResponse>(profilePerformanceRequestPath(asOf, rangeDays));
      })
      .then((response) => {
        if (!cancelled && response) setData(response);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(caught instanceof ApiError ? caught.toDescriptiveString() : caught instanceof Error ? caught.message : "Erro desconhecido");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [asOf, rangeDays, reloadToken]);

  const filteredRows = useMemo(() => {
    if (!data) return [];
    const selected = filterProfileRows(data.profiles, { profileId, status, search });
    return sortProfileRows(selected, sortKey, sortDirection);
  }, [data, profileId, search, sortDirection, sortKey, status]);

  const detailRow = detailProfileId ? data?.profiles.find((row) => row.profile_id === detailProfileId) ?? null : null;
  const maxDate = todayUtc();
  const minDate = data?.available_from ?? undefined;

  const handleSort = useCallback((column: ProfilePerformanceSortKey) => {
    setSortKey((current) => {
      if (current === column) setSortDirection((direction) => direction === "desc" ? "asc" : "desc");
      else setSortDirection("desc");
      return column;
    });
  }, []);

  if (loading && !data) return <LoadingDashboard />;
  if (error && !data) return <ErrorState message={error} onRetry={() => setReloadToken((value) => value + 1)} />;
  if (!data) return null;

  if (data.profiles.length === 0) {
    return <EmptyProfilesState />;
  }

  const summary = data.summary;
  return (
    <div className="space-y-5">
      <section className="rounded-xl border bg-[#10121a] p-4" style={{ borderColor: C.border }}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2"><Activity size={16} style={{ color: C.blue }} /><h2 className="m-0 text-sm font-semibold" style={{ color: C.text }}>Performance dos Profiles</h2></div>
            <p className="mt-1 text-[10.5px]" style={{ color: C.muted }}>Saúde diária dos profiles L3 · contrato {data.contract_version} · timezone {data.timezone}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => setAsOf((value) => shiftIsoDay(value, -1))} disabled={Boolean(minDate && asOf <= minDate)} className="rounded-md border p-2 disabled:cursor-not-allowed disabled:opacity-35" style={{ borderColor: C.border, color: C.muted }} aria-label="Dia anterior"><ChevronLeft size={14} /></button>
            <label className="flex items-center gap-2 rounded-md border bg-[#161824] px-3 py-2" style={{ borderColor: C.border }}>
              <CalendarDays size={13} style={{ color: C.blue }} />
              <input type="date" value={asOf} min={minDate} max={maxDate} onChange={(event) => { if (event.target.value) setAsOf(event.target.value); }} className="bg-transparent text-[11px] outline-none [color-scheme:dark]" style={{ color: C.text }} />
            </label>
            <button onClick={() => setAsOf((value) => shiftIsoDay(value, 1))} disabled={asOf >= maxDate} className="rounded-md border p-2 disabled:cursor-not-allowed disabled:opacity-35" style={{ borderColor: C.border, color: C.muted }} aria-label="Próximo dia"><ChevronRight size={14} /></button>
            <div className="ml-1 flex gap-1">
              {([7, 14, 30] as RangeDays[]).map((value) => <button key={value} onClick={() => setRangeDays(value)} className="rounded-md border px-3 py-2 text-[10px] font-semibold" style={{ color: rangeDays === value ? "white" : C.muted, background: rangeDays === value ? C.blue : C.elevated, borderColor: rangeDays === value ? C.blue : C.border }}>{value} dias</button>)}
            </div>
            <button onClick={() => setReloadToken((value) => value + 1)} disabled={loading} className="rounded-md border p-2 disabled:opacity-40" style={{ borderColor: C.border, color: C.muted }} aria-label="Atualizar"><RefreshCw size={14} className={loading ? "animate-spin" : ""} /></button>
          </div>
        </div>
        {error ? <div className="mt-3 rounded-md border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2 text-[10.5px] text-amber-200">Última atualização falhou: {error}. Mantendo o último resultado carregado.</div> : null}
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <SummaryCard label="Profiles ativos" value={String(summary.active_profiles)} sub={`Com trades nos últimos ${rangeDays}d`} icon={<Target size={17} />} />
        <SummaryCard label="EV Score médio" value={summary.ev_score_mean?.toFixed(1) ?? "—"} sub={`${formatSigned(summary.ev_score_delta)} vs D-1`} accent={deltaColor(summary.ev_score_delta)} icon={<Activity size={17} />} />
        <SummaryCard label="Win Rate TP/SL" value={formatRate(summary.win_rate)} sub={`${formatSigned(summary.win_rate_delta_pp, " pp")} vs D-1`} accent={deltaColor(summary.win_rate_delta_pp)} icon={<ShieldCheck size={17} />} />
        <SummaryCard label="P&L Dia" value={formatUsd(summary.pnl_day_usdt)} sub={`${formatUsd(summary.pnl_period_usdt)} nos últimos ${rangeDays}d`} accent={deltaColor(summary.pnl_day_usdt)} icon={summary.pnl_day_usdt >= 0 ? <TrendingUp size={17} /> : <TrendingDown size={17} />} />
        <SummaryCard label="Trades" value={summary.trades_period.toLocaleString("pt-BR")} sub={`${summary.closed_trades_period.toLocaleString("pt-BR")} finalizados no período`} icon={<BarChart3 size={17} />} />
        <SummaryCard label="Profiles em alerta" value={String(summary.alerts)} sub="Atenção ou deteriorando" accent={summary.alerts ? C.amber : C.green} icon={<AlertTriangle size={17} />} />
      </section>

      {summary.trades_period === 0 ? (
        <div className="rounded-lg border border-blue-400/20 bg-blue-400/[0.05] px-4 py-3 text-[11px] text-blue-100/75">
          Nenhum trade novo no período selecionado. Os valores exibidos representam o estado acumulado até {displayDate(data.as_of)}.
        </div>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <HighlightCard label="Melhor Profile" item={data.highlights.best_profile} detail={(item) => `EV ${item.ev_score.toFixed(1)} · WR ${formatRate(item.win_rate)}`} accent={C.green} />
        <HighlightCard label="Maior evolução" item={data.highlights.biggest_improvement} detail={(item) => `EV ${formatSigned(item.ev_period_change)} em ${rangeDays}d`} accent={C.green} />
        <HighlightCard label="Maior deterioração" item={data.highlights.biggest_deterioration} detail={(item) => `EV ${formatSigned(item.ev_period_change)} em ${rangeDays}d`} accent={C.red} />
        <HighlightCard label="Maior P&L" item={data.highlights.highest_pnl} detail={(item) => formatUsd(item.pnl_period_usdt)} accent={C.green} />
      </section>

      <L3DailyEvolution asOf={asOf} />

      <section className="rounded-xl border bg-[#10121a]" style={{ borderColor: C.border }}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-4" style={{ borderColor: C.border }}>
          <div><h2 className="m-0 text-sm font-semibold" style={{ color: C.text }}>Profile Monitor</h2><p className="mt-1 text-[10.5px]" style={{ color: C.muted }}>Rank canônico permanece fixo; ordenações temporárias não renumeram os profiles.</p></div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative"><Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" style={{ color: C.dim }} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar profile..." className="w-48 rounded-md border bg-[#161824] py-2 pl-8 pr-3 text-[11px] outline-none placeholder:text-[#5a6075]" style={{ color: C.text, borderColor: C.border }} /></label>
            <select value={profileId} onChange={(event) => setProfileId(event.target.value)} className="max-w-[260px] rounded-md border bg-[#161824] px-3 py-2 text-[11px] outline-none" style={{ color: C.text, borderColor: C.border }}><option value="">Todos os profiles</option>{data.profiles.map((row) => <option key={row.profile_id} value={row.profile_id}>{row.profile_name}</option>)}</select>
            <select value={status} onChange={(event) => setStatus(event.target.value as "ALL" | ProfileMonitorStatus)} className="rounded-md border bg-[#161824] px-3 py-2 text-[11px] outline-none" style={{ color: C.text, borderColor: C.border }}><option value="ALL">Todos os status</option>{(Object.keys(STATUS_LABEL) as ProfileMonitorStatus[]).map((key) => <option key={key} value={key}>{STATUS_LABEL[key]}</option>)}</select>
          </div>
        </div>
        <ProfileMonitorTable rows={filteredRows} rangeDays={rangeDays} sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} onOpen={setDetailProfileId} />
      </section>

      <ProfileHistoryHeatmap rows={filteredRows} metric={heatmapMetric} onMetricChange={setHeatmapMetric} onOpen={setDetailProfileId} />

      <div className="flex flex-wrap items-center justify-between gap-2 text-[9.5px]" style={{ color: C.dim }}>
        <span>EV, amostra e prioridade usam a configuração canônica do ranking. P&L diário usa o timestamp de encerramento.</span>
        <span className="inline-flex items-center gap-1"><Clock3 size={11} /> Atualizado para {displayDate(data.as_of)}</span>
      </div>

      {detailRow ? <ProfileDrawer row={detailRow} rangeDays={rangeDays} onRangeChange={setRangeDays} onClose={() => setDetailProfileId(null)} /> : null}
    </div>
  );
}
