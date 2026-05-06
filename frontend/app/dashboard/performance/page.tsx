"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Database,
  Download,
  Gauge,
  RefreshCw,
  ShieldAlert,
  Sigma,
  TrendingUp,
  Cpu,
  Zap,
  Clock,
  History,
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
  ok:       { bg: "rgba(34,197,94,0.12)",  border: "rgba(34,197,94,0.45)",  text: C.ok,            icon: <CheckCircle2 size={20} /> },
  degraded: { bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.45)", text: C.warn,          icon: <AlertTriangle size={20} /> },
  warn:     { bg: "rgba(245,158,11,0.12)", border: "rgba(245,158,11,0.45)", text: C.warn,          icon: <AlertTriangle size={20} /> },
  critical: { bg: "rgba(239,68,68,0.14)",  border: "rgba(239,68,68,0.55)",  text: C.critical,      icon: <ShieldAlert size={20} /> },
  unknown:  { bg: "rgba(139,146,165,0.10)", border: "rgba(139,146,165,0.30)", text: C.textSecondary, icon: <Activity size={20} /> },
};

const STATUS_LABEL_PT: Record<string, string> = {
  ok: "Pipeline saudável",
  degraded: "Degradado",
  warn: "Atrasado",
  critical: "Crítico",
  unknown: "Sem dados",
};

// ─── Types ───────────────────────────────────────────────────────────────────
interface SnapshotEnvelope<T = Record<string, unknown>> {
  as_of: string | null;
  status: "ok" | "degraded" | "critical" | "unknown";
  data: T;
  error: string | null;
  failure_streak?: number;
}

interface OperationalAlert {
  severity: "warning" | "critical";
  category: string;
  code: string;
  impact: string;
  since: string | null;
  details: Record<string, unknown>;
}

interface QueueStats { active: number; reserved: number; scheduled: number }

interface OverviewResp {
  as_of: string;
  overall_status: "ok" | "degraded" | "critical" | "unknown";
  snapshots: {
    ingestion: SnapshotEnvelope<{
      rows_window?: number;
      distinct_symbols?: number;
      last_candle?: string | null;
      delay_seconds?: number | null;
    }>;
    celery: SnapshotEnvelope<{
      workers?: string[];
      worker_count?: number;
      active_tasks?: number;
      reserved_tasks?: number;
      scheduled_tasks?: number;
      registered_tasks?: number;
      per_queue?: Record<string, QueueStats>;
      beat?: { status?: string; schedule_age_seconds?: number | null };
    }>;
    redis: SnapshotEnvelope<{
      alive?: boolean;
      ping_ms?: number;
      connected_clients?: number;
      used_memory_human?: string;
      instantaneous_ops_per_sec?: number;
      total_commands_processed?: number;
      queue_lengths?: Record<string, number>;
      backlog_total?: number;
      unrouted_backlog?: number;
    }>;
    db: SnapshotEnvelope<{
      select1_ms?: number;
      pool_size?: number;
      checked_out?: number;
      checked_in?: number;
      overflow?: number;
      status?: string;
    }>;
    score: SnapshotEnvelope<{
      decisions_24h?: number;
      allow_24h?: number;
      block_24h?: number;
      allow_rate_24h?: number;
      avg_score?: number | null;
      min_score?: number | null;
      max_score?: number | null;
      last_decision?: string | null;
      last_decision_age_seconds?: number | null;
    }>;
    ingestion_latency: SnapshotEnvelope<{
      delay_seconds?: number | null;
      last_candle?: string | null;
      rows_window?: number;
    }>;
    decision_latency: SnapshotEnvelope<{
      p50_ms?: number | null;
      p95_ms?: number | null;
      max_ms?: number | null;
      samples_1h?: number;
    }>;
    processing_latency: SnapshotEnvelope<{
      available?: boolean;
      samples?: number;
      p50_ms?: number | null;
      p95_ms?: number | null;
      avg_ms?: number | null;
    }>;
  };
  alerts: OperationalAlert[];
  alert_count: number;
}

interface EventItem {
  ts: string;
  code: string;
  message: string;
  extra: Record<string, unknown>;
  category?: string;
}
interface EventsResp {
  as_of: string;
  alert_history?: EventItem[];
  worker_events?: EventItem[];
  redis_degradations?: EventItem[];
}

// Analytics types (lazy section)
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
function usePoll<T>(endpoint: string | null, intervalMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(endpoint != null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (endpoint == null) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
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
function fmtMs(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)} ms`;
}
function statusColor(s: string | undefined | null): string {
  if (s === "ok") return C.ok;
  if (s === "degraded" || s === "warn") return C.warn;
  if (s === "critical") return C.critical;
  return C.textSecondary;
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
      <span className="text-[11px] uppercase tracking-wide" style={{ color: C.textTertiary }}>{label}</span>
      <span className="text-[22px] font-bold tabular-nums" style={{ color: color ?? C.textPrimary }}>{value}</span>
      {hint && <span className="text-[11px]" style={{ color: C.textTertiary }}>{hint}</span>}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center py-10">
      <p className="text-sm" style={{ color: C.textTertiary }}>{message}</p>
    </div>
  );
}

// ─── Operational banner (driven by /overview) ───────────────────────────────
function OperationalBanner({ data }: { data: OverviewResp | null }) {
  const overall = data?.overall_status ?? "unknown";
  const style = STATUS_STYLES[overall] ?? STATUS_STYLES.unknown;
  const ing = data?.snapshots?.ingestion?.data;
  const delay = ing?.delay_seconds ?? null;
  return (
    <div
      className="rounded-2xl p-5 flex flex-wrap items-center justify-between gap-4"
      style={{ background: style.bg, border: `1px solid ${style.border}` }}
    >
      <div className="flex items-center gap-4">
        <div style={{ color: style.text }}>{style.icon}</div>
        <div className="flex flex-col">
          <span className="text-[18px] font-bold" style={{ color: style.text }}>
            {STATUS_LABEL_PT[overall] ?? overall}
          </span>
          <span className="text-[12px]" style={{ color: C.textSecondary }}>
            Limiares de ingestão: verde &lt; 10 min · amarelo 10–20 min · vermelho &gt; 20 min · degrada após 3 falhas consecutivas
          </span>
        </div>
      </div>
      <div className="flex items-center gap-6 flex-wrap">
        <StatTile label="Atraso ingest" value={fmtAge(delay)} color={style.text} />
        <StatTile label="Símbolos" value={String(ing?.distinct_symbols ?? "—")} />
        <StatTile label="Candles (15m)" value={String(ing?.rows_window ?? "—")} />
        <StatTile
          label="Alertas ativos"
          value={String(data?.alert_count ?? 0)}
          color={(data?.alert_count ?? 0) > 0 ? C.critical : C.ok}
        />
      </div>
    </div>
  );
}

// ─── Alerts list ────────────────────────────────────────────────────────────
function AlertsPanel({ data }: { data: OverviewResp | null }) {
  const alerts = data?.alerts ?? [];
  return (
    <Panel title={`Alertas (${alerts.length})`} icon={<ShieldAlert size={16} />}>
      {alerts.length === 0 ? (
        <div className="flex items-center gap-2 py-2" style={{ color: C.ok }}>
          <CheckCircle2 size={16} />
          <span className="text-sm">Nenhum alerta ativo. Tudo operacional.</span>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {alerts.map((a) => {
            const sev = a.severity === "critical" ? C.critical : C.warn;
            return (
              <li
                key={a.code}
                className="rounded-xl p-3 flex flex-col gap-1"
                style={{
                  background: a.severity === "critical" ? "rgba(239,68,68,0.06)" : "rgba(245,158,11,0.06)",
                  border: `1px solid ${a.severity === "critical" ? "rgba(239,68,68,0.30)" : "rgba(245,158,11,0.30)"}`,
                }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wide"
                      style={{ background: sev, color: "white" }}
                    >
                      {a.severity}
                    </span>
                    <span className="text-[11px] uppercase tracking-wide" style={{ color: C.textTertiary }}>{a.category}</span>
                    <span className="text-[12px] font-mono" style={{ color: C.textSecondary }}>{a.code}</span>
                  </div>
                  {a.since && (
                    <span className="text-[11px]" style={{ color: C.textTertiary }}>desde {fmtTime(a.since)}</span>
                  )}
                </div>
                <p className="text-[13px]" style={{ color: C.textPrimary }}>{a.impact}</p>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// ─── Snapshot card ──────────────────────────────────────────────────────────
function SnapshotCard({
  title,
  icon,
  envelope,
  rows,
  footer,
}: {
  title: string;
  icon: React.ReactNode;
  envelope: SnapshotEnvelope | undefined;
  rows: { label: string; value: string }[];
  footer?: React.ReactNode;
}) {
  const status = envelope?.status ?? "unknown";
  const color = statusColor(status);
  const streak = envelope?.failure_streak ?? 0;
  return (
    <div
      className="rounded-2xl p-4 flex flex-col gap-3"
      style={{ background: C.elevated, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span style={{ color: C.textSecondary }}>{icon}</span>
          <h4 className="text-[12px] font-semibold uppercase tracking-wide" style={{ color: C.textSecondary }}>{title}</h4>
        </div>
        <span
          className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full"
          style={{ background: `${color}22`, color, border: `1px solid ${color}55` }}
        >
          {status}{streak > 0 ? ` (${streak}/3)` : ""}
        </span>
      </div>
      <div className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex justify-between text-[13px]">
            <span style={{ color: C.textTertiary }}>{r.label}</span>
            <span className="tabular-nums font-medium text-right" style={{ color: C.textPrimary }}>{r.value}</span>
          </div>
        ))}
      </div>
      {footer}
      {envelope?.error && (
        <p className="text-[11px] mt-1 truncate" title={envelope.error} style={{ color: C.critical }}>{envelope.error}</p>
      )}
      {envelope?.as_of && (
        <p className="text-[10px]" style={{ color: C.textTertiary }}>atualizado {fmtTime(envelope.as_of)}</p>
      )}
    </div>
  );
}

// ─── Per-queue mini-table (Celery + Redis backlog) ──────────────────────────
function QueueBreakdown({
  perQueue,
  queueLengths,
}: {
  perQueue?: Record<string, QueueStats>;
  queueLengths?: Record<string, number>;
}) {
  const queues = useMemo(() => {
    const names = new Set<string>([
      ...Object.keys(perQueue ?? {}),
      ...Object.keys(queueLengths ?? {}),
    ]);
    return Array.from(names).sort();
  }, [perQueue, queueLengths]);
  if (queues.length === 0) return null;
  return (
    <div className="border-t pt-2" style={{ borderColor: C.border }}>
      <p className="text-[10px] uppercase tracking-wide mb-1.5" style={{ color: C.textTertiary }}>
        Por fila — backlog (LLEN) · A/R/S
      </p>
      <ul className="flex flex-col gap-1">
        {queues.map((q) => {
          const ll = queueLengths?.[q];
          const stats = perQueue?.[q];
          const isSentinel = q === "__no_default__";
          const danger = isSentinel && (ll ?? 0) > 0;
          return (
            <li key={q} className="flex justify-between text-[12px] tabular-nums">
              <span style={{ color: danger ? C.critical : C.textPrimary }}>
                {q}{isSentinel ? " ⚠" : ""}
              </span>
              <span style={{ color: danger ? C.critical : C.textSecondary }}>
                {ll != null && ll >= 0 ? `LLEN ${ll}` : "LLEN —"}
                {stats ? ` · ${stats.active}/${stats.reserved}/${stats.scheduled}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ─── Snapshot grid ──────────────────────────────────────────────────────────
function OpsSnapshotsGrid({ data }: { data: OverviewResp | null }) {
  const s = data?.snapshots;
  const beat = s?.celery.data.beat;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <SnapshotCard
        title="Ingestão OHLCV"
        icon={<Gauge size={14} />}
        envelope={s?.ingestion}
        rows={[
          { label: "Atraso", value: fmtAge(s?.ingestion.data.delay_seconds) },
          { label: "Símbolos (15m)", value: String(s?.ingestion.data.distinct_symbols ?? "—") },
          { label: "Candles (15m)", value: String(s?.ingestion.data.rows_window ?? "—") },
          { label: "Último candle", value: fmtTime(s?.ingestion.data.last_candle) },
        ]}
      />
      <SnapshotCard
        title="Celery"
        icon={<Cpu size={14} />}
        envelope={s?.celery}
        rows={[
          { label: "Workers", value: String(s?.celery.data.worker_count ?? 0) },
          { label: "Active", value: String(s?.celery.data.active_tasks ?? 0) },
          { label: "Reserved", value: String(s?.celery.data.reserved_tasks ?? 0) },
          { label: "Scheduled", value: String(s?.celery.data.scheduled_tasks ?? 0) },
          {
            label: "Beat",
            value: `${beat?.status ?? "—"} (${fmtAge(beat?.schedule_age_seconds)})`,
          },
        ]}
        footer={<QueueBreakdown perQueue={s?.celery.data.per_queue} />}
      />
      <SnapshotCard
        title="Redis"
        icon={<Zap size={14} />}
        envelope={s?.redis}
        rows={[
          { label: "Status", value: s?.redis.data.alive ? "online" : "offline" },
          { label: "Ping", value: fmtMs(s?.redis.data.ping_ms, 1) },
          { label: "Clientes", value: String(s?.redis.data.connected_clients ?? "—") },
          { label: "Memória", value: s?.redis.data.used_memory_human ?? "—" },
          { label: "Ops/s", value: String(s?.redis.data.instantaneous_ops_per_sec ?? "—") },
        ]}
        footer={<QueueBreakdown queueLengths={s?.redis.data.queue_lengths} />}
      />
      <SnapshotCard
        title="Banco de dados"
        icon={<Database size={14} />}
        envelope={s?.db}
        rows={[
          { label: "SELECT 1", value: fmtMs(s?.db.data.select1_ms, 1) },
          { label: "Pool", value: `${s?.db.data.checked_out ?? 0}/${s?.db.data.pool_size ?? 0}` },
          { label: "Overflow", value: String(s?.db.data.overflow ?? 0) },
        ]}
      />
      <SnapshotCard
        title="Score engine (24 h)"
        icon={<Sigma size={14} />}
        envelope={s?.score}
        rows={[
          { label: "Decisões", value: String(s?.score.data.decisions_24h ?? 0) },
          { label: "Taxa ALLOW", value: fmtPct(s?.score.data.allow_rate_24h ?? null) },
          { label: "Score médio", value: s?.score.data.avg_score != null ? s.score.data.avg_score.toFixed(1) : "—" },
          { label: "Última decisão", value: `${fmtAge(s?.score.data.last_decision_age_seconds)} atrás` },
        ]}
      />
      <LatencyCard
        ingestion={s?.ingestion_latency}
        decision={s?.decision_latency}
        processing={s?.processing_latency}
      />
    </div>
  );
}

// ─── Latency triple-card ─────────────────────────────────────────────────────
function LatencyCard({
  ingestion,
  decision,
  processing,
}: {
  ingestion: SnapshotEnvelope<{ delay_seconds?: number | null }> | undefined;
  decision: SnapshotEnvelope<{ p50_ms?: number | null; p95_ms?: number | null; samples_1h?: number }> | undefined;
  processing: SnapshotEnvelope<{ p50_ms?: number | null; p95_ms?: number | null; samples?: number; available?: boolean }> | undefined;
}) {
  const ranks: Record<string, number> = { unknown: 0, ok: 1, degraded: 2, critical: 3 };
  const worst = [ingestion?.status, decision?.status, processing?.status].reduce<string>(
    (acc, s) => (ranks[s ?? "unknown"] > ranks[acc] ? (s ?? "unknown") : acc),
    "unknown",
  );
  const color = statusColor(worst);
  return (
    <div
      className="rounded-2xl p-4 flex flex-col gap-3"
      style={{ background: C.elevated, border: `1px solid ${C.border}` }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Clock size={14} style={{ color: C.textSecondary }} />
          <h4 className="text-[12px] font-semibold uppercase tracking-wide" style={{ color: C.textSecondary }}>
            Latência (3 famílias)
          </h4>
        </div>
        <span
          className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full"
          style={{ background: `${color}22`, color, border: `1px solid ${color}55` }}
        >
          {worst}
        </span>
      </div>
      <div className="flex flex-col gap-2 text-[12px]">
        <LatencyRow
          label="Ingestão (gap)"
          value={fmtAge(ingestion?.data.delay_seconds ?? null)}
          status={ingestion?.status}
        />
        <LatencyRow
          label="Decisão p50/p95"
          value={`${fmtMs(decision?.data.p50_ms)} / ${fmtMs(decision?.data.p95_ms)}`}
          status={decision?.status}
          hint={`${decision?.data.samples_1h ?? 0} amostras`}
        />
        <LatencyRow
          label="Compute p50/p95"
          value={
            processing?.data.available === false
              ? "prom indisponível"
              : `${fmtMs(processing?.data.p50_ms)} / ${fmtMs(processing?.data.p95_ms)}`
          }
          status={processing?.status}
          hint={`${processing?.data.samples ?? 0} amostras`}
        />
      </div>
    </div>
  );
}
function LatencyRow({ label, value, status, hint }: { label: string; value: string; status?: string; hint?: string }) {
  const dot = statusColor(status);
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: dot }} />
        <span style={{ color: C.textTertiary }}>{label}</span>
      </div>
      <span className="tabular-nums" style={{ color: C.textPrimary }}>
        {value}
        {hint && <span className="ml-2" style={{ color: C.textTertiary }}>· {hint}</span>}
      </span>
    </div>
  );
}

// ─── Events history ─────────────────────────────────────────────────────────
function EventsHistoryPanel({ data }: { data: EventsResp | null }) {
  const merged = useMemo(() => {
    if (!data) return [];
    const all: (EventItem & { kind: string })[] = [
      ...(data.alert_history ?? []).map((e) => ({ ...e, kind: e.category ?? "alert" })),
      ...(data.worker_events ?? []).map((e) => ({ ...e, kind: e.category ?? "worker" })),
      ...(data.redis_degradations ?? []).map((e) => ({ ...e, kind: e.category ?? "redis" })),
    ];
    return all.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime()).slice(0, 30);
  }, [data]);
  return (
    <Panel title="Histórico de eventos" icon={<History size={16} />}>
      {merged.length === 0 ? (
        <EmptyState message="Sem eventos registrados nesta sessão." />
      ) : (
        <ul className="flex flex-col gap-1.5">
          {merged.map((e, i) => {
            const recovered = e.code.includes("recovered") || e.code === "worker_online";
            const dotColor = recovered ? C.ok : e.kind === "redis" ? C.warn : C.critical;
            return (
              <li key={`${e.ts}-${e.code}-${i}`} className="flex items-center gap-3 text-[12px] py-1">
                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: dotColor }} />
                <span className="tabular-nums" style={{ color: C.textTertiary }}>{fmtTime(e.ts)}</span>
                <span className="font-mono text-[11px] px-1.5 py-0.5 rounded" style={{ background: C.elevated2, color: C.textSecondary }}>
                  {e.code}
                </span>
                <span className="truncate" style={{ color: C.textPrimary }}>{e.message}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// ─── Analytics panels (lazy section) ────────────────────────────────────────
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
            <Tooltip contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }} labelStyle={{ color: C.textSecondary }} itemStyle={{ color: C.textPrimary }} />
            <Area type="monotone" dataKey="candles" stroke={C.blue} strokeWidth={2} fill="url(#ingestGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}

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
                  {pieData.map((d) => <Cell key={d.name} fill={d.color} stroke="none" />)}
                </Pie>
                <Tooltip contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }} labelStyle={{ color: C.textSecondary }} itemStyle={{ color: C.textPrimary }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dist} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                <XAxis dataKey="bucket" tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: C.textTertiary, fontSize: 11 }} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
                <Tooltip contentStyle={{ background: C.elevated2, border: `1px solid ${C.borderStrong}`, borderRadius: 12 }} labelStyle={{ color: C.textSecondary }} itemStyle={{ color: C.textPrimary }} />
                <Bar dataKey="count" fill={C.purple} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {!empty && reasons.length > 0 && (
        <div className="border-t pt-3" style={{ borderColor: C.border }}>
          <p className="text-[11px] uppercase tracking-wide mb-2" style={{ color: C.textTertiary }}>Top motivos de bloqueio</p>
          <ul className="flex flex-wrap gap-2">
            {reasons.map((r) => (
              <li key={r.reason} className="text-[12px] px-2 py-1 rounded-md" style={{ background: C.elevated2, border: `1px solid ${C.border}`, color: C.textPrimary }}>
                {r.reason} <span style={{ color: C.textTertiary }}>· {r.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

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
                <linearGradient id="pnlUp" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C.ok} stopOpacity={0.30} /><stop offset="95%" stopColor={C.ok} stopOpacity={0} /></linearGradient>
                <linearGradient id="pnlDown" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C.critical} stopOpacity={0.28} /><stop offset="95%" stopColor={C.critical} stopOpacity={0} /></linearGradient>
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
              <Area type="monotone" dataKey="value" stroke={isUp ? C.ok : C.critical} strokeWidth={2} fill={isUp ? "url(#pnlUp)" : "url(#pnlDown)"} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </>
      )}
    </Panel>
  );
}

function SimVsRealPanel({ data }: { data: CompResp | null }) {
  const real = data?.items.find((i) => i.kind === "real");
  const sim = data?.items.find((i) => i.kind === "simulated");
  const Card = ({ title, item, accent }: { title: string; item?: { total: number; win_rate: number | null; avg_pnl_pct: number | null }; accent: string }) => (
    <div className="rounded-xl p-4 flex flex-col gap-2" style={{ background: C.elevated2, border: `1px solid ${C.border}` }}>
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
                const resultColor = r.result === "WIN" ? C.ok : r.result === "LOSS" ? C.critical : C.warn;
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

// ─── Lazy analytics section ──────────────────────────────────────────────────
function AnalyticsSection() {
  // Polling endpoints only mount when the section is expanded — that
  // satisfies the Task #225 invariant that observability panels must not
  // poll legacy /api/dashboard/{decisions,trades,ml-dataset,...} endpoints.
  // These are auxiliary KPI charts the operator can choose to load.
  const ingest    = usePoll<OhlcvRateResp>("/dashboard/ohlcv-rate?minutes=60", 60_000);
  const decisions = usePoll<DecisionsResp>("/dashboard/decisions?hours=24", 60_000);
  const trades    = usePoll<TradesResp>("/dashboard/trades?days=30", 120_000);
  const comp      = usePoll<CompResp>("/dashboard/trade-comparison?days=30", 120_000);
  const ml        = usePoll<MlResp>("/dashboard/ml-dataset?limit=100", 120_000);

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <IngestRateChart data={ingest.data} />
        <DecisionStatsPanel data={decisions.data} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2"><TradePerformancePanel data={trades.data} /></div>
        <SimVsRealPanel data={comp.data} />
      </div>
      <MLDatasetPanel data={ml.data} />
    </div>
  );
}

// ─── Page root ───────────────────────────────────────────────────────────────
export default function PerformanceDashboardPage() {
  // Observability surface: ONLY two endpoints poll continuously, both
  // backed by the OperationalSnapshotService cache so handlers never block
  // on Celery inspect or Redis INFO.
  const overview = usePoll<OverviewResp>("/dashboard/overview", 10_000);
  const events   = usePoll<EventsResp>("/dashboard/events?limit=50", 30_000);

  const [analyticsOpen, setAnalyticsOpen] = useState(false);

  const refreshOps = () => {
    overview.refresh();
    events.refresh();
  };

  return (
    <div className="min-h-screen px-6 py-8 max-w-[1400px] mx-auto" style={{ background: C.surface }}>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: C.textPrimary }}>Centro Operacional</h1>
          <p className="text-[13px] mt-1" style={{ color: C.textSecondary }}>
            Saúde do pipeline e alertas — atualização automática a cada 10 s.
          </p>
        </div>
        <button
          onClick={refreshOps}
          className="flex items-center gap-2 text-[13px] px-3 py-2 rounded-lg transition-colors"
          style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.textPrimary }}
        >
          <RefreshCw size={14} /> Atualizar
        </button>
      </div>

      <div className="flex flex-col gap-5">
        <OperationalBanner data={overview.data} />
        <AlertsPanel data={overview.data} />
        <OpsSnapshotsGrid data={overview.data} />
        <EventsHistoryPanel data={events.data} />

        {/* Lazy analytics section — does not poll until the operator opens it. */}
        <button
          onClick={() => setAnalyticsOpen((v) => !v)}
          className="flex items-center justify-between gap-2 rounded-2xl p-4 text-left transition-colors"
          style={{ background: C.elevated, border: `1px solid ${C.border}`, color: C.textPrimary }}
        >
          <div className="flex items-center gap-2">
            {analyticsOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            <div>
              <p className="text-[14px] font-semibold">Histórico & performance</p>
              <p className="text-[12px]" style={{ color: C.textSecondary }}>
                Gráficos auxiliares (ingestão, decisões, trades, ML dataset) — carregados sob demanda.
              </p>
            </div>
          </div>
          <span className="text-[11px] uppercase tracking-wide" style={{ color: C.textTertiary }}>
            {analyticsOpen ? "ocultar" : "expandir"}
          </span>
        </button>
        {analyticsOpen && <AnalyticsSection />}

        {(overview.error || events.error) && (
          <div className="text-[12px] px-3 py-2 rounded-lg" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)", color: C.critical }}>
            Falha ao buscar dados de observabilidade. Veja o console para detalhes.
          </div>
        )}
      </div>
    </div>
  );
}
