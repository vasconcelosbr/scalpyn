"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Download,
  Gauge,
  RefreshCw,
  Server,
  ShieldAlert,
  Sigma,
  TrendingUp,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiGet } from "@/lib/api";

// ─── Theme ───────────────────────────────────────────────────────────────────
const C = {
  surface: "#0C0D12",
  elevated: "#12131A",
  elevated2: "#1A1C28",
  border: "rgba(255,255,255,0.07)",
  borderStrong: "rgba(255,255,255,0.12)",
  textPrimary: "#E8ECF4",
  textSecondary: "#8B92A5",
  textTertiary: "#555B6E",
  ok: "#22c55e",
  warn: "#f59e0b",
  critical: "#ef4444",
  blue: "#4F7BF7",
  purple: "#a78bfa",
} as const;

const STATUS_STYLES: Record<string, { bg: string; border: string; text: string; icon: React.ReactNode }> = {
  ok: {
    bg: "rgba(34,197,94,0.12)",
    border: "rgba(34,197,94,0.45)",
    text: C.ok,
    icon: <CheckCircle2 size={20} />,
  },
  warn: {
    bg: "rgba(245,158,11,0.12)",
    border: "rgba(245,158,11,0.45)",
    text: C.warn,
    icon: <AlertTriangle size={20} />,
  },
  critical: {
    bg: "rgba(239,68,68,0.14)",
    border: "rgba(239,68,68,0.55)",
    text: C.critical,
    icon: <ShieldAlert size={20} />,
  },
  unknown: {
    bg: "rgba(139,146,165,0.10)",
    border: "rgba(139,146,165,0.30)",
    text: C.textSecondary,
    icon: <Activity size={20} />,
  },
};

// ─── Types ───────────────────────────────────────────────────────────────────
interface HealthResp {
  rows_window: number;
  distinct_symbols: number;
  last_candle: string | null;
  delay_seconds: number | null;
  status: "ok" | "warn" | "critical" | "unknown";
  status_label: string;
}

interface SystemStatusResp {
  redis_alive: boolean;
  redis_error: string | null;
  last_ohlcv_ts: string | null;
  last_ohlcv_age_seconds: number | null;
  last_decision_ts: string | null;
  last_decision_age_seconds: number | null;
  last_pipeline_scan_ts: string | null;
  last_pipeline_scan_age_seconds: number | null;
}

interface OhlcvRateResp {
  window_minutes: number;
  timeframe: string;
  total_candles: number;
  buckets: { bucket: string; candles: number }[];
}

interface DecisionsResp {
  window_hours: number;
  total: number;
  allow: number;
  block: number;
  allow_rate: number;
  avg_score: number | null;
  score_distribution: { bucket: string; count: number }[];
  top_block_reasons: { reason: string; count: number }[];
}

interface TradesResp {
  window_days: number;
  total: number;
  win_rate: number | null;
  avg_pnl_pct: number | null;
  avg_holding_seconds: number | null;
  cumulative_pnl: { time: string; cumulative_pnl_pct: number }[];
}

interface CompResp {
  window_days: number;
  items: { kind: string; total: number; win_rate: number | null; avg_pnl_pct: number | null }[];
}

interface MlResp {
  total: number;
  items: {
    id: string;
    symbol: string;
    direction: string;
    decision_type: string;
    result: string;
    time_to_result: number | null;
    entry_price: number;
    exit_price: number | null;
    timestamp_entry: string;
  }[];
}

// ─── Polling hook ────────────────────────────────────────────────────────────
function usePoll<T>(endpoint: string, intervalMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const res = await apiGet<T>(endpoint);
        if (!cancelled) {
          setData(res);
          setError(null);
        }
      } catch (e: unknown) {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    const id = setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [endpoint, intervalMs, tick]);

  return { data, error, loading, refresh };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}
function fmtPctSigned(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}
function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ─── Shared UI ───────────────────────────────────────────────────────────────
function Panel({
  title,
  icon,
  children,
  right,
  className = "",
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl p-5 flex flex-col gap-4 ${className}`}
      style={{ background: C.elevated, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span style={{ color: C.textSecondary }}>{icon}</span>
          <h3 className="text-[13px] font-semibold uppercase tracking-[0.06em]" style={{ color: C.textSecondary }}>
            {title}
          </h3>
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

function StatTile({ label, value, hint, color }: { label: string; value: string; hint?: string; color?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wide" style={{ color: C.textTertiary }}>
        {label}
      </span>
      <span className="text-[22px] font-bold tabular-nums" style={{ color: color ?? C.textPrimary }}>
        {value}
      </span>
      {hint && (
        <span className="text-[11px]" style={{ color: C.textTertiary }}>
          {hint}
        </span>
      )}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center py-10">
      <p className="text-sm" style={{ color: C.textTertiary }}>
        {message}
      </p>
    </div>
  );
}

// ─── Health banner ───────────────────────────────────────────────────────────
function HealthBanner({ data }: { data: HealthResp | null }) {
  const status = data?.status ?? "unknown";
  const style = STATUS_STYLES[status];
  return (
    <div
      className="rounded-2xl p-5 flex items-center justify-between gap-4"
      style={{
        background: style.bg,
        border: `1px solid ${style.border}`,
      }}
    >
      <div className="flex items-center gap-4">
        <div style={{ color: style.text }}>{style.icon}</div>
        <div className="flex flex-col">
          <span className="text-[18px] font-bold" style={{ color: style.text }}>
            {data?.status_label ?? "Carregando…"}
          </span>
          <span className="text-[12px]" style={{ color: C.textSecondary }}>
            Limiares: verde &lt; 6 min · amarelo 6–10 min · vermelho &gt; 10 min
          </span>
        </div>
      </div>
      <div className="flex items-center gap-6">
        <StatTile
          label="Atraso"
          value={fmtAge(data?.delay_seconds)}
          color={style.text}
        />
        <StatTile
          label="Símbolos"
          value={String(data?.distinct_symbols ?? "—")}
        />
        <StatTile
          label="Candles (15m)"
          value={String(data?.rows_window ?? "—")}
        />
        <StatTile
          label="Último candle"
          value={fmtTime(data?.last_candle)}
          hint={data?.last_candle ? new Date(data.last_candle).toLocaleDateString("pt-BR") : undefined}
        />
      </div>
    </div>
  );
}

// ─── System status panel ────────────────────────────────────────────────────
function SystemStatusPanel({ data }: { data: SystemStatusResp | null }) {
  return (
    <Panel title="System Status" icon={<Server size={16} />}>
      <div className="grid grid-cols-2 gap-4">
        <div className="flex items-center gap-2">
          <span
            className="w-2.5 h-2.5 rounded-full"
            style={{ background: data?.redis_alive ? C.ok : C.critical }}
          />
          <span className="text-sm" style={{ color: C.textPrimary }}>
            Redis {data?.redis_alive ? "online" : "offline"}
          </span>
        </div>
        <StatTile
          label="Último candle OHLCV"
          value={fmtAge(data?.last_ohlcv_age_seconds)}
          hint="atrás"
        />
        <StatTile
          label="Última decisão"
          value={fmtAge(data?.last_decision_age_seconds)}
          hint={data?.last_decision_ts ? fmtTime(data.last_decision_ts) : "sem registros"}
        />
        <StatTile
          label="Última varredura"
          value={fmtAge(data?.last_pipeline_scan_age_seconds)}
          hint="≈ pelo decisions_log"
        />
      </div>
      {data?.redis_error && (
        <p className="text-[11px] mt-2" style={{ color: C.critical }}>
          {data.redis_error}
        </p>
      )}
    </Panel>
  );
}

// ─── Ingest rate chart ──────────────────────────────────────────────────────
function IngestRateChart({ data }: { data: OhlcvRateResp | null }) {
  const chartData = useMemo(
    () =>
      (data?.buckets ?? []).map((b) => ({
        label: new Date(b.bucket).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
        candles: b.candles,
      })),
    [data],
  );
  return (
    <Panel
      title="Volume de ingestão (60 min)"
      icon={<Gauge size={16} />}
      right={
        <span className="text-[12px]" style={{ color: C.textSecondary }}>
          Total: <strong style={{ color: C.textPrimary }}>{data?.total_candles ?? 0}</strong>
        </span>
      }
    >
      {chartData.length === 0 ? (
        <EmptyState message="Sem candles na janela." />
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={chartData} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="ingestGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={C.blue} stopOpacity={0.35} />
                <stop offset="95%" stopColor={C.blue} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
            <YAxis tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} width={32} allowDecimals={false} />
            <Tooltip
              contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }}
              labelStyle={{ color: C.textSecondary }}
              itemStyle={{ color: C.textPrimary }}
            />
            <Area type="monotone" dataKey="candles" stroke={C.blue} strokeWidth={2} fill="url(#ingestGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

// ─── Decision stats ─────────────────────────────────────────────────────────
function DecisionStatsPanel({ data }: { data: DecisionsResp | null }) {
  const pieData = useMemo(
    () => [
      { name: "ALLOW", value: data?.allow ?? 0, color: C.ok },
      { name: "BLOCK", value: data?.block ?? 0, color: C.critical },
    ],
    [data],
  );
  const dist = data?.score_distribution ?? [];
  const reasons = data?.top_block_reasons ?? [];
  const empty = (data?.total ?? 0) === 0;

  return (
    <Panel title="Decisões (24 h)" icon={<Sigma size={16} />}>
      {empty ? (
        <EmptyState message="Nenhuma decisão registrada na última janela." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="flex flex-col gap-3">
            <StatTile label="Total" value={String(data?.total ?? 0)} />
            <StatTile label="Taxa ALLOW" value={fmtPct(data?.allow_rate)} color={C.ok} />
            <StatTile label="Score médio" value={data?.avg_score != null ? data.avg_score.toFixed(1) : "—"} />
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={42} outerRadius={70} paddingAngle={2}>
                  {pieData.map((d) => (
                    <Cell key={d.name} fill={d.color} stroke="none" />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }}
                  labelStyle={{ color: C.textSecondary }}
                  itemStyle={{ color: C.textPrimary }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dist} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                <XAxis dataKey="bucket" tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
                <Tooltip
                  contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }}
                  labelStyle={{ color: C.textSecondary }}
                  itemStyle={{ color: C.textPrimary }}
                />
                <Bar dataKey="count" fill={C.purple} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {!empty && reasons.length > 0 && (
        <div className="border-t pt-3" style={{ borderColor: C.border }}>
          <p className="text-[11px] uppercase tracking-wide mb-2" style={{ color: C.textTertiary }}>
            Top motivos de bloqueio
          </p>
          <ul className="flex flex-wrap gap-2">
            {reasons.map((r) => (
              <li
                key={r.reason}
                className="text-[12px] px-2 py-1 rounded-md"
                style={{ background: C.elevated2, border: `1px solid ${C.border}`, color: C.textPrimary }}
              >
                {r.reason} <span style={{ color: C.textTertiary }}>· {r.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

// ─── Trade performance ─────────────────────────────────────────────────────
function TradePerformancePanel({ data }: { data: TradesResp | null }) {
  const curve = useMemo(
    () =>
      (data?.cumulative_pnl ?? []).map((p) => ({
        label: new Date(p.time).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }),
        value: p.cumulative_pnl_pct,
      })),
    [data],
  );
  const empty = (data?.total ?? 0) === 0;
  const lastVal = curve.length ? curve[curve.length - 1].value : 0;
  const isUp = lastVal >= 0;

  return (
    <Panel title="Performance — trades reais (30 d)" icon={<TrendingUp size={16} />}>
      {empty ? (
        <EmptyState message="Nenhum trade real fechado na janela." />
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatTile label="Total" value={String(data?.total ?? 0)} />
            <StatTile label="Win rate" value={fmtPct(data?.win_rate)} color={C.ok} />
            <StatTile label="PnL médio" value={fmtPctSigned(data?.avg_pnl_pct)} color={(data?.avg_pnl_pct ?? 0) >= 0 ? C.ok : C.critical} />
            <StatTile label="Holding médio" value={fmtAge(data?.avg_holding_seconds)} />
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={curve} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="pnlUp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={C.ok} stopOpacity={0.30} />
                  <stop offset="95%" stopColor={C.ok} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="pnlDown" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={C.critical} stopOpacity={0.28} />
                  <stop offset="95%" stopColor={C.critical} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fill: C.textTertiary, fontSize: 11 }} tickFormatter={(v) => `${v.toFixed(1)}%`} tickLine={false} axisLine={false} width={48} />
              <Tooltip
                contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }}
                labelStyle={{ color: C.textSecondary }}
                itemStyle={{ color: C.textPrimary }}
                formatter={(v) => fmtPctSigned(typeof v === "number" ? v : Number(v))}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke={isUp ? C.ok : C.critical}
                strokeWidth={2}
                fill={isUp ? "url(#pnlUp)" : "url(#pnlDown)"}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </>
      )}
    </Panel>
  );
}

// ─── Sim vs real ───────────────────────────────────────────────────────────
function SimVsRealPanel({ data }: { data: CompResp | null }) {
  const real = data?.items.find((i) => i.kind === "real");
  const sim = data?.items.find((i) => i.kind === "simulated");
  const Card = ({ title, item, accent }: { title: string; item?: { total: number; win_rate: number | null; avg_pnl_pct: number | null }; accent: string }) => (
    <div
      className="rounded-xl p-4 flex flex-col gap-2"
      style={{ background: C.elevated2, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center justify-between">
        <span className="text-[12px] uppercase tracking-wide" style={{ color: C.textTertiary }}>{title}</span>
        <span className="w-2 h-2 rounded-full" style={{ background: accent }} />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <StatTile label="N" value={String(item?.total ?? 0)} />
        <StatTile label="Win" value={fmtPct(item?.win_rate ?? null)} />
        <StatTile label="PnL" value={fmtPctSigned(item?.avg_pnl_pct ?? null)} />
      </div>
    </div>
  );
  return (
    <Panel title="Simulado vs Real (30 d)" icon={<Activity size={16} />}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Card title="Reais" item={real} accent={C.ok} />
        <Card title="Simulados" item={sim} accent={C.purple} />
      </div>
    </Panel>
  );
}

// ─── ML dataset table ──────────────────────────────────────────────────────
function MLDatasetPanel({ data }: { data: MlResp | null }) {
  const handleExport = useCallback(async () => {
    try {
      const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
      const res = await fetch("/api/dashboard/ml-dataset/export?limit=1000", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "scalpyn_ml_dataset.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("Export failed", e);
    }
  }, []);
  const items = data?.items ?? [];
  return (
    <Panel
      title="ML Dataset — últimas 100 simulações"
      icon={<Database size={16} />}
      right={
        <button
          onClick={handleExport}
          className="flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-lg transition-colors"
          style={{ background: C.elevated2, border: `1px solid ${C.border}`, color: C.textPrimary }}
        >
          <Download size={13} /> Exportar CSV
        </button>
      }
    >
      {items.length === 0 ? (
        <EmptyState message="Sem simulações registradas." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr style={{ color: C.textTertiary }}>
                {["Símbolo", "Direção", "Decisão", "Resultado", "Tempo (s)", "Entrada", "Saída", "Quando"].map((h) => (
                  <th key={h} className="text-left font-medium uppercase tracking-wide py-2 px-2">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((r) => {
                const resultColor =
                  r.result === "WIN" ? C.ok : r.result === "LOSS" ? C.critical : C.warn;
                return (
                  <tr key={r.id} style={{ borderTop: `1px solid ${C.border}` }}>
                    <td className="py-2 px-2 font-medium" style={{ color: C.textPrimary }}>{r.symbol}</td>
                    <td className="py-2 px-2" style={{ color: C.textSecondary }}>{r.direction}</td>
                    <td className="py-2 px-2" style={{ color: C.textSecondary }}>{r.decision_type}</td>
                    <td className="py-2 px-2 font-semibold" style={{ color: resultColor }}>{r.result}</td>
                    <td className="py-2 px-2 tabular-nums" style={{ color: C.textSecondary }}>{r.time_to_result ?? "—"}</td>
                    <td className="py-2 px-2 tabular-nums" style={{ color: C.textSecondary }}>{r.entry_price.toFixed(4)}</td>
                    <td className="py-2 px-2 tabular-nums" style={{ color: C.textSecondary }}>{r.exit_price?.toFixed(4) ?? "—"}</td>
                    <td className="py-2 px-2" style={{ color: C.textTertiary }}>{new Date(r.timestamp_entry).toLocaleString("pt-BR")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

// ─── Page root ─────────────────────────────────────────────────────────────
export default function PerformanceDashboardPage() {
  const health = usePoll<HealthResp>("/dashboard/health", 10_000);
  const sysstat = usePoll<SystemStatusResp>("/dashboard/system-status", 60_000);
  const ingest = usePoll<OhlcvRateResp>("/dashboard/ohlcv-rate?minutes=60", 15_000);
  const decisions = usePoll<DecisionsResp>("/dashboard/decisions?hours=24", 60_000);
  const trades = usePoll<TradesResp>("/dashboard/trades?days=30", 60_000);
  const comp = usePoll<CompResp>("/dashboard/trade-comparison?days=30", 60_000);
  const ml = usePoll<MlResp>("/dashboard/ml-dataset?limit=100", 60_000);

  const refreshAll = () => {
    health.refresh(); sysstat.refresh(); ingest.refresh();
    decisions.refresh(); trades.refresh(); comp.refresh(); ml.refresh();
  };

  return (
    <div className="min-h-screen px-6 py-8 max-w-[1400px] mx-auto" style={{ background: C.surface }}>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: C.textPrimary }}>
            Performance Operacional
          </h1>
          <p className="text-[13px] mt-1" style={{ color: C.textSecondary }}>
            Saúde do pipeline, decisões e desempenho — atualização automática.
          </p>
        </div>
        <button
          onClick={refreshAll}
          className="flex items-center gap-2 text-[13px] px-3 py-2 rounded-lg transition-colors"
          style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.textPrimary }}
        >
          <RefreshCw size={14} /> Atualizar
        </button>
      </div>

      <div className="flex flex-col gap-5">
        <HealthBanner data={health.data} />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <SystemStatusPanel data={sysstat.data} />
          <IngestRateChart data={ingest.data} />
        </div>
        <DecisionStatsPanel data={decisions.data} />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2"><TradePerformancePanel data={trades.data} /></div>
          <SimVsRealPanel data={comp.data} />
        </div>
        <MLDatasetPanel data={ml.data} />

        {(health.error || sysstat.error || ingest.error || decisions.error || trades.error || comp.error || ml.error) && (
          <div className="text-[12px] px-3 py-2 rounded-lg" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)", color: C.critical }}>
            Falha em algum endpoint do dashboard. Veja o console para detalhes.
          </div>
        )}
      </div>
    </div>
  );
}
