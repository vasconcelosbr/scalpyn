"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { CheckCircle2, Inbox, WalletCards } from "lucide-react";
import { apiGet } from "@/lib/api";

/**
 * One row from ``GET /api/diagnostics/l3-queue`` — the L3-approved
 * snapshot the pipeline scan keeps in ``pipeline_watchlist_assets``.
 *
 * This is the same source the ``/watchlist`` page L3 tab reads, so
 * the card always reflects "what passed L3 in the latest scan",
 * independent of whether ``execute_buy`` has fired or whether the
 * caller has any USDT to spend.
 */
interface L3QueueRow {
  symbol: string;
  score: number | null;
  score_long: number | null;
  score_short: number | null;
  confidence_score: number | null;
  futures_direction: string | null;
  market_type: string;
  approved_at: string | null;
  pool_id: string | null;
  watchlist_id: string;
  watchlist_name: string;
}

interface L3QueueResponse {
  items: L3QueueRow[];
  count: number;
  /** ``buying.capital_per_trade_min_usdt`` from the user's spot_engine
   * config, or ``10.0`` when no config row exists. Drives the
   * "aguardando saldo" badge below. */
  min_trade_usdt: number;
}

interface BalanceResponse {
  available_usdt: number;
  in_positions: number;
  total: number;
  source: string;
}

const ENDPOINT = "/api/diagnostics/l3-queue?limit=50";
const BALANCE_ENDPOINT = "/api/live/balance";


/**
 * Left-column card showing the L3-approved assets currently sitting
 * in the active-pool pipeline. Source: the same
 * ``pipeline_watchlist_assets`` table the watchlist page L3 tab reads.
 *
 * Pulse animation fires on the *first render that contains a symbol
 * we haven't seen before* — we keep the seen-set in a ref so the
 * effect doesn't fire on every poll for the same row. Avoids the
 * page being a constant disco of pulses while the user is scrolling.
 */
export function L3ApprovedList() {
  const { data, isLoading } = useSWR<L3QueueResponse>(
    ENDPOINT,
    (url: string) => apiGet<L3QueueResponse>(url),
    {
      refreshInterval: 8_000,
      revalidateOnFocus: true,
      dedupingInterval: 3_000,
      keepPreviousData: true,
    }
  );

  // Balance is fetched separately so the L3 list keeps rendering
  // even if the exchange call fails (no_connection / exchange_error
  // both surface as available_usdt=0, which is the conservative
  // assumption — every row gets the "aguardando saldo" badge until
  // the user wires credentials).
  const { data: balance } = useSWR<BalanceResponse>(
    BALANCE_ENDPOINT,
    (url: string) => apiGet<BalanceResponse>(url),
    {
      refreshInterval: 15_000,
      revalidateOnFocus: true,
      dedupingInterval: 5_000,
      keepPreviousData: true,
    }
  );

  const items = data?.items ?? [];
  const minTradeUsdt = data?.min_trade_usdt ?? 10;
  const availableUsdt = balance?.available_usdt ?? 0;
  const awaitingBalance = availableUsdt < minTradeUsdt;

  const seenRef = useRef<Set<string>>(new Set());
  const [recentlyAdded, setRecentlyAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    const fresh: string[] = [];
    for (const row of items) {
      const key = rowKey(row);
      if (!seenRef.current.has(key)) {
        seenRef.current.add(key);
        fresh.push(key);
      }
    }
    if (fresh.length > 0 && seenRef.current.size > fresh.length) {
      // Only animate when there were already-seen rows (i.e. this is
      // a delta, not the initial mount). Prevents a pulse storm on
      // page load when the buffer fills with N rows at once.
      setRecentlyAdded(new Set(fresh));
      const t = setTimeout(() => setRecentlyAdded(new Set()), 1_500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [items]);

  return (
    <div className="card fade-in-up">
      <div className="card-header">
        <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <CheckCircle2 size={16} style={{ color: "var(--color-profit)" }} />
          L3 aprovados — pool ativo
        </h3>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          {items.length}
        </span>
      </div>
      <div
        style={{
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          maxHeight: 420,
          overflowY: "auto",
        }}
        className="custom-scrollbar"
      >
        {isLoading && items.length === 0 ? (
          <SkeletonList />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Inbox size={24} />}
            title="Nada aprovado ainda"
            subtitle="Ativos que passarem pelos filtros L3 do pipeline aparecem aqui."
          />
        ) : (
          items.map((row) => (
            <ApprovedCard
              key={rowKey(row)}
              row={row}
              pulse={recentlyAdded.has(rowKey(row))}
              awaitingBalance={awaitingBalance}
              minTradeUsdt={minTradeUsdt}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ── Internal ────────────────────────────────────────────────────────


function ApprovedCard({
  row,
  pulse,
  awaitingBalance,
  minTradeUsdt,
}: {
  row: L3QueueRow;
  pulse: boolean;
  awaitingBalance: boolean;
  minTradeUsdt: number;
}) {
  const score = useMemo(() => extractScore(row), [row]);
  const scoreColor = score == null
    ? "var(--text-tertiary)"
    : score >= 80
    ? "var(--color-profit)"
    : score >= 60
    ? "var(--color-warning)"
    : "var(--color-loss)";

  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-default)",
        borderLeft: "2px solid var(--color-profit)",
        borderRadius: "var(--radius-md)",
        padding: "12px 14px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        animation: pulse
          ? "pulseGlow 1.5s ease-out, fadeInUp 220ms ease-out"
          : "fadeInUp 220ms ease-out",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 14,
            fontWeight: 600,
            color: "var(--text-primary)",
            letterSpacing: "-0.01em",
          }}
        >
          {row.symbol}
        </div>
        <div
          style={{
            fontSize: 11,
            color: "var(--text-tertiary)",
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <span
            style={{
              padding: "1px 6px",
              border: "1px solid var(--border-default)",
              borderRadius: 4,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              fontWeight: 600,
            }}
          >
            {row.market_type}
          </span>
          {row.futures_direction && (
            <span
              style={{
                padding: "1px 6px",
                border: "1px solid var(--border-default)",
                borderRadius: 4,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                fontWeight: 600,
              }}
            >
              {row.futures_direction}
            </span>
          )}
          <span style={{ fontFamily: "var(--font-mono)" }}>
            {fmtTime(row.approved_at)}
          </span>
          {awaitingBalance && (
            <span
              title={`Saldo disponível abaixo do mínimo (${minTradeUsdt.toFixed(2)} USDT)`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "1px 6px",
                borderRadius: 4,
                background: "color-mix(in srgb, var(--color-warning) 14%, transparent)",
                border: "1px solid color-mix(in srgb, var(--color-warning) 35%, transparent)",
                color: "var(--color-warning)",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              <WalletCards size={11} />
              aguardando saldo
            </span>
          )}
        </div>
      </div>
      {score != null && (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            fontWeight: 600,
            color: scoreColor,
            background: `color-mix(in srgb, ${scoreColor} 12%, transparent)`,
            border: `1px solid color-mix(in srgb, ${scoreColor} 28%, transparent)`,
            padding: "2px 10px",
            borderRadius: 999,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {score.toFixed(0)}
        </span>
      )}
    </div>
  );
}

function SkeletonList() {
  return (
    <>
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          style={{
            height: 56,
            borderRadius: 8,
            background:
              "linear-gradient(90deg, var(--bg-hover) 0%, var(--bg-active) 50%, var(--bg-hover) 100%)",
            backgroundSize: "200% 100%",
            animation: "shimmer 1.4s ease-in-out infinite",
          }}
        />
      ))}
    </>
  );
}

function EmptyState({
  icon,
  title,
  subtitle,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div
      style={{
        textAlign: "center",
        padding: "32px 16px",
        color: "var(--text-tertiary)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 8, opacity: 0.7 }}>
        {icon}
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: 13, fontWeight: 600 }}>
        {title}
      </div>
      <div style={{ fontSize: 12, marginTop: 4 }}>{subtitle}</div>
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────


/**
 * Stable identity for an L3-queue row across polls. ``watchlist_id``
 * is needed because the same symbol can legitimately exist on both
 * a spot and a futures L3 watchlist for the same user — keying by
 * symbol alone would collapse them and break the pulse-on-new
 * heuristic.
 */
function rowKey(row: L3QueueRow): string {
  return `${row.watchlist_id}:${row.symbol}`;
}

/**
 * Pulls a numeric "score" from the row using a small waterfall:
 * spot ``alpha_score`` → futures ``confidence_score`` →
 * max(score_long, score_short). Returns ``null`` when nothing is
 * usable so the card renders without a pill rather than ``"NaN"``.
 */
function extractScore(row: L3QueueRow): number | null {
  if (typeof row.score === "number" && isFinite(row.score)) return clamp(row.score);
  if (typeof row.confidence_score === "number" && isFinite(row.confidence_score)) {
    return clamp(row.confidence_score);
  }
  const longS = typeof row.score_long === "number" && isFinite(row.score_long) ? row.score_long : null;
  const shortS = typeof row.score_short === "number" && isFinite(row.score_short) ? row.score_short : null;
  if (longS != null || shortS != null) {
    return clamp(Math.max(longS ?? 0, shortS ?? 0));
  }
  return null;
}

function clamp(n: number): number {
  return Math.max(0, Math.min(100, n));
}

function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return "—";
  }
}
