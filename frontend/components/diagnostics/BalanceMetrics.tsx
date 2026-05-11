"use client";

import { Wallet, Briefcase, CheckCircle2, XCircle } from "lucide-react";
import { useLiveBalance } from "@/hooks/useLiveBalance";

interface BalanceMetricsProps {
  /** Approved count from the live SSE buffer for the current session. */
  approvedCount: number;
  /** Rejected count from the live SSE buffer for the current session. */
  rejectedCount: number;
}

/**
 * Top-bar metric strip: 4 cards.
 *
 * Layout uses CSS grid (``grid-cols-2 lg:grid-cols-4``) so on narrower
 * screens the cards wrap into a 2×2 instead of becoming microscopic
 * horizontal slivers.
 */
export function BalanceMetrics({ approvedCount, rejectedCount }: BalanceMetricsProps) {
  const { data, isLoading } = useLiveBalance();

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 fade-in-up">
      <MetricCard
        icon={<Wallet size={16} />}
        label="Saldo USDT disponível"
        value={isLoading && !data ? null : fmtUsd(data?.available_usdt ?? 0)}
        accent="profit"
      />
      <MetricCard
        icon={<Briefcase size={16} />}
        label="Capital em posições"
        value={isLoading && !data ? null : fmtUsd(data?.in_positions ?? 0)}
        accent="warning"
      />
      <MetricCard
        icon={<CheckCircle2 size={16} />}
        label="Aprovados (ciclo)"
        value={String(approvedCount)}
        accent="profit"
      />
      <MetricCard
        icon={<XCircle size={16} />}
        label="Rejeitados (ciclo)"
        value={String(rejectedCount)}
        accent="loss"
      />
    </div>
  );
}

// ── Internal ────────────────────────────────────────────────────────


type AccentKey = "profit" | "loss" | "warning" | "neutral";

const ACCENT_COLOR: Record<AccentKey, string> = {
  profit: "var(--color-profit)",
  loss: "var(--color-loss)",
  warning: "var(--color-warning)",
  neutral: "var(--text-primary)",
};

function MetricCard({
  icon,
  label,
  value,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | null;
  accent: AccentKey;
}) {
  return (
    <div
      className="card"
      style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          color: "var(--text-tertiary)",
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        <span style={{ color: ACCENT_COLOR[accent], opacity: 0.85 }}>{icon}</span>
        <span>{label}</span>
      </div>
      {value === null ? (
        <Shimmer height={32} width="60%" />
      ) : (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 26,
            fontWeight: 600,
            letterSpacing: "-0.03em",
            color: ACCENT_COLOR[accent],
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </div>
      )}
    </div>
  );
}

function Shimmer({ height, width }: { height: number; width: number | string }) {
  return (
    <div
      style={{
        height,
        width,
        borderRadius: 6,
        background:
          "linear-gradient(90deg, var(--bg-hover) 0%, var(--bg-active) 50%, var(--bg-hover) 100%)",
        backgroundSize: "200% 100%",
        animation: "shimmer 1.4s ease-in-out infinite",
      }}
    />
  );
}

function fmtUsd(v: number): string {
  if (!isFinite(v)) return "—";
  return `$${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v)}`;
}
