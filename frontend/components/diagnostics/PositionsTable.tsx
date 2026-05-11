"use client";

import { Activity, Inbox } from "lucide-react";
import { useLivePositions, type LivePosition } from "@/hooks/useLivePositions";

/**
 * Right-column table: open positions with margin-to-target progress.
 *
 * Polling is delegated to ``useLivePositions`` (5 s). The table itself
 * is presentational — sorting and filtering aren't required by the
 * spec at this stage.
 */
export function PositionsTable() {
  const { positions, isLoading } = useLivePositions();

  return (
    <div className="card fade-in-up">
      <div className="card-header">
        <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Activity size={16} style={{ color: "var(--accent-primary)" }} />
          Posições abertas — aguardando margem
        </h3>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          {positions.length}
        </span>
      </div>

      <div style={{ maxHeight: 420, overflowY: "auto" }} className="custom-scrollbar">
        {isLoading && positions.length === 0 ? (
          <SkeletonRows />
        ) : positions.length === 0 ? (
          <div
            style={{
              padding: "32px 16px",
              textAlign: "center",
              color: "var(--text-tertiary)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 8, opacity: 0.7 }}>
              <Inbox size={24} />
            </div>
            <div style={{ color: "var(--text-secondary)", fontSize: 13, fontWeight: 600 }}>
              Sem posições abertas
            </div>
            <div style={{ fontSize: 12, marginTop: 4 }}>
              Quando uma compra for executada ela aparecerá aqui.
            </div>
          </div>
        ) : (
          <table className="data-table" style={{ minWidth: "100%" }}>
            <thead>
              <tr>
                <th>Par</th>
                <th style={{ textAlign: "right" }}>Entrada</th>
                <th style={{ textAlign: "right" }}>Atual</th>
                <th style={{ textAlign: "right" }}>P&amp;L</th>
                <th style={{ minWidth: 160 }}>Margem p/venda</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <PositionRow key={p.trade_id} position={p} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Internal ────────────────────────────────────────────────────────


function PositionRow({ position }: { position: LivePosition }) {
  const negativePnl = (position.pnl_usdt ?? 0) < 0;
  const margin = position.margin_to_target_pct;

  return (
    <tr
      style={{
        animation: negativePnl ? "softPulse 3s ease-in-out infinite" : undefined,
      }}
    >
      <td style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
        {position.symbol}
      </td>
      <td className="numeric">{fmtPrice(position.entry_price)}</td>
      <td className="numeric">{fmtPrice(position.current_price)}</td>
      <td
        className={`numeric ${
          (position.pnl_usdt ?? 0) >= 0 ? "profit" : "loss"
        }`}
      >
        {fmtPnl(position.pnl_usdt, position.pnl_pct)}
      </td>
      <td>
        <MarginBar pct={margin} />
      </td>
      <td>
        <StatusPill label={position.status_label} />
      </td>
    </tr>
  );
}

function MarginBar({ pct }: { pct: number | null }) {
  if (pct == null) {
    return (
      <span style={{ color: "var(--text-tertiary)", fontSize: 12 }}>—</span>
    );
  }
  const color =
    pct >= 70
      ? "var(--color-profit)"
      : pct >= 30
      ? "var(--color-warning)"
      : "var(--color-loss)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 140 }}>
      <div
        style={{
          flex: 1,
          height: 6,
          background: "var(--bg-hover)",
          borderRadius: 3,
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: color,
            transition: "width var(--transition-base)",
          }}
        />
      </div>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color,
          fontVariantNumeric: "tabular-nums",
          minWidth: 36,
          textAlign: "right",
        }}
      >
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}

function StatusPill({ label }: { label: string }) {
  const palette: Record<string, { bg: string; fg: string; border: string }> = {
    holding: {
      bg: "var(--color-profit-muted)",
      fg: "var(--color-profit)",
      border: "var(--color-profit-border)",
    },
    aguardando: {
      bg: "var(--color-warning-muted)",
      fg: "var(--color-warning)",
      border: "rgba(251, 191, 36, 0.25)",
    },
    underwater: {
      bg: "var(--color-loss-muted)",
      fg: "var(--color-loss)",
      border: "var(--color-loss-border)",
    },
  };
  const c = palette[label] ?? {
    bg: "var(--bg-hover)",
    fg: "var(--text-secondary)",
    border: "var(--border-default)",
  };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        background: c.bg,
        color: c.fg,
        border: `1px solid ${c.border}`,
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}

function SkeletonRows() {
  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          style={{
            height: 36,
            borderRadius: 6,
            background:
              "linear-gradient(90deg, var(--bg-hover) 0%, var(--bg-active) 50%, var(--bg-hover) 100%)",
            backgroundSize: "200% 100%",
            animation: "shimmer 1.4s ease-in-out infinite",
          }}
        />
      ))}
    </div>
  );
}

// ── Formatters ──────────────────────────────────────────────────────


function fmtPrice(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs === 0) return "$0.00";
  if (abs < 0.01) return `$${v.toFixed(6)}`;
  if (abs < 1) return `$${v.toFixed(4)}`;
  if (abs < 1000) return `$${v.toFixed(2)}`;
  if (abs < 1_000_000) return `$${(v / 1000).toFixed(2)}K`;
  return `$${(v / 1_000_000).toFixed(2)}M`;
}

function fmtPnl(usdt: number | null | undefined, pct: number | null | undefined): string {
  if (usdt == null || !isFinite(usdt)) return "—";
  const sign = usdt >= 0 ? "+" : "-";
  const abs = Math.abs(usdt);
  const usdStr = `${sign}$${abs.toFixed(2)}`;
  if (pct == null || !isFinite(pct)) return usdStr;
  const pctSign = pct >= 0 ? "+" : "";
  return `${usdStr} (${pctSign}${pct.toFixed(2)}%)`;
}
