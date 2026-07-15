"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, ChevronDown, ChevronRight,
  Download, Layers, RefreshCw, Settings2, TrendingUp, Wallet,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { apiGet, apiPost } from "@/lib/api";
import {
  ExecutionFilters,
  PerformanceFilter,
  WindowKey,
  usePerformanceByAsset,
  usePerformanceDistribution,
  usePerformanceEquity,
  usePerformanceExecutions,
  usePerformanceSummary,
  useGateAccountToday,
} from "@/hooks/usePerformance";

// ── theme ────────────────────────────────────────────────────────────────────
const C = {
  bg: "#0A0B10",
  surface: "#10121A",
  elevated: "#161824",
  border: "rgba(255,255,255,0.06)",
  borderStrong: "rgba(255,255,255,0.12)",
  text: "#E6E8EE",
  muted: "#8A91A4",
  dim: "#5A6075",
  green: "#22B97A",
  red: "#E5484D",
  blue: "#4F7BF7",
  purple: "#9D7CF7",
  amber: "#F2A33A",
} as const;

const WINDOWS: { key: WindowKey; label: string }[] = [
  { key: "1D", label: "Hoje" },
  { key: "7D", label: "7d" },
  { key: "15D", label: "15d" },
  { key: "30D", label: "30d" },
  { key: "90D", label: "90d" },
];

const dateBoundary = (value: string, nextDay = false) => {
  const date = new Date(`${value}T00:00:00`);
  if (nextDay) date.setDate(date.getDate() + 1);
  return date.toISOString();
};

const fmtUsd = (n: number | null | undefined, dp = 2) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtPct = (n: number | null | undefined, dp = 2) =>
  n === null || n === undefined || Number.isNaN(n) ? "—" : `${n.toFixed(dp)}%`;
const fmtSec = (s: number | null | undefined) => {
  if (!s || s < 0) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return `${d}d ${h}h`;
};
const num = (n: number | null | undefined, dp = 4) =>
  n === null || n === undefined ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: dp });

const dateInputStyle: React.CSSProperties = {
  colorScheme: "dark", background: C.elevated, color: C.text,
  border: `1px solid ${C.borderStrong}`, borderRadius: 6,
  padding: "5px 8px", fontSize: 11.5,
};

// ── tiny UI helpers ──────────────────────────────────────────────────────────
function StatCard({
  label, value, hint, accent, sub,
}: {
  label: string; value: string; hint?: string;
  accent?: "green" | "red" | "blue" | "amber"; sub?: string;
}) {
  const accentColor = accent === "green" ? C.green : accent === "red" ? C.red : accent === "amber" ? C.amber : C.text;
  return (
    <div style={{ background: C.elevated, border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px" }}>
      <div style={{ fontSize: 11, color: C.muted, letterSpacing: 0.6, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: accentColor, marginTop: 6 }}>
        {value}
      </div>
      {sub ? <div style={{ fontSize: 11, color: C.muted, fontVariantNumeric: "tabular-nums", marginTop: 4 }}>{sub}</div> : null}
      {hint ? <div style={{ fontSize: 10.5, color: C.dim, marginTop: 4 }}>{hint}</div> : null}
    </div>
  );
}

function SectionTitle({ icon, label, right }: { icon?: React.ReactNode; label: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 12, letterSpacing: 0.6, textTransform: "uppercase" }}>
        {icon}{label}
      </div>
      {right}
    </div>
  );
}

function ChartCard({ title, children, height = 240 }: { title: string; children: React.ReactNode; height?: number }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ fontSize: 12, color: C.muted, letterSpacing: 0.6, textTransform: "uppercase", marginBottom: 10 }}>{title}</div>
      <div style={{ width: "100%", height }}>{children}</div>
    </div>
  );
}

// ── filter bar ───────────────────────────────────────────────────────────────
function FilterBar({
  filter, onChange, onSync, syncing,
}: {
  filter: PerformanceFilter;
  onChange: (next: PerformanceFilter) => void;
  onSync: () => void;
  syncing: boolean;
}) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: "10px 14px", display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 12, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {WINDOWS.map(({ key, label }) => {
          const active = filter.window === key && !filter.from;
          return (
            <button key={key} onClick={() => onChange({ ...filter, window: key, from: undefined, to: undefined })}
              style={{
                background: active ? C.elevated : "transparent",
                color: active ? C.text : C.muted,
                border: `1px solid ${active ? C.borderStrong : C.border}`,
                borderRadius: 6, padding: "5px 10px", fontSize: 11.5, cursor: "pointer",
                letterSpacing: 0.4,
              }}>
              {label}
            </button>
          );
        })}
        <input type="date" aria-label="Data inicial"
          value={filter.from ? filter.from.slice(0, 10) : ""}
          onChange={e => e.target.value && onChange({
            ...filter, from: dateBoundary(e.target.value),
            to: filter.to ?? dateBoundary(e.target.value, true),
          })}
          style={dateInputStyle} />
        <span style={{ color: C.dim, fontSize: 11 }}>até</span>
        <input type="date" aria-label="Data final"
          value={filter.to ? new Date(new Date(filter.to).getTime() - 1).toISOString().slice(0, 10) : ""}
          min={filter.from?.slice(0, 10)}
          onChange={e => e.target.value && onChange({
            ...filter, to: dateBoundary(e.target.value, true),
          })}
          style={dateInputStyle} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 11.5 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={filter.autoRefresh}
            onChange={e => onChange({ ...filter, autoRefresh: e.target.checked })} />
          Auto-refresh 30s
        </label>
        <button onClick={onSync} disabled={syncing}
          style={{
            background: C.elevated, border: `1px solid ${C.borderStrong}`, color: C.text,
            borderRadius: 6, padding: "6px 12px", fontSize: 11.5, cursor: "pointer",
            display: "inline-flex", alignItems: "center", gap: 6, opacity: syncing ? 0.6 : 1,
          }}>
          <RefreshCw size={13} className={syncing ? "spin" : ""} />
          {syncing ? "Sincronizando…" : "Sync Gate.io"}
        </button>
      </div>
      <style jsx>{`
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg);} to { transform: rotate(360deg);} }
      `}</style>
    </div>
  );
}

// ── flash cards cluster ──────────────────────────────────────────────────────
function FlashCards({ filter }: { filter: PerformanceFilter }) {
  const { data, isLoading, error } = usePerformanceSummary(filter);
  const { data: gateToday, isLoading: gateLoading } = useGateAccountToday(filter.autoRefresh);

  if (error) return (
    <div style={{ background: C.surface, border: `1px solid ${C.red}55`, borderRadius: 10, padding: 14, color: C.red, fontSize: 12 }}>
      <AlertTriangle size={14} style={{ verticalAlign: "middle" }} /> Erro ao carregar resumo: {String(error.message ?? error)}
    </div>
  );

  const s = data;
  const placeholder = isLoading || !s;

  const pnlColor = s && s.pnl.total_usdt >= 0 ? "green" : "red";
  const deltaColor = s && s.pnl.delta_vs_previous >= 0 ? "green" : "red";

  return (
    <>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
      <StatCard label="Ordens executadas na Gate" value={placeholder ? "—" : String(s.stats.executed_trades)} hint="Ordens concluídas únicas · Spot + Futures · fonte Gate API" />
      <StatCard label="PnL de hoje na Gate" value={gateLoading || !gateToday?.available ? "—" : fmtPct(gateToday.pnl_pct)} accent={(gateToday?.pnl_usdt ?? 0) >= 0 ? "green" : "red"} hint="Variação patrimonial desde 00:00 · fonte Gate API" />
      <StatCard label="Valor ganho hoje na Gate" value={gateLoading || !gateToday?.available ? "—" : `${(gateToday.pnl_usdt ?? 0) >= 0 ? "+" : ""}$${fmtUsd(gateToday.pnl_usdt)}`} accent={(gateToday?.pnl_usdt ?? 0) >= 0 ? "green" : "red"} hint={`Capital atual $${fmtUsd(gateToday?.current_equity_usdt)} · inicial $${fmtUsd(gateToday?.start_equity_usdt)}`} />
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
      {/* Capital cluster */}
      <div>
        <SectionTitle icon={<Wallet size={13} />} label="Capital" />
        <div style={{ display: "grid", gap: 8 }}>
          <StatCard label="Giro executado (período)" value={placeholder ? "—" : `$${fmtUsd(s.capital.invested_usdt)}`} sub="Soma do capital aplicado nos fechamentos; pode reutilizar o mesmo saldo" />
          <StatCard label="Lotes FIFO em aberto" value={placeholder ? "—" : String(s.capital.open_positions)} hint="Projeção histórica; não representa o saldo atual da Gate" />
        </div>
      </div>

      {/* PnL cluster */}
      <div>
        <SectionTitle icon={<TrendingUp size={13} />} label="PnL" />
        <div style={{ display: "grid", gap: 8 }}>
          <StatCard label="PnL realizado FIFO" value={placeholder ? "—" : `$${fmtUsd(s.pnl.total_usdt)}`} accent={pnlColor as "green" | "red"} sub={`ROI sobre lotes fechados ${fmtPct(s?.pnl.roi_pct)} · não é o PnL patrimonial Gate`} />
          <StatCard label="Δ vs período anterior" value={placeholder ? "—" : `${s.pnl.delta_vs_previous >= 0 ? "+" : ""}$${fmtUsd(s.pnl.delta_vs_previous)}`} accent={deltaColor as "green" | "red"} hint={`Taxas pagas $${fmtUsd(s?.pnl.fees_usdt, 4)}`} />
        </div>
      </div>

      {/* Stats cluster */}
      <div>
        <SectionTitle icon={<Layers size={13} />} label="Estatísticas" />
        <div style={{ display: "grid", gap: 8 }}>
          <StatCard
            label="Lotes fechados · Win Rate"
            value={placeholder ? "—" : `${s.stats.total_trades} · ${fmtPct(s.stats.win_rate_pct, 1)}`}
            sub={`W ${s?.stats.wins ?? 0} · L ${s?.stats.losses ?? 0} · PF ${s?.stats.profit_factor ?? "—"}`}
          />
          <StatCard
            label="Médias / Holding"
            value={placeholder ? "—" : `+$${fmtUsd(s.stats.avg_win_usdt)} / -$${fmtUsd(Math.abs(s.stats.avg_loss_usdt))}`}
            sub={`Hold médio ${fmtSec(s?.stats.avg_holding_seconds)} · Sharpe ${s?.stats.sharpe ?? "—"}`}
          />
        </div>
      </div>

      {/* Risk cluster */}
      <div>
        <SectionTitle icon={<AlertTriangle size={13} />} label="Risco" />
        <div style={{ display: "grid", gap: 8 }}>
          <StatCard
            label="Drawdown máximo"
            value={placeholder ? "—" : `$${fmtUsd(s.risk.max_drawdown_usdt)}`}
            accent="amber"
            sub={`Atual $${fmtUsd(s?.risk.current_drawdown_usdt)} · Recovery ${s?.risk.recovery_pct === null || s?.risk.recovery_pct === undefined ? "—" : fmtPct(s.risk.recovery_pct)}`}
          />
          <StatCard
            label="Volume negociado"
            value={placeholder ? "—" : `$${fmtUsd(s.stats.volume_usdt, 0)}`}
            hint={`maior win $${fmtUsd(s?.stats.biggest_win_usdt)} · maior loss $${fmtUsd(s?.stats.biggest_loss_usdt)}`}
          />
        </div>
      </div>
    </div>
    </>
  );
}

// ── equity / drawdown chart ─────────────────────────────────────────────────
function EquityChart({ filter }: { filter: PerformanceFilter }) {
  const { data, isLoading } = usePerformanceEquity(filter);
  const points = data?.points ?? [];
  return (
    <ChartCard title="Equity Curve & Drawdown" height={260}>
      {isLoading ? (
        <div style={{ color: C.muted, fontSize: 12 }}>Carregando…</div>
      ) : points.length === 0 ? (
        <div style={{ color: C.muted, fontSize: 12 }}>Sem trades fechados no período.</div>
      ) : (
        <ResponsiveContainer>
          <AreaChart data={points} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <defs>
              <linearGradient id="eqArea" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={C.green} stopOpacity={0.35} />
                <stop offset="100%" stopColor={C.green} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="ddArea" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={C.red} stopOpacity={0.35} />
                <stop offset="100%" stopColor={C.red} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={C.border} vertical={false} />
            <XAxis dataKey="date" tick={{ fill: C.muted, fontSize: 10 }}
              tickFormatter={(v: string) => v ? v.slice(5, 10) : ""} />
            <YAxis tick={{ fill: C.muted, fontSize: 10 }} width={60}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`} />
            <Tooltip
              contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, fontSize: 12 }}
              labelStyle={{ color: C.muted }}
            />
            <Area dataKey="cum_pnl" stroke={C.green} fill="url(#eqArea)" strokeWidth={2} name="Cumulative PnL" />
            <Area dataKey="drawdown" stroke={C.red} fill="url(#ddArea)" strokeWidth={1.5} name="Drawdown" />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

// ── daily PnL bars ──────────────────────────────────────────────────────────
function DailyPnLChart({ filter }: { filter: PerformanceFilter }) {
  const { data, isLoading } = usePerformanceEquity(filter);
  const points = data?.points ?? [];
  return (
    <ChartCard title="PnL Diário (USDT)" height={260}>
      {isLoading ? (
        <div style={{ color: C.muted, fontSize: 12 }}>Carregando…</div>
      ) : points.length === 0 ? (
        <div style={{ color: C.muted, fontSize: 12 }}>Sem dados.</div>
      ) : (
        <ResponsiveContainer>
          <BarChart data={points} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <CartesianGrid stroke={C.border} vertical={false} />
            <XAxis dataKey="date" tick={{ fill: C.muted, fontSize: 10 }}
              tickFormatter={(v: string) => v ? v.slice(5, 10) : ""} />
            <YAxis tick={{ fill: C.muted, fontSize: 10 }} width={60}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`} />
            <Tooltip contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, fontSize: 12 }} />
            <Bar dataKey="pnl_day" name="PnL Diário">
              {points.map((p, i) => (
                <Cell key={i} fill={p.pnl_day >= 0 ? C.green : C.red} fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

// ── distribution + heatmap ──────────────────────────────────────────────────
const DOWS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sab"];
function DistributionPanel({ filter }: { filter: PerformanceFilter }) {
  const { data } = usePerformanceDistribution(filter);
  const counts = data?.counts;
  const wlPie = useMemo(() => counts ? [
    { name: "Wins", value: counts.wins, color: C.green },
    { name: "Losses", value: counts.losses, color: C.red },
  ] : [], [counts]);
  const sfBars = useMemo(() => counts ? [
    { type: "Spot", n: counts.spot }, { type: "Futures", n: counts.futures },
    { type: "Long", n: counts.longs }, { type: "Short", n: counts.shorts },
  ] : [], [counts]);
  return (
    <ChartCard title="Distribuição de lotes FIFO" height={260}>
      {!data ? (
        <div style={{ color: C.muted, fontSize: 12 }}>Carregando…</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", height: "100%" }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={wlPie} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2}>
                {wlPie.map((d, i) => <Cell key={i} fill={d.color} />)}
              </Pie>
              <Tooltip contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
          <ResponsiveContainer>
            <BarChart data={sfBars} layout="vertical" margin={{ top: 8, right: 8, left: 24, bottom: 4 }}>
              <CartesianGrid stroke={C.border} horizontal={false} />
              <XAxis type="number" tick={{ fill: C.muted, fontSize: 10 }} />
              <YAxis dataKey="type" type="category" tick={{ fill: C.muted, fontSize: 10 }} width={60} />
              <Tooltip contentStyle={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, fontSize: 12 }} />
              <Bar dataKey="n" fill={C.blue} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </ChartCard>
  );
}

function HeatmapPanel({ filter }: { filter: PerformanceFilter }) {
  const { data } = usePerformanceDistribution(filter);
  const grid = useMemo(() => {
    const m: Record<string, number> = {};
    let max = 0;
    let min = 0;
    (data?.heatmap ?? []).forEach(c => {
      m[`${c.dow}-${c.hour}`] = c.pnl;
      if (c.pnl > max) max = c.pnl;
      if (c.pnl < min) min = c.pnl;
    });
    return { m, max, min };
  }, [data]);
  const cellColor = (v: number | undefined) => {
    if (v === undefined) return C.elevated;
    const denom = v >= 0 ? grid.max || 1 : Math.abs(grid.min) || 1;
    const intensity = Math.min(1, Math.abs(v) / denom);
    return v >= 0
      ? `rgba(34, 185, 122, ${0.15 + intensity * 0.7})`
      : `rgba(229, 72, 77,  ${0.15 + intensity * 0.7})`;
  };
  return (
    <ChartCard title="Heatmap PnL — Dia da Semana × Hora (UTC)" height={260}>
      <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 2 }}>
        <div style={{ display: "grid", gridTemplateColumns: `30px repeat(24, 1fr)`, gap: 1, fontSize: 9, color: C.muted }}>
          <div />
          {Array.from({ length: 24 }).map((_, h) => (
            <div key={h} style={{ textAlign: "center" }}>{h % 3 === 0 ? h : ""}</div>
          ))}
        </div>
        {DOWS.map((label, dow) => (
          <div key={dow} style={{ display: "grid", gridTemplateColumns: `30px repeat(24, 1fr)`, gap: 1, flex: 1 }}>
            <div style={{ fontSize: 10, color: C.muted, alignSelf: "center" }}>{label}</div>
            {Array.from({ length: 24 }).map((_, hr) => {
              const v = grid.m[`${dow}-${hr}`];
              return <div key={hr} title={v !== undefined ? `${label} ${hr}h · $${fmtUsd(v)}` : "Sem trades"}
                style={{ background: cellColor(v), borderRadius: 2 }} />;
            })}
          </div>
        ))}
      </div>
    </ChartCard>
  );
}

// ── by-asset table ──────────────────────────────────────────────────────────
function ByAssetTable({ filter }: { filter: PerformanceFilter }) {
  const { data } = usePerformanceByAsset(filter);
  const rows = data?.rows ?? [];
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ fontSize: 12, color: C.muted, letterSpacing: 0.6, textTransform: "uppercase", marginBottom: 10 }}>
        Performance por ativo
      </div>
      <div style={{ overflowX: "auto", maxHeight: 260 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
          <thead style={{ position: "sticky", top: 0, background: C.surface, color: C.muted, fontWeight: 500 }}>
            <tr>
              <th style={cellHead}>Símbolo</th>
              <th style={cellHead}>Mkt</th>
              <th style={cellHead}>Trades</th>
              <th style={cellHead}>Win %</th>
              <th style={cellHead}>PnL USDT</th>
              <th style={cellHead}>ROI %</th>
              <th style={cellHead}>Fees</th>
              <th style={cellHead}>Hold médio</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={8} style={{ color: C.muted, padding: 16, textAlign: "center" }}>Sem dados.</td></tr>
            ) : rows.map(r => (
              <tr key={`${r.symbol}-${r.market_type}`} style={{ borderTop: `1px solid ${C.border}` }}>
                <td style={cellTd}>{r.symbol}</td>
                <td style={cellTd}>{r.market_type}</td>
                <td style={cellTd}>{r.trades}</td>
                <td style={cellTd}>{fmtPct(r.win_rate_pct, 1)}</td>
                <td style={{ ...cellTd, color: r.pnl_usdt >= 0 ? C.green : C.red }}>{r.pnl_usdt >= 0 ? "+" : ""}${fmtUsd(r.pnl_usdt)}</td>
                <td style={{ ...cellTd, color: r.roi_pct >= 0 ? C.green : C.red }}>{fmtPct(r.roi_pct, 2)}</td>
                <td style={cellTd}>${fmtUsd(r.fees_usdt, 4)}</td>
                <td style={cellTd}>{fmtSec(r.avg_holding_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const cellHead: React.CSSProperties = { textAlign: "left", padding: "8px 6px", fontSize: 11, letterSpacing: 0.4 };
const cellTd: React.CSSProperties = { padding: "7px 6px", color: C.text };

// ── executions table ────────────────────────────────────────────────────────
function ExecutionsTable({ filter }: { filter: PerformanceFilter }) {
  const [ex, setEx] = useState<ExecutionFilters>({ page: 1, page_size: 25, sort: "closed_at_desc" });
  const [expanded, setExpanded] = useState<number | null>(null);
  const { data, isLoading } = usePerformanceExecutions(filter, ex);
  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const page = data?.page ?? 1;
  const pageSize = data?.page_size ?? 25;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));

  const onSort = (key: string) => {
    setEx(prev => {
      const isDesc = prev.sort === `${key}_desc`;
      return { ...prev, sort: isDesc ? `${key}_asc` : `${key}_desc`, page: 1 };
    });
  };

  const exportCsv = () => {
    if (!rows.length) return;
    const headers = [
      "id","symbol","market_type","direction","opened_at","closed_at","holding_s",
      "qty","avg_entry","avg_exit","invested_usdt","final_usdt","fees_total",
      "pnl_usdt","pnl_pct","roi","status","data_quality",
    ];
    const lines = [headers.join(",")];
    rows.forEach(r => lines.push([
      r.id, r.symbol, r.market_type, r.direction,
      r.opened_at ?? "", r.closed_at ?? "", r.holding_seconds ?? "",
      r.qty ?? "", r.avg_entry ?? "", r.avg_exit ?? "",
      r.invested_usdt ?? "", r.final_usdt ?? "", r.fees_total ?? "",
      r.pnl_usdt ?? "", r.pnl_pct ?? "", r.roi ?? "",
      r.status, r.data_quality,
    ].join(",")));
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url;
    a.download = `executions_${filter.window}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10, gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 12, letterSpacing: 0.6, textTransform: "uppercase" }}>
          <Settings2 size={13} /> Histórico de Execuções
          <span style={{ color: C.dim, fontSize: 11, textTransform: "none", letterSpacing: 0 }}>
            · {total.toLocaleString("en-US")} resultados
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input placeholder="Símbolo (ex: BTC_USDT)" value={ex.symbol ?? ""}
            onChange={e => setEx({ ...ex, symbol: e.target.value || undefined, page: 1 })}
            style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, padding: "5px 8px", borderRadius: 6, width: 160 }} />
          <input placeholder="Trade ID" value={ex.search ?? ""}
            onChange={e => setEx({ ...ex, search: e.target.value || undefined, page: 1 })}
            style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, padding: "5px 8px", borderRadius: 6, width: 130 }} />
          <select value={ex.market_type ?? ""} onChange={e => setEx({ ...ex, market_type: e.target.value || undefined, page: 1 })}
            style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, padding: "5px 8px", borderRadius: 6 }}>
            <option value="">Mkt</option><option value="spot">Spot</option><option value="futures">Futures</option>
          </select>
          <select value={ex.direction ?? ""} onChange={e => setEx({ ...ex, direction: e.target.value || undefined, page: 1 })}
            style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, padding: "5px 8px", borderRadius: 6 }}>
            <option value="">Lado</option><option value="long">Long</option><option value="short">Short</option>
          </select>
          <select value={ex.status ?? ""} onChange={e => setEx({ ...ex, status: e.target.value || undefined, page: 1 })}
            style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, padding: "5px 8px", borderRadius: 6 }}>
            <option value="">Status</option><option value="closed">Fechado</option><option value="open">Aberto</option>
          </select>
          <button onClick={exportCsv}
            style={{ background: C.elevated, border: `1px solid ${C.borderStrong}`, color: C.text,
              borderRadius: 6, padding: "6px 10px", fontSize: 12, cursor: "pointer",
              display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Download size={12} /> CSV
          </button>
        </div>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, fontVariantNumeric: "tabular-nums" }}>
          <thead style={{ color: C.muted }}>
            <tr>
              <th style={cellHead}></th>
              <th style={{ ...cellHead, cursor: "pointer" }} onClick={() => onSort("symbol")}>Par</th>
              <th style={cellHead}>Mkt</th>
              <th style={cellHead}>Lado</th>
              <th style={cellHead}>Aberto</th>
              <th style={cellHead}>Fechado</th>
              <th style={{ ...cellHead, cursor: "pointer" }} onClick={() => onSort("holding")}>Hold</th>
              <th style={cellHead}>Qty</th>
              <th style={cellHead}>Entrada</th>
              <th style={cellHead}>Saída</th>
              <th style={cellHead}>Investido</th>
              <th style={cellHead}>Final</th>
              <th style={cellHead}>Fees</th>
              <th style={{ ...cellHead, cursor: "pointer" }} onClick={() => onSort("pnl")}>PnL</th>
              <th style={cellHead}>PnL %</th>
              <th style={cellHead}>Status</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={16} style={{ color: C.muted, padding: 16, textAlign: "center" }}>Carregando…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={16} style={{ color: C.muted, padding: 16, textAlign: "center" }}>
                Sem execuções no período. Clique em <b>Sync Gate.io</b> para importar.</td></tr>
            ) : rows.map(r => {
              const isOpen = expanded === r.id;
              const pnlColor = (r.pnl_usdt ?? 0) >= 0 ? C.green : C.red;
              return (
                <>
                  <tr key={r.id} style={{ borderTop: `1px solid ${C.border}`, cursor: "pointer" }}
                    onClick={() => setExpanded(isOpen ? null : r.id)}>
                    <td style={{ ...cellTd, color: C.muted }}>{isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</td>
                    <td style={cellTd}>{r.symbol}</td>
                    <td style={cellTd}>{r.market_type}</td>
                    <td style={{ ...cellTd, color: r.direction === "long" ? C.green : C.red }}>
                      {r.direction === "long" ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />} {r.direction}
                    </td>
                    <td style={cellTd}>{r.opened_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                    <td style={cellTd}>{r.closed_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                    <td style={cellTd}>{fmtSec(r.holding_seconds)}</td>
                    <td style={cellTd}>{num(r.qty, 6)}</td>
                    <td style={cellTd}>{num(r.avg_entry, 6)}</td>
                    <td style={cellTd}>{num(r.avg_exit, 6)}</td>
                    <td style={cellTd}>${fmtUsd(r.invested_usdt)}</td>
                    <td style={cellTd}>{r.final_usdt === null ? "—" : `$${fmtUsd(r.final_usdt)}`}</td>
                    <td style={cellTd}>${fmtUsd(r.fees_total ?? 0, 4)}</td>
                    <td style={{ ...cellTd, color: pnlColor, fontWeight: 600 }}>
                      {r.pnl_usdt === null ? "—" : `${r.pnl_usdt >= 0 ? "+" : ""}$${fmtUsd(r.pnl_usdt)}`}
                    </td>
                    <td style={{ ...cellTd, color: pnlColor }}>{fmtPct(r.pnl_pct, 2)}</td>
                    <td style={cellTd}>
                      <span style={{
                        background: r.data_quality === "OK" ? "rgba(34,185,122,0.12)" : "rgba(242,163,58,0.12)",
                        color: r.data_quality === "OK" ? C.green : C.amber,
                        padding: "2px 6px", borderRadius: 4, fontSize: 10, letterSpacing: 0.4,
                      }}>{r.status}{r.data_quality !== "OK" ? ` · ${r.data_quality}` : ""}</span>
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr key={`${r.id}-x`}>
                      <td colSpan={16} style={{ background: C.bg, borderTop: `1px solid ${C.border}`, padding: "10px 16px" }}>
                        <FillsBreakdown lifecycleId={r.id} />
                      </td>
                    </tr>
                  ) : null}
                </>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10, color: C.muted, fontSize: 11.5 }}>
        <div>Página {page} de {lastPage}</div>
        <div style={{ display: "flex", gap: 6 }}>
          <button disabled={page <= 1} onClick={() => setEx({ ...ex, page: Math.max(1, page - 1) })}
            style={pagerBtn(page <= 1)}>Anterior</button>
          <button disabled={page >= lastPage} onClick={() => setEx({ ...ex, page: Math.min(lastPage, page + 1) })}
            style={pagerBtn(page >= lastPage)}>Próxima</button>
        </div>
      </div>
    </div>
  );
}

const pagerBtn = (disabled: boolean): React.CSSProperties => ({
  background: C.elevated, border: `1px solid ${C.border}`, color: disabled ? C.dim : C.text,
  borderRadius: 6, padding: "5px 10px", fontSize: 12, cursor: disabled ? "default" : "pointer",
  opacity: disabled ? 0.5 : 1,
});

interface FillRow {
  trade_id: string;
  order_id: string | null;
  side: string;
  role: string | null;
  price: number | null;
  quantity: number | null;
  fee: number | null;
  fee_currency: string | null;
  executed_at: string | null;
}

function FillsBreakdown({ lifecycleId }: { lifecycleId: number }) {
  // Fills are heavy/optional → fetched lazily once expanded.
  const [rows, setRows] = useState<FillRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    apiGet<{ rows: FillRow[] }>(`/api/performance/executions/${lifecycleId}/fills`)
      .then(d => { if (!cancel) setRows(d.rows ?? []); })
      .catch(() => { if (!cancel) setRows([]); })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [lifecycleId]);

  if (loading) return <div style={{ color: C.muted, fontSize: 11.5 }}>Carregando fills…</div>;
  if (!rows.length) return <div style={{ color: C.muted, fontSize: 11.5 }}>Sem fills detalhados.</div>;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
      <thead style={{ color: C.muted }}>
        <tr>
          <th style={cellHead}>Hora</th><th style={cellHead}>Lado</th><th style={cellHead}>Role</th>
          <th style={cellHead}>Preço</th><th style={cellHead}>Qty</th>
          <th style={cellHead}>Fee</th><th style={cellHead}>Order ID</th><th style={cellHead}>Trade ID</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(f => (
          <tr key={f.trade_id} style={{ borderTop: `1px solid ${C.border}` }}>
            <td style={cellTd}>{f.executed_at?.slice(0, 19).replace("T", " ") ?? "—"}</td>
            <td style={{ ...cellTd, color: f.side === "buy" ? C.green : C.red }}>{f.side}</td>
            <td style={cellTd}>{f.role ?? "—"}</td>
            <td style={cellTd}>{num(f.price, 8)}</td>
            <td style={cellTd}>{num(f.quantity, 8)}</td>
            <td style={cellTd}>{num(f.fee, 8)} {f.fee_currency ?? ""}</td>
            <td style={cellTd}>{f.order_id ?? "—"}</td>
            <td style={{ ...cellTd, color: C.muted }}>{f.trade_id}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── page ────────────────────────────────────────────────────────────────────
export default function PerformanceDashboardPage() {
  const [filter, setFilter] = useState<PerformanceFilter>({ window: "30D", autoRefresh: false });
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [syncWarn, setSyncWarn] = useState<string | null>(null);

  const onSync = async () => {
    setSyncing(true); setSyncMsg(null); setSyncWarn(null);
    try {
      type SyncResp = {
        sync?: { imported?: { spot?: number; futures?: number } };
        rebuild?: { lifecycle_rows_closed?: number; open_positions?: number };
        history_window_capped?: boolean;
        effective_days?: number;
        requested_days?: number;
        message?: string | null;
      };
      const r = await apiPost<SyncResp>("/api/performance/sync?days=90&markets=spot,futures");
      setSyncMsg(
        `Importados spot=${r?.sync?.imported?.spot ?? 0} futures=${r?.sync?.imported?.futures ?? 0} · ` +
        `lifecycle rebuilt: ${r?.rebuild?.lifecycle_rows_closed ?? 0} fechados, ${r?.rebuild?.open_positions ?? 0} abertos.`
      );
      if (r?.history_window_capped) {
        setSyncWarn(
          r.message ??
          `Gate.io só permite consultar os últimos ${r.effective_days ?? "?"} dia(s) ` +
          `(você pediu ${r.requested_days ?? 90}).`
        );
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setSyncMsg(`Falhou: ${msg}`);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div style={{ background: C.bg, minHeight: "100vh", color: C.text, padding: "20px 24px" }}>
      <div style={{ maxWidth: 1480, margin: "0 auto", display: "grid", gap: 14 }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <div>
            <div style={{ fontSize: 19, fontWeight: 600, letterSpacing: 0.3 }}>Performance Institucional</div>
            <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
              Trades reais executados na Gate.io (Spot + Futures) · FIFO PnL determinístico · Centro Operacional movido para
              <a href="/dashboard/operations" style={{ color: C.blue, marginLeft: 4 }}>/dashboard/operations</a>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.muted, fontSize: 11 }}>
            <Activity size={14} /> SSOT: <code style={{ color: C.text }}>position_lifecycle</code>
          </div>
        </header>

        <FilterBar filter={filter} onChange={setFilter} onSync={onSync} syncing={syncing} />
        {syncMsg ? (
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px", fontSize: 12, color: C.muted }}>
            {syncMsg}
          </div>
        ) : null}
        {syncWarn ? (
          <div style={{
            background: "rgba(242,163,58,0.08)",
            border: `1px solid ${C.amber}55`,
            borderRadius: 8, padding: "8px 12px", fontSize: 12, color: C.amber,
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <AlertTriangle size={13} /> {syncWarn}
          </div>
        ) : null}

        <FlashCards filter={filter} />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <EquityChart filter={filter} />
          <DailyPnLChart filter={filter} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <DistributionPanel filter={filter} />
          <HeatmapPanel filter={filter} />
        </div>

        <ByAssetTable filter={filter} />

        <ExecutionsTable filter={filter} />
      </div>
    </div>
  );
}
