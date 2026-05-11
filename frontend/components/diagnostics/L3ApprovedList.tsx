"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { CheckCircle2, Inbox } from "lucide-react";
import { apiGet } from "@/lib/api";

/**
 * Subset of ``trade_decisions`` rows returned by
 * ``GET /api/diagnostics/decisions?status=APPROVED``.
 *
 * Only the fields used by this card are typed strictly; the catch-all
 * keeps us forward-compatible with new columns the backend adds.
 */
interface DecisionRow {
  id: number | string;
  trace_id: string;
  symbol: string;
  market_type: string;
  status: string;
  decided_at: string;
  score_breakdown?: Record<string, unknown> | null;
  indicators_snapshot?: Record<string, unknown> | null;
  [k: string]: unknown;
}

interface DecisionsResponse {
  items: DecisionRow[];
  limit: number;
  offset: number;
  count: number;
}

const ENDPOINT = "/api/diagnostics/decisions?status=APPROVED&limit=20";


/**
 * Left-column card showing the most recent L3-approved assets.
 *
 * Pulse animation is triggered on the *first render that contains a
 * trace_id we haven't seen before* — we keep the seen-set in a ref so
 * the effect doesn't fire on every poll for the same row. Avoids the
 * page being a constant disco of pulses while the user is scrolling.
 */
export function L3ApprovedList() {
  const { data, isLoading } = useSWR<DecisionsResponse>(
    ENDPOINT,
    (url: string) => apiGet<DecisionsResponse>(url),
    {
      refreshInterval: 8_000,
      revalidateOnFocus: true,
      dedupingInterval: 3_000,
      keepPreviousData: true,
    }
  );

  const items = data?.items ?? [];

  const seenRef = useRef<Set<string>>(new Set());
  const [recentlyAdded, setRecentlyAdded] = useState<Set<string>>(new Set());

  useEffect(() => {
    const fresh: string[] = [];
    for (const row of items) {
      if (!seenRef.current.has(row.trace_id)) {
        seenRef.current.add(row.trace_id);
        fresh.push(row.trace_id);
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
            subtitle="Decisões APPROVED do L3 vão aparecer aqui em tempo real."
          />
        ) : (
          items.map((row) => (
            <ApprovedCard
              key={String(row.id) + row.trace_id}
              row={row}
              pulse={recentlyAdded.has(row.trace_id)}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ── Internal ────────────────────────────────────────────────────────


function ApprovedCard({ row, pulse }: { row: DecisionRow; pulse: boolean }) {
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
          <span style={{ fontFamily: "var(--font-mono)" }}>
            {fmtTime(row.decided_at)}
          </span>
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
 * Pulls a numeric "score" from the decision row using a small
 * waterfall of likely shapes. Returns ``null`` when nothing usable is
 * found so the card renders without a pill rather than ``"NaN"``.
 *
 * Order: explicit ``score`` field → sum of layer_* breakdown for
 * futures (5 layers) → sum of any numeric leaf in score_breakdown
 * (last-ditch — caps at 100 anyway).
 */
function extractScore(row: DecisionRow): number | null {
  const explicit = (row as any).score;
  if (typeof explicit === "number" && isFinite(explicit)) return clamp(explicit);

  const breakdown = row.score_breakdown;
  if (!breakdown || typeof breakdown !== "object") return null;

  const layerKeys = Object.keys(breakdown).filter((k) => k.startsWith("layer_"));
  if (layerKeys.length > 0) {
    const sum = layerKeys.reduce((acc, k) => {
      const v = (breakdown as any)[k];
      return acc + (typeof v === "number" && isFinite(v) ? v : 0);
    }, 0);
    return clamp(sum);
  }
  // Fallback: average of numeric leaves.
  const leaves = Object.values(breakdown).filter(
    (v) => typeof v === "number" && isFinite(v as number)
  ) as number[];
  if (leaves.length === 0) return null;
  const avg = leaves.reduce((a, b) => a + b, 0) / leaves.length;
  return clamp(avg);
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
