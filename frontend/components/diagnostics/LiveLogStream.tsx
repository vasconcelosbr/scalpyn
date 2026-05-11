"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, Trash2, Radio, RadioReceiver } from "lucide-react";
import {
  useLiveLogStream,
  type LiveDecisionEvent,
  type LogFilter,
  type StreamStatus,
} from "@/hooks/useLiveLogStream";

interface LiveLogStreamProps {
  /** Hook handle — passed in by the page so the same stream instance
   * also feeds the cycle counters in ``BalanceMetrics``. */
  stream: ReturnType<typeof useLiveLogStream>;
}

const FILTERS: { key: LogFilter; label: string }[] = [
  { key: "all", label: "Todos" },
  { key: "spot", label: "Spot" },
  { key: "futures", label: "Futures" },
  { key: "APPROVED", label: "Aprovados" },
  { key: "REJECTED", label: "Rejeitados" },
];


/**
 * Full-width live trace viewer.
 *
 * Auto-scroll behaviour: we scroll to the bottom on every event
 * arrival *unless* the user has manually scrolled up (we treat being
 * within 32 px of the bottom as "still tailing"). This avoids the
 * common SSE-table footgun where the viewport jumps every render and
 * the user can never inspect anything older than the latest tick.
 *
 * Pausing freezes the *visible* event list inside the hook but keeps
 * the SSE connection alive so the buffer continues to fill in the
 * background — no events are lost on unpause.
 */
export function LiveLogStream({ stream }: LiveLogStreamProps) {
  const { events, status, filter, setFilter, paused, setPaused, clear, totalReceived } = stream;

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const tailRef = useRef(true);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    if (tailRef.current && !paused) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events, paused]);

  const onScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    tailRef.current = dist < 32;
  };

  return (
    <div className="card fade-in-up">
      <div className="card-header" style={{ flexWrap: "wrap", gap: 12 }}>
        <h3 style={{ display: "flex", alignItems: "center", gap: 10 }}>
          Trilha de execução ao vivo — spot + futures
          <ConnectionDot status={status} />
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              style={{
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.04em",
                textTransform: "uppercase",
                borderRadius: 999,
                cursor: "pointer",
                background:
                  filter === f.key
                    ? "var(--accent-primary-muted)"
                    : "var(--bg-hover)",
                color:
                  filter === f.key
                    ? "var(--accent-primary)"
                    : "var(--text-secondary)",
                border: `1px solid ${
                  filter === f.key
                    ? "var(--accent-primary-border)"
                    : "var(--border-default)"
                }`,
                transition: "all var(--transition-fast)",
              }}
            >
              {f.label}
            </button>
          ))}
          <div style={{ width: 1, height: 20, background: "var(--border-default)" }} />
          <ToolbarButton
            onClick={() => setPaused(!paused)}
            icon={paused ? <Play size={12} /> : <Pause size={12} />}
            label={paused ? "Retomar" : "Pausar"}
            active={paused}
          />
          <ToolbarButton onClick={clear} icon={<Trash2 size={12} />} label="Limpar" />
        </div>
      </div>

      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="custom-scrollbar"
        style={{
          maxHeight: 420,
          overflowY: "auto",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {events.length === 0 ? (
          <div
            style={{
              padding: "40px 16px",
              textAlign: "center",
              color: "var(--text-tertiary)",
            }}
          >
            <Radio size={20} style={{ opacity: 0.5, marginBottom: 8 }} />
            <div style={{ color: "var(--text-secondary)", fontSize: 13, fontWeight: 600 }}>
              {status === "live"
                ? "Conectado, aguardando eventos…"
                : status === "connecting"
                ? "Conectando ao stream…"
                : "Stream offline — tentando reconectar"}
            </div>
            <div style={{ fontSize: 12, marginTop: 4 }}>
              {totalReceived > 0
                ? `Buffer total da sessão: ${totalReceived}`
                : "Os eventos aparecem aqui em tempo real."}
            </div>
          </div>
        ) : (
          events.map((evt, i) => (
            <LogLine key={`${evt.trace_id}-${i}`} evt={evt} />
          ))
        )}
      </div>
    </div>
  );
}

// ── Internal ────────────────────────────────────────────────────────


function ConnectionDot({ status }: { status: StreamStatus }) {
  const live = status === "live";
  const color = live
    ? "var(--color-profit)"
    : status === "offline"
    ? "var(--color-loss)"
    : "var(--color-warning)";
  return (
    <span
      title={
        live
          ? "LIVE"
          : status === "offline"
          ? "OFFLINE — tentando reconectar"
          : "Conectando…"
      }
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.08em",
        color,
        textTransform: "uppercase",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: color,
          boxShadow: live ? `0 0 8px ${color}` : "none",
          animation: live ? "softPulse 1.6s ease-in-out infinite" : undefined,
        }}
      />
      {live ? "LIVE" : status === "offline" ? "OFFLINE" : "…"}
      {!live && status === "offline" && (
        <RadioReceiver size={10} style={{ opacity: 0.7 }} />
      )}
    </span>
  );
}

function ToolbarButton({
  onClick,
  icon,
  label,
  active,
}: {
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        borderRadius: 6,
        cursor: "pointer",
        background: active ? "var(--accent-primary-muted)" : "var(--bg-hover)",
        color: active ? "var(--accent-primary)" : "var(--text-secondary)",
        border: `1px solid ${
          active ? "var(--accent-primary-border)" : "var(--border-default)"
        }`,
        transition: "all var(--transition-fast)",
      }}
    >
      {icon}
      {label}
    </button>
  );
}

function LogLine({ evt }: { evt: LiveDecisionEvent }) {
  const [expanded, setExpanded] = useState(false);
  const expandable = evt.status === "REJECTED" && !!evt.rule_details;

  const palette = STATUS_PALETTE[evt.status] ?? STATUS_PALETTE.SISTEMA;

  return (
    <div
      onClick={() => expandable && setExpanded((v) => !v)}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "6px 10px",
        borderRadius: 6,
        borderLeft: `2px solid ${palette.border}`,
        background: palette.bg,
        cursor: expandable ? "pointer" : "default",
        transition: "background var(--transition-fast)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontSize: 12,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            color: "var(--text-tertiary)",
            fontVariantNumeric: "tabular-nums",
            minWidth: 80,
          }}
        >
          {fmtTime(evt.decided_at)}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            color: "var(--text-primary)",
            fontWeight: 600,
            minWidth: 100,
          }}
        >
          {evt.symbol}
        </span>
        <span
          style={{
            color: "var(--text-secondary)",
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {evt.reason ?? evt.blocking_rule ?? evt.stage ?? "—"}
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            padding: "2px 8px",
            borderRadius: 4,
            color: palette.fg,
            background: palette.pillBg,
            border: `1px solid ${palette.border}`,
          }}
        >
          {evt.status}
        </span>
      </div>
      {expanded && expandable && (
        <pre
          style={{
            margin: 0,
            padding: 10,
            background: "var(--bg-base)",
            border: "1px solid var(--border-default)",
            borderRadius: 6,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-secondary)",
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {JSON.stringify(evt.rule_details, null, 2)}
        </pre>
      )}
    </div>
  );
}

interface Palette {
  bg: string;
  border: string;
  fg: string;
  pillBg: string;
}

const STATUS_PALETTE: Record<string, Palette> = {
  APPROVED: {
    bg: "rgba(52, 211, 153, 0.05)",
    border: "#34D399",
    fg: "#34D399",
    pillBg: "rgba(52, 211, 153, 0.12)",
  },
  REJECTED: {
    bg: "rgba(251, 191, 36, 0.05)",
    border: "#FBBF24",
    fg: "#FBBF24",
    pillBg: "rgba(251, 191, 36, 0.12)",
  },
  SKIPPED: {
    bg: "transparent",
    border: "#555B6E",
    fg: "#8B92A5",
    pillBg: "var(--bg-hover)",
  },
  ERROR: {
    bg: "rgba(248, 113, 113, 0.05)",
    border: "#F87171",
    fg: "#F87171",
    pillBg: "rgba(248, 113, 113, 0.12)",
  },
  SISTEMA: {
    bg: "rgba(79, 123, 247, 0.05)",
    border: "#4F7BF7",
    fg: "#4F7BF7",
    pillBg: "rgba(79, 123, 247, 0.12)",
  },
};

function fmtTime(iso?: string | null): string {
  if (!iso) return "—:—:—";
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return "—:—:—";
  }
}
