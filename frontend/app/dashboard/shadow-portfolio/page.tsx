"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  Clock,
  Hourglass,
  RefreshCw,
  Target,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";

// ── theme (alinhado com /dashboard/performance) ──────────────────────────────
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
  amber: "#F2A33A",
  purple: "#9D7CF7",
} as const;

// ── domain types (espelham backend/app/schemas/shadow_trade.py) ──────────────
type ShadowStatus = "PENDING" | "RUNNING" | "COMPLETED" | "ERROR";
type ShadowOutcome = "TP_HIT" | "SL_HIT" | "TIMEOUT" | null;

interface ShadowTradeRead {
  id: string;
  symbol: string;
  direction: string | null;
  entry_price: number | null;
  current_price: number | null;
  tp_price: number | null;
  sl_price: number | null;
  amount_usdt: number;
  outcome: ShadowOutcome;
  pnl_pct: number | null;
  pnl_usdt: number | null;
  status: ShadowStatus;
  skip_reason: string | null;
  holding_seconds: number | null;
  created_at: string | null;
  completed_at: string | null;
  entry_timestamp: string | null;
}

interface ShadowTradeListResponse {
  items: ShadowTradeRead[];
  total: number;
  page: number;
  page_size: number;
}

interface ShadowTradeSummary {
  total: number;
  pending: number;
  completed: number;
  win: number;
  loss: number;
  timeout: number;
  win_rate: number;
  total_pnl_usdt: number;
  avg_pnl_pct: number;
  period_start: string | null;
  period_end: string | null;
}

interface ShadowTradeDetail extends ShadowTradeRead {
  strategy: string | null;
  entry_timestamp: string | null;
  exit_price: number | null;
  exit_timestamp: string | null;
  tp_pct: number | null;
  sl_pct: number | null;
  timeout_candles: number | null;
  decision_id: number | null;
  last_processed_time: string | null;
  updated_at: string | null;
  config_snapshot: Record<string, unknown> | null;
  features_snapshot: Record<string, unknown> | null;
  features_snapshot_exit: Record<string, unknown> | null;
  decision_strategy: string | null;
  decision_score: number | null;
  decision_decision: string | null;
  decision_event_type: string | null;
  decision_l1_pass: boolean | null;
  decision_l2_pass: boolean | null;
  decision_l3_pass: boolean | null;
  decision_latency_ms: number | null;
  decision_created_at: string | null;
  decision_reasons: Record<string, unknown> | null;
  decision_metrics: Record<string, unknown> | null;
  // Task #316 — par entry/exit flat para o painel side-by-side.
  // Backend só preenche quando ENABLE_EXIT_METRICS_UI=true.
  entry_metrics: Record<string, unknown> | null;
  exit_metrics: Record<string, unknown> | null;
  // Strategy Lab fields (migration 077)
  profile_id: string | null;
  profile_version: string | null;
  profile_name: string | null;
  strategy_type: string | null;
  rules_snapshot: Record<string, unknown> | null;
  ml_probability: number | null;
  ml_model_id: string | null;
  final_priority_score: number | null;
}

interface ProfileItem {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
}

interface ProfileReportRow {
  profile_id: string;
  profile_name: string;
  total: number;
  open_count: number;
  win_count: number;
  decided_count: number;
  win_rate: number | null;
  pnl_total_usdt: number;
  pnl_avg_pct: number | null;
  avg_holding_win_seconds: number | null;
}

// ── filter shape ─────────────────────────────────────────────────────────────
type StatusFilter = "ALL" | "OPEN" | "TP_HIT" | "SL_HIT" | "TIMEOUT";

interface FilterState {
  status: StatusFilter;
  symbol: string;
  minDate: string; // YYYY-MM-DD
  maxDate: string;
  page: number;
  pageSize: number;
}

const DEFAULT_FILTER: FilterState = {
  status: "ALL",
  symbol: "",
  minDate: "",
  maxDate: "",
  page: 1,
  pageSize: 50,
};

// ── formatters ───────────────────────────────────────────────────────────────
function fmtUsd(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  const abs = Math.abs(n);
  const formatted = abs.toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
  return n < 0 ? `-$${formatted}` : `$${formatted}`;
}

function fmtPct(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(dp)}%`;
}

function fmtPrice(n: number | null | undefined): string {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs === 0) return "$0";
  if (abs < 0.0001) return `$${n.toExponential(2)}`;
  if (abs < 0.01) return `$${n.toFixed(6)}`;
  if (abs < 1) return `$${n.toFixed(4)}`;
  if (abs < 1000) return `$${n.toFixed(2)}`;
  if (abs < 1_000_000) return `$${(n / 1000).toFixed(2)}K`;
  return `$${(n / 1_000_000).toFixed(2)}M`;
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

function fmtHolding(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hours < 24) return `${hours}h ${remMins}m`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return `${days}d ${remHours}h`;
}

// ── status / outcome helpers ─────────────────────────────────────────────────
interface BadgeStyle {
  bg: string;
  fg: string;
  border: string;
  label: string;
}

function statusStyle(status: ShadowStatus): BadgeStyle {
  switch (status) {
    case "PENDING":
      return { bg: `${C.amber}22`, fg: C.amber, border: `${C.amber}55`, label: "Pendente" };
    case "RUNNING":
      return { bg: `${C.blue}22`, fg: C.blue, border: `${C.blue}55`, label: "Em andamento" };
    case "COMPLETED":
      return { bg: `${C.muted}22`, fg: C.text, border: `${C.muted}55`, label: "Finalizado" };
    case "ERROR":
      return { bg: `${C.red}22`, fg: C.red, border: `${C.red}55`, label: "Erro" };
  }
}

function outcomeStyle(outcome: ShadowOutcome): BadgeStyle | null {
  if (!outcome) return null;
  switch (outcome) {
    case "TP_HIT":
      return { bg: `${C.green}22`, fg: C.green, border: `${C.green}55`, label: "TP" };
    case "SL_HIT":
      return { bg: `${C.red}22`, fg: C.red, border: `${C.red}55`, label: "SL" };
    case "TIMEOUT":
      return { bg: `${C.purple}22`, fg: C.purple, border: `${C.purple}55`, label: "Timeout" };
  }
}

function Badge({ style }: { style: BadgeStyle }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        background: style.bg,
        color: style.fg,
        border: `1px solid ${style.border}`,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 10.5,
        fontWeight: 600,
        letterSpacing: 0.4,
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      {style.label}
    </span>
  );
}

// ── stat card (alinhado com /dashboard/performance) ──────────────────────────
function StatCard({
  label,
  value,
  sub,
  accent,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: "green" | "red" | "amber" | "blue" | "purple";
  icon?: React.ReactNode;
}) {
  const accentColor =
    accent === "green"
      ? C.green
      : accent === "red"
      ? C.red
      : accent === "amber"
      ? C.amber
      : accent === "blue"
      ? C.blue
      : accent === "purple"
      ? C.purple
      : C.text;
  return (
    <div
      style={{
        background: C.elevated,
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        padding: "14px 16px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          color: C.muted,
          letterSpacing: 0.6,
          textTransform: "uppercase",
        }}
      >
        {icon}
        <span>{label}</span>
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          color: accentColor,
          marginTop: 6,
        }}
      >
        {value}
      </div>
      {sub ? (
        <div
          style={{
            fontSize: 11,
            color: C.muted,
            fontVariantNumeric: "tabular-nums",
            marginTop: 4,
          }}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
}

// ── filter bar ───────────────────────────────────────────────────────────────
const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: "ALL", label: "Todos" },
  { key: "OPEN", label: "Em aberto" },
  { key: "TP_HIT", label: "TP" },
  { key: "SL_HIT", label: "SL" },
  { key: "TIMEOUT", label: "Timeout" },
];

function FilterBar({
  filter,
  onChange,
  onRefresh,
  loading,
}: {
  filter: FilterState;
  onChange: (next: FilterState) => void;
  onRefresh: () => void;
  loading: boolean;
}) {
  return (
    <div
      style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        padding: "10px 14px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        {STATUS_TABS.map((tab) => {
          const active = filter.status === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => onChange({ ...filter, status: tab.key, page: 1 })}
              style={{
                background: active ? C.elevated : "transparent",
                color: active ? C.text : C.muted,
                border: `1px solid ${active ? C.borderStrong : C.border}`,
                borderRadius: 6,
                padding: "5px 12px",
                fontSize: 11.5,
                cursor: "pointer",
                letterSpacing: 0.4,
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <input
          type="text"
          value={filter.symbol}
          onChange={(e) =>
            onChange({ ...filter, symbol: e.target.value.toUpperCase(), page: 1 })
          }
          placeholder="Símbolo (ex.: BTC_USDT)"
          style={{
            background: C.elevated,
            border: `1px solid ${C.border}`,
            color: C.text,
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11.5,
            width: 170,
            outline: "none",
          }}
        />
        <input
          type="date"
          value={filter.minDate}
          onChange={(e) => onChange({ ...filter, minDate: e.target.value, page: 1 })}
          style={{
            background: C.elevated,
            border: `1px solid ${C.border}`,
            color: C.text,
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11.5,
            outline: "none",
            colorScheme: "dark",
          }}
          title="Data inicial"
        />
        <span style={{ color: C.dim, fontSize: 11 }}>até</span>
        <input
          type="date"
          value={filter.maxDate}
          onChange={(e) => onChange({ ...filter, maxDate: e.target.value, page: 1 })}
          style={{
            background: C.elevated,
            border: `1px solid ${C.border}`,
            color: C.text,
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11.5,
            outline: "none",
            colorScheme: "dark",
          }}
          title="Data final"
        />
        <button
          onClick={onRefresh}
          disabled={loading}
          style={{
            background: C.elevated,
            border: `1px solid ${C.borderStrong}`,
            color: C.text,
            borderRadius: 6,
            padding: "6px 12px",
            fontSize: 11.5,
            cursor: loading ? "default" : "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            opacity: loading ? 0.6 : 1,
          }}
          title="Atualizar"
        >
          <RefreshCw size={13} className={loading ? "spin" : ""} />
          {loading ? "Carregando…" : "Atualizar"}
        </button>
      </div>
      <style jsx>{`
        .spin {
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>
    </div>
  );
}

// ── summary cards ────────────────────────────────────────────────────────────
function SummaryCards({
  data,
  loading,
}: {
  data: ShadowTradeSummary | null;
  loading: boolean;
}) {
  const placeholder = loading || !data;
  const pnlAccent: "green" | "red" =
    data && data.total_pnl_usdt >= 0 ? "green" : "red";
  const avgAccent: "green" | "red" = data && data.avg_pnl_pct >= 0 ? "green" : "red";
  const winRateAccent: "green" | "red" | undefined = !data
    ? undefined
    : data.win_rate >= 50
    ? "green"
    : "red";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
        gap: 12,
      }}
    >
      <StatCard
        label="Total"
        icon={<Activity size={12} />}
        value={placeholder ? "—" : String(data!.total)}
        sub={placeholder ? "" : `${data!.completed} finalizados`}
      />
      <StatCard
        label="Em aberto"
        icon={<Hourglass size={12} />}
        value={placeholder ? "—" : String(data!.pending)}
        accent="amber"
        sub="PENDING + RUNNING"
      />
      <StatCard
        label="Win Rate"
        icon={<Target size={12} />}
        value={placeholder ? "—" : `${data!.win_rate.toFixed(1)}%`}
        accent={winRateAccent}
        sub={
          placeholder
            ? ""
            : `W ${data!.win} · L ${data!.loss} · TO ${data!.timeout}`
        }
      />
      <StatCard
        label="P&L Total"
        icon={
          data && data.total_pnl_usdt >= 0 ? (
            <ArrowUpRight size={12} />
          ) : (
            <ArrowDownRight size={12} />
          )
        }
        value={placeholder ? "—" : fmtUsd(data!.total_pnl_usdt)}
        accent={pnlAccent}
        sub="Soma dos finalizados"
      />
      <StatCard
        label="P&L Médio"
        icon={
          data && data.avg_pnl_pct >= 0 ? (
            <TrendingUp size={12} />
          ) : (
            <TrendingDown size={12} />
          )
        }
        value={placeholder ? "—" : fmtPct(data!.avg_pnl_pct)}
        accent={avgAccent}
        sub="Por trade finalizado"
      />
    </div>
  );
}

// ── trade table ──────────────────────────────────────────────────────────────
const COLS: { key: string; label: string; align?: "left" | "right" | "center" }[] = [
  { key: "created_at", label: "Aberto em" },
  { key: "symbol", label: "Símbolo" },
  { key: "status", label: "Status", align: "center" },
  { key: "entry", label: "Entrada", align: "right" },
  { key: "current", label: "Preço Atual", align: "right" },
  { key: "tp", label: "TP", align: "right" },
  { key: "sl", label: "SL", align: "right" },
  { key: "outcome", label: "Resultado", align: "center" },
  { key: "pnl_pct", label: "P&L %", align: "right" },
  { key: "pnl_usdt", label: "P&L $", align: "right" },
  { key: "holding", label: "Holding", align: "right" },
  { key: "completed_at", label: "Fechado em" },
];

function TradeTable({
  items,
  loading,
  error,
  onRowClick,
}: {
  items: ShadowTradeRead[];
  loading: boolean;
  error: string | null;
  onRowClick: (id: string) => void;
}) {
  // Tick a cada 30s para o "Holding" das operações em aberto avançar
  // visualmente sem precisar de refetch. P&L em aberto também usa esse
  // re-render quando livePrices chegam (via parent).
  const [nowTick, setNowTick] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowTick(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  if (error) {
    return (
      <div
        style={{
          background: C.surface,
          border: `1px solid ${C.red}55`,
          borderRadius: 10,
          padding: 16,
          color: C.red,
          fontSize: 12,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <AlertTriangle size={14} />
        Erro ao carregar shadow trades: {error}
      </div>
    );
  }

  if (loading && items.length === 0) {
    return (
      <div
        style={{
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 10,
          padding: 24,
          color: C.muted,
          fontSize: 12,
          textAlign: "center",
        }}
      >
        Carregando…
      </div>
    );
  }

  if (!loading && items.length === 0) {
    return (
      <div
        style={{
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 10,
          padding: 24,
          color: C.muted,
          fontSize: 12,
          textAlign: "center",
        }}
      >
        Nenhum shadow trade no filtro selecionado.
      </div>
    );
  }

  return (
    <div
      style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        overflow: "auto",
      }}
    >
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 12,
          color: C.text,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <thead>
          <tr style={{ background: C.elevated }}>
            {COLS.map((col) => (
              <th
                key={col.key}
                style={{
                  textAlign: col.align ?? "left",
                  padding: "10px 12px",
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: C.muted,
                  letterSpacing: 0.5,
                  textTransform: "uppercase",
                  borderBottom: `1px solid ${C.border}`,
                  whiteSpace: "nowrap",
                }}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((it) => {
            const sStyle = statusStyle(it.status);
            const oStyle = outcomeStyle(it.outcome);
            // Para trades em aberto (sem pnl_pct persistido), calcula
            // P&L "marked-to-market" usando current_price vs entry_price.
            // Para trades fechados, usa o valor persistido pelo monitor.
            const isOpen = it.status === "PENDING" || it.status === "RUNNING";
            const livePnlPct: number | null =
              isOpen && it.entry_price != null && it.current_price != null && it.entry_price > 0
                ? ((it.current_price - it.entry_price) / it.entry_price) * 100
                : it.pnl_pct;
            const livePnlUsdt: number | null =
              isOpen && livePnlPct != null && it.amount_usdt
                ? (it.amount_usdt * livePnlPct) / 100
                : it.pnl_usdt;
            // Holding "ao vivo" enquanto a operação está em aberto:
            // entry_timestamp → agora. Para fechados, usa holding_seconds
            // persistido (= entry → exit).
            const entryRef = it.entry_timestamp ?? it.created_at;
            let liveHolding: number | null = it.holding_seconds;
            if (isOpen && entryRef) {
              try {
                const entryMs = new Date(entryRef).getTime();
                if (!isNaN(entryMs)) {
                  liveHolding = Math.max(
                    0,
                    Math.floor((nowTick - entryMs) / 1000),
                  );
                }
              } catch {
                /* keep null */
              }
            }
            const pnlPctColor =
              livePnlPct === null
                ? C.dim
                : livePnlPct >= 0
                ? C.green
                : C.red;
            const pnlUsdtColor =
              livePnlUsdt === null
                ? C.dim
                : livePnlUsdt >= 0
                ? C.green
                : C.red;
            return (
              <tr
                key={it.id}
                onClick={() => onRowClick(it.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onRowClick(it.id);
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`Abrir detalhe de ${it.symbol}`}
                style={{
                  borderBottom: `1px solid ${C.border}`,
                  cursor: "pointer",
                  transition: "background 80ms ease",
                  outline: "none",
                }}
                onFocus={(e) =>
                  ((e.currentTarget as HTMLElement).style.background = C.elevated)
                }
                onBlur={(e) =>
                  ((e.currentTarget as HTMLElement).style.background = "transparent")
                }
                onMouseEnter={(e) =>
                  ((e.currentTarget as HTMLElement).style.background = C.elevated)
                }
                onMouseLeave={(e) =>
                  ((e.currentTarget as HTMLElement).style.background = "transparent")
                }
              >
                <td style={{ padding: "10px 12px", whiteSpace: "nowrap", color: C.muted }}>
                  {fmtDateTime(it.created_at)}
                </td>
                <td style={{ padding: "10px 12px", fontWeight: 600 }}>{it.symbol}</td>
                <td style={{ padding: "10px 12px", textAlign: "center" }}>
                  <Badge style={sStyle} />
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right" }}>
                  {fmtPrice(it.entry_price)}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    textAlign: "right",
                    color:
                      it.current_price == null || it.entry_price == null
                        ? C.text
                        : it.current_price >= it.entry_price
                        ? C.green
                        : C.red,
                    fontVariantNumeric: "tabular-nums",
                  }}
                  title={
                    it.current_price != null && it.entry_price != null
                      ? `${(((it.current_price - it.entry_price) / it.entry_price) * 100).toFixed(2)}% vs entrada`
                      : undefined
                  }
                >
                  {fmtPrice(it.current_price)}
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right", color: C.green }}>
                  {fmtPrice(it.tp_price)}
                </td>
                <td style={{ padding: "10px 12px", textAlign: "right", color: C.red }}>
                  {fmtPrice(it.sl_price)}
                </td>
                <td style={{ padding: "10px 12px", textAlign: "center" }}>
                  {oStyle ? <Badge style={oStyle} /> : <span style={{ color: C.dim }}>—</span>}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    textAlign: "right",
                    color: pnlPctColor,
                    fontWeight: 600,
                  }}
                >
                  {fmtPct(livePnlPct)}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    textAlign: "right",
                    color: pnlUsdtColor,
                    fontWeight: 600,
                  }}
                >
                  {fmtUsd(livePnlUsdt)}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    textAlign: "right",
                    whiteSpace: "nowrap",
                    color: it.outcome === "TP_HIT" ? C.green : C.muted,
                    fontVariantNumeric: "tabular-nums",
                  }}
                  title={
                    liveHolding != null
                      ? `${liveHolding}s ${
                          isOpen ? "em aberto" : it.outcome === "TP_HIT" ? "até bater o TP" : "até fechar"
                        }`
                      : undefined
                  }
                >
                  {fmtHolding(liveHolding)}
                </td>
                <td style={{ padding: "10px 12px", whiteSpace: "nowrap", color: C.muted }}>
                  {fmtDateTime(it.completed_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── pagination ───────────────────────────────────────────────────────────────
function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const btn = (label: string, target: number, disabled: boolean) => (
    <button
      onClick={() => !disabled && onChange(target)}
      disabled={disabled}
      style={{
        background: C.elevated,
        border: `1px solid ${C.border}`,
        color: disabled ? C.dim : C.text,
        borderRadius: 6,
        padding: "5px 10px",
        fontSize: 11.5,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 4px",
        fontSize: 11.5,
        color: C.muted,
      }}
    >
      <span>
        {from}–{to} de {total}
      </span>
      <div style={{ display: "flex", gap: 6 }}>
        {btn("«", 1, page === 1)}
        {btn("‹", page - 1, page === 1)}
        <span style={{ alignSelf: "center", padding: "0 6px" }}>
          {page} / {totalPages}
        </span>
        {btn("›", page + 1, page >= totalPages)}
        {btn("»", totalPages, page >= totalPages)}
      </div>
    </div>
  );
}

// ── detail modal ─────────────────────────────────────────────────────────────
function DetailRow({
  label,
  value,
  color,
}: {
  label: string;
  value: React.ReactNode;
  color?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "8px 0",
        borderBottom: `1px solid ${C.border}`,
        fontSize: 12,
      }}
    >
      <span style={{ color: C.muted }}>{label}</span>
      <span
        style={{
          color: color ?? C.text,
          fontWeight: 500,
          fontVariantNumeric: "tabular-nums",
          textAlign: "right",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function DecisionAuditBlock({ data }: { data: ShadowTradeDetail }) {
  const reasons = data.decision_reasons
    ? Object.entries(data.decision_reasons)
    : [];
  const metrics = data.decision_metrics
    ? Object.entries(data.decision_metrics)
    : [];
  const hasAny =
    data.decision_id !== null ||
    reasons.length > 0 ||
    metrics.length > 0 ||
    data.decision_l1_pass !== null ||
    data.decision_l2_pass !== null ||
    data.decision_l3_pass !== null;

  if (!hasAny) {
    return null;
  }

  const passColor = (v: boolean | null) =>
    v === null ? C.dim : v ? C.green : C.red;
  const passLabel = (v: boolean | null) =>
    v === null ? "—" : v ? "PASS" : "FAIL";

  const fmtMetric = (v: unknown): string => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
      if (!Number.isFinite(v)) return String(v);
      return Number.isInteger(v) ? String(v) : v.toFixed(2);
    }
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "object") {
      const obj = v as Record<string, unknown>;
      if ("value" in obj && typeof obj.value !== "object") {
        return fmtMetric(obj.value);
      }
      return JSON.stringify(v);
    }
    return String(v);
  };

  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: C.muted,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        Auditoria da Decisão (L1/L2/L3)
        {data.decision_strategy ? (
          <span
            style={{
              fontFamily: "monospace",
              fontSize: 10,
              color: C.text,
              background: C.bg,
              border: `1px solid ${C.border}`,
              borderRadius: 4,
              padding: "1px 6px",
              letterSpacing: 0,
              textTransform: "none",
            }}
          >
            {data.decision_strategy}
          </span>
        ) : null}
        {data.decision_score !== null ? (
          <span
            style={{
              fontFamily: "monospace",
              fontSize: 10,
              color: C.text,
              letterSpacing: 0,
              textTransform: "none",
            }}
          >
            score {data.decision_score.toFixed(1)}
          </span>
        ) : null}
        {data.decision_created_at ? (
          <span
            style={{
              fontSize: 10,
              color: C.dim,
              letterSpacing: 0,
              textTransform: "none",
            }}
          >
            • {fmtDateTime(data.decision_created_at)}
          </span>
        ) : null}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 12,
        }}
      >
        {/* Reasons */}
        <div
          style={{
            background: C.bg,
            border: `1px solid ${C.border}`,
            borderRadius: 6,
            padding: 10,
            maxHeight: 260,
            overflow: "auto",
          }}
        >
          <div
            style={{
              fontSize: 10,
              color: C.dim,
              textTransform: "uppercase",
              letterSpacing: 0.6,
              marginBottom: 6,
            }}
          >
            Reasons
          </div>
          {reasons.length === 0 ? (
            <div style={{ fontSize: 11.5, color: C.dim }}>—</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {reasons.map(([k, v]) => {
                const label = String(v);
                const ok = label.toUpperCase() === "OK";
                return (
                  <span
                    key={k}
                    style={{
                      fontSize: 10.5,
                      fontFamily: "monospace",
                      padding: "2px 6px",
                      borderRadius: 4,
                      border: `1px solid ${ok ? C.green : C.red}`,
                      color: ok ? C.green : C.red,
                      background: "transparent",
                    }}
                  >
                    {k}: {label}
                  </span>
                );
              })}
            </div>
          )}
        </div>

        {/* Metrics */}
        <div
          style={{
            background: C.bg,
            border: `1px solid ${C.border}`,
            borderRadius: 6,
            padding: 10,
            maxHeight: 260,
            overflow: "auto",
          }}
        >
          <div
            style={{
              fontSize: 10,
              color: C.dim,
              textTransform: "uppercase",
              letterSpacing: 0.6,
              marginBottom: 6,
            }}
          >
            Metrics
          </div>
          {metrics.length === 0 ? (
            <div style={{ fontSize: 11.5, color: C.dim }}>—</div>
          ) : (
            metrics
              .sort((a, b) => a[0].localeCompare(b[0]))
              .map(([k, v]) => (
                <div
                  key={k}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 12,
                    fontSize: 11,
                    padding: "2px 0",
                  }}
                >
                  <span style={{ color: C.muted }}>{k}</span>
                  <span
                    style={{
                      color: C.text,
                      fontFamily: "monospace",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {fmtMetric(v)}
                  </span>
                </div>
              ))
          )}
        </div>

        {/* Timeline */}
        <div
          style={{
            background: C.bg,
            border: `1px solid ${C.border}`,
            borderRadius: 6,
            padding: 10,
          }}
        >
          <div
            style={{
              fontSize: 10,
              color: C.dim,
              textTransform: "uppercase",
              letterSpacing: 0.6,
              marginBottom: 6,
            }}
          >
            Timeline
          </div>
          {(
            [
              ["L1", data.decision_l1_pass],
              ["L2", data.decision_l2_pass],
              ["L3", data.decision_l3_pass],
            ] as const
          ).map(([label, passed]) => (
            <div
              key={label}
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 11,
                padding: "3px 0",
              }}
            >
              <span style={{ fontFamily: "monospace", color: C.text }}>
                {label}
              </span>
              <span style={{ color: passColor(passed) }}>
                {passLabel(passed)}
              </span>
            </div>
          ))}
          <div
            style={{
              fontSize: 10.5,
              color: C.muted,
              marginTop: 6,
              borderTop: `1px solid ${C.border}`,
              paddingTop: 6,
            }}
          >
            Latency total:{" "}
            <span
              style={{ fontFamily: "monospace", color: C.text }}
            >
              {data.decision_latency_ms ?? 0}ms
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// Task #312: a partir desta data, TODO shadow fechado deve ter
// ``features_snapshot_exit`` preenchido (snapshot real OU marcador
// ``{"_capture_failed": True, ...}``) graças à fortificação do
// ``_capture_exit_features`` (helper agora nunca propaga exceção e
// nunca deixa NULL) + split de transações em ``_record_simulation_one_async``.
// Para trades fechados ANTES desta data, manter a mensagem antiga
// ("antes da Task #306") como fallback. Para trades posteriores com
// NULL, mostrar mensagem técnica que sinaliza regressão imediata —
// não mascarar como "trade antigo".
const SHADOW_EXIT_CAPTURE_INVARIANT_SINCE = new Date(
  "2026-05-20T00:00:00Z",
);

function exitSnapshotEmptyMessage(data: ShadowTradeDetail): string {
  if (data.status !== "COMPLETED") {
    return "Trade ainda em aberto — snapshot da saída será capturado quando TP/SL/timeout for atingido.";
  }
  const exit = data.features_snapshot_exit as Record<string, unknown> | null;
  if (exit && exit["_capture_failed"] === true) {
    return "Snapshot indisponível no fechamento — indicadores estavam stale ou ausentes no provider.";
  }
  // features_snapshot_exit === null neste branch
  const closedAt = data.completed_at ? new Date(data.completed_at) : null;
  if (closedAt && closedAt >= SHADOW_EXIT_CAPTURE_INVARIANT_SINCE) {
    return "Snapshot ausente — captura não chegou a executar (regressão pós-Task #312). Abrir ticket.";
  }
  return "Snapshot ainda não capturado para este trade (fechado antes da Task #306).";
}

function SnapshotBlock({
  title,
  data,
  emptyMessage,
}: {
  title: string;
  data: Record<string, unknown> | null;
  emptyMessage?: string;
}) {
  // Task #306: o capture de saída pode gravar um marcador
  // `{"_capture_failed": true, "_reason": "..."}` quando o provider de
  // indicadores estava sem dados no instante do fechamento. Tratamos
  // esse caso como "sem dados informativos" (não renderiza as duas
  // chaves técnicas) e exibe a mensagem contextual.
  const captureFailed =
    !!data && typeof data === "object" && data["_capture_failed"] === true;
  const isEmpty = !data || Object.keys(data).length === 0 || captureFailed;
  if (isEmpty) {
    return (
      <div>
        <div
          style={{
            fontSize: 11,
            color: C.muted,
            letterSpacing: 0.6,
            textTransform: "uppercase",
            marginBottom: 8,
          }}
        >
          {title}
        </div>
        <div
          style={{
            fontSize: 11.5,
            color: C.dim,
            background: C.bg,
            border: `1px solid ${C.border}`,
            borderRadius: 6,
            padding: 10,
          }}
        >
          {emptyMessage ?? "Sem dados."}
        </div>
      </div>
    );
  }
  const entries = Object.entries(data).sort((a, b) => a[0].localeCompare(b[0]));
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: C.muted,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          marginBottom: 8,
        }}
      >
        {title}
      </div>
      <div
        style={{
          background: C.bg,
          border: `1px solid ${C.border}`,
          borderRadius: 6,
          padding: "8px 12px",
          maxHeight: 240,
          overflow: "auto",
        }}
      >
        {entries.map(([k, v]) => {
          let display: string;
          if (v === null || v === undefined) {
            display = "—";
          } else if (typeof v === "number") {
            display = Number.isInteger(v) ? String(v) : v.toFixed(4);
          } else if (typeof v === "boolean") {
            display = v ? "true" : "false";
          } else if (typeof v === "object") {
            display = JSON.stringify(v);
          } else {
            display = String(v);
          }
          return (
            <div
              key={k}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                fontSize: 11.5,
                padding: "4px 0",
                borderBottom: `1px solid ${C.border}`,
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
              }}
            >
              <span style={{ color: C.muted }}>{k}</span>
              <span
                style={{
                  color: C.text,
                  textAlign: "right",
                  wordBreak: "break-all",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {display}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Task #316 — Painel comparativo Entry | Exit lado-a-lado com delta.
//
// Catálogo dinâmico: união ordenada das chaves de ``entry`` ∪ ``exit``
// (descartando chaves internas começadas em ``_`` — _capture_error,
// _capture_failed, etc.). Sem nenhuma lista hardcoded de indicadores
// (runbook §4.1). Renderiza linhas com Δ absoluto e %; sem comparação
// quando algum lado é não-numérico ou ausente.
const _INTERNAL_PREFIX = "_";

function _isScalarNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function _formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(4);
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function _formatDelta(entry: unknown, exit_: unknown): {
  abs: string;
  pct: string;
  color: string;
} {
  if (!_isScalarNumber(entry) || !_isScalarNumber(exit_)) {
    return { abs: "—", pct: "—", color: C.dim };
  }
  const delta = exit_ - entry;
  const pct = entry !== 0 ? (delta / Math.abs(entry)) * 100 : 0;
  const color = delta > 0 ? C.green : delta < 0 ? C.red : C.muted;
  const absStr =
    (delta >= 0 ? "+" : "") +
    (Number.isInteger(delta) ? String(delta) : delta.toFixed(4));
  const pctStr =
    entry !== 0
      ? (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%"
      : "—";
  return { abs: absStr, pct: pctStr, color };
}

function EntryExitCompareBlock({
  entry,
  exit,
  exitEmptyMessage,
}: {
  entry: Record<string, unknown> | null;
  exit: Record<string, unknown> | null;
  exitEmptyMessage: string;
}) {
  const entryObj = entry ?? {};
  const exitObj = exit ?? {};
  const allKeys = Array.from(
    new Set([...Object.keys(entryObj), ...Object.keys(exitObj)]),
  )
    .filter((k) => !k.startsWith(_INTERNAL_PREFIX))
    .sort((a, b) => a.localeCompare(b));

  const hasEntry = entry !== null && Object.keys(entryObj).length > 0;
  const hasExit = exit !== null && Object.keys(exitObj).length > 0;

  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: C.muted,
          letterSpacing: 0.6,
          textTransform: "uppercase",
          marginBottom: 8,
        }}
      >
        Comparativo Entry | Exit (Δ)
      </div>
      <div
        style={{
          background: C.bg,
          border: `1px solid ${C.border}`,
          borderRadius: 6,
          padding: "8px 12px",
          maxHeight: 320,
          overflow: "auto",
        }}
      >
        {allKeys.length === 0 ? (
          <div style={{ fontSize: 11.5, color: C.dim }}>
            {hasEntry || hasExit
              ? "Sem indicadores comparáveis."
              : exitEmptyMessage}
          </div>
        ) : (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1.4fr 1fr 1fr 1fr",
                gap: 8,
                fontSize: 10.5,
                color: C.muted,
                paddingBottom: 6,
                borderBottom: `1px solid ${C.border}`,
                letterSpacing: 0.4,
                textTransform: "uppercase",
              }}
            >
              <span>Indicador</span>
              <span style={{ textAlign: "right" }}>Entry</span>
              <span style={{ textAlign: "right" }}>Exit</span>
              <span style={{ textAlign: "right" }}>Δ</span>
            </div>
            {allKeys.map((k) => {
              const e = entryObj[k];
              const x = exitObj[k];
              const delta = _formatDelta(e, x);
              return (
                <div
                  key={k}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1.4fr 1fr 1fr 1fr",
                    gap: 8,
                    fontSize: 11.5,
                    padding: "4px 0",
                    borderBottom: `1px solid ${C.border}`,
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <span style={{ color: C.muted }}>{k}</span>
                  <span style={{ color: C.text, textAlign: "right" }}>
                    {_formatValue(e)}
                  </span>
                  <span style={{ color: C.text, textAlign: "right" }}>
                    {_formatValue(x)}
                  </span>
                  <span
                    style={{
                      color: delta.color,
                      textAlign: "right",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {delta.abs}
                    {delta.pct !== "—" ? (
                      <span style={{ color: C.dim, marginLeft: 6 }}>
                        ({delta.pct})
                      </span>
                    ) : null}
                  </span>
                </div>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}

function DetailModal({
  shadowId,
  onClose,
}: {
  shadowId: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<ShadowTradeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiGet<ShadowTradeDetail>(`/api/shadow-trades/${shadowId}`)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) {
          const msg =
            err instanceof ApiError
              ? err.toDescriptiveString()
              : err?.message ?? "Erro desconhecido";
          setError(msg);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [shadowId]);

  // Close on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      role="presentation"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(6,7,11,0.65)",
        backdropFilter: "blur(4px)",
        zIndex: 100,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Detalhe do shadow trade"
        style={{
          background: C.surface,
          border: `1px solid ${C.borderStrong}`,
          borderRadius: 12,
          width: "min(720px, 100%)",
          maxHeight: "90vh",
          overflow: "auto",
          boxShadow: "0 24px 64px rgba(0,0,0,0.5)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "14px 18px",
            borderBottom: `1px solid ${C.border}`,
            position: "sticky",
            top: 0,
            background: C.surface,
            zIndex: 1,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {data ? data.symbol : "Carregando…"}
            </div>
            {data ? <Badge style={statusStyle(data.status)} /> : null}
            {data && outcomeStyle(data.outcome) ? (
              <Badge style={outcomeStyle(data.outcome)!} />
            ) : null}
          </div>
          <button
            onClick={onClose}
            aria-label="Fechar"
            style={{
              background: "transparent",
              border: "none",
              color: C.muted,
              cursor: "pointer",
              padding: 4,
              display: "flex",
            }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 18 }}>
          {error ? (
            <div
              style={{
                color: C.red,
                fontSize: 12,
                display: "flex",
                gap: 8,
                alignItems: "center",
              }}
            >
              <AlertTriangle size={14} /> {error}
            </div>
          ) : loading || !data ? (
            <div style={{ color: C.muted, fontSize: 12 }}>Carregando detalhes…</div>
          ) : (
            <div style={{ display: "grid", gap: 16 }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 18,
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: 11,
                      color: C.muted,
                      letterSpacing: 0.6,
                      textTransform: "uppercase",
                      marginBottom: 4,
                    }}
                  >
                    Trade
                  </div>
                  <DetailRow
                    label="Direção"
                    value={data.direction ?? "—"}
                  />
                  <DetailRow label="Estratégia" value={data.strategy ?? "—"} />
                  <DetailRow
                    label="Aberto em"
                    value={fmtDateTime(data.entry_timestamp ?? data.created_at)}
                  />
                  <DetailRow
                    label="Fechado em"
                    value={fmtDateTime(data.exit_timestamp ?? data.completed_at)}
                  />
                  <DetailRow
                    label="Tempo em posição"
                    value={fmtHolding(data.holding_seconds)}
                  />
                  <DetailRow
                    label="Decision ID"
                    value={data.decision_id ?? "—"}
                  />
                  {data.profile_name != null && (
                    <DetailRow
                      label="Profile (Lab)"
                      value={data.profile_name}
                      color={C.purple}
                    />
                  )}
                  {data.ml_probability != null && (
                    <DetailRow
                      label="ML Probability"
                      value={`${(data.ml_probability * 100).toFixed(1)}%`}
                      color={data.ml_probability >= 0.5 ? C.green : C.amber}
                    />
                  )}
                  {data.final_priority_score != null && (
                    <DetailRow
                      label="Priority Score"
                      value={data.final_priority_score.toFixed(3)}
                    />
                  )}
                  {data.profile_version != null && (
                    <DetailRow
                      label="Profile Version"
                      value={fmtDateTime(data.profile_version)}
                    />
                  )}
                </div>

                <div>
                  <div
                    style={{
                      fontSize: 11,
                      color: C.muted,
                      letterSpacing: 0.6,
                      textTransform: "uppercase",
                      marginBottom: 4,
                    }}
                  >
                    Preços
                  </div>
                  <DetailRow
                    label="Entrada"
                    value={fmtPrice(data.entry_price)}
                  />
                  <DetailRow
                    label="Take Profit"
                    value={`${fmtPrice(data.tp_price)} (${fmtPct(data.tp_pct)})`}
                    color={C.green}
                  />
                  <DetailRow
                    label="Stop Loss"
                    value={`${fmtPrice(data.sl_price)} (${fmtPct(data.sl_pct)})`}
                    color={C.red}
                  />
                  <DetailRow
                    label="Saída"
                    value={fmtPrice(data.exit_price)}
                  />
                  <DetailRow
                    label="Tamanho"
                    value={fmtUsd(data.amount_usdt)}
                  />
                  <DetailRow
                    label="P&L"
                    value={`${fmtPct(data.pnl_pct)} (${fmtUsd(data.pnl_usdt)})`}
                    color={
                      data.pnl_usdt === null
                        ? undefined
                        : data.pnl_usdt >= 0
                        ? C.green
                        : C.red
                    }
                  />
                </div>
              </div>

              <DecisionAuditBlock data={data} />

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: 16,
                }}
              >
                <SnapshotBlock title="Config Snapshot" data={data.config_snapshot} />
                <SnapshotBlock
                  title="Indicadores na ENTRADA"
                  data={data.features_snapshot}
                />
                <SnapshotBlock
                  title="Indicadores na SAÍDA"
                  data={data.features_snapshot_exit}
                  emptyMessage={exitSnapshotEmptyMessage(data)}
                />
              </div>

              {/*
                Task #316 — Painel comparativo Entry | Exit com deltas.
                Só aparece quando o backend devolve os pares (flag
                ENABLE_EXIT_METRICS_UI=true). União das chaves: catálogo
                dinâmico, ZERO hardcode (runbook §4.1).
              */}
              {data.entry_metrics || data.exit_metrics ? (
                <EntryExitCompareBlock
                  entry={data.entry_metrics}
                  exit={data.exit_metrics}
                  exitEmptyMessage={exitSnapshotEmptyMessage(data)}
                />
              ) : null}

              <div
                style={{
                  fontSize: 10.5,
                  color: C.dim,
                  display: "flex",
                  gap: 12,
                  flexWrap: "wrap",
                  paddingTop: 4,
                }}
              >
                <span>
                  <Clock size={10} style={{ verticalAlign: "middle", marginRight: 4 }} />
                  Última candle processada: {fmtDateTime(data.last_processed_time)}
                </span>
                <span>Atualizado: {fmtDateTime(data.updated_at)}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── profile report table ─────────────────────────────────────────────────────
type ReportSortKey =
  | "profile_name"
  | "total"
  | "open_count"
  | "win_rate"
  | "pnl_total_usdt"
  | "pnl_avg_pct"
  | "avg_holding_win_seconds";

function ProfileReportTable({
  rows,
  loading,
}: {
  rows: ProfileReportRow[];
  loading: boolean;
}) {
  const [sortKey, setSortKey] = useState<ReportSortKey>("profile_name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const av = a[sortKey] ?? (sortDir === "asc" ? Infinity : -Infinity);
      const bv = b[sortKey] ?? (sortDir === "asc" ? Infinity : -Infinity);
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortDir === "asc"
        ? (av as number) - (bv as number)
        : (bv as number) - (av as number);
    });
  }, [rows, sortKey, sortDir]);

  const handleSort = (key: ReportSortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const SortBtn = ({ col }: { col: ReportSortKey }) => {
    const active = sortKey === col;
    return (
      <button
        onClick={() => handleSort(col)}
        style={{
          background: "transparent",
          border: "none",
          cursor: "pointer",
          padding: "0 2px",
          color: active ? C.blue : C.dim,
          verticalAlign: "middle",
          lineHeight: 0,
        }}
      >
        {active && sortDir === "asc" ? (
          <ChevronUp size={12} />
        ) : active && sortDir === "desc" ? (
          <ChevronDown size={12} />
        ) : (
          <ChevronDown size={12} style={{ opacity: 0.35 }} />
        )}
      </button>
    );
  };

  const thStyle: React.CSSProperties = {
    padding: "8px 12px",
    fontSize: 11,
    color: C.muted,
    fontWeight: 500,
    textAlign: "left",
    whiteSpace: "nowrap",
    borderBottom: `1px solid ${C.border}`,
    background: C.elevated,
  };
  const tdStyle: React.CSSProperties = {
    padding: "9px 12px",
    fontSize: 12,
    borderBottom: `1px solid ${C.border}`,
    color: C.text,
  };

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: C.muted, fontSize: 12 }}>
        Carregando relatório...
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div style={{ padding: 24, textAlign: "center", color: C.muted, fontSize: 12 }}>
        Nenhum perfil com shadow trades encontrado.
      </div>
    );
  }

  return (
    <div
      style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        overflow: "hidden",
      }}
    >
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={thStyle}>
              Perfil <SortBtn col="profile_name" />
            </th>
            <th style={{ ...thStyle, textAlign: "right" }}>
              Total <SortBtn col="total" />
            </th>
            <th style={{ ...thStyle, textAlign: "right" }}>
              Em aberto <SortBtn col="open_count" />
            </th>
            <th style={{ ...thStyle, textAlign: "right" }}>
              Win Rate <SortBtn col="win_rate" />
            </th>
            <th style={{ ...thStyle, textAlign: "right" }}>
              P&amp;L Total <SortBtn col="pnl_total_usdt" />
            </th>
            <th style={{ ...thStyle, textAlign: "right" }}>
              P&amp;L Médio <SortBtn col="pnl_avg_pct" />
            </th>
            <th style={{ ...thStyle, textAlign: "right" }}>
              Holding (WIN) <SortBtn col="avg_holding_win_seconds" />
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const winRateColor =
              row.win_rate === null
                ? C.muted
                : row.win_rate >= 50
                ? C.green
                : C.red;
            const pnlColor =
              row.pnl_total_usdt > 0 ? C.green : row.pnl_total_usdt < 0 ? C.red : C.muted;
            const pnlAvgColor =
              row.pnl_avg_pct === null
                ? C.muted
                : row.pnl_avg_pct > 0
                ? C.green
                : row.pnl_avg_pct < 0
                ? C.red
                : C.muted;
            return (
              <tr
                key={row.profile_id}
                style={{ background: i % 2 === 0 ? "transparent" : `${C.elevated}66` }}
              >
                <td style={tdStyle}>{row.profile_name}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{row.total}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{row.open_count}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: winRateColor }}>
                  {row.win_rate !== null ? `${row.win_rate.toFixed(1)}%` : "—"}
                </td>
                <td style={{ ...tdStyle, textAlign: "right", color: pnlColor }}>
                  {fmtUsd(row.pnl_total_usdt)}
                </td>
                <td style={{ ...tdStyle, textAlign: "right", color: pnlAvgColor }}>
                  {row.pnl_avg_pct !== null ? fmtPct(row.pnl_avg_pct) : "—"}
                </td>
                <td style={{ ...tdStyle, textAlign: "right", color: C.muted }}>
                  {fmtHolding(row.avg_holding_win_seconds)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── data hooks ───────────────────────────────────────────────────────────────
//
// Backend limitation: `status` query param só aceita PENDING|RUNNING|
// COMPLETED|ERROR. Não há filtro nativo por `outcome` (TP_HIT/SL_HIT/TIMEOUT).
// Estratégia adotada (sem mudar backend):
//
// • ALL          → 1 GET paginado server-side (sem filtro de status).
// • OPEN         → 2 GETs (status=PENDING + status=RUNNING) com page_size=MAX,
//                  merge + sort por created_at desc, paginação client-side.
// • TP/SL/TIMEOUT → 1 GET (status=COMPLETED) com page_size=MAX e filtro
//                   client-side por `outcome`, paginação client-side.
//
// MAX_LOCAL_FETCH é cap de segurança — assumimos que volumes shadow por
// usuário cabem nessa janela. Se algum dia atingir, o backend ganha um
// param `outcome` e a UI volta a paginar 100% server-side.
const MAX_LOCAL_FETCH = 200; // = _MAX_PAGE_SIZE em backend/app/api/shadow_trades.py
const CLIENT_PAGE_SIZE = 50;

type SourceTab = "L3" | "L3_REJECTED" | "L3_SIMULATED" | "L1_SPECTRUM";

function buildBaseQuery(
  filter: FilterState,
  overrides: { status?: string; page?: number; page_size?: number } = {},
  source?: SourceTab,
  profileId?: string | null,
): string {
  const params = new URLSearchParams();
  if (overrides.status) params.set("status", overrides.status);
  if (filter.symbol.trim()) params.set("symbol", filter.symbol.trim());
  if (filter.minDate) params.set("min_date", filter.minDate);
  if (filter.maxDate) params.set("max_date", filter.maxDate);
  // Strategy Lab profiles store shadows with source='L3_LAB'; override when profile selected.
  const effectiveSource = profileId ? "L3_LAB" : source;
  if (effectiveSource) params.set("source", effectiveSource);
  if (profileId) params.set("profile_id", profileId);
  params.set("page", String(overrides.page ?? filter.page));
  params.set("page_size", String(overrides.page_size ?? filter.pageSize));
  return params.toString();
}

function buildSummaryQuery(filter: FilterState, source?: SourceTab, profileId?: string | null): string {
  const params = new URLSearchParams();
  if (filter.symbol.trim()) params.set("symbol", filter.symbol.trim());
  if (filter.minDate) params.set("min_date", filter.minDate);
  if (filter.maxDate) params.set("max_date", filter.maxDate);
  // Strategy Lab profiles store shadows with source='L3_LAB'; override when profile selected.
  const effectiveSource = profileId ? "L3_LAB" : source;
  if (effectiveSource) params.set("source", effectiveSource);
  if (profileId) params.set("profile_id", profileId);
  return params.toString();
}

// ── page ─────────────────────────────────────────────────────────────────────
export default function ShadowPortfolioPage() {
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER);
  const [list, setList] = useState<ShadowTradeListResponse | null>(null);
  const [summary, setSummary] = useState<ShadowTradeSummary | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [errorList, setErrorList] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const [sourceTab, setSourceTab] = useState<SourceTab>("L3");
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [mainTab, setMainTab] = useState<"trades" | "report">("trades");
  const [profileReport, setProfileReport] = useState<ProfileReportRow[]>([]);
  const [loadingReport, setLoadingReport] = useState(false);

  // Fetch profiles on mount for Strategy Lab profile selector
  useEffect(() => {
    apiGet<{ items?: ProfileItem[]; profiles?: ProfileItem[] } | ProfileItem[]>("/api/profiles/")
      .then((res) => {
        let items: ProfileItem[] = [];
        if (Array.isArray(res)) {
          items = res;
        } else if (res && typeof res === "object") {
          items = (res as { items?: ProfileItem[]; profiles?: ProfileItem[] }).items
            ?? (res as { profiles?: ProfileItem[] }).profiles
            ?? [];
        }
        setProfiles(items.filter((p) => p.is_active));
      })
      .catch(() => {
        // Silently fail — profile selector is optional
        setProfiles([]);
      });
  }, []);

  useEffect(() => {
    if (mainTab !== "report") return;
    setLoadingReport(true);
    apiGet<ProfileReportRow[]>("/api/shadow-trades/profile-report")
      .then(setProfileReport)
      .catch(() => setProfileReport([]))
      .finally(() => setLoadingReport(false));
  }, [mainTab, tick]);

  const fetchList = useCallback(() => {
    setLoadingList(true);
    setErrorList(null);

    const handleError = (err: unknown) => {
      const msg =
        err instanceof ApiError
          ? err.toDescriptiveString()
          : err instanceof Error
          ? err.message
          : "Erro desconhecido";
      setErrorList(msg);
      setList({
        items: [],
        total: 0,
        page: filter.page,
        page_size: filter.pageSize,
      });
    };

    if (filter.status === "ALL") {
      // Server-side pagination puro.
      const qs = buildBaseQuery(filter, {}, sourceTab, selectedProfileId);
      apiGet<ShadowTradeListResponse>(`/api/shadow-trades?${qs}`)
        .then(setList)
        .catch(handleError)
        .finally(() => setLoadingList(false));
      return;
    }

    if (filter.status === "OPEN") {
      // 2 fetches paralelos (PENDING + RUNNING) e merge — backend não
      // suporta `IN (...)` em status.
      const qsPending = buildBaseQuery(filter, {
        status: "PENDING",
        page: 1,
        page_size: MAX_LOCAL_FETCH,
      }, sourceTab, selectedProfileId);
      const qsRunning = buildBaseQuery(filter, {
        status: "RUNNING",
        page: 1,
        page_size: MAX_LOCAL_FETCH,
      }, sourceTab, selectedProfileId);
      Promise.all([
        apiGet<ShadowTradeListResponse>(`/api/shadow-trades?${qsPending}`),
        apiGet<ShadowTradeListResponse>(`/api/shadow-trades?${qsRunning}`),
      ])
        .then(([pending, running]) => {
          const merged = [...pending.items, ...running.items].sort((a, b) => {
            const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
            const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
            return tb - ta;
          });
          setList({
            items: merged,
            total: pending.total + running.total,
            page: 1,
            page_size: merged.length,
          });
        })
        .catch(handleError)
        .finally(() => setLoadingList(false));
      return;
    }

    // TP_HIT / SL_HIT / TIMEOUT: 1 fetch COMPLETED + filtro local por outcome.
    const qsCompleted = buildBaseQuery(filter, {
      status: "COMPLETED",
      page: 1,
      page_size: MAX_LOCAL_FETCH,
    }, sourceTab, selectedProfileId);
    apiGet<ShadowTradeListResponse>(`/api/shadow-trades?${qsCompleted}`)
      .then((res) => setList(res))
      .catch(handleError)
      .finally(() => setLoadingList(false));
  }, [filter, sourceTab, selectedProfileId]);

  const fetchSummary = useCallback(() => {
    setLoadingSummary(true);
    const qs = buildSummaryQuery(filter, sourceTab, selectedProfileId);
    apiGet<ShadowTradeSummary>(
      qs ? `/api/shadow-trades/summary?${qs}` : `/api/shadow-trades/summary`
    )
      .then((res) => setSummary(res))
      .catch(() => setSummary(null))
      .finally(() => setLoadingSummary(false));
  }, [filter, sourceTab, selectedProfileId]);

  useEffect(() => {
    fetchList();
    fetchSummary();
  }, [fetchList, fetchSummary, tick]);

  // Polling leve de preços correntes a cada 15s, só com símbolos visíveis,
  // sem repaginar a lista. Pausa quando a aba do navegador está oculta.
  useEffect(() => {
    if (!list || list.items.length === 0) return;
    const symbols = Array.from(new Set(list.items.map((it) => it.symbol)));
    if (symbols.length === 0) return;

    let cancelled = false;
    const fetchPrices = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      const qs = new URLSearchParams({ symbols: symbols.join(",") }).toString();
      apiGet<{ prices: Record<string, number>; fetched_at: string }>(
        `/api/shadow-trades/prices?${qs}`,
      )
        .then((res) => {
          if (cancelled) return;
          setLivePrices((prev) => ({ ...prev, ...res.prices }));
        })
        .catch(() => {
          // silencioso — preços continuam exibindo o último valor conhecido
        });
    };
    fetchPrices();
    const interval = setInterval(fetchPrices, 15_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [list]);

  // Mescla livePrices em cima do current_price vindo do backend.
  const itemsWithLivePrices = useMemo(() => {
    if (!list) return [] as ShadowTradeRead[];
    if (Object.keys(livePrices).length === 0) return list.items;
    return list.items.map((it) => {
      const live = livePrices[it.symbol];
      if (live === undefined) return it;
      return { ...it, current_price: live };
    });
  }, [list, livePrices]);

  // Quando status != ALL, paginamos client-side sobre o conjunto local
  // (já filtrado/agregado em fetchList). Ao mudar de aba zeramos a página.
  const filteredAll = useMemo(() => {
    if (!list) return [];
    if (filter.status === "ALL") return itemsWithLivePrices;
    if (filter.status === "OPEN") {
      // fetchList já mergeou PENDING+RUNNING e ordenou desc.
      return itemsWithLivePrices;
    }
    // TP_HIT/SL_HIT/TIMEOUT — itemsWithLivePrices aqui é a janela COMPLETED.
    return itemsWithLivePrices.filter((it) => it.outcome === filter.status);
  }, [list, itemsWithLivePrices, filter.status]);

  const isClientPaginated = filter.status !== "ALL";
  const clientTotal = filteredAll.length;
  const clientPageStart = (filter.page - 1) * CLIENT_PAGE_SIZE;
  const displayItems = isClientPaginated
    ? filteredAll.slice(clientPageStart, clientPageStart + CLIENT_PAGE_SIZE)
    : filteredAll;

  // O fetch local é capado em MAX_LOCAL_FETCH para cada base de status.
  // Se a janela bate o teto, sinalizamos pro usuário que pode haver mais.
  const fetchedAtCap =
    isClientPaginated && list
      ? (filter.status === "OPEN"
          ? list.total >= MAX_LOCAL_FETCH * 2
          : (list.total ?? 0) >= MAX_LOCAL_FETCH)
      : false;

  return (
    <div
      style={{
        background: C.bg,
        minHeight: "100vh",
        padding: "20px 24px 40px",
        color: C.text,
      }}
    >
      <div style={{ maxWidth: 1400, margin: "0 auto", display: "grid", gap: 16 }}>
        <div>
          <h1
            style={{
              fontSize: 20,
              fontWeight: 600,
              margin: 0,
              letterSpacing: 0.2,
            }}
          >
            Shadow Portfolio
          </h1>
          <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
            {(
              [
                { key: "trades" as const, label: "Shadow Trades" },
                { key: "report" as const, label: "Relatório Executivo" },
              ]
            ).map(({ key, label }) => {
              const active = mainTab === key;
              return (
                <button
                  key={key}
                  onClick={() => setMainTab(key)}
                  style={{
                    background: active ? C.blue : C.elevated,
                    color: active ? "#fff" : C.muted,
                    border: `1px solid ${active ? C.blue : C.border}`,
                    borderRadius: 6,
                    padding: "5px 16px",
                    fontSize: 12,
                    cursor: "pointer",
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>
          {mainTab === "trades" && (
          <p
            style={{
              fontSize: 12,
              color: C.muted,
              margin: "8px 0 0",
              maxWidth: 720,
            }}
          >
            {sourceTab === "L3" ? (
              <>
                Trades simulados a partir de decisões <code>ALLOW</code> spot do
                pipeline (L3). Alimentados pelo monitor após cada decisão aprovada.
              </>
            ) : sourceTab === "L3_REJECTED" ? (
              <>
                Trades simulados para ativos <code>BLOCK</code> — o que teria
                acontecido se os rejeitados pela L3 tivessem sido operados.
                Usado para medir a qualidade do filtro.
              </>
            ) : sourceTab === "L3_SIMULATED" ? (
              <>
                Camada contrafactual: shadow para <strong>todos</strong> os ativos
                que chegaram ao gate L3, independente de ALLOW ou BLOCK.
                Compara o universo completo com as decisões reais.
              </>
            ) : (
              <>
                Captures do espectro bruto L1 — exatamente o dataset que o ML
                considera. Sem filtro de score ou regras L3. Win rate esperado ~25%.
              </>
            )}
          </p>
          )}
          {mainTab === "trades" && profiles.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
              <span style={{ fontSize: 11, color: C.muted, whiteSpace: "nowrap" }}>
                Strategy Lab:
              </span>
              <select
                value={selectedProfileId ?? ""}
                onChange={(e) => {
                  setSelectedProfileId(e.target.value || null);
                  setFilter({ ...DEFAULT_FILTER });
                }}
                style={{
                  background: C.elevated,
                  color: C.text,
                  border: `1px solid ${C.border}`,
                  borderRadius: 6,
                  padding: "4px 10px",
                  fontSize: 11.5,
                  cursor: "pointer",
                  outline: "none",
                }}
              >
                <option value="">Todos os profiles</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {selectedProfileId && (
                <button
                  onClick={() => {
                    setSelectedProfileId(null);
                    setFilter({ ...DEFAULT_FILTER });
                  }}
                  style={{
                    background: "transparent",
                    border: `1px solid ${C.border}`,
                    color: C.muted,
                    borderRadius: 4,
                    padding: "3px 8px",
                    fontSize: 10.5,
                    cursor: "pointer",
                  }}
                >
                  Limpar
                </button>
              )}
            </div>
          )}
          {mainTab === "trades" && (
          <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
            {(
              [
                { key: "L3",           label: "Aprovados (L3)",    color: C.blue   },
                { key: "L3_REJECTED",  label: "Rejeitados (L3)",   color: C.amber  },
                { key: "L3_SIMULATED", label: "Simulados (L3)",    color: C.purple },
                { key: "L1_SPECTRUM",  label: "Dataset ML (L1)",   color: C.blue   },
              ] as { key: SourceTab; label: string; color: string }[]
            ).map(({ key, label, color }) => {
              const active = sourceTab === key;
              return (
                <button
                  key={key}
                  onClick={() => {
                    setSourceTab(key);
                    setFilter({ ...DEFAULT_FILTER });
                  }}
                  style={{
                    background: active ? color : C.elevated,
                    color: active ? "#fff" : C.muted,
                    border: `1px solid ${active ? color : C.border}`,
                    borderRadius: 6,
                    padding: "5px 14px",
                    fontSize: 11.5,
                    cursor: "pointer",
                    fontWeight: active ? 600 : 400,
                    letterSpacing: 0.3,
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>
          )}
        </div>

        {mainTab === "trades" ? (
          <>
            <FilterBar
              filter={filter}
              onChange={setFilter}
              onRefresh={() => setTick((t) => t + 1)}
              loading={loadingList || loadingSummary}
            />

            <SummaryCards data={summary} loading={loadingSummary} />

            {fetchedAtCap ? (
              <div
                style={{
                  fontSize: 11,
                  color: C.amber,
                  padding: "8px 12px",
                  background: `${C.amber}11`,
                  border: `1px solid ${C.amber}33`,
                  borderRadius: 6,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <AlertTriangle size={12} />
                Mostrando os {MAX_LOCAL_FETCH} itens mais recentes desse filtro.
                Use intervalo de datas ou filtro de símbolo para ver mais.
              </div>
            ) : null}

            <TradeTable
              items={displayItems}
              loading={loadingList}
              error={errorList}
              onRowClick={(id) => setSelectedId(id)}
            />

            {list ? (
              isClientPaginated ? (
                <Pagination
                  page={filter.page}
                  pageSize={CLIENT_PAGE_SIZE}
                  total={clientTotal}
                  onChange={(page) => setFilter({ ...filter, page })}
                />
              ) : (
                <Pagination
                  page={list.page}
                  pageSize={list.page_size}
                  total={list.total}
                  onChange={(page) => setFilter({ ...filter, page })}
                />
              )
            ) : null}
          </>
        ) : (
          <ProfileReportTable rows={profileReport} loading={loadingReport} />
        )}
      </div>

      {selectedId ? (
        <DetailModal shadowId={selectedId} onClose={() => setSelectedId(null)} />
      ) : null}
    </div>
  );
}
