"use client";

import { BalanceMetrics } from "@/components/diagnostics/BalanceMetrics";
import { L3ApprovedList } from "@/components/diagnostics/L3ApprovedList";
import { PositionsTable } from "@/components/diagnostics/PositionsTable";
import { LiveLogStream } from "@/components/diagnostics/LiveLogStream";
import { RejectionSummary } from "@/components/diagnostics/RejectionSummary";
import { useLiveLogStream } from "@/hooks/useLiveLogStream";


/**
 * Live diagnostics page — single SSE connection feeds both the cycle
 * counters in the top bar AND the trace viewer in section 3.
 *
 * "Cycle" here is operator-visible, not an L3 cycle: we reset the
 * APPROVED/REJECTED counters whenever the page sees the trace_id of
 * the *first* event change, which happens between Celery beat ticks.
 * This matches the spec's "reset a cada novo trace_id do sistema"
 * intent without forcing the page to subscribe to a separate
 * "cycle-boundary" event we don't actually emit yet.
 */
export default function DiagnosticsPage() {
  const stream = useLiveLogStream({ maxBuffer: 200 });

  // Counters are read from the RAW (unfiltered) buffer counts the
  // hook exposes — picking them off ``stream.events`` would couple
  // the cards to the active chip filter and zero out one column the
  // moment the operator filters by the other.
  const approvedCount = stream.rawApprovedCount;
  const rejectedCount = stream.rawRejectedCount;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Inline keyframes — kept here instead of patching globals.css to
       * keep this delivery purely additive. Once the diagnostics page
       * stabilises these can graduate to globals.css. */}
      <style jsx global>{`
        @keyframes shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        @keyframes fadeInUp {
          from { opacity: 0; transform: translate3d(0, 8px, 0); }
          to   { opacity: 1; transform: translate3d(0, 0, 0); }
        }
        .fade-in-up { animation: fadeInUp 280ms cubic-bezier(0.4, 0, 0.2, 1) both; }
        @keyframes pulseGlow {
          0%   { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.45); }
          70%  { box-shadow: 0 0 0 6px rgba(52, 211, 153, 0); }
          100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
        }
        @keyframes softPulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.55; }
        }
        .custom-scrollbar::-webkit-scrollbar { width: 8px; height: 8px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: var(--bg-hover);
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--bg-active); }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1>Diagnóstico ao vivo</h1>
        <p
          style={{
            fontSize: 13,
            color: "var(--text-secondary)",
            margin: 0,
          }}
        >
          Trilha completa de execução spot e futures, em tempo real, com tenancy isolada.
        </p>
      </div>

      {/* SECTION 1 — Top metrics */}
      <BalanceMetrics approvedCount={approvedCount} rejectedCount={rejectedCount} />

      {/* SECTION 2 — Two-column grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <L3ApprovedList />
        <PositionsTable />
      </div>

      {/* SECTION 3 — Live log stream */}
      <LiveLogStream stream={stream} />

      {/* SECTION 4 — Collapsible rejection summary */}
      <RejectionSummary />
    </div>
  );
}

